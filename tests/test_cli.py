from __future__ import annotations

import json

from polymarket_watch.polymarket import Market, Trade


class _StubClient:
    def get_recent_trades(self, limit: int = 200, offset: int = 0):  # noqa: ANN201
        return [
            Trade(
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
        ]

    def get_market_by_condition_id(self, condition_id: str):  # noqa: ANN201
        return Market(
            condition_id=condition_id,
            question="Test market?",
            slug="test-market",
            liquidity_num=10_000.0,
            volume24hr=5_000.0,
            outcomes=["Yes", "No"],
            outcome_prices=[0.2, 0.8],
        )


def test_cli_once_emits_alert_json(monkeypatch, capsys) -> None:  # noqa: ANN001
    import polymarket_watch.cli as cli

    monkeypatch.setattr(cli, "PolymarketClient", lambda: _StubClient())
    code = cli.main(
        [
            "once",
            "--db",
            ":memory:",
            "--min-notional",
            "1000",
            "--min-score",
            "3",
            "--format",
            "json",
            "--limit",
            "1",
            "--log-level",
            "CRITICAL",
        ]
    )
    assert code == 0

    out = capsys.readouterr().out.strip().splitlines()
    payload = json.loads(out[0])
    assert payload["type"] == "alert"
    assert payload["trade"]["slug"] == "test-market"


def test_cli_watch_recovers_from_iteration_error(monkeypatch) -> None:  # noqa: ANN001
    import polymarket_watch.cli as cli

    class _StubStore:
        def __init__(self) -> None:
            self.closed = False

        def close(self) -> None:
            self.closed = True

    store = _StubStore()
    monkeypatch.setattr(cli, "Store", lambda _db: store)
    monkeypatch.setattr(cli, "PolymarketClient", lambda: object())

    calls = {"n": 0}
    events: list[str] = []

    def _fake_run_once(**_kwargs) -> int:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("boom")
        raise KeyboardInterrupt

    def _fake_log(_logger, _level, message: str, **_fields) -> None:
        events.append(message)

    monkeypatch.setattr(cli, "_run_once", _fake_run_once)
    monkeypatch.setattr(cli, "log", _fake_log)
    monkeypatch.setattr(cli.time, "sleep", lambda _s: None)

    code = cli.main(["watch", "--db", ":memory:", "--poll-seconds", "0", "--log-level", "CRITICAL"])
    assert code == 0
    assert calls["n"] == 2
    assert "watch_iteration_failed" in events
    assert store.closed is True
