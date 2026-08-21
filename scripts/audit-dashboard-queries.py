#!/usr/bin/env python3
"""
Read-only audit of Grafana dashboard queries against the live Prometheus.

NEVER MUTATES ANYTHING. Every call is a PromQL read through the Prometheus HTTP
API (`kubectl exec` into the Prometheus pod, `wget` only). Safe to run at any
time and intended for CI.

Why this exists
---------------
A dashboard ConfigMap is applied, ArgoCD reports Synced, the sidecar imports it,
Grafana renders it -- and every panel is empty because the expressions name
metrics that do not exist. That is not hypothetical: the `Necronia -
Infrastructure` dashboard shipped querying four .NET metrics under their old
names, a GC label spelled `generation` when the live one is `gc_heap_generation`,
five Orleans series this version never emits, and an entire PostgreSQL row
against `pg_stat_*` when nothing in this cluster has ever produced a `pg_`
series. Nothing alerted, because nothing was wrong -- it just showed nothing.

Assert values and behaviour, never file presence.

Checks
------
PARSE    every `expr` is accepted by the Prometheus query parser
EXISTS   every metric name referenced by an `expr` is in the live __name__ index

Known-absent metrics
--------------------
Absence has three causes and only one of them is a bug:

  NEVER_OBSERVED   OpenTelemetry does not export a counter until it is first
                   incremented, so counters for rare events (packet drops,
                   contained faults, write-behind rejections) genuinely do not
                   exist yet. Hand-maintained list below.

  producer down    The job or service the query names has NO series at all.
                   Detected automatically per query -- these are reported as
                   NOT CHECKED, never as failures, because absence proves
                   nothing while the producer is off. This Prometheus is
                   emptyDir with 10d retention, so a pod restart empties the
                   index for every producer that is not currently writing.

  a real bug       Everything else. This is what the script is for.

`--strict` disables NEVER_OBSERVED. The producer check is not disableable:
turning it off would not make the answer more accurate, only more confident.

Exit codes
----------
0  all checks passed
1  at least one FAIL
2  could not run (no kubectl, no Prometheus, unreadable dashboard)

Usage
-----
    scripts/audit-dashboard-queries.py                  # all dashboards in git
    scripts/audit-dashboard-queries.py path/to/one.yaml [...]
    scripts/audit-dashboard-queries.py --strict         # ignore the allow-lists
"""

from __future__ import annotations

import argparse
import glob
import json
import re
import shutil
import subprocess
import sys
import urllib.parse

PROM_POD = "sts/prometheus-prometheus-operator-kube-p-prometheus"
DASHBOARD_GLOB = "gitops/clusters/home/apps/*/manifests/*dashboard*.yaml"

# Counters no observation has ever incremented -- absent, not broken.
NEVER_OBSERVED = {
    "necronia_login_failures_total",
    "necronia_grains_ecs_tick_overruns_total",
    "necronia_grains_pathfind_failures_total",
    "necronia_grains_player_deaths_total",
    "necronia_grains_spells_cast_total",
    "necronia_grains_ground_items_bucket",
    "necronia_gateway_packets_invalid_total",
    "necronia_gateway_packets_dropped_total",
    "necronia_gateway_pipeline_backpressure_total",
    "necronia_gateway_drains_total",
    "necronia_gateway_contained_faults_total",
    "necronia_grains_scheduler_drain_clipped_total",
    "necronia_persistence_writebehind_writes_total",
    "necronia_persistence_writebehind_rejections_total",
    "necronia_scripting_errors_total",
    "necronia_scripting_timeouts_total",
}

# (A PENDING_SCRAPE set lived here while the CNPG PodMonitor was in flight.
# Removed 2026-08-17 once cnpg_* was confirmed live -- a stale entry in a set
# like that hides a real typo, which is the failure this script exists to catch.)
PENDING_SCRAPE = set()

# Absence is not evidence when the producer is simply not running.
#
# The __name__ index only knows what is currently IN THE TSDB, and this
# Prometheus is deliberately emptyDir with 10d retention. So a name disappears
# from it for two completely different reasons: it never existed (a real bug,
# the thing this script hunts) or its producer has not written recently
# (nothing wrong at all).
#
# Not hypothetical. Within hours of this script being written, a Prometheus pod
# restart wiped the TSDB while the Necronia dev server -- a workstation process,
# off most of the time -- was down, and the audit reported 81 failures against
# metric names it had itself verified as live that morning. An audit that cries
# wolf gets muted, and a muted audit is worth the same as no audit.
#
# The producer is read from the QUERY rather than from a table of name
# prefixes. A first attempt used prefixes and immediately mis-classified
# `target_info{job="necronia-server"}`, which comes from that same dead
# producer but shares no prefix with it. The expression already says who it is
# asking about; believe it.
PRODUCER_SELECTOR = re.compile(r'\b(job|service)\s*=\s*"([^"]+)"')


