from __future__ import annotations

import time
from dataclasses import dataclass

from polymarket_watch.polymarket import Market, Trade
from polymarket_watch.store import WalletStats


@dataclass(frozen=True)
class Alert:
    score: int
    reasons: list[str]
    trade: Trade
    notional: float
    wallet_stats: WalletStats
    market: Market | None

    @property
    def url(self) -> str:
        slug = self.trade.slug or (self.market.slug if self.market else "")
        return f"https://polymarket.com/market/{slug}" if slug else "https://polymarket.com"


def trade_notional_usd(trade: Trade) -> float:
    # Polymarket prices are in [0,1] (USDC per share). Use size*price as a simple notional proxy.
    return max(0.0, float(trade.size) * float(trade.price))


def score_trade(
    *,
    trade: Trade,
    notional: float,
    wallet_stats: WalletStats,
    market: Market | None,
    min_notional: float,
) -> tuple[int, list[str]]:
    score = 0
    reasons: list[str] = []

    if notional >= min_notional:
        score += 1
        reasons.append("large_trade")

    if wallet_stats.trades_total <= 1:
        score += 2
        reasons.append("new_wallet_to_system")

    if wallet_stats.trades_7d >= 3 and wallet_stats.unique_markets_7d <= 3:
        score += 1
        reasons.append("concentrated_activity_7d")

    if market and market.liquidity_num is not None:
        if market.liquidity_num > 0:
            frac = notional / market.liquidity_num
            if frac >= 0.01:
                score += 1
                reasons.append("large_vs_liquidity")
        if market.liquidity_num < 50_000:
            score += 1
            reasons.append("low_liquidity_market")

    if market and market.volume24hr is not None:
        if market.volume24hr > 0:
            frac = notional / market.volume24hr
            if frac >= 0.05:
                score += 1
                reasons.append("large_vs_24h_volume")
        if market.volume24hr < 25_000:
            score += 1
            reasons.append("low_24h_volume_market")

    # Light nudge for trades very close to 0 or 1 (often indicates urgency or certainty).
    if trade.price <= 0.05 or trade.price >= 0.95:
        score += 1
        reasons.append("extreme_price")

    # If the trade timestamp is very recent, prioritize alerting quickly (no extra score; just reason).
    if int(time.time()) - int(trade.timestamp) <= 60:
        reasons.append("recent_trade")

    return score, reasons


def build_alert(
    *,
    trade: Trade,
    wallet_stats: WalletStats,
    market: Market | None,
    min_notional: float,
    min_score: int,
) -> Alert | None:
    notional = trade_notional_usd(trade)
    score, reasons = score_trade(
        trade=trade,
        notional=notional,
        wallet_stats=wallet_stats,
        market=market,
        min_notional=min_notional,
    )
    if notional < min_notional or score < min_score:
        return None
    return Alert(
        score=score,
        reasons=reasons,
        trade=trade,
        notional=notional,
        wallet_stats=wallet_stats,
        market=market,
    )
