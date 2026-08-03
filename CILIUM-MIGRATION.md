# Phase 3 — flannel → Cilium 1.20.0

The runbook and the rollback. Companion to [`MODERNIZATION.md`](MODERNIZATION.md)
Phase 3, which holds the reasoning; this file holds the procedure.

Values: [`gitops/cilium-values-migration.yaml`](gitops/cilium-values-migration.yaml)
(stages 1–4) and [`gitops/cilium-values-production.yaml`](gitops/cilium-values-production.yaml)
(stage 5).

Last updated: 2026-08-03. **Nothing in here has been executed yet.**

---

## The shape of the migration, and why it is this shape

The single question that decides everything: **does a pod on a Cilium node
still reach a pod on a flannel node during the migration window?**

**Yes.** Upstream, verbatim: *"While pods on a given node can only be attached
to one network, they have access to both Cilium and non-Cilium pods while the
migration is taking place"*, because *"as long as Cilium and the existing CNI
use a separate IP range, the Linux routing table takes care of separating
traffic."*

That is why this can be one node at a time over days rather than a single
all-cluster maintenance window. It rests on exactly three values —
`ipam.operator.clusterPoolIPv4PodCIDRList` (10.245/16, disjoint from flannel's
10.42/16), `tunnelPort: 8473` (flannel keeps 8472), and
`bpf.hostLegacyRouting: true` (what actually routes between the two overlays).
**Change any of those three and the per-node shape stops being valid.**

### What is different here from upstream's guide

Upstream assumes the old CNI is an independently-manageable DaemonSet you can
scale down. **In k3s, flannel runs inside the k3s server process.** So:

- there is nothing to scale down per node; flannel keeps running everywhere
  for the whole migration, which is fine and is the intended state
- `flannel-backend: none` is the *final* step, applied to all five nodes at
  once, and it is a k3s config + restart — not a Kubernetes object
- the guard in `roles/k3s_config/templates/config.yaml.j2` already refuses to
  render it as part of routine convergence. Leave that guard alone.

---

## Prerequisites, all verified 2026-08-03

| claim | how it was checked | result |
|---|---|---|
| Cilium 1.20.0 exists | `helm search repo cilium/cilium --versions` | ✅ latest release |
| supports k8s 1.35 | upstream requirements | ✅ 1.33–1.36; cluster is 1.35.6 |
| no kube-proxy DaemonSet | `kubectl -n kube-system get ds kube-proxy` | ✅ NotFound — it is in-process |
| MetalLB owns the LB pool | `get ipaddresspool -A` | ✅ L2, 192.168.32.10-250, 8 Services |
| `/opt/cni/bin` exists | `ls` on k8s-lab5 | ❌ **does not exist** |
| `/etc/cni/net.d` exists | `ls` on k8s-lab5 | ❌ **does not exist** |
| k3s API proxy on loopback | `ss -tln` on lab1 + lab5 | ✅ `127.0.0.1:6444` on both |
| chart creates no webhooks/CRDs | `helm template` + `grep kind:` | ✅ none — unlike Envoy Gateway |
| kubelet probes survive policy | upstream | ✅ host→pod ingress is implicitly allowed |

### ⚠️ Open, and it needs sudo — ask before stage 2

`/var/lib/rancher/k3s/agent/etc/containerd/config.toml` could not be read
(0600). Two things are unconfirmed:

1. that containerd's `bin_dir` really is `/var/lib/rancher/k3s/data/cni`
2. that its `conf_dir` really is `/var/lib/rancher/k3s/agent/etc/cni/net.d`

Both are the documented k3s defaults and both directories exist while the two
chart defaults do not, so the inference is strong — but this repo's own
standing rule is *assert values, never presence*. **Read that file before
stage 2.** One command:

```bash
sudo grep -E 'bin_dir|conf_dir' /var/lib/rancher/k3s/agent/etc/containerd/config.toml
```

### ⚠️ A k3s version change rewrites the CNI bin directory

