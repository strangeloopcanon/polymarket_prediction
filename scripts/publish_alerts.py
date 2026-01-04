from __future__ import annotations

import argparse
import json
import os
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from polymarket_watch.polymarket import Market, PolymarketClient, Trade
from polymarket_watch.scoring import build_alert
from polymarket_watch.store import WalletStats


def _load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(path)


def _append_lines(path: Path, lines: list[str]) -> None:
    if not lines:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        for line in lines:
            f.write(line)
            if not line.endswith("\n"):
                f.write("\n")


def _archive_path(archive_dir: Path, ts: int) -> Path:
    dt = datetime.fromtimestamp(int(ts), tz=timezone.utc)
    return archive_dir / f"alerts-{dt:%Y-%m}.jsonl"


def _now_ts() -> int:
    return int(time.time())


def _as_market(obj: dict[str, Any]) -> Market:
    return Market(
        condition_id=str(obj.get("condition_id", "")),
        question=str(obj.get("question", "")),
        slug=str(obj.get("slug", "")),
        liquidity_num=(
            float(obj["liquidity_num"]) if obj.get("liquidity_num") is not None else None
        ),
        volume24hr=(float(obj["volume24hr"]) if obj.get("volume24hr") is not None else None),
        outcomes=[str(x) for x in (obj.get("outcomes") or [])],
        outcome_prices=[float(x) for x in (obj.get("outcome_prices") or [])],
    )


def _wallet_stats_from_state(
    state: dict[str, Any], wallet: str, *, min_notional: float
) -> WalletStats:
    wallets = state.setdefault("wallets", {})
    w = wallets.setdefault(wallet, {"first_seen_ts": None, "events": [], "markets": []})

    events: list[list[Any]] = w.get("events") or []
    filtered: list[list[Any]] = []
    for e in events:
        if not isinstance(e, list) or len(e) < 3:
            continue
        try:
            if float(e[2]) >= min_notional:
                filtered.append(e)
        except Exception:
            continue
    events = filtered

    cutoff = _now_ts() - 7 * 24 * 60 * 60
    events_7d = [e for e in events if int(e[0]) >= cutoff]
    notional_sum = sum(float(e[2]) for e in events_7d) if events_7d else 0.0
    avg_notional_7d = notional_sum / len(events_7d) if events_7d else 0.0

    markets: list[str] = w.get("markets") or []
    markets_total = set(markets)
    markets_7d = {str(e[1]) for e in events_7d}

    first_seen_ts = None
    if events:
        try:
            first_seen_ts = int(min(int(e[0]) for e in events))
        except Exception:
            first_seen_ts = None

    return WalletStats(
        proxy_wallet=wallet,
        first_seen_ts=first_seen_ts,
        trades_total=len(events),
        unique_markets_total=len(markets_total),
        trades_7d=len(events_7d),
        unique_markets_7d=len(markets_7d),
        avg_notional_7d=avg_notional_7d,
    )


def _record_wallet_event(state: dict[str, Any], trade: Trade, notional: float) -> None:
    wallets = state.setdefault("wallets", {})
    w = wallets.setdefault(trade.proxy_wallet, {"first_seen_ts": None, "events": [], "markets": []})

    if w.get("first_seen_ts") is None:
        w["first_seen_ts"] = int(trade.timestamp)
    w["last_seen_ts"] = int(trade.timestamp)

    events: list[list[Any]] = w.get("events") or []
    events.append([int(trade.timestamp), trade.condition_id, float(notional)])

    markets: list[str] = w.get("markets") or []
    if trade.condition_id and trade.condition_id not in markets:
        markets.append(trade.condition_id)

    # Prune to rolling 7d and cap size for repo-friendly state.
    cutoff = _now_ts() - 7 * 24 * 60 * 60
    events = [e for e in events if int(e[0]) >= cutoff]
    if len(events) > 400:
        events = events[-400:]
    w["events"] = events
    w["markets"] = markets[-500:]


