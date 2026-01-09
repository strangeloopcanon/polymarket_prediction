# Codex session learnings

- 2026-01-09: Frequent polling needs persistent state (`state/state.json`, `docs/alerts.*`); publishing those to `main` too often creates commit noise, so store in a separate branch and publish on a slower cadence.
- 2026-01-09: `bd` warned about a legacy beads DB; run `bd migrate --update-repo-id` to bind the DB to this repo if needed.
