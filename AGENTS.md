---
name: polymarket-watch-agent
description: Working agreement for this repo.
mode: baseline
---

# Polymarket Watch

## Defaults
- `AGENT_MODE=baseline`
- Prefer `uv` over `pip`.
- Do not commit secrets; keep them in `.env` (ignored by git).

## Interface contract
- Setup: `make setup`
- Format/lint/tests: `make all`

## Publishing
- GitHub Pages serves from `/docs` (`docs/index.html`, `docs/alerts.json`, `docs/alerts.jsonl`).
- `.github/workflows/publish_alerts.yml` runs on a 6-hour cron and commits updates to `docs/` and `state/`.

