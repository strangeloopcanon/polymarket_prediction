# Polymarket Watch (MVP)

So what: this polls Polymarket’s public APIs and emits alerts when a trade looks “suspicious”
(large size, new wallet, low-liquidity markets, concentrated activity).

## APIs / auth / rate limits

- No API key required for **read-only** use:
  - Market metadata: `https://gamma-api.polymarket.com` (Gamma API)
  - Trades feed: `https://data-api.polymarket.com` (Data API)
- API keys are only needed for **authenticated/trading** CLOB endpoints.
- Official rate limits (docs.polymarket.com):
  - Gamma API: general `4000 requests / 10s`, `/markets` `300 requests / 10s`
  - Data API: `/trades` `200 requests / 10s`

## Quickstart

```bash
make setup
pmwatch once --min-notional 2000
pmwatch watch --min-notional 2000 --min-score 3
```

Optional Discord alerts:

```bash
export PMWATCH_DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/..."
pmwatch watch --min-notional 2000 --min-score 3
```

## Notes

- This is an MVP heuristic scorer (not a prediction model).
- Unit tests stub network calls; the watcher uses live HTTP.

## Cheap public publishing (GitHub Pages)

So what: you can publish a simple webpage + JSON feed of alerts without running servers.

- GitHub Action (`.github/workflows/publish_alerts.yml`) runs every 6 hours and updates:
  - `docs/index.html` (webpage)
  - `docs/alerts.json` and `docs/alerts.jsonl` (public feeds)
  - `state/state.json` (lightweight dedupe + cooldown state)
- Enable GitHub Pages in repo settings:
  - Settings → Pages → Build and deployment → Source: “Deploy from a branch”
  - Branch: `main` (or default) / Folder: `/docs`