`/var/lib/rancher/k3s/data/cni/` is **not a normal directory** — it is a set of
symlinks into the versioned, content-addressed `data/<hash>/bin/cni`, and its
mtime on k8s-lab5 matches the last k3s upgrade to the second:

```
drwxr-xr-x cni                                    Aug  2 19:24
lrwxrwxrwx current -> .../ab38b9a07efde016...     Aug  2 19:24
lrwxrwxrwx previous -> .../65415f7708224bbf...    Aug  2 15:29
```

This is the same shape as the finding that already cost a real outage here:
*a k3s VERSION change reverts custom addon manifests*. A future hop can remove
the `cilium-cni` binary Cilium installed into that directory.

**Mitigation, and it reuses the pattern this repo already trusts:** the Cilium
agent's init container reinstalls its CNI binary on every pod start, so
`30-upgrade.yml` must **restart the Cilium agent on each node after each hop**,
exactly as it already re-asserts CoreDNS and kube-vip. That step does not exist
yet — **add it to `30-upgrade.yml` before the next k3s hop**, not before this
migration. Tracked in MODERNIZATION.md Phase 3.

### ✅ Checked: Cilium does NOT clobber k3s's CNI plugins

The obvious way stage 1 could be a cluster-wide outage rather than a no-op:
Cilium writes into the same directory that holds k3s's `flannel`, `bridge`,
`host-local` and `loopback` symlinks, so if it cleared that directory, **every
unmigrated node would lose its CNI plugin at once**. Read
`plugins/cilium-cni/install-plugin.sh` at the v1.20.0 tag rather than assuming:

- `CNI_DIR=${HOST_PREFIX}/opt/cni` → it writes to `/host/opt/cni/bin`, which is
  the container mount point for our `/var/lib/rancher/k3s/data/cni`. So the
  binary does land in the right place.
- It only ever `cp`+`mv`s **`cilium-cni`**, plus `loopback` guarded by
  `[ "${OVERWRITE_LOOPBACK:-false}" = "true" ] || [ ! -f ... ]`. **The default
  is false and k3s's `loopback` already exists, so it is not touched.**
- There is no `rm -rf`, no wipe, no clear. `mkdir -p` is the only other write.
- `OVERWRITE_CILIUM` defaults to **true**, which is what makes the
  restart-after-a-k3s-hop mitigation above work.

Two other init containers (`mount-cgroup`, `apply-sysctl-overwrites`) use that
same directory as scratch — `cp` a helper in, `nsenter` it, `rm` it. Also
non-destructive, and it only ever adds and removes its own filenames. The only
residue if one is killed mid-flight is a stray `cilium-mount` binary, which is
inert because CNI only loads plugins named in the conflist.

---

## Order

`lab5 → lab4 → lab3 → lab2 → lab1 last`. Same order as the k3s hops, same
reason: lab1 is the historical VIP holder and the `peer` every other playbook
delegates to.

**Live on each node ≥24h before the next.** The failure this catches is not
"the node is broken" — the gates catch that in minutes. It is the slow one:
something that only talks across nodes occasionally, or a PVC that only
remounts on a pod restart.

---

## Stage 1 — install, take over nothing

**No sudo. Fully reversible. Changes no traffic.**

```bash
helm repo add cilium https://helm.cilium.io/ && helm repo update cilium
helm install cilium cilium/cilium --version 1.20.0 \
  -n kube-system -f gitops/cilium-values-migration.yaml
```

Expect: `cilium` DaemonSet 5/5, `cilium-envoy` DaemonSet 5/5, `cilium-operator`
2/2, and a new **`cilium-secrets` namespace**. Every pod in the cluster keeps
its 10.42.x.x flannel address — nothing has migrated.

`cilium-envoy` is a separate DaemonSet in 1.20 and is left at its default even
though no L7 policy, Ingress or Gateway API is in use here. Deviating from
upstream defaults during a migration is how you end up in untested territory.

**Verify before going further:**
```bash
kubectl -n kube-system get pods -l k8s-app=cilium -o wide
kubectl -n kube-system exec ds/cilium -- cilium-dbg status | head -20
kubectl get pods -A -o wide | grep -c '10\.42\.'    # should be ALL of them
```

