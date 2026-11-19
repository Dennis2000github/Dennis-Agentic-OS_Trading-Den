"""
Unit tests for Market Catalog, Asset Suggestions, and /api/markets REST Endpoint.
Tests instrument type filtering, exchange mapping (MEXC, Binance, Bybit, OKX, Kraken, MT5),
search query filtering, volume formatting, and live state overlay.
"""

import asyncio
import json
import os
import shutil
import tempfile
import time
import unittest
from io import BytesIO

from arbitrage.market_catalog import (
    BASE_MARKET_CATALOG,
    VENUE_INSTRUMENT_TYPES,
    format_volume_usd,
    get_market_items,
)
from arbitrage.models import Ticker
from arbitrage.persistence import PersistenceManager
from arbitrage.server import ArbitrageServer


class TestMarketCatalog(unittest.TestCase):

    def test_format_volume_usd(self):
        self.assertEqual(format_volume_usd(38450000000.0), "$38.45B")
        self.assertEqual(format_volume_usd(12800000000.0), "$12.80B")
        self.assertEqual(format_volume_usd(845200000.0), "$845.20M")
        self.assertEqual(format_volume_usd(120500.0), "$120.50K")
        self.assertEqual(format_volume_usd(50.25), "$50.25")

    def test_venue_instrument_types(self):
        expected_venues = ["MEXC", "Binance", "Bybit", "OKX", "Kraken", "MT5"]
        for v in expected_venues:
            self.assertIn(v, VENUE_INSTRUMENT_TYPES)
            types = VENUE_INSTRUMENT_TYPES[v]
            self.assertGreater(len(types), 0)
            values = [t["value"] for t in types]
            if v == "MT5":
                self.assertIn("CFD", values)
                self.assertIn("Spot", values)
                self.assertIn("Futures", values)
            else:
                self.assertIn("Spot", values)
                self.assertIn("Perp", values)
                self.assertIn("Futures", values)

    def test_mexc_perp_search(self):
        items = get_market_items(exchange="MEXC", inst_type="Perp", search_q="BTC")
        self.assertGreater(len(items), 0)
        btc_item = items[0]
        self.assertEqual(btc_item["symbol"], "BTCUSDT")
        self.assertEqual(btc_item["displayName"], "BTCUSDT Perpetual")
        self.assertEqual(btc_item["instType"], "Perp")
        self.assertEqual(btc_item["exchange"], "MEXC")
        # In real-data architecture: unquoted symbols have no fake volume or prices
        self.assertIn(btc_item["volumeFormatted"], ["—", "$0.00"])
        self.assertIsNone(btc_item["price"])

    def test_mexc_spot_search(self):
        items = get_market_items(exchange="MEXC", inst_type="Spot", search_q="BTC")
        self.assertGreater(len(items), 0)
        btc_item = items[0]
        self.assertEqual(btc_item["symbol"], "BTCUSDT")
        self.assertEqual(btc_item["displayName"], "BTCUSDT Spot")
        self.assertEqual(btc_item["instType"], "Spot")
        self.assertEqual(btc_item["exchange"], "MEXC")
        # In real-data architecture: unquoted symbols have no fake volume or prices
        self.assertIn(btc_item["volumeFormatted"], ["—", "$0.00"])
        self.assertIsNone(btc_item["price"])

    def test_keystroke_filtering_and_sorting(self):
        # Typing "B" should return symbols containing B (BTC, BNB, etc.)
        items = get_market_items(exchange="binance", inst_type="spot", search_q="b")
        self.assertGreater(len(items), 0)
        syms = [it["symbol"] for it in items]
        self.assertTrue(any("BTC" in s for s in syms))
        self.assertTrue(any("BNB" in s for s in syms))

        # Sorted by exact/prefix match then volume descending
        first_item = items[0]
        self.assertTrue(
            first_item["symbol"].startswith("B") or first_item["base"].startswith("B")
        )

    def test_empty_query_returns_top_volume(self):
        items = get_market_items(exchange="mexc", inst_type="perp", search_q="")
        self.assertGreater(len(items), 1)
        # Verify descending order of volume
        for i in range(len(items) - 1):
            self.assertGreaterEqual(items[i]["volume24h"], items[i + 1]["volume24h"])

    def test_mt5_cfd_search(self):
        items = get_market_items(exchange="MT5", inst_type="CFD", search_q="EUR")
        self.assertGreater(len(items), 0)
        self.assertEqual(items[0]["symbol"], "EURUSD")
        self.assertEqual(items[0]["instType"], "CFD")
        self.assertEqual(items[0]["exchange"], "MT5")


