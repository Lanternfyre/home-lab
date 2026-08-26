# Manual steps — things Claude cannot do for you

Living list of actions that need a human: console access, credentials, physical
hardware, sudo passwords, or a judgement call about your own data.

**Legend:** 🔴 blocks the modernization plan · 🟡 do soon · 🟢 whenever

Last updated: 2026-08-26

---

## 🔴 MinIO — two things before the first sync (2026-08-26)

`apps/minio/` is committed but will not come up correctly until both of these
exist. Do them in this order.

### 1. Create the 1Password item

Vault **Infrastructure**, item titled exactly **`MinIO`**, with two fields:

| Field | Value |
|---|---|
| `rootUser` | 40 chars |
| `rootPassword` | 40 chars |

⚠️ **Generator with symbols OFF — [A-Za-z0-9] only.** These are S3 credentials
and end up interpolated raw into endpoint URLs and connection strings by tooling
that does not URL-encode. A `/` in the secret key truncates it silently and
surfaces as `SignatureDoesNotMatch`, which reads like a clock or region problem
and sends you looking in entirely the wrong place.

⚠️ **Before first sync, not after.** If `minio-auth` is missing or incomplete the
chart generates its own root credential into a Secret it owns, and regenerates it
on some upgrades — at which point every stored credential elsewhere stops
matching at once.

Then put the same two values into the GitHub org secrets on **Lanternfyre**:
`OBJECT_STORE_ACCESS_KEY_ID` and `OBJECT_STORE_SECRET_ACCESS_KEY`. CI hard-fails
on an unreachable store by design, so a half-done rotation reds every pipeline
rather than degrading quietly.

### 2. Register the console's OAuth redirect URI

`minio.lab.techyon.dev` is on the **gated** gateway, so add

```
https://minio.lab.techyon.dev/oauth2/callback
```

as an authorized redirect URI on the Google OAuth client (same client as every
other gated hostname). Without it the console returns an OAuth error rather than
a login page.

The S3 API host `s3.lab.techyon.dev` is on the **open** gateway and needs nothing
here — deliberately, because `mc`, the CI runners and any future Velero or CNPG
backup cannot complete a browser OIDC redirect.

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

## ✅ Storage fix — APPLIED and verified 2026-08-17

### 0. Converge `no_path_retry` onto the nodes — ✅ DONE

**This section read 🔴 "written but NOT applied" until 2026-08-17, when a
preflight run showed all 12 LUNs already at `120`.** The sudo run had happened;
nothing updated the docs. Both this file and MODERNIZATION.md were telling
whoever picked the work up next that the cluster was still one NAS blip from
another week-long outage. Recorded here rather than quietly deleted, because
"the doc says it is broken and it is not" costs the same kind of time as the
reverse.

Verify any time — read-only, no sudo, asserts behaviour rather than file
contents:

```bash
cd ansible
ansible-playbook playbooks/90-preflight.yml
```

`mpath queueing:` must read `mpathX|120` for every LUN; `-` means that LUN is
**not** protected. `wedged ext4:` must be empty. As of 2026-08-17: 12/12 LUNs
at 120 across lab2/3/4/5 (lab1 has no PVCs scheduled), nothing wedged.

If it ever needs re-applying — a rebuilt node, a reverted config:

```bash
cd ansible
ansible-playbook site.yml --tags storage --ask-become-pass
```

**What it changes:** adds `no_path_retry 120` + `polling_interval 5` to
`/etc/multipath.conf`, so I/O to a QNAP LUN **queues for ~10 minutes** when the
NAS is unreachable instead of erroring immediately and taking the ext4 journal
down with it.

**Is it disruptive?** No. It applies with `multipathd reconfigure`, which
re-reads the config in place — no unit restart, no map teardown, no I/O
interruption — and the role then asserts the value is live rather than trusting
the exit code. The `multipath_force_restart` path is only for a sick multipathd
and still wants a drained node.

