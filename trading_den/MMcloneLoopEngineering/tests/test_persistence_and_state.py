"""
Unit tests for In-Memory Live State, SQLite Persistence Layer, and Non-blocking Startup.
"""

import asyncio
import os
import shutil
import tempfile
import time
import unittest

from arbitrage.live_state import LiveStateManager
from arbitrage.models import DataSourceType, OrderBook, OrderBookEntry, Ticker, FundingRate
from arbitrage.persistence import PersistenceManager
from arbitrage.server import ArbitrageServer


class TestLiveState(unittest.TestCase):

    def setUp(self):
        self.state = LiveStateManager()

    def test_in_memory_book_storage_and_versioning(self):
        v0 = self.state.version
        book1 = OrderBook(
            symbol="BTC/USDT",
            venue="BINANCE",
            bids=[OrderBookEntry(60000.0, 1.0)],
            asks=[OrderBookEntry(60001.0, 1.0)],
            timestamp=time.time(),
            source=DataSourceType.NATIVE_WS.value,
        )
        changed = self.state.set_order_book("BINANCE", "BTC/USDT", book1)
        self.assertTrue(changed)
        self.assertGreater(self.state.version, v0)

        # Retrieve book
        retrieved = self.state.get_order_book("BINANCE", "BTC/USDT")
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved.best_bid, 60000.0)

        # Same book shouldn't mark changed
        v1 = self.state.version
        changed2 = self.state.set_order_book("BINANCE", "BTC/USDT", book1)
        self.assertFalse(changed2)
        self.assertEqual(self.state.version, v1)

    def test_live_snapshot_generation(self):
        self.state.set_opportunities([{"symbol": "BTC/USDT", "pct": 0.5}])
        snapshot = self.state.get_live_snapshot(connector_status={"status": "ok"}, connected_clients=2)
        self.assertEqual(snapshot["type"], "live_snapshot")
        self.assertEqual(len(snapshot["opportunities"]), 1)
        self.assertEqual(snapshot["connected_clients"], 2)


class TestPersistenceManager(unittest.TestCase):

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.test_dir, "test_history.db")
        self.pm = PersistenceManager(db_path=self.db_path)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_initial_seed_and_on_demand_pnl(self):
        # Initial DB was empty, so it seeded sample records
        records = self.pm.get_pnl_history(limit=50)
        self.assertGreater(len(records), 0)
        self.assertIn("amount_usd", records[0])
        self.assertIn("strategy_type", records[0])

    def test_pnl_strategy_filter(self):
        records = self.pm.get_pnl_history(strategy="tri", limit=50)
        for r in records:
            self.assertEqual(r["strategy_type"], "tri")

    def test_record_and_query_order_and_event(self):
        order = {
            "id": "ord_test_1",
            "bot_id": "bot_1",
            "symbol": "ETH/USDT",
            "venue": "BINANCE",
            "side": "BUY",
            "order_type": "LIMIT",
            "price": 2500.0,
            "qty": 1.5,
            "status": "FILLED",
        }
        self.pm.record_order(order)
        orders = self.pm.get_orders(bot_id="bot_1")
        self.assertEqual(len(orders), 1)
        self.assertEqual(orders[0]["id"], "ord_test_1")
        self.assertEqual(orders[0]["price"], 2500.0)

        self.pm.record_bot_event("bot_1", "TEST_EVENT", "Test event message", "INFO")
        events = self.pm.get_bot_events(bot_id="bot_1")
        self.assertTrue(any(e["message"] == "Test event message" for e in events))

    def test_spread_candles_save_and_retrieve(self):
        # 1. Real-data-first: initially no synthetic candles are seeded
        seeded = self.pm.get_spread_candles("BTCUSDT_MEXC_Perp")
        self.assertEqual(len(seeded), 0)

        # 2. Save single candle
        test_ts = 1727600000
        saved = self.pm.save_spread_candle({
            "asset_key": "TEST_ASSET_1",
            "symbol": "TESTUSDT",
            "timestamp": test_ts,
            "open": 0.10,
            "high": 0.15,
            "low": 0.08,
            "close": 0.12,
            "buy_venue": "Binance",
            "sell_venue": "Bybit",
            "ref_price": 100.0,
            "venue_price": 100.12,
        })
        self.assertTrue(saved)

        # 3. Retrieve
        candles = self.pm.get_spread_candles("TEST_ASSET_1")
        self.assertEqual(len(candles), 1)
        self.assertEqual(candles[0]["time"], test_ts)
        self.assertEqual(candles[0]["open"], 0.10)
        self.assertEqual(candles[0]["close"], 0.12)

        # 4. De-duplication / update of existing candle
        updated = self.pm.save_spread_candle({
            "asset_key": "TEST_ASSET_1",
            "symbol": "TESTUSDT",
            "timestamp": test_ts,
            "open": 0.10,
            "high": 0.20,
            "low": 0.08,
            "close": 0.18,
            "buy_venue": "Binance",
            "sell_venue": "Bybit",
            "ref_price": 100.0,
            "venue_price": 100.18,
        })
        self.assertTrue(updated)
        candles_after = self.pm.get_spread_candles("TEST_ASSET_1")
        self.assertEqual(len(candles_after), 1)  # No duplicate row inserted!
        self.assertEqual(candles_after[0]["high"], 0.20)
        self.assertEqual(candles_after[0]["close"], 0.18)

        # 5. Batch save
        batch = [
            {"asset_key": "TEST_ASSET_1", "symbol": "TESTUSDT", "timestamp": test_ts + 60, "open": 0.18, "high": 0.22, "low": 0.15, "close": 0.20},
            {"asset_key": "TEST_ASSET_1", "symbol": "TESTUSDT", "timestamp": test_ts + 120, "open": 0.20, "high": 0.25, "low": 0.19, "close": 0.24},
        ]
        count = self.pm.batch_save_spread_candles(batch)
        self.assertEqual(count, 2)
        all_test = self.pm.get_spread_candles("TEST_ASSET_1")
        self.assertEqual(len(all_test), 3)

        # 6. CSV export includes spread_candles_1m
        csv_path = self.pm.export_rolling_csv("spread_candles_1m", archive_dir=self.test_dir)
        self.assertTrue(os.path.exists(csv_path))

    def test_market_candles_save_and_retrieve(self):
        ts = int(time.time())
        candles = [
            {"time": ts - 120, "open": 64000.0, "high": 64100.0, "low": 63900.0, "close": 64050.0, "volume": 12.5},
            {"time": ts - 60, "open": 64050.0, "high": 64200.0, "low": 64000.0, "close": 64150.0, "volume": 8.0},
        ]
        saved = self.pm.batch_save_market_candles("binance", "BTC/USDT", "1m", candles)
        self.assertEqual(saved, 2)

        res = self.pm.get_market_candles("binance", "BTC/USDT", "1m", limit=10)
        self.assertEqual(len(res), 2)
        self.assertEqual(res[0]["close"], 64050.0)
        self.assertEqual(res[1]["close"], 64150.0)

    def test_prune_market_data_rolling_window(self):
        now = int(time.time())
        old_ts = now - (35 * 86400) # 35 days ago
        recent_ts = now - 3600 # 1 hour ago

        self.pm.save_market_candle("binance", "ETH/USDT", "1m", old_ts, 3500.0, 3510.0, 3490.0, 3505.0)
        self.pm.save_market_candle("binance", "ETH/USDT", "1m", recent_ts, 3600.0, 3610.0, 3590.0, 3605.0)

        pruned = self.pm.prune_market_data(retention_days=30)
        self.assertGreaterEqual(pruned, 1)

        res = self.pm.get_market_candles("binance", "ETH/USDT", "1m", limit=10)
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["time"], recent_ts)



