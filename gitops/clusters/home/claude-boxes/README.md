# Claude Code workspace boxes

A long-lived Claude Code box that lives in the cluster instead of on a laptop.
One directory per box, holding an `app.yaml` (the chart version) and a
`chart-values.yaml`; `bootstrap/claude-boxes.appset.yaml` renders each from the
**`claude-box` chart** (helm-compendium, `oci://ghcr.io/lanternfyre/charts`),
with release name and namespace `claude-box-<dir>`. **A second box is
`cp -r alpha bravo`, `rbac.clusterRole.create: false` in its values, plus a
namespace file in `bootstrap/namespaces/`** — nothing else changes.

The objects, the probes, the network policy and the reasoning behind each
literal now live in the chart's templates and `values.yaml`; this directory
carries only what differs per box.

For the pending native NDepend integration, use the
[disposable identity probe](../../../../docs/operate/ndepend-workspace-probe.md)
first. It leaves the running boxes and their namespace policies untouched.

To try the shared Beads runner with either engine in golf, follow
[Run one Beads pilot on golf](../../../../docs/operate/agent-box-pilot.md).
It uses a separate worktree and tmux window. The Codex pilot used full access
inside the hardened pod to work around its inner sandbox restriction and opened
[draft runbook PR #268](https://github.com/Lanternfyre/home-lab/pull/268).
The pilot left window 0 unchanged; the chart rollout below still needs review.

## The boxes

| dir | console | Remote Control name | repos | volume | auq |
|---|---|---|---|---|---|
| `alpha` | `https://operator.lab.lanternfyre.dev` | `Lanternfyre-operator` | Lanternfyre (all) | 40Gi | on |
| `bravo` | `https://spec-writer.lab.lanternfyre.dev` | `Lanternfyre-spec-writer` | Lanternfyre (all) | 20Gi | on |
| `charlie` | `https://oracle-ops.lab.lanternfyre.dev` | `Lanternfyre-oracle-ops` | Lanternfyre (all) | 20Gi | on |
| `golf` | `https://ops.lab.lanternfyre.dev` | `Lanternfyre-ops` | Lanternfyre, clean | 20Gi | on (alpha's token) |
| `hotel` | `https://implementer-1.lab.lanternfyre.dev` | `Lanternfyre-implementer-1` | placeholder, clean | 20Gi | on (bravo's token) |
| `india` | `https://implementer-2.lab.lanternfyre.dev` | `Lanternfyre-implementer-2` | placeholder, clean | 20Gi | on (charlie's token) |
| `delta` | `https://box1.lab.techyon.dev` | `Techyon-box1` | t3chy0n | 20Gi | on (alpha's token) |
| `echo` | `https://box2.lab.techyon.dev` | `Techyon-box2` | t3chy0n | 20Gi | on (bravo's token) |
| `foxtrot` | `https://box3.lab.techyon.dev` | `Techyon-box3` | t3chy0n | 20Gi | on (charlie's token) |

## Shared skills in both CLIs

`common-values.yaml` gives all nine boxes the Beadwork and Docs plugins. Claude
Code reads its managed marketplace settings at session start; a box that has not
logged in or started a new session may show no installed Claude plugins yet.
Codex reads the system marketplace and enabled plugin defaults from
`/etc/codex/config.toml`. Start a new Codex session after the chart rollout to
load the skills. The chart renders Codex's MCP entries from the same `mcp.servers`
map Claude uses, adding `auq` only where that box has its proxy sidecar.

The same common values seed a public `claude-shared-skills` working checkout in
`~/work/` on workspace startup. Seeding skips an existing directory; it never
fetches or resets work in progress. `publicRepos` does not enter the GitHub App
broker's token list. Keep the token-scoped `repos:` list for each box's own org.

The operator chose Codex `danger-full-access` and `approval_policy = "never"`
inside these boxes because the pod blocks Codex's inner bubblewrap namespace.
The pod remains non-root with all capabilities dropped, RuntimeDefault seccomp,
its existing network policy and the read-only cluster token. This removes
Codex's own file and network sandbox within that pod; use an isolated worktree
for a bead and keep review before integration.

**The Lanternfyre boxes are ROLE boxes (2026-10-25)** -- the Necronia v2 plan's
clean room (O30, `~/.claude/plans/this-is-project-we-cheerful-pizza.md`,
"Clean-room process"). Hostname and Remote Control name are the role; the
directory stays the box's permanent identity (it names the namespace and the
volume), so a role can move to another box without orphaning anything.

- **C++ side** -- may read the v1 GPL code: `operator` (alpha: the
  orchestrator, reads everything, drives the others, never runs slices),
  `spec-writer` (bravo), `oracle-ops` (charlie). They kept their volumes.
- **Clean side** -- must never see it: `ops` (golf), `implementer-1/2` (hotel,
  india). **New boxes on new, empty volumes**, never an old one, and no
  `Lanternfyre/Necronia` in `repos:`. `Lanternfyre/Necronia2.0` joins them only
  after M0/S03 removes its tainted docs, as a history-cut clone (a chart change
  still to come); until then the implementers hold a placeholder repo.
- **Only the operator messages boxes**, and to clean boxes only bead ids and
  commands, never C++ detail. Work itself is pulled from Beads by role label.
- **Every box is `updateStrategy: RollingUpdate`** (chart >= 0.1.10, operator
  2026-10-25): Argo replaces the pod as soon as a spec change syncs -- a merged
  values edit **restarts that box's session**, unannounced.

**Two groups, split by DNS domain (2026-10-22).** Lanternfyre work is on
`lanternfyre.dev`, techyon work on `techyon.dev`. A group's boxes pin
`github.installations` to their own org, so a repo from the other org fails the
render instead of quietly widening the token. The domain is only the
hostname -- the gate, the Gateway and the listener are the same for both.

What the two groups SHARE (operator, 2026-10-22): the GitHub App and its key,
the console item (one ttyd credential and SSH host key for all nine), the GHCR
pull secret, the github MCP proxy's PAT, and the auq device tokens -- each
techyon box reuses its Lanternfyre counterpart's, so unpairing that device in
auq cuts off both. What differs: repos, `github.installations`, hostname, and
the Anthropic account each box is `/login`-ed with.

`bravo` and `charlie` were added on 2026-09-13 as copies of alpha, with three
differences, each explained in their `chart-values.yaml`:

* **`rbac.clusterRole.create: false`**: alpha owns the shared ClusterRole.
* **20Gi**: enough for now. To grow one, patch the **claim**
  (`qnap-iscsi` allows expansion), not `storage.size`: a StatefulSet's
  `volumeClaimTemplates` cannot be updated, so a values edit fails the sync.
* **auq on its own device**: `Claude Box Bravo auq` / `Claude Box Charlie auq`,
  paired per box, so unpairing one leaves the others working. auq's ingress
  policy names both namespaces.

Every hostname behind the gate also needs its callback in
`apps/authentik/manifests/blueprint-envoy-gateway.external-secret.yaml`, in the
same change. box1's was added one commit late, and the page failed while
everything else stayed green.

This is the Kubernetes port of the operator's own `claude-box`
(`~/.claude/scripts/workbench/sandbox/`). That is a bubblewrap sandbox with a
host-side GitHub App token minter and a CONNECT-only egress allowlist; here the
mount/netns machinery collapses into a PodSpec and a CiliumNetworkPolicy, and
the minter becomes a sidecar.

## The Anthropic API pool for supplementary runs

Every box gets `ANTHROPIC_POOL_API_KEY`, an Anthropic Console API key with about $200 of credit (operator,
2026-10-29), from the `api-token` field of the 1Password item `Claude Workspace Console`. It is for extra headless
Claude runs, the way agents already use Codex: an independent second review, or more parallel read-only work when
the box's claude.ai limits are the bottleneck. The box's own sessions keep using the claude.ai login.

An agent opts in for one run at a time, with a spending cap:

```bash
ANTHROPIC_API_KEY="$ANTHROPIC_POOL_API_KEY" claude -p --max-budget-usd 2 "…"
```

Never export it as `ANTHROPIC_API_KEY` in a shell profile or a settings `env` block. Claude Code ranks that
variable above the claude.ai login and always uses it with `-p`, so every session on the box would bill the pool,
and Remote Control, which needs the login, would stop working. Spend is visible in the Claude Console under the
key's workspace.

## The three properties worth knowing

**One session, three doors.** ttyd in a browser, ssh, and `kubectl exec` all
exec `/usr/local/bin/attach`, which is `tmux new-session -A -s main` on one
socket. The entrypoint creates that session *detached, before any door starts* —
tmux captures its environment at server start, so letting the first ssh login
create it would give every Claude process sshd's stripped environment.

**A fourth door: Remote Control, by name.** With `remoteControl.name` set
(chart >= 0.1.8, image >= 0.1.17), window 0 starts as
`claude --name <n> --remote-control <n> --continue`. The same session is then
drivable from claude.ai/code and the Claude app, and **an orchestrator session
reaches it by name** -- `ListAgents` lists it as a `Remote Control` row, and
`SendMessage` to `Lanternfyre-box1` lands in window 0. See "Remote Control"
below for what that needs.

**No durable GitHub secret in the box.** The App PEM is mounted into the broker
sidecar only. It mints ~1h installation tokens into a memory-backed emptyDir,
scoped to the repos in `chart-values.yaml` (`repos:`), and a git credential helper
re-reads the file on every git invocation so rotation is invisible.

**Repos are values.** `repos:` in `chart-values.yaml` is the list; `seed-repos.sh`
clones what is missing at container start and **never touches an existing
checkout**. Nothing about the repos is in the image.

## Where it runs

A home agent node, scheduler's choice. No `nodeSelector`: the volume is
`qnap-iscsi`, so it follows the pod, and pinning would only create a way for a
drain to strand it. The anti-control-plane affinity is not about capacity --
`k8s-lab1/2/3` carry no taints, and this box clones ~1 GiB of repositories and
runs builds, which on a server node would put that I/O on the same filesystem
as the etcd WAL.

### It used to run on the edge node, and that cost two exceptions

Both are now closed, and the trace is kept rather than deleted:

* **Storage.** `k8s-edge1` has zero registered CSI drivers by design --
  `no-storage-on-edge.validatingpolicy.yaml` keeps the QNAP node plugin off a
  public machine -- so `local-path` was the only class available and
  `reclaimPolicy: Delete` violated CLAUDE.md's Retain invariant. The claim sat
  in `unprotected:` as a written-down exception. On a home node it is
  `qnap-iscsi`, Retain, and lives in `protected:` like everything else.
* **A firewall bug nobody had hit.** The edge's `edge_filter` INPUT chain had
  no rule for the CNI interfaces, so any pod traffic terminating on the host --
  which is what an L7 `rules.dns` redirect is, and therefore every `toFQDNs`
  policy -- was dropped silently. Fixed in #182; it applied to anything ever
  scheduled there, not just this box.

⚠️ **Moving back is not a `nodeSelector` edit.** The volume is the constraint,
not the scheduler: see "Recovery" below.

## The console

`https://box1.lab.lanternfyre.dev` (and its siblings), on the **gated** Gateway.

🔴 That choice is the security decision for the whole feature. ttyd is an
interactive shell next to an Anthropic OAuth credential, a GitHub token with
`contents:write` on seven repositories, and a read-only cluster token. On the
plain Gateway that is a root-equivalent shell for anything reaching the LAN or
the VPN. ttyd keeps its own `-c user:password` underneath regardless -- that is
what still holds if the route is ever attached to the wrong Gateway, which
would otherwise present as a working page rather than an error.

It appears on the homepage dashboard under **Agents**, from the
`gethomepage.dev/*` annotations on the HTTPRoute. There is no central layout to
register a group in: the section exists because the annotation says so, and
dropping the annotations makes it vanish silently.

## Before it can start

Two 1Password items in the `Infrastructure` vault:

| item | fields |
|---|---|
| `Claude Workspace Console` | `ttyd-credential` (literal `user:password`), `ssh-authorized-keys`, `ssh-host-ed25519-key` |
| `Claude Box GitHub App` | `app-id` (4043649), `private-key` (PEM, **with** trailing newline), `installation-lanternfyre` (140425201), `installation-t3chy0n` (140425220) |

⚠️ Not `ARC-GitHub-App` — that is the CI runners' App.

⚠️ No ExternalSecret in this repo has pulled a multi-line value through
1Password Connect before. Prove the PEM survives before assuming the broker is
merely misconfigured:

```sh
kubectl -n claude-box-alpha get secret gh-app -o jsonpath='{.data.private-key}' \
  | base64 -d | openssl rsa -check -noout      # must print "RSA key ok"
```

## First run

```sh
kubectl exec -it -n claude-box-alpha claude-box-alpha-0 -- attach
```

Window 0 already runs Claude -- `--continue`, which resumes the most recent
conversation **in the current directory** (window 0 starts in
`/home/agent/work`; each repo keeps its own history). On a box with no login
yet, run `/login`: the browser shows a CODE to paste back, with no loopback
callback, which is why a TTY in a pod is sufficient.

Then prove the feature actually works -- 30 seconds, and it is the acceptance
test:

1. in the session: `echo hello-from-exec`
2. open the box's console URL -> the **same** scrollback
3. ssh in -> the same again

Three different scrollbacks means `AGENT_TMUX_SOCKET` is not reaching one of
the doors.

## Remote Control -- each box answers to its name

The names are in the table above: brand from the console's domain, number from
its hostname, so `Techyon-box2` is `box2.lab.techyon.dev`. Letters, digits and
`-` only, so an `@Techyon-box2` mention needs no quotes (the chart fails the
render otherwise).

Mechanics, measured on 2.1.268 (2026-10-23) before any of this shipped:

* **Only window 0 carries the name.** claude-session passes it; the shim and the
  managed settings do not. A second window or a `kubectl exec -- claude` must not
  answer to the box's name, or senders fall back to disambiguating by id. A
  hand-typed `/rc` in another window is titled `<name>-<adjective>-<noun>`,
  lowercased (`lanternfyre-box1-...`) -- Claude Code slugs the prefix.
* **A restart keeps the address.** `--continue` reattaches to the Remote
  Control session the conversation recorded -- same `session_` URL after a pod
  delete.
* **Network: nothing new.** Remote Control is outbound HTTPS to
  `api.anthropic.com`, which the chart's policy already admits.

### What it needs, per box

1. **A claude.ai login** (`/login`). Before it the name is inert and the box
   onboards exactly as before; after it, Remote Control connects on the next
   start, or at once with `/rc`. On the very first `/rc` Claude asks once to
   enable Remote Control -- answer it at the console.
2. **The SAME Anthropic account as the orchestrator.** Remote Control sessions
   are visible only to the account that logged the box in, and cross-machine
   `ListAgents` reads that account's list. alpha, bravo and charlie are on the
   operator's personal account (checked 2026-10-23); delta, echo and foxtrot
   have no login yet. A techyon box logged into a different account is
   invisible to an orchestrator on the first one.
3. **A pod restart**, once the pins move: chart >= `0.1.8` in `app.yaml`, image
   >= `0.1.17` in `chart-values.yaml`. Since 0.1.10 every box is
   `updateStrategy: RollingUpdate`, so Argo does it by itself when the change
   syncs -- no manual delete.
   To get a running box online now without losing its session, type
   `/rc Lanternfyre-box1` (its own name) in window 0; `--continue` reattaches
   to that same remote session at the next restart.
4. **Check window 0 after the delete.** alpha, bravo and charlie used `/rc` by
   hand before, so their conversations carry an OLD recorded remote session,
   and `--continue` reattaches to that recording rather than making a new one.
   What was measured is a fresh recording; an old or archived one was not.
   If window 0 shows `Previous session is unavailable`, or the old title
   instead of the box's name, type `/rc <its name>` once.

### ⚠️ Delivery into a bypass box is NOT automatic

Every box runs `bypassPermissions`. With `crossSessionInbound` unset, Claude
Code then **holds** each inbound message from a sender that is not also in
bypass mode, behind an approval dialog in window 0 -- and drops it after five
minutes when nobody answers, which on an unattended box is always. So:

* an orchestrator running in `bypassPermissions` messages the boxes as-is;
* an orchestrator in `default`, `acceptEdits` or `auto` sees them in
  `ListAgents` but its messages never arrive.

Lifting that is `"crossSessionInbound": "accept"` in claude-workspace's
`managed-settings.json` -- an **open operator decision**, not done: it lets any
session on the account put a prompt into a box holding a GitHub write token.

### Acceptance check

From a session connected to Remote Control (`/rc`) on the same account:

1. `/list-agents` shows `Lanternfyre-box1` as a `Remote Control` row, not
   `offline`.
2. Ask it to message `@Lanternfyre-box1` "reply with your hostname" -- the answer
   is `claude-box-alpha-0`, and the exchange is visible at the box's console.

## Recovery, and moving the box

The volume is the constraint. `qnap-iscsi` detaches and re-attaches wherever
the pod lands, so a node drain is now a non-event -- but a move BETWEEN storage
classes still means a new volume, and the login lives on the old one.

```sh
# push anything unpushed FIRST -- it is the only thing on the volume that is
# not reconstructible
kubectl exec -n claude-box-alpha claude-box-alpha-0 -- \
  bash -lc 'for d in ~/work/*/; do git -C "$d" status --porcelain; done'

kubectl -n claude-box-alpha delete pod claude-box-alpha-0   # deliberate: the session ends here
kubectl -n claude-box-alpha delete pvc home-claude-box-alpha-0
# edit the storage class / placement, commit, let Argo sync, then re-login
```

What comes back by itself: the checkouts (seed-repos), `~/.claude` skills and
commands, the npm and uv caches. What does not: the Anthropic login (~2 min to
redo) and anything unpushed.

## Gates still open

| what | why it is not settled |
|---|---|
| ttyd through the gated Gateway's oauth2 filter | ttyd is a websocket, and the filter has to authenticate the upgrade GET then get out of the way. This estate has also seen oauth2 cookies exceed Envoy's 60 KB header limit on gated apps -- it surfaces as `ERR_HTTP2_PROTOCOL_ERROR` or a 431, not a login failure. Fallback is `homelab` plus the ttyd credential, written down rather than silently applied |
| ssh reachability | the VPN routes only the two Gateway `/32`s, so a ClusterIP is not reachable. Either a TCP listener on `homelab` (LAN-wide too) or a pinned ClusterIP plus a NetBird route -- three coupled files |

## Deliberately not done

**Do not port claude-box's `ghu_` device-flow refresh path.** GitHub rotates
the `ghr_` refresh token on every use, so two refreshers — or one restart
racing an in-flight refresh — brick the credential permanently. The
installation mint is stateless and safe to repeat; that is why it is the half
that was ported.

**Do not label this namespace `edge-publish`.** It looks like the right label
for a workload on the edge node and is the opposite: it forbids
`automountServiceAccountToken`, which kills the read-only cluster token, and
requires `readOnlyRootFilesystem`, which a development box cannot satisfy.
