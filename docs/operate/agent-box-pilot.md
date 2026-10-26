# Run one Beads pilot on golf

For the operator: run an approved bead with Claude or Codex in a separate worktree
and tmux window, while golf's Remote Control session stays in window 0. The
[project helper](../../scripts/agent-pilot.py) calls the shared beadwork runner;
it does not copy its workflow or choose credentials, model access or permissions.
The [shared runtime guide](https://github.com/Lanternfyre/claude-shared-skills/blob/feat/shared-worker-sessions/plugins/beadwork/runtime.md#conversations-and-recovery)
defines its session and recovery behavior; this link uses the workflow branch
until that change is merged.

**Prepared, not deployed.** The values change admits only `auth.openai.com` and
`chatgpt.com` on HTTPS for golf. Chart `0.1.11` and image `0.1.18` stay pinned.
Offline rendering of both values with chart `0.1.11` changed only the Cilium
policy; the StatefulSet was identical. No live
model pilot has been verified; track its outcome and operator actions in the
pilot bead rather than this page.

## Check the runtime first

Read-only checks on golf with image `0.1.18` measured Claude `2.1.268`, Codex
`0.155.0`, Beads `1.3.1` and tmux `3.3a`. Codex supports native plugins,
`exec`, exact-session resume and final-message output. Beads supports guarded
updates and unclaims. The existing Claude window showed an input prompt; that
is a snapshot, so check ownership again before starting work.

**Codex is blocked at these pins by its workspace sandbox.** This model-free
check reproduces the failure:

```sh
codex -c 'sandbox_mode="workspace-write"' sandbox -- /bin/true
```

It exits 1 because bubblewrap cannot create a namespace in the current pod.
`features.use_legacy_landlock=true` is not a verified workaround: with
`workspace-write` or the named `:workspace` profile it panics, reporting that
direct runtime enforcement is incompatible with legacy Landlock. A bare
Landlock no-op succeeding does not verify the workspace restrictions. Resolve
this under an explicit sandbox/runtime decision before a Codex run; the helper
stops on it. Do not disable sandboxing or change pod privileges to pass the check.

Both engines use the same runner and bounded work contract. Start with Claude
if its prerequisites pass; switching to Codex requires its own completed checks.

## Prepare once, outside the running agent

Golf remains a [clean-side role box](../../gitops/clusters/home/claude-boxes/README.md#the-boxes):
no v1 or tainted checkouts, transcripts or memories. Keep
`BEADWORK_NO_MEMORIES=1`; the helper sets it for every run. Do not reuse another
box's volume or credential cache. `AGENTS.md` links to the existing `CLAUDE.md`
so both hosts get one source of project instructions.

1. **Codex only:** let the reviewed golf-only network policy sync through GitOps.
   Claude does not require these OpenAI endpoints. Do not patch
   it live. Before and after, check that window 0 and the pod remain in place.
   An unauthenticated `curl --connect-timeout 3 --max-time 5 -o /dev/null
   https://auth.openai.com/` (and `https://chatgpt.com/`) should establish a
   connection; an HTTP rejection alone does not mean networking failed.
2. Update/install the shared plugins in a new session, leaving window 0 alone.
   Golf's measured managed Claude cache was beadwork `0.4.0` / docs `0.3.1`,
   which predates `scripts/run.py`. Use an approved shared-skills revision that
   contains the runner. Supply its exact `run.py` path to the helper below.
   For Codex's native package registration, run:

   ```sh
   codex plugin marketplace add https://github.com/Lanternfyre/claude-shared-skills.git
   codex plugin add beadwork@claude-shared-skills
   codex plugin add docs@claude-shared-skills
   ```

   Review bundled hooks through normal Codex trust. The runner can read the
   installed startup script explicitly if its context is absent; no bypass is needed.
3. Check `claude auth status` or `codex login status`. If Codex needs login,
   the operator runs `codex login --device-auth` at the console and completes
   the browser step. This is the documented [headless login flow](https://learn.chatgpt.com/docs/auth#login-on-headless-devices).
   Never copy login files or tokens between boxes.
4. Claude's chart-managed MCP configuration is supplied by its CLI shim. Keep
   the normal pod PATH: `command -v claude` should resolve to
   `/usr/local/sbin/claude`. A login shell such as `bash -lc` can reset PATH
   and select the raw binary, omitting the chart-managed tool configuration.
   Invoke the helper directly through `kubectl exec … -- python3 …` or from a
   shell with the normal pod PATH.
   Codex does not inherit that JSON file: explicitly configure any required
   MCP servers in its own configuration. The existing `auq-mcp` executable uses
   the local proxy; it needs no copied bearer token. `codex mcp add auq -- auq-mcp`
   is an operator configuration action, not something the helper does.
   Confirm required tools before an unattended run; do not send a test question.
   Codex shell commands also need authorized network access to remote Beads,
   within the existing Cilium policy. Make that policy choice explicit.

## Create a separate project worktree

Use the approved merged project revision. The original golf checkout predates
the committed `.beads/metadata.json`; fetch it into a new worktree rather than
changing the branch under window 0. The metadata attaches to the existing
`home_lab` database; do not initialize a replacement database.

```sh
git -C /home/agent/work/home-lab fetch origin master
git -C /home/agent/work/home-lab worktree add -b pilot/beadwork \
  /home/agent/work/home-lab-pilot origin/master
cd /home/agent/work/home-lab-pilot
bd --readonly ping
bd --readonly list --status in_progress --json
```

If the branch/worktree already exists, inspect and resume it; do not reset it.
Use only the exact authorized non-human bead. The shared runner generates a
unique worker actor instead of sharing the box-wide `Lanternfyre-ops` claim.
Its checkpoint, ownership checks and fixed deadline survive session changes.

## Check, then start the pilot

Replace the runner path and bead id below with real approved values. Choose a
model with the runner's `--model` option only when needed; otherwise retain the
selected host's configured default. Roles are optional. Keep the first pilot
to one bead and an explicit 30-minute budget.

```sh
python3 scripts/agent-pilot.py --check claude "$PWD" /absolute/path/to/run.py -- \
  --bead <pilot-bead-id> --max-beads 1 --time-budget 30m --mode attended

python3 scripts/agent-pilot.py claude "$PWD" /absolute/path/to/run.py -- \
  --bead <pilot-bead-id> --max-beads 1 --time-budget 30m --mode attended
```

`--check` runs only prerequisites and the shared runner's read-only dry-run.
The second command creates a detached `beadwork-claude-…` window in `main` and
prints its name and private log path under `~/.local/state/beadwork/pilot/`.
It leaves a shell there after the runner exits. It never touches window 0 or
registers another process under the box's Remote Control name.
New windows inherit the pod's existing Beads/GitHub environment from tmux;
the helper forwards only named workflow settings and CLI configuration paths.
It does not copy credentials into command arguments or log them.

After Codex prerequisites pass, replace `claude` with `codex`. For unattended
work, change only `--mode attended` to `--mode unattended`; questions remain
queued, and unattended action boundaries still apply. The agent may keep,
resume or replace its session within the same fixed run budget. Do not start a
second engine on the same worktree. Recovery and any new budget are explicit
shared-runner actions, described below.

Record the resulting commit/PR, acceptance evidence, run log and remaining
integration state in the bead.

## Stop, inspect and resume the same run

To interrupt only the pilot, replace `<printed-window>` with the helper's window
name and send Ctrl-C there:

```sh
tmux -S /run/agent/tmux.sock send-keys -t 'main:<printed-window>' C-c
```

Wait for the runner to exit. Inspect that pilot's log, its diff and bead notes;
confirm its model process and delegates have stopped, and reconcile the exact
claim with the saved actor before recovery. From the same isolated worktree:

```sh
cd /home/agent/work/home-lab-pilot
git diff
bd --readonly show <pilot-bead-id>
cat "$(git rev-parse --git-path beadwork/run.json)"
```

Do not delete the checkpoint, release another worker's claim, kill the `main`
tmux session or restart the pod as cleanup. If ownership or stopped writers
cannot be established, preserve the state and record what remains unresolved.

Use the saved engine, exact bead, worktree and runner source for all three
commands below. Keep any original model, role, labels and availability settings.
Each helper invocation opens a new pilot window; only one runner may own this
worktree at a time.

```sh
# Clean pause: resume its native conversation and original remaining budget.
python3 scripts/agent-pilot.py claude "$PWD" /absolute/path/to/run.py -- \
  --bead <pilot-bead-id> --max-beads 1 --time-budget 30m --mode attended

# Interrupted run: after inspecting and reconciling writers, state and ownership.
python3 scripts/agent-pilot.py claude "$PWD" /absolute/path/to/run.py -- \
  --bead <pilot-bead-id> --max-beads 1 --time-budget 30m --mode attended --recover

# Expired budget: only when another 30 minutes has been explicitly authorized.
python3 scripts/agent-pilot.py claude "$PWD" /absolute/path/to/run.py -- \
  --bead <pilot-bead-id> --max-beads 1 --time-budget 30m --mode attended --new-budget
```

The first two commands retain the saved deadline despite the repeated
`--time-budget` argument. `--new-budget` performs recovery checks and explicitly
sets a new deadline; it does not waive ownership or acceptance checks. For a
saved Codex run use `codex` in the same commands once its prerequisites pass.