class TestServerImmediateStartup(unittest.IsolatedAsyncioTestCase):

    async def test_server_binds_and_responds_fast(self):
        # Pick high random port
        port = 18889
        server = ArbitrageServer(host="127.0.0.1", port=port)
        await server.start()

        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            req = b"GET /api/status HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n"
            writer.write(req)
            await writer.drain()

            resp_data = await reader.read(4096)
            writer.close()
            await writer.wait_closed()

            self.assertIn(b"200 OK", resp_data)
            self.assertIn(b"online", resp_data)
        finally:
            await server.stop()

    async def test_server_spread_candles_endpoints(self):
        port = 18890
        server = ArbitrageServer(host="127.0.0.1", port=port)
        await server.start()

        try:
            # 1. GET /api/history/spread-candles
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            req = b"GET /api/history/spread-candles?asset_key=BTCUSDT_MEXC_Perp HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n"
            writer.write(req)
            await writer.drain()
            resp = await reader.read(8192)
            writer.close()
            await writer.wait_closed()

            self.assertIn(b"200 OK", resp)
            self.assertIn(b"candles", resp)

            # 2. POST /api/history/spread-candles
            body = b'{"asset_key":"ETHUSDT_Binance_Spot","symbol":"ETHUSDT","candles":[{"time":1727600060,"open":0.02,"high":0.05,"low":0.01,"close":0.04}]}'
            content_len = len(body)
            post_req = (
                f"POST /api/history/spread-candles HTTP/1.1\r\n"
                f"Host: 127.0.0.1\r\n"
                f"Content-Type: application/json\r\n"
                f"Content-Length: {content_len}\r\n"
                f"Connection: close\r\n\r\n"
            ).encode() + body

            reader2, writer2 = await asyncio.open_connection("127.0.0.1", port)
            writer2.write(post_req)
            await writer2.drain()
            resp2 = await reader2.read(4096)
            writer2.close()
            await writer2.wait_closed()

            self.assertIn(b"200 OK", resp2)
            self.assertIn(b'"status": "ok"', resp2)
            self.assertIn(b'"saved": 1', resp2)
        finally:
            await server.stop()


if __name__ == "__main__":
    unittest.main()

