# Phase 3 — flannel → Cilium 1.20.0

The runbook and the rollback. Companion to [`MODERNIZATION.md`](MODERNIZATION.md)
Phase 3, which holds the reasoning; this file holds the procedure.

Values: [`gitops/cilium-values-migration.yaml`](gitops/cilium-values-migration.yaml)
(stages 1–4) and [`gitops/cilium-values-production.yaml`](gitops/cilium-values-production.yaml)
(stage 5).

Last updated: 2026-08-03. **Stages 1–4 are DONE; only stage 5 (cleanup) remains.**

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

### ✅ containerd paths — CONFIRMED 2026-08-03, not inferred

`/var/lib/rancher/k3s/agent/etc/containerd/config.toml` read with sudo:

```
bin_dirs = ["/var/lib/rancher/k3s/data/cni"]
conf_dir = "/var/lib/rancher/k3s/agent/etc/cni/net.d"
```

Both match the values in `cilium-values-migration.yaml` exactly. The last open
prerequisite is closed.

💡 **`bin_dirs` is a plural array, and that is a useful escape hatch.**
containerd accepts multiple CNI bin directories. Adding a second entry —
`/opt/cni/bin`, created and owned by Ansible — would take Cilium's binary off
versioned k3s ground entirely and retire the "restart the agent after every
k3s hop" mitigation below. Worth doing later as its own small op; **not** worth
changing mid-migration.

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

~~**Live on each node ≥24h before the next.**~~ **Dropped for stages 3–4, on
purpose, 2026-08-03.** Once stage 2 had *measured* cross-CNI routing, storage
and DNS on a real migrated node, the per-node soak was buying little: the
failure it guards against is a slow one, but the mixed two-overlay state is
itself the least-tested configuration this cluster can be in, so sitting in it
for a week is its own risk. The nodes were rolled back-to-back with the full
gate set on each.

**The soak that was KEPT is the one before stage 5**, which is where the
genuinely irreversible changes live (policy enforcement, then removing flannel
from k3s). Be happy on Cilium for a week before going there.

---

## Stage 1 — install, take over nothing

### ✅ DONE 2026-08-03 08:00 — clean, and it changed nothing

Observed after the install, which is what makes stage 1 a verified no-op
rather than a claimed one:

- `cilium` DaemonSet **5/5**, `cilium-envoy` **5/5**, `cilium-operator` 2/2 —
  **zero restarts** on all 12 pods
- `cilium-dbg status` → `Ok 1.20.0`, and every value confirmed live:
  `KubeProxyReplacement: False`, `Routing: Tunnel [vxlan] / Host: Legacy`,
  `CNI Chaining: none`, `IPAM: 10.245.1.0/24`
- **0 pods on 10.245.x.x** — nothing migrated, exactly as intended
- all 5 nodes Ready, API VIP answering, LAN DNS resolving, ArgoCD unchanged at
  53 Synced / 2 OutOfSync, all Healthy
- 🔑 **the CNI directory proof**, which was the one thing that could have made
  this a cluster-wide outage. `/var/lib/rancher/k3s/data/cni/` now reads:
  ```
  bandwidth  bridge  cilium-cni  cni  firewall  flannel  host-local  loopback  portmap
  ```
  `cilium-cni` added; **every k3s original intact**, `flannel` and `loopback`
  included. The `install-plugin.sh` reading below is now confirmed by
  observation.

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

### ✅ DONE 2026-08-03 — lab5 is on Cilium, every gate passed

Sequence as run: CiliumNodeConfig applied (0 nodes matched) → both CNPG
primaries failed over off lab5, each waited back to 3/3 before the next →
cordon + drain (24 pods evicted, **no `--force`**, every PDB respected) →
label → agent restart → reboot → gates → uncordon.

Gates, all passed:

| gate | result |
|---|---|
| cilium agent | `Ok 1.20.0`, IPAM `10.245.4.0/24`, Tunnel vxlan, Host Legacy |
| `csinode` | `csi.trident.qnap.io` registered |
| DaemonSets | every `desired == ready` |
| etcd | `has_leader 1`, `health_failures 0` on **all five** |
| pod IP on lab5 | **10.245.4.2** — Cilium IPAM, not flannel |
| DNS | resolves via CoreDNS `10.43.0.10` |
| **cross-CNI** | **ping to a flannel pod `10.42.0.131` on another node: 0% loss, 0.47ms** |
| ClusterIP | `kubernetes.default` → 401 (kube-proxy still routing) |
| storage | mounted `/dev/mapper/mpathq`, ext4, 16 MB written + read back, md5 match |

The cross-CNI row is the one that mattered: it is the empirical confirmation of
the premise this whole per-node shape rests on. It is no longer inherited from
a doc.

Also observed and worth keeping: **lab5 was the busiest node in the cluster** —
it held *both* CNPG primaries plus Pi-hole plus an ingress replica. The
primary-move-first step was not ceremonial; the two `*-primary` PDBs allow zero
disruptions, so the drain would have blocked without it.

⚠️ Test volumes leak on `Retain`. Both proof PVCs left a PV **and** a QNAP
backend volume; cleaned with `tridentctl delete volume` via the trident
controller pod (`tridentctl` is not on the workstation). Verified back to 19
volumes with the audit passing.

---

**⚠️ THIS IS THE FIRST IRREVERSIBLE-WITHOUT-SUDO STEP. It requires a node
reboot, and its rollback requires sudo on the node.**

Do not start this while nobody can log into the nodes.

> 🪤 **Set an explicit `-n` on every command in this section.** This
> workstation's kubeconfig context has `namespace: kube-system`, so a bare
> `kubectl run cil-probe` creates the probe in **kube-system**, and a bare
> `kubectl get pod <name>` silently looks there too. During the live run that
> cost ~15 minutes and a wrong conclusion: a proof pod that had actually
> **completed successfully** in `default` read as NotFound, which was
> misdiagnosed as "something deleted it" — and acted on by deleting a backend
> volume that was still claimed. No damage, because it was a test volume, but
> the reasoning was wrong for 15 minutes. Same family as the rest of this
> repo's findings: the command succeeded, it just answered a different question
> than the one being asked.

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

⚠️⚠️ **Deleting the Cilium conflist is NOT enough, and getting this wrong
leaves the node with no CNI at all.** Observed on lab5 2026-08-03: because
`cni-exclusive: "true"`, Cilium **renames flannel's config out of the way**
rather than leaving it alongside. After takeover the directory reads:

```
05-cilium.conflist                 <- new
10-flannel.conflist.cilium_bak     <- flannel's, RENAMED
```

So the rollback must **restore flannel's conflist**, not just remove Cilium's:

```bash
kubectl cordon k8s-lab5 && kubectl drain k8s-lab5 --ignore-daemonsets --delete-emptydir-data
kubectl label node k8s-lab5 io.cilium.migration/cilium-default-      # remove label

# 🔑 sudo — BOTH commands, and the mv is the one that matters
ssh k8s-5.home 'sudo rm -f /var/lib/rancher/k3s/agent/etc/cni/net.d/05-cilium.conflist'
ssh k8s-5.home 'sudo mv /var/lib/rancher/k3s/agent/etc/cni/net.d/10-flannel.conflist.cilium_bak \
                        /var/lib/rancher/k3s/agent/etc/cni/net.d/10-flannel.conflist'
ssh k8s-5.home sudo reboot                                                              # 🔑 sudo
kubectl uncordon k8s-lab5
```

If you cannot reach the node over ssh, k3s will also rewrite its own flannel
conflist on start — but only on a **version** change, so do not rely on it.

The node comes back on flannel with a 10.42.x.x pod CIDR. Cost: one node
drained twice, ~10 minutes. **Verify the node's pods get 10.42 addresses again
before declaring it recovered.**

---

## Stages 3 and 4 — lab4, lab3, lab2, lab1

### ✅ DONE 2026-08-03 — all five nodes are on Cilium

Run by `ansible/playbooks/35-cilium-migrate.yml` rather than by hand, so the
whole remainder took one `--ask-become-pass` prompt. Final state:

```
cilium 10.245.x : 63 pods
flannel 10.42.x : 0 pods
Cluster health  : 5/5 reachable      (was 0/0 — nothing was managed before)
```

