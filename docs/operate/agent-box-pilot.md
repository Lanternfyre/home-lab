# Run one Beads pilot on golf

For the operator: run an approved bead with Claude or Codex in a separate worktree
and tmux window, while golf's Remote Control session stays in window 0. The
[project helper](../../scripts/agent-pilot.py) calls the shared beadwork runner;
it does not copy its workflow or choose credentials, model access or permissions.
The [shared runtime guide](https://github.com/Lanternfyre/claude-shared-skills/blob/main/plugins/beadwork/runtime.md#conversations-and-recovery)
defines its session and recovery behavior.

**Rollout pending:** the shared plugin checkout, Codex system settings and MCPs,
and newer CLI pins are in [draft homelab PR #267](https://github.com/Lanternfyre/home-lab/pull/267).
Its image and chart changes restart the boxes when synced. An earlier Codex
pilot used a temporary CLI in golf, separate from the shared runner, and opened
[draft runbook PR #268](https://github.com/Lanternfyre/home-lab/pull/268).
It proved skill discovery and a fresh-session bead checkpoint; it did not prove
the full runner launch path. Do not treat the draft rollout as live configuration.

## Check the runtime first

The current pod blocks the namespace creation used by Codex's `workspace-write`
sandbox. This model-free check reproduces the failure:

```sh
codex -c 'sandbox_mode="workspace-write"' sandbox -- /bin/true
```

It exits 1 because bubblewrap cannot create a namespace. The operator approved
`danger-full-access` and `approval_policy = "never"` **inside these hardened
pods**; the proposed common values in PR #267 select them for Codex. The pod's
non-root user, dropped capabilities, seccomp and network policy remain. After
that rollout, `codex sandbox -- /bin/true` checks the configured mode without
a model. The pilot helper uses this check; it must not force `workspace-write`.

Both engines use the same bounded runner contract. Select the engine that has
a working login and the required tools in the box.

## Prepare once, outside the running agent

Golf remains a [clean-side role box](../../gitops/clusters/home/claude-boxes/README.md#the-boxes):
no v1 or tainted checkouts, transcripts or memories. Keep
`BEADWORK_NO_MEMORIES=1`; the helper sets it for every run. Do not reuse another
box's volume or credential cache. `AGENTS.md` links to the existing `CLAUDE.md`
so both hosts get one source of project instructions.

1. Let the reviewed network policy and PR #267 sync through GitOps before using
   the chart-managed Codex settings. Check the actual pod and window 0 afterward;
   a merged PR or render does not prove a live rollout. The common values declare
   both plugins for Claude and Codex, and the chart supplies the same MCP roster
   to both. Start a new session to load refreshed plugin files. Use the installed
   `beadwork` plugin's exact `scripts/run.py` path below.
2. Check `claude auth status` or `codex login status`. If Codex needs login,
   the operator runs `codex login --device-auth` at the console and completes
   the browser step. This is the documented [headless login flow](https://learn.chatgpt.com/docs/auth#login-on-headless-devices).
   Never copy login files or tokens between boxes.
3. Claude's chart-managed MCP configuration is supplied by its CLI shim. Keep
   the normal pod PATH: `command -v claude` should resolve to
   `/usr/local/sbin/claude`. A login shell such as `bash -lc` can reset PATH
   and select the raw binary, omitting the chart-managed tool configuration.
   Invoke the helper directly through `kubectl exec … -- python3 …` or from a
   shell with the normal pod PATH.
   Codex reads the chart's `/etc/codex/config.toml` after rollout. Confirm the
   required MCP tools before an unattended run; the chart shares configuration,
   while each tool's actual connection still needs verification.

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