class MockWriter:
    """Mock StreamWriter for testing _handle_api_request without opening network ports."""

    def __init__(self):
        self.data = bytearray()
        self.closed = False

    def write(self, data: bytes):
        self.data.extend(data)

    async def drain(self):
        pass

    def close(self):
        self.closed = True


class TestServerMarketEndpoint(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        self.test_dir = tempfile.mkdtemp()
        db_path = os.path.join(self.test_dir, "test_market_db.db")
        pm = PersistenceManager(db_path=db_path)
        self.server = ArbitrageServer(host="127.0.0.1", port=0, persistence=pm)

    async def asyncTearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    async def test_get_markets_catalog_live_overlay(self):
        # 1. Base catalog lookup
        markets = self.server.get_markets_catalog(exchange="mexc", inst_type="perp", search_q="BTC")
        self.assertGreater(len(markets), 0)
        initial_vol = markets[0]["volume24h"]

        # 2. Feed live ticker to live_state
        ticker = Ticker(
            symbol="BTC/USDT",
            venue="MEXC",
            bid=72000.0,
            ask=72001.0,
            last=72000.5,
            volume_24h=99000000000.0,
            change_24h_pct=5.5,
            timestamp=time.time(),
        )
        self.server.live_state.set_ticker("MEXC", "BTC/USDT", ticker)

        # 3. Retrieve catalog with live overlay
        overlaid_markets = self.server.get_markets_catalog(exchange="mexc", inst_type="perp", search_q="BTC")
        btc_m = overlaid_markets[0]
        self.assertEqual(btc_m["price"], 72000.5)
        self.assertEqual(btc_m["volume24h"], 99000000000.0)
        self.assertEqual(btc_m["volumeFormatted"], "$99.00B")
        self.assertEqual(btc_m["change24h"], 5.5)

    async def test_api_markets_endpoint_http(self):
        writer = MockWriter()
        await self.server._handle_api_request(
            path="/api/markets",
            query={"exchange": ["mexc"], "type": ["perp"], "q": ["BTC"], "limit": ["10"]},
            writer=writer,
        )

        response_bytes = bytes(writer.data)
        self.assertIn(b"HTTP/1.1 200 OK", response_bytes)
        self.assertIn(b"Content-Type: application/json", response_bytes)

        body_idx = response_bytes.find(b"\r\n\r\n")
        self.assertNotEqual(body_idx, -1)
        json_body = json.loads(response_bytes[body_idx + 4:].decode("utf-8"))

        self.assertEqual(json_body["status"], "ok")
        self.assertEqual(json_body["exchange"], "MEXC")
        self.assertEqual(json_body["type"], "Perp")
        self.assertEqual(json_body["query"], "BTC")
        self.assertGreater(json_body["count"], 0)
        self.assertIn("supported_types", json_body)
        self.assertEqual(json_body["markets"][0]["symbol"], "BTCUSDT")
        self.assertEqual(json_body["markets"][0]["displayName"], "BTCUSDT Perpetual")
        self.assertIn(json_body["markets"][0]["volumeFormatted"], ["—", "$0.00"])
        self.assertIsNone(json_body["markets"][0]["price"])

    async def test_api_markets_with_real_live_ticker(self):
        """Verify that when real live ticker data arrives, catalog updates with real price and volume."""
        from arbitrage.models import Ticker
        import time

        ticker = Ticker(
            venue="MEXC",
            symbol="BTCUSDT",
            bid=65432.10,
            ask=65433.00,
            last=65432.50,
            volume_24h=1250000000.0,
            timestamp=time.time(),
        )
        self.server.live_state.set_ticker("MEXC", "BTCUSDT", ticker)

        writer = MockWriter()
        await self.server._handle_api_request(
            path="/api/markets",
            query={"exchange": ["mexc"], "type": ["perp"], "q": ["BTC"], "limit": ["10"]},
            writer=writer,
        )

        response_bytes = bytes(writer.data)
        body_idx = response_bytes.find(b"\r\n\r\n")
        json_body = json.loads(response_bytes[body_idx + 4:].decode("utf-8"))

        btc_market = json_body["markets"][0]
        self.assertEqual(btc_market["symbol"], "BTCUSDT")
        self.assertEqual(btc_market["price"], 65432.50)
        self.assertEqual(btc_market["volume24h"], 1250000000.0)
        self.assertIn("$", btc_market["volumeFormatted"])
        self.assertEqual(btc_market["status"], "LIVE")


if __name__ == "__main__":
    unittest.main()
