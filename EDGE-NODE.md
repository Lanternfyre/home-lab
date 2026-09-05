# Edge node — public TCP/UDP, and a VPN that is not a hole in the LAN

**Living document.** Companion to [`MODERNIZATION.md`](MODERNIZATION.md). This
one covers work that does not exist yet: a public VPS joined to the cluster as
an agent, arbitrary TCP/UDP published through it by manifest, and a client VPN
that reaches cluster services and deliberately nothing else.

Started 2026-09-05. Nothing in Phase C or D has been built. Phase B is written
and sitting on a branch.

---

## Why this exists

necronia is a TFS-style game server: anonymous clients on the internet connect
straight to a TCP game port, and the login server hands them an address and
port for the world they picked. Nothing about that is HTTP.

The existing public path cannot carry it. Cloudflare Tunnel is already carrying
two such entries and they do not work for real clients:

```yaml
- hostname: ot-login.techyon.dev
  service: tcp://ot-login.ot-demo.svc.cluster.local:7171
- hostname: ot-game.techyon.dev
  service: tcp://ot-game.ot-demo.svc.cluster.local:7172
```

A `tcp://` ingress entry requires the *client* to run `cloudflared access tcp`
locally. A game client will not do that. Those two entries are therefore
decorative, and they stay in place deliberately — see "The last gate" — as the
proof target for the thing that replaces them.

So: a VPS with a public IP, joined as a k3s **agent**, carrying the Envoy data
plane for a Gateway whose listeners are real TCP/UDP ports.

---

## What it looks like

```
                         INTERNET
                            │
              anonymous TCP / UDP game traffic
                            │
                            ▼
        ┌───────────────────────────────────────┐
        │  edge-1  (VPS, public IP)             │
        │  k3s AGENT, node-ip = 10.250.0.1      │
        │  tainted; only Envoy + Cilium land    │
        │                                       │
        │  Envoy (homelab-edge Gateway)         │
        │    hostNetwork, real listener ports   │
        │  NetBird control plane (host, not k8s)│
        │  wg0 — per-node peers, no LAN routes  │
        └────────────────┬──────────────────────┘
                         │
                 WireGuard, one peer PER HOME NODE
                 (no single home-side terminator)
                         │
        ┌────────────────▼──────────────────────┐
        │  HOME — 192.168.32.0/23, one flat L2  │
        │  k8s-lab1..3 servers, lab4..7 agents  │
        │  necronia / ot-demo pods, DBs, NAS    │
        └───────────────────────────────────────┘
```

Two flows cross that tunnel and they are **not** the same problem:

| | cluster underlay | client VPN |
|---|---|---|
| carries | edge ↔ home node IPs: 6443, VXLAN 8473, kubelet 10250 | laptops → cluster services |
| needs | symmetric, un-NATed, stable addresses | nothing much; SNAT is fine |
| built from | plain `wg-quick`, Ansible, always | NetBird (replaceable) |

Keeping them separate is the load-bearing decision in this document. Cluster
membership must never depend on a VPN control plane that can be down or
mid-upgrade, and k3s bakes `node-ip` in at registration.

---

## Findings — verified against the live cluster, do not re-derive

Each of these was checked on 2026-09-05 and each one changed the design.

1. **ServiceLB is disabled cluster-wide and must stay that way.**
   `k3s_disable: [traefik, servicelb]`. The obvious design for this whole
   feature — k3s ServiceLB with `svccontroller.k3s.cattle.io/lbpool` — is
   therefore unavailable. It is not a matter of taste: klipper binds hostPorts
   80/443/53 on every node, and its `svclb-pihole-dns` pods claiming port 53
   while stuck in ImagePullBackOff is what took k8s-lab4's DNS out and left it
   with zero CSI drivers. Re-enabling it to get `lbpool` would reintroduce
   that. **The replacement is Gateway API + a per-Gateway EnvoyProxy.**

2. **MetalLB cannot serve the edge node.** It runs in L2 mode on
   `192.168.32.0/23`; a VPS is not on that segment. The edge Gateway's Service
   must be `ClusterIP`, with Envoy reaching the network through `hostNetwork`.

3. **TCPRoute / UDPRoute / TLSRoute CRDs are already installed** (Gateway API
   experimental channel, since 2026-01-02), and `external-dns-cloudflare`
   already lists `gateway-tcproute` and `gateway-udproute` in its sources. The
   primitive exists; nothing needs installing.

