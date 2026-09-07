# Cilium kube-proxy replacement — verified plan, and the road to zero hardcoded IPs

**Status: IN PROGRESS, staged. Verified against the live cluster and against
Cilium 1.20.0 / k3s v1.35.6 source on 2026-09-07.** The stage table in §5 is the
resume point; each stage records the date it landed.

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

It resolved correctly — the routing peer's policy map gained a real allow for the
envoy pod identity — **and traffic was still dropped**:

```
netbird/netbird-routing-peer <> gateway-envoy/envoy-gateway-envoy-homelab-gated-06cddf46:443 (world)
policy-verdict:none EGRESS DENIED (TCP Flags: SYN)
```

Hubble NAMES the destination as the Service while the identity it carries is
`world`. The name is enrichment; the identity is what policy matches on.

**This is not specific to NetBird.** Every policy in this estate that needs to
reach a LoadBalancer address is forced to name an address instead of a workload.

---

## 2. The actual mechanism (sharper than the first write-up)

The first version of this document said "policy is evaluated before kube-proxy's
DNAT". True, but not the useful statement. From `bpf/bpf_lxc.c` (v1.20.0) and the
live BPF maps:

* `bpf_lxc.c:178` — `svc = lb4_lookup_service(&key, is_defined(ENABLE_NODEPORT))`.
  Without NodePort the pod-side lookup never switches to the *internal* scope of an
  `externalTrafficPolicy: Local` service.
* `ENABLE_NODEPORT` is defined **only** under `kubeProxyReplacement: true`
  (`pkg/datapath/linux/config/config.go`). `pkg/kpr/kpr.go` in 1.20 has exactly two
  flags, `kube-proxy-replacement` and `bpf-lb-sock`. **There is no partial NodePort
  mode any more** — `nodePort.enabled` / `externalIPs.enabled` are gone.
* Measured on k8s-lab3 with KPR off: 312 frontends in the BPF LB map, **zero of
  LoadBalancer type**. `192.168.32.16` (argocd) is absent entirely, so a pod's SYN
  leaves untranslated and carries `world`:

  ```
  monitoring/grafana -> argocd/argocd-server:443 (world) to-stack FORWARDED
  ```

* 🔴 **PR #150 made it worse.** `spec.addresses` on a Gateway makes Envoy Gateway
  set `spec.externalIPs` on the Service. Cilium *does* load ExternalIPs frontends
  even without KPR — as `[ExternalIPs, Local, two-scopes]` with **only the
  node-local backend**. Measured 2026-09-07 with `curl` from pods:

  | from a pod on | → .19 (envoy on lab1) | → .18 (envoy on lab2) | → .11 (envoy on lab5) |
  |---|---|---|---|
  | lab1 | connects | **fails** | **fails** |
  | lab3 | **fails** | **fails** | **fails** |

  Pod → LAN-gateway address works **only from the node hosting that Envoy pod**.
  `argocd.lab` resolves to .18 and `grafana.lab` to .19, so any pod using those
  names is placement-dependent, and the routing peer (on lab3) cannot reach either.
  Operator decision 2026-09-07: leave this until stage B2 rather than fix it
  ahead; B2b removes the addresses.

**Consequence:** the identity form needs LoadBalancer/ExternalIP frontends
translated at the pod, and in Cilium 1.20 that means `kubeProxyReplacement: true`.

### Would `socketLB` alone have been enough? No.

* Socket LB translates at `connect()` against the **same** BPF map. Without KPR the
  LoadBalancer frontends are not in it, so there is nothing to translate to.
* Full socket LB compiles per-packet LB **out** of `bpf_lxc`
  (`ENABLE_PER_PACKET_LB` needs `!ENABLE_SOCKET_LB_FULL || ENABLE_SOCKET_LB_HOST_ONLY
  || ENABLE_L7_LB || ENABLE_SCTP`), so pods would *lose* the ClusterIP translation
  they have today for anything not socket-originated.
* Under KPR socket LB is forced on anyway (`kpr.go`). The rootless NetBird peer
  forwards VPN traffic through real sockets (`client/firewall/uspfilter/forwarder/`
  uses `net.Dialer.DialContext`), so it works under socket LB.
