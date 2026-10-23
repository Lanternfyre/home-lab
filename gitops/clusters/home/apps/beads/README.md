# beads — the agents' shared issue graph and memory

[beads](https://github.com/gastownhall/beads) (`bd`) is a dependency-graph issue
tracker for coding agents, stored in **Dolt** (a MySQL-wire database with git-like
history). This app is the **one Dolt server** every client shares: the six claude
boxes east-west, and the operator's machine over the LAN. Each opted-in project
is its own database on it, named by its prefix.

| | |
|---|---|
| Chart | helm-compendium `dolt/` (generic), pinned in `app.yaml` |
| Image | `dolthub/dolt-sql-server:2.5.1` — the engine bd 1.3.1 is built against |
| In-cluster | `beads-dolt.beads.svc.cluster.local:3306`, plaintext |
| LAN | `beads.lab.lanternfyre.dev:3306`, TLS (Let's Encrypt) |
| Users | `beads`@`%` (every client); `admin`@`localhost` (kubectl exec only) |
| Secrets | 1Password item **`Beads Dolt`**: `admin-password`, `beads-password` |
| Data | `qnap-beads-dolt`, protected, **no backup** |

## What beads gives an agent

- **Issues as a graph** — `bd ready` lists only unblocked work; `bd update <id>
  --claim` is atomic (measured: three simultaneous claims, exactly one wins).
- **Memories** — `bd remember "…" --key k`, `bd recall k`, `bd memories`,
  `bd forget k`. They belong to the **project's database**: shared by everyone
  attached to it, invisible to other projects (measured). `bd prime` injects
  ALL of them at SessionStart, so prune — every memory costs every session.
- **Compaction** — `bd admin compact --analyze` → the agent writes summaries →
  `--apply`. Tier 1 = closed ≥ 30 days. Manual, no API key.

## Clients

### The boxes

The claude-box chart's `beads` block (chart ≥ 0.1.9) and claude-workspace image
(≥ 0.1.18) do it all: `bd` and `perles` (TUI) in the image, the connection in
env, `DO_NOT_TRACK=1`, an ExternalSecret for the password, and an egress rule.
Each box is `BEADS_ACTOR=<its Remote Control name>`.

### The operator's machine

```bash
# bd 1.3.1, checksum-verified, into ~/bin (no package manager needed)
v=1.3.1; u=https://github.com/gastownhall/beads/releases/download/v$v
curl -sSfLO $u/beads_${v}_linux_amd64.tar.gz && curl -sSfLO $u/checksums.txt
grep " beads_${v}_linux_amd64.tar.gz\$" checksums.txt | sha256sum -c - && tar -xzf beads_${v}_linux_amd64.tar.gz -C ~/bin bd

# Env (shell profile). The password comes from the credentials file, not env.
export DO_NOT_TRACK=1                       # bd phones home otherwise
export BEADS_DOLT_SERVER_MODE=1             # a plain `bd init` must fail, not go embedded
export BEADS_DOLT_SERVER_HOST=beads.lab.lanternfyre.dev
export BEADS_DOLT_SERVER_PORT=3306
export BEADS_DOLT_SERVER_USER=beads
export BEADS_DOLT_SERVER_TLS=1
export BEADS_ACTOR=operator

# ~/.config/beads/credentials, chmod 600. INI, one section per host:port
# (measured: a bare password line under the section is refused):
#   [beads.lab.lanternfyre.dev:3306]
#   password = <beads-password from 1Password>
#   [127.0.0.1:3306]
#   password = <the same>          # for the port-forward below
```

**Away from home** the LAN door is closed by design (the VPN arrives looking like
the edge node). Use a port-forward, which is loopback inside the pod and so never
meets the policy:

```bash
kubectl -n beads port-forward svc/beads-dolt 3306:3306 &
BEADS_DOLT_SERVER_HOST=127.0.0.1 BEADS_DOLT_SERVER_TLS= bd ready
```

## Opting a repo in

One deliberate command, run once, committed:

```bash
bd init --server --server-user beads --prefix <unique> --skip-agents --skip-hooks
bd setup claude          # one SessionStart hook (`bd prime --hook-json`) + a CLAUDE.md block
```

- 🔴 **`--prefix` explicit and unique.** It defaults to the directory name, and
  the same prefix *is* the same database: two repos called `server` would
  silently share one.
- 🔴 **`--server-user beads` is mandatory.** init ignores `BEADS_DOLT_SERVER_USER`
  and dials as root, which is refused (measured).
- **Host and port from env, never `--server-host`.** Then the committed
  `.beads/metadata.json` pins neither, and the same repo works from the LAN and
  from the boxes.
- **`--skip-agents`**, or init also writes AGENTS.md, Codex, Cursor and skill
  files — and **commits them by itself**.
- ⚠️ The CLAUDE.md block `bd setup claude` writes tells agents "do NOT use
  MEMORY.md files / TodoWrite". Decide per repo whether that is wanted before
  committing it.

## Runbook

```bash
# An admin shell (localhost only; the password is in the Secret)
kubectl -n beads exec -it beads-dolt-0 -c dolt -- \
  dolt --host 127.0.0.1 --user admin --password "$(kubectl -n beads get secret beads-dolt -o jsonpath='{.data.admin-password}' | base64 -d)" --no-tls sql

# Inside it:
SHOW DATABASES;                    -- one per opted-in project
CALL DOLT_UNDROP('<db>');          -- an agent dropped it; the app user cannot undo that
SELECT * FROM `<db>`.dolt_log LIMIT 20;   -- who changed what (BEADS_ACTOR)
```

- **Rotate a password**: edit the field in 1Password. ExternalSecrets picks it
  up within the hour, Reloader rolls the server (its init container re-sets
  users on every start) and the boxes. Update the operator's credentials file.
- **Users** are reconciled from `chart-values.yaml` on every start. A user
  *removed* from the list is not dropped — `DROP USER` it as admin.

## Measured, 2026-10-23

Everything below was measured against `dolthub/dolt-sql-server:2.5.1` under the
same securityContext as the pod, with bd 1.3.1; the dolt chart PR has the detail.

- The image runs non-root with a read-only root fs. Its **entrypoint is
  bypassed** (it creates users once and supervises its own restart loop).
- 🔴 **Dolt ignores the FILE privilege**: a user with it revoked still read the
  TLS key with `load_file()`. The chart always sets `secure_file_priv` to a
  missing directory, which closes it.
- An app user **can** `DROP DATABASE` and **cannot** `DOLT_UNDROP` — admin can.
- 🔴 `bd flatten` / `bd compact` rewrite history and then garbage-collect it.
  Irrecoverable, and any box can run them. **Dolt history is not a backup.**
- Memories sit **uncommitted** in Dolt's working set — durable across restarts,
  but not versioned until something runs `bd vc commit`.
- bd sends usage metrics to two hosts no box may reach; a denied FQDN hangs.
  `DO_NOT_TRACK=1` turns it off.

## Not done, on purpose

- **Backup.** None, like every PV here (H0 deferred). The first follow-up: a
  scheduled Dolt backup to an off-NAS remote — which also needs an egress rule.
- **VPN access.** See above; revisit when the kube-proxy-replacement identity
  work makes VPN clients distinguishable from the edge.
- **Web UI.** CLI and `perles` first; a board (beads-web / Maggie) behind the
  gated Gateway only once the data proves useful.
