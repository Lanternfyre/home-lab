# Cluster modernization — status and plan

**Living document. Update it as things land.** This is the reference to re-read
when picking the work back up, or after a context reset.

Companions: [`MANUAL-STEPS.md`](MANUAL-STEPS.md) (actions needing a human),
[`ansible/README.md`](ansible/README.md), and
[`scripts/audit-protected-volumes.py`](scripts/audit-protected-volumes.py).

Last updated: 2026-08-02

---

## Where we are

k3s **v1.32.10+k3s1**, 5 nodes, all `control-plane,etcd,master`, no workers.
Target end state: k3s 1.35.6 on Cilium, Envoy Gateway replacing archived
ingress-nginx, Kyverno via native VAP, dashboards behind Google OIDC.

| | at session start | now |
|---|---|---|
| Backups | **none at all** | 4 nightly jobs, restore-verified |
| PV reclaim policy | 14× `Delete` | 18× `Retain` + protect labels |
| ArgoCD | 13 OutOfSync / 27 Synced / **15 Unknown** | 1 OutOfSync / 54 Synced / **0 Unknown** |
| QNAP CSI | v1.6.0 (mkfs bug) | **v1.6.2** |
| k8s-lab4 | Ready but **0 CSI drivers**, no DNS | **repaired + uncordoned** |
| k8s-lab5 | storage unproven | **storage-proven + uncordoned** |
| ServiceLB / traefik | 9 svclb DS, 640+ crashloops | **removed** |
| inotify sysctl | broken on all 5 for 211 days | 1048576 everywhere |
| Node config | prose runbook only | Ansible, detect-then-remediate |

### Immediately next

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
3. **Decide on the Envoy Gateway bump.** v1.6.1 → v1.8.3 is **committed but
   deliberately unpushed** — read the one-way-door warning in Phase 2 first.
   Every unpushed commit after it is inert Ansible (playbooks change nothing
   until invoked), so the *only* cluster-affecting commit in the local stack is
   this one. Pushing applies it.
4. ~~**kube-vip Pod → DaemonSet**~~ ✅ **DONE 2026-08-02.** Cut over cleanly:
   DaemonSet 5/5 ready, the old single Pod pruned, lease `plndr-cp-lock` held
   by k8s-lab1, API VIP answering, all five nodes Ready, **zero restarts**.
   Losing lab1 no longer takes the API VIP with it.
5. **`30-upgrade.yml` — now unblocked, and the next big build.** Its gates
   assert against a 5-node-healthy cluster, which as of 2026-08-02 is finally
   true. Write it after the kube-vip cutover, so the gates can assume a VIP
   that survives losing a node.
6. Then Phase 2 below.

---

## Phases

### Phase 0 — stabilize ✅ mostly done
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
- [ ] **Repoint `--server` off the VIP** — each node at a different peer's node
      IP, removing the shared dependency. Must be a **systemd drop-in**: k3s CLI
      args take precedence over config.yaml, so "Ansible owns config.yaml" does
      not reach it. `k3s_config` role has this behind `k3s_repoint_server`.
- [ ] CoreDNS 1 → 3 replicas. It is a k3s **addon** reconciled from a node
      manifest on checksum change, so `kubectl scale` is reverted by upgrades —
      needs a durable mechanism.
- [ ] CNPG `immich-db` 2 → 3 instances (lab3 is currently the sole switchover
      target, a pinch point for all three upgrade hops).
- [ ] PDBs for CoreDNS and ArgoCD (ingress-nginx now has one).

### Phase 1 — Ansible ✅ largely built
`site.yml` converges; `20-config-converge.yml` applies k3s config + restarts;
`40-add-node.yml` joins a node safely; `90-preflight.yml` audits read-only.

Still to write: `30-upgrade.yml` (the rolling upgrade — see Phase 2 for the
gates it must implement).

### Phase 2 — k3s 1.32.10 → **1.35.6** (hard stop)
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
**The central correction: keep `flannel-backend: vxlan` for the entire
migration.** In k3s flannel runs *inside the server process*, not as a
DaemonSet, so flipping it per node destroys the bridge the dual-overlay
migration depends on and partitions the pod network. `flannel-backend: none` is
a cleanup-phase, all-nodes-at-once change.

- Pod CIDR **10.245.0.0/16**, tunnel port **8473** (flannel keeps 8472)
- `cni.binPath: /var/lib/rancher/k3s/data/cni` — `/opt/cni/bin` does not exist
- **`enableLBIPAM: false` and `defaultLBServiceIPAM: none`** — chart defaults
  are `true`/`lbipam` and would fight MetalLB
- **Leave `MTU: 0`.** Both overlays compute 1450; pinning 1500 is the classic
  silent breakage (handshakes fine, large transfers hang)
- Keep kube-proxy. Order: lab5 → lab4 → lab3 → lab2 → **lab1 last**
- Install out-of-band via `helm`; adopt into ArgoCD only after cleanup
  (`selfHeal` + `prune` would fight a half-migrated state and delete the
  `CiliumNodeConfig`)
- **Still needs a written rollback procedure before starting.**

### Phase 4 — Headlamp + Alertmanager
API-server OIDC via `kube-apiserver-arg` (Ansible owns config.yaml by then).
⚠️ k3s refuses to start on an invalid apiserver flag — a typo bricks a server.
Roll `serial: 1`. Alertmanager currently has **no receivers at all**.

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
