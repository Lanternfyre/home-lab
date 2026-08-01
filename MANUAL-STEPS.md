# Manual steps — things Claude cannot do for you

Living list of actions that need a human: console access, credentials, physical
hardware, sudo passwords, or a judgement call about your own data.

**Legend:** 🔴 blocks the modernization plan · 🟡 do soon · 🟢 whenever

Last updated: 2026-08-01

---

## 🔴 Blocks the k3s upgrade (Phase 2)

### 1. Verify an etcd snapshot actually restores
Snapshots exist on the nodes, but an unrestored snapshot is not a backup, and
**minor-version downgrades are impossible** — an etcd restore is the *only*
rollback from a bad upgrade hop.

```bash
ssh 192.168.33.3 'sudo ls -la /var/lib/rancher/k3s/server/db/snapshots/'
ssh 192.168.33.3 'sudo k3s etcd-snapshot save --name pre-upgrade-verify'
# copy it off the node, then restore it onto a scratch VM and confirm the
# API server comes up and `kubectl get nodes` works.
```
Also worth checking: snapshots for **lab1 and lab2 do not appear** in
`kubectl get etcdsnapshotfiles`. Could be retention rotation, could be a config
gap. Find out which.

### 2. Set the Google OAuth client to "Internal"
Google Cloud Console → project `techyon-393614` → OAuth consent screen →
set user type to **Internal**.

This makes Google reject non-`techyon.dev` accounts **server-side** with
`org_internal` — unbypassable, and the strongest single control in the planned
auth design. Costs nothing.

**Verify first** that the GCP project sits under your Workspace organisation;
"Internal" is unavailable otherwise.

### 3. Confirm Workspace alias domains
The planned Envoy Gateway `SecurityPolicy` authorises on the Google `hd` claim,
which carries the **primary** domain only. If `techyon.dev` has alias or
secondary domains in Workspace, users on them get a 403.

List every domain in Workspace admin and tell me, so they all go into the
policy's `values[]`.

---

## 🟡 Do soon

### 4. Decide the fate of the 57 GB Immich library
Currently protected against *deletion* (Retain + protect label + the coming
Kyverno rule) but has **no second copy anywhere**. It is on SMB, so it is immune
to the QNAP CSI `mkfs` bug — but nothing protects it from the NAS failing.

It is the single highest-value dataset in the cluster and the only one where
"off the NAS" is the entire point. Options: an external USB disk, `pc.home`,
or cloud (R2 / GCS / B2, roughly €1–2/month for 57 GB).

### 5. Check free space on the QNAP
Backups now land on the NAS. Confirm there is headroom in QTS → Storage &
Snapshots. Current commitments: ~46 MB/night immich-db, ~85 KB/night
postgres-ha, plus small Mealie and Pi-hole tarballs, all with 14-day retention
— so roughly 1–2 GB steady state. Not large, but worth confirming it fits.

### 6. Verify the two leaked NAS volumes, then delete them
The audit finds two `TridentVolume`s with **no PV and no PVC** — almost
certainly still consuming ~10 GB:

```
pvc-2db4ec71-9b16-4ab9-891f-9d4f5e47022e   5Gi  iSCSI  (LUN trident-pvc-2db4ec71-…)
pvc-a33b3db5-7403-4990-93cf-e017f3b73ab4   5Gi  SMB    (share trident-pvc-a33b3db5-…)
```

They trace to the leftover experiment at repo root `mealie-pvc-qnap.yaml`
(a 5Gi `pvc-qts-david` claim + a `storage-test` busybox pod), dated ~8–9 Jan.

**Check them in QTS before deleting anything.** Both StorageClasses were
`reclaimPolicy: Delete` at the time, so a mistake here is irreversible.

Also stale, and safe to remove from the repo once you have looked:
`mealie-pvc-qnap.yaml` and the empty `test-pvc-qnap.yaml`.

### 7. Delete my leftover test StorageClass
I created this while proving the driver ignores `mountOptions`, and
`kubectl delete sc` is blocked for me by the permission classifier:

```bash
kubectl delete sc qnap-samba-backup-test
```
Nothing uses it; its test PVC is already gone.

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
