from __future__ import annotations

from polymarket_watch.polymarket import Market, Trade
from polymarket_watch.scoring import build_alert, trade_notional_usd
from polymarket_watch.store import WalletStats


def test_build_alert_triggers_on_large_new_wallet_low_liquidity() -> None:
    trade = Trade(
        trade_id="t1",
        proxy_wallet="0xabc",
        side="BUY",
        asset="1",
        condition_id="0xcond",
        size=10_000,
        price=0.2,
        timestamp=1_700_000_000,
        title="Test market",
        slug="test-market",
        event_slug="test-event",
        outcome="Yes",
        outcome_index=0,
        transaction_hash="0xtx",
        name=None,
        pseudonym=None,
    )
    wallet = WalletStats(
        proxy_wallet="0xabc",
        first_seen_ts=None,
        trades_total=1,
        unique_markets_total=1,
        trades_7d=1,
        unique_markets_7d=1,
        avg_notional_7d=0.0,
    )
    market = Market(
        condition_id="0xcond",
        question="Test market",
        slug="test-market",
        liquidity_num=10_000,
        volume24hr=5_000,
        outcomes=["Yes", "No"],
        outcome_prices=[0.2, 0.8],
    )
    alert = build_alert(
        trade=trade, wallet_stats=wallet, market=market, min_notional=1000, min_score=3
    )
    assert alert is not None
    assert alert.notional == trade_notional_usd(trade)
    assert alert.score >= 3
    assert "new_wallet_to_system" in alert.reasons