**The trade-off, stated plainly:** during a partition, pods touching these
volumes now **hang** instead of erroring. A process in uninterruptible sleep
cannot be killed, so an outage longer than ~10 minutes can leave pods stuck
until the node reboots. That is the better failure here — a ten-minute freeze
is recoverable; an aborted journal cost a week. Bounded at 120 rather than
`queue` (infinite) precisely so a NAS that never comes back degrades the node
instead of wedging kubelet on it forever.

This is **live behaviour now**, which changes what a future outage looks like
from the monitoring side. `volume-health-exporter` statfs's every PVC mount, and
during a partition those calls land in uninterruptible sleep rather than
returning EIO — so expect `VolumeHealthProbeStale` (the probe blocked) rather
than `VolumeFilesystemStatfsFailing`. Both page. See the note at the statfs loop
in `prometheus/manifests/volume-health-exporter.configmap.yaml`.

### 0b. 🟢 If a volume ever returns EIO after a confirmed unmount+remount

The recovery in MODERNIZATION.md (scale to 0, confirm unmount, scale up) fixed
all six volumes on 2026-08-15 and needs no sudo. If a future one is still
`Input/output error` *after* `journalctl -k` shows a real unmount and remount,
the on-disk filesystem is damaged and needs a repair pass, which does need root
and the volume detached:

```bash
# with the workload scaled to 0 and the volume confirmed unmounted:
ssh <node> sudo e2fsck -fy /dev/mapper/<mpathX>
```

Get `<mpathX>` from `findmnt -rno SOURCE,TARGET | grep <pvc-uid>` *before*
scaling down. Never run this against a mounted filesystem.

### 0c. 🟡 Release the LoadBalancer NodePorts (needs one patch per service)

**This is a step where the git change alone provably does nothing** — the
reason it is written down rather than assumed.

Every `LoadBalancer` Service in this cluster also allocates a NodePort, so each
one answers on its MetalLB address **and** on all five node IPs somewhere in
30000-32767. That second door is not less authenticated (it reaches the same
backend), but it defeats any firewall rule written against the `192.168.32.x`
addresses, and nothing in the repo mentioned it existed.

`allocateLoadBalancerNodePorts: false` is now set in git for `postgres-ha-lb`,
`redis-lb`, `immich-db-lb` and `ingress-nginx-controller`. **That only stops
NEW allocations.** Verified by server-side dry-run against the live API:
after applying the change, the flag reads `false` and `nodePort: 30669` is
still there. Removing the port on its own does not work either — while the flag
is still `true` live, the allocator immediately re-assigns the same number.

Both fields have to change in **one** update. This form is verified (dry-run)
to clear the port, set the flag, and **keep the MetalLB IP** — no delete and
recreate, so the address does not move and the external-dns records do not
churn:

✅ **The four below were run on 2026-08-17 and verified.** All five LB addresses
still answer; all five old NodePorts are closed on all five nodes (25/25);
`argocd-server:32497`, deliberately left open at the time, still answered — the
control that proves the probe was not simply returning CLOSED for everything.
Kept here as the reference form for the remaining services and for any future
Service.

```sh
kubectl -n databases patch svc postgres-ha-lb --type=merge -p \
  '{"spec":{"allocateLoadBalancerNodePorts":false,"ports":[{"name":"postgresql","port":5432,"targetPort":5432,"protocol":"TCP"}]}}'

kubectl -n redis patch svc redis-lb --type=merge -p \
  '{"spec":{"allocateLoadBalancerNodePorts":false,"ports":[{"name":"redis","port":6379,"targetPort":6379,"protocol":"TCP"}]}}'

kubectl -n immich patch svc immich-db-lb --type=merge -p \
  '{"spec":{"allocateLoadBalancerNodePorts":false,"ports":[{"name":"postgresql","port":5432,"targetPort":5432,"protocol":"TCP"}]}}'

kubectl -n ingress-nginx patch svc ingress-nginx-controller --type=merge -p \
  '{"spec":{"allocateLoadBalancerNodePorts":false,"ports":[{"name":"http","port":80,"targetPort":"http","protocol":"TCP","appProtocol":"http"},{"name":"https","port":443,"targetPort":"https","protocol":"TCP","appProtocol":"https"}]}}'
```

