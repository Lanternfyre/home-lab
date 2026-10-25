# Restoring data: what you can restore from today

This runbook is for the operator after data loss or a broken cluster. It says what restore sources exist today, and the steps for each one. The steps that overwrite data are marked; none of them has been rehearsed here. Terms such as CNPG are in the [glossary](../domain/glossary.md).

## What exists today

Checked against the live cluster on 2026-10-25:

- **etcd snapshots: yes.** k3s takes one at 00:00 and 12:00 on each of the three servers (k8s-lab1, k8s-lab2, k8s-lab3) and keeps ten on each. k8s-lab1 also holds named ones: pre-upgrade, pre-roleswap, on-demand and `pre-token-rotation` from 2026-09-06. They live in `/var/lib/rancher/k3s/server/db/snapshots` on each server's own disk, not off the cluster. They hold the cluster's API state (every Kubernetes object), not the data inside volumes. Check: `kubectl get etcdsnapshotfiles.k3s.cattle.io` listed 45, the newest at 12:00 on all three servers.
- **Database dumps and file archives: no new ones.** The jobs that dumped postgres-ha and immich-db and archived Mealie and Pi-hole were deleted on 2026-08-15, in favour of a Velero deployment that never happened. Today there are no backup CronJobs, no CNPG `ScheduledBackup`s and no `spec.backup` on either CNPG cluster. The four backup volumes on the NAS still exist (`databases/pg-backup`, `immich/immich-db-backup`, `home-utils/mealie-backup`, `dns/pihole-backup`), and [Modernization](../../MODERNIZATION.md) says they hold the last dumps, stale since 2026-08-16. Unverified: their contents were not checked for this page.
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

⚠️ **Overwrites data, and untested.** No restore has been run since the dump jobs were deleted.

First, make sure the data is really lost. A volume returning `Input/output error`, or database pods that are wedged, is usually a storage fault, and its recovery keeps the data: start at [Standing warnings](standing-warnings.md#nodes-and-storage).

`postgres-ha` holds two databases, `authentik` and `reportportal`. The only dumps that can exist are cold ones from August 2026. [Modernization](../../MODERNIZATION.md) records two: a pre-incident dump from 2026-08-07, and `postgres-ha-20260815T165534Z.sql.gz`, a `pg_dumpall` of the whole cluster taken after the recovery. It says the dumps have been stale since 2026-08-16. Whether they are still on the share was not checked. That limits what a restore can do:

- **authentik cannot be restored.** Its database was created on 2026-08-31, after the newest dump.
- **ReportPortal goes back to mid-August.** Everything written since is lost.

If you go ahead:

1. **Find the dump.** The backup volumes are SMB shares on the QNAP (StorageClass `qnap-samba`), one per database: `pg-backup` for postgres-ha and `immich-db-backup` for immich-db. Browse the share in the QNAP web interface, which works without a healthy cluster, and copy the file to your workstation.
2. **Stop the writers, through git.** The chart renders a replica count even where the values file sets none, and ArgoCD's server-side apply owns that field (`spec.replicas` in the Deployment's managed fields, checked 2026-10-25), so selfHeal reverts a `kubectl scale`. Set ReportPortal's replicas to 0 in `gitops/clusters/home/apps/reportportal/chart-values.yaml` (that file sets none today; unverified which chart keys do), merge, and confirm with `kubectl -n reportportal get deploy` that every Deployment reads `0/0`. Note the counts you changed, for step 6.
3. **Keep a copy of what is there now**, so the restore can be undone. Find the primary with `kubectl -n databases get pods -l cnpg.io/cluster=postgres-ha,cnpg.io/instanceRole=primary`, then:
   ```bash
   kubectl -n databases exec <primary> -c postgres -- pg_dumpall | gzip > postgres-ha-before-restore.sql.gz
   ```
4. **Load the dump.** A `pg_dumpall` recreates roles and databases, so it needs a superuser. The `tarjei` role is one, and its password is the `password` key of the Secret `databases/pg-role-admin`. From your workstation, through a port-forward:
   ```bash
   kubectl -n databases port-forward svc/postgres-ha-rw 5432:5432 &
   export PGPASSWORD="$(kubectl -n databases get secret pg-role-admin -o jsonpath='{.data.password}' | base64 -d)"
   gunzip -c postgres-ha-<ts>.sql.gz | psql -h 127.0.0.1 -U tarjei postgres
   kill %1; unset PGPASSWORD
   ```
   psql reports an error for each object that already exists and carries on, so read its output. Settle the open questions below before this step.
5. **Verify the data, not the server.** `\l` lists both databases whether or not the restore worked. Instead, open in the ReportPortal UI something you know existed in mid-August, and something you know was created after it: the first must be back, and the second gone.
6. **Start the writers again**: revert the replicas change from step 2 and merge.

immich-db works the same way, with its own share, Service and namespace: a port-forward to `svc/immich-db-rw` in `immich`, then `gunzip -c immich-db-<ts>.sql.gz | psql -h 127.0.0.1 -U immich postgres`. Unverified: `immich` is the database's owner, not a superuser, and superuser access is off on that cluster, so a `pg_dumpall` may not load as `immich`.

### Open questions

- **Clear the target first?** Loading over a database that still holds data mixes the two. Whether to drop or empty `reportportal` before step 4 has not been decided or tested.
- **Roles in the dump.** A `pg_dumpall` replays roles and their passwords. CNPG manages the roles on `postgres-ha` (`tarjei`, `necronia`, `techyon`, `reportportal`, `authentik`); whether replaying them conflicts with CNPG has not been tested.

## Restore a file volume from an archive

⚠️ **Overwrites data**, under the same condition: an archive from mid-August 2026 has to still be on the share (`mealie-backup` for Mealie, `pihole-backup` for Pi-hole). Stop the app first, or it overwrites you. As with the databases, ArgoCD owns the Deployment's replicas, so stop it through git: set the replicas to 0 in the app's `chart-values.yaml`, merge, and wait for `kubectl -n home-utils get deploy mealie` to read `0/0`. Then extract `mealie-<ts>.tar.gz` over the volume's `/app/data`, and revert the change to start it again. Unverified: how to reach the volume's files while the app is stopped is not written down here.

The old way to take a backup on demand (`kubectl create job --from=cronjob/pg-backup …`) fails today, because those CronJobs no longer exist.
