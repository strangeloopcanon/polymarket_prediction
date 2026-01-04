from __future__ import annotations

import os
import tempfile

from polymarket_watch.polymarket import Trade
from polymarket_watch.store import Store


def test_store_records_and_stats() -> None:
    with tempfile.TemporaryDirectory() as td:
        db = os.path.join(td, "t.db")
        store = Store(db)
        try:
            trade = Trade(
                trade_id="t1",
                proxy_wallet="0xabc",
                side="BUY",
                asset="1",
                condition_id="0xcond",
                size=100,
                price=0.5,
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
            assert store.has_trade("t1") is False
            store.record_trade(trade, notional=50.0)
            assert store.has_trade("t1") is True
            stats = store.wallet_stats("0xabc")
            assert stats.trades_total == 1
            assert stats.unique_markets_total == 1
        finally:
            store.close()