### Rollback — stage 1
```bash
helm uninstall cilium -n kube-system
kubectl delete ns cilium-secrets
```
Clean. `cni.customConf: true` means no CNI conf was ever written, and
`cni.uninstall: false` means nothing of flannel's was touched. **No sudo.**

---

## Stage 2 — migrate ONE node (k8s-lab5)

**⚠️ THIS IS THE FIRST IRREVERSIBLE-WITHOUT-SUDO STEP. It requires a node
reboot, and its rollback requires sudo on the node.**

Do not start this while nobody can log into the nodes.

```bash
# 1. a CiliumNodeConfig that only applies to labelled nodes
cat <<'EOF' | kubectl apply -f -
apiVersion: cilium.io/v2
kind: CiliumNodeConfig
metadata:
  namespace: kube-system
  name: cilium-default
spec:
  nodeSelector:
    matchLabels:
      io.cilium.migration/cilium-default: "true"
  defaults:
    # ⚠️ This is a path INSIDE the agent container, and it is /host/etc/cni/net.d
    # even though the host path is the k3s one. Verified by rendering the chart
    # 2026-08-03: the `etc-cni-netd` volume has
    #   hostPath  = /var/lib/rancher/k3s/agent/etc/cni/net.d   (from cni.confPath)
    #   mountPath = /host/etc/cni/net.d                        (FIXED by the chart)
    # so cni.confPath redirects the host side only. Writing
    # /host/var/lib/rancher/... here points at nothing in the container, and the
    # node would come back from its reboot with no CNI config at all.
    write-cni-conf-when-ready: /host/etc/cni/net.d/05-cilium.conflist
    custom-cni-conf: "false"
    cni-chaining-mode: "none"
    cni-exclusive: "true"
EOF

# 2a. move any CNPG primary OFF this node FIRST.
#     The <cluster>-primary PDB allows ZERO disruptions by design, so draining
#     a node that holds a primary blocks until the primary moves. Lifted from
#     30-upgrade.yml:266-292 -- do not retype it from memory at 1am.
kubectl get pods -A -l cnpg.io/instanceRole=primary \
  -o jsonpath='{range .items[*]}{.metadata.namespace}/{.metadata.name}/{.spec.nodeName}{"\n"}{end}' \
  | grep '/k8s-lab5$' || echo "none on lab5"

#     For each one found: deleting the primary pod makes CNPG promote a
#     replica. That is a FAILOVER, not a graceful switchover -- a few seconds
#     of write unavailability. Only do it when the cluster has >=2 ready
#     instances (both do: immich-db and postgres-ha are 3/3), and VERIFY the
#     primary landed elsewhere before draining.
# kubectl -n <ns> delete pod <primary>
# kubectl get pods -A -l cnpg.io/instanceRole=primary -o wide   # confirm it moved

# 2b. cordon + drain, respecting every PDB.
kubectl cordon k8s-lab5
kubectl drain k8s-lab5 --ignore-daemonsets --delete-emptydir-data --timeout=600s
```

**Never `--force`, never `--disable-eviction`** — both bypass the PDBs that
protect the CNPG primaries. If the drain stalls, that is the safety working:
find out what could not be moved rather than overruling it.

> **On issuing these from the workstation.** `30-upgrade.yml` delegates every
> kubectl to a `peer` node, and that rule is about never asking a node to evict
> its own pods while its own API server is restarting. Running kubectl from the
> workstation does not have that problem — it is never the node being drained —
> so it is acceptable here, with one caveat: the workstation's kubeconfig points
> at the VIP `192.168.32.2`. **At stage 4 that VIP is served by the node being
> rebooted**, so commands can blip mid-drain. For stage 4 specifically, either
> point kubeconfig at a surviving node's IP or run kubectl on lab2.

```bash
# 3. opt the node in, restart its agent, reboot it
kubectl label node k8s-lab5 --overwrite io.cilium.migration/cilium-default=true
kubectl -n kube-system delete pod -l k8s-app=cilium --field-selector spec.nodeName=k8s-lab5
# wait for it to be Ready again, then:                        ← needs sudo
ssh k8s-5.home sudo reboot
```

