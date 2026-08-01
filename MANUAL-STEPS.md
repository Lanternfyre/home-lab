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

### 1. Switch the Google OAuth consent screen to "Internal"
**CHECKED 2026-08-01 — it is currently External. The assumption did not hold.**

Evidence:
- The project **is** under the `techyon.dev` organization (org `185150869552`),
  so "Internal" is available.
- `orgInternalOnly` is **absent** from both `gcloud iap oauth-brands list` and
  the raw `iap.googleapis.com/v1/.../brands` response. Google's APIs omit
  `false` booleans (proto3 default-value omission), so the brand is External.
  Brand: `projects/138732748534/brands/138732748534` ("Techyon").

Action: GCP Console → project `techyon-393614` → APIs & Services →
OAuth consent screen → **User type → Internal**.

The `techyon.dev`-only behaviour you observe today comes entirely from
application-level config, not from Google:

| Where | Setting |
|---|---|
| `oauth2-proxy/chart-values.yaml:17` | `email-domain: techyon.dev` |
| `prometheus/chart-values.yaml:33-34` | `allowed_domains` + `hosted_domain` |
| `argocd-values.yaml:25-27` | `hd` claim marked `essential: true` |

So today's restriction would look identical whether the client is Internal or
External. **This matters because Phase 6 retires oauth2-proxy**, and if the
client turns out to be External, that layer disappears with it.

Check: GCP Console → project `techyon-393614` → APIs & Services →
OAuth consent screen → **User type**. If it says External and "Internal" is
available, switch it — Google then rejects non-org accounts server-side with
`org_internal`, which is unbypassable and free.

(I could not check this for you: `gcloud` is installed and authenticated as
`adrian.jutrowski@techyon.dev`, but its token is expired —
`invalid_grant: Bad Request`. Run `gcloud auth login` if you would rather I
verified it.)

Either way the plan is safe: the Envoy Gateway `SecurityPolicy` enforces the
`hd` claim itself. "Internal" is defence in depth, not the only line.

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