def _get_market(
    state: dict[str, Any], client: PolymarketClient, condition_id: str
) -> Market | None:
    markets = state.setdefault("markets", {})
    cached = markets.get(condition_id)
    if isinstance(cached, dict):
        try:
            return _as_market(cached)
        except Exception:
            pass

    market = client.get_market_by_condition_id(condition_id)
    if market is None:
        return None
    markets[condition_id] = asdict(market)
    return market


def _cooldown_ok(state: dict[str, Any], key: str, cooldown_s: int) -> bool:
    alerts = state.setdefault("alerts", {})
    last = alerts.get(key)
    if last is None:
        return True
    return (_now_ts() - int(last)) >= cooldown_s


def _mark_alerted(state: dict[str, Any], key: str) -> None:
    alerts = state.setdefault("alerts", {})
    alerts[key] = _now_ts()


def _alert_to_public_dict(alert) -> dict[str, Any]:  # noqa: ANN001
    return {
        "type": "alert",
        "score": alert.score,
        "reasons": alert.reasons,
        "notional": alert.notional,
        "url": alert.url,
        "trade": asdict(alert.trade),
        "wallet_stats": asdict(alert.wallet_stats),
        "market": (asdict(alert.market) if alert.market else None),
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--state", default="state/state.json", help="State JSON path (committed).")
    p.add_argument("--out", default="docs/alerts.json", help="Public JSON feed path.")
    p.add_argument("--out-jsonl", default="docs/alerts.jsonl", help="Public JSONL feed path.")
    p.add_argument(
        "--archive-dir",
        default="archive",
        help="Directory for append-only JSONL archives (partitioned monthly).",
    )
    p.add_argument("--limit", type=int, default=500, help="Trades fetch limit.")
    p.add_argument("--min-notional", type=float, default=2000.0, help="Min notional ($).")
    p.add_argument("--min-score", type=int, default=3, help="Min score to alert.")
    p.add_argument(
        "--cooldown-seconds", type=int, default=6 * 60 * 60, help="Cooldown per wallet+market."
    )
    p.add_argument("--max-seen", type=int, default=5000, help="Max seen trade IDs retained.")
    p.add_argument(
        "--max-alerts", type=int, default=200, help="Max alerts retained in public feed."
    )
    p.add_argument(
        "--state-keep-seconds",
        type=int,
        default=14 * 24 * 60 * 60,
        help="How long to keep wallet/market/cooldown state (seconds).",
    )
    args = p.parse_args(argv)

    state_path = Path(args.state)
    out_path = Path(args.out)
    out_jsonl_path = Path(args.out_jsonl)
    archive_dir = Path(args.archive_dir)

    state: dict[str, Any] = _load_json(state_path, default={})
    client = PolymarketClient()

    trades = client.get_recent_trades(limit=min(int(args.limit), 500), offset=0)
    trades = sorted(trades, key=lambda t: (t.timestamp, t.trade_id))

    seen_list = state.setdefault("seen_trade_ids", [])
    if not isinstance(seen_list, list):
        seen_list = []
        state["seen_trade_ids"] = seen_list
    seen_set = set(seen_list)

    new_alerts: list[dict[str, Any]] = []
    for trade in trades:
        if trade.trade_id in seen_set:
            continue

        notional = float(trade.size) * float(trade.price)
        seen_list.append(trade.trade_id)
        seen_set.add(trade.trade_id)
        max_seen = int(args.max_seen)
        if len(seen_list) > max_seen:
            del seen_list[: len(seen_list) - max_seen]
            seen_set = set(seen_list)

        # Keep state small: only track trades that could ever alert.
        if notional < float(args.min_notional):
            continue

        _record_wallet_event(state, trade, notional=notional)

        wallet_stats = _wallet_stats_from_state(
            state, trade.proxy_wallet, min_notional=float(args.min_notional)
        )
        market = _get_market(state, client, trade.condition_id) if trade.condition_id else None

        alert = build_alert(
            trade=trade,
            wallet_stats=wallet_stats,
            market=market,
            min_notional=float(args.min_notional),
            min_score=int(args.min_score),
        )
        if alert is None:
            continue

        key = f"{trade.proxy_wallet}:{trade.condition_id}"
        if not _cooldown_ok(state, key, cooldown_s=int(args.cooldown_seconds)):
            continue
        _mark_alerted(state, key)
        new_alerts.append(_alert_to_public_dict(alert))

    # Merge into existing feed.
    existing = _load_json(out_path, default={})
    prev_alerts = existing.get("alerts") if isinstance(existing, dict) else None
    if not isinstance(prev_alerts, list):
        prev_alerts = []

    min_notional = float(args.min_notional)
    prev_filtered: list[dict[str, Any]] = []
    for a in prev_alerts:
        if not isinstance(a, dict):
            continue
        try:
            if float(a.get("notional", 0.0) or 0.0) >= min_notional:
                prev_filtered.append(a)
        except Exception:
            continue
    prev_alerts = prev_filtered
    combined = prev_alerts + new_alerts
    combined_sorted = sorted(
        combined,
        key=lambda a: int(a.get("trade", {}).get("timestamp", 0)),
        reverse=True,
    )
    combined_sorted = combined_sorted[: int(args.max_alerts)]

    payload = {
        "generated_at": _now_ts(),
        "repo": os.environ.get("GITHUB_REPOSITORY", ""),
        "alerts": combined_sorted,
        "new_alerts": len(new_alerts),
    }

    _atomic_write(out_path, json.dumps(payload, indent=2, sort_keys=True) + "\n")
    for_alerts_jsonl = sorted(
        combined_sorted,
        key=lambda a: int(a.get("trade", {}).get("timestamp", 0)),
    )
    _atomic_write(
        out_jsonl_path, "\n".join(json.dumps(x, sort_keys=True) for x in for_alerts_jsonl) + "\n"
    )

    # Append new alerts to an archive so we don't lose history as the public feed is capped.
    archive_batches: dict[Path, list[str]] = {}
    for a in new_alerts:
        ts = int(a.get("trade", {}).get("timestamp", 0))
        path = _archive_path(archive_dir, ts if ts > 0 else _now_ts())
        archive_batches.setdefault(path, []).append(json.dumps(a, sort_keys=True))
    for path, lines in archive_batches.items():
        _append_lines(path, lines)

    # Keep state small-ish.
    state["updated_at"] = _now_ts()

    keep_wallets = {str(a.get("trade", {}).get("proxy_wallet", "")) for a in combined_sorted}
    keep_markets = {str(a.get("trade", {}).get("condition_id", "")) for a in combined_sorted}
    cutoff = _now_ts() - int(args.state_keep_seconds)

    wallets = state.get("wallets")
    if isinstance(wallets, dict):
        for k in list(wallets.keys()):
            if k in keep_wallets:
                continue
            w = wallets.get(k)
            if not isinstance(w, dict):
                wallets.pop(k, None)
                continue
            last_seen = w.get("last_seen_ts")
            if last_seen is None:
                events = w.get("events") or []
                if isinstance(events, list) and events:
                    try:
                        last_seen = int(events[-1][0])
                    except Exception:
                        last_seen = None
            if last_seen is None or int(last_seen) < cutoff:
                wallets.pop(k, None)

    markets = state.get("markets")
    if isinstance(markets, dict):
        for k in list(markets.keys()):
            if k not in keep_markets:
                markets.pop(k, None)

    alerts = state.get("alerts")
    if isinstance(alerts, dict):
        for k in list(alerts.keys()):
            try:
                if int(alerts.get(k, 0)) < cutoff:
                    alerts.pop(k, None)
            except Exception:
                alerts.pop(k, None)

    _atomic_write(state_path, json.dumps(state, indent=2, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