4. **The EnvoyProxy CRD has every knob required.** Confirmed against the live
   v1.8.3 schema:
   `provider.kubernetes.envoyDeployment.pod.{nodeSelector,tolerations}`,
   `envoyService.type`, `envoyDeployment.patch` (for `hostNetwork`), and
   `useListenerPortAsContainerPort`. Attached per-Gateway via
   `spec.infrastructure.parametersRef`, so it affects the edge Gateway **only**
   and the three existing Gateways are untouched.

5. 🔴 **`externalTrafficPolicy: Local` kills the tempting shortcut.**
   ```
   gateway-envoy/envoy-gateway-envoy-homelab-*   ETP=Local
   dns/pihole-dns-udp                            ETP=Local
   ```
   The idea that "the VPS can reach `192.168.32.18` because it is a cluster
   member" is **false**: an edge node holding no local Envoy pod drops that
   traffic. This is why VPN clients get cluster-internal addresses rather than
   MetalLB addresses.

6. 🔴 **`trident-node-linux` will schedule itself onto a public VPS.** Its
   tolerations are `[{effect: NoExecute, operator: Exists}, {effect:
   NoSchedule, operator: Exists}]` with **no nodeSelector**. A taint alone does
   not keep the QNAP CSI driver — and the iSCSI credentials it carries — off an
   internet-facing machine. It needs an explicit nodeSelector.
   `smartctl-exporter` is in the same position and is meaningless there anyway
   (the VPS has no SATA disk). `kube-vip-ds` is safe: its nodeAffinity requires
   the control-plane label.

7. 🔴 **`config.yaml.j2` renders no `node-ip`.** Left alone, k3s on the VPS
   picks the default-route interface — the public NIC — and Cilium's VXLAN on
   UDP 8473 then rides the open internet unencrypted. `node-ip` must be the
   WireGuard address, and this is a template change, not a host tweak.

8. **The relevant Cilium facts**: `routingMode: tunnel`, `tunnelProtocol:
   vxlan`, `tunnelPort: 8473`, `kubeProxyReplacement: "false"`,
   `enableLBIPAM: false`, no transparent encryption. Pod CIDR
   `10.245.0.0/16` (a `/24` per node), services `10.43.0.0/16`, cluster DNS
   `10.43.0.10`.

9. **The LAN is one flat `/23`.** `192.168.32.0/23 dev enp3s0` — MetalLB space
   and node space share a broadcast domain; the gateway is `192.168.33.1`.
   There are not two subnets to route, only one.

10. **Public DNS already answers with RFC1918 addresses.**
    `authentik.lab.techyon.dev → 192.168.32.18`, `db.lab.techyon.dev →
    192.168.32.10`. external-dns publishes them to Cloudflare unproxied. Useful
    to know, and a reason the VPN needs a DNS override rather than a DNS
    server.

11. **The Envoy image is distroless.** No shell, no curl —
    `exec ... sh` fails with "executable file not found in $PATH". Inspect its
    admin interface with `kubectl port-forward` and a local curl.

12. **authentik pins no hostname.** No `AUTHENTIK_HOST`, no allowed-hosts, no
    cookie domain in its environment, so it derives its OIDC issuer from the
    request host. Two public hostnames therefore mint two different issuers —
    see the warning in Phase B.

---

## Decisions, and what they cost

**The underlay is per-node hub-and-spoke WireGuard.** Every host in
`k3s_nodes` gets its own peer on the VPS, `AllowedIPs` = that node's `/32`.
There is no single home-side terminator, so there is nothing to fail over: a
dead node is an ordinary dead node and Kubernetes already handles it. Rendered
from inventory, entirely Ansible-owned.

**VPN clients reach cluster services and nothing else.** No `192.168.32.0/23`,
no `.home` names, no NAS. This is a deliberate wall, and it is what makes the
rest simple: client traffic is **SNAT'd at the VPS** to `10.250.0.1`, so every
reply goes to an address that already has a route on every node. That single
choice deletes the entire asymmetric-routing problem and means **no MikroTik
configuration is required at all** — the router never learns the VPN exists.

**The API VIP is not routed.** `192.168.32.2` floats between servers and
WireGuard's `AllowedIPs` cannot express a floating address. The edge node
bootstraps against one control-plane node's real IP, reachable through that
node's own `/32` peer; afterwards the k3s agent load balancer on
`127.0.0.1:6444` discovers all three servers and fails over among them. All
three are peers, so losing the bootstrap node is survivable.

