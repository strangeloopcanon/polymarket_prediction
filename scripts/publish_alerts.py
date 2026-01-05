from __future__ import annotations

import argparse
import json
import os
import time
from collections import defaultdict
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from polymarket_watch.polymarket import Market, PolymarketClient, Trade
from polymarket_watch.scoring import score_trade, trade_notional_usd
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


def _record_market_event(
    state: dict[str, Any],
    trade: Trade,
    *,
    notional: float,
    now_ts: int,
    keep_seconds: int,
    max_events_per_market: int,
) -> None:
    market_events = state.setdefault("market_events", {})
    if not isinstance(market_events, dict):
        market_events = {}
        state["market_events"] = market_events
    events = market_events.setdefault(trade.condition_id, [])
    if not isinstance(events, list):
        events = []
        market_events[trade.condition_id] = events
    events.append(
        [
            int(trade.timestamp),
            trade.proxy_wallet,
            float(trade.price),
            float(notional),
            int(trade.outcome_index),
            str(trade.side),
        ]
    )

    cutoff = int(now_ts) - int(keep_seconds)
    pruned: list[list[Any]] = []
    for e in events:
        if not isinstance(e, list) or len(e) < 4:
            continue
        try:
            if int(e[0]) >= cutoff:
                pruned.append(e)
        except Exception:
            continue
    if len(pruned) > int(max_events_per_market):
        pruned = pruned[-int(max_events_per_market) :]
    market_events[trade.condition_id] = pruned


def _window_stats(events: list[Any], *, since_ts: int) -> dict[str, Any]:
    notional_sum = 0.0
    wallets: set[str] = set()
    raw_prices: list[float] = []
    p0_prices: list[float] = []
    notional_by_wallet: dict[str, float] = defaultdict(float)
    trades_by_wallet: dict[str, int] = defaultdict(int)

    pro_notional_by_wallet: dict[str, float] = defaultdict(float)
    anti_notional_by_wallet: dict[str, float] = defaultdict(float)
    pro_trades_by_wallet: dict[str, int] = defaultdict(int)
    anti_trades_by_wallet: dict[str, int] = defaultdict(int)
    multi_outcome_seen = False

    for e in events:
        if not isinstance(e, list) or len(e) < 4:
            continue
        try:
            ts = int(e[0])
            if ts < int(since_ts):
                continue
            wallet = str(e[1] or "")
            price = float(e[2])
            notional = float(e[3])
        except Exception:
            continue
        notional_sum += notional
        wallets.add(wallet)
        raw_prices.append(price)
        notional_by_wallet[wallet] += notional
        trades_by_wallet[wallet] += 1

        outcome_index = None
        side = None
        if len(e) >= 6:
            try:
                outcome_index = int(e[4])
            except Exception:
                outcome_index = None
            side_raw = str(e[5] or "").upper()
            if side_raw in {"BUY", "SELL"}:
                side = side_raw

        # If we see trades on outcomes beyond {0,1}, treat the market as non-binary for
        # canonical price/direction calculations.
        if outcome_index is not None and outcome_index not in {0, 1}:
            multi_outcome_seen = True
            continue

        # Canonicalize to "outcome 0" implied probability so we can compare apples-to-apples
        # even when trades are on different outcome tokens (e.g., "Yes" vs "No").
        if outcome_index in {0, 1}:
            p0 = price if outcome_index == 0 else (1.0 - price)
            p0 = max(0.0, min(1.0, p0))
            p0_prices.append(p0)

            if side is not None:
                pro0 = (outcome_index == 0 and side == "BUY") or (
                    outcome_index == 1 and side == "SELL"
                )
                if pro0:
                    pro_notional_by_wallet[wallet] += notional
                    pro_trades_by_wallet[wallet] += 1
                else:
                    anti_notional_by_wallet[wallet] += notional
                    anti_trades_by_wallet[wallet] += 1

    price_range_raw = (max(raw_prices) - min(raw_prices)) if len(raw_prices) >= 2 else None
    price_range = (
        (max(p0_prices) - min(p0_prices))
        if not multi_outcome_seen and len(p0_prices) >= 2
        else None
    )

    top_wallet = None
    top_wallet_notional = 0.0
    for w, v in notional_by_wallet.items():
        if v > top_wallet_notional:
            top_wallet = w
            top_wallet_notional = v

    top_wallet_share = (top_wallet_notional / notional_sum) if notional_sum > 0 else None
    top_wallet_trades = trades_by_wallet.get(top_wallet or "", 0)

    top_net_wallet = None
    top_net_wallet_notional = 0.0
    top_net_wallet_direction = None
    top_net_wallet_share_of_wallet = None
    top_net_wallet_share_of_market = None
    top_net_wallet_trades = 0
    if not multi_outcome_seen:
        for w in set(pro_notional_by_wallet.keys()) | set(anti_notional_by_wallet.keys()):
            pro = float(pro_notional_by_wallet.get(w, 0.0) or 0.0)
            anti = float(anti_notional_by_wallet.get(w, 0.0) or 0.0)
            total = pro + anti
            if total <= 0:
                continue
            net = abs(pro - anti)
            if net <= top_net_wallet_notional:
                continue
            direction = "pro0" if pro >= anti else "anti0"
            trades = (
                int(pro_trades_by_wallet.get(w, 0) or 0)
                if direction == "pro0"
                else int(anti_trades_by_wallet.get(w, 0) or 0)
            )
            top_net_wallet = w
            top_net_wallet_notional = net
            top_net_wallet_direction = direction
            top_net_wallet_share_of_wallet = net / total
            top_net_wallet_trades = trades

        if top_net_wallet is not None and notional_sum > 0:
            top_net_wallet_share_of_market = top_net_wallet_notional / notional_sum

    return {
        "notional_sum": notional_sum,
        "unique_wallets": len(wallets),
        "price_range": price_range,
        "price_range_raw": price_range_raw,
        "top_wallet": top_wallet,
        "top_wallet_notional": top_wallet_notional,
        "top_wallet_share": top_wallet_share,
        "top_wallet_trades": top_wallet_trades,
        "top_net_wallet": top_net_wallet,
        "top_net_wallet_notional": top_net_wallet_notional,
        "top_net_wallet_direction": top_net_wallet_direction,
        "top_net_wallet_share_of_wallet": top_net_wallet_share_of_wallet,
        "top_net_wallet_share_of_market": top_net_wallet_share_of_market,
        "top_net_wallet_trades": top_net_wallet_trades,
    }


