from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass

from polymarket_watch.polymarket import Market, Trade


@dataclass(frozen=True)
class WalletStats:
    proxy_wallet: str
    first_seen_ts: int | None
    trades_total: int
    unique_markets_total: int
    trades_7d: int
    unique_markets_7d: int
    avg_notional_7d: float


class Store:
    def __init__(self, path: str) -> None:
        self._conn = sqlite3.connect(path)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def close(self) -> None:
        self._conn.close()

    def _init_schema(self) -> None:
        cur = self._conn.cursor()
        cur.executescript(
            """
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS trades (
              trade_id TEXT PRIMARY KEY,
              ts INTEGER NOT NULL,
              proxy_wallet TEXT NOT NULL,
              condition_id TEXT NOT NULL,
              slug TEXT NOT NULL,
              title TEXT NOT NULL,
              side TEXT NOT NULL,
              outcome TEXT NOT NULL,
              outcome_index INTEGER NOT NULL,
              size REAL NOT NULL,
              price REAL NOT NULL,
              notional REAL NOT NULL,
              transaction_hash TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_trades_wallet_ts ON trades(proxy_wallet, ts);
            CREATE INDEX IF NOT EXISTS idx_trades_market_ts ON trades(condition_id, ts);

            CREATE TABLE IF NOT EXISTS markets (
              condition_id TEXT PRIMARY KEY,
              question TEXT NOT NULL,
              slug TEXT NOT NULL,
              liquidity_num REAL,
              volume24hr REAL,
              outcomes_json TEXT NOT NULL,
              outcome_prices_json TEXT NOT NULL,
              updated_ts INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS alerts (
              alert_key TEXT PRIMARY KEY,
              last_alert_ts INTEGER NOT NULL
            );
            """
        )
        self._conn.commit()

    def has_trade(self, trade_id: str) -> bool:
        cur = self._conn.execute("SELECT 1 FROM trades WHERE trade_id = ? LIMIT 1", (trade_id,))
        return cur.fetchone() is not None

    def record_trade(self, trade: Trade, notional: float) -> None:
        self._conn.execute(
            """
            INSERT OR IGNORE INTO trades(
              trade_id, ts, proxy_wallet, condition_id, slug, title,
              side, outcome, outcome_index, size, price, notional, transaction_hash
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                trade.trade_id,
                trade.timestamp,
                trade.proxy_wallet,
                trade.condition_id,
                trade.slug,
                trade.title,
                trade.side,
                trade.outcome,
                trade.outcome_index,
                trade.size,
                trade.price,
                notional,
                trade.transaction_hash,
            ),
        )
        self._conn.commit()

    def upsert_market(self, market: Market) -> None:
        self._conn.execute(
            """
            INSERT INTO markets(
              condition_id, question, slug, liquidity_num, volume24hr,
              outcomes_json, outcome_prices_json, updated_ts
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(condition_id) DO UPDATE SET
              question=excluded.question,
              slug=excluded.slug,
              liquidity_num=excluded.liquidity_num,
              volume24hr=excluded.volume24hr,
              outcomes_json=excluded.outcomes_json,
              outcome_prices_json=excluded.outcome_prices_json,
              updated_ts=excluded.updated_ts
            """,
            (
                market.condition_id,
                market.question,
                market.slug,
                market.liquidity_num,
                market.volume24hr,
                json.dumps(market.outcomes),
                json.dumps(market.outcome_prices),
                int(time.time()),
            ),
        )
        self._conn.commit()

    def get_market(self, condition_id: str) -> Market | None:
        cur = self._conn.execute(
            "SELECT * FROM markets WHERE condition_id = ? LIMIT 1",
            (condition_id,),
        )
        row = cur.fetchone()
        if row is None:
            return None
        try:
            outcomes = json.loads(row["outcomes_json"])
            prices = json.loads(row["outcome_prices_json"])
        except Exception:
            outcomes = []
            prices = []
        return Market(
            condition_id=row["condition_id"],
            question=row["question"],
            slug=row["slug"],
            liquidity_num=row["liquidity_num"],
            volume24hr=row["volume24hr"],
            outcomes=[str(x) for x in outcomes] if isinstance(outcomes, list) else [],
            outcome_prices=[float(x) for x in prices] if isinstance(prices, list) else [],
        )

    def wallet_stats(self, proxy_wallet: str) -> WalletStats:
        cur = self._conn.execute(
            "SELECT MIN(ts) AS first_seen, COUNT(*) AS trades_total, "
            "COUNT(DISTINCT condition_id) AS unique_markets_total "
            "FROM trades WHERE proxy_wallet = ?",
            (proxy_wallet,),
        )
        row = cur.fetchone()
        first_seen = row["first_seen"] if row and row["first_seen"] is not None else None
        trades_total = int(row["trades_total"] or 0) if row else 0
        unique_markets_total = int(row["unique_markets_total"] or 0) if row else 0

        cutoff = int(time.time()) - 7 * 24 * 60 * 60
        cur = self._conn.execute(
            "SELECT COUNT(*) AS trades_7d, COUNT(DISTINCT condition_id) AS unique_markets_7d, "
            "AVG(notional) AS avg_notional_7d "
            "FROM trades WHERE proxy_wallet = ? AND ts >= ?",
            (proxy_wallet, cutoff),
        )
        row2 = cur.fetchone()
        trades_7d = int(row2["trades_7d"] or 0) if row2 else 0
        unique_markets_7d = int(row2["unique_markets_7d"] or 0) if row2 else 0
        avg_notional_7d = float(row2["avg_notional_7d"] or 0.0) if row2 else 0.0

        return WalletStats(
            proxy_wallet=proxy_wallet,
            first_seen_ts=int(first_seen) if first_seen is not None else None,
            trades_total=trades_total,
            unique_markets_total=unique_markets_total,
            trades_7d=trades_7d,
            unique_markets_7d=unique_markets_7d,
            avg_notional_7d=avg_notional_7d,
        )

    def should_alert(self, alert_key: str, cooldown_s: int) -> bool:
        now = int(time.time())
        cur = self._conn.execute(
            "SELECT last_alert_ts FROM alerts WHERE alert_key = ? LIMIT 1",
            (alert_key,),
        )
        row = cur.fetchone()
        if row is None:
            return True
        last = int(row["last_alert_ts"])
        return (now - last) >= cooldown_s

    def mark_alerted(self, alert_key: str) -> None:
        now = int(time.time())
        self._conn.execute(
            """
            INSERT INTO alerts(alert_key, last_alert_ts) VALUES (?, ?)
            ON CONFLICT(alert_key) DO UPDATE SET last_alert_ts=excluded.last_alert_ts
            """,
            (alert_key, now),
        )
        self._conn.commit()
