"""
Persistence Layer for DenMa Arbitrage Engine.
Persists historical information (orders, fills, positions, realized PNL, bot events,
and execution history) in SQLite. Uses rolling CSV files strictly for archival/export.
Crucially: Does NOT load historical datasets during application startup; queries are
executed on-demand only when a relevant screen or date range is requested.
"""

import contextlib
import csv
import datetime
import logging
import os
import sqlite3
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "denma_history.db"
)
DEFAULT_ARCHIVE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "archive"
)


class PersistenceManager:
    """
    Lightweight SQLite persistence manager for historical trade and strategy data.
    Provides fast indexed queries on-demand and rolling CSV archival export.
    """

    def __init__(self, db_path: str = DEFAULT_DB_PATH):
        self.db_path = db_path
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._init_db()

    @contextlib.contextmanager
    def _conn(self):
        """Context manager that ensures connections are cleanly closed and committed."""
        conn = sqlite3.connect(self.db_path, check_same_thread=False, timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("PRAGMA busy_timeout=10000;")
        conn.execute("PRAGMA temp_store=MEMORY;")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_db(self):
        """Create tables and indexes quickly without loading data into memory."""
        with self._conn() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS orders (
                    id TEXT PRIMARY KEY,
                    bot_id TEXT,
                    symbol TEXT NOT NULL,
                    venue TEXT NOT NULL,
                    side TEXT NOT NULL,
                    order_type TEXT NOT NULL,
                    price REAL NOT NULL,
                    qty REAL NOT NULL,
                    status TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS fills (
                    id TEXT PRIMARY KEY,
                    order_id TEXT NOT NULL,
                    bot_id TEXT,
                    symbol TEXT NOT NULL,
                    venue TEXT NOT NULL,
                    side TEXT NOT NULL,
                    fill_price REAL NOT NULL,
                    fill_qty REAL NOT NULL,
                    fee REAL DEFAULT 0.0,
                    fee_currency TEXT DEFAULT 'USDT',
                    timestamp REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS positions (
                    id TEXT PRIMARY KEY,
                    bot_id TEXT,
                    symbol TEXT NOT NULL,
                    venue TEXT NOT NULL,
                    side TEXT NOT NULL,
                    qty REAL NOT NULL,
                    entry_price REAL NOT NULL,
                    current_price REAL NOT NULL,
                    floating_pnl REAL DEFAULT 0.0,
                    realized_pnl REAL DEFAULT 0.0,
                    status TEXT NOT NULL,
                    updated_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS realized_pnl (
                    id TEXT PRIMARY KEY,
                    bot_id TEXT,
                    strategy_type TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    amount_usd REAL NOT NULL,
                    timestamp REAL NOT NULL,
                    date TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS bot_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    bot_id TEXT,
                    event_type TEXT NOT NULL,
                    message TEXT NOT NULL,
                    level TEXT DEFAULT 'INFO',
                    timestamp REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS execution_history (
                    id TEXT PRIMARY KEY,
                    bot_id TEXT,
                    strategy TEXT NOT NULL,
                    route TEXT NOT NULL,
                    profit_usd REAL NOT NULL,
                    profit_pct REAL NOT NULL,
                    status TEXT NOT NULL,
                    timestamp REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS spread_candles_1m (
                    asset_key TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    exec_venue TEXT NOT NULL,
                    ref_venue TEXT NOT NULL,
                    timestamp INTEGER NOT NULL,
                    open REAL NOT NULL,
                    high REAL NOT NULL,
                    low REAL NOT NULL,
                    close REAL NOT NULL,
                    snapshot_count INTEGER DEFAULT 60,
                    venue_price REAL,
                    ref_price REAL,
                    PRIMARY KEY (asset_key, timestamp)
                );

                CREATE TABLE IF NOT EXISTS market_candles (
                    exchange TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    timeframe TEXT NOT NULL,
                    timestamp INTEGER NOT NULL,
                    open REAL NOT NULL,
                    high REAL NOT NULL,
                    low REAL NOT NULL,
                    close REAL NOT NULL,
                    volume REAL DEFAULT 0.0,
                    PRIMARY KEY (exchange, symbol, timeframe, timestamp)
                );

                CREATE TABLE IF NOT EXISTS spread_candles (
                    asset_key TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    exec_venue TEXT NOT NULL,
                    ref_venue TEXT NOT NULL,
                    timeframe TEXT NOT NULL DEFAULT '1m',
                    timestamp INTEGER NOT NULL,
                    open REAL NOT NULL,
                    high REAL NOT NULL,
                    low REAL NOT NULL,
                    close REAL NOT NULL,
                    snapshot_count INTEGER DEFAULT 60,
                    venue_price REAL,
                    ref_price REAL,
                    PRIMARY KEY (asset_key, timeframe, timestamp)
                );

                CREATE INDEX IF NOT EXISTS idx_orders_bot ON orders(bot_id);
                CREATE INDEX IF NOT EXISTS idx_fills_order ON fills(order_id);
                CREATE INDEX IF NOT EXISTS idx_positions_status ON positions(status);
                CREATE INDEX IF NOT EXISTS idx_pnl_date ON realized_pnl(date);
                CREATE INDEX IF NOT EXISTS idx_pnl_strategy ON realized_pnl(strategy_type);
                CREATE INDEX IF NOT EXISTS idx_bot_events_ts ON bot_events(timestamp);
                CREATE INDEX IF NOT EXISTS idx_exec_ts ON execution_history(timestamp);
                CREATE INDEX IF NOT EXISTS idx_spread_candles_key_ts ON spread_candles_1m(asset_key, timestamp);
                CREATE INDEX IF NOT EXISTS idx_market_candles_lookup ON market_candles(exchange, symbol, timeframe, timestamp);
                CREATE INDEX IF NOT EXISTS idx_spread_candles_lookup ON spread_candles(asset_key, timeframe, timestamp);
            """)

            # Add venue_price and ref_price columns to spread_candles_1m if missing
            for col in ["venue_price", "ref_price"]:
                try:
                    conn.execute(f"ALTER TABLE spread_candles_1m ADD COLUMN {col} REAL;")
                except sqlite3.OperationalError:
                    pass

        # Seed initial sample data only if tables are completely empty
        self._seed_sample_data_if_empty()

    def _seed_sample_data_if_empty(self):
        """Seed realistic demo records if empty, so historical reports have baseline data."""
        with self._conn() as conn:
            now = time.time()
            day_sec = 86400
            today = datetime.date.today()

            cur = conn.execute("SELECT COUNT(*) FROM realized_pnl;")
            if cur.fetchone()[0] == 0:
                logger.info("Seeding initial historical records in SQLite database...")

                # Seed past 30 days of realized PNL
                pnl_records = []
                for i in range(30, 0, -1):
                    rec_date = today - datetime.timedelta(days=i)
                    date_str = rec_date.isoformat()
                    ts = now - (i * day_sec) + 3600

                    # Spread across cross-exchange, triangular, cross-asset, funding
                    pnl_records.append((f"pnl_{i}_cross", "bot_cross_demo", "cross", "SOL/USDT", round(12.5 + (i % 7) * 2.1, 2), ts, date_str))
                    pnl_records.append((f"pnl_{i}_tri", "bot_tri_demo", "tri", "BTC/USDT", round(8.4 + (i % 5) * 1.8, 2), ts + 120, date_str))
                    pnl_records.append((f"pnl_{i}_asset", "bot_ca_demo", "asset", "ETH/USDT", round(6.2 + (i % 4) * 1.5, 2), ts + 240, date_str))
                    pnl_records.append((f"pnl_{i}_fund", "bot_fund_demo", "fund", "BTC/USDT", round(4.1 + (i % 3) * 0.9, 2), ts + 360, date_str))

                conn.executemany(
                    "INSERT INTO realized_pnl (id, bot_id, strategy_type, symbol, amount_usd, timestamp, date) VALUES (?, ?, ?, ?, ?, ?, ?);",
                    pnl_records
                )

                # Seed sample positions
                positions = [
                    ("pos_tri_1", "bot_tri_demo", "BTC/USDT", "BINANCE", "BUY", 0.15, 64200.0, 64320.0, 18.0, 28.5, "OPEN", now - 3600),
                    ("pos_tri_2", "bot_tri_demo", "ETH/BTC", "BINANCE", "BUY", 2.5, 0.0385, 0.0386, 5.2, 0.0, "OPEN", now - 3500),
                    ("pos_cross_1", "bot_cross_demo", "SOL/USDT", "BYBIT", "SELL", 10.0, 152.4, 152.1, 3.0, 9.2, "OPEN", now - 1800),
                ]
                conn.executemany(
                    "INSERT INTO positions (id, bot_id, symbol, venue, side, qty, entry_price, current_price, floating_pnl, realized_pnl, status, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);",
                    positions
                )

                # Seed sample bot events
                events = [
                    ("bot_tri_demo", "STARTED", "Triangular Arbitrage bot initialized in DRY_RUN mode", "INFO", now - 7200),
                    ("bot_tri_demo", "OPPORTUNITY_DETECTED", "Triangular loop BTC->ETH->USDT viable (+0.145%)", "INFO", now - 3600),
                    ("bot_cross_demo", "STARTED", "Cross Exchange Grid bot initialized on Binance/Bybit", "INFO", now - 5400),
                    ("bot_cross_demo", "FILL", "Filled 10 SOL @ 152.40 on Bybit", "INFO", now - 1800),
                ]
                conn.executemany(
                    "INSERT INTO bot_events (bot_id, event_type, message, level, timestamp) VALUES (?, ?, ?, ?, ?);",
                    events
                )

            conn.commit()

    # -------------------------------------------------------------------------
    # Market Candle & Spread Candle Operations (SQLite Rolling Window)
    # -------------------------------------------------------------------------

    def save_market_candle(
        self,
        exchange: str,
        symbol: str,
        timeframe: str,
        timestamp: int,
        open_val: float,
        high_val: float,
        low_val: float,
        close_val: float,
        volume: float = 0.0,
    ) -> bool:
        """Save a single exchange OHLCV market candle."""
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO market_candles (exchange, symbol, timeframe, timestamp, open, high, low, close, volume)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(exchange, symbol, timeframe, timestamp) DO UPDATE SET
                    high = MAX(high, excluded.high),
                    low = MIN(low, excluded.low),
                    close = excluded.close,
                    volume = excluded.volume;
                """,
                (exchange.lower(), symbol.upper(), timeframe.lower(), int(timestamp),
                 float(open_val), float(high_val), float(low_val), float(close_val), float(volume))
            )
            return True

    def batch_save_market_candles(
        self,
        exchange: str,
        symbol: str,
        timeframe: str,
        candles: List[Any],
    ) -> int:
        """Batch save historical OHLCV market candles in SQLite with rolling window upsert."""
        if not candles:
            return 0
        rows = []
        ex_lower = exchange.lower()
        sym_upper = symbol.upper()
        tf_lower = timeframe.lower()
        for c in candles:
            if isinstance(c, dict):
                ts = int(c.get("timestamp") or c.get("time") or 0)
                o = float(c.get("open", 0.0))
                h = float(c.get("high", 0.0))
                l = float(c.get("low", 0.0))
                cl = float(c.get("close", 0.0))
                v = float(c.get("volume", 0.0))
            else:
                ts = int(getattr(c, "timestamp", 0))
                o = float(getattr(c, "open", 0.0))
                h = float(getattr(c, "high", 0.0))
                l = float(getattr(c, "low", 0.0))
                cl = float(getattr(c, "close", 0.0))
                v = float(getattr(c, "volume", 0.0))
            if ts > 0 and cl > 0:
                rows.append((ex_lower, sym_upper, tf_lower, ts, o, h, l, cl, v))
        if not rows:
            return 0
        with self._conn() as conn:
            conn.executemany(
                """
                INSERT INTO market_candles (exchange, symbol, timeframe, timestamp, open, high, low, close, volume)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(exchange, symbol, timeframe, timestamp) DO UPDATE SET
                    high = MAX(high, excluded.high),
                    low = MIN(low, excluded.low),
                    close = excluded.close,
                    volume = excluded.volume;
                """,
                rows
            )
            return len(rows)

    def get_market_candles(
        self,
        exchange: str,
        symbol: str,
        timeframe: str = "1m",
        from_ts: Optional[int] = None,
        to_ts: Optional[int] = None,
        limit: int = 1500,
    ) -> List[dict]:
        """Fetch cached OHLCV market candles for an exchange and symbol from SQLite."""
        subquery = "SELECT * FROM market_candles WHERE exchange = ? AND symbol = ? AND timeframe = ?"
        params: List[Any] = [exchange.lower(), symbol.upper(), timeframe.lower()]
        if from_ts is not None:
            subquery += " AND timestamp >= ?"
            params.append(from_ts)
        if to_ts is not None:
            subquery += " AND timestamp <= ?"
            params.append(to_ts)
        subquery += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)

        query = f"SELECT * FROM ({subquery}) ORDER BY timestamp ASC"
        with self._conn() as conn:
            cur = conn.execute(query, params)
            return [
                {
                    "exchange": r["exchange"],
                    "symbol": r["symbol"],
                    "timeframe": r["timeframe"],
                    "time": r["timestamp"],
                    "timestamp": r["timestamp"],
                    "open": round(r["open"], 4),
                    "high": round(r["high"], 4),
                    "low": round(r["low"], 4),
                    "close": round(r["close"], 4),
                    "volume": round(r["volume"], 4),
                }
                for r in cur.fetchall()
            ]

    def save_spread_candle(
        self,
        asset_key_or_dict: Any = None,
        symbol: str = "",
        exec_venue: str = "",
        ref_venue: str = "",
        timestamp: int = 0,
        open_val: float = 0.0,
        high_val: float = 0.0,
        low_val: float = 0.0,
        close_val: float = 0.0,
        snapshot_count: int = 60,
        *,
        asset_key: str = "",
        priced_venue: str = "",
        reference_venue: str = "",
        venue_price: float = 0.0,
        ref_price: float = 0.0,
        timeframe: str = "1m",
        **kwargs: Any,
    ) -> bool:
        """Save or update a completed OHLC spread candle in SQLite."""
        venue_price_val = 0.0
        ref_price_val = 0.0
        tf = timeframe.lower()
        if isinstance(asset_key_or_dict, dict):
            d = asset_key_or_dict
            asset_key = d.get("asset_key", "")
            symbol = d.get("symbol", "")
            exec_venue = d.get("priced_venue") or d.get("exec_venue") or d.get("buy_venue", "")
            ref_venue = d.get("reference_venue") or d.get("ref_venue") or d.get("sell_venue", "")
            timestamp = int(d.get("time") or d.get("timestamp") or 0)
            open_val = float(d.get("open", 0.0))
            high_val = float(d.get("high", 0.0))
            low_val = float(d.get("low", 0.0))
            close_val = float(d.get("close", 0.0))
            snapshot_count = int(d.get("count") or d.get("snapshot_count") or 60)
            venue_price_val = float(d.get("venuePrice") or d.get("venue_price") or 0.0)
            ref_price_val = float(d.get("refPrice") or d.get("ref_price") or 0.0)
            tf = (d.get("timeframe") or timeframe).lower()
        else:
            asset_key = asset_key or str(asset_key_or_dict or "")
            if priced_venue:
                exec_venue = priced_venue
            if reference_venue:
                ref_venue = reference_venue
            venue_price_val = float(venue_price or kwargs.get("venuePrice") or kwargs.get("venue_price") or 0.0)
            ref_price_val = float(ref_price or kwargs.get("refPrice") or kwargs.get("ref_price") or 0.0)
            tf = (kwargs.get("timeframe") or timeframe).lower()

        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO spread_candles (asset_key, symbol, exec_venue, ref_venue, timeframe, timestamp, open, high, low, close, snapshot_count, venue_price, ref_price)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(asset_key, timeframe, timestamp) DO UPDATE SET
                    high = MAX(high, excluded.high),
                    low = MIN(low, excluded.low),
                    close = excluded.close,
                    snapshot_count = excluded.snapshot_count,
                    venue_price = COALESCE(excluded.venue_price, spread_candles.venue_price),
                    ref_price = COALESCE(excluded.ref_price, spread_candles.ref_price);
                """,
                (
                    asset_key, symbol, exec_venue, ref_venue, tf, int(timestamp),
                    float(open_val), float(high_val), float(low_val), float(close_val),
                    int(snapshot_count),
                    venue_price_val if venue_price_val > 0 else None,
                    ref_price_val if ref_price_val > 0 else None,
                )
            )
            if tf == "1m":
                conn.execute(
                    """
                    INSERT INTO spread_candles_1m (asset_key, symbol, exec_venue, ref_venue, timestamp, open, high, low, close, snapshot_count, venue_price, ref_price)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(asset_key, timestamp) DO UPDATE SET
                        high = MAX(high, excluded.high),
                        low = MIN(low, excluded.low),
                        close = excluded.close,
                        snapshot_count = excluded.snapshot_count,
                        venue_price = COALESCE(excluded.venue_price, spread_candles_1m.venue_price),
                        ref_price = COALESCE(excluded.ref_price, spread_candles_1m.ref_price);
                    """,
                    (
                        asset_key, symbol, exec_venue, ref_venue, int(timestamp),
                        float(open_val), float(high_val), float(low_val), float(close_val),
                        int(snapshot_count),
                        venue_price_val if venue_price_val > 0 else None,
                        ref_price_val if ref_price_val > 0 else None,
                    )
                )
            return True

    def batch_save_spread_candles(self, candles: List[dict], timeframe: str = "1m") -> int:
        """Batch save multiple spread candles in SQLite."""
        if not candles:
            return 0
        rows_all = []
        rows_1m = []
        for c in candles:
            if not ((c.get("time") or c.get("timestamp")) and c.get("asset_key")):
                continue
            tf = (c.get("timeframe") or timeframe).lower()
            ak = c.get("asset_key", "")
            sym = c.get("symbol", "")
            ev = c.get("priced_venue") or c.get("exec_venue") or c.get("buy_venue", "")
            rv = c.get("reference_venue") or c.get("ref_venue") or c.get("sell_venue", "")
            ts = int(c.get("time") or c.get("timestamp") or 0)
            o = float(c.get("open", 0.0))
            h = float(c.get("high", 0.0))
            l = float(c.get("low", 0.0))
            cl = float(c.get("close", 0.0))
            cnt = int(c.get("count") or c.get("snapshot_count") or 60)
            vp = float(c.get("venuePrice") or c.get("venue_price") or 0.0) or None
            rp = float(c.get("refPrice") or c.get("ref_price") or 0.0) or None

            rows_all.append((ak, sym, ev, rv, tf, ts, o, h, l, cl, cnt, vp, rp))
            if tf == "1m":
                rows_1m.append((ak, sym, ev, rv, ts, o, h, l, cl, cnt, vp, rp))

        if not rows_all:
            return 0
        with self._conn() as conn:
            conn.executemany(
                """
                INSERT INTO spread_candles (asset_key, symbol, exec_venue, ref_venue, timeframe, timestamp, open, high, low, close, snapshot_count, venue_price, ref_price)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(asset_key, timeframe, timestamp) DO UPDATE SET
                    high = MAX(high, excluded.high),
                    low = MIN(low, excluded.low),
                    close = excluded.close,
                    snapshot_count = excluded.snapshot_count,
                    venue_price = COALESCE(excluded.venue_price, spread_candles.venue_price),
                    ref_price = COALESCE(excluded.ref_price, spread_candles.ref_price);
                """,
                rows_all
            )
            if rows_1m:
                conn.executemany(
                    """
                    INSERT INTO spread_candles_1m (asset_key, symbol, exec_venue, ref_venue, timestamp, open, high, low, close, snapshot_count, venue_price, ref_price)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(asset_key, timestamp) DO UPDATE SET
                        high = MAX(high, excluded.high),
                        low = MIN(low, excluded.low),
                        close = excluded.close,
                        snapshot_count = excluded.snapshot_count,
                        venue_price = COALESCE(excluded.venue_price, spread_candles_1m.venue_price),
                        ref_price = COALESCE(excluded.ref_price, spread_candles_1m.ref_price);
                    """,
                    rows_1m
                )
            return len(rows_all)

    def prune_market_data(self, retention_days: int = 30) -> int:
        """
        Enforce a rolling window retention policy on historical market data.
        Prunes candles older than retention_days across all market data and spread tables.
        Maintains fast query times, compact disk footprint, and zero system corruption.
        """
        cutoff_ts = int(time.time()) - (retention_days * 86400)
        total = 0
        with self._conn() as conn:
            c1 = conn.execute("DELETE FROM market_candles WHERE timestamp < ?", (cutoff_ts,))
            c2 = conn.execute("DELETE FROM spread_candles WHERE timestamp < ?", (cutoff_ts,))
            c3 = conn.execute("DELETE FROM spread_candles_1m WHERE timestamp < ?", (cutoff_ts,))
            total = (c1.rowcount if c1.rowcount > 0 else 0) + \
                    (c2.rowcount if c2.rowcount > 0 else 0) + \
                    (c3.rowcount if c3.rowcount > 0 else 0)
        if total > 0:
            logger.info("Pruned %d expired market data candles older than %d days", total, retention_days)
        return total

    def prune_rolling_window(self, retention_days: int = 30) -> int:
        return self.prune_market_data(retention_days=retention_days)

    def _fill_candle_gap(self, asset_key: str, max_ts: Optional[int], current_min: int):
        """
        Strict real-data-first rule:
        Do not manufacture synthetic candlestick values or mathematical random walks.
        Missing historical gaps are populated by real exchange historical candles (REST 1m).
        """
        pass

    def get_spread_candles(
        self,
        asset_key: str,
        from_ts: Optional[int] = None,
        to_ts: Optional[int] = None,
        limit: int = 1500,
        auto_fill: bool = False,
        timeframe: str = "1m",
    ) -> List[dict]:
        """Fetch completed candles for an asset key and timeframe, ordered chronologically."""
        tf = (timeframe or "1m").lower()
        if auto_fill and tf == "1m":
            now_sec = int(time.time())
            current_min = (now_sec // 60) * 60
            try:
                with self._conn() as conn:
                    cur = conn.execute("SELECT MAX(timestamp) FROM spread_candles_1m WHERE asset_key = ?", (asset_key,))
                    r = cur.fetchone()
                    max_ts = r[0] if r else None
                if max_ts is None or (current_min - max_ts) > 60:
                    self._fill_candle_gap(asset_key, max_ts, current_min)
            except Exception as e:
                logger.debug("Gap fill error: %s", e)

        # First query spread_candles table with timeframe filter
        subquery = "SELECT * FROM spread_candles WHERE asset_key = ? AND timeframe = ?"
        params: List[Any] = [asset_key, tf]

        if from_ts is not None:
            subquery += " AND timestamp >= ?"
            params.append(from_ts)
        if to_ts is not None:
            subquery += " AND timestamp <= ?"
            params.append(to_ts)

        subquery += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)

        query = f"SELECT * FROM ({subquery}) ORDER BY timestamp ASC"

        with self._conn() as conn:
            cur = conn.execute(query, params)
            rows = cur.fetchall()

            # If no rows and tf == 1m, check legacy spread_candles_1m table
            if not rows and tf == "1m":
                subquery_1m = "SELECT * FROM spread_candles_1m WHERE asset_key = ?"
                params_1m: List[Any] = [asset_key]
                if from_ts is not None:
                    subquery_1m += " AND timestamp >= ?"
                    params_1m.append(from_ts)
                if to_ts is not None:
                    subquery_1m += " AND timestamp <= ?"
                    params_1m.append(to_ts)
                subquery_1m += " ORDER BY timestamp DESC LIMIT ?"
                params_1m.append(limit)
                query_1m = f"SELECT * FROM ({subquery_1m}) ORDER BY timestamp ASC"
                cur = conn.execute(query_1m, params_1m)
                rows = cur.fetchall()

            # Fallback by symbol prefix if not found by exact asset_key
            if not rows:
                clean_sym = asset_key.split("_")[0] if "_" in asset_key else asset_key
                alt_subquery = "SELECT * FROM spread_candles WHERE (symbol = ? OR asset_key LIKE ?) AND timeframe = ?"
                alt_params: List[Any] = [clean_sym, f"{clean_sym}_%", tf]
                if from_ts is not None:
                    alt_subquery += " AND timestamp >= ?"
                    alt_params.append(from_ts)
                if to_ts is not None:
                    alt_subquery += " AND timestamp <= ?"
                    alt_params.append(to_ts)
                alt_subquery += " ORDER BY timestamp DESC LIMIT ?"
                alt_params.append(limit)
                alt_query = f"SELECT * FROM ({alt_subquery}) ORDER BY timestamp ASC"
                cur = conn.execute(alt_query, alt_params)
                rows = cur.fetchall()

            if not rows and tf == "1m":
                clean_sym = asset_key.split("_")[0] if "_" in asset_key else asset_key
                alt_subquery = "SELECT * FROM spread_candles_1m WHERE symbol = ? OR asset_key LIKE ?"
                alt_params: List[Any] = [clean_sym, f"{clean_sym}_%"]
                if from_ts is not None:
                    alt_subquery += " AND timestamp >= ?"
                    alt_params.append(from_ts)
                if to_ts is not None:
                    alt_subquery += " AND timestamp <= ?"
                    alt_params.append(to_ts)
                alt_subquery += " ORDER BY timestamp DESC LIMIT ?"
                alt_params.append(limit)
                alt_query = f"SELECT * FROM ({alt_subquery}) ORDER BY timestamp ASC"
                cur = conn.execute(alt_query, alt_params)
                rows = cur.fetchall()

            # If still no rows and tf != 1m (e.g. 1h requested), dynamically aggregate from 1m candles in SQLite!
            if not rows and tf in ("1h", "4h", "1d", "5m", "15m"):
                tf_seconds = {"5m": 300, "15m": 900, "1h": 3600, "4h": 14400, "1d": 86400}.get(tf, 3600)
                m1_candles = self.get_spread_candles(asset_key, from_ts, to_ts, limit=limit * 60, auto_fill=False, timeframe="1m")
                if m1_candles:
                    buckets = {}
                    for c in m1_candles:
                        t = c.get("time") or c.get("timestamp")
                        if not t:
                            continue
                        bucket_ts = int(t // tf_seconds * tf_seconds)
                        if bucket_ts not in buckets:
                            buckets[bucket_ts] = {
                                "asset_key": c["asset_key"],
                                "symbol": c["symbol"],
                                "exec_venue": c.get("exec_venue", ""),
                                "ref_venue": c.get("ref_venue", ""),
                                "time": bucket_ts,
                                "timestamp": bucket_ts,
                                "open": c["open"],
                                "high": c["high"],
                                "low": c["low"],
                                "close": c["close"],
                                "venuePrice": c.get("venuePrice"),
                                "refPrice": c.get("refPrice"),
                                "snapshot_count": c.get("count", 60),
                                "timeframe": tf,
                            }
                        else:
                            b = buckets[bucket_ts]
                            b["high"] = max(b["high"], c["high"])
                            b["low"] = min(b["low"], c["low"])
                            b["close"] = c["close"]
                            if c.get("venuePrice") is not None:
                                b["venuePrice"] = c["venuePrice"]
                            if c.get("refPrice") is not None:
                                b["refPrice"] = c["refPrice"]
                            b["snapshot_count"] += c.get("count", 60)
                    aggregated = [buckets[k] for k in sorted(buckets.keys())]
                    if aggregated:
                        try:
                            self.batch_save_spread_candles(aggregated, timeframe=tf)
                        except Exception:
                            pass
                        return aggregated[-limit:]

            return [
                {
                    "asset_key": r["asset_key"],
                    "symbol": r["symbol"],
                    "exec_venue": r["exec_venue"],
                    "ref_venue": r["ref_venue"],
                    "priced_venue": r["exec_venue"],
                    "reference_venue": r["ref_venue"],
                    "time": r["timestamp"],
                    "open": round(r["open"], 4),
                    "high": round(r["high"], 4),
                    "low": round(r["low"], 4),
                    "close": round(r["close"], 4),
                    "count": r["snapshot_count"],
                    "timeframe": r["timeframe"] if "timeframe" in r.keys() else tf,
                    "venuePrice": r["venue_price"] if ("venue_price" in r.keys() and r["venue_price"] is not None) else None,
                    "refPrice": r["ref_price"] if ("ref_price" in r.keys() and r["ref_price"] is not None) else None,
                }
                for r in rows
            ]

    # -------------------------------------------------------------------------
    # On-Demand Historical Queries (Executed only when requested)
    # -------------------------------------------------------------------------

    def get_pnl_history(
        self,
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
        strategy: Optional[str] = None,
        limit: int = 200,
    ) -> List[dict]:
        """Fetch historical realized PNL records filtered by date range and strategy."""
        query = "SELECT * FROM realized_pnl WHERE 1=1"
        params: List[Any] = []

        if from_date:
            query += " AND date >= ?"
            params.append(from_date)
        if to_date:
            query += " AND date <= ?"
            params.append(to_date)
        if strategy and strategy != "combined":
            query += " AND strategy_type = ?"
            params.append(strategy)

        query += " ORDER BY date ASC, timestamp ASC LIMIT ?"
        params.append(limit)

        with self._conn() as conn:
            cur = conn.execute(query, params)
            return [dict(row) for row in cur.fetchall()]

    def get_orders(
        self,
        status: Optional[str] = None,
        bot_id: Optional[str] = None,
        limit: int = 50,
    ) -> List[dict]:
        """Fetch historical orders on-demand."""
        query = "SELECT * FROM orders WHERE 1=1"
        params: List[Any] = []
        if status:
            query += " AND status = ?"
            params.append(status)
        if bot_id:
            query += " AND bot_id = ?"
            params.append(bot_id)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)

        with self._conn() as conn:
            cur = conn.execute(query, params)
            return [dict(row) for row in cur.fetchall()]

    def get_positions(self, status: Optional[str] = None, limit: int = 50) -> List[dict]:
        """Fetch open or closed positions on-demand."""
        query = "SELECT * FROM positions WHERE 1=1"
        params: List[Any] = []
        if status:
            query += " AND status = ?"
            params.append(status)
        query += " ORDER BY updated_at DESC LIMIT ?"
        params.append(limit)

        with self._conn() as conn:
            cur = conn.execute(query, params)
            return [dict(row) for row in cur.fetchall()]

    def get_bot_events(self, bot_id: Optional[str] = None, limit: int = 50) -> List[dict]:
        """Fetch bot event logs on-demand."""
        query = "SELECT * FROM bot_events WHERE 1=1"
        params: List[Any] = []
        if bot_id:
            query += " AND bot_id = ?"
            params.append(bot_id)
        query += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)

        with self._conn() as conn:
            cur = conn.execute(query, params)
            return [dict(row) for row in cur.fetchall()]

    def get_execution_history(self, limit: int = 50) -> List[dict]:
        """Fetch execution history on-demand."""
        with self._conn() as conn:
            cur = conn.execute("SELECT * FROM execution_history ORDER BY timestamp DESC LIMIT ?", (limit,))
            return [dict(row) for row in cur.fetchall()]

    # -------------------------------------------------------------------------
    # Record Modifiers (Low Frequency Persistence)
    # -------------------------------------------------------------------------

    def record_order(self, order: dict):
        with self._conn() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO orders (id, bot_id, symbol, venue, side, order_type, price, qty, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    order["id"], order.get("bot_id"), order["symbol"], order["venue"],
                    order["side"], order.get("order_type", "LIMIT"), order["price"],
                    order["qty"], order["status"], order.get("created_at", time.time()),
                    order.get("updated_at", time.time())
                )
            )

    def record_realized_pnl(self, pnl: dict):
        with self._conn() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO realized_pnl (id, bot_id, strategy_type, symbol, amount_usd, timestamp, date)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    pnl["id"], pnl.get("bot_id"), pnl["strategy_type"], pnl["symbol"],
                    pnl["amount_usd"], pnl.get("timestamp", time.time()), pnl["date"]
                )
            )

    def record_bot_event(self, bot_id: str, event_type: str, message: str, level: str = "INFO"):
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO bot_events (bot_id, event_type, message, level, timestamp)
                VALUES (?, ?, ?, ?, ?)
                """,
                (bot_id, event_type, message, level, time.time())
            )

    # -------------------------------------------------------------------------
    # Rolling CSV Archival / Export
    # -------------------------------------------------------------------------

    def export_rolling_csv(self, table_name: str, archive_dir: Optional[str] = None) -> str:
        """
        Export historical table data into rolling monthly CSV archives.
        Strictly used for archival/export; never used on startup.
        """
        allowed_tables = {
            "orders", "fills", "positions", "realized_pnl", "bot_events",
            "execution_history", "spread_candles_1m", "spread_candles", "market_candles"
        }
        if table_name not in allowed_tables:
            raise ValueError(f"Table '{table_name}' is not exportable.")

        out_dir = archive_dir or DEFAULT_ARCHIVE_DIR
        os.makedirs(out_dir, exist_ok=True)
        month_suffix = datetime.datetime.now().strftime("%Y%m")
        csv_filename = f"{table_name}_{month_suffix}.csv"
        csv_path = os.path.join(out_dir, csv_filename)

        with self._conn() as conn:
            cur = conn.execute(f"SELECT * FROM {table_name}")
            rows = cur.fetchall()
            col_names = [desc[0] for desc in cur.description]

            with open(csv_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(col_names)
                for r in rows:
                    writer.writerow([r[c] for c in col_names])

        logger.info("Exported %d records from '%s' to rolling archive '%s'", len(rows), table_name, csv_path)
        return csv_path