def _day_key_utc(ts: int) -> str:
    dt = datetime.fromtimestamp(int(ts), tz=timezone.utc)
    return f"{dt:%Y-%m-%d}"


def _cap_alerts_per_day(alerts: list[dict[str, Any]], *, max_per_day: int) -> list[dict[str, Any]]:
    if max_per_day <= 0:
        return []

    by_day: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for a in alerts:
        try:
            ts = int(a.get("trade", {}).get("timestamp", 0))
        except Exception:
            ts = 0
        by_day[_day_key_utc(ts)].append(a)

    kept: list[dict[str, Any]] = []
    for day in sorted(by_day.keys(), reverse=True):
        day_alerts = by_day[day]

        def _sort_key(x: dict[str, Any]) -> tuple[float, float, int]:
            try:
                score = float(x.get("score", 0) or 0)
            except Exception:
                score = 0.0
            try:
                notional = float(x.get("notional", 0) or 0)
            except Exception:
                notional = 0.0
            try:
                ts = int(x.get("trade", {}).get("timestamp", 0) or 0)
            except Exception:
                ts = 0
            return (score, notional, ts)

        day_alerts_sorted = sorted(day_alerts, key=_sort_key, reverse=True)
        kept.extend(day_alerts_sorted[:max_per_day])

    return sorted(
        kept,
        key=lambda a: int(a.get("trade", {}).get("timestamp", 0) or 0),
        reverse=True,
    )


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
    p.add_argument("--min-score", type=int, default=7, help="Min score to alert.")
    p.add_argument(
        "--cooldown-seconds", type=int, default=6 * 60 * 60, help="Cooldown per wallet+market."
    )
    p.add_argument("--max-seen", type=int, default=5000, help="Max seen trade IDs retained.")
    p.add_argument(
        "--max-alerts", type=int, default=200, help="Max alerts retained in public feed."
    )
    p.add_argument(
        "--max-alerts-per-day",
        type=int,
        default=5,
        help="Cap alerts per UTC day in public feed.",
    )
    p.add_argument(
        "--fast-window-seconds",
        type=int,
        default=30 * 60,
        help="Window for fast-move signals (seconds).",
    )
    p.add_argument(
        "--accum-window-seconds",
        type=int,
        default=6 * 60 * 60,
        help="Window for accumulation signals (seconds).",
    )
    p.add_argument(
        "--market-events-keep-seconds",
        type=int,
        default=6 * 60 * 60,
        help="How long to keep per-market trade events in state (seconds).",
    )
    p.add_argument(
        "--market-events-max-per-market",
        type=int,
        default=500,
        help="Max per-market events retained in state.",
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

    now_ts = _now_ts()

    seen_list = state.setdefault("seen_trade_ids", [])
    if not isinstance(seen_list, list):
        seen_list = []
        state["seen_trade_ids"] = seen_list
    seen_set = set(seen_list)

    new_alerts: list[dict[str, Any]] = []
    latest_trade_by_market: dict[str, Trade] = {}
    latest_trade_by_market_wallet: dict[str, dict[str, Trade]] = {}
    for trade in trades:
        if trade.trade_id in seen_set:
            continue

        notional = trade_notional_usd(trade)
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
        _record_market_event(
            state,
            trade,
            notional=notional,
            now_ts=now_ts,
            keep_seconds=int(args.market_events_keep_seconds),
            max_events_per_market=int(args.market_events_max_per_market),
        )

        if trade.condition_id:
            prev = latest_trade_by_market.get(trade.condition_id)
            if prev is None or int(trade.timestamp) >= int(prev.timestamp):
                latest_trade_by_market[trade.condition_id] = trade
            per_wallet = latest_trade_by_market_wallet.setdefault(trade.condition_id, {})
            prev_w = per_wallet.get(trade.proxy_wallet)
            if prev_w is None or int(trade.timestamp) >= int(prev_w.timestamp):
                per_wallet[trade.proxy_wallet] = trade

    min_score = int(args.min_score)
    market_events = (
        state.get("market_events") if isinstance(state.get("market_events"), dict) else {}
    )
    for condition_id, trade in latest_trade_by_market.items():
        events = market_events.get(condition_id, [])
        if not isinstance(events, list):
            continue

        fast = _window_stats(events, since_ts=now_ts - int(args.fast_window_seconds))
        accum = _window_stats(events, since_ts=now_ts - int(args.accum_window_seconds))

        reasons: list[str] = []
        fast_score = 0
        accum_score = 0

        fast_price_range = fast.get("price_range")
        if isinstance(fast_price_range, float):
            if fast_price_range >= 0.15:
                fast_score += 6
                reasons.append("market_price_move_30m")
            elif fast_price_range >= 0.08:
                fast_score += 4
                reasons.append("market_price_move_30m")

        fast_notional = float(fast.get("notional_sum", 0.0) or 0.0)
        if fast_notional >= 50_000:
            fast_score += 4
            reasons.append("market_heat_30m")
        elif fast_notional >= 20_000:
            fast_score += 2
            reasons.append("market_heat_30m")

        fast_wallets = int(fast.get("unique_wallets", 0) or 0)
        if fast_wallets >= 20:
            fast_score += 2
            reasons.append("market_participation_30m")
        elif fast_wallets >= 5:
            fast_score += 1
            reasons.append("market_participation_30m")

        accum_top_wallet = accum.get("top_wallet")
        accum_top_notional = float(accum.get("top_wallet_notional", 0.0) or 0.0)
        accum_top_share = accum.get("top_wallet_share")
        accum_top_trades = int(accum.get("top_wallet_trades", 0) or 0)
        accum_price_range = accum.get("price_range")

        accum_top_net_wallet = accum.get("top_net_wallet")
        accum_top_net_notional = float(accum.get("top_net_wallet_notional", 0.0) or 0.0)
        accum_top_net_direction = accum.get("top_net_wallet_direction")
        accum_top_net_share_of_wallet = accum.get("top_net_wallet_share_of_wallet")
        accum_top_net_share_of_market = accum.get("top_net_wallet_share_of_market")
        accum_top_net_trades = int(accum.get("top_net_wallet_trades", 0) or 0)

        if (
            accum_top_net_trades >= 2
            and isinstance(accum_top_net_share_of_wallet, float)
            and accum_top_net_share_of_wallet >= 0.6
        ):
            is_whale = False
            if accum_top_net_notional >= 50_000:
                accum_score += 6
                reasons.append("whale_accumulation_6h")
                is_whale = True
            elif accum_top_net_notional >= 25_000:
                accum_score += 4
                reasons.append("whale_accumulation_6h")
                is_whale = True
            if is_whale:
                if isinstance(accum_top_net_share_of_market, float):
                    if accum_top_net_share_of_market >= 0.8:
                        accum_score += 2
                        reasons.append("concentrated_flow_6h")
                    elif accum_top_net_share_of_market >= 0.6:
                        accum_score += 1
                        reasons.append("concentrated_flow_6h")
                if isinstance(accum_price_range, float) and accum_price_range <= 0.05:
                    accum_score += 1
                    reasons.append("quiet_price_6h")

        score = fast_score + accum_score
        if score < min_score:
            continue

        event_type = "fast_move" if fast_score >= accum_score else "accumulation"
        key = f"{event_type}:{condition_id}"
        if not _cooldown_ok(state, key, cooldown_s=int(args.cooldown_seconds)):
            continue
        _mark_alerted(state, key)

        rep_trade = trade
        if (
            event_type == "accumulation"
            and isinstance(accum_top_net_wallet, str)
            and accum_top_net_wallet
        ):
            rep_trade = (
                latest_trade_by_market_wallet.get(condition_id, {}).get(accum_top_net_wallet)
                or rep_trade
            )

        wallet_stats = _wallet_stats_from_state(
            state, rep_trade.proxy_wallet, min_notional=float(args.min_notional)
        )
        market = _get_market(state, client, condition_id) if condition_id else None
        _, ctx_reasons = score_trade(
            trade=rep_trade,
            notional=trade_notional_usd(rep_trade),
            wallet_stats=wallet_stats,
            market=market,
            min_notional=float(args.min_notional),
        )
        for r in ctx_reasons:
            if r in {"new_wallet_to_system", "concentrated_activity_7d", "extreme_price"}:
                reasons.append(r)

        alert_notional = fast_notional if event_type == "fast_move" else accum_top_net_notional
        slug = rep_trade.slug or (market.slug if market else "")
        new_alerts.append(
            {
                "type": "alert",
                "score": score,
                "reasons": reasons,
                "notional": alert_notional,
                "url": f"https://polymarket.com/market/{slug}"
                if slug
                else "https://polymarket.com",
                "trade": asdict(rep_trade),
                "wallet_stats": asdict(wallet_stats),
                "market": (asdict(market) if market else None),
                "metrics": {
                    "event_type": event_type,
                    "fast_window_s": int(args.fast_window_seconds),
                    "notional_fast_window": fast_notional,
                    "unique_wallets_fast_window": fast_wallets,
                    "price_range_fast_window": fast_price_range,
                    "accum_window_s": int(args.accum_window_seconds),
                    "top_wallet_accum_window": accum_top_wallet,
                    "top_wallet_notional_accum_window": accum_top_notional,
                    "top_wallet_share_accum_window": accum_top_share,
                    "top_wallet_trades_accum_window": accum_top_trades,
                    "top_net_wallet_accum_window": accum_top_net_wallet,
                    "top_net_wallet_notional_accum_window": accum_top_net_notional,
                    "top_net_wallet_direction_accum_window": accum_top_net_direction,
                    "top_net_wallet_share_of_wallet_accum_window": accum_top_net_share_of_wallet,
                    "top_net_wallet_share_of_market_accum_window": accum_top_net_share_of_market,
                    "top_net_wallet_trades_accum_window": accum_top_net_trades,
                    "price_range_accum_window": accum_price_range,
                },
            }
        )

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
        key=lambda a: int(a.get("trade", {}).get("timestamp", 0) or 0),
        reverse=True,
    )

    max_per_day = int(args.max_alerts_per_day)
    if max_per_day > 0:
        combined_sorted = _cap_alerts_per_day(combined_sorted, max_per_day=max_per_day)
    combined_sorted = combined_sorted[: int(args.max_alerts)]

    new_trade_ids = {str(a.get("trade", {}).get("trade_id", "")) for a in new_alerts}
    new_in_feed = sum(
        1 for a in combined_sorted if str(a.get("trade", {}).get("trade_id", "")) in new_trade_ids
    )

    payload = {
        "generated_at": _now_ts(),
        "repo": os.environ.get("GITHUB_REPOSITORY", ""),
        "alerts": combined_sorted,
        "new_alerts": new_in_feed,
    }

    _atomic_write(out_path, json.dumps(payload, indent=2, sort_keys=True) + "\n")
    for_alerts_jsonl = sorted(
        combined_sorted,
        key=lambda a: int(a.get("trade", {}).get("timestamp", 0) or 0),
    )
    _atomic_write(
        out_jsonl_path, "\n".join(json.dumps(x, sort_keys=True) for x in for_alerts_jsonl) + "\n"
    )

    # Append new alerts to an archive so we don't lose history as the public feed is capped.
    archive_batches: dict[Path, list[str]] = {}
    for a in new_alerts:
        ts = int(a.get("trade", {}).get("timestamp", 0) or 0)
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

    market_events = state.get("market_events")
    if isinstance(market_events, dict):
        event_cutoff = _now_ts() - int(args.market_events_keep_seconds)
        for k in list(market_events.keys()):
            v = market_events.get(k)
            if not isinstance(v, list):
                market_events.pop(k, None)
                continue
            pruned: list[list[Any]] = []
            for e in v:
                if not isinstance(e, list) or len(e) < 4:
                    continue
                try:
                    if int(e[0]) >= event_cutoff:
                        pruned.append(e)
                except Exception:
                    continue
            if not pruned and k not in keep_markets:
                market_events.pop(k, None)
                continue
            if len(pruned) > int(args.market_events_max_per_market):
                pruned = pruned[-int(args.market_events_max_per_market) :]
            market_events[k] = pruned
            keep_markets.add(k)

    _atomic_write(state_path, json.dumps(state, indent=2, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
