from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Literal

from polymarket_watch.http import HttpClient


Side = Literal["BUY", "SELL"]


@dataclass(frozen=True)
class Trade:
    trade_id: str
    proxy_wallet: str
    side: Side
    asset: str
    condition_id: str
    size: float
    price: float
    timestamp: int
    title: str
    slug: str
    event_slug: str
    outcome: str
    outcome_index: int
    transaction_hash: str
    name: str | None
    pseudonym: str | None


@dataclass(frozen=True)
class Market:
    condition_id: str
    question: str
    slug: str
    liquidity_num: float | None
    volume24hr: float | None
    outcomes: list[str]
    outcome_prices: list[float]


def _stable_trade_id(trade: dict[str, Any]) -> str:
    parts = [
        str(trade.get("transactionHash", "")),
        str(trade.get("asset", "")),
        str(trade.get("outcomeIndex", "")),
        str(trade.get("side", "")),
        str(trade.get("proxyWallet", "")),
        str(trade.get("timestamp", "")),
    ]
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()
    return digest


class PolymarketClient:
    def __init__(
        self,
        http: HttpClient | None = None,
        gamma_base: str = "https://gamma-api.polymarket.com",
        data_base: str = "https://data-api.polymarket.com",
    ) -> None:
        self._http = http or HttpClient()
        self._gamma_base = gamma_base.rstrip("/")
        self._data_base = data_base.rstrip("/")

    def get_recent_trades(self, limit: int = 200, offset: int = 0) -> list[Trade]:
        raw = self._http.get_json(
            f"{self._data_base}/trades",
            params={"limit": int(limit), "offset": int(offset)},
        )
        trades: list[Trade] = []
        if not isinstance(raw, list):
            return trades
        for item in raw:
            if not isinstance(item, dict):
                continue
            trades.append(
                Trade(
                    trade_id=_stable_trade_id(item),
                    proxy_wallet=str(item.get("proxyWallet", "")),
                    side=str(item.get("side", "")).upper(),  # type: ignore[arg-type]
                    asset=str(item.get("asset", "")),
                    condition_id=str(item.get("conditionId", "")),
                    size=float(item.get("size", 0.0)),
                    price=float(item.get("price", 0.0)),
                    timestamp=int(item.get("timestamp", 0)),
                    title=str(item.get("title", "")),
                    slug=str(item.get("slug", "")),
                    event_slug=str(item.get("eventSlug", "")),
                    outcome=str(item.get("outcome", "")),
                    outcome_index=int(item.get("outcomeIndex", -1)),
                    transaction_hash=str(item.get("transactionHash", "")),
                    name=str(item.get("name")) if item.get("name") else None,
                    pseudonym=str(item.get("pseudonym")) if item.get("pseudonym") else None,
                )
            )
        return trades

    def get_market_by_condition_id(self, condition_id: str) -> Market | None:
        raw = self._http.get_json(
            f"{self._gamma_base}/markets",
            params={"condition_ids": condition_id, "limit": 1, "offset": 0},
        )
        if not isinstance(raw, list) or not raw:
            return None
        item = raw[0]
        if not isinstance(item, dict):
            return None

        def _maybe_float(v: Any) -> float | None:
            try:
                if v is None:
                    return None
                return float(v)
            except (TypeError, ValueError):
                return None

        outcomes_raw = item.get("outcomes", "[]")
        prices_raw = item.get("outcomePrices", "[]")
        outcomes: list[str] = []
        prices: list[float] = []
        for parsed, target in [(outcomes_raw, outcomes), (prices_raw, prices)]:
            if isinstance(parsed, str):
                try:
                    parsed = json.loads(parsed)
                except Exception:
                    parsed = []
            if isinstance(parsed, list):
                if target is outcomes:
                    target.extend([str(x) for x in parsed])
                else:
                    target.extend([float(x) for x in parsed if x is not None])

        return Market(
            condition_id=str(item.get("conditionId", condition_id)),
            question=str(item.get("question", "")),
            slug=str(item.get("slug", "")),
            liquidity_num=_maybe_float(item.get("liquidityNum")),
            volume24hr=_maybe_float(item.get("volume24hr")),
            outcomes=outcomes,
            outcome_prices=prices,
        )