This is a legitimate exception to "never `kubectl patch`": `nodePort` is
allocated by the API server and is **not** declared in git, so `selfHeal` has
nothing to revert it to. Confirmed on the live object — no field manager claims
`nodePort` at all.

Verify (all four must return nothing):

```sh
kubectl get svc -A -o json | python3 -c '
import json,sys
for s in json.load(sys.stdin)["items"]:
    if s["spec"]["type"] != "LoadBalancer": continue
    np = [p.get("nodePort") for p in s["spec"]["ports"] if p.get("nodePort")]
    if np: print(s["metadata"]["namespace"], s["metadata"]["name"], np)'
```

Then confirm each service still answers on its LB address —
`192.168.32.10:5432`, `192.168.32.11:6379`, `192.168.32.15:5432`,
`192.168.32.13:80`. **`Ready` is not the test; a connection is.**

**The remaining seven services** — pihole (4 ports), `argocd-server` (2),
`mealie` (1) and the two Envoy Gateways (4) — are now covered *going forward*
by `apps/kyverno/manifests/loadbalancer-no-nodeports.mutatingpolicy.yaml`,
which sets the flag on every LoadBalancer Service at admission. That is what
made an `EnvoyProxy` CR unnecessary and what makes the four git-declared
services durable across a chart upgrade too.

**First verify the policy actually mutates** (creates nothing — server dry-run
runs the webhook and discards the object):

```sh
kubectl -n default create service loadbalancer np-probe --tcp=8080:8080 \
  --dry-run=server -o json | python3 -c '
import json,sys; s=json.load(sys.stdin)["spec"]
print("allocateLoadBalancerNodePorts:", s.get("allocateLoadBalancerNodePorts"))
print("ports:", s["ports"])'
```

Recorded **before** the policy existed, so the difference is unambiguous:

```
allocateLoadBalancerNodePorts: True
ports: [{... 'nodePort': 32769}]
```

It must now read `False` with no `nodePort`. If it still says `True`, the
policy is not doing anything — check `kubectl get mpol` status, and remember
`failurePolicy: Ignore` means a broken policy fails silently by design.

Only once that passes, clear the seven existing allocations (same one-update
rule as above; port specs taken from the live objects, so `targetPort` names
and protocols are preserved):

```sh
kubectl -n argocd patch svc argocd-server --type=merge -p \
  '{"spec":{"allocateLoadBalancerNodePorts":false,"ports":[{"name":"http","port":80,"protocol":"TCP","targetPort":8080},{"name":"https","port":443,"protocol":"TCP","targetPort":8080}]}}'

kubectl -n dns patch svc pihole-dns-tcp --type=merge -p \
  '{"spec":{"allocateLoadBalancerNodePorts":false,"ports":[{"name":"dns","port":53,"protocol":"TCP","targetPort":"dns"}]}}'

kubectl -n dns patch svc pihole-dns-udp --type=merge -p \
  '{"spec":{"allocateLoadBalancerNodePorts":false,"ports":[{"name":"dns-udp","port":53,"protocol":"UDP","targetPort":"dns-udp"}]}}'

kubectl -n dns patch svc pihole-web --type=merge -p \
  '{"spec":{"allocateLoadBalancerNodePorts":false,"ports":[{"name":"http","port":80,"protocol":"TCP","targetPort":"http"},{"name":"https","port":443,"protocol":"TCP","targetPort":"https"}]}}'

kubectl -n home-utils patch svc mealie --type=merge -p \
  '{"spec":{"allocateLoadBalancerNodePorts":false,"ports":[{"name":"http","port":9000,"protocol":"TCP","targetPort":"http"}]}}'

# ⚠️ These two front ALL 15 HTTPRoutes. Do them last, one at a time, and
# confirm https://grafana.lab.techyon.dev still loads between the two.
kubectl -n gateway-envoy patch svc envoy-gateway-envoy-homelab-b0a9a155 --type=merge -p \
  '{"spec":{"allocateLoadBalancerNodePorts":false,"ports":[{"name":"https-443","port":443,"protocol":"TCP","targetPort":10443},{"name":"http-80","port":80,"protocol":"TCP","targetPort":10080}]}}'

kubectl -n gateway-envoy patch svc envoy-gateway-envoy-homelab-gated-06cddf46 --type=merge -p \
  '{"spec":{"allocateLoadBalancerNodePorts":false,"ports":[{"name":"https-443","port":443,"protocol":"TCP","targetPort":10443},{"name":"http-80","port":80,"protocol":"TCP","targetPort":10080}]}}'
```

