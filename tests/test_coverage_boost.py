from __future__ import annotations

import io
import json
import urllib.error
from unittest.mock import patch

from polymarket_watch.alerts import DiscordAlerter
from polymarket_watch.http import HttpClient, HttpConfig
from polymarket_watch.polymarket import Market, Trade
from polymarket_watch.scoring import build_alert
from polymarket_watch.store import Store, WalletStats


class _FakeResp(io.BytesIO):
    def __enter__(self) -> "_FakeResp":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001
        return None


def _sample_trade(ts: int = 1_700_000_000) -> Trade:
    return Trade(
        trade_id="t1",
        proxy_wallet="0xabc",
        side="BUY",
        asset="1",
        condition_id="0xcond",
        size=100.0,
        price=0.5,
        timestamp=ts,
        title="Test market",
        slug="test-market",
        event_slug="test-event",
        outcome="Yes",
        outcome_index=0,
        transaction_hash="0xtx",
        name=None,
        pseudonym=None,
    )


def test_discord_alerter_calls_webhook() -> None:
    calls: list[dict[str, object]] = []

    class _StubHttp:
        def post_json(self, url: str, payload: dict[str, object]) -> None:
            calls.append({"url": url, "payload": payload})

    alert = build_alert(
        trade=_sample_trade(),
        wallet_stats=WalletStats(
            proxy_wallet="0xabc",
            first_seen_ts=None,
            trades_total=1,
            unique_markets_total=1,
            trades_7d=1,
            unique_markets_7d=1,
            avg_notional_7d=0.0,
        ),
        market=Market(
            condition_id="0xcond",
            question="Q?",
            slug="test-market",
            liquidity_num=10_000.0,
            volume24hr=5_000.0,
            outcomes=["Yes", "No"],
            outcome_prices=[0.5, 0.5],
        ),
        min_notional=1.0,
        min_score=1,
    )
    assert alert is not None

    alerter = DiscordAlerter("https://example.com/webhook", http=_StubHttp())  # type: ignore[arg-type]
    alerter.send(alert)
    assert calls and calls[0]["url"] == "https://example.com/webhook"


def test_store_alert_cooldown_roundtrip(tmp_path) -> None:  # noqa: ANN001
    store = Store(str(tmp_path / "x.db"))
    try:
        key = "0xabc:0xcond"
        assert store.should_alert(key, cooldown_s=3600) is True
        store.mark_alerted(key)
        assert store.should_alert(key, cooldown_s=3600) is False
    finally:
        store.close()


def test_httpclient_retries_on_429_then_succeeds() -> None:
    client = HttpClient(HttpConfig(min_interval_s=0.0, max_retries=1))

    body = json.dumps({"ok": True}).encode("utf-8")
    calls = {"n": 0}

    def _fake_urlopen(req, timeout):  # noqa: ANN001, ANN201
        calls["n"] += 1
        if calls["n"] == 1:
            raise urllib.error.HTTPError(
                req.full_url,
                429,
                "too many",
                hdrs={"Retry-After": "0"},
                fp=None,
            )
        return _FakeResp(body)

    with patch("polymarket_watch.http.time.sleep", return_value=None):
        with patch("urllib.request.urlopen", side_effect=_fake_urlopen):
            assert client.get_json("https://example.com/api") == {"ok": True}


def test_scoring_returns_none_below_threshold() -> None:
    trade = _sample_trade()
    alert = build_alert(
        trade=trade,
        wallet_stats=WalletStats(
            proxy_wallet="0xabc",
            first_seen_ts=None,
            trades_total=10,
            unique_markets_total=4,
            trades_7d=3,
            unique_markets_7d=3,
            avg_notional_7d=0.0,
        ),
        market=None,
        min_notional=10_000.0,
        min_score=10,
    )
    assert alert is None
