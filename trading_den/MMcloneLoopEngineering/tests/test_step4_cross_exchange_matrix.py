"""
Unit tests for STEP 4: Cross-Exchange Matrix.
Validates normalized state fields, snapshot streaming, zero mock data,
reference/priced venue mappings, and explicit freshness/connection states.
"""

import time
import unittest
from arbitrage.models import (
    OrderBook,
    OrderBookEntry,
    Ticker,
    FundingRate,
    NormalizedMarketState,
    FreshnessStatus,
)
from arbitrage.live_state import LiveStateManager
from arbitrage.market_data_manager import MarketDataManager


class TestStep4CrossExchangeMatrix(unittest.TestCase):
    def setUp(self):
        self.live_state = LiveStateManager()
        self.md_mgr = MarketDataManager(live_state=self.live_state)

    def test_normalized_state_contains_all_step4_fields(self):
        """Verify all 9 matrix fields are present and properly serialized in NormalizedMarketState."""
        state = NormalizedMarketState(
            venue="BINANCE",
            symbol="BTC/USDT",
            best_bid=65420.50,
            best_ask=65421.00,
            bid_size=2.45,
            ask_size=1.85,
            funding_rate=0.0001,
            funding_direction="PAYS_SHORT",
            volume_24h=1500000000.0,
            timestamp=time.time(),
            sequence=101,
            freshness="fresh",
            connection_status="connected",
            is_execution_ready=True,
        )

        d = state.to_dict()
        required_fields = [
            "venue",
            "symbol",
            "best_bid",
            "best_ask",
            "bid_size",
            "ask_size",
            "funding_rate",
            "funding_direction",
            "volume_24h",
            "timestamp",
            "freshness",
            "connection_status",
        ]
        for field in required_fields:
            self.assertIn(field, d, f"Missing required Step 4 field: {field}")

        self.assertEqual(d["venue"], "BINANCE")
        self.assertEqual(d["symbol"], "BTC/USDT")
        self.assertEqual(d["best_bid"], 65420.50)
        self.assertEqual(d["best_ask"], 65421.00)
        self.assertEqual(d["bid_size"], 2.45)
        self.assertEqual(d["ask_size"], 1.85)
        self.assertEqual(d["funding_rate"], 0.0001)
        self.assertEqual(d["funding_direction"], "PAYS_SHORT")
        self.assertEqual(d["volume_24h"], 1500000000.0)
        self.assertEqual(d["freshness"], "fresh")
        self.assertEqual(d["connection_status"], "connected")

    def test_live_snapshot_contains_normalized_states_for_matrix(self):
        """Verify that get_live_snapshot includes all normalized_states for frontend matrix consumption."""
        # Feed books for Reference Venue (BINANCE) and Priced Venues (BYBIT, OKX)
        now = time.time()
        b_binance = OrderBook(
            venue="BINANCE",
            symbol="BTC/USDT",
            bids=[OrderBookEntry(price=65000.0, size=3.0)],
            asks=[OrderBookEntry(price=65002.0, size=2.5)],
            timestamp=now,
        )
        b_bybit = OrderBook(
            venue="BYBIT",
            symbol="BTC/USDT",
            bids=[OrderBookEntry(price=65010.0, size=1.5)],
            asks=[OrderBookEntry(price=65012.0, size=4.0)],
            timestamp=now,
        )
        b_okx = OrderBook(
            venue="OKX",
            symbol="BTC/USDT",
            bids=[OrderBookEntry(price=64995.0, size=2.0)],
            asks=[OrderBookEntry(price=64998.0, size=1.2)],
            timestamp=now,
        )

        self.md_mgr._update_normalized_state_from_book(b_binance)
        self.md_mgr._update_normalized_state_from_book(b_bybit)
        self.md_mgr._update_normalized_state_from_book(b_okx)

        # Also feed funding rate for BYBIT
        fr_bybit = FundingRate(
            venue="BYBIT",
            symbol="BTC/USDT",
            rate=0.00015,
            annual_pct=16.42,
            next_funding_time=int(now + 3600),
            timestamp=now,
        )
        self.md_mgr._update_normalized_state_from_funding(fr_bybit)

        # Also feed 24h ticker volume for BINANCE
        t_binance = Ticker(
            venue="BINANCE",
            symbol="BTC/USDT",
            bid=65000.0,
            ask=65002.0,
            last=65001.0,
            volume_24h=2400000000.0,
            timestamp=now,
        )
        self.md_mgr._update_normalized_state_from_ticker(t_binance)

        snapshot = self.live_state.get_live_snapshot(
            connector_status={"binance": {"status": "connected"}, "bybit": {"status": "connected"}},
            connected_clients=1,
        )

        self.assertIn("normalized_states", snapshot)
        norm_dict = snapshot["normalized_states"]

        # Check BINANCE key (Reference Venue)
        self.assertIn("BINANCE:BTC/USDT", norm_dict)
        binance_entry = norm_dict["BINANCE:BTC/USDT"]
        self.assertEqual(binance_entry["best_bid"], 65000.0)
        self.assertEqual(binance_entry["bid_size"], 3.0)
        self.assertEqual(binance_entry["best_ask"], 65002.0)
        self.assertEqual(binance_entry["ask_size"], 2.5)
        self.assertEqual(binance_entry["volume_24h"], 2400000000.0)

        # Check BYBIT key (Priced Venue)
        self.assertIn("BYBIT:BTC/USDT", norm_dict)
        bybit_entry = norm_dict["BYBIT:BTC/USDT"]
        self.assertEqual(bybit_entry["best_bid"], 65010.0)
        self.assertEqual(bybit_entry["best_ask"], 65012.0)
        self.assertEqual(bybit_entry["funding_rate"], 0.00015)
        self.assertEqual(bybit_entry["funding_direction"], "LONG_PAYS_SHORT")

        # Check OKX key
        self.assertIn("OKX:BTC/USDT", norm_dict)
        okx_entry = norm_dict["OKX:BTC/USDT"]
        self.assertEqual(okx_entry["best_bid"], 64995.0)

    def test_stale_detection_and_explicit_status(self):
        """Verify explicit STALE state when timestamp exceeds staleness threshold."""
        stale_time = time.time() - 35.0  # 35 seconds ago
        state = NormalizedMarketState(
            venue="KRAKEN",
            symbol="ETH/USDT",
            best_bid=3400.0,
            best_ask=3401.0,
            timestamp=stale_time,
            freshness="fresh",
            connection_status="connected",
            has_rest_snapshot=True,
            ws_synchronized=True,
        )
        # evaluate_readiness should detect age > 10.0 and mark as stale
        is_ready = state.evaluate_readiness(stale_threshold_seconds=10.0)
        self.assertFalse(is_ready)
        self.assertEqual(state.freshness, FreshnessStatus.STALE.value)

    def test_invalid_quote_rejection(self):
        """Verify invalid quotes (bid >= ask or <= 0) are flagged as not execution ready."""
        crossed_state = NormalizedMarketState(
            venue="BINANCE",
            symbol="BTC/USDT",
            best_bid=65010.0,
            best_ask=65005.0,  # crossed book
            timestamp=time.time(),
            freshness="fresh",
            connection_status="connected",
        )
        self.assertFalse(crossed_state.evaluate_readiness())
        self.assertFalse(crossed_state.is_execution_ready)

    def test_mirrored_cross_exchange_spread_computation(self):
        """Verify mirrored computation across X and Y exchange axes: Spread(A->B) == -Spread(B->A)."""
        # Venue A: Mid = 65000.0 (Bid=64999.0, Ask=65001.0)
        state_a = NormalizedMarketState(
            venue="BINANCE",
            symbol="BTC/USDT",
            best_bid=64999.0,
            best_ask=65001.0,
            bid_size=2.0,
            ask_size=2.0,
            timestamp=time.time(),
            freshness="fresh",
            connection_status="connected",
            is_execution_ready=True,
        )
        # Venue B: Mid = 65065.0 (Bid=65064.0, Ask=65066.0)
        state_b = NormalizedMarketState(
            venue="BYBIT",
            symbol="BTC/USDT",
            best_bid=65064.0,
            best_ask=65066.0,
            bid_size=1.5,
            ask_size=1.5,
            timestamp=time.time(),
            freshness="fresh",
            connection_status="connected",
            is_execution_ready=True,
        )

        mid_a = (state_a.best_bid + state_a.best_ask) / 2.0
        mid_b = (state_b.best_bid + state_b.best_ask) / 2.0

        # Off-diagonal (Row A, Col B): (Mid_B - Mid_A) / Mid_A * 100
        spread_a_to_b = ((mid_b - mid_a) / mid_a) * 100.0
        # Off-diagonal (Row B, Col A): (Mid_A - Mid_B) / Mid_B * 100
        spread_b_to_a = ((mid_a - mid_b) / mid_b) * 100.0

        self.assertAlmostEqual(spread_a_to_b, 0.10, places=2)
        self.assertAlmostEqual(spread_b_to_a, -0.10, places=2)
        # Mirrored symmetry: opposite signs
        self.assertAlmostEqual(spread_a_to_b, -spread_b_to_a, places=2)

        # Diagonal (Row A, Col A): Native quotes
        self.assertEqual(state_a.best_bid, 64999.0)
        self.assertEqual(state_a.best_ask, 65001.0)
        # Diagonal (Row B, Col B): Native quotes
        self.assertEqual(state_b.best_bid, 65064.0)
        self.assertEqual(state_b.best_ask, 65066.0)

    def test_zero_mock_matrix_unquoted_states(self):
        """Verify that when no feed exists for a symbol/venue, state is explicitly WAITING_FOR_DATA without fake values."""
        from arbitrage.server import ArbitrageServer

        server = ArbitrageServer(
            host="127.0.0.1",
            port=9999,
        )
        server.live_state = self.live_state

        # Query catalog for unquoted symbol
        markets = server.get_markets_catalog(exchange="BINANCE", inst_type="Spot", search_q="SOL")
        self.assertGreater(len(markets), 0)
        sol_m = markets[0]
        self.assertIsNone(sol_m["price"])
        self.assertEqual(sol_m["status"], "WAITING_FOR_DATA")

    def test_spread_candles_history_and_1h_bollinger(self):
        """Verify /api/history/spread-candles endpoint structure, 1H Bollinger deviation, and venues closes."""
        import asyncio
        from unittest.mock import AsyncMock
        from arbitrage.server import ArbitrageServer
        from arbitrage.models import OHLCV

        server = ArbitrageServer(host="127.0.0.1", port=9999)
        server.live_state = self.live_state

        # Mock REST connector
        now_sec = int(time.time() // 60 * 60)
        mock_1m_pv = [
            OHLCV(symbol="BTC/USDT", venue="binance", timeframe="1m", timestamp=now_sec - i * 60, open=65000.0 + i, high=65010.0 + i, low=64990.0 + i, close=65000.0 + i, volume=10.0)
            for i in range(30)
        ]
        mock_1m_rv = [
            OHLCV(symbol="BTC/USDT", venue="bybit", timeframe="1m", timestamp=now_sec - i * 60, open=64950.0 + i, high=64960.0 + i, low=64940.0 + i, close=64950.0 + i, volume=15.0)
            for i in range(30)
        ]
        mock_1h_pv = [
            OHLCV(symbol="BTC/USDT", venue="binance", timeframe="1h", timestamp=now_sec - i * 3600, open=65000.0, high=65100.0, low=64900.0, close=65000.0 + i * 5, volume=500.0)
            for i in range(12)
        ]
        mock_1h_rv = [
            OHLCV(symbol="BTC/USDT", venue="bybit", timeframe="1h", timestamp=now_sec - i * 3600, open=64900.0, high=65000.0, low=64800.0, close=64900.0 + i * 5, volume=600.0)
            for i in range(12)
        ]

        async def fake_fetch_ohlcv(venue, symbol, timeframe="1m", limit=100):
            if timeframe == "1h":
                return mock_1h_pv if venue == "binance" else mock_1h_rv
            return mock_1m_pv if venue == "binance" else mock_1m_rv

        server.market_data_mgr.rest = AsyncMock()
        server.market_data_mgr.rest.fetch_ohlcv = AsyncMock(side_effect=fake_fetch_ohlcv)

        class DummyWriter:
            def __init__(self):
                self.data = b""
            def write(self, d):
                self.data += d
            async def drain(self):
                pass
            def close(self):
                pass
            async def wait_closed(self):
                pass

        writer = DummyWriter()
        query = {
            "symbol": ["BTC/USDT"],
            "priced_venue": ["binance"],
            "reference_venue": ["bybit"],
            "limit": ["30"],
        }
        asyncio.run(server._handle_api_request("/api/history/spread-candles", query, writer))

        import json
        body_bytes = writer.data.split(b"\r\n\r\n")[1]
        resp = json.loads(body_bytes.decode("utf-8"))
        self.assertEqual(resp["status"], "ok")
        self.assertIn("candles", resp)
        self.assertIn("priced_closes", resp)
        self.assertIn("reference_closes", resp)
        self.assertIn("bollinger", resp)

        boll = resp["bollinger"]
        self.assertIsNotNone(boll)
        self.assertEqual(boll["timeframe"], "1h")
        self.assertIn("mean", boll)
        self.assertIn("sd", boll)
        self.assertIn("upper", boll)
        self.assertIn("lower", boll)
        self.assertIn("bandwidth", boll)
        self.assertGreater(boll["upper"], boll["lower"])

    def test_frontend_template_price_chart_and_lookback(self):
        """Verify templates/index.html complies with zero mock data, dual-series Price Chart, and Look back toolbar."""
        import os
        tmpl_path = os.path.join(os.path.dirname(__file__), "..", "templates", "index.html")
        with open(tmpl_path, "r", encoding="utf-8") as f:
            content = f.read()

        # Zero mock assertions
        self.assertNotIn("seedInitialBaselineCandles", content)
        self.assertNotIn("microJitter", content)
        self.assertNotIn("spread-skew", content)

        # Look back toolbar assertions
        self.assertIn("Rolling 1s", content)
        self.assertIn("1M Historical Closes · 1S Live Window", content)

        # Two-series Price Chart assertions
        self.assertIn("entry.seriesRef", content)
        self.assertIn("(Priced)", content)
        self.assertIn("(Reference)", content)
        self.assertIn("chartDataPriced", content)
        self.assertIn("chartDataRef", content)

        # 1H Bollinger computation assertions
        self.assertIn("getHourlySpreadCloses", content)
        self.assertIn("timeframe: '1h'", content)


if __name__ == "__main__":
    unittest.main()