The Envoy Services are controller-generated, so if Envoy Gateway ever rebuilds
them the ports come back — but the policy stamps the flag at creation, so they
come back **without** node ports. That is the whole reason the policy exists
rather than seven more patches.

**Still open, deliberately not done here:**

- The genuinely LAN-exposed things a NetworkPolicy **cannot** touch:
  node-exporter `:9100` and metallb-speaker `:7472` **and** `:7473` all serve
  unauthenticated metrics to anything on the LAN (all three fetched from a pod
  on another node to confirm), and cilium-agent listens on `:9879`/`:4244`.
  All are `hostNetwork: true`, so their traffic is not pod traffic and no
  NetworkPolicy applies to it. Closing those needs a host firewall (nftables
  via Ansible, which needs the same sudo run as step 0) or dropping
  `hostNetwork`, which changes the `instance` label on every node-exporter
  series and would poison any alert with a lookback longer than the change.

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

### 2b. Fix lab4/lab5 — ✅ BOTH DONE (lab4 and lab5 uncordoned 2026-08-02)

> **✅ CLOSED. Re-verified by value on 2026-08-19** — this section is kept for
> the diagnosis, not as an open item. Both nodes now report:
> `kubectl get csinode` → 1 registered driver each · `find_multipaths no` in
> `/etc/multipath.conf` · `iscsid` and `multipathd` both `active` ·
> `fs.inotify.max_user_watches` = 1048576 · external registries resolve ·
> `.spec.unschedulable` unset, i.e. **neither is cordoned**.
> The header below used to read "🔴 lab5 STILL BLOCKED"; it had been fixed
> since 2026-08-02 and the doc simply never caught up.

**Status as of 2026-08-02, measured not assumed:**

| node | multipath.conf written | multipathd (re)started | verdict |
|---|---|---|---|
| lab4 | 2026-08-02 08:38:51 | 2026-08-02 08:38:51 | ✅ reloaded → **done, uncordoned** |
| lab5 | 2026-08-02 08:39:24 | 2026-07-31 06:53:50 | ❌ **never reloaded** |

lab5's `multipath.conf` is correct *on disk* and has never been loaded by the
running multipathd — the role deferred the restart because lab5 carries stale
iSCSI sessions. Proven behaviourally the same day with a live storage proof
(1Gi `qnap-iscsi` PVC + pod pinned to lab5 via `nodeName`), which failed with
the identical 2026-08-01 error:

```
MountVolume.MountDevice failed ... rpc error: code = Internal
  desc = failed to stage volume: multipath device not found when it is expected
```

The test PV was set to `reclaimPolicy: Delete` before deletion, so the backend
volume was reclaimed — verified, nothing leaked.

**⚠️ Do not judge this from a `--check` run.** `ansible-playbook ... --check`
reported `sysctls=BROKEN, services=BROKEN` on all five nodes and `liveLUNs=0`
on lab1/2/3 (which carry 4 each) — every one of those an artefact of Ansible
skipping `command:` tasks under check mode. Fixed 2026-08-02; see the
"hard-won findings" entry in `MODERNIZATION.md`.

**The one command that unblocks lab5** (needs your sudo password):

```bash
cd ~/Private/home-lab/ansible
ansible-playbook playbooks/10-baseline.yml --ask-become-pass \
  --limit k8s-5.home -e multipath_force_restart=true
```

Then tell me, and I re-run the storage proof and uncordon if it passes.

<details>
<summary>Original instructions (still accurate for a fresh node)</summary>


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

What it did, measured live on 2026-08-01 (**historical — steps 1–3 have since
been applied; only step 4 remains**):

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

