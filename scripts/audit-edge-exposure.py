#!/usr/bin/env python3
"""
Read-only audit of what this cluster publishes to the internet through the
`homelab-edge` Gateway, and whether each published namespace is contained.

NEVER MUTATES ANYTHING. Every kubectl call is a `get`. Safe to run at any time,
against a healthy or a broken cluster, and intended for CI.

Why this exists
---------------
`edge-published-pod-constraints` (Kyverno) enforces what a published workload
must BE, at admission. It cannot see cross-resource facts: whether the namespace
has an egress policy at all, whether a listener has a matching firewall rule,
whether the namespace label and the route agree. Those are this script's job.

🔴 Neither this script nor that policy isolates INGRESS. The edge Envoy runs
hostNetwork, so its traffic carries the node identity and is admitted by the
kubelet-probe rule every namespace needs. A published service is bounded by what
it IS and what it can REACH -- never by who may talk to it. See HARDENING.md.

Checks
------
LABEL      every namespace with a route attached to `homelab-edge` carries
           homelab.techyon.dev/edge-publish=true          [live cluster]
PSA        every published namespace enforces PodSecurity `restricted`
                                                          [live cluster]
EGRESS     every published namespace has a CiliumNetworkPolicy setting
           enableDefaultDeny.egress EXPLICITLY. Cilium's implicit per-direction
           default-deny is correct and invisible; a published namespace must not
           rely on it                                     [live cluster]
SCOPE      that egress names only in-cluster endpoints or FQDNs -- never a bare
           `toEntities: [world]`, which is "may phone anywhere"
                                                          [live cluster]
FIREWALL   every listener on the `homelab-edge` Gateway has a matching
           edge_fw_service_tcp/udp entry -- or, for a privileged port, an
           edge_fw_service_tcp_dnat entry. A listener with no firewall rule is a
           service that silently does not work            [repo]
SHIFT      each DNAT entry redirects to exactly public_port + 10000, which is
           the offset Envoy Gateway applies to privileged listeners. Nothing at
           runtime reconciles the two halves                [repo]
ORPHAN     every firewall entry has a listener. A rule with no listener is a
           port open to the internet that answers nothing [repo]
PROGRAM    every declared listener is actually Programmed on the LIVE Gateway.
           A listener with an unresolvable ref never binds, and the Gateway's
           top-level Programmed stays True regardless   [live cluster]

Exit codes
----------
0  all checks passed
1  at least one FAIL
2  could not run (no kubectl, no PyYAML, unreadable repo file, no cluster)

Usage
-----
    scripts/audit-edge-exposure.py [--gateway PATH] [--firewall PATH] [--quiet]
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys

PUBLISH_LABEL = "homelab.techyon.dev/edge-publish"
PSA_ENFORCE = "pod-security.kubernetes.io/enforce"
REQUIRED_PSA = "restricted"
EDGE_GATEWAY = "homelab-edge"

DEFAULT_GATEWAY = (
    "gitops/clusters/home/apps/envoy-gateway-edge/manifests/homelab-edge.gateway.yaml"
)
DEFAULT_FIREWALL = "ansible/roles/edge_firewall/defaults/main.yml"

# `world` is the one that matters; `all` is its superset. Everything else --
# cluster, host, remote-node, kube-apiserver -- is in-cluster and fine.
FORBIDDEN_ENTITIES = {"world", "all"}

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
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
        return None
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return None


def gateway_listeners(path: str, yaml) -> list[tuple[str, str, int]] | None:
    """(name, protocol, port) for each listener on the homelab-edge Gateway.

    Read from the REPO, not the cluster: the firewall side of the comparison
    only exists in the repo, and comparing a live listener against a repo rule
    would report drift as a contract violation.
    """
    try:
        with open(path) as fh:
            docs = [d for d in yaml.safe_load_all(fh) if d]
    except OSError as exc:
        print(f"cannot read gateway manifest {path}: {exc}", file=sys.stderr)
        return None
    for doc in docs:
        if doc.get("kind") != "Gateway":
            continue
        if doc.get("metadata", {}).get("name") != EDGE_GATEWAY:
            continue
        return [
            (l.get("name", "?"), str(l.get("protocol", "?")).upper(), int(l["port"]))
            for l in doc.get("spec", {}).get("listeners", []) or []
            if "port" in l
        ]
    print(f"no Gateway/{EDGE_GATEWAY} found in {path}", file=sys.stderr)
    return None


def firewall_ports(path: str, yaml) -> tuple[dict[str, set[int]], dict[int, int]] | None:
    """({'TCP': {...}, 'UDP': {...}}, {public_port: listener_port}).

    The second element is the privileged-port mapping. A port below 1024 cannot
    be a plain listener here -- binding it under hostNetwork needs
    NET_BIND_SERVICE, which `gateway-edge-pod-constraints` forbids outright --
    so Envoy Gateway shifts it by +10000 and nftables redirects the public port
    to the shifted one. Both halves live in this one defaults file, which is the
    only reason they can be checked against each other at all.
    """
    try:
        with open(path) as fh:
            data = yaml.safe_load(fh) or {}
    except OSError as exc:
        print(f"cannot read firewall defaults {path}: {exc}", file=sys.stderr)
        return None
    out: dict[str, set[int]] = {}
    for proto, key in (("TCP", "edge_fw_service_tcp"), ("UDP", "edge_fw_service_udp")):
        out[proto] = {
            int(e["port"]) for e in (data.get(key) or []) if isinstance(e, dict) and "port" in e
        }
    dnat = {
        int(e["public_port"]): int(e["listener_port"])
        for e in (data.get("edge_fw_service_tcp_dnat") or [])
        if isinstance(e, dict) and "public_port" in e and "listener_port" in e
    }
    return out, dnat


def check_programmed(r: Report, listeners: list, quiet: bool) -> None:
    """PROGRAM -- every declared listener is actually Programmed on the live Gateway.

    Added after a real miss. A listener was shipped with a certificateRef to a
    Secret in a namespace that did not exist; it went
    `ResolvedRefs=False (RefNotPermitted)` / `Programmed=False (Invalid)`, Envoy
    never bound the port, and nothing answered on :443 -- while this script
    reported "contract satisfied", because every other check reads the REPO.

    The Gateway's top-level `Programmed` condition does not help: it stayed True
    on "1/1 envoy replicas available" while one of its four listeners was
    invalid. Listener status is per-listener and has to be read as such.
    """
    gw = kubectl("get", "gateway", EDGE_GATEWAY, "-n", "gateway-edge")
    if gw is None:
        r.warn("PROGRAM", f"could not read Gateway/{EDGE_GATEWAY}; skipping live check")
        return
    live = {
        l.get("name"): {c.get("type"): (c.get("status"), c.get("reason"))
                        for c in l.get("conditions", []) or []}
        for l in gw.get("status", {}).get("listeners", []) or []
    }
    for name, proto, port in listeners:
        conds = live.get(name)
        if conds is None:
            r.fail(
                "PROGRAM",
                f"listener `{name}` ({proto}/{port}) is declared in the manifest but "
                f"absent from the live Gateway status. It has not been applied, or "
                f"the controller rejected the Gateway outright.",
            )
            continue
        status, reason = conds.get("Programmed", ("Unknown", "NoCondition"))
        if status != "True":
            refs = conds.get("ResolvedRefs", ("?", "?"))
            r.fail(
                "PROGRAM",
                f"listener `{name}` ({proto}/{port}) is Programmed={status} "
                f"({reason}), ResolvedRefs={refs[0]} ({refs[1]}). Envoy has NOT bound "
                f"this port -- the firewall rule leads nowhere and the service is "
                f"unreachable, while the Gateway's top-level status still reads "
                f"Programmed=True.",
            )
        else:
            r.ok("PROGRAM", f"listener {name} ({proto}/{port}) is programmed", quiet)


def check_routes(r: Report, published: set[str], quiet: bool) -> None:
    """LABEL -- a route may only attach from a namespace carrying the label."""
    found_any = False
    for kind in ("tcproutes", "udproutes", "tlsroutes"):
        routes = kubectl("get", kind, "-A")
        if routes is None:
            continue  # CRD may not be installed; not a failure on its own
        for rt in routes.get("items", []):
            parents = rt.get("spec", {}).get("parentRefs", []) or []
            if not any(p.get("name") == EDGE_GATEWAY for p in parents):
                continue
            found_any = True
            ns = rt["metadata"]["namespace"]
            name = rt["metadata"]["name"]
            if ns in published:
                r.ok("LABEL", f"{kind[:-1]} {ns}/{name} -> {EDGE_GATEWAY}", quiet)
                continue

            # The Gateway's allowedRoutes selector should already refuse this.
            # Whether it DID is the difference between "someone tried" and "the
            # containment is broken", so read the status rather than assume.
            accepted = any(
                c.get("type") == "Accepted" and c.get("status") == "True"
                for p in rt.get("status", {}).get("parents", []) or []
                for c in p.get("conditions", []) or []
            )
            if accepted:
                r.fail(
                    "LABEL",
                    f"{kind[:-1]} {ns}/{name} is ACCEPTED on {EDGE_GATEWAY} but "
                    f"namespace {ns} does not carry {PUBLISH_LABEL}=true. The "
                    f"allowedRoutes selector has been weakened -- something is "
                    f"published to the internet that never opted in.",
                )
            else:
                r.warn(
                    "LABEL",
                    f"{kind[:-1]} {ns}/{name} points at {EDGE_GATEWAY} from a namespace "
                    f"without {PUBLISH_LABEL}=true. The Gateway correctly refused it, so "
                    f"nothing is exposed -- but the route is dead and says so only in "
                    f"its own status. Label the namespace or delete the route.",
                )
    if not found_any:
        r.warn(
            "LABEL",
            f"no route anywhere attaches to {EDGE_GATEWAY}. Nothing is published, so "
            f"every check below is vacuous.",
        )


def check_namespace(r: Report, ns: str, ns_obj: dict, quiet: bool) -> None:
    """PSA, EGRESS and SCOPE for one published namespace."""
    labels = ns_obj.get("metadata", {}).get("labels", {}) or {}

    psa = labels.get(PSA_ENFORCE)
    if psa != REQUIRED_PSA:
        r.fail(
            "PSA",
            f"namespace {ns} is published but enforces PodSecurity "
            f"{psa or '(nothing)'}, not `{REQUIRED_PSA}`. That is the floor a "
            f"published namespace stands on.",
        )
    else:
        r.ok("PSA", f"{ns} enforces {REQUIRED_PSA}", quiet)

    cnps = kubectl("get", "ciliumnetworkpolicies", "-n", ns)
    if cnps is None:
        r.fail("EGRESS", f"could not list CiliumNetworkPolicies in {ns}")
        return

    namespace_wide = [
        c for c in cnps.get("items", [])
        if c.get("spec", {}).get("endpointSelector") == {}
    ]
    explicit = [
        c for c in namespace_wide
        if (c.get("spec", {}).get("enableDefaultDeny") or {}).get("egress") is True
    ]

    if not explicit:
        others = [c["metadata"]["name"] for c in cnps.get("items", [])]
        r.fail(
            "EGRESS",
            f"namespace {ns} has no namespace-wide CiliumNetworkPolicy setting "
            f"enableDefaultDeny.egress: true explicitly "
            f"(policies present: {', '.join(sorted(others)) or 'none'}). Cilium's "
            f"implicit default-deny may well be in force -- but it is invisible in "
            f"the config, and a published namespace must state it.",
        )
    else:
        r.ok(
            "EGRESS",
            f"{ns}: {', '.join(sorted(c['metadata']['name'] for c in explicit))} "
            f"sets enableDefaultDeny.egress explicitly",
            quiet,
        )

    # SCOPE looks at EVERY policy in the namespace, not just the explicit ones:
    # a permissive second policy is exactly how a containment gets undone.
    offenders: list[str] = []
    for c in cnps.get("items", []):
        for rule in c.get("spec", {}).get("egress", []) or []:
            bad = FORBIDDEN_ENTITIES & {str(e).lower() for e in rule.get("toEntities", []) or []}
            if bad:
                offenders.append(f"{c['metadata']['name']} -> toEntities {sorted(bad)}")
    if offenders:
        r.fail(
            "SCOPE",
            f"namespace {ns} permits unrestricted egress: {'; '.join(offenders)}. "
            f"A workload strangers can reach must not be able to phone anywhere it "
            f"likes -- name the FQDNs or endpoints it needs.",
        )
    else:
        r.ok("SCOPE", f"{ns}: no bare `world` egress", quiet)


def check_firewall(
    r: Report,
    listeners: list,
    fw: dict[str, set[int]],
    dnat: dict[int, int],
    quiet: bool,
) -> None:
    """FIREWALL, SHIFT and ORPHAN -- listener/rule parity, all read from the repo."""
    # Gateway API listeners name the port the CLIENT connects to. For a
    # privileged port that is NOT the port Envoy binds, so a listener is
    # satisfied by either a direct firewall rule or a DNAT entry.
    for name, proto, port in listeners:
        if proto == "HTTPS":
            proto = "TCP"  # same firewall rule either way
        if proto not in fw:
            r.warn("FIREWALL", f"listener {name} has protocol {proto}, which this "
                               f"script does not know how to check")
            continue
        if port in dnat:
            expected = port + 10000
            if dnat[port] != expected:
                r.fail(
                    "SHIFT",
                    f"listener `{name}` is {proto}/{port} and its DNAT entry redirects "
                    f"to {dnat[port]}, but Envoy Gateway shifts privileged listener "
                    f"ports by exactly +10000, so it will be bound on {expected}. "
                    f"nftables and Envoy disagree about where this traffic goes, and "
                    f"nothing at runtime reports it -- the port just stops answering.",
                )
            else:
                r.ok("SHIFT", f"listener {name} {proto}/{port} -> host {dnat[port]}", quiet)
            continue
        if port < 1024:
            r.fail(
                "FIREWALL",
                f"listener `{name}` is {proto}/{port}, a privileged port with no "
                f"edge_fw_service_tcp_dnat entry. Envoy cannot bind it under "
                f"hostNetwork without NET_BIND_SERVICE, which "
                f"gateway-edge-pod-constraints forbids. Add the DNAT entry rather "
                f"than the capability.",
            )
        elif port not in fw[proto]:
            r.fail(
                "FIREWALL",
                f"listener `{name}` is {proto}/{port} on {EDGE_GATEWAY} but there is "
                f"no edge_fw_service_{proto.lower()} entry for it. The firewall will "
                f"drop the traffic and the service will silently not work.",
            )
        else:
            r.ok("FIREWALL", f"listener {name} {proto}/{port} has a firewall rule", quiet)

    listener_ports = {("TCP" if p == "HTTPS" else p, port) for _, p, port in listeners}
    for proto, ports in fw.items():
        for port in sorted(ports):
            if (proto, port) not in listener_ports:
                r.fail(
                    "ORPHAN",
                    f"edge_fw_service_{proto.lower()} permits {proto}/{port} but no "
                    f"{EDGE_GATEWAY} listener uses it. That is a port open to the "
                    f"internet answering nothing -- delete the rule or add the listener.",
                )
            else:
                r.ok("ORPHAN", f"firewall rule {proto}/{port} has a listener", quiet)

    for public_port in sorted(dnat):
        if ("TCP", public_port) not in listener_ports:
            r.fail(
                "ORPHAN",
                f"edge_fw_service_tcp_dnat redirects public {public_port} -> "
                f"{dnat[public_port]} but no {EDGE_GATEWAY} listener declares port "
                f"{public_port}. The redirect lands on a port nothing is bound to.",
            )
        else:
            r.ok("ORPHAN", f"DNAT {public_port} -> {dnat[public_port]} has a listener", quiet)


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--gateway", default=DEFAULT_GATEWAY)
    ap.add_argument("--firewall", default=DEFAULT_FIREWALL)
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

    listeners = gateway_listeners(args.gateway, yaml)
    fwres = firewall_ports(args.firewall, yaml)
    if listeners is None or fwres is None:
        return 2
    fw, dnat = fwres

    namespaces = kubectl("get", "namespaces")
    if namespaces is None:
        print("cannot reach the cluster (kubectl get namespaces failed)", file=sys.stderr)
        return 2

    published = {
        n["metadata"]["name"]: n
        for n in namespaces.get("items", [])
        if (n.get("metadata", {}).get("labels", {}) or {}).get(PUBLISH_LABEL) == "true"
    }

    print(f"\n{BOLD}Edge exposure audit{RESET}")
    print(f"{DIM}  gateway manifest : {args.gateway}{RESET}")
    print(f"{DIM}  firewall defaults: {args.firewall}{RESET}")
    print(f"{DIM}  published namespaces: {', '.join(sorted(published)) or 'none'}{RESET}\n")

    r = Report()
    check_firewall(r, listeners, fw, dnat, args.quiet)
    check_programmed(r, listeners, args.quiet)
    check_routes(r, set(published), args.quiet)
    if not published:
        r.warn("PSA", f"no namespace carries {PUBLISH_LABEL}=true; nothing to contain")
    for ns in sorted(published):
        check_namespace(r, ns, published[ns], args.quiet)

    print(f"\n{BOLD}Summary{RESET}")
    print(f"  {GREEN}{r.oks} passed{RESET}, "
          f"{YELLOW}{len(r.warns)} warnings{RESET}, "
          f"{RED}{len(r.fails)} failures{RESET}")
    if r.fails:
        print(f"\n{RED}{BOLD}The exposure contract is NOT satisfied.{RESET} Fix the "
              f"failures above before publishing anything else.")
        return 1
    if r.warns:
        print(f"\n{YELLOW}Contract satisfied, with warnings.{RESET}")
    else:
        print(f"\n{GREEN}Exposure contract satisfied.{RESET}")
    print(f"{DIM}Reminder: this checks what a published service IS and what it can "
          f"REACH. It does not isolate ingress, and cannot -- see HARDENING.md.{RESET}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
