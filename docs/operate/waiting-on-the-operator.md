# Waiting on the operator

The steps only you can do, kept here instead of in beads (retired 2026-11-17): credentials, sudo, a console, a judgement call. Each entry carries its exact commands and checks. Add an entry when a change leaves you a step; delete it when done. Facts that stay true belong on [Standing warnings](standing-warnings.md), not here.

## Open

### Close the LAN-exposed hostNetwork metrics ports (node-exporter, metallb-speaker, cilium-agent)

*home-lab-x9j · P2 · opened 2026-10-25 · topic:security*

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

**Status check, 2026-10-25** (read-only, by the migration session): From the operator workstation on the LAN, `k8s-lab2` answers HTTP 200 on `:9100/metrics` (node-exporter) and `:7472/metrics` (metallb). `:9879` did not answer.

**Done when:**
- From a LAN host outside the cluster, `curl -m 3 http://<node-ip>:9100/metrics`, `:7472` and `:7473` fail to connect on every node; or the operator records a decision to accept the exposure.

### VoIP: Asterisk on the edge (Halonet to ElevenLabs), three steps before it works

*home-lab-20a · P2 · opened 2026-10-25 · topic:voice*

**🟡 VoIP: Asterisk on the edge (Halonet ⇄ ElevenLabs) — three steps before it works (2026-09-30)**

Asterisk runs `hostNetwork` on `k8s-edge1` and bridges the Halonet number
(SIP/UDP only) to ElevenLabs Agents (SIP/TLS). See `EDGE-NODE.md` → "Asterisk".
Everything is in git except these steps:

**1. Create the 1Password item — vault `Infrastructure`, item `voip-asterisk`**

| field | value |
|---|---|
| `halonet_registrar` | the SIP domain/server Linphone uses, no `sip:` (e.g. `sip.halonet.pl`) |
| `halonet_username` | Linphone "Username" |
| `halonet_auth_username` | Linphone "Auth ID"/"User ID"; **repeat the username** if there is none |
| `halonet_password` | SIP password |
| `halonet_number` | the number in E.164 (`+48…`). Must equal the number imported into ElevenLabs |
| `elevenlabs_sip_username` | invent one, e.g. `el-trunk` |
| `elevenlabs_sip_password` | long random string |

⚠️ **Log Linphone out of the Halonet account first.** Two registrations on one
account split or steal the incoming calls.

**2. Apply the edge firewall (sudo)**

```bash
cd ansible && ansible-playbook playbooks/61-edge-firewall.yml --ask-become-pass
```

This opens 5061/tcp and 20000-20099/udp and adds the uid-20060 output
confinement. **Run it before or together with the merge.** Without it Asterisk
registers but no audio arrives and ElevenLabs cannot reach 5061.

**3. ElevenLabs dashboard: Phone numbers → Import → SIP trunk**

* Phone number: the same E.164 value as `halonet_number`
* Inbound (ElevenLabs receives calls): digest auth, username and password from step 1
* Outbound (the agent calls out): address `edge-1.techyon.dev:5061`, transport **TLS**,
  digest auth with the same username and password, media encryption **Disabled** or **Allowed**
* Assign the agent to the number

Verify: `kubectl -n voice exec deploy/asterisk -- asterisk -rx 'pjsip show registrations'`
→ `Registered`, and `... -rx 'pjsip show contacts'` → the halonet contact `Avail`
with an RTT. Then call the number from a mobile phone. If either check or the
call fails, use the decision table in `EDGE-NODE.md` → "Asterisk".

**Status check, 2026-10-25** (read-only, by the migration session): `pjsip show registrations` reads `halonet-reg ... Registered`, so steps 1 and 2 are at least partly done. The ElevenLabs trunk (step 3) and the test call were not checked.

**Done when:**
- `kubectl -n voice exec deploy/asterisk -- asterisk -rx 'pjsip show registrations'` reads `Registered`.
- `kubectl -n voice exec deploy/asterisk -- asterisk -rx 'pjsip show contacts'` shows the halonet contact `Avail` with an RTT.
- A call to the number from a mobile phone reaches the ElevenLabs agent.

### Retire or adopt the old pihole PVC (deferred by decision 2026-08-03)

*home-lab-c48 · P2 · opened 2026-10-25 · topic:storage*

**9b. DEFERRED BY DECISION 2026-08-03 — retire the old `pihole` PVC**

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

**Status check, 2026-10-25** (read-only, by the migration session): The PVC `dns/pihole` (local-path, `pvc-c9212135-243e-4c78-8367-5df27209b626`) is still Bound.