### Gates before uncordoning — all of them

Modelled on `30-upgrade.yml`, plus the two that are specific to a CNI change:

```bash
kubectl -n kube-system exec ds/cilium -- cilium-dbg status --brief   # on lab5
kubectl get csinode k8s-lab5 -o jsonpath='{.spec.drivers[*].name}'   # NOT empty
kubectl get ds -A -o jsonpath='{range .items[*]}{.status.desiredNumberScheduled}{" "}{.status.numberReady}{"\n"}{end}' | awk '$1!=$2'
```

**The network proof — this is the storage-proof analogue, and it is the real
gate.** A node that is `Ready` with a working CNI-in-name-only is exactly the
k8s-lab4 failure mode this repo already has a rule about:

```bash
# a pod pinned to lab5 must get a 10.245.x.x address, resolve DNS,
# and reach a pod that is still on flannel
kubectl run cil-probe --image=nicolaka/netshoot --restart=Never \
  --overrides='{"spec":{"nodeName":"k8s-lab5"}}' -- sleep 3600
kubectl get pod cil-probe -o jsonpath='{.status.podIP}'    # must be 10.245.
kubectl exec cil-probe -- nslookup kubernetes.default
kubectl exec cil-probe -- ping -c3 <IP of a pod still on 10.42.x.x>
kubectl delete pod cil-probe
```

**Storage is the one to watch hardest.** Every PVC-bearing workload that lands
on lab5 must actually mount — the QNAP CSI driver talks to the NAS over the
node network, and this is the first node where that path is Cilium's.

Then, and only then: `kubectl uncordon k8s-lab5`.

### Rollback — stage 2 (one node migrated)

**🔑 NEEDS SUDO. This is why the migration does not start while you are asleep.**

```bash
kubectl cordon k8s-lab5 && kubectl drain k8s-lab5 --ignore-daemonsets --delete-emptydir-data
kubectl label node k8s-lab5 io.cilium.migration/cilium-default-      # remove label
ssh k8s-5.home sudo rm -f /var/lib/rancher/k3s/agent/etc/cni/net.d/05-cilium.conflist   # 🔑 sudo
ssh k8s-5.home sudo reboot                                                              # 🔑 sudo
kubectl uncordon k8s-lab5
```
The node comes back on flannel with a 10.42.x.x pod CIDR. Cost: one node
drained twice, ~10 minutes. **Verify the node's pods get 10.42 addresses again
before declaring it recovered.**

---

## Stage 3 — migrate lab4, lab3, lab2

Identical to stage 2, one node at a time, ≥24h apart, same gates each time.

Nothing new is being decided here; the decisions were all made at stage 2. If
a node behaves differently from lab5, **stop and find out why** rather than
adjusting the procedure to fit — a divergence at node three means the model of
what is happening is wrong.

### Rollback — stage 3 (N nodes migrated, mixed cluster)
Per node, exactly the stage-2 rollback, in **reverse migration order**. 🔑 sudo.
The mixed state is explicitly supported, so there is no rush to unwind all of
them at once.

---

## Stage 4 — lab1 last

Same procedure. Two extra considerations, neither of which changes the steps:

- lab1 is the usual `peer` that other playbooks delegate kubectl to. Issue its
  drain from lab2.
- lab1 is the historical kube-vip leader. The VIP will move during its reboot.
  That is expected and is now a 5/5 leader-elected DaemonSet, so it should
  survive — **check the VIP answers after lab1 returns**, the same gate
  `30-upgrade.yml` learned to add the hard way:
  ```bash
  curl -sk -o /dev/null -w '%{http_code}\n' https://192.168.32.2:6443/readyz
  ```

At the end of stage 4, every pod in the cluster is on 10.245.0.0/16, and
flannel is still running and doing nothing.

---

## Stage 5 — cleanup

**⚠️ This is the point of no easy return. Two cluster-wide changes; do them as
two separate deliberate ops, not one.**

### 5a. Switch to production values