**Public HTTP stays on cloudflared.** The edge node serves only what the
tunnel cannot: raw TCP and UDP. Keeping `app.techyon.dev` on the existing path
means the edge node is never in the way of anything that already works, and the
old path stays available as a control.

**The client VPN is self-hosted NetBird**, authenticating against authentik.
Its control plane runs on the VPS **host**, not in the cluster — the remote
path in must not depend on the thing it is a path to. Note that NetBird peers
are authenticated devices, so it does nothing for necronia's anonymous players;
the two features do not overlap and neither replaces the other.

---

## Naming

| | value | why |
|---|---|---|
| inventory host | `edge-1.edge` | not `.home` — that file's contract is that `.home` names are LAN names served by Pi-hole, and this box is not on the LAN |
| OS hostname | `k8s-edge1` | the k8s node name follows the hostname, so `kubectl get nodes` reads `k8s-lab1..7` + `k8s-edge1` and the topology explains itself |
| Pi-hole entry | `address=/edge-1.edge/10.250.0.1` | resolves to the **tunnel** address — the only one a LAN client can use |
| public name | `edge-1.techyon.dev` | the real public IP; the address game clients connect to and NetBird's endpoint. Left to external-dns |
| taint | `infrastructure.techyon.dev/edge=true:NoSchedule` | |
| label | `infrastructure.techyon.dev/location=edge` | what the EnvoyProxy nodeSelector matches |

Corollary: MetalLB and kube-vip must never touch this node. MetalLB is L2 on a
segment it is not on; kube-vip's nodeAffinity already excludes it.

---

## Phases

| | what | depends on |
|---|---|---|
| **A** | authentik ingress network policy | — |
| **B** | publish authentik publicly | A |
| **C** | edge node: underlay, join, edge Gateway | — |
| **D** | NetBird self-hosted | B, C |

A and C are independent and can run in parallel. B is written already.

### Phase A — the authentik ingress policy

authentik has an egress policy and deliberately no ingress one; its own file
says an ingress allow-list "is a separate exercise with its own capture". This
is that exercise, and it is a prerequisite for B rather than part of it.

The danger is specific: authentik is what ArgoCD, Grafana, Immich, Silo,
Headlamp and the k3s API server all trust, so a wrong allow-list is a
simultaneous 403 across every one of them.

🔴 **An idle capture is worthless here**, for exactly the reason the egress
policy documents: OIDC token exchange only happens *during a sign-in*. Capture
while deliberately logging in to each consumer:

```
hubble observe -n authentik --to-pod authentik/authentik-server -f
```

Expected sources, and anything else is a surprise worth chasing before it
becomes a rule:

* `gateway-envoy` — both Envoys, plus in-cluster JWKS fetches
* `host` / `remote-node` — the apiserver validating `kubectl` tokens
* `world` — kubelet probes. **Not sloppiness**: see
  `apps/mealie/manifests/mealie-ingress.ciliumnetworkpolicy.yaml`, where a
  `fromEntities: [host, remote-node]` rule crashlooped a healthy app eight
  times, and a `fromCIDR` allow-list failed *silently*.

`canary-containment` was the model for this shape and was retired with the
canary; `ot-demo.ciliumnetworkpolicy.yaml` carries the same pattern.

### Phase B — publish authentik ✍️ written, on `feat/publish-authentik-open-tunnel`

Self-hosted NetBird authenticates clients against authentik over OIDC, and a
device enrolling from outside must reach the IdP *before* it has a tunnel.
Keeping authentik LAN-only makes the VPN circular: a new or reinstalled device
could never get in.

What the branch does:

* removes the `tunnel-gate` SecurityPolicy from `homelab-tunnel` — it
  authenticates against authentik, so it cannot stand in front of authentik
  without looping. This mirrors the LAN, where authentik has always been on the
  open Gateway for the same reason.
* adds `authentik.techyon.dev` and repoints cloudflared at it.
* retargets `tunnel-root-path.envoypatchpolicy.yaml` from the canary's Envoy
  cluster to authentik's. Its old target no longer exists, so it would have
  gone `Programmed: False` and silently removed the only defence against the
  tunnel's doubled root path — on the IdP's login page. The symptom is
  `ERR_TOO_MANY_REDIRECTS` with authentik perfectly healthy.
* adds a brand blueprint carrying the theme.

🔴 **The Gateway's meaning is inverted by this.** Attaching a route used to
publish it *behind* authentik; it now publishes it to the internet *ungated*.
Anything needing the gate must go on a new `homelab-tunnel-gated` Gateway —
never by reinstating a policy on this one.