**Done when:**
- Either a `pihole-old.pvc.yaml` is declared in `gitops/clusters/home/apps/pihole/manifests/` and both pihole Applications read Synced, or the PVC, the PV and the directory on k8s-lab3 are all gone.

### auq: the two things only the operator can do (mobile dev build, push)

*home-lab-pia · P2 · opened 2026-10-25 · topic:auq*

**Two more things only you can do:**

1. **The mobile app's server URL** — `expo.extra.AUQ_SERVER_URL` needs a custom
   dev build. Expo Go cannot handle the `auq://` deep links.
2. **Push is broken upstream, and it is not this deployment's fault** — nothing
   calls `updatePushToken` and `extra.eas.projectId` is missing. Fixing it
   needs `eas init`, which is yours. Tracked as claude-workbench #1098 / #1139.
   Questions still arrive; only the notification does not.

**Status check, 2026-10-25** (read-only, by the migration session): claude-workbench #1098 and #1139 are both closed upstream. Whether `eas init` and the custom dev build were done was not checked.

**Done when:**
- A dev build of the mobile app talks to `https://auq.lab.techyon.dev`, and a new question pushes a notification to the phone.

### Settle contradicting and stale docs (backups first)

*home-lab-6cz · P2 · opened 2026-10-25 · topic:docs*

The docs pilot walkthrough (2026-10-25) found docs that contradict each other or are out of date. Settle each one against the live cluster, or ask the operator; never edit one source to match another without evidence.
1. **Backups (critical).**
   - MANUAL-STEPS.md "Restore procedures" describes nightly backups with 14-day retention.
   - Its "Standing warnings" and MODERNIZATION.md ("Backups: still none at all") say there are none.
   - CLUSTER-TOKEN.md says "no other backups", while MANUAL-STEPS lists etcd snapshots twice a day.

   The README marks the restore link Unverified until this is settled.
2. **Node count.** Several pages still say five nodes; the cluster has eight.
3. **MANUAL-STEPS §9a** says "DO THIS FIRST", but MODERNIZATION says it is done.
4. **CLUSTER-TOKEN.md is partly stale.**
   - "Target state" still suggests a separate agent token, which is already done.
   - "Afterwards" asks to fix argo-install.md and MANUAL-STEPS §8, both done.
   - Phase 5b is missing from the six-phase table.
5. **Token rotation versus join hardening.**
   - CLUSTER-JOIN-HARDENING calls a join test "the real verification"; the rotation runbook has none.
   - Join hardening says to re-key the agents; the runbook says agents are not restarted.
6. **Status freshness.** MODERNIZATION.md was last updated 2026-09-07, while other docs are from October.

**Done when:**
- Each item is resolved (doc corrected, with evidence noted) or recorded as an open question bead.
- The README's restore entry loses its Unverified note only once the backups item is settled.
- `docs.py check` passes.

**Notes:**
- F
- o
- u
- n
- d
- w
- h
- i
- l
- e
- m
- i
- g
- r
- a
- t
- i

*(The full list of contradictions, about 1,800 lines, is in the beads export of 2026-11-17 under `home-lab-6cz`; this entry keeps its first item, the backups, which decides the rest.)*

### Sudo on the nodes: decide on NOPASSWD for unattended Ansible

*home-lab-ou4 · P3 · opened 2026-10-25 · topic:ansible*

**10. Sudo on the nodes**
Ansible will run with `--ask-become-pass`, which is fine. If you would rather it
run unattended, configure `NOPASSWD` for `tarjei` on all five nodes — a standing
credential on every control-plane node, so your call:

```bash
**on each of k8s-1.home … k8s-5.home**
echo 'tarjei ALL=(ALL) NOPASSWD:ALL' | sudo tee /etc/sudoers.d/50-ansible
sudo visudo -c
```

**Done when:**
- The operator records the decision in this bead. If NOPASSWD: `ssh <node> sudo -n true` succeeds on every node.

## To decide

The Claude boxes were driven by beads (role boxes). With beads gone these three are about the boxes themselves: decide whether the boxes stay as plain workspaces, and only then do them.

### Claude boxes on Remote Control: log in delta, echo and foxtrot, and decide crossSessionInbound

*home-lab-07v · P2 · opened 2026-10-25 · topic:claude-boxes*

**🟡 Claude boxes on Remote Control, by name (2026-10-23)**

Each box's window 0 registers as a Remote Control session named after the box
(`Lanternfyre-box1..3`, `Techyon-box1..3`), so an orchestrator session can reach
it with `ListAgents` / `SendMessage`. Detail and the acceptance check:
`gitops/clusters/home/claude-boxes/README.md`, "Remote Control".