* ⚠️ `bpf_sock.c:sock4_skip_xlate` refuses to translate an **ExternalIPs**-type
  frontend whose address is not the node's own (external-IP MITM mitigation). That
  is exactly what `spec.addresses` produces. So under full socket LB the identity
  test for .18/.19 **cannot pass until the addresses are deleted** — hence B2b.

---

## 3. The end state — no hardcoded addresses anywhere

| where | what | removed |
|---|---|---|
| `apps/envoy-gateway/manifests/*.gateway.yaml` | `spec.addresses` on 3 Gateways | B2b, 2026-09-07 |
| `apps/netbird/manifests/netbird-egress.ciliumnetworkpolicy.yaml` | `toCIDRSet` .18/.19 | B3, 2026-09-07 — `toServices` by Gateway name |
| `apps/netbird-ops/chart-values.yaml` | routes `192.168.32.18/32`, `.19/32` | B3, 2026-09-07 — a `fromService` route: the reconciler reads each Gateway Service's address at apply time (NetBird Routes take no wildcard and the Android client installs no DNS route at all) |

**Reached.** No Gateway address is written anywhere in this repository. What
still names a `192.168.32.x` address in git is the MetalLB pool itself, and the
services that legitimately PIN one (`pihole-dns` at `.53`, the databases' LB
Services): those are declared allocations, not values MetalLB happened to hand
out.

---

## 4. Two findings that came out of verifying, and precede everything

### 4a. ArgoCD was on the public internet through the edge node

Probed 2026-09-07 from outside the cluster: `167.86.81.59:32497` and `:31066` —
`argocd-server`'s LoadBalancer NodePorts, `externalTrafficPolicy: Cluster` —
**answered**. kube-proxy on the edge DNATs a NodePort in `prerouting` and forwards
it over VXLAN; `edge_firewall` has an `input` hook with policy drop and **no
`forward` hook**, so it never sees the packet. `mealie` (31447) was the same class.
eTP=Local NodePorts were filtered only because the edge holds no local endpoint.

Under KPR this gets *harder* to close, not easier: BPF NodePort runs at tc
ingress, **before nftables**. It has to be closed at the source (stage 0) and
kept closed by `nodePort.addresses` (stage B1).

### 4b. What the doc had right, verified

| claim | verdict |
|---|---|
| kube-proxy runs in-process | ✅ `127.0.0.1:10249`/`:10256` on every node sampled, ~885 `KUBE-*` nat rules per node, no pods |
| `k8sServiceHost: 127.0.0.1`, `k8sServicePort: 6444` | ✅ but as DaemonSet/operator **env** `KUBERNETES_SERVICE_HOST`, not a ConfigMap key. Listening on server, agent and edge |
| k3s CLI args may override `config.yaml` | inert: ExecStart is literally `k3s server` / `k3s agent` on all 8 |
| MetalLB owns L2 + IPAM, Cilium owns neither | ✅ MetalLB 0.15.3 L2, 7 speakers (not the edge); `enable-lb-ipam=false`, no `CiliumL2AnnouncementPolicy` |
| "no ipcache entry for .19" | stale — it maps to a CIDR identity now (the `toCIDRSet` rule). The datapath problem is §2 |

---

## 5. Stages, with a gate between each

Do **not** batch these. Each is separately reversible; the combination is not.

