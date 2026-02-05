from __future__ import annotations

from polymarket_watch.polymarket import PolymarketClient


class _StubHttp:
    def __init__(self, trades, markets):  # noqa: ANN001
        self._trades = trades
        self._markets = markets

    def get_json(self, url: str, params=None):  # noqa: ANN001, ANN201
        if url.endswith("/trades"):
            return self._trades
        if url.endswith("/markets"):
            return self._markets
        raise AssertionError("unexpected url")


def test_get_recent_trades_parses_fields() -> None:
    http = _StubHttp(
        trades=[
            {
                "proxyWallet": "0xabc",
                "side": "BUY",
                "asset": "1",
                "conditionId": "0xcond",
                "size": 10,
                "price": 0.25,
                "timestamp": 123,
                "title": "T",
                "slug": "s",
                "eventSlug": "e",
                "outcome": "Yes",
                "outcomeIndex": 0,
                "transactionHash": "0xtx",
                "name": "n",
                "pseudonym": "p",
            }
        ],
        markets=[],
    )
    client = PolymarketClient(http=http, gamma_base="https://gamma", data_base="https://data")
    trades = client.get_recent_trades(limit=1, offset=0)
    assert len(trades) == 1
    t = trades[0]
    assert t.proxy_wallet == "0xabc"
    assert t.side == "BUY"
    assert t.trade_id


def test_get_market_by_condition_id_parses_outcomes() -> None:
    http = _StubHttp(
        trades=[],
        markets=[
            {
                "conditionId": "0xcond",
                "question": "Q?",
                "slug": "q",
                "liquidityNum": 1000,
                "volume24hr": 200,
                "outcomes": '["Yes","No"]',
                "outcomePrices": '["0.25","0.75"]',
            }
        ],
    )
    client = PolymarketClient(http=http, gamma_base="https://gamma", data_base="https://data")
    market = client.get_market_by_condition_id("0xcond")
    assert market is not None
    assert market.outcomes == ["Yes", "No"]
    assert market.outcome_prices == [0.25, 0.75]


def test_get_recent_trades_handles_malformed_numeric_fields() -> None:
    http = _StubHttp(
        trades=[
            {
                "proxyWallet": "0xabc",
                "side": "maybe",
                "asset": "1",
                "conditionId": "0xcond",
                "size": "bad",
                "price": "nan",
                "timestamp": "oops",
                "title": "T",
                "slug": "s",
                "eventSlug": "e",
                "outcome": "Yes",
                "outcomeIndex": "x",
                "transactionHash": "0xtx",
            }
        ],
        markets=[],
    )
    client = PolymarketClient(http=http, gamma_base="https://gamma", data_base="https://data")
    trades = client.get_recent_trades(limit=1, offset=0)
    assert len(trades) == 1
    t = trades[0]
    assert t.side == "BUY"
    assert t.size == 0.0
    assert t.price == 0.0
    assert t.timestamp == 0
    assert t.outcome_index == -1


def test_get_market_by_condition_id_ignores_bad_prices() -> None:
    http = _StubHttp(
        trades=[],
        markets=[
            {
                "conditionId": "0xcond",
                "question": "Q?",
                "slug": "q",
                "liquidityNum": 1000,
                "volume24hr": 200,
                "outcomes": '["Yes","No"]',
                "outcomePrices": '["0.25","x",null,"0.75"]',
            }
        ],
    )
    client = PolymarketClient(http=http, gamma_base="https://gamma", data_base="https://data")
    market = client.get_market_by_condition_id("0xcond")
    assert market is not None
    assert market.outcome_prices == [0.25, 0.75]
