# home-lab: agent instructions

@README.md

The README above holds the overview, the invariants, the conventions and the map of the docs. Agents follow all of it. This file adds only the documentation rules.

## Documentation

- Docs are for people: the operator first, then anyone joining or running the cluster. Agents are tools that write and read them. If something helps agents but makes a page harder for a person, don't do it, or keep it off the page.
- Every page answers one reader's question. Load the `docs:write` skill before creating or substantially changing a page. Standard: claude-shared-skills `docs` 0.3.1.
- **Top-level docs move when touched.** The topic files at the repo root stay where they are until someone substantially edits one. That change moves it into its `docs/` section with `git mv` and updates every reference to it in manifests, scripts and docs. New pages go straight into `docs/<section>/`.
- **Things waiting on the operator** stay in MANUAL-STEPS.md until its open items move to Beads, which is tracked as its own change.