| stage | what | sudo? | landed |
|---|---|---|---|
| **0** | close the public NodePort door: every LoadBalancer NodePort released (MANUAL-STEPS §0c form), `pihole-dhcp` off, `NODEPORT` check + `--probe` in `audit-edge-exposure.py` | no | 2026-09-07, #153 |
| **A** | NetBird bootstrap: wipe the management PVC, reconciler becomes owner, routing peer re-enrols, routes created, operator promoted to admin, phone enrolled | no | 2026-09-07 (charts 0.1.10→0.1.14, #155–#171) |
| **B1** | prerequisites in git: `roles/cilium` verify assert, server-only `disable-kube-proxy` in the k3s template, `nodePort.addresses` in the Cilium values, before-picture captured | no | 2026-09-07, #154 (helm rev 7) |
| **B2a** | `kubeProxyReplacement: "true"` — DaemonSet roll on 8, kube-proxy stays and becomes redundant | no | 2026-09-07, #157 (helm rev 8) — see the finding below |
| **B2b** | delete `spec.addresses` from the three Gateways | no | 2026-09-07, #158 |
| **B3** | the payoff: `toServices` back, `toCIDRSet` out, NetBird routes by `domains` | no | 2026-09-07, #160 + the port fix below |
| **B4** | `disable-kube-proxy` in k3s, servers then agents, stale `KUBE-*` rules flushed | **yes** | 2026-09-07, #161 + #164 — see the two findings below |

Why this order:

* **A before B.** Small, authorised, and the reconciler Job fails on every
  `netbird-ops` sync today (403). Its end-to-end proof (phone → `grafana.lab`) is
  deferred to the B2b gate because of §2's #150 finding.
* **B2 before B4, and the payoff between them.** The payoff does not need
  kube-proxy removed: with `kubeProxyReplacement: true` and kube-proxy still present
  (a supported overlap) the pod datapath already translates before policy. B4 is
  hygiene that needs sudo and a window; it must not hold the payoff hostage.
* 🔴 **B4's order is forced by k3s, not by preference.** `disable-kube-proxy` is a
  *server* flag; agents fetch it at startup from `/v1-k3s/config`
  (`pkg/agent/config/config.go`, `pkg/daemons/agent/agent.go`). An agent restarted
  before every server has it can fetch `false` from a stale server. So: all three
  servers first (`20-config-converge.yml --limit k3s_servers`), then agents
  (`--limit k3s_agents`, which includes the edge). The playbook's reverse-inventory
  default would restart agents first — wrong for this change. The agent branch of
  `config.yaml.j2` must never render the key: agents reject unknown keys and refuse
  to start.

---

### 🔴 Hard-won, B4: the first server restart since a token rotation is the last one that works

k8s-3, the first server restarted for B4, died on k3s's reconcile guard
(`cred/passwd newer than datastore`) and stayed down from 12:44 to 13:09. Not
kube-proxy at all: `k3s token rotate` (2026-09-06) only re-encrypts the copy of
the bootstrap data in etcd, each server's next start rewrites its own `passwd`
with the new tokens, and the restart after that is refused. Removing the file
buys one start. The durable fix is `k3s certificate rotate-ca --path=<EMPTY
dir>` on a running server, which re-saves the on-disk data to etcd; the
rotation playbook now does that as phase 5b. Full account in
`CLUSTER-TOKEN.md` → "What 2026-09-07 taught us".

Compounded by a repo bug: `roles/k3s_config` dropped the `token-file` /
`agent-token-file` lines from `config.yaml` on every converge that carried no
secret, so step 1 of this stage silently removed the agent-token split from all
three servers' config and k8s-3 restarted without it. Fixed in #164; the servers
were re-rendered and k8s-3 restarted once more before k8s-2 and k8s-1.

Order and gates held exactly as designed: k8s-3 → k8s-2 → k8s-1 → edge → k8s-7
→ k8s-6 → k8s-5 → k8s-4, each gated (no kube-proxy listeners, Ready, every pod
Ready, KPR True, health 8/8, host DNS via `.53`, its MetalLB addresses, the edge
ports from outside). A single server failing left two healthy, which is the
whole reason for serial.

### 🔴 Hard-won, B3: a `toServices` rule's `toPorts` is the BACKEND port

Policy runs after socket LB has rewritten the connection to the backend pod, so
the port it sees is the pod's. Envoy Gateway binds privileged listeners +10000:
the Gateways' `:443` is `10443` in the pod. Measured 2026-09-07 with the restored
#149 rule (`port: "443"`): the routing peer's SYN carried the Envoy pod identity
(`ID:14942`, no longer `world`) and was still `EGRESS DENIED`. `port: "10443"`
is the rule that matches. Any future `toServices` rule in this estate names the
targetPort, never the Service port.

### 🔴 Hard-won, B2a: the flip kills every established pod→Service connection, and PostgreSQL keeps the corpses

Measured 2026-09-07, right after the DaemonSet roll. Connections that existed
BEFORE the flip were translated per-packet in `bpf_lxc` with conntrack entries;
the regenerated pod programs (socket LB, per-packet LB compiled out) no longer
carry that reverse NAT, so every such TCP session died on the client side and the
client reconnected through socket LB. The **server** side never saw a FIN. On
`postgres-ha` that left **75 idle zombie backends** — reportportal-api 27,
reportportal-uat 24, reportportal-jobs 11, authentik-worker 8, authentik-server
5 — with `state_change` equal to the roll time, against `max_connections=100`.
`authentik-server` could not open a connection ("remaining connection slots are
reserved for roles with the SUPERUSER attribute"), failed its startup probe and
crash-looped; with it, every login on the gated Gateway. `tcp_keepalives_idle`
is 0 on both CNPG clusters, i.e. the kernel's 7200 s, so untouched the zombies
live for **two hours**.

The cure is one statement on the primary, and it is the operator's call because
it terminates sessions:

```sql
select count(pg_terminate_backend(pid)) from pg_stat_activity
 where backend_type='client backend' and state='idle'
   and state_change < now() - interval '10 minutes';
```

Lessons: (1) a datapath-mode switch is a connection-reset event for every pod on
the node, plan it like one; (2) any server that counts connections needs
`tcp_keepalives_idle/interval/count` set low enough that a vanished client is
reaped in minutes — follow-up for both CNPG clusters; (3) `roles/cilium`'s
post-verify caught it ("1 Running pod not Ready") — keep that assert.

## 6. What changes

| component | change | blast radius |
|---|---|---|
| Cilium (B1) | `nodePort.addresses: ["192.168.33.0/24", "10.250.0.0/24"]` | none yet. Never the edge's public IP, never the VIP `192.168.32.2` (a secondary on lab2's `eno1`), never `flannel.1` |
| Cilium (B2a) | `kubeProxyReplacement: "true"` in `gitops/cilium-values-production.yaml` | DaemonSet roll on all 8 |
| Cilium | socket LB: Cilium default (full, pods included) — operator's choice | per-packet LB is compiled out of `bpf_lxc`; a pod emitting non-socket traffic to a Service IP is no longer translated. None known here |
| Ansible (B1) | `roles/cilium` post-verify asserts what the values file says, not `False` | apply would otherwise fail its own verify |
| k3s (B4) | `disable-kube-proxy: true`, **server branch only** | restarts k3s on all 8, serial |
| unchanged, deliberately | `bpf.hostLegacyRouting: true`, iptables masquerade, tunnel vxlan/8473, `enableLBIPAM: false`, MetalLB config | one change at a time |

---

## 7. Verification — behaviour, never file contents

### B2a gate (kube-proxy still running)

1. ConfigMap `kube-proxy-replacement=true`, `bpf-lb-sock=true`.
2. `cilium-dbg status --verbose` "KubeProxyReplacement Details": on **lab2** the
   NodePort address must be `192.168.33.2`, not the VIP; on the **edge** `eth0`
   must carry no NodePort address and `wg0 10.250.0.1` must.
3. `cilium-dbg bpf lb list | grep 192.168.32.16` on lab3 → LoadBalancer frontend
   with both argocd backends.
4. Identity on a LoadBalancer frontend: grafana (lab3) `curl` .16 connects and
   Hubble shows the argocd-server pod **identity**, not `world`. (.18/.19 stay
   blocked until B2b — the external-IP mitigation, §2.)
5. apiserver from a pod via `10.43.0.1`; CoreDNS from a pod over UDP **and** TCP;
   node DNS via `scripts/diagnose-node-dns.sh` (nodes resolve through the `.53` LB
   address — host-namespace socket LB on an eTP=Local service, the least obvious
   path).
6. The MetalLB protocol below, before/after.
7. Edge: 7171/7172 TCP and 7173 UDP answer from the internet; Hubble on lab4 shows
   `remote-node → ot-demo/ot-login:7171`; `cilium-health` 8/8; no NodePort answers
   on `167.86.81.59`.
8. ArgoCD via `192.168.32.16` and via `argocd.lab.techyon.dev`.
9. `hubble observe -t drop` on lab1/lab3/edge: no new service-related drops.
10. `scripts/audit-edge-exposure.py` exits 0.

### B2b gate

1. The three Services lose `externalIPs`, keep their ingress address (MetalLB
   re-allocates only on Service deletion); external-dns unchanged.
2. `cilium-dbg bpf lb list | grep 192.168.32.19` on lab3 → **LoadBalancer** type,
   backend `10.245.1.53:10443`.
3. **The §1 line:** grafana (lab3) `curl` .19/.18/.11 all connect, and Hubble shows
   the Envoy pod **identity**. That single line is the whole point of the exercise.
4. From the phone via NetBird, `https://grafana.lab.techyon.dev` loads — ✅
   **2026-09-07 15:1x, operator-confirmed**, after the two `/32` routes derived
   from the Gateway Services were live on the routing peer. It needed BOTH: the
   gated Gateway (`.19`) redirects the login to `authentik.lab`, which lives on
   the plain Gateway (`.18`); a route to `.19` alone loads forever at the
   redirect. The operator spotted that.

### "Verify MetalLB L2 still works" — made concrete

MetalLB answers ARP for a LB address from one elected home node (eTP=Local → only
a node holding a local endpoint). The frame lands on that node's NIC. Today
kube-proxy DNATs in iptables; after B2a Cilium's `from-netdev` BPF DNATs first.
MetalLB is not touched. The failure to detect: ARP still answered, SYN never
forwarded — indistinguishable from "slow" without the rows below. From the
workstation (`192.168.33.8`), before and after:

| check | how | pass |
|---|---|---|
| owner unchanged | `ping -c1 <LB>; ip neigh show <LB>` → MAC → node | same as baseline (.19 lab1, .18/.16 lab2, .53 lab7) |
| forwards, not just ARPs | `curl -sk -o /dev/null -m5 -w '%{http_code} %{time_connect}'` for .11 .13 .14 .16 .18 .19; `nc -z` .10/.15:5432; `dig @192.168.32.53` UDP and `+tcp` | connect ≤ baseline ×2, no timeouts |
| BPF owns the flow | owner node: `cilium-dbg bpf ct list global \| grep <LB>` after a request | an entry with the workstation address |
| kube-proxy no longer sees it | owner node: `iptables -t nat -L KUBE-SERVICES -v -n \| grep <LB>` pkts | flat while traffic flows (rises today) |
| eTP=Local keeps the client IP | Envoy access log / pihole query log | `192.168.33.8`, not a node address |
| eTP=Cluster remote backend | `.16` (owner lab2, backends lab4/lab5) | connects; SNAT to node IP as today |
| shared address | `.53` TCP + UDP | both answer |
| Hubble on the owner | `hubble observe --to-ip <LB>` | FORWARDED, no DROPPED |

### B4 runbook (sudo; the operator drives, one node at a time)

Precondition: SSH to a control-plane node open and confirmed. Rollback is by
SSH, not kubectl (§8).

```sh
cd ansible
# 1. render config.yaml on the servers -- converges, NEVER restarts
ansible-playbook site.yml --limit k3s_servers --ask-become-pass
# 2. servers first, serial, etcd-gated, k8s-1 last. Agents fetch the flag from
#    a server at startup, so no agent may restart before this finishes.
ansible-playbook playbooks/20-config-converge.yml --limit k3s_servers --ask-become-pass
# 3. per-server gate (behaviour, not files), then the agents the same way
ansible-playbook playbooks/20-config-converge.yml --limit k3s_agents --ask-become-pass
# 4. flush what kube-proxy left behind, every node, then the gate again
ansible k3s_nodes -b --ask-become-pass -m shell -a 'iptables-save | grep -v KUBE | iptables-restore'
```

### B4 gate, per node

`ss -ltn` shows no `127.0.0.1:10249`/`10256` (kube-proxy's metrics and healthz
are the only listeners it owns); a pod on that node reaches `10.43.0.1` and a LB
address; node DNS resolves; the edge still answers 7171/7172 from outside. Then
the flush above, and the gate again: `iptables -t nat -S | grep -c KUBE` reads 0.
nft-native tables (`edge_firewall`, `wg_guard`) are untouched by the flush.

---

## 8. Rollback

🔴 **Rollback of B4 is NOT `kubectl`.** If service networking is broken, ArgoCD is
broken too. Have SSH to a control-plane node open and confirmed before B4.

* B2a: flip the value back, `ansible-playbook playbooks/34-cilium.yml` —
  workstation only, no sudo. kube-proxy is still there, so nothing is lost.
* B2b: `git revert` restores the addresses, and the #150 regression with them.
* B4: `k3s_disable_kube_proxy: false`, `20-config-converge.yml` servers then
  agents, `--ask-become-pass`.

---

## 9. State of the NetBird work

**Working:** management/signal/relay/routing-peer on 0.78.1; gRPC over h2c
through the edge proven from the public internet; Android peer enrolled; the 15s
Envoy stream cuts fixed.

**Stage A done 2026-09-07.** The management PVC (`local-path`, `reclaim=Delete`,
on lab7) was wiped; the reconciler (chart `0.1.11`, the `netbird-ops` app, an
ArgoCD PostSync hook Job) authenticated first and is the account **owner**; it
created the setup key, wrote `netbird/netbird-setup-key`, the routing peer
re-enrolled on it (emptyDir state, `NB_SETUP_KEY` read at pod start — delete the
pod to re-enrol), the two routes exist, and the operator's login was approved and
promoted to admin. What it took, all measured:

* 🔴 **The first wipe was lost to a browser tab.** 54 s after fresh management
  came up, the dashboard's auto-refresh (a valid OIDC session) created the
  account with the human as owner; the reconciler then joined as a user *pending
  approval*. Second wipe: the `netbird-ops` sync was fired the second the pod
  reported Ready, with every dashboard tab closed. The window is seconds, not
  minutes.
* Chart `0.1.9` matched the routing peer on the API's `hostname` (the OS hostname,
  i.e. the pod name) — it can never equal the `NB_HOSTNAME` name. `0.1.10` matches
  `name`/`dns_label`.
