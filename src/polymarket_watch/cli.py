from __future__ import annotations

import argparse
import logging
import os
import time

from polymarket_watch.alerts import DiscordAlerter, render_json, render_text
from polymarket_watch.logging_json import log, setup_logging
from polymarket_watch.polymarket import PolymarketClient
from polymarket_watch.scoring import build_alert, trade_notional_usd
from polymarket_watch.store import Store


logger = logging.getLogger("pmwatch")


def _add_common_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--db", default="pmwatch.db", help="SQLite DB path (state + history).")
    p.add_argument("--limit", type=int, default=500, help="Trades fetch limit (max is capped).")
    p.add_argument("--poll-seconds", type=float, default=15.0, help="Polling interval for watch.")
    p.add_argument("--min-notional", type=float, default=2000.0, help="Min trade notional ($).")
    p.add_argument("--min-score", type=int, default=3, help="Min score to alert.")
    p.add_argument("--cooldown-seconds", type=int, default=3600, help="Per wallet+market cooldown.")
    p.add_argument(
        "--format",
        choices=["text", "json"],
        default="text",
        help="Alert output format.",
    )
    p.add_argument(
        "--discord-webhook-url",
        default=os.environ.get("PMWATCH_DISCORD_WEBHOOK_URL", ""),
        help="Discord webhook URL (or set PMWATCH_DISCORD_WEBHOOK_URL).",
    )
    p.add_argument("--log-level", default="INFO", help="Log level (INFO, DEBUG, ...).")


def _run_once(
    *,
    store: Store,
    client: PolymarketClient,
    limit: int,
    min_notional: float,
    min_score: int,
    cooldown_seconds: int,
    out_format: str,
    discord_webhook_url: str,
) -> int:
    trades = client.get_recent_trades(limit=limit, offset=0)
    if not trades:
        log(logger, logging.INFO, "no_trades")
        return 0

    alerter = DiscordAlerter(discord_webhook_url) if discord_webhook_url else None

    # Process chronologically to preserve time order.
    trades_sorted = sorted(trades, key=lambda t: (t.timestamp, t.trade_id))
    emitted = 0
    for trade in trades_sorted:
        if store.has_trade(trade.trade_id):
            continue

        notional = trade_notional_usd(trade)
        store.record_trade(trade, notional=notional)

        wallet_stats = store.wallet_stats(trade.proxy_wallet)

        market = store.get_market(trade.condition_id)
        if market is None and trade.condition_id:
            market = client.get_market_by_condition_id(trade.condition_id)
            if market is not None:
                store.upsert_market(market)

        alert = build_alert(
            trade=trade,
            wallet_stats=wallet_stats,
            market=market,
            min_notional=min_notional,
            min_score=min_score,
        )
        if alert is None:
            continue

        alert_key = f"{trade.proxy_wallet}:{trade.condition_id}"
        if not store.should_alert(alert_key, cooldown_seconds):
            continue

        store.mark_alerted(alert_key)
        emitted += 1

        if out_format == "json":
            print(render_json(alert))
        else:
            print(render_text(alert))
            print("-" * 40)

        if alerter:
            try:
                alerter.send(alert)
            except Exception as e:
                log(logger, logging.WARNING, "discord_send_failed", error=str(e))

    log(logger, logging.INFO, "poll_complete", fetched=len(trades), emitted=emitted)
    return emitted


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pmwatch")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_once = sub.add_parser("once", help="Poll once and emit alerts.")
    _add_common_args(p_once)

    p_watch = sub.add_parser("watch", help="Continuously poll and emit alerts.")
    _add_common_args(p_watch)

    args = parser.parse_args(argv)
    setup_logging(args.log_level)

    store = Store(args.db)
    client = PolymarketClient()
    try:
        limit = min(int(args.limit), 500)
        if int(args.limit) != limit:
            log(logger, logging.WARNING, "limit_capped", requested=int(args.limit), used=limit)
        if args.cmd == "once":
            _run_once(
                store=store,
                client=client,
                limit=limit,
                min_notional=args.min_notional,
                min_score=args.min_score,
                cooldown_seconds=args.cooldown_seconds,
                out_format=args.format,
                discord_webhook_url=args.discord_webhook_url,
            )
            return 0

        while True:
            try:
                _run_once(
                    store=store,
                    client=client,
                    limit=limit,
                    min_notional=args.min_notional,
                    min_score=args.min_score,
                    cooldown_seconds=args.cooldown_seconds,
                    out_format=args.format,
                    discord_webhook_url=args.discord_webhook_url,
                )
            except Exception as e:
                log(logger, logging.ERROR, "watch_iteration_failed", error=str(e))
            time.sleep(float(args.poll_seconds))
    except KeyboardInterrupt:
        log(logger, logging.INFO, "shutdown")
        return 0
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
