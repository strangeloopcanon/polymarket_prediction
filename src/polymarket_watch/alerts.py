from __future__ import annotations

import datetime as dt
import json
from dataclasses import asdict
from typing import Any

from polymarket_watch.http import HttpClient
from polymarket_watch.scoring import Alert


def _ts_iso(ts: int) -> str:
    return dt.datetime.fromtimestamp(ts, tz=dt.timezone.utc).isoformat()


def render_text(alert: Alert) -> str:
    wallet = alert.trade.proxy_wallet
    ident = alert.trade.pseudonym or alert.trade.name or wallet
    market_title = alert.trade.title or (alert.market.question if alert.market else "")
    lines = [
        f"ALERT score={alert.score} notional=${alert.notional:,.2f}",
        f"wallet={wallet} ({ident})",
        f"side={alert.trade.side} outcome={alert.trade.outcome} price={alert.trade.price} size={alert.trade.size}",
        f"market={market_title}",
        f"url={alert.url}",
        f"ts={_ts_iso(alert.trade.timestamp)} tx={alert.trade.transaction_hash}",
        "reasons=" + ",".join(alert.reasons),
        (
            "wallet_stats="
            f"trades_total={alert.wallet_stats.trades_total} "
            f"unique_markets_total={alert.wallet_stats.unique_markets_total} "
            f"trades_7d={alert.wallet_stats.trades_7d} "
            f"unique_markets_7d={alert.wallet_stats.unique_markets_7d} "
            f"avg_notional_7d=${alert.wallet_stats.avg_notional_7d:,.2f}"
        ),
    ]
    if alert.market:
        lines.append(
            "market_stats="
            f"liquidity=${(alert.market.liquidity_num or 0):,.2f} "
            f"volume24hr=${(alert.market.volume24hr or 0):,.2f}"
        )
    return "\n".join(lines)


def render_json(alert: Alert) -> str:
    payload: dict[str, Any] = {
        "type": "alert",
        "score": alert.score,
        "reasons": alert.reasons,
        "notional": alert.notional,
        "url": alert.url,
        "trade": asdict(alert.trade),
        "wallet_stats": asdict(alert.wallet_stats),
        "market": asdict(alert.market) if alert.market else None,
    }
    return json.dumps(payload, separators=(",", ":"), sort_keys=True)


class DiscordAlerter:
    def __init__(self, webhook_url: str, http: HttpClient | None = None) -> None:
        self._webhook_url = webhook_url
        self._http = http or HttpClient()

    def send(self, alert: Alert) -> None:
        content = render_text(alert)
        # Discord: keep it simple; users can switch to embeds later.
        self._http.post_json(self._webhook_url, {"content": content[:1900]})
