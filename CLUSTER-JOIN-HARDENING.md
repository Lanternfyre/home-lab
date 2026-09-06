# Cluster join hardening — and what "safe to expose" has to mean

**Living document.** Companion to [`EDGE-NODE.md`](EDGE-NODE.md), which built
the public edge node, and [`CLUSTER-TOKEN.md`](CLUSTER-TOKEN.md), which owns the
rotation runbook this plan finally executes.

⚠️ **This document used to claim it was what `H1` and `H3` point at.** It is
not — it never uses those tokens; its findings are numbered 1-6 and its
programme 1-5. That forward reference is now implemented properly in
[`HARDENING.md`](HARDENING.md), which defines `H0`-`H5`.

Started 2026-09-06. **Programme items 1-3 are built and verified; 4 and 5 are
not.** See the programme table below for per-item status.

---

## Why now

`k8s-edge1` is a k3s agent on a public VPS, publishing raw TCP and UDP to
anonymous clients. It works. It also means the cluster now has a member that
strangers can attack, so the credential that makes a machine a member stopped
being a background concern.

Investigating that turned up something larger than the edge node.

## ✅ The join token was published — rotated 2026-09-06

**RESOLVED.** `k3sblog` is dead: `server-bootstrap` returns `401` for it on all
three servers, verified rather than assumed. An agent token was split out in the
same window, so an agent credential can no longer join a server. The procedure
and the failure it caused are in
[CLUSTER-TOKEN.md](CLUSTER-TOKEN.md#what-2026-09-06-taught-us); the playbook is
`ansible/playbooks/71-rotate-cluster-token.yml`.

The finding as originally written follows, because the history matters.

### 🔴 The join token is published (as found)

```
gitops/argo-install.md:33    --token k3sblog       ← PUBLIC repository
gitops/argo-install.md:110   --token k3sblog
CLUSTER-TOKEN.md             "Nothing here has been executed yet."
                             Current token | 7 characters, a dictionary word plus a suffix
```

`k3sblog` is seven characters, a dictionary word plus a suffix, and the rotation
runbook has never run. **The live join token is on GitHub, in git history, and
in k3s it joins a *server* — which is etcd, which is everything.**

The edge node holding a copy is a footnote beside that. Anyone who reads the
repository already has it. The only thing between that and a hostile etcd member
is network reach to `6443`, which is open to the entire LAN.

Rotation is therefore urgent, not a tidy-up.

---

## What is already safe

Recorded so this plan does not "harden" what is already sound, and so the next
person does not re-derive it.

### The kubelet credential is well contained

The Node authorizer is active and effective. Verified by impersonation rather
than by reading k3s defaults:

```
$ kubectl auth can-i list secrets --all-namespaces --as=system:node:k8s-edge1 --as-group=system:nodes
no - can only read namespaced object of this type

$ kubectl auth can-i get secrets -n databases  --as=system:node:k8s-edge1 …
no - No Object name found

$ kubectl auth can-i create pod -n kube-system --as=system:node:k8s-edge1 …
no

$ kubectl auth can-i list nodes --as=system:node:k8s-edge1 …
no - node 'k8s-edge1' cannot read all nodes, only its own Node object

$ kubectl label node k8s-lab1 x=1 --dry-run=server --as=system:node:k8s-edge1 …
Error from server (Forbidden): node 'k8s-edge1' cannot read 'k8s-lab1'
```

So a stolen **node identity** is contained: it cannot enumerate nodes, touch
another node, create pods, or read secrets beyond those its own pods need.

⚠️ **The join token is a different credential** and none of that applies to it.
Conflating the two is the mistake this document exists to prevent.

### WireGuard containment works

From the edge node, over the tunnel:

```
192.168.33.14:445  (NAS)     blocked
192.168.33.1:80    (router)  blocked
```

`AllowedIPs` covering only node `/32`s does exactly what it was designed to.

### Anonymous API access is denied

`/version` and `/openapi/v2` both return `401`.

---

## Findings — verified, do not re-derive

### 1. 🔴 The tunnel reaches far more than it needs

From `k8s-edge1`, over WireGuard, against a server:

```
6443   OPEN    needed — apiserver
8473   needed  — VXLAN
4240   needed  — cilium-health, see below
10250  OPEN    NOT needed — kubelet API
2379   OPEN    NOT needed — etcd client
2380   OPEN    NOT needed — etcd peer
```

`2379`/`2380` are open to the **whole LAN**, independent of the edge node.

⚠️ **`cilium-health` must keep working.** `cilium-health status` reports
`8/8 reachable`, which means the edge probes home nodes on `4240` and ICMP.
Blocking those costs cluster health reporting and buys nothing — the classic
"tightening" that removes a signal instead of an exposure.

### 2. 🔴 Ingress to a published service cannot be restricted to the edge

The edge Envoy runs `hostNetwork`, so its traffic carries the **node identity**.
`ot-demo`'s first ingress rule is:

```yaml
- fromEntities: [host, remote-node, world]     # no toPorts
```

The `cloudflare-tunnel` rule cannot be what admits the edge — wrong namespace —
and the banner demonstrably works. **So edge traffic is admitted by the
kubelet-probe rule, on any port.** At L3/L4 the edge data path and a kubelet
probe are indistinguishable, and `mealie-ingress` already records that narrowing
that rule crashloops healthy pods eight times over.

**Consequence for the contract below: do not promise ingress isolation.** What
bounds a published service is what it *is* (pod hardening) and what it can
*reach* (egress). Anything else would be a comforting sentence rather than a
control.

### 3. 🔴 Nothing enforces what a published service must be

`ot-demo/servers.yaml` does everything right — digest-pinned image,
`runAsNonRoot`, `readOnlyRootFilesystem`, `capabilities.drop: [ALL]`,
`automountServiceAccountToken: false`, resource limits — **by convention, with
nothing enforcing any of it.** PSA `restricted` checks none of those four
properties.

Across the whole repository exactly one policy (`buildkit-pod-constraints`)
*requires* anything. Every other Kyverno rule is a prohibition. A "must have"
contract is new work here, not a copy of an existing pattern.

### 4. Only 2 of 17 network policies set `enableDefaultDeny` explicitly

The other fifteen rely on Cilium's implicit per-direction default-deny: a
direction with any rule becomes default-deny, an omitted direction stays open.
That is correct and invisible — precisely the "control that looks complete and
has a hole nobody can see from the config" shape `CLAUDE.md` warns about. The
contract mandates the explicit form for published namespaces.

### 5. ✅ FIXED — both join playbooks used to hand agents the SERVER token

`40-add-node.yml` and `45-change-node-role.yml` both read
`/var/lib/rancher/k3s/server/node-token` unconditionally, regardless of the
node's role. That is exactly why `edge-1` held a server-capable credential, and
it is the line that had to change for an agent token to ever reach an agent.

✅ **Fixed 2026-09-06** in the same window as the rotation. Both playbooks now
choose `agent-token` or `node-token` by `k3s_role`, with an explicit assert that
refuses to fall back to the server token. `edge-1` holds the agent token only.

### 6. A node joining is completely silent

No PrometheusRule anywhere fires on node count changing or on a node appearing.
Every layer here is prevention with no detection behind it.

---

## The contract: what "safe to expose" means

The operator's requirement, in their words: *adding a service to expose publicly
should stay easy, but must not compromise any node nor service.*

Publishing stays **four edits** — a listener, a route, the namespace label, a
firewall entry. What changes is that the properties are enforced rather than
remembered.

### A published namespace must

| property | enforced by | why |
|---|---|---|
| PSA `restricted` | namespace label | the floor |
| explicit `enableDefaultDeny.egress` | audit script | implicit is invisible (finding 4) |
| egress named, no bare `world` | audit script | a compromised pod must not phone home |
| no `hostNetwork`/`hostPID`/`hostIPC`/`hostPath` | Kyverno | a published pod must not touch the node |
| no `privileged`, no added capabilities | Kyverno | |
| `runAsNonRoot`, `readOnlyRootFilesystem` | Kyverno | |
| `automountServiceAccountToken: false` | Kyverno | no API credential in a pod strangers can reach |
| image pinned by digest | Kyverno | a tag is mutable; a published workload must not be |
| memory limit set | Kyverno | a public port is a public OOM vector |
| a firewall entry per listener | audit script | a listener with no rule silently does not work |

### What it does NOT promise

**It does not isolate ingress.** See finding 2. Anything with node identity
reaches a published pod on any port. If that matters for a future service, the
answer is a different data path, not a stricter policy.

**It does not stop a cluster-admin.** Anyone who can label a namespace can
publish through the edge. That is ArgoCD and this repository, and it is stated
rather than hidden — the same residual hole already recorded on
`bootstrap/namespaces/ot-demo.yaml`.

### `ot-demo` is the reference implementation

It already satisfies every row: `restricted`, explicit default-deny both
directions, **zero** egress rules, digest-pinned, non-root, read-only rootfs, no
SA token. It is why it — and not necronia — was the first thing published.

Necronia will need egress (a database at minimum), so it cannot be zero-egress.
It must instead name exactly what it needs, in the idiom every other policy here
uses: the L7 DNS rule first, then `toEndpoints` with a namespace label.

---

## The programme

| # | what | risk |
|---|---|---|
| 1 | this document + the two doc corrections | none |
| 2 | ✅ **DONE 2026-09-06** — rotate the token AND split out an agent-token | highest |
| 3 | ✅ **DONE 2026-09-06** — tunnel guard, surgical nftables on home nodes. Applied to all 7, and **proven from the edge**: 2379/2380/10250 refuse, 6443 answers, cilium-health 8/8 | first rules on home nodes |
| 4 | 🔜 the contract — Kyverno policy + audit script | admission |
| 5 | 🔜 detection — alert on node-count change | none |

### 2 — rotation and agent-token, one window

Chosen because k3s rotation **already** requires restarting every server and
agent with the new token. Introducing the agent-token split in the same pass
costs no extra restarts: each node is re-keyed once instead of twice.

Repo changes: `agent-token-file` in the **server branch only** of
`config.yaml.j2` (it is a server-only key — the agent branch would refuse to
start), `token-file` in both branches gated on the var being defined, and a
role-dependent token source in both join playbooks (finding 5).

Operational order is `CLUSTER-TOKEN.md`'s runbook, unchanged: etcd snapshot,
**archive the old token** (pre-rotation snapshots need it), generate both new
tokens and record them *before* use, rotate, converge servers `serial: 1`, then
re-key agents and the edge with the agent token.

🔴 **The health gates cannot see a wrong token value.** A wrong value leaves the
apiserver running, so every node stays Ready and etcd stays quorate; the fault
surfaces the next time something tries to join. The real verification is a join
test: **a node must join as an agent with the agent token, and must FAIL to join
as a server with it.** That single test is the entire point of the change.

### 3 — the tunnel guard

`roles/wg_guard`, `hosts: k3s_nodes:!k3s_edge`, its own nftables table:

```
table inet wg_guard {
  chain input {
    type filter hook input priority -10; policy accept;
    ct state established,related accept          # replies to home-initiated flows
    ip saddr 10.250.0.1 tcp dport 6443 accept    # apiserver
    ip saddr 10.250.0.1 udp dport 8473 accept    # VXLAN
    ip saddr 10.250.0.1 tcp dport 4240 accept    # cilium-health
    ip saddr 10.250.0.1 icmp type echo-request accept
    ip saddr 10.250.0.1 drop                     # etcd, kubelet, everything else
  }
}
```

⚠️ `policy accept` and a source-address match are what make this safe: LAN
traffic never matches a rule at all, so it cannot lock anyone out of a home
node. This is deliberately **not** a general firewall.

⚠️ `ct state established` must come first. Without it, replies to connections
the *home* node opened toward the edge — apiserver→kubelet, Hubble — are dropped.

Follows `edge_firewall`'s three-layer scoping: a role-level guard assert (mirror
image: refuse if the host **is** in `k3s_edge`), the playbook `hosts:`, and
`validate: "nft -c -f %s"` so a broken ruleset never lands.

### 4 — the contract

A Kyverno `ValidatingPolicy` selecting on `homelab.techyon.dev/edge-publish`,
plus `scripts/audit-edge-exposure.py` in the shape of
`audit-protected-volumes.py` — read-only, named checks, exit 0/1/2 — covering
the cross-resource facts admission cannot see: the egress policy, the
firewall/listener parity, the namespace label, the PSA level.

Migration cost is one namespace. `ot-demo` is the only one carrying the label
and it already passes every check.

### 5 — detection

A PrometheusRule on `count(kube_node_info)` deviating from expected. Prevention
with no detection is a posture, not a control.

---

## Standing warnings

* **Rotation does not erase history.** `k3sblog` stays in the public git history
  permanently. Rotation is what makes that moot — it is the reason to rotate,
  not a side effect of it.
* **`_captured/` holds the plaintext token** for every agent and for the public
  VPS, on the workstation. Gitignored, still a copy on a laptop.
* **The edge node's own containment is not this document's subject.** That is
  `EDGE-NODE.md`, and it is working: firewalled, no CSI driver, no LAN reach.
* **Verify against the live cluster.** The previous phase landed nine defects,
  every one of them from asserting how something behaves instead of checking.
  Every check in this plan is to be run against a live host before it is written
  into a role.