* Routes need a non-empty distribution `groups` list (`422` otherwise). `0.1.11`
  resolves `routeGroups` (default `["All"]`) by name, and accepts `domains` routes
  for stage B3.
* NetBird 0.78 puts every new login in *pending approval*; the reconciler now
  approves (`POST /api/users/{id}/approve`) before promoting.
* An ArgoCD sync whose PostSync hook keeps failing **retries for ~15 min and
  blocks the next sync**, so a chart bump that fixes the hook does not apply until
  the old operation is terminated (`status.operationState.phase: Terminating`).

⚠️ Order still matters for any future rebuild: the reconciler must authenticate
BEFORE any human session reaches management, tabs included.

⚠️ **Routes by name did not survive contact with the phone.** NetBird network
Routes take no wildcard (`*.lab.techyon.dev` was accepted and matched nothing),
and the Android client (0.71) installs no DNS route at all — one name or twenty.
The route is now `fromService`: the reconciler reads each LAN Gateway Service's
LoadBalancer address from the cluster at apply time and keeps one `/32` per
Gateway. No address is written anywhere; a moved address moves the route on the
next sync. Wildcard names live in NetBird's Networks API; moving there is a
follow-up, not a need.

🔴 **The reconciler blocked the operator.** NetBird represents a user pending
approval as blocked; chart `0.1.10`–`0.1.13` approved the user and then
promoted them with the `is_blocked` value read *before* approving — account
events 11:23:47: approved, role updated, blocked, all by the reconciler. A
blocked user's peers are refused at `Sync`, so the phone cycled
connected → connecting for an hour while routing was blamed. Chart `0.1.14`
sends `is_blocked: false` on every pass. The tell was the edge Envoy trace:
`Login` 200, then `Sync` 200 with **zero bytes in 40 ms**, then the client
closing its own relay session — the management refusing a peer silently.

⚠️ **The routing peer's identity lives in an emptyDir.** Any pod recreation
registers a new peer with the same name; the old record lingers, and until chart
`0.1.13` the route stayed bound to it (found when B4's k3s restart on lab3 took
containerd, and the pod, with it: the phone connected fine and `grafana.lab`
loaded forever). The reconciler now binds the route to the newest incarnation
and prunes the dead ones (`peerPrune: true`).

⚠️ Until B2b, the routing peer on any node but lab1 cannot reach .19 (§2), so
"the phone loads grafana.lab" is a B2b check, not a stage-A check.
