# Manual steps — things Claude cannot do for you

Living list of actions that need a human: console access, credentials, physical
hardware, sudo passwords, or a judgement call about your own data.

**Legend:** 🔴 blocks the modernization plan · 🟡 do soon · 🟢 whenever

Last updated: 2026-08-01

---

## ✅ Done (2026-08-01) — do not repeat

- **Test StorageClass deleted.** `qnap-samba-backup-test` is gone. Confirmed.
- **QNAP has enough space.** Confirmed by you.
- **Pre-upgrade etcd snapshot taken** —
  `pre-upgrade-verify-k8s-lab1-1785613636`, 39 MB, registered on k8s-lab1.
- **Restore-to-a-VM test: deliberately skipped.** Your call, and see below for
  why it is less risky than it first looked.
- **Snapshots stay on the nodes**, not copied off. Your call.

### On skipping the restore test — the risk is smaller than I claimed
My earlier note said lab1 and lab2 had no snapshots. **That was wrong.** All
five nodes are taking them twice daily (00:00 and 12:00):

```
k8s-lab1  6   k8s-lab2  5   k8s-lab3  5   k8s-lab4  3   k8s-lab5  3
```

Each snapshot is a complete copy of cluster state, so this is five independent
copies. Losing any one node loses nothing — which is most of what copying them
off-node would have bought. Keeping them on the nodes is a reasonable call.

What remains untested is **not the snapshot file** — it is the *restore
procedure*. On a 5-member etcd cluster that means
`k3s server --cluster-reset --cluster-reset-restore-path=<snap>` on one node,
then wiping `/var/lib/rancher/k3s/server/db` on the other four and rejoining
them. That is the part people get stuck on at 2am, and it is still unrehearsed.
Accepted knowingly; the mitigation is that the upgrade gates are strict and
each hop is verified before the next.

---

## 🔴 Blocks the k3s upgrade (Phase 2)

### ~~1. Switch the Google OAuth consent screen to "Internal"~~ ✅ DONE
Verified 2026-08-01 after you changed it: the IAP brands API now returns
`orgInternalOnly: true` for `projects/138732748534/brands/138732748534`
("Techyon"). Google now rejects non-`techyon.dev` accounts **server-side** with
`org_internal`, before any of your apps see the request.

This matters for Phase 6: when oauth2-proxy is retired, its `email-domain`
check goes with it. The Envoy Gateway `SecurityPolicy` will enforce the `hd`
claim itself, and this setting is now the second, unbypassable layer under it.

### ~~2. Regenerate the GHCR token~~ ✅ DONE
Refreshed by you 2026-08-01 and verified: `api.github.com/user` returns HTTP
200, and a GHCR pull token now fetches the `qnap-trident` 0.1.1 chart manifest
(HTTP 200). ESO needed a forced sync (`force-sync` annotation) because its
refresh interval is 1h.

Result: **ArgoCD Unknown apps went 15 → 0**, and the QNAP CSI v1.6.2 upgrade
finally rolled — operator, controller, sidecar and all node pods, with
`TridentOrchestrator.status.currentInstallationParams` confirming v1.6.2.
Zero disruption: 18/18 PVCs Bound, 13 VolumeAttachments intact.

**Worth remembering:** this had been silently freezing five apps, including the
storage driver, and nothing alerted. The ExternalSecret reported `Ready=True`
throughout — it delivered the value correctly; it cannot know the registry
rejects it. If you set the new PAT to expire, put a calendar reminder on it,
because the failure mode is invisible.

### 2b. Fix lab4/lab5 — RUN ANSIBLE, do not hand-edit

**This is now a playbook, not a copy-paste session.** The only reason it was
ever manual is that sudo needs a password, which I cannot type.

```bash
cd ~/Private/home-lab/ansible

# 1. see what is broken -- read-only, no sudo, safe any time
ansible-playbook playbooks/90-preflight.yml

# 2. review what would change
ansible-playbook playbooks/10-baseline.yml --ask-become-pass --check --diff

# 3. apply -- detects per node and fixes ONLY what that node fails
ansible-playbook playbooks/10-baseline.yml --ask-become-pass

# 4. lab5 only: its single iSCSI session is a STALE leftover pointing at
#    pvc-4de1d672-..., a test volume already deleted (0 mpath devices, no
#    PVC-bearing pods on the node). The normal run defers its multipathd
#    restart because it sees a session; forcing it here is safe and is what
#    makes find_multipaths take effect.
ansible-playbook playbooks/10-baseline.yml --ask-become-pass \
  --limit k8s-5.home -e multipath_force_restart=true
```

What it will do, measured live:

| node | DNS | multipath | inotify | iscsid | action |
|---|---|---|---|---|---|
| lab1/2/3 | ok | ok | BROKEN | ok | sysctl only — multipathd NOT restarted (4 LUNs each) |
| lab4 | BROKEN | BROKEN | BROKEN | BROKEN | all four |
| lab5 | ok | BROKEN | BROKEN | ok | multipath + sysctl |

**If lab4 still fails DNS after step 3**, the play says so explicitly and stops.
Its resolver stub returns REFUSED to glibc while `resolvectl` works, and a
config rewrite has not cleared it — that node needs a **reboot**, then re-run.

Then tell me and I will re-run the storage proof on both nodes and uncordon
them if it passes. **Do not uncordon manually** — the proof is the gate.

<details>
<summary>Historical: the equivalent manual commands</summary>
**PROVEN by test 2026-08-01, not theorised. This blocks uncordoning both nodes,
and therefore blocks every drain in the k3s upgrade and the CNI migration.**

I created a 1Gi `qnap-iscsi` PVC and a pod pinned to lab5. It failed:

```
MountVolume.MountDevice failed ... rpc error: code = Internal
  desc = failed to stage volume: multipath device not found when it is expected
```

Cause — lab4 and lab5 have the WRONG `/etc/multipath.conf`:

| Node | multipath.conf | iscsid |
|---|---|---|
| lab1 / lab2 / lab3 ✅ | `find_multipaths no` + blacklist | active |
| **lab5** ❌ | `user_friendly_names` only | active |
| **lab4** ❌ | `user_friendly_names` only | **inactive** |

multipath-tools 0.9.4 defaults `find_multipaths` to `strict`, so a single-path
QNAP LUN never gets a device-mapper entry and Trident cannot stage it.

*Correction to something I told you earlier:* I previously reported
"multipath.conf present on all 5 nodes ✓". That checked **presence, not
content**. It is present on both new nodes and wrong — the same class of
mistake the audit script explicitly guards against for sysctls.

**Fix — run on BOTH `k8s-4.home` and `k8s-5.home`:**

```bash
sudo tee /etc/multipath.conf >/dev/null <<'CONF'
defaults {
    find_multipaths no
    user_friendly_names yes
}
blacklist {
    devnode "^(ram|raw|loop|fd|md|dm-|sr|scd|st|sda)[0-9]*"
}
CONF
sudo systemctl restart multipathd
sudo systemctl enable --now iscsid     # lab4 needs this; it is inactive there
systemctl is-active multipathd iscsid  # both should print "active"
```

**And on `k8s-4.home` only**, its resolver stub is still wedged (`getent hosts
registry.k8s.io` fails, so containerd cannot pull ANY image):

```bash
sudo systemctl restart systemd-resolved
getent hosts registry.k8s.io && echo OK          # if this works, you are done

# if it does NOT work, harden the config and reboot:
sudo tee /etc/systemd/resolved.conf >/dev/null <<'CONF'
[Resolve]
DNS=1.1.1.1 8.8.8.8
FallbackDNS=8.8.4.4
DNSStubListener=yes
CONF
sudo systemctl restart systemd-resolved
getent hosts registry.k8s.io || sudo reboot
```

Tell me when done and I will re-run the storage proof on both nodes and uncordon
them if it passes. **Do not uncordon them yourself** — the proof is the gate.

</details>

### 3. Confirm Workspace alias domains
The planned Envoy Gateway `SecurityPolicy` authorises on the Google `hd` claim,
which carries the **primary** domain only. If `techyon.dev` has alias or
secondary domains in Workspace, users on them get a 403.

List every domain in Workspace admin and tell me, so they all go into the
policy's `values[]`.

---

## 🟡 Do soon

### 4. Immich library — DEFERRED by decision, risk accepted
You have chosen to run without a second copy of the 57 GB library for now.
Recording it so it is a decision rather than an oversight.

What still covers it: `reclaimPolicy: Retain`, the `homelab.techyon.dev/protect`
label, ArgoCD prune/delete guards, and the fact that it lives on SMB and so
cannot be hit by the QNAP CSI `mkfs` bug.

What does NOT cover it: NAS failure, RAID loss, QTS corruption, ransomware, or
an accidental deletion made on the NAS itself. This is a 2-bay TS-231K and the
library exists in exactly one place.

Revisit when convenient — an external USB disk or `pc.home` costs nothing, and
cloud is roughly €1–2/month for 57 GB.

### ~~6. Verify the two leaked NAS volumes, then delete them~~ ✅ DONE
Removed 2026-08-01 on your instruction, after verifying each had no PV, no PVC,
no VolumeAttachment, no publication and no reference in git — and after the
v1.6.2 reconciliation had itself re-flagged both as `orphaned: true`:

```
pvc-2db4ec71-…  5Gi  qnap-iscsi  block/RWO   created 2026-01-09
pvc-a33b3db5-…  5Gi  qnap-samba  file/RWX    created 2026-01-08
```

Deleted with `tridentctl delete volume` so the backend storage went too, not
just the record. Trident volume count 19 → 17; ~10 GB reclaimed on the NAS.
The orphaned `TridentVolumePublication` (`pvc-bc4f35d9-….k8s-lab1`) was removed
in the same pass, 14 → 13.

Verified afterwards: 18/18 PVCs Bound, all 13 VolumeAttachments intact, both
CNPG clusters healthy. The stale repo-root files `mealie-pvc-qnap.yaml` and
`test-pvc-qnap.yaml` that created them have also been removed.

### 8. Rotate the k3s cluster token
`k3sblog` is committed in plaintext in `gitops/argo-install.md`. Anyone with it
and network reach can join a server to your cluster. Rotate it and move the new
value into `ansible/vault/secrets.yml` when the Ansible layer lands.

### 9. Decide: keep or remove Envoy Gateway's idle install
Not urgent on its own, but it is what broke the k3s Traefik addon (580+
crashloops: Gateway API CRDs installed without Helm ownership labels). The plan
adopts Envoy Gateway, so the answer is probably "keep and finish it" — but if
you would rather not, say so before I do the CRD ownership work.

---

## 🟢 Whenever

### 10. Sudo on the nodes
Ansible will run with `--ask-become-pass`, which is fine. If you would rather it
run unattended, configure `NOPASSWD` for `tarjei` on all five nodes — a standing
credential on every control-plane node, so your call:

```bash
# on each of k8s-1.home … k8s-5.home
echo 'tarjei ALL=(ALL) NOPASSWD:ALL' | sudo tee /etc/sudoers.d/50-ansible
sudo visudo -c
```

### 11. SSH host keys for lab2 and lab3
Both currently fail host-key verification from this workstation. Either accept
them once, or I will set `host_key_checking = False` in `ansible.cfg`:

```bash
ssh-keyscan -H 192.168.33.2 192.168.33.7 >> ~/.ssh/known_hosts
```

### 12. Add `.idea/` and `.serena/` to `.gitignore`
Both are untracked IDE/tooling cruft sitting in the repo root.

---

## Restore procedures — how to actually use the backups

Backups run nightly to SMB shares on the NAS. All are browsable directly from
the QNAP GUI, so **a restore does not require the cluster to be healthy.**

| What | Schedule | Location |
|---|---|---|
| postgres-ha (all DBs) | 02:15 | `pg-backup` share, `postgres-ha-<ts>.sql.gz` |
| immich-db | 02:45 | `immich-db-backup` share, `immich-db-<ts>.sql.gz` |
| Mealie data | 03:15 | `mealie-backup` share, `mealie-<ts>.tar.gz` |
| Pi-hole config | 03:45 | `pihole-backup` share, `pihole-<ts>.tar.gz` |

Retention is 14 days on all four.

**Restore a database:**
```bash
gunzip -c /backup/immich-db-<ts>.sql.gz | psql -h immich-db-rw -U immich postgres
gunzip -c /backup/postgres-ha-<ts>.sql.gz | psql -h postgres-ha-rw -U tarjei postgres
```

**Restore a file volume** (scale the app down first, or it will overwrite you):
```bash
kubectl -n home-utils scale deploy mealie --replicas=0
# extract mealie-<ts>.tar.gz over /app/data
kubectl -n home-utils scale deploy mealie --replicas=1
```

**Run any backup on demand** rather than waiting for the schedule:
```bash
kubectl -n databases  create job --from=cronjob/pg-backup        pg-backup-now
kubectl -n immich     create job --from=cronjob/immich-db-backup immich-db-now
kubectl -n home-utils create job --from=cronjob/mealie-backup    mealie-backup-now
kubectl -n dns        create job --from=cronjob/pihole-backup    pihole-backup-now
```

**Check protection at any time** (read-only, safe, CI-suitable):
```bash
./scripts/audit-protected-volumes.py
```

---

## Standing warnings

- **Do not uncordon k8s-lab4** until `kubectl get csinode k8s-lab4` shows a
  registered driver. It is Ready but cannot mount any QNAP volume; any
  PVC-bearing pod that lands there hangs in `ContainerCreating` forever.
- **Do not uncordon k8s-lab5** until a test PVC has been proven to attach there.
  It has never established an iSCSI session, so its storage path is unproven
  rather than merely untested.
- **Never `kubectl patch` the QNAP StorageClasses.** They are chart-managed and
  `selfHeal` silently reverts imperative patches. Change them in git.
- **Backups live on the same NAS as the data.** They cover driver bugs,
  accidental deletion and bad restores. They do **not** cover the NAS failing.