🔴 **Two hostnames, two issuers.** A token minted through the public route
carries `https://authentik.techyon.dev/...`, while the Envoy gates pin
`https://authentik.lab.techyon.dev/...`. They do not match, and a mismatched
issuer is a 403 from the jwt provider with no useful message. Survivable only
because the clients are disjoint — the gates always see the `.lab` name,
NetBird will only ever see the public one. **No client may be reachable through
both.**

### Phase C — the edge node

Build order, each step gated:

1. **Inventory group.** A `k3s_edge` group under `k3s_agents`. It must be
   distinct because this node can never pass `46-prove-node-storage.yml` and
   must not run the iscsi/multipath baseline at all — the storage gate has to
   exempt it *explicitly*, not be worked around by hand. The repo's invariant
   ("do not uncordon until a storage proof passes") needs a stated exception
   here rather than a silent one.
2. **`node-ip` in `config.yaml.j2`** (finding 7), plus a separate
   `--agent-token` so a compromised VPS never holds the server token — see
   [`CLUSTER-TOKEN.md`](CLUSTER-TOKEN.md), which already suggests it.
3. **WireGuard, both ends, before k3s.** Ansible role rendering one peer per
   node from inventory.
   *Gate:* every home node ↔ edge ping across `wg0`, and an MTU proof with a
   large payload. VXLAN (50 bytes) inside WireGuard (1420) leaves ~1370 against
   1450 elsewhere, and inner PMTUD does not work — this fails as "large
   responses hang" rather than as an error.
4. **Taint and label first, join second**, so nothing schedules in the window.
   Plus the nodeSelector on `trident-node-linux` (finding 6) — **before** the
   join, not after.
   *Gate:* `kubectl get pods -A -o wide --field-selector spec.nodeName=k8s-edge1`
   returns Cilium and nothing else. No trident, no smartctl-exporter.
5. **EnvoyProxy CR + `homelab-edge` Gateway.** `nodeSelector` and `tolerations`
   for the edge node, `envoyService.type: ClusterIP`,
   `useListenerPortAsContainerPort: true`, `hostNetwork` via
   `envoyDeployment.patch`.
   ⚠️ Ports below 1024 need `NET_BIND_SERVICE` under hostNetwork; 7171/7172 do
   not, which is a small reason to keep the edge to high ports.
6. **The last gate — `ot-demo`, not necronia.** Attach a TCPRoute for
   `ot-login:7171` and prove a real client connects to `edge-1.techyon.dev:7171`
   without `cloudflared access tcp`. Compare against the existing
   `ot-login.techyon.dev` tunnel entry, which is the control. **Only then**
   delete the two `tcp://` entries from the cloudflared config, and only then
   point necronia at any of it.

⚠️ necronia's login server hands clients an address and port for each world, so
its config must carry the *public* name and the *edge listener* port. N worlds
means roughly 2N listeners unless they share a login server. The manifests
cannot hide this coupling; it has to be configured in the app.

### Phase D — NetBird

Control plane on the VPS host, OIDC against `authentik.techyon.dev`, clients
routed `10.43.0.0/16` only, SNAT at the VPS.

Open question worth answering before designing around it: NetBird's own
nameserver configuration may subsume the small DNS resolver this otherwise
needs — does self-hosted NetBird support a domain-match nameserver forwarding
`lab.techyon.dev` to `10.43.0.10`? If it does, `*.lab.techyon.dev` works over
the VPN with no extra service.

---

## Standing warnings

* **The edge node is the first node in this fleet that is not trusted
  infrastructure.** Every DaemonSet with a catch-all toleration is a decision
  about what runs on a public machine. Audit the list on every chart bump:
  ```
  kubectl get ds -A -o json | jq '.items[] |
    select(any(.spec.template.spec.tolerations[]?;
      .operator=="Exists" and (.key|not))) | .metadata.name'
  ```
* **`flannel-backend: none` and the storage invariants in
  [`CLAUDE.md`](CLAUDE.md) still apply**, and the storage one applies *by
  exception* here — write the exception down where the gate can see it.
* **Do not put the client VPN and the cluster underlay on one mechanism.** They
  are separated on purpose; the underlay must survive NetBird being broken.
* **Verify against the live cluster.** Most of this repo's history is things
  that looked correct and were not — including, in this document, an
  `iptables-save | grep` that returned zero matches because `sudo` had silently
  failed, and very nearly became evidence.
