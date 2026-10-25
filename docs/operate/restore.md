# Restoring data: what you can restore from today

This runbook is for the operator after data loss or a broken cluster. It says what restore sources exist today, and the steps for each one. The steps that overwrite data are marked; none of them has been rehearsed here.

## What exists today

Checked against the live cluster on 2026-10-25:

- **etcd snapshots: yes.** k3s takes one at 00:00 and 12:00 on each of the three servers (k8s-lab1, k8s-lab2, k8s-lab3) and keeps ten on each. k8s-lab1 also holds named ones: pre-upgrade, pre-roleswap, on-demand and `pre-token-rotation` from 2026-09-06. They live in `/var/lib/rancher/k3s/server/db/snapshots` on each server's own disk, not off the cluster. They hold the cluster's API state (every Kubernetes object), not the data inside volumes. Check: `kubectl get etcdsnapshotfiles.k3s.cattle.io` listed 45, the newest at 12:00 on all three servers.
- **Database dumps and file archives: no.** The jobs that dumped postgres-ha and immich-db and archived Mealie and Pi-hole were deleted on 2026-08-15, in favour of a Velero deployment that never happened. Today there are no backup CronJobs, no CNPG `ScheduledBackup`s and no `spec.backup` on either CNPG cluster. The four SMB backup volumes still exist (`databases/pg-backup`, `immich/immich-db-backup`, `home-utils/mealie-backup`, `dns/pihole-backup`), so they may still hold dumps from before 2026-08-15. Unverified: their contents were not checked.
- **The Immich photo library: one copy.** It lives once, on the NAS. A second copy was deferred by decision.

Not a backup, but it prevents the commonest loss: every protected volume is `reclaimPolicy: Retain` and labelled `homelab.techyon.dev/protect`, so deleting a PVC by mistake keeps the data. Check that the protection holds at any time (read-only):

```bash
./scripts/audit-protected-volumes.py
```

## Restore the cluster's state from an etcd snapshot

⚠️ **Destructive and untested.** This overwrites the whole etcd datastore. It has never been rehearsed on this cluster; skipping the rehearsal was accepted knowingly on 2026-08-01. Use it when the cluster's objects are lost or corrupted, not to recover data inside a volume.

**Needs:** sudo on all three servers, and a console on each in case the API never comes back.

The steps follow the k3s documentation ([snapshot restore](https://docs.k3s.io/cli/etcd-snapshot#snapshot-restore-steps)). Servers run `k3s.service`; agents run `k3s-agent.service`.

1. Pick a snapshot: `ls -l /var/lib/rancher/k3s/server/db/snapshots/` on a server.
2. **If the snapshot is older than 2026-09-06**, it needs the cluster token from before that day's rotation. That token was archived on purpose; see [Cluster join token](../../CLUSTER-TOKEN.md). Restoring onto a different host also needs the token that was in use when the snapshot was taken.
3. Stop k3s on every node: `k3s-killall.sh`.
4. On the first server (the one with no `server:` entry in its k3s config), restore:
   ```bash
   sudo k3s server --cluster-reset --cluster-reset-restore-path=<path-to-snapshot>
   ```
   Then start it again: `sudo systemctl start k3s`.
5. On each of the other two servers, delete the database and start k3s, so they rejoin the restored member:
   ```bash
   sudo rm -rf /var/lib/rancher/k3s/server/db
   sudo systemctl start k3s
   ```
6. Start the agents: `sudo systemctl start k3s-agent` on k8s-lab4 to k8s-lab7 and on the edge node.

**Verify:** `kubectl get nodes` lists all eight nodes as Ready, and ArgoCD's Applications go back to Synced.

**Watch out:** a node that has been demoted from server to agent has lost its snapshots, along with the rest of `/var/lib/rancher/k3s`. The role-change playbook takes a fresh snapshot on a peer first.

## Restore a database from a dump

⚠️ **Overwrites data.** Only possible if one of the old dumps from before 2026-08-15 is still on the share. These commands are from when the dump jobs ran, and they have not been run since.

The backup shares can be browsed from the QNAP web interface, so finding a dump does not need a healthy cluster. With the share's contents available as `/backup`:

```bash
gunzip -c /backup/immich-db-<ts>.sql.gz | psql -h immich-db-rw -U immich postgres
gunzip -c /backup/postgres-ha-<ts>.sql.gz | psql -h postgres-ha-rw -U tarjei postgres
```

## Restore a file volume from an archive

⚠️ **Overwrites data**, under the same condition: an archive from before 2026-08-15 has to exist. Scale the app down first, or it overwrites you:

```bash
kubectl -n home-utils scale deploy mealie --replicas=0
# extract mealie-<ts>.tar.gz over /app/data
kubectl -n home-utils scale deploy mealie --replicas=1
```

The old way to take a backup on demand (`kubectl create job --from=cronjob/pg-backup …`) fails today, because those CronJobs no longer exist.
