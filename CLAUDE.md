# home-lab: agent instructions

@README.md

The README above holds the overview, the invariants, the conventions and the map of the docs. Agents follow all of it. This file adds the documentation rules and the Beads rules.

## Documentation

- Docs are for people: the operator first, then anyone joining or running the cluster. Agents are tools that write and read them. If something helps agents but makes a page harder for a person, don't do it, or keep it off the page.
- Every page answers one reader's question. Load the `docs:write` skill before creating or substantially changing a page. Standard: claude-shared-skills `docs` 0.3.1.
- **Top-level docs move when touched.** The topic files at the repo root stay where they are until someone substantially edits one. That change moves it into its `docs/` section with `git mv` and updates every reference to it in manifests, scripts and docs. New pages go straight into `docs/<section>/`.
- **Things waiting on the operator are beads, not pages.** File a `human` bead (see Beads below). A fact that stays true goes on [Standing warnings](docs/operate/standing-warnings.md) instead.

## Beads

- **A step only the operator can do is a bead labelled `human`**: credentials, sudo, a console, a judgement call. File it with `bd create -t chore -l human,topic:<area> --body-file <file> --validate`, and keep the exact commands, dates and checks in it.
- **Agents never claim a `human` bead.** Add `--exclude-label human` whenever you claim work, for example `bd ready --exclude-type epic,decision --exclude-label human --claim --json`. Without it a session with no role is handed the operator's chores (measured 2026-10-25).