# PromQL keywords and functions -- identifiers that are not metric names.
RESERVED = {
    "abs", "absent", "absent_over_time", "and", "avg", "avg_over_time", "bool",
    "bottomk", "by", "ceil", "changes", "clamp", "clamp_max", "clamp_min",
    "count", "count_over_time", "count_values", "delta", "deriv", "exp",
    "floor", "group", "group_left", "group_right", "histogram_quantile",
    "holt_winters", "idelta", "ignoring", "increase", "irate", "label_join",
    "label_replace", "last_over_time", "le", "ln", "log2", "log10", "max",
    "max_over_time", "min", "min_over_time", "offset", "on", "or",
    "predict_linear", "present_over_time", "quantile", "quantile_over_time",
    "rate", "resets", "round", "scalar", "sgn", "sort", "sort_desc", "sqrt",
    "stddev", "stdvar", "sum", "sum_over_time", "time", "timestamp", "topk",
    "unless", "vector", "without",
    "label_values",  # Grafana template-variable helper, not PromQL
}
IDENT = re.compile(r"[a-zA-Z_:][a-zA-Z0-9_:]*")
GROUPING = re.compile(
    r"\b(by|without|on|ignoring|group_left|group_right)\s*\([^)]*\)")


def prom(path: str) -> dict:
    """One read against the Prometheus HTTP API. Returns {} on any failure."""
    out = subprocess.run(
        ["kubectl", "-n", "monitoring", "exec", "-i", PROM_POD, "-c",
         "prometheus", "--", "wget", "-qO-", f"http://localhost:9090{path}"],
        capture_output=True, text=True).stdout
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return {}


def targets(path: str):
    """Yield (label, expr) for every panel target and query variable."""
    import yaml
    doc = yaml.safe_load(open(path))
    if doc.get("kind") != "ConfigMap":
        return
    for blob in doc.get("data", {}).values():
        try:
            dash = json.loads(blob)
        except json.JSONDecodeError:
            continue
        for panel in dash.get("panels", []):
            for t in panel.get("targets", []):
                if "expr" in t:
                    yield panel.get("title", "?"), t["expr"]
        for var in dash.get("templating", {}).get("list", []):
            q = var.get("query")
            # label_values(<selector>, <label>) -- audit the selector half
            if var.get("type") == "query" and isinstance(q, str) \
                    and q.startswith("label_values("):
                yield f"var:{var['name']}", q[q.index("(") + 1:q.rindex(",")]


def metric_names(expr: str) -> set[str]:
    """Metric names in an expr: identifiers left after stripping everything else."""
    e = re.sub(r'"[^"]*"', "", expr)      # string literals
    e = re.sub(r"\{[^}]*\}", "", e)       # label matchers
    e = re.sub(r"\$\w+", "", e)           # dashboard variables
    e = re.sub(r"\[[^\]]*\]", "", e)      # range selectors
    e = GROUPING.sub("", e)               # by()/without()/on() label lists
    return {m.group() for m in IDENT.finditer(e)
            if m.group() not in RESERVED
            and not m.group().startswith("__")
            and not (m.end() < len(e) and e[m.end()] == "(")}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("paths", nargs="*")
    ap.add_argument("--strict", action="store_true",
                    help="fail on allow-listed metrics too")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    if not shutil.which("kubectl"):
        print("kubectl not found", file=sys.stderr)
        return 2

    known = {n for n in prom("/api/v1/label/__name__/values").get("data", [])}
    if not known:
        print("could not read the Prometheus __name__ index", file=sys.stderr)
        return 2
    allowed = set() if args.strict else NEVER_OBSERVED | PENDING_SCRAPE

    live_producer = {}   # (label, value) -> bool, memoised across dashboards

    def producer_alive(expr):
        """False only when the expr names a job/service that has NO series."""
        m = PRODUCER_SELECTOR.search(expr)
        if not m:
            return True                      # no producer claimed -> judge it
        key = (m.group(1), m.group(2))
        if key not in live_producer:
            sel = urllib.parse.quote('{%s="%s"}' % key)
            live_producer[key] = bool(
                prom("/api/v1/series?match[]=" + sel).get("data"))
        return live_producer[key]

    paths = args.paths or sorted(glob.glob(DASHBOARD_GLOB))
    if not args.quiet:
        print(f"{len(known)} metric names live; auditing {len(paths)} dashboards\n")

    failures = 0
    skipped = set()
    for path in paths:
        problems = []
        for title, expr in targets(path):
            q = re.sub(r"\$\w+", "5m", expr)   # any var; 5m parses as a duration
            r = prom("/api/v1/query?query=" + urllib.parse.quote(q))
            if r.get("status") != "success":
                problems.append(f"PARSE   {title}: {r.get('error', 'unparseable')}")
                continue
            missing = [n for n in sorted(metric_names(expr))
                       if n not in known and n not in allowed]
            if not missing:
                continue
            # A name whose producer is offline proves nothing either way.
            if producer_alive(expr):
                problems.append(f"EXISTS  {title}: {', '.join(missing)}")
            else:
                skipped.update(missing)
        failures += len(problems)
        if problems:
            print(f"FAIL  {path}")
            for p in problems:
                print(f"        {p}")
        elif not args.quiet:
            print(f"ok    {path}")

    if skipped and not args.quiet:
        dead = sorted(f'{k}="{v}"' for (k, v), up in live_producer.items()
                      if not up)
        print(f"\n{len(skipped)} name(s) NOT CHECKED -- their producer has no "
              f"series at all right now ({', '.join(dead)}).")
        print("Absence proves nothing while the producer is down. Re-run with "
              "it up for a real verdict.")
    print(f"\n{failures} problem(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
