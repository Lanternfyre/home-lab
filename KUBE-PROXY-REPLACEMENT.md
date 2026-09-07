# Cilium kube-proxy replacement — plan, and the road to zero hardcoded IPs

**Status: PLANNED, not started.** Nothing in this document has been applied.
Written 2026-09-07 as a handoff, so the work starts from a document rather than
from a session's memory.

The goal is not kube-proxy replacement for its own sake. It is that **service
traffic should carry workload identity**, so policy can name what it means
instead of naming an address that nobody guarantees.

---

## 1. Why — the measurement that forced this

A CiliumNetworkPolicy was written to let the NetBird routing peer reach the LAN
gateways by LABEL rather than by address:

```yaml
- toServices:
    - k8sServiceSelector:
        namespace: gateway-envoy
        selector:
          matchExpressions:
            - key: gateway.envoyproxy.io/owning-gateway-name
              operator: In
              values: [homelab, homelab-gated]
```

It resolved correctly. The routing peer's policy map gained a real allow:

```
k8s:app.kubernetes.io/name=envoy
k8s:gateway.envoyproxy.io/owning-gateway-name=homelab-gated
k8s:gateway.envoyproxy.io/owning-gateway-namespace=gateway-envoy
```

**And traffic was still dropped:**

```
netbird/netbird-routing-peer <> gateway-envoy/envoy-gateway-envoy-homelab-gated-06cddf46:443 (world)
policy-verdict:none EGRESS DENIED (TCP Flags: SYN)
```

🔴 **Read that flow carefully: Hubble NAMES the destination as the Service,
while the identity it carries is `world`.** The name in the log is cosmetic
enrichment; the identity is what policy matches on. Confirmed alongside it:

```
192.168.32.19  ->  no ipcache entry at all  ->  falls back to `world`
kube-proxy-replacement = false
bpf-lb-sock            = false
```

**The mechanism.** Cilium enforces egress policy at the pod endpoint using the
destination IP's security identity. The packet is addressed to the LoadBalancer
*frontend*. kube-proxy's DNAT to the backend pod happens later, in iptables —
by which point the packet has already been dropped. So a rule naming backends
can never match, and `toCIDRSet` "works" only because it CREATES an ipcache
entry for the frontend address.

**This is not specific to NetBird.** Every policy in this estate that needs to
reach a Service is forced to name an address instead of a workload. That is the
actual problem.

---

## 2. The end state — no hardcoded addresses anywhere

Today three places carry an address that nobody guarantees:

| where | what | why it is there |
|---|---|---|
| `apps/envoy-gateway/manifests/*.gateway.yaml` | `spec.addresses` on 3 Gateways | added so the CIDR below is at least *honest* |
| `apps/netbird/manifests/netbird-egress.ciliumnetworkpolicy.yaml` | `toCIDRSet` .18/.19 | the only form that matches, per §1 |
| `apps/netbird-ops/chart-values.yaml` | routes `192.168.32.18/32`, `.19/32` | NetBird routes |

⚠️ **All three are crutches, and this plan removes all three.** They exist only
because policy cannot name a Service today.

After the work:

* the CNP names Gateways by label (`toServices`) — no address
* the Gateways stop declaring `spec.addresses` — MetalLB may allocate freely
  again, because nothing references the result
