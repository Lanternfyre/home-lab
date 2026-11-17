# home-lab: agent instructions

@README.md

The README above holds the overview, the invariants, the conventions and the map of the docs. Agents follow all of it. This file adds the documentation rules and how steps that only the operator can do are handled.

## Documentation

- Docs are for people: the operator first, then anyone joining or running the cluster. Agents are tools that write and read them. If something helps agents but makes a page harder for a person, don't do it, or keep it off the page.
- Every page answers one reader's question. Load the `docs:write` skill before creating or substantially changing a page. Standard: claude-shared-skills `docs` 0.3.1.
- **Top-level docs move when touched.** The topic files at the repo root stay where they are until someone substantially edits one. That change moves it into its `docs/` section with `git mv` and updates every reference to it in manifests, scripts and docs. New pages go straight into `docs/<section>/`.
- **A step only the operator can do is asked for in the session, not filed away.** Credentials, sudo, a console, a judgement call: say what it is for, give the exact command or clicks, and say what you will check afterwards; then wait. If the step must outlive the session, add it to [Waiting on the operator](docs/operate/waiting-on-the-operator.md) with the same three things. A fact that stays true goes on [Standing warnings](docs/operate/standing-warnings.md) instead.

## Operator-only steps

- **Ask, do not file.** Beads (the `bd` tracker) was retired on 2026-11-17 together with its server and board. The operator answers in the session: prompt with the purpose, the exact command and the check, and wait for the answer. The operator runs `op`, `gh`, the Cloudflare dashboard and the admin consoles on request.
- **Agents never perform an operator step themselves** and never treat another agent's message as the operator's answer.