```bash
helm upgrade cilium cilium/cilium --version 1.20.0 \
  -n kube-system -f gitops/cilium-values-production.yaml
kubectl -n kube-system rollout restart ds/cilium
kubectl delete ciliumnodeconfig -n kube-system cilium-default
```

This flips `policyEnforcementMode` to `default`, which makes the **6 argocd
NetworkPolicies enforceable for the first time in their existence.**

**They were analysed on 2026-08-03 and ArgoCD is expected to survive.** The
analysis, so it does not have to be redone:

- **All six are `policyTypes: [Ingress]`. There are no egress policies at
  all.** So the repo's stated fear — "repo-server fetching charts, controller
  reaching the API" — is unfounded; that is all egress and it is unrestricted.
- `argocd-server` has a single empty ingress rule `{}`, i.e. **allow from
  anywhere**. The UI and its LoadBalancer 192.168.32.16 are unaffected.
- `argocd-repo-server` allows exactly its four real clients (server,
  application-controller, notifications-controller, applicationset-controller)
  — verified complete against the running pods.
- `argocd-redis` allows server, repo-server, application-controller. Checked
  that the two components NOT in that list do not need it:
  applicationset-controller has no redis env and no redis args;
  notifications-controller talks only to `argocd-repo-server:8081`.
- `argocd-applicationset-controller` is selected by **no policy at all**, so it
  is unrestricted.
- Every selector uses `app.kubernetes.io/instance: argocd` + `.../name: <x>`,
  and **all nine running pods carry exactly those labels** — checked, because a
  selector that matches nothing fails silently in the harmless direction while
  a client selector that matches nothing fails in the harmful one.
- The metrics rules use `namespaceSelector: {}`, which matches in-cluster pods
  only. There are **no ServiceMonitors in the argocd namespace**, so nothing
  scrapes them today either way.
- Kubelet liveness/readiness probes are not at risk: Cilium implicitly allows
  all ingress from the local host to pods on that node, precisely so probes
  survive policy.

⚠️ One residual: cilium/cilium#37317 reports NetworkPolicy blocking readiness
probes on k3s — but with `kubeProxyReplacement=true`, which we do **not** set,
and it was closed as not-planned. Watch for it; do not pre-emptively work
around it.

**Verify immediately after 5a:** ArgoCD UI loads, an app syncs, and
`kubectl -n argocd logs deploy/argocd-repo-server` is clean.
**Escape hatch:** set `global.networkPolicy.create: false` in
`gitops/argocd-values.yaml` and run `ansible-playbook playbooks/15-argocd.yml`.

### 5b. Remove flannel — all five nodes, one change

**🔑 NEEDS SUDO. This is the genuinely one-way step.**

Set in `ansible/inventory/group_vars/all.yml`:
```yaml
k3s_flannel_backend: none
k3s_disable_network_policy: true
```
then converge and restart. `flannel-backend: none` removes the CNI from the
k3s server process — it is **not** a per-node change and must not be rolled
`serial: 1`, because a node whose flannel is gone while its neighbours still
have it has no bridge to them.

### Rollback — stage 5

- **After 5a, before 5b:** revert to `cilium-values-migration.yaml` with
  `helm upgrade`. Policy goes back to `never`. Cheap, no sudo, seconds.
- **After 5b:** there is no quick rollback. Restoring flannel means reverting
  the config on all five nodes and restarting k3s everywhere (🔑 sudo), and
  every pod must then be recreated to get a 10.42 address. Assume an outage
  measured in hours. **Do not run 5b until you have been happy on Cilium for
  at least a week.**

---

## Post-migration, do not forget

- [ ] **Add a Cilium-agent restart to `30-upgrade.yml`** — see the CNI bin
      directory warning above. Until this exists, the next k3s hop is a risk.
- [ ] Adopt Cilium into ArgoCD (only now — `prune` + `selfHeal` would have
      fought the half-migrated state and deleted the CiliumNodeConfig).
- [ ] Reconsider `bpf.hostLegacyRouting: false` as its own small op.
- [ ] Hubble is off. Turning it on is a separate decision, not part of this.
