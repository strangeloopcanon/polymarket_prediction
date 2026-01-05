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

## Scoring heuristics (current)

So what: alerts are triggered when `score >= --min-score` **and** `notional >= --min-notional`.

Points are added by `src/polymarket_watch/scoring.py:1`:

- `large_trade` (+1): `size * price >= --min-notional`
- `new_wallet_to_system` (+2): wallet has `<= 1` observed trade in local state/DB
- `concentrated_activity_7d` (+1): wallet has `>= 3` trades in last 7d and `<= 3` unique markets in last 7d
- `large_vs_liquidity` (+1): `notional / liquidity_num >= 0.01`
- `low_liquidity_market` (+1): `liquidity_num < 50_000`
- `large_vs_24h_volume` (+1): `notional / volume24hr >= 0.05`
- `low_24h_volume_market` (+1): `volume24hr < 25_000`
- `extreme_price` (+1): `price <= 0.05` or `price >= 0.95`
- `recent_trade` (label only): trade happened in last 60s

Important caveat:
- In the GitHub Pages publisher (`scripts/publish_alerts.py:1`), wallet history is only tracked for trades `>= --min-notional`, so `new_wallet_to_system` means “new among tracked large trades”, not necessarily a brand-new Polymarket user.

## Publisher heuristics (GitHub Pages)

So what: the public feed is tuned to surface a handful of “key event” markets per day (fast moves + quiet accumulation), not every suspicious trade.

The publisher (`scripts/publish_alerts.py:1`) emits alerts based on:

- `market_price_move_30m`: large price range over the last `--fast-window-seconds` (default 30m)
- `market_heat_30m`: meaningful notional over the same window
- `market_participation_30m`: multiple unique wallets over the same window
- `whale_accumulation_6h`: a single wallet accumulating over `--accum-window-seconds` (default 6h)

Notes:
- The feed is capped to `--max-alerts-per-day` (default 5) in UTC days.
- `alerts.json` includes a `metrics` object per alert with the window stats that triggered it.
- The displayed `notional` is the signal notional for the window (not necessarily `size * price` of the single displayed trade).

## Cheap public publishing (GitHub Pages)

So what: you can publish a simple webpage + JSON feed of alerts without running servers.

- GitHub Action (`.github/workflows/publish_alerts.yml`) runs every 6 hours and updates:
  - `docs/index.html` (webpage)
  - `docs/alerts.json` and `docs/alerts.jsonl` (public feeds)
  - `state/state.json` (lightweight dedupe + cooldown state)
  - `archive/alerts-YYYY-MM.jsonl` (append-only full history; partitioned monthly)
- Enable GitHub Pages in repo settings:
  - Settings → Pages → Build and deployment → Source: “Deploy from a branch”
  - Branch: `main` (or default) / Folder: `/docs`