### 9b. DEFERRED BY DECISION 2026-08-03 — retire the old `pihole` PVC

**A judgement call you explicitly parked, kept here so it does not evaporate.**

The old `pihole` PVC (5Gi, `local-path`, pinned to **k8s-lab3**) is the Phase
0.D rollback point from moving Pi-hole to `qnap-iscsi`. Pi-hole has been proven
on the new volume since 2026-08-02.

It is now the **only** thing keeping any ArgoCD app OutOfSync — 60 Synced / 1
OutOfSync, and that one is this. The chart no longer renders the PVC, so ArgoCD
marks it `requiresPruning: true` while `Prune=false,Delete=false` blocks the
prune. Permanent by construction, not a fault.

Two ways to make it green, both understood, neither started:

1. **Adopt it into git.** Add a `pihole-old.pvc.yaml` to
   `gitops/clusters/home/apps/pihole/manifests/`, next to the two PVCs already
   declared there. Ownership moves to `pihole-manifests`, so the Helm app stops
   seeing an extraneous resource and both go green. Keeps the rollback point
   and upgrades it from untracked leftover to declared intent. Reversible;
   deletes nothing.
2. **Retire it.** ⚠️ One-way, and it needs **three** steps, not one:
   - remove `homelab.techyon.dev/protect: "true"` from **both** the PVC and
     the PV `pvc-c9212135-243e-4c78-8367-5df27209b626` — the Kyverno VAP denies
     the delete otherwise, in-process, even with Kyverno down;
   - delete the PVC, then the PV (it is `reclaimPolicy: Retain`, so the PV
     does not go on its own);
   - **then clean up on k8s-lab3 by hand.** This is local-path, so the data is
     a directory on that node's disk and nothing above removes it. Unlike the
     `qnap-iscsi` volumes there is no `tridentctl delete volume` to run — it is
     `sudo rm -rf` on the node, which is why this sits in MANUAL-STEPS.

Do not "tidy" this away without deciding which one you want — it holds the only
copy of the pre-migration Pi-hole data.

### 9a. 🔴 DO THIS FIRST — register Headlamp's redirect URI in Google Console

**Blocks the API-server OIDC rollout. Two minutes, and it is the one part of
Phase 4 that cannot be done from this repo.**

Google rejects any authorization request whose `redirect_uri` is not
pre-registered on the client, so without this the login fails with
`Error 400: redirect_uri_mismatch` before the cluster is ever involved.

