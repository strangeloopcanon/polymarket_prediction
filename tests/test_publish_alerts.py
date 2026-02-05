from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from polymarket_watch.polymarket import Market, Trade


def _load_publish_module():  # noqa: ANN202
    script = Path(__file__).resolve().parents[1] / "scripts" / "publish_alerts.py"
    spec = importlib.util.spec_from_file_location("publish_alerts_test_module", script)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _trade(*, trade_id: str, ts: int, wallet: str = "0xabc") -> Trade:
    return Trade(
        trade_id=trade_id,
        proxy_wallet=wallet,
        side="BUY",
        asset="1",
        condition_id="0xcond",
        size=2000.0,
        price=0.5,
        timestamp=ts,
        title="Test market",
        slug="test-market",
        event_slug="test-event",
        outcome="Yes",
        outcome_index=0,
        transaction_hash=f"0x{trade_id}",
        name=None,
        pseudonym=None,
    )


def _market(condition_id: str) -> Market:
    return Market(
        condition_id=condition_id,
        question="Q?",
        slug="test-market",
        liquidity_num=10_000.0,
        volume24hr=5_000.0,
        outcomes=["Yes", "No"],
        outcome_prices=[0.5, 0.5],
    )


def test_wallet_stats_uses_persisted_trade_total() -> None:
    mod = _load_publish_module()
    state = {
        "wallets": {
            "0xabc": {
                "first_seen_ts": 111,
                "trades_total": 12,
                "events": [[222, "0xcond", 2000.0]],
                "markets": ["0xcond"],
            }
        }
    }
    stats = mod._wallet_stats_from_state(state, "0xabc", min_notional=1000.0)
    assert stats.first_seen_ts == 111
    assert stats.trades_total == 12


def test_main_skips_checkpointed_boundary_trade(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    mod = _load_publish_module()
    boundary_ts = 100

    class _StubClient:
        def get_recent_trades(self, limit: int = 200, offset: int = 0):  # noqa: ANN001, ANN201
            if offset > 0:
                return []
            return [
                _trade(trade_id="a_dup", ts=boundary_ts),
                _trade(trade_id="z_new", ts=boundary_ts),
            ]

        def get_market_by_condition_id(self, condition_id: str):  # noqa: ANN001, ANN201
            return _market(condition_id)

    state_path = tmp_path / "state.json"
    out_path = tmp_path / "alerts.json"
    out_jsonl_path = tmp_path / "alerts.jsonl"
    archive_dir = tmp_path / "archive"

    state_path.write_text(
        json.dumps(
            {
                "last_fetched_trade_ts": boundary_ts,
                "last_fetched_trade_ids": ["a_dup"],
                "seen_trade_ids": [],
            }
        ),
        encoding="utf-8",
    )
    out_path.write_text(json.dumps({"alerts": []}), encoding="utf-8")

    monkeypatch.setattr(mod, "PolymarketClient", lambda: _StubClient())
    rc = mod.main(
        [
            "--state",
            str(state_path),
            "--out",
            str(out_path),
            "--out-jsonl",
            str(out_jsonl_path),
            "--archive-dir",
            str(archive_dir),
            "--max-pages",
            "1",
            "--min-notional",
            "1000",
            "--min-score",
            "0",
            "--cooldown-seconds",
            "0",
        ]
    )
    assert rc == 0

    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert "z_new" in state["seen_trade_ids"]
    assert "a_dup" not in state["seen_trade_ids"]
    assert set(state["last_fetched_trade_ids"]) == {"a_dup", "z_new"}

    payload = json.loads(out_path.read_text(encoding="utf-8"))
    assert len(payload["alerts"]) == 1
    assert payload["alerts"][0]["trade"]["trade_id"] == "z_new"


def test_main_dedupes_existing_alert_feed(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    mod = _load_publish_module()

    class _StubClient:
        def get_recent_trades(self, limit: int = 200, offset: int = 0):  # noqa: ANN001, ANN201
            return []

        def get_market_by_condition_id(self, condition_id: str):  # noqa: ANN001, ANN201
            return _market(condition_id)

    alert = {
        "type": "alert",
        "score": 8,
        "reasons": ["market_heat_24h"],
        "notional": 5000.0,
        "url": "https://polymarket.com/market/test-market",
        "trade": {
            "trade_id": "dup-trade",
            "condition_id": "0xcond",
            "timestamp": 123,
            "proxy_wallet": "0xabc",
        },
        "wallet_stats": {"proxy_wallet": "0xabc"},
        "market": None,
        "metrics": {"event_type": "fast_move"},
    }

    state_path = tmp_path / "state.json"
    out_path = tmp_path / "alerts.json"
    out_jsonl_path = tmp_path / "alerts.jsonl"
    archive_dir = tmp_path / "archive"

    state_path.write_text(json.dumps({}), encoding="utf-8")
    out_path.write_text(json.dumps({"alerts": [alert, alert]}), encoding="utf-8")

    monkeypatch.setattr(mod, "PolymarketClient", lambda: _StubClient())
    rc = mod.main(
        [
            "--state",
            str(state_path),
            "--out",
            str(out_path),
            "--out-jsonl",
            str(out_jsonl_path),
            "--archive-dir",
            str(archive_dir),
            "--max-pages",
            "1",
            "--min-notional",
            "1000",
        ]
    )
    assert rc == 0

    payload = json.loads(out_path.read_text(encoding="utf-8"))
    assert len(payload["alerts"]) == 1