1. ~~**Merge, in order:** docker-compendium #29 (image `claude-workspace` 0.1.17),
   then helm-compendium #42 (chart `claude-box` 0.1.8).~~ ✅ DONE 2026-10-23 --
   both NEW tags verified (`claude-workspace-v0.1.17`, `claude-box-v0.1.8`),
   the chart pulled from ghcr.io/lanternfyre/charts and the image manifest
   present on GHCR.
2. ~~**Move the pins** in all six boxes~~ ✅ DONE 2026-10-23 -- chart 0.1.8,
   image 0.1.17. ⚠️ Argo syncing this changes the StatefulSet, NOT the pod:
   `OnDelete` means nothing reaches a running box until step 4.
3. **`/login` delta, echo and foxtrot** -- on the SAME Anthropic account the
   orchestrator uses, or it cannot see them. Answer the one-time "Enable
   Remote Control" prompt at the console on each box's first `/rc`.
4. **Delete each pod when it is idle** (`OnDelete`; costs the tmux session,
   `--continue` brings the conversation back):
   `kubectl -n claude-box-<dir> delete pod claude-box-<dir>-0`.
   To bring a running box online without a restart: `/rc <its name>` in
   window 0. After each delete, look for `/remote-control is active` and the
   box's name in window 0: alpha/bravo/charlie carry an old recorded remote
   session from earlier hand-typed `/rc`, and on `Previous session is
   unavailable` (or the old title) the fix is one `/rc <its name>`.
5. **Decide `crossSessionInbound`.** The boxes run `bypassPermissions`, so a
   message from an orchestrator that is NOT in bypass mode is held in window 0
   and dropped after five minutes. Either run the orchestrator in
   `bypassPermissions`, or add `"crossSessionInbound": "accept"` to
   claude-workspace's `managed-settings.json` -- which lets any session on the
   account prompt a box holding a GitHub write token. Not done: yours to weigh.

**Status check, 2026-10-25** (read-only, by the migration session): Every box pod restarted on 2026-10-25 around 05:38 UTC (RollingUpdate since the role-box PR), so step 4's pod delete has happened. Whether window 0 shows `/remote-control is active` was not checked. Steps 3 and 5 are open.

**Done when:**
- delta, echo and foxtrot appear in ListAgents from an orchestrator session on the same account.
- The crossSessionInbound decision is recorded in this bead.

### Lanternfyre role boxes: log Claude in on golf, hotel and india, and check window 0

*home-lab-139 · P2 · opened 2026-10-25 · topic:claude-boxes*

**🟡 Lanternfyre role boxes -- operator, spec-writer, oracle-ops, ops, implementers (2026-10-25)**

The Necronia v2 plan's per-role boxes (O30). Layout, clean-room sides and why:
`gitops/clusters/home/claude-boxes/README.md`, "The Lanternfyre boxes are ROLE
boxes". alpha/bravo/charlie are renamed (`operator`, `spec-writer`,
`oracle-ops`); golf/hotel/india are new (`ops`, `implementer-1`,
`implementer-2`).

1. **Merge helm-compendium #46 first** (claude-box `updateStrategy`; expect a
   NEW tag `claude-box-v0.1.10`), then the home-lab role-box PR.
2. 🔴 **That merge restarts all nine boxes at once** (RollingUpdate, plus a
   changed pod spec on each). Push anything unpushed in alpha/bravo/charlie
   first, and expect their running sessions to end.
3. **Log Claude in on each NEW box**, once -- the login lives on the volume, and
   golf/hotel/india start empty:
   ```sh
   kubectl exec -it -n claude-box-golf  claude-box-golf-0  -- attach   # then /login
   kubectl exec -it -n claude-box-hotel claude-box-hotel-0 -- attach
   kubectl exec -it -n claude-box-india claude-box-india-0 -- attach
   ```
   Use the SAME Anthropic account as the operator box, or ListAgents cannot see
   them. On the first `/rc`, answer Claude's one-time Remote Control prompt.
4. **Check window 0 on alpha/bravo/charlie after their restart.** Their
   conversations carry a remote session recorded under the OLD name
   (`Lanternfyre-box1..3`), and `--continue` reattaches to that. If window 0
   shows the old title or `Previous session is unavailable`, type
   `/rc Lanternfyre-<role>` once.
5. **Not yet:** Necronia2.0 on the clean boxes (after M0/S03, as a history-cut
   clone -- a claude-box chart change), per-box CPU/memory limits, read-only
   mounts and per-role `.claude/roles/*.json` (plan S01 (8) / S04).

**Status check, 2026-10-25** (read-only, by the migration session): Steps 1 and 2 have happened: all nine box pods restarted on 2026-10-25 around 05:38 UTC. Steps 3 and 4 were not checked. Step 5 is plan work (Necronia v2 plan S01/S04), not an operator step.