In [Google Cloud Console](https://console.cloud.google.com/apis/credentials),
open the OAuth 2.0 Client ID
`138732748534-v50ig1jfefsmfnf910a8ns4gpj2sj120.apps.googleusercontent.com`
— the same client oauth2-proxy already uses — and under
**Authorised redirect URIs** add:

```
https://headlamp.lab.techyon.dev/oidc-callback
```

⚠️ **That exact string is OBSERVED, not guessed.** Headlamp builds its
`redirect_uri` from the *request host*, not from the `OIDC_CALLBACK_URL` env
var, so it was captured by replaying a real request with the ingress's own
Host and `X-Forwarded-Proto: https` headers. Do not retype it from memory —
Google matches redirect URIs exactly, including scheme and trailing slash.

Leave the existing `https://oauth.lab.techyon.dev/oauth2/callback` entry in
place; oauth2-proxy still needs it. A client may hold several.

### 9d. Redirect URI for **kubectl** OIDC — `http://localhost:8000`

Only needed if you use the `homelab-oidc` kubectl context (installed by
`ansible-playbook playbooks/16-kubectl-oidc.yml`). Same Google client, same
Credentials page as §9a — add a **second** authorised redirect URI:

```
http://localhost:8000
```

`http://` and no TLS is correct and is not a downgrade: Google permits plain
HTTP **only** for loopback addresses, precisely because the authorization code
never leaves your machine. kubelogin spins up a throwaway local listener on
that port to catch the callback.

⚠️ The former alternative — Google's out-of-band `urn:ietf:wg:oauth:2.0:oob`
flow, which avoided any local listener — was **shut off by Google in 2022**.
Loopback is the only remaining option, so do not go looking for a way to skip
this step.

All three redirect URIs coexist on one client:
`https://oauth.lab.techyon.dev/oauth2/callback` (oauth2-proxy),
`https://headlamp.lab.techyon.dev/oidc-callback` (Headlamp),
`http://localhost:8000` (kubectl).

---

### 9e. Cloudflare DNS records left behind by Phase 6 scratch work

`external-dns-cloudflare` runs `--policy=upsert-only`, so it **never deletes**.
Two throwaway hostnames from Phase 6 therefore still resolve even though the
objects behind them are gone. Delete these in the Cloudflare dashboard:

```
spike.lab.techyon.dev        A  192.168.32.17   (+ its a-spike TXT record)
bodytest.lab.techyon.dev     A  192.168.32.18   (+ its TXT record)
```

⚠️ `external-dns.alpha.kubernetes.io/exclude: "true"` was set on the
`bodytest` HTTPRoute specifically to avoid this, **and it did not work** — the
record was published regardless. Do not rely on that annotation for temporary
routes; assume any hostname you create is permanent until removed by hand.

Neither is harmful — both point at LAN addresses that answer with 404 once the
route is gone — but they are litter, and a stale record pointing at a Gateway
that later serves something else is a genuine footgun.

---

### 9c. Headlamp login — the `headlamp-admin` SA was DELETED 2026-08-03

**Headlamp cannot be logged into right now, and that is expected.** Removed on
purpose: it was a hand-made `cluster-admin` binding that existed in no git file
and was therefore invisible to both review and
`scripts/audit-protected-volumes.py`.

⚠️ **Headlamp's own ServiceAccount (`headlamp/headlamp`) has NO RoleBinding or
ClusterRoleBinding whatsoever** — verified, and deliberate: the chart's default
cluster-admin binding was removed when it was deployed. So Headlamp has no
authority of its own and every bit of access came from pasting a token minted
from `headlamp-admin`. Deleting that SA is therefore the whole login path, not
a piece of it.

The real fix is **API-server OIDC** (Phase 4 remainder), which removes the
token-paste step entirely. Until then, recreate it in this order when you
actually need Headlamp:

```bash
kubectl -n kube-system create serviceaccount headlamp-admin
kubectl create clusterrolebinding headlamp-admin \
  --serviceaccount=kube-system:headlamp-admin --clusterrole=cluster-admin
kubectl -n kube-system create token headlamp-admin      # paste this into Headlamp
```

Two things worth knowing before worrying about it having existed. There was
**no long-lived token Secret** — checked — so this was never a credential
sitting around waiting to leak; k8s ≥1.24 does not auto-create them, and
`create token` issues one that expires in an hour by default. And minting one
requires `kubectl` access that is already cluster-admin, so the SA granted
nothing its user did not already have. It was untidy rather than dangerous,
which is why deleting it was cheap.

**Delete it again after OIDC lands** if you recreate it in the meantime.

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

- ~~**Do not uncordon k8s-lab4 / k8s-lab5**~~ ✅ Both were repaired and
  uncordoned 2026-08-02, re-verified 2026-08-19 (see §2b).
  **The RULE stands for every future node, and it is now enforced rather than
  remembered:** `40-add-node.yml` and `45-change-node-role.yml` keep a node
  cordoned until a test PVC actually mounts on it. `Ready` is not the gate —
  k8s-lab4 sat Ready, untainted and schedulable with zero registered CSI
  drivers, and any PVC-bearing pod landing there would have hung in
  `ContainerCreating` forever.
- **A node being demoted loses its etcd snapshots** along with the rest of
  `/var/lib/rancher/k3s`. `45-change-node-role.yml` takes a fresh snapshot on a
  peer first, but there is still no off-cluster backup (see §8 and the Velero
  note in MODERNIZATION.md).
- **Never `kubectl patch` the QNAP StorageClasses.** They are chart-managed and
  `selfHeal` silently reverts imperative patches. Change them in git.
- **Backups live on the same NAS as the data.** They cover driver bugs,
  accidental deletion and bad restores. They do **not** cover the NAS failing.
