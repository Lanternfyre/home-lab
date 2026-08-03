# Cluster modernization — status and plan

**Living document. Update it as things land.** This is the reference to re-read
when picking the work back up, or after a context reset.

Companions: [`MANUAL-STEPS.md`](MANUAL-STEPS.md) (actions needing a human),
[`ansible/README.md`](ansible/README.md), and
[`scripts/audit-protected-volumes.py`](scripts/audit-protected-volumes.py).

Last updated: 2026-08-03

---

## Where we are

k3s **v1.35.6+k3s1** (the QNAP CSI ceiling), 5 nodes, all
`control-plane,etcd,master`, no workers.
Target end state: k3s 1.35.6 on Cilium, Envoy Gateway replacing archived
ingress-nginx, Kyverno via native VAP, dashboards behind Google OIDC.

| | at session start | now |
|---|---|---|
| Backups | **none at all** | 3 nightly jobs, restore-verified (pihole's retired — see below) |
| Alerting | receiver `"null"`, 7 alerts into a black hole | **Pushover**, verified delivered; 3 permanent false positives purged |
| PV reclaim policy | 14× `Delete` | 18× `Retain` + protect labels |
| ArgoCD | 13 OutOfSync / 27 Synced / **15 Unknown** | 53 Synced / 2 OutOfSync / **0 Unknown** (both benign, both diagnosed) |
| QNAP CSI | v1.6.0 (mkfs bug) | **v1.6.2** |
| k8s-lab4 | Ready but **0 CSI drivers**, no DNS | **repaired + uncordoned** |
| k8s-lab5 | storage unproven | **storage-proven + uncordoned** |
| API VIP | single Pod on lab1 | **5/5 DaemonSet, leader-elected** |
| cert-manager | v1.14.5 | **v1.20.3** |
| Envoy Gateway | v1.6.1 (EOL, k8s ≤1.33) | **v1.8.3**, GW API CRDs v1.5.1 |
| ArgoCD | v3.3.6, installed by hand, version recorded nowhere | **v3.4.6**, pinned in Ansible |
| CoreDNS | 1 replica, no PDB | **3 replicas on 3 nodes + PDB** |
| ServiceLB / traefik | 9 svclb DS, 640+ crashloops | **removed** |
| inotify sysctl | broken on all 5 for 211 days | 1048576 everywhere |
| Node config | prose runbook only | Ansible, detect-then-remediate |
| CNI | flannel (in the k3s server process) | **Cilium 1.20.0 on all 5**, 63/63 pods, 5/5 reachable |

### Immediately next

**✅ RESOLVED 2026-08-03: the Pi-hole backup CronJob was silently broken, and
is now retired.** Found incidentally while verifying Cilium stage 1 — it was
NOT related to Cilium. The CronJob is pruned; `pihole-data` is now registered
in `protected-volumes.yaml` and has **no backup at all**, which is a deliberate,
recorded trade (recovery is a rebuild from `apps/pihole/chart-values.yaml`).
History of the failure:

`pihole-backup-29762025` has sat **Pending for 4h**, and the last successful
run was **28h ago**. The cause is the Phase 0.D storage migration:

- the CronJob mounts PVC **`pihole`** — the OLD `local-path` claim, pinned by
  node affinity to **k8s-lab3**
- it also carries a `requiredDuringScheduling` **podAffinity** onto the running
  Pi-hole pod
- Pi-hole now uses `pihole-data` (`qnap-iscsi`) and runs on **k8s-lab5**

So the two constraints are unsatisfiable — `0/5 nodes are available: 1 node(s)
didn't match pod affinity rules, 4 node(s) didn't match PersistentVolume's node
affinity`. It can never schedule.

Two problems, not one: the job cannot run **and** it was pointed at the stale
volume, so even its last "successful" runs backed up data Pi-hole had already
stopped using. Fix is to point the `config` volume at `pihole-data`.

⚠️ **This is the exact failure shape this repo keeps rediscovering** — the
CronJob exists, the PVC exists, `MODERNIZATION.md` counts "4 nightly jobs" as a
win, and nothing alerted. Same family as the GHCR token that reported
`Ready=True` while delivering an expired credential. **Assert values and
behaviour, never presence.** Worth asking what else the Pi-hole migration left
pointing at the old claim.

**0. ~~The wedged `redis` ArgoCD operation~~ ✅ RESOLVED — it cleared itself.**

Verified 2026-08-03: the operation reached `phase=Succeeded` at
**2026-08-03T00:02:28Z** ("successfully synced (all tasks run)"), `.operation`
is gone from the spec, and the app reads **Synced / Healthy**. None of the
escalations in the old note were needed — no `terminate-op`, no finalizer
surgery. Nothing was pending a push either; `master` is level with `origin`.

**The redis migration is complete and verified**, on all four checks the old
note asked for:
1. `redis-master-0` 1/1 on `redis:8.10.0-alpine` — official image, Bitnami gone
2. no PDB in the `redis` namespace (the chart refuses one at a single instance)
3. `immich-server` Running, unchanged — the Service is still `redis-master`
4. drain-safety is still the *outstanding* proof: the next node drain must not
   stall. Phase 3 supplies that drain.

⚠️ **The lesson is about the diagnosis, not the fix.** The old note recorded
three escalations that "did NOT work" and reached for an authenticated CLI. The
operation was in fact *progressing* — an ArgoCD operation that keeps retrying a
genuinely-Forbidden write looks identical, at a glance, to one that is wedged.
`.status.operationState.startedAt` had not moved, which is what "wedged" was
inferred from; but that field records when the operation *started*, not when it
last did work. **Before declaring an ArgoCD operation stuck, re-read it once
more after a real interval** — this one needed ~3 hours, and the patch commands
that "failed" would have been unnecessary damage had they succeeded.

Still deliberately orphaned by the migration: `redis-data-redis-master-0` and
`redis-data-redis-replicas-0` (both still Bound, `qnap-iscsi`, 8Gi). Nothing
mounts them — the new chart has no persistence. They hold the parked rollback
data; keep until you are satisfied, then remember `Retain` means
`tridentctl delete volume` too, or the backend volume leaks.

Rollback, if redis ever needs it: `git revert` the `app.yaml` change. The old
Bitnami release is gone but the parked data is still on those volumes.

**Still parked deliberately** — these are rollback material, not litter. Redis
is proven on the new chart, so the first entry is now yours to clear whenever
you want; the rest still stand:
- `appendonlydir.rdb14-parked*` / `parked-appendonlydir*` on both redis PVCs
  (~63 MB each, one dated 2026-06-27 predating this session)
- the old `pihole` PVC on local-path — one of the two benign OutOfSync apps
- stray `coredns-pdb.yaml` copies on lab2–5 from a delegation bug; identical
  content so they do not flap, untidy rather than harmful


1. ~~**lab5**~~ ✅ **DONE 2026-08-02.** The forced multipathd restart landed
   (daemon now newer than its config), and the storage proof **passed**: the
   pod mounted `/dev/mapper/mpathb`, wrote and read back 16 MB. The multipath
   device it could never create before now exists. Test PV reclaimed, no leak.
   **lab5 uncordoned.** All five nodes are now Ready, schedulable, and
   registering `csi.trident.qnap.io` — the drain targets Phase 2 needs.

   <details><summary>historical: the command that fixed it</summary>
   `cd ansible && ansible-playbook playbooks/10-baseline.yml --ask-become-pass --limit k8s-5.home -e multipath_force_restart=true`
   Its `multipath.conf` is correct on disk but multipathd never reloaded it —
   **re-proven behaviourally on 2026-08-02**, not inferred: a 1Gi `qnap-iscsi`
   PVC with a pod pinned to lab5 via `nodeName` still fails with
   `failed to stage volume: multipath device not found when it is expected`.
   Timestamps agree (conf written 08:39:24 today, multipathd last started
   2026-07-31 06:53). The role defers that restart when it sees live LUNs, and
   lab5 carries stale sessions. Then re-run the storage proof and uncordon.
   *(lab4 is done — its multipathd restarted in the same second the conf was
   written, and it is already uncordoned.)*
   </details>
2. ~~**Push**~~ ✅ done 2026-08-02. cert-manager rolled v1.14.5 → **v1.20.3**,
   Healthy — the first hard Phase 2 prerequisite is met. `prometheus` came back
   into sync on its own; `pyroscope` is diagnosed benign (see findings).
3. ~~**Envoy Gateway bump**~~ ✅ **DONE 2026-08-02.** v1.6.1 → **v1.8.3**
   rolled cleanly: deployment Running, 0 restarts, app **Synced/Healthy**, and
   the Gateway API CRDs moved **v1.4.1 → v1.5.1**. Both hard Phase 2
   prerequisites are now met. ⚠️ The `safe-upgrades.gateway.networking.k8s.io`
   VAP + binding are now **live** — the rollback order in Phase 2 is no longer
   hypothetical.
4. ~~**kube-vip Pod → DaemonSet**~~ ✅ **DONE 2026-08-02.** Cut over cleanly:
   DaemonSet 5/5 ready, the old single Pod pruned, lease `plndr-cp-lock` held
   by k8s-lab1, API VIP answering, all five nodes Ready, **zero restarts**.
   Losing lab1 no longer takes the API VIP with it.
5. ~~**`30-upgrade.yml`**~~ ✅ **built, run, and now REVIEWED 2026-08-03.**
   Read deliberately end-to-end, as Phase 2 said to do before Phase 3 leans on
   it. **Verdict: it is sound, and Phase 3 should copy its patterns rather
   than invent new ones** — the `peer` indirection (never drain a node from
   itself), CNPG-primary-move-before-drain, drain that respects every PDB with
   `--force` explicitly rejected, uncordon only *after* every gate, addon
   re-assert, and the VIP gate placed after the repair that fixes it.

   Two things it does **not** yet cover, both real for Phase 3:
   - ~~no Cilium-agent re-assert~~ ✅ **added 2026-08-03.** It now bounces the
     Cilium agent per node after each hop, alongside CoreDNS and kube-vip,
     because a k3s version change rewrites `data/cni/` where Cilium's binary
     lives. No-ops on a cluster without Cilium.
   - **its `desired == ready` DaemonSet gate will trip during a Cilium
     migration**, when the cilium DaemonSet is legitimately part-ready. Do not
     run a k3s hop and the CNI migration in the same window.
6. **Phase 3 (Cilium) — ✅ STAGES 1–4 DONE 2026-08-03. Only cleanup remains.**
   See [`CILIUM-MIGRATION.md`](CILIUM-MIGRATION.md).

   **All five nodes are on Cilium.** 63 pods on `10.245.x.x`, **zero on
   flannel**, and `Cluster health: 5/5 reachable` where it read `0/0` before.
   Every agent 1/1 with a single restart (its reboot), no unhealthy pods, both
   CNPG clusters 3/3, etcd leader on all five, VIP answering, LAN DNS
   resolving, ingress responding, audit clean.

   ✅ **The premise of the plan was MEASURED before it was relied on:** at
   stage 2 a pod on Cilium (`10.245.4.2`) pinged a pod still on flannel
   (`10.42.0.131`) on another node — 0% loss, 0.47 ms. Storage proved on the
   migrated node too (`/dev/mapper/mpathq`, 16 MB, md5 match).

   Stages 3–4 ran from `ansible/playbooks/35-cilium-migrate.yml`, so the
   remaining four nodes took one `--ask-become-pass` prompt instead of four
   interactive reboots.

   ⚠️ **The rollback material is intact and verified on all five nodes** —
   each still holds `10-flannel.conflist.cilium_bak` beside
   `05-cilium.conflist`. Per-node rollback stays possible until stage 5b.

   ⚠️ **The documented rollback was WRONG until this work corrected it.**
   `cni-exclusive: true` makes Cilium **rename** flannel's conflist rather than
   leave it, so deleting only `05-cilium.conflist` — which is what the
   procedure said — strands the node with **no CNI at all**.

   **Next is stage 5 (cleanup), and it is the irreversible one.** Soak for a
   week first. 5a flips `policyEnforcementMode` to `default`, which makes the 6
   argocd NetworkPolicies enforceable for the first time ever; 5b sets
   `flannel-backend: none` on all five at once, after which there is no quick
   way back.

---

## Phases

### Phase 0 — stabilize ✅ **complete** (bar one downgraded item)
Done: backups, PV protection, CSI v1.6.2, lab4 repair, ServiceLB + traefik
removal, ArgoCD baseline green, ingress made drain-safe, Ansible baseline.

Remaining:
- [x] lab5 multipath restart → storage proof → uncordon ✅ 2026-08-02
- [x] **kube-vip bare Pod → DaemonSet** ✅ **2026-08-02.** Was a single Pod on
      lab1; if lab1 died the API VIP `192.168.32.2` went with it, and that VIP
      is the `--server` join target in lab2–5's systemd units, the `tls-san`,
      and every kubeconfig. Now a 5/5 DaemonSet with leader election.
      Run it again any time with
      `ansible-playbook playbooks/25-kube-vip-daemonset.yml --ask-become-pass`
      — idempotent, and a no-op when the rendered manifest is unchanged.

      ✅ **The NIC-name blocker did not exist, and the live rollout proved it.**
      One identical manifest, each node auto-bound to its own interface:
      `kube-vip bind interface=eno1` on lab1/2/3, `enp0s31f6` on lab4,
      `enp7s0` on lab5 — all preceded by
      `No interface is specified for VIP in config, auto-detecting default
      Interface`. Zero restarts on all five.
      kube-vip v1.0.3 auto-detects the default-route interface when
      `vip_interface` is unset. Verified three ways: the string
      `"No interface is specified for VIP in config, auto-detecting default
      Interface"` in the shipped binary; the call site at
      `cmd/kube-vip.go:377` guarded by `if initConfig.Interface == ""`, calling
      `vip.GetDefaultGatewayInterface()` (netlink lookup of the `0.0.0.0/0`
      route) and then `vip.MonitorDefaultInterface()` to follow changes; and
      `kube-vip manifest daemonset` emitting **no** `vip_interface` env when
      `--interface` is omitted. So one uniform DaemonSet covers all five nodes.
      **Delete the `vip_interface: eno1` env — do not templatise it per node.**

      ⚠️ **The real blocker was leader election, and it is missing today.** The
      live Pod has *no* `vip_leaderelection`, no `vip_leasename`, and no
      `vip_nodename` — harmless at one replica, but five instances with that
      same env would all ARP for `192.168.32.2` simultaneously and flap the
      API VIP. The canonical generated manifest sets `vip_leaderelection: true`,
      `vip_leasename: plndr-cp-lock`, `vip_leaseduration: 5`,
      `vip_renewdeadline: 3`, `vip_retryperiod: 1`, and `vip_nodename` from
      `fieldRef: spec.nodeName` (unique lease-holder identity — required).
      RBAC is already sufficient: the existing `kube-vip` ClusterRole grants
      `coordination.k8s.io/leases` get/list/watch/create/update/patch.

      ⚠️ **It is a k3s Addon, not a hand-made Pod.** It carries
      `objectset.rio.cattle.io/owner-gvk: k3s.cattle.io/v1, Kind=Addon` and is
      reconciled from `/var/lib/rancher/k3s/server/manifests/kube-vip.yaml` on
      lab1 — the same mechanism as the CoreDNS-replicas item below. Editing the
      live Pod is pointless; *deleting* the manifest prunes the objectset and
      takes the VIP with it. Replace the **contents of that same file** so the
      objectset transitions Pod → DaemonSet. The objectset also owns the
      `kube-vip` ServiceAccount, ClusterRole and ClusterRoleBinding, so the new
      file must keep all four or they get pruned.

      Expect a **brief VIP outage** during the cutover while the old Pod is
      removed and the first DaemonSet pod claims the lease. Deliberate op, done
      with a human watching — not a drive-by.
- [x] ~~**Repoint `--server` off the VIP**~~ **DECIDED AGAINST, 2026-08-02.**
      The item was written when the VIP was a single Pod on lab1: back then
      lab1 dying took the VIP, and with it the address four nodes used to find
      the API. That is no longer true — kube-vip is a 5/5 leader-elected
      DaemonSet with ~5s failover.

      Pinning each node to a peer IP trades ONE shared dependency for **N
      specific ones**: if lab2 points at lab1 and lab1 is down, lab2 cannot
      rejoin on restart. It also bakes in a static topology that goes stale
      whenever nodes change. An HA VIP has neither problem.

      ✅ **MEASURED 2026-08-02, not inferred.** On k8s-lab4 — a node whose
      unit explicitly says `--server https://192.168.32.2:6443` —
      `ss -tn | grep 6443 | grep -c 192.168.32.2` returns **0**. It holds
      direct connections to peer node IPs instead. So an already-joined server
      consults the VIP at join and never again, exactly as the k3s docs
      sentence implies. A VIP outage cannot disturb a running cluster; it can
      only delay a node that is joining or re-bootstrapping.

      Residual risk, accepted knowingly: cold-start ordering (boot only a
      non-init node with all others off and it waits for a VIP nobody holds —
      k3s retries, so it resolves once a holder appears). Gratuitous-ARP
      failover latency is now a non-issue for running nodes, since they do not
      use the VIP at all.

      *Measurement gotcha worth remembering:* `sudo grep /var/lib/rancher/k3s/agent/*.kubeconfig`
      does NOT work — the shell expands the glob as the unprivileged user
      before sudo applies, and that directory is 0700, so the pattern stays
      literal and grep reports "No such file". Use `sudo sh -c '...'` when
      globbing inside root-only paths.

      ✅ **Checked and NOT a problem:** whether the VIP could move to a node
      whose serving cert lacks it. `--tls-san 192.168.32.2` appears only in
      lab1's unit, but k3s propagates it through the datastore — verified with
      `openssl s_client` against all five node IPs, and every cert carries
      `IP Address:192.168.32.2` plus every node IP. So the VIP can move
      anywhere without breaking TLS.

- [ ] **Normalise lab1's systemd unit — it is the only node that differs.**
      ```
      k8s-1:    --cluster-init --token … --tls-san 192.168.32.2
      k8s-2..5: --server https://192.168.32.2:6443 --token …
      ```
      Harmless in steady state (`--cluster-init` after bootstrap just selects
      embedded etcd; all five are equal etcd members, lab1 is not a master).

      ⚠️ **The risk is the REBUILD path, and it is severe.** If lab1's disk
      dies and it is rebuilt from `gitops/argo-install.md`, that runbook says
      `--cluster-init` — which starts a **brand new empty cluster** instead of
      rejoining. The four survivors would keep running while lab1 formed a
      second cluster of one, and the failure would look like "lab1 came back
      but the cluster is wrong". It must be rebuilt with `--server` pointing at
      a survivor.

      Fix: drop `--cluster-init` from lab1 and give it the same `--server` as
      the rest, so all five units are identical and no node needs special
      knowledge. Must be a **systemd drop-in** — CLI args in `ExecStart` beat
      `config.yaml`, so the Ansible k3s_config role does not reach this.
      Deliberate op, one node at a time, verifying etcd keeps its leader.
- [ ] CoreDNS 1 → 3 replicas + PDB. **Built (`roles/coredns_ha`, wired into
      `site.yml`), not yet applied — needs one sudo run.**

      **Correction to what this file used to say.** It claimed `kubectl scale`
      "is reverted by upgrades". Two things turned out to be wrong:

      1. k3s does **not** rewrite `coredns.yaml` on every start — only when the
         k3s *version* changes. Proven 2026-08-02: all five servers restarted
         at 08:34–08:36 and the CoreDNS Deployment was still `generation=1`,
         untouched since 2026-07-05.
      2. The manifest does not contain a `replicas` field **at all** — decoded
         from the objectset's `applied` annotation. The Deployment just
         defaults to 1. A field absent from the applied set is not managed by
         the deploy controller, so setting it fights nothing.

      So scaling is legitimate and convergent, which is why it sits in
      `site.yml` rather than being a deliberate op. What is *not* proven is
      whether it survives a k3s **version** change, when the template is
      replaced — so `30-upgrade.yml` re-asserts after every hop instead of
      trusting the reasoning.

      Two more things checked rather than assumed: the bundled Deployment
      **already has** `topologySpreadConstraints` (`maxSkew: 1`,
      `DoNotSchedule` on hostname), so the replicas genuinely spread and the
      role does not need to add them — but that also means asking for more
      replicas than schedulable nodes leaves the surplus Pending, so the role
      refuses that case. And the **PDB lives in its own manifest file**
      (`coredns-pdb.yaml`), which k3s never writes — so unlike the Deployment,
      the PDB survives all three upgrade hops.
- [x] CNPG `immich-db` 2 → 3 instances ✅ **2026-08-02.** 3/3 Ready, "Cluster
      in healthy state", and — the part that matters — on **three distinct
      nodes**: primary on lab1, replicas on lab3 and lab5. Two switchover
      targets instead of one. The new `immich-db-4` PVC inherited both
      invariants automatically (`homelab.techyon.dev/protect: "true"` via
      `inheritedMetadata`, and `reclaimPolicy: Retain`). CNPG also created a
      second PDB, `immich-db`, allowing 1 disruption alongside the existing
      `immich-db-primary` which allows 0.
      At 2 instances there is exactly one switchover target, so every drain has
      a single candidate; if that node is the one draining, the drain stalls
      against the CNPG PDB and the only ways out are waiting or
      `--force`/`--disable-eviction`, which kills a primary bypassing its PDB.
- [x] **CoreDNS 3 replicas + PDB** ✅ **2026-08-02.** 3/3 Ready on three
      distinct nodes (lab4, lab5, lab2), PDB allowing 1 disruption, and all
      five nodes still resolving afterwards — checked because Pi-hole runs
      in-cluster, so breaking cluster DNS also breaks the `.home` names Ansible
      resolves its own inventory from.
- [x] **ArgoCD under version control** ✅ **2026-08-02.** Was installed by a
      hand-typed `helm install`, with the version recorded only in the
      in-cluster Helm release secret. Now `roles/argocd` +
      `playbooks/15-argocd.yml`, pinned at chart **10.2.2 (v3.4.6)**, upgraded
      from 9.5.0 (v3.3.6). Runs on the workstation, so it is the only playbook
      here needing no sudo. Deliberately **not** self-managed — ArgoCD's CRDs
      hold every Application in the cluster and the ApplicationSets apply
      `prune: true`, so a self-managing ArgoCD can prune its own CRDs with
      nothing left to repair it. Bootstrap and upgrade are the same code path
      (`-e argocd_bootstrap=true` adds the GHCR creds + root Application), so
      disaster recovery is exercised on every routine update.
- [x] **PDB for ArgoCD** ✅ **2026-08-02.** The "no obvious home" problem
      dissolved once ArgoCD moved under Ansible: the argo-cd chart has
      per-component PDB support, so it lives in `gitops/argocd-values.yaml`.

      ⚠️ **A PDB alone would have been useless or harmful.** Every ArgoCD
      component ships at ONE replica, where `minAvailable: 1` gives
      `disruptionsAllowed 0` and blocks every drain permanently, while
      `maxUnavailable: 1` still permits the only pod to be evicted. So
      `server` and `repoServer` went to **2 replicas** (stateless, scale
      cleanly) with `maxUnavailable: 1` budgets and
      `topologySpreadConstraints`. Verified: one pod each on lab1 and lab2,
      both PDBs reporting `disruptionsAllowed=1`.

      The singletons — controller, redis, dex, notifications, applicationset —
      deliberately get **no PDB**. ArgoCD being briefly unavailable during a
      drain costs a UI outage and a reconciliation pause, not workload
      downtime; a deadlocked drain costs the whole upgrade.

### Phase 1 — Ansible ✅ largely built
`site.yml` converges; `20-config-converge.yml` applies k3s config + restarts;
`40-add-node.yml` joins a node safely; `90-preflight.yml` audits read-only.

`30-upgrade.yml` is **written (2026-08-02) but UNRUN** — no hop has been taken
with it, so it is code, not yet a proven procedure. It needs
`--ask-become-pass` even for `--check`, because `k3s kubectl` cannot read the
0600 `k3s.yaml` without root.

What is already validated: syntax, the pre-flight ceiling and no-downgrade
assertions (dry-run against the live cluster), every gate query run by hand
against the live cluster, the upgrade ORDER (`order: reverse_inventory` →
lab5→lab1, lab1 last), and the version-match regex, which correctly rejects
old-version-but-Ready, new-version-but-NotReady, and a similar-but-wrong
version string.

What is NOT validated: the CNPG primary move, the drain, the install, and the
gates as a running sequence. The first hop is the test.

### Phase 2 — k3s 1.32.10 → **1.35.6** (hard stop)

### ✅ PHASE 2 COMPLETE — all five nodes on v1.35.6+k3s1 (2026-08-02)

Three hops in one day: 1.32.10 → 1.33.13 → 1.34.9 → 1.35.6. Final validation:
all five Ready and schedulable, etcd leader on 5/5, `csi.trident.qnap.io`
registered on every node, zero broken pods, kube-vip DaemonSet 5/5 with the
bare Pod gone, CoreDNS 3/3, both CNPG clusters 3/3, VIP answering, DNS
resolving, 53 Synced / 2 OutOfSync (both benign and diagnosed).

**This is the ceiling.** Do not raise `k3s_max_version` without first
re-checking QNAP's declared support matrix — the driver claims Kubernetes
support only to 1.35, and there is no downgrade path.

**What actually cost time:** four runs were lost to gates in this playbook, not
to the cluster. Every cluster-facing gate (etcd, CSI, DaemonSets, drain safety,
storage proof) behaved correctly and nothing was ever damaged. The failures
were preconditions I wrote describing the happy starting state. `30-upgrade.yml`
was patched five times while live — **read it deliberately before Phase 3 leans
on it**, rather than trusting it because tonight eventually worked.

<details><summary>historical: hop 1 detail</summary>

**Hop 1 (1.33.13) ✅ COMPLETE 2026-08-02.** All five nodes on
`v1.33.13+k3s1`, all schedulable, readyz passed, `csi.trident.qnap.io`
registered on every node, both CNPG clusters 3/3 healthy after two live
primary failovers (lab2 and lab1 each hosted one), ArgoCD 54 Synced / 1
OutOfSync (the known-benign pyroscope). Zero failed tasks on the completing
run. **The playbook is now a proven procedure rather than just code.**

</details>

✅ **Phase 0.D DONE 2026-08-02 — Pi-hole is off local-path.** It now uses an
explicitly declared `pihole-data` claim on `qnap-iscsi`, and the migration
proved itself immediately: the pod moved from k8s-lab3 to **k8s-lab1** and the
volume followed. History came with it (5340 blocked queries, 801 domains, 19
clients in the FTL DB), all `.home` names resolve, zero restarts. The blocking
prerequisite for hop 2 is cleared.

The old `pihole` PVC is deliberately kept as a rollback point
(`Prune=false,Delete=false`, PV `Retain`, protect label). Delete it once you
are satisfied — and remember `Retain` means the backend volume leaks, so it
needs `tridentctl delete volume` too... except this one is local-path, so it
is a directory on lab3's disk.

**Remaining hops: exactly two.** `1.33.13 → 1.34.9 → 1.35.6`, then STOP.
1.35 is the end of the road for this cluster until QNAP ships a CSI driver
declaring 1.36 support — at which point `k3s_max_version` is raised
deliberately, not incidentally. Note that the 1.35 ceiling is a recorded
finding from QNAP's support matrix; it could NOT be re-derived from the live
cluster (the driver logs no supported-version range), so re-check the vendor's
matrix before assuming it still binds.

Playbook: `playbooks/30-upgrade.yml`. ⚠️ Its CNPG step
moves a primary by **deleting the primary pod**, which is a failover (a few
seconds of write unavailability), not a graceful switchover — `kubectl cnpg
promote` needs the CNPG plugin and it is not installed. The step is gated on
the cluster having ≥2 ready instances and VERIFIES the primary landed on a
different node before draining. Watch this closely on the first hop; if it is
unacceptable, install the plugin and switch to a real switchover.
Three sequential hops: `1.33.13 → 1.34.9 → 1.35.6`, **one variable**
(`k3s_version` in `inventory/group_vars/all.yml`), three deliberate runs, live
on each 24–48h.

**Do not go to 1.36.** The QNAP CSI driver declares Kubernetes support only to
1.35 — even v1.6.2. k3s `stable` is already 1.36.2, so the pre-flight assertion
is doing real work.

**Prerequisites, both hard:**
- cert-manager → **v1.20.3** (committed, unpushed). NOT v1.21: it supports
  k8s 1.33–1.36 and would be unsupported *today* on 1.32.
- Envoy Gateway → **v1.8.3**. v1.6.1 supports k8s **1.30–1.33 only** and is
  EOL, so upgrading k3s first would break the gateway.
  **Committed 2026-08-02, NOT yet pushed** — pushing applies it immediately.
  What the bump actually carries, measured by rendering both charts:

  - **Gateway API CRDs v1.4.1 → v1.5.1.** The chart's `crds` subchart is
    enabled by default and ArgoCD includes CRDs, so this lands whether or not
    you think of it as a CRD change.
  - Exposure is genuinely low: Envoy Gateway carries **no traffic at all** —
    0 Gateways, 0 HTTPRoutes, 0 GRPCRoutes, 1 GatewayClass. ingress-nginx
    still serves all 13 Ingresses.
  - New `MutatingWebhookConfiguration` `topology.webhook.gateway.envoyproxy.io`
    — `failurePolicy: Ignore`, matching only `pods/binding`, so an Envoy
    Gateway outage cannot block scheduling. Benign.
  - New `Job gateway-envoy-gateway-helm-certgen`, a `pre-install,pre-upgrade`
    Helm hook (ArgoCD runs it as a PreSync hook).

  ⚠️ **This bump is a one-way door, and the trap is in the rollback.** v1.8.3
  installs a `ValidatingAdmissionPolicy`
  `safe-upgrades.gateway.networking.k8s.io` with `failurePolicy: Fail` and
  `validationActions: [Deny]` over all CRD CREATE/UPDATE. Its CEL
  short-circuits on `object.spec.group != 'gateway.networking.k8s.io'`, so
  Trident, cert-manager and ArgoCD CRDs are unaffected — but it **denies any
  Gateway API CRD whose `bundle-version` matches `v1.[0-4].\d+`**. Reverting
  `targetRevision` to v1.6.1 therefore gets Denied, because v1.6.1 ships
  pre-v1.5.0 CRDs.

  **Rollback order, and it is not optional:** delete the VAP *and* its binding
  first, then revert the app.
  ```
  kubectl delete validatingadmissionpolicybinding safe-upgrades.gateway.networking.k8s.io
  kubectl delete validatingadmissionpolicy        safe-upgrades.gateway.networking.k8s.io
  ```
  Reverting first and deleting after does not work — the CRD write is rejected
  before ArgoCD ever prunes the policy.

**Per node, `serial: 1`, `any_errors_fatal`:** CNPG switchover → cordon → drain
(from a *different* node) → upgrade → gates → uncordon → settle → re-assert.
**Never `--force` or `--disable-eviction`** — that kills a CNPG primary
bypassing its PDB.

Gates: node Ready at new version · `etcd_server_has_leader 1` and
`etcd_server_health_failures 0` from `:2381` on **all five** (all five
answering *is* the member-count check) · `/readyz?verbose` contains
`[+]etcd ok` · every DaemonSet `desired == ready` · `csinode` non-empty · no
ImagePullBackOff.

⚠️ **Minor downgrades are impossible.** The only rollback is an etcd restore:
`--cluster-reset --cluster-reset-restore-path` on one node, then wiping
`/var/lib/rancher/k3s/server/db` on the other four and rejoining. Hours,
destructive, unrehearsed. That is why the gates are strict.

### Phase 3 — Flannel → Cilium 1.20.0

📖 **The runbook and the per-stage rollback now live in
[`CILIUM-MIGRATION.md`](CILIUM-MIGRATION.md).** Values are committed as
`gitops/cilium-values-migration.yaml` and `gitops/cilium-values-production.yaml`.
The rollback prerequisite this section used to carry is **satisfied**. Nothing
has been executed.

**The central correction still stands: keep `flannel-backend: vxlan` for the
entire migration.** In k3s flannel runs *inside the server process*, not as a
DaemonSet, so flipping it per node destroys the bridge the dual-overlay
migration depends on and partitions the pod network. `flannel-backend: none` is
a cleanup-phase, all-nodes-at-once change.

✅ **The question the plan rested on is now answered rather than assumed.**
Cross-CNI pod traffic **does** work during the migration — upstream: *"they
have access to both Cilium and non-Cilium pods while the migration is taking
place"*, because separate IP ranges let the Linux routing table separate the
traffic. So one-node-at-a-time over days is a valid shape. It depends on
exactly three values (the 10.245/16 pool, `tunnelPort: 8473`, and
`bpf.hostLegacyRouting: true`); change any of them and the shape is no longer
valid.

Verified 2026-08-03 rather than inherited: Cilium 1.20.0 is the current
release and supports k8s 1.33–1.36 (cluster is 1.35.6); the chart creates
**no admission webhooks and no CRDs**, so it carries nothing like the Envoy
Gateway one-way-door VAP; `kubeProxyReplacement: "false"` is already the chart
default and there is no kube-proxy DaemonSet to remove.

**Corrections to what this section used to say:**

- ⚠️ **`cni.confPath` was missing and it matters as much as `binPath`.** Both
  chart defaults (`/opt/cni/bin`, `/etc/cni/net.d`) **do not exist** on these
  nodes — checked on k8s-lab5. The plan named only `binPath`. Left at its
  default, Cilium writes its CNI config where nothing reads it, and the failure
  is silent until a pod cannot be created.
- ⚠️ **`/var/lib/rancher/k3s/data/cni` is versioned k3s-managed ground**, not a
  stable directory: it is symlinks into `data/<hash>/bin/cni`, and its mtime on
  lab5 matches the last k3s upgrade exactly. This is the same shape as the
  finding that already cost an outage here — *a k3s VERSION change reverts
  custom things in k3s-managed paths*. **`30-upgrade.yml` must gain a
  Cilium-agent restart per hop**, alongside the CoreDNS and kube-vip
  re-asserts; the agent's init container reinstalls the binary on start. That
  step does not exist yet.
- ⚠️ **`k8sServiceHost` must be `127.0.0.1:6444`, NOT the API VIP.** The plan
  never specified it. The VIP is the tempting choice and it is a circular
  dependency: the agent on the node holding the VIP would depend on the VIP
  that node serves, and that node is the one being drained and rebooted during
  its own migration step. k3s's loopback API proxy has no such coupling —
  verified listening on both lab1 and lab5.
- ✅ **The ArgoCD NetworkPolicy risk is real but much smaller than feared, and
  it lands at CLEANUP, not at the first node.** `policyEnforcementMode: never`
  is required for the whole migration window, so the policies stay inert until
  stage 5a. And on analysis **all six are Ingress-only — there are no egress
  policies at all**, so "repo-server fetching charts, controller reaching the
  API" was never at risk; that is all egress. `argocd-server` is allow-all,
  `applicationset-controller` is selected by no policy, and every selector was
  checked against the running pods' labels. Full analysis in
  `CILIUM-MIGRATION.md` stage 5a. The escape hatch is unchanged:
  `global.networkPolicy.create: false` + `ansible-playbook playbooks/15-argocd.yml`.

Unchanged and still true: pod CIDR **10.245.0.0/16**, tunnel port **8473**,
`enableLBIPAM: false` + `defaultLBServiceIPAM: none` (chart defaults would
fight MetalLB — confirmed, they are `true`/`lbipam`), leave **`MTU: 0`**, order
lab5 → lab4 → lab3 → lab2 → **lab1 last**, install out-of-band via helm and
adopt into ArgoCD only after cleanup.

🔑 **One probe still needs sudo before stage 2:** confirm containerd's actual
`bin_dir`/`conf_dir` in
`/var/lib/rancher/k3s/agent/etc/containerd/config.toml` (0600, unreadable
without it). The inference is strong but this repo's rule is *assert values,
never presence*.

### Phase 4 — Headlamp + Alertmanager

✅ **ALERTING IS DONE 2026-08-03 — and confirmed on a real phone.**
Alertmanager's only receiver was `name: "null"`; every alert the cluster
produced went into a black hole. Now routed to **Pushover**, credentials from
1Password via ExternalSecret and mounted as FILES (`user_key_file` /
`token_file`), so nothing secret is in git or in the rendered Helm release.

Proven rather than assumed, in this order:
`amtool check-config` inside the running v0.30.1 container → SUCCESS ·
ExternalSecret `SecretSynced`, both values 30 bytes with no trailing newline
(exactly Pushover's format) · `alertmanager_notifications_total{integration=
"pushover"} 2` with **every** `..._failed_total` reason at 0 · and finally the
notifications arriving on the operator's phone.

⚠️ **The false-positive purge was a PREREQUISITE, not tidying.**
`KubeSchedulerDown`, `KubeControllerManagerDown` and `KubeProxyDown` had been
firing since the stack was installed and can never resolve — kube-prometheus-
stack expects those as separate pods, and k3s runs all three inside the server
process. Pointing a receiver at a phone without fixing that delivers three
permanently-wrong alerts first, which is how alerting gets muted. Alerts went
**7 → 3**. Note that disabling only the components makes it WORSE: the rules
are `absent(up{job=...})`, so removing the ServiceMonitor makes `absent()`
match harder. The `defaultRules.rules.*` toggles are what actually stop them.

Routing decisions: `Watchdog` and `InfoInhibitor` → `null` (Watchdog fires
constantly by design as a dead-man's switch — sent to a phone it is a
notification every few minutes forever); `repeat_interval: 12h`; priority 1 for
critical and 0 otherwise, deliberately **not** 2/emergency, which retries until
acknowledged.

Two GENUINE signals surfaced immediately, and BOTH were real config bugs that
had been live for months with nothing to report them — which is the clearest
possible argument that this work was overdue:

**`KubeHpaMaxedOut` — ingress-nginx. ✅ FIXED 2026-08-03.** The HPA scaled on
memory at 80% against a 120Mi request, while pods actually used 121/123/291Mi
and **CPU sat at 3%**. nginx allocates worker buffers and never releases them,
so its memory is roughly constant per replica and says nothing about load: the
target was unsatisfiable by construction and the HPA had been pinned at max
forever. That is not autoscaling, it is a fixed replica count with an alert
attached. Now CPU-only, min 3 (deliberately raised from 2 so fixing an alert
does not quietly drop a replica), max 5, request corrected to 256Mi.

⚠️ **The obvious edit was exactly backwards, and only rendering caught it.**
Deleting `targetMemoryUtilizationPercentage` does NOT disable memory scaling —
Helm merges values over the chart's defaults and this chart defaults it to
**50**, i.e. stricter than the 80 being removed, which would have scaled
straight to max. The template guards with `{{- with ... }}`, so an explicit
`0` is what actually removes the metric.

**`CPUThrottlingHigh` — trident-operator, 70.2% throttled. ⚠️ NOT FIXED, and
it needs a different repo.** The QNAP CSI *operator* — the thing that
reconciles the storage driver — runs with `limits.cpu: 20m`. It idles at 2m but
any burst is clamped, hence 70% throttling. This is the classic Kubernetes
anti-pattern: a CPU limit does not protect anything here, it just throttles.
The value is **hardcoded**, not templated from values, at
`~/Private/Techyon/helm-compendium/qnap-trident/templates/bundle.yaml:553`, so
it cannot be overridden from this repo and must not be `kubectl patch`ed
(chart-managed, `selfHeal` reverts it). Fix = raise or drop the limit in that
chart, bump `qnap-trident` past 0.1.1, publish, bump `targetRevision`. Also: `Watchdog` did not reappear
after the Alertmanager restart, which is mildly odd for an always-firing alert
— it changes nothing operationally (it routes to `null`) but it is the canary
for "is the pipeline alive", so worth a glance.

**Still to do in this phase: Headlamp + API-server OIDC.** That is the part
with teeth — OIDC goes in via `kube-apiserver-arg`, and ⚠️ **k3s refuses to
start on an invalid apiserver flag, so a typo bricks a server.** Roll
`serial: 1` with full gates, at a keyboard.

### Phase 5 — Kyverno
Use `ValidatingPolicy` (`policies.kyverno.io/v1`), **not** the deprecated
`ClusterPolicy` (removal planned v1.20, Oct 2026). Set
`autogen.validatingAdmissionPolicy.enabled: true` so enforcing policies compile
to **native VAPs** evaluated in-process — Kyverno being down then cannot block
pod creation. Zero mutation (avoids the ServerSideApply drift-fight).
Acceptance test: Kyverno at 0 replicas → pods still schedule, protected PVCs
still undeletable.

### Phase 6 — ingress-nginx → Envoy Gateway
`ingress-nginx` was **archived 2026-03-24**; no further CVE fixes. Exposure is
LAN-only (RFC1918, no tunnel), so this is unhurried but not optional.

Cilium Gateway API is ruled out: it requires `kubeProxyReplacement=true`, which
we deliberately defer. NGINX Gateway Fabric is ruled out: OIDC and ext-auth are
NGINX Plus only. **Envoy Gateway** is already installed and wired into
external-dns.

⚠️ **Spike before committing:** no upstream e2e test composes
`oidc` + `jwt` + `authorization`. Test on a throwaway host — the decisive check
is logging in with a `@gmail.com` account and getting **403, not 200**.

Notes: `spec.oidc` does **not** validate the ID token signature (EG #5414 open)
— the `jwt` block is the only real validation. Pin `oidc.cookieNames.idToken`
or the JWT filter cannot address the cookie. Google consent screen is already
`orgInternalOnly: true`, which is layer one under the `hd` claim rule.

### Phase 7 — automation
SMART exporter → unattended-upgrades (security only, never auto-reboot) →
Descheduler → Goldilocks/VPA (recommend only) → NFD → **kured last**, gated:
all five nodes are etcd members, concurrency 1, blocking-pod-selector for CNPG
primaries. Reloader is already installed.

---

## Hard-won findings — do not re-derive these

**ServiceLB's klipper claimed hostPort 53 with an *unconditional* DNAT**
(`--dport 53 -j DNAT --to <podIP>:53`, no destination match). It captured
`127.0.0.53` on every node, so node DNS has always gone through Pi-hole, not
systemd-resolved — `DNS=1.1.1.1 8.8.8.8` in `resolved.conf` was shadowed. On
lab4 the target pod was ImagePullBackOff, so the DNAT pointed at nothing and
even the node's own resolver stub was refused: a loop it could not escape, since
fixing DNS required pulling an image. Proven by controlled comparison with lab5
(identical rules, live pod, worked). Removing ServiceLB fixed it.

**ArgoCD `retry` was nested wrong** — at `spec.template.spec.retry` instead of
inside `syncPolicy`, so the CRD silently pruned it and **no Helm app had any
retry policy**. Nothing errors when you get this wrong; the field just vanishes.

**Assert values and behaviour, never file presence.** Three separate bugs of
this exact shape: the inotify sysctl file existed on all 5 nodes and had never
worked (value wrapped onto a second line); `multipath.conf` existed on lab4/5
and was wrong (missing `find_multipaths no`); the GHCR ExternalSecret reported
`Ready=True` while delivering an expired token that froze five apps including
the storage driver.

**`pyroscope` OutOfSync is benign, permanent, and cannot be fixed by syncing.**
Diagnosed 2026-08-02 by rendering the chart locally and diffing against the
live object. The *entire* difference is one field:

```
spec/volumeClaimTemplates[0]/metadata/annotations: desired {} , absent in live
```

The chart emits an empty annotations map; the API server drops it. ArgoCD's
differ sees a difference and `volumeClaimTemplates` is **immutable on a
StatefulSet**, so no sync can ever converge it — the app will sit OutOfSync
forever while being perfectly Healthy (STS 1/1, pyroscope 1.21.0). It is *not*
a stuck operation: `.operation` is empty and the last sync reported Succeeded.
Fix if the noise matters: an `ignoreDifferences` entry for that path. Note the
ApplicationSet template is shared by all Helm apps and `app.yaml` is
deliberately flat 5 keys, so this is a decision about a global rule, not a
per-app tweak. Left alone for now.

**`prometheus` drifts in and out of Sync on its own.** Observed flapping twice
on 2026-08-02 — `Secret/prometheus-operator-grafana` plus the matching
`Deployment`. The shape is the classic one: a chart that generates a random
value at render time produces a different Secret on every reconcile, and the
Deployment's checksum annotation follows it. Not yet confirmed, and unlike
`pyroscope` it is *not* diagnosed — worth pinning down rather than assuming,
because a genuinely flapping app is indistinguishable at a glance from one that
is quietly failing to converge.

**k3s IGNORES `--cluster-init` / `--server` once an etcd datastore exists on
disk.** From the k3s docs, verbatim: *"If an etcd datastore is found on disk
either because that node has either initialized or joined a cluster already,
the datastore arguments (`--cluster-init`, `--server`, `--datastore-endpoint`,
etc) are ignored."* Three consequences, all of which changed decisions here:

1. **Normalising lab1's systemd unit is pointless.** The flags it differs by
   are inert on every restart. A plan to rewrite `ExecStart` via a drop-in was
   abandoned on this evidence — it carried real risk (a bad unit stops k3s) for
   zero runtime benefit.
2. **A VIP outage does not affect already-joined nodes.** `--server` is
   consulted at join, not continuously, which further weakens the case for
   pointing nodes at peer IPs.
3. **The real exposure is the REBUILD path**, where there *is* no datastore, so
   the flags are honoured. `k3s_cluster_init: true` was a static host var on
   k8s-1 and `40-add-node.yml` writes config.yaml *before* installing — so
   rebuilding k8s-1 would have started a **brand new empty cluster of one**
   while the four survivors kept serving. It would not have looked like an
   error: the node comes up `Ready`, with nothing on it. The flag is now an
   explicit bootstrap-time argument, and `k3s_config` refuses to render
   `cluster-init` on a node with no datastore while other servers exist.

**A gate placed before its own remedy blocks the repair.** FOUR instances in
one evening, all self-inflicted: the upgrade's VIP gate ran before the
kube-vip re-assert that fixes the VIP, so the run died with "No route to host"
while the fix sat unreached below it; the cutover playbook asserted the VIP was
healthy *before* converting it, which refused the exact run that restores a
down VIP; the preflight refused to resume any hop that had left a node
cordoned — which is the state an interrupted hop always leaves; and the
"clever" replacement for that refused only when a cordoned node was ALREADY at
the target, on the theory that this meant a deliberate cordon. It does not. A
hop installs → restarts → **gates** → and only *then* uncordons, so a run that
dies anywhere in the gates leaves a node cordoned AT the target, which is
indistinguishable from a deliberate cordon by node state alone.

That last one is the instructive failure: the fix was not a better heuristic,
it was abandoning the attempt to guess. Refusing blocks every resumed run — the
common case — while uncordoning something a human meant to keep cordoned costs
one command. Report loudly, proceed, and return the node to service. **When writing
a check, ask what state the operator is in when they need this tool most.** For
a repair tool that is the broken state, so the precondition belongs at the END
as verification, not at the start as a gate.

**Every k3s server reconciles addons from ITS OWN manifests directory, so a
stale copy anywhere fights the managed one.** The kube-vip DaemonSet was
written to a single node on the theory that a second copy would put two deploy
controllers on one objectset. Backwards: they already all reconcile it. The
danger is not a second copy, it is a second copy with different CONTENT — the
addon then flaps between DaemonSet and bare Pod depending on which server
restarted last, and the bare Pod hardcodes `vip_interface: eno1`, so it
CrashLoops on any node whose NIC differs and the API VIP vanishes. Happened
twice. The fix is identical bytes on every server: same checksum, nothing to
fight over.

**A k3s VERSION change reverts CUSTOM addon manifests, not just bundled ones.**
Proven the hard way on the 1.34 hop: the kube-vip addon reverted to its
pre-DaemonSet bare-Pod manifest (the Addon checksum returned to its exact
pre-cutover value). That old manifest hardcodes `vip_interface: eno1`; the
recreated Pod landed on lab5, whose NIC is `enp7s0`, CrashLooped with
`eno1 is not valid interface`, and the API VIP vanished. The DaemonSet was
pruned as part of the same objectset reconcile.

Two lessons. First, **anything living in
`/var/lib/rancher/k3s/server/manifests/` must be re-asserted after every hop** —
CoreDNS was, kube-vip was not, and that asymmetry was the bug.
`30-upgrade.yml` now re-asserts both. Second, this failure **passes every other
gate**: nodes stay Ready, etcd keeps its leader, DaemonSets are even — and the
address every kubeconfig uses is simply gone. There is now an explicit
per-node gate asserting the VIP answers.

The cluster itself was never at risk, because joined nodes talk to peer node
IPs and never the VIP — which is the measurement that made this survivable
rather than an outage.

**`local-path` volumes pin a pod to ONE node, and draining it strands them.**
*(Resolved for Pi-hole on 2026-08-02 — see Phase 0.D. The lesson stands for
anything else that might land on local-path, which is now the k3s default
StorageClass and therefore the silent default for any PVC that omits one.)*
A local-path PV carries `nodeAffinity` to the machine that holds it, so
cordoning that node yields `0/5 nodes are available: 1 node(s) were
unschedulable, 4 node(s) had volume node affinity conflict` — Pending until
that exact node returns. Bit us on the first 1.33 hop: Pi-hole's PVC is
local-path pinned to k8s-lab3, so draining lab3 took DNS down for the whole
LAN. Worse, because Pi-hole serves the `.home` names the Ansible inventory
resolved, the playbook then reported the node it was mid-upgrade on as
UNREACHABLE. Two fixes: the inventory now carries explicit `ansible_host` IPs
so Ansible resolves nothing (3/5 → 5/5 reachable immediately), and
`30-upgrade.yml` pre-flight enumerates every local-path PVC up front. Exactly
one exists cluster-wide: `dns/pihole`.

**`ansible.builtin.command` runs no shell and shlex-splits its arguments,
which EATS backslashes and quotes.** Two separate bugs in one day from this.
`jsonpath={.metadata.labels.cnpg\.io/cluster}` reached kubectl as
`cnpg.io/cluster` — a nested-key lookup — and returned empty; and
`jsonpath={...[?(@.type=="Available")]...}` lost its quotes and died with
"unrecognized identifier Available". Both looked like the cluster was broken
when the query was. Use `ansible.builtin.shell` with the jsonpath quoted, or
avoid characters the splitter consumes.

**A YAML folded scalar (`>-`) keeps the newline on any MORE-indented line, so
a "one-liner" shell command silently becomes several.** Folding joins lines
with spaces only while they hold the *first* line's indentation. Written as

```yaml
shell: >-
  POD=$(kubectl get pod -l k8s-app=cilium
        --field-selector spec.nodeName=x        # <- indented further
        -o jsonpath='{.items[0].metadata.name}');
  kubectl exec $POD -c cilium-agent --
    cilium-dbg status --brief                   # <- indented further
```

bash received four commands, not one, and said so:
`--field-selector: command not found` · `-o: command not found` ·
`error: you must specify at least one command for the container` ·
`cilium-dbg: command not found`. Newlines separate commands inside `$( )` too.
It then failed all 60 retries against a node that had migrated perfectly. Use a
literal block `|` with explicit `\` continuations for anything multi-line. A
sweep of the tree found every other instance is a `msg:` or a Jinja `set_fact`,
where a stray newline is harmless whitespace — the bug only bites shell.

**`set -o pipefail` turns any early-exiting pipe consumer into a task failure.**
`kubectl … | awk '{print $1; exit}'` — awk exits on the first match, closes the
pipe, kubectl takes SIGPIPE and dies **rc 141**, and pipefail reports the
pipeline as failed *while stdout holds the correct answer*. Diagnosed from a
failure whose own output was `"stdout": "10.42.0.131"`, which is exactly what
the command was supposed to produce. Consume all input and latch instead
(`… && !seen {print $1; seen=1}`). `| head -1` is the same trap.

**The `difference` filter does not preserve order — it is a set operation.**
`groups['k3s_servers'] | difference([inventory_hostname]) | first` therefore
picks an arbitrary peer, not the first in inventory: on this 5-host inventory
it returns `k8s-1, k8s-3, k8s-4, …`, and a run delegated k8s-5's commands to
k8s-4 while the expression reads as k8s-1. Harmless, because any peer is a
valid delegation target — but a delegate you cannot predict is one you cannot
reason about mid-incident. Use `reject('equalto', …) | list | first`.
`30-upgrade.yml` still has the `difference` form.

**This workstation's kubeconfig context defaults to `namespace: kube-system`,
and that silently redirects every un-namespaced `kubectl`.** Found the hard way
on 2026-08-03. A storage-proof pod was created in `default` via an explicit
manifest, then polled with a bare `kubectl get pod <name>` — which looked in
`kube-system`, returned NotFound, and made a pod that had **completed
successfully** look as though something had deleted it. The wrong conclusion
was then acted on: a PV and its QNAP backend volume were deleted while the PVC
still existed. It was a test volume so nothing was lost, but the diagnosis was
wrong for fifteen minutes and a real cleanup was performed on a false premise.

Two habits follow. **Pass `-n` explicitly in anything written down**, because a
runbook command that works interactively for one person silently targets a
different namespace for the next. And when a resource "vanishes", check *where
you looked* before concluding *what happened to it* — `NotFound` is an answer
about a namespace, not about existence.

**An ArgoCD "hard refresh" does NOT re-pull an OCI Helm chart — it can report
`Synced` against a stale cached render.** Hit on 2026-08-03 bumping
`qnap-trident` 0.1.1 → 0.1.2. Everything looked right and was not: the
Application's `spec.sources[0].targetRevision` read **0.1.2**,
`status.sync.comparedTo` also read **0.1.2**, `status.sync.revisions` listed
`['0.1.2', <git sha>]`, health was Healthy and `Deployment/trident-operator`
was individually reported **Synced** — while the live Deployment still carried
the old hardcoded `limits.cpu: 20m` and its pod was four hours old.

The published chart was genuinely correct (verified independently: the
`qnap-trident-v0.1.2` git tag points at the right commit and contains the
templated resources). ArgoCD had simply compared against a cached render.
**The fix is an explicit sync, not a refresh** — patching an `operation` onto
the Application applied it immediately and the Deployment rolled.

Why this is nasty: every field you would check to confirm the bump landed says
it landed. The only reliable check is the LIVE OBJECT — here,
`kubectl -n storage get deploy trident-operator -o jsonpath='{...resources}'`.
Same family as everything else in this section: the status was accurate about
what ArgoCD had done, and wrong about what was running.

**A stale ArgoCD operation blocks everything.** Seen three times (qnap-trident,
ingress twice): a sync stuck "waiting for healthy state" on something that
cannot become healthy never re-reads git. Clear it with
`kubectl -n argocd patch app <name> --type json -p '[{"op":"remove","path":"/operation"}]'`.

**Percentage `maxUnavailable` floors to 0 at 3 replicas**, so a Deployment
cannot self-heal a scheduling-constrained rollout. Use an absolute `1`.

**`preferred` anti-affinity does not spread simultaneous pods** — all three
ingress replicas landed on one node. `topologySpreadConstraints` with
`maxSkew: 1, ScheduleAnyway` is the correct expression.

**Ansible auto-loads `group_vars/` only from the inventory's or the playbook's
directory.** It lives at `inventory/group_vars/` for that reason.

**A correct config file is not a loaded config file, and `node_verify` could
not tell the difference.** lab5 passed every assertion — `multipath.conf`
contains `find_multipaths no` — while provably unable to stage a `qnap-iscsi`
volume, because multipathd last started 2026-07-31 and the file was written
2026-08-02. Two hours of "verified healthy" on a node that could not mount a
PVC. This is the third variant of the same lesson: the file existing was not
enough, the file's *contents* being right was not enough either.

`node_verify` now compares multipathd's `ActiveEnterTimestamp` against the
file's mtime — but **staleness alone is deliberately not a failure**, because
`node_baseline` is *designed* to write the file and defer the restart on any
node with attached LUNs (lab1/2/3, four each). Failing on staleness would red-
flag three nodes doing exactly what the role intends. The discriminator is
behavioural: stale **and zero multipath devices** fails (lab5's signature —
sessions attached, no dm entries, proven unable to stage); stale **with** live
mpath devices warns and notes that the config lands at the next restart.
Verified against all five: lab1/2/3 pass with 4 devices each, lab4 passes,
lab5 fails.

**The `multipath_force_restart` escape hatch could not reach the only node it
was built for.** The restart was gated on `multipath_written.changed` *and*
then the force flag, so forcing only worked when the file also needed
rewriting. lab5's file was already correct, so the copy task skipped,
`multipath_written` stayed undefined, and the documented recovery command
was a silent no-op that reported success. The force flag now short-circuits
the changed-check entirely.

**`--check` silently skips `command:`/`shell:` tasks and returns an EMPTY
stdout, rc=0.** So the "review what would change" step in `MANUAL-STEPS.md`
reported `sysctls=BROKEN, services=BROKEN` on **all five** nodes and
`liveLUNs=0` on lab1/2/3 (which carry 4 LUNs each) — then `node_verify`
hard-failed asserting `"" >= 1048576` against sysctls that were correct. A
detect-then-remediate design is *especially* exposed to this: its whole input
is command output. Every read-only probe now carries `check_mode: false`, and
the two post-fix re-tests carry `when: not ansible_check_mode` (they would
otherwise re-observe the un-remediated state and report a failure that has not
happened). Fixed 2026-08-02. Same shape as the other bugs here: the dry run
existed, was documented, and had never told the truth.

**QNAP SMB mounts are `uid=0` and the driver ignores StorageClass
`mountOptions`** (tested). Backup jobs therefore run as uid 0. Also: busybox
`tar` fstat()s its own output and gets ESTALE on CIFS — pipe through `gzip`.

**`Retain` means test PVCs leak.** Deleting a test PVC leaves the PV *and* the
backend volume. Clean up with `tridentctl delete volume` after any storage test.

---

## Standing warnings

- ~~Do not uncordon k8s-lab5 until a storage proof passes on it.~~ ✅ The
  proof passed 2026-08-02 and it is uncordoned. The rule stands for any
  *future* node: `Ready` is not the gate, a mounted volume is.
- **Backups are on the same NAS as the data.** They cover driver bugs,
  accidental deletion and bad restores. They do **not** cover the NAS failing.
  The 57 GB Immich library has no second copy anywhere — accepted risk,
  recorded in `MANUAL-STEPS.md`.
- **Never `kubectl patch` the QNAP StorageClasses** — chart-managed, `selfHeal`
  reverts it. Change them in git.
- **`flannel-backend: none` is not a config-convergence item.** It removes the
  CNI cluster-wide.