* NetBird routes use `domains: ["*.lab.techyon.dev"]` instead of CIDRs —
  verified available in the NetBird API (`domains`, "dynamically resolved,
  conflicts with network")

---

## 3. What changes

| component | change | blast radius |
|---|---|---|
| k3s | `disable-kube-proxy: true` | **restarts k3s on all 8 nodes** — kube-proxy runs IN-PROCESS in k3s, there is no DaemonSet to drain |
| Cilium | `kubeProxyReplacement: "true"` in `gitops/cilium-values-production.yaml` | DaemonSet roll on all 8 |
| already correct | `k8sServiceHost: 127.0.0.1`, `k8sServicePort: 6444` | **load-bearing** — see below |
| revisit | `bpf.hostLegacyRouting: true` | set deliberately; re-examine AFTER, never in the same change |

🔴 **`k8sServiceHost`/`k8sServicePort` are what make this survivable.** Without
kube-proxy, nothing can reach the apiserver through its ClusterIP until Cilium
is running — and Cilium needs the apiserver to start. The existing
`127.0.0.1:6444` (the k3s agent load balancer) breaks that circularity. **Verify
it is still set before touching anything.** If it were empty, this change
bricks the cluster.

⚠️ **k3s CLI args in the systemd unit take precedence over `config.yaml`** (see
CLAUDE.md). Confirm `disable-kube-proxy` actually reaches the process — check
the running args, not the rendered file.

---

## 4. The risky interaction: MetalLB is L2

```
l2advertisement.metallb.io/l2adv-lb-pool-32   ["lb-pool-32"]
enableLBIPAM: false          # Cilium is NOT doing LB IPAM
```

MetalLB assigns the address and answers ARP for it; the *datapath* for that
frontend moves from kube-proxy's iptables to Cilium's LB. These are meant to
coexist, and this is the part of the change with the least margin for
assumption.

⚠️ **Measure it, do not reason about it.** A LoadBalancer that still ARPs but no
longer forwards looks identical from outside to one that is simply slow.

---

## 5. Sequence, with a gate between each step

Do **not** batch these. Each step is separately reversible; the combination is
not.

1. **Pre-flight (read-only).** Record `kubectl get svc -A` with allocated IPs,
   `cilium-dbg status`, and a Hubble sample showing `world` identities on
   service traffic — that sample is the before-picture the whole change is
   judged against.
2. **Cilium first, kube-proxy still running.** `kubeProxyReplacement: "true"`
   while kube-proxy is present is a supported overlap; Cilium takes over and
   kube-proxy's rules become redundant rather than conflicting. This is the
   reversible half — verify §6 fully here.
3. **Then disable kube-proxy in k3s**, one node first, and re-run §6 on that
   node before the rest.
4. **Only then** the follow-ups in §7.

⚠️ `kubeProxyReplacement` is cluster-wide in Cilium: step 2 cannot be done for
one node. Step 3 can.

---

## 6. Verification — behaviour, never file contents

* apiserver reachable **from inside a pod** via the `kubernetes` ClusterIP
* ClusterIP, NodePort and LoadBalancer each proven with real traffic
* MetalLB still answers ARP for `lb-pool-32`, and the frontend still forwards
* 🔴 **the edge node's published game ports** (`ot-demo` 7171/7172/7173) — the
  edge is a k3s agent too and its DNAT path is the least like the others
* ArgoCD reachable, because it is the tool you would need to fix anything
* CoreDNS resolving from a pod
* Hubble shows a **pod identity** where §1 showed `world`. That single line is
  the whole point of the exercise
* `scripts/audit-edge-exposure.py` exits 0

---

## 7. Follow-ups this unlocks — the actual payoff

Land these only after §6 passes:

1. **Restore the `toServices` rule** — home-lab PR #149 has it written; it was
   reverted by #150 only because the datapath could not support it.
2. **Delete `spec.addresses`** from the three Gateways. They exist solely so the
   CIDR rule is honest; once no rule names an address, MetalLB can allocate
   freely again.
3. **NetBird routes by domain**: `domains: ["*.lab.techyon.dev"]` in
   `apps/netbird-ops/chart-values.yaml` instead of the two `/32`s.

At that point no gateway address is written down anywhere.

---

## 8. Rollback

🔴 **Rollback is NOT `kubectl`.** If service networking is broken, ArgoCD is
broken too, and so is the path you would normally use to revert.

* re-enable kube-proxy: revert `k3s_config` and run the k3s play, per node, over
  SSH
* revert `kubeProxyReplacement` in `gitops/cilium-values-production.yaml` and
  apply Cilium via `ansible-playbook playbooks/34-cilium.yml`
* both need an operator at a keyboard with `--ask-become-pass`

Have SSH to at least one control-plane node open and confirmed working BEFORE
starting step 3.

---

## 9. State of the NetBird work this came out of

Not part of this change, but mid-flight, and the next session needs it.

**Working:** management/signal/relay/routing-peer on 0.78.1; gRPC over h2c
through the edge proven from the public internet; dashboard login; Android peer
enrolled; the 15s Envoy stream cuts fixed; VPN traffic passing.

**Built, not yet exercised:** the reconciler (chart `0.1.9`, released as the
`netbird-ops` app). Its authentik identity works end to end — a
`client_credentials` token with correct `iss`, `aud`, `sub` and the
`netbird/wt_account_domain` claims.

🔴 **The one blocking fact.** The reconciler authenticated into an account **of
its own**, not the existing one:

```
peers visible to the service identity : 0     (the real account has 2)
users in that account                 : only netbird-reconciler
```

The existing account was created before the domain-routing claims existed, so
`GetAccountIDByPrivateDomain("techyon.dev")` finds nothing to join and
`addNewPrivateAccount` makes a new one. Against the current account the
reconciler is a regular user and gets **403** on `/api/setup-keys` and
`/api/routes` — measured.

**The agreed fix, authorised by the operator, not yet done:** wipe the
management PVC (`reclaim=Delete`, `local-path` — clean, no Trident leak, and the
CLAUDE.md Retain warning does not apply). On a fresh account the reconciler
authenticates first, `addNewPrivateAccount` makes it the **owner**, and it then
creates the setup key, writes it to `netbird/netbird-setup-key`, creates the
routes, and promotes `adminEmails` on the pass after their first login. The
routing peer re-enrols on the new key; the phone must be re-enrolled by hand.

⚠️ Order matters: the reconciler must authenticate BEFORE any human logs in, or
the human owns the account and the reconciler is a regular user again.
