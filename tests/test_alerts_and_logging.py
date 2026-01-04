from __future__ import annotations

import json
import logging

from polymarket_watch.alerts import render_json, render_text
from polymarket_watch.logging_json import JsonFormatter
from polymarket_watch.polymarket import Market, Trade
from polymarket_watch.scoring import Alert
from polymarket_watch.store import WalletStats


def _sample_alert() -> Alert:
    trade = Trade(
        trade_id="t1",
        proxy_wallet="0xabc",
        side="BUY",
        asset="1",
        condition_id="0xcond",
        size=100.0,
        price=0.5,
        timestamp=1_700_000_000,
        title="Test market",
        slug="test-market",
        event_slug="test-event",
        outcome="Yes",
        outcome_index=0,
        transaction_hash="0xtx",
        name="alice",
        pseudonym="Quiet-Fox",
    )
    wallet = WalletStats(
        proxy_wallet="0xabc",
        first_seen_ts=1_699_000_000,
        trades_total=10,
        unique_markets_total=4,
        trades_7d=3,
        unique_markets_7d=2,
        avg_notional_7d=123.45,
    )
    market = Market(
        condition_id="0xcond",
        question="Test market?",
        slug="test-market",
        liquidity_num=12_345.0,
        volume24hr=6_789.0,
        outcomes=["Yes", "No"],
        outcome_prices=[0.5, 0.5],
    )
    return Alert(
        score=5,
        reasons=["large_trade", "new_wallet_to_system"],
        trade=trade,
        notional=50.0,
        wallet_stats=wallet,
        market=market,
    )


def test_render_text_and_json() -> None:
    alert = _sample_alert()
    text = render_text(alert)
    assert "ALERT score=5" in text
    assert "wallet=0xabc" in text
    assert "url=https://polymarket.com/market/test-market" in text

    payload = json.loads(render_json(alert))
    assert payload["type"] == "alert"
    assert payload["score"] == 5
    assert payload["trade"]["slug"] == "test-market"


def test_json_formatter_includes_fields() -> None:
    formatter = JsonFormatter()
    record = logging.LogRecord(
        name="pmwatch",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="hello",
        args=(),
        exc_info=None,
    )
    record.fields = {"a": 1}
    rendered = formatter.format(record)
    payload = json.loads(rendered)
    assert payload["message"] == "hello"
    assert payload["fields"]["a"] == 1