**Done when:**
- golf, hotel and india are logged in, and window 0 on each reads `/remote-control is active` under its `Lanternfyre-<role>` name.
- Window 0 on alpha, bravo and charlie shows the new `Lanternfyre-<role>` name.

### box2 and box3: first Claude login and the three-doors check

*home-lab-slc · P2 · opened 2026-10-25 · topic:claude-boxes*

**🟡 box2 and box3 — first login, and auq when wanted (2026-09-13)**

`claude-boxes/bravo` (now `box2.lab.lanternfyre.dev`) and `claude-boxes/charlie`
(now `box3.lab.lanternfyre.dev`) need no new 1Password items to start: they reuse
alpha's `Claude Workspace Console` and `Claude Box GitHub App`. That means the
**same ttyd credential and the same ssh host key** as box1. What is left is per
box:

1. **Log in to Claude**, once each. The login lives on that box's volume:
   ```sh
   kubectl exec -it -n claude-box-bravo claude-box-bravo-0 -- attach    # then /login
   kubectl exec -it -n claude-box-charlie claude-box-charlie-0 -- attach
   ```
2. **Run the three-doors check** from `claude-boxes/README.md` ("First run")
   against `box2` and `box3`. The browser door also proves Authentik accepts the
   new callbacks.
3. **auq is on, per box.** Devices `claude-box-bravo` and `claude-box-charlie`
   are paired and stored as `Claude Box Bravo auq` / `Claude Box Charlie auq`
   (property `credential`), done 2026-09-13. The `auq-proxy` sidecar only
   appears after the pod is recreated (`OnDelete`):
   `kubectl -n claude-box-bravo delete pod claude-box-bravo-0`, same for charlie.
   Renaming or deleting either item leaves that box stuck in
   `ContainerCreating` on its next start.

**Status check, 2026-10-25** (read-only, by the migration session): bravo and charlie became the role boxes `spec-writer` and `oracle-ops` on 2026-10-25, and every box pod restarted that morning (about 05:38 UTC), so step 3's pod recreate has happened. Steps 1 and 2 were not checked.

**Done when:**
- The three-doors check from `gitops/clusters/home/claude-boxes/README.md` ("First run") passes for both boxes.

## Follow-ups from the assets move

### A second git-annex remote for the NecroniaAssets objects

*opened 2026-11-17 · topic:silo*

Silo's `necronia-assets` bucket is the only server-side copy of what authors push between releases ([NecroniaAssets on Silo](necronia-assets-on-silo.md)). The already-credentialed Wasabi bucket can be a second git-annex remote (`git annex initremote wasabi type=S3 …` in the assets repo), copied by the weekly prune job after it drops the unused objects. Until then an unreleased push exists in Silo and in its author's clone.

## Closed by the beads retirement (2026-11-17)

Exported, not carried; why each no longer applies:

- home-lab-2am, *Register Headlamp's redirect URI in Google Console*: Google-era gate; authentik replaced it (the redirect URI lives in the authentik blueprint).
- home-lab-ca3, *Confirm the Workspace alias domains for the hd claim*: the gate authorises on authentik groups now, not on Google's hd claim.
- home-lab-uiq, *Silo: the 1Password item, the GitHub org secrets and the redirect URIs from before its first sync*: Silo's first sync happened on 2026-08-27; the item, secrets and redirect URIs exist.
- home-lab-yf2, *Hand the Silo drop key to the 2 GB uploader, collect the files, revoke*: the drop path was retired on 2026-11-17; the file it waited for is runtime.cwm version 1, already in the assets history.
- home-lab-av6, *Role card for ops, and the ops box's role*: role cards belonged to the beads-driven role boxes, retired with beads.
- home-lab-vgu, *Register the kubectl OIDC redirect URI http://localhost:8000*: http://localhost:8000 is already a redirect URI on the kubernetes authentik client.
- home-lab-8ww, *ARC: delete the four dead org variables CI_RUNNER_**: done: the four CI_RUNNER_* org variables were already gone on 2026-11-17.
- home-lab-3d6, *Delete the Cloudflare DNS records left by Phase 6 (spike.lab, bodytest.lab)*: done on 2026-11-17 together with the drop and beads DNS records (Cloudflare, by hand).
- home-lab-ebk, *Immich library: no second copy (deferred by decision, risk accepted)*: a standing fact, not a chore: moved to Standing warnings.
- home-lab-984, *Glossary for terms the docs use undefined*: closed with the glossary edits of the beads retirement.

The full export (every issue of every project, with notes) is in the operator's archive of 2026-11-17, outside this repository.
