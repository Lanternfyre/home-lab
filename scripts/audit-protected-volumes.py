#!/usr/bin/env python3
"""
Read-only audit of persistent-volume protection and QNAP Trident bookkeeping.

NEVER MUTATES ANYTHING. Every kubectl call is a `get`. It is safe to run at any
time, against a healthy or a broken cluster, and is intended for CI.

Checks
------
REGISTRY   every live PVC appears in exactly one list in protected-volumes.yaml
LABEL      every `protected` PVC carries homelab.techyon.dev/protect=true
RETAIN     every `protected` PVC's bound PV has reclaimPolicy: Retain
PVLABEL    every `protected` PVC's bound PV carries the protect label
           (dynamically provisioned PVs do NOT inherit PVC labels, so this must
           be applied to the PV explicitly or the Kyverno PV rule never matches)
GUARD      every `protected` PVC carries Argo Prune=false,Delete=false
TRIDENT    Trident bookkeeping drift: leaked backend volumes, orphaned
           publications, and nodes with no registered CSI driver

Exit codes
----------
0  all checks passed
1  at least one FAIL
2  could not run (no kubectl, bad registry file)

Usage
-----
    scripts/audit-protected-volumes.py [--registry PATH] [--quiet]
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys

PROTECT_LABEL = "homelab.techyon.dev/protect"
UNPROTECT_ANNOTATION = "homelab.techyon.dev/unprotect"
SYNC_OPTIONS = "argocd.argoproj.io/sync-options"
DEFAULT_REGISTRY = "gitops/clusters/home/protected-volumes.yaml"

RED, YELLOW, GREEN, DIM, BOLD, RESET = (
    "\033[31m", "\033[33m", "\033[32m", "\033[2m", "\033[1m", "\033[0m",
)
if not sys.stdout.isatty():
    RED = YELLOW = GREEN = DIM = BOLD = RESET = ""


class Report:
    def __init__(self) -> None:
        self.fails: list[str] = []
        self.warns: list[str] = []
        self.oks = 0

    def fail(self, check: str, msg: str) -> None:
        self.fails.append(f"{check}: {msg}")
        print(f"  {RED}FAIL{RESET} [{check}] {msg}")

    def warn(self, check: str, msg: str) -> None:
        self.warns.append(f"{check}: {msg}")
        print(f"  {YELLOW}WARN{RESET} [{check}] {msg}")

    def ok(self, check: str, msg: str, quiet: bool) -> None:
        self.oks += 1
        if not quiet:
            print(f"  {GREEN}ok{RESET}   {DIM}[{check}] {msg}{RESET}")


def kubectl(*args: str) -> dict | None:
    """Run a read-only kubectl and return parsed JSON, or None if unavailable."""
    try:
        out = subprocess.run(
            ["kubectl", *args, "-o", "json"],
            capture_output=True, text=True, timeout=60, check=True,
        ).stdout
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return None
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--registry", default=DEFAULT_REGISTRY)
    ap.add_argument("--quiet", action="store_true", help="only print WARN/FAIL")
    args = ap.parse_args()

    if not shutil.which("kubectl"):
        print("kubectl not found on PATH", file=sys.stderr)
        return 2
    try:
        import yaml  # noqa: PLC0415  (optional dep, checked at runtime)
    except ImportError:
        print("PyYAML is required: pip install pyyaml", file=sys.stderr)
        return 2

    try:
        with open(args.registry) as fh:
            registry = yaml.safe_load(fh)
    except OSError as exc:
        print(f"cannot read registry {args.registry}: {exc}", file=sys.stderr)
        return 2

    protected = {(e["namespace"], e["claim"]): e
                 for e in registry.get("protected") or []}
    unprotected = {(e["namespace"], e["claim"]): e
                   for e in registry.get("unprotected") or []}

    overlap = set(protected) & set(unprotected)
    r = Report()
    for ns, claim in sorted(overlap):
        r.fail("REGISTRY", f"{ns}/{claim} is in BOTH protected and unprotected")

    pvcs = kubectl("get", "pvc", "-A")
    pvs = kubectl("get", "pv")
    if pvcs is None or pvs is None:
        print("cannot reach the cluster (kubectl get pvc/pv failed)", file=sys.stderr)
        return 2

    pv_by_name = {p["metadata"]["name"]: p for p in pvs["items"]}
    live: dict[tuple[str, str], dict] = {
        (i["metadata"]["namespace"], i["metadata"]["name"]): i for i in pvcs["items"]
    }

    print(f"\n{BOLD}Persistent volume protection audit{RESET}")
    print(f"{DIM}registry: {args.registry} | {len(live)} live PVCs | "
          f"{len(protected)} protected, {len(unprotected)} deliberately unprotected{RESET}\n")

    # --- REGISTRY: every live PVC must be classified -------------------------
    print(f"{BOLD}Registry coverage{RESET}")
    for key in sorted(live):
        ns, claim = key
        if key in protected or key in unprotected:
            r.ok("REGISTRY", f"{ns}/{claim} is registered", args.quiet)
        else:
            r.fail("REGISTRY",
                   f"{ns}/{claim} exists in the cluster but is in NEITHER list. "
                   f"Add it to {args.registry} with a reason.")
    for key in sorted(set(protected) | set(unprotected)):
        if key not in live:
            r.warn("REGISTRY",
                   f"{key[0]}/{key[1]} is registered but does not exist "
                   f"(renamed, or deleted without updating the registry?)")

    # --- Per-protected-claim checks ------------------------------------------
    print(f"\n{BOLD}Protected claims{RESET}")
    for key in sorted(protected):
        ns, claim = key
        pvc = live.get(key)
        if pvc is None:
            continue  # already reported above

        meta = pvc["metadata"]
        labels = meta.get("labels") or {}
        annos = meta.get("annotations") or {}
        name = f"{ns}/{claim}"

        if labels.get(PROTECT_LABEL) == "true":
            r.ok("LABEL", f"{name} carries {PROTECT_LABEL}", args.quiet)
        else:
            r.fail("LABEL",
                   f"{name} is registered protected but has no "
                   f"{PROTECT_LABEL}=true label -- Kyverno will NOT guard it")

        if UNPROTECT_ANNOTATION in annos:
            r.warn("LABEL",
                   f"{name} carries the break-glass annotation "
                   f"{UNPROTECT_ANNOTATION}={annos[UNPROTECT_ANNOTATION]!r}. "
                   f"If this was not deliberate, remove it now.")

        # The Argo prune/delete guards only mean anything on claims Argo actually
        # manages. CNPG- and StatefulSet-generated PVCs are owned by their
        # controller (Argo tracks the Cluster / StatefulSet, not the claim), so
        # demanding the annotation there is a false positive.
        argo_managed = "argocd.argoproj.io/tracking-id" in annos
        if not argo_managed:
            r.ok("GUARD",
                 f"{name} is controller-generated, not Argo-managed -- prune "
                 f"guards N/A", args.quiet)
        else:
            opts = annos.get(SYNC_OPTIONS, "")
            missing = [o for o in ("Prune=false", "Delete=false") if o not in opts]
            if missing:
                r.warn("GUARD",
                       f"{name} is Argo-managed but missing sync-options "
                       f"{', '.join(missing)} -- a deleted or renamed manifest "
                       f"could prune it")
            else:
                r.ok("GUARD", f"{name} has Argo prune/delete guards", args.quiet)

        pv_name = pvc["spec"].get("volumeName")
        if not pv_name:
            r.fail("RETAIN", f"{name} is not bound to a PV (phase="
                             f"{pvc.get('status', {}).get('phase')})")
            continue
        pv = pv_by_name.get(pv_name)
        if pv is None:
            r.fail("RETAIN", f"{name} references PV {pv_name} which does not exist")
            continue

        policy = pv["spec"].get("persistentVolumeReclaimPolicy")
        if policy == "Retain":
            r.ok("RETAIN", f"{name} -> {pv_name} is Retain", args.quiet)
        else:
            r.fail("RETAIN",
                   f"{name} -> {pv_name} has reclaimPolicy={policy}. "
                   f"Deleting the PVC would DESTROY the data on the NAS.")

        pv_labels = pv["metadata"].get("labels") or {}
        if pv_labels.get(PROTECT_LABEL) == "true":
            r.ok("PVLABEL", f"{pv_name} carries the protect label", args.quiet)
        else:
            r.warn("PVLABEL",
                   f"PV {pv_name} (for {name}) has no {PROTECT_LABEL} label. "
                   f"Dynamically provisioned PVs do not inherit PVC labels, so "
                   f"the Kyverno PV rule will not match this one. Fix with: "
                   f"kubectl label pv {pv_name} {PROTECT_LABEL}=true")

    # --- Trident bookkeeping --------------------------------------------------
    print(f"\n{BOLD}QNAP Trident bookkeeping{RESET}")

    csinodes = kubectl("get", "csinodes")
    nodes = kubectl("get", "nodes")
    # A node with no CSI driver is only an *active* hazard if it is schedulable.
    # Cordoning is the documented mitigation, so distinguish the two states --
    # otherwise the audit cries wolf on a node we have already defused.
    unschedulable = set()
    if nodes is not None:
        unschedulable = {n["metadata"]["name"] for n in nodes["items"]
                         if n["spec"].get("unschedulable")}
    if csinodes is None:
        r.warn("TRIDENT", "could not list csinodes")
    else:
        for n in csinodes["items"]:
            nm = n["metadata"]["name"]
            drivers = n["spec"].get("drivers") or []
            if drivers:
                r.ok("TRIDENT", f"{nm} has {len(drivers)} CSI driver(s)", args.quiet)
            elif nm in unschedulable:
                r.warn("TRIDENT",
                       f"node {nm} has ZERO registered CSI drivers, but is cordoned "
                       f"so nothing can be scheduled onto it. Do NOT uncordon until "
                       f"`kubectl get csinode {nm}` shows a driver.")
            else:
                r.fail("TRIDENT",
                       f"node {nm} has ZERO registered CSI drivers and is SCHEDULABLE. "
                       f"Any pod with a QNAP PVC that lands there hangs in "
                       f"ContainerCreating forever. Cordon it now: kubectl cordon {nm}")

    tvols = kubectl("get", "tridentvolumes", "-A")
    if tvols is None:
        r.warn("TRIDENT", "could not list tridentvolumes (CRD absent?)")
    else:
        pv_names = set(pv_by_name)
        leaked = [t["metadata"]["name"] for t in tvols["items"]
                  if t["metadata"]["name"] not in pv_names]
        if leaked:
            r.warn("TRIDENT",
                   f"{len(leaked)} TridentVolume(s) have no matching PV -- these are "
                   f"almost certainly still consuming space on the NAS: "
                   f"{', '.join(sorted(leaked))}. Verify in QTS before deleting; "
                   f"both StorageClasses had reclaimPolicy Delete historically.")
        else:
            r.ok("TRIDENT", "no leaked backend volumes", args.quiet)

    tpubs = kubectl("get", "tridentvolumepublications", "-A")
    if tpubs is not None:
        orphans = [p["metadata"]["name"] for p in tpubs["items"]
                   if p.get("volumeID") not in pv_names]
        if orphans:
            r.warn("TRIDENT",
                   f"{len(orphans)} TridentVolumePublication(s) reference a volume "
                   f"that no longer exists: {', '.join(sorted(orphans))}. Inert now, "
                   f"but a Trident upgrade's reconciliation can trip over them.")
        else:
            r.ok("TRIDENT", "no orphaned volume publications", args.quiet)

    # --- Summary --------------------------------------------------------------
    print(f"\n{BOLD}Summary{RESET}")
    print(f"  {GREEN}{r.oks} passed{RESET}, "
          f"{YELLOW}{len(r.warns)} warnings{RESET}, "
          f"{RED}{len(r.fails)} failures{RESET}")
    if r.fails:
        print(f"\n{RED}{BOLD}Protection is NOT intact.{RESET} Fix the failures above "
              f"before any node drain, reboot or upgrade.")
        return 1
    if r.warns:
        print(f"\n{YELLOW}Protection is in place, with warnings.{RESET}")
    else:
        print(f"\n{GREEN}All protection layers intact.{RESET}")
    print(f"{DIM}Reminder: these layers stop mistakes, not hardware faults. "
          f"Only off-NAS backups survive a disk failure.{RESET}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