All five Ready and uncordoned, every agent 1/1 with a single restart (its
reboot), no unhealthy pods anywhere, both CNPG clusters 3/3, etcd leader on all
five, VIP answering, LAN DNS resolving, ingress responding, audit clean, and no
leftover proof pods or PVCs.

The 24h-per-node soak was deliberately dropped for stages 3–4 — see the note
under "Order". The soak that was kept is the one before stage 5.

⚠️ **The rollback material is intact on every node**, verified rather than
assumed: all five still hold `10-flannel.conflist.cilium_bak` next to
`05-cilium.conflist`. That is what makes a per-node rollback possible right up
until stage 5b removes flannel from k3s itself.

### What the first live runs actually cost — all gate bugs, zero cluster damage

Three consecutive failures, none of them the cluster's fault, all of them mine.
Recording them because the pattern is now established beyond doubt: on this
repo the gates are more fragile than the operations they guard.

1. **A YAML folded scalar (`>-`) split one command into four.** Folding joins
   lines with spaces *only* while they keep the first line's indentation; a
   more-indented continuation keeps its newline. `kubectl exec … --` and
   `cilium-dbg status` became separate commands, and newlines inside `$( )`
   broke the pod lookup too. It failed 60 retries against **k8s-lab4, a node
   that had migrated perfectly.**
2. **`set -o pipefail` + `awk '{print $1; exit}'`.** awk exiting early closed
   the pipe, kubectl took SIGPIPE and died rc 141, and pipefail reported the
   pipeline as failed — *while stdout held the correct answer*. The same trap
   applies to `| head -1`.
3. **Gates living inside the `when: not already_migrated` block.** The first
   failure left lab4 migrated-but-cordoned; a resume would have called it
   "already migrated", skipped everything, and left it cordoned forever with no
   gate ever having passed. `30-upgrade.yml` had already learned this; the new
   playbook had copied its shape but not that scar. Resumes now re-verify.

Also found and fixed: `peer` used the `difference` filter, which is a **set**
operation and does not preserve order, so the delegation target was arbitrary
(`k8s-5 -> k8s-4` where the expression reads as `k8s-1`). Harmless — any peer
works — but unpredictable. `30-upgrade.yml` still has this.

### Rollback — stages 3/4 (N nodes migrated)
Per node, exactly the stage-2 rollback, in **reverse migration order**. 🔑 sudo.
The mixed state is explicitly supported, so there is no rush to unwind all of
them at once. Every node still has its `.cilium_bak` file.

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
cd ansible && ansible-playbook playbooks/34-cilium.yml -e cilium_stage=production
kubectl delete ciliumnodeconfig -n kube-system cilium-default
```

No sudo. The playbook **refuses to run** if any node is unmigrated or any pod
still holds a `10.42.x` address, because enabling policy around a pod on the
old CNI is exactly what upstream warns against. Rehearse it first with
`--check`, which runs every gate and applies nothing.

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

- [x] ~~**Add a Cilium-agent restart to `30-upgrade.yml`**~~ ✅ **2026-08-03.**
      It now detects Cilium and bounces the agent on each node after the hop,
      alongside the CoreDNS and kube-vip re-asserts. Deletes the pod rather than
      trusting the k3s restart to bounce it: if containerd merely resumes the
      existing container the init container does not re-run, and the node is
      left unable to create pods with no obvious symptom. No-ops cleanly on a
      cluster without Cilium.
- [x] ~~Adopt Cilium into ArgoCD~~ **DECIDED AGAINST, 2026-08-03 — it is under
      Ansible instead** (`roles/cilium` + `playbooks/34-cilium.yml`), following
      the `roles/argocd` precedent. ArgoCD's ApplicationSets apply `prune: true`
      + `selfHeal: true`, ArgoCD's own pods run on the pod network Cilium
      provides, and there is no "just restart the pod" for a cluster with no
      CNI. It would also delete the `CiliumNodeConfig`, which is migration
      state rather than desired state in git.
- [ ] Reconsider `bpf.hostLegacyRouting: false` as its own small op.
- [ ] Hubble is off. Turning it on is a separate decision, not part of this.
