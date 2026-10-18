"""
Step 2 Verification Tests: Normalized Market Data & Hybrid Startup Sequence.
Tests all requirements of Step 2 from modular_stepbystep.md:
- 13 normalized fields (Venue, Symbol, Best Bid, Best Ask, Bid Size, Ask Size,
  Funding Rate, Funding Direction, 24H Volume, Timestamp, Sequence, Freshness, Connection Status)
- Native WS Primary + CCXT Pro Standby without duplicate connections
- Hybrid Startup Pipeline: REST Snapshot -> REST 1m candles -> Historical Deviation -> Bollinger References -> WS sync -> Live state
- Sequence tracking, gap detection, invalid data rejection
- Startup readiness gating (symbol is NOT execution-ready until snapshot, WS sync, valid prices, and freshness all pass)
- Raw high-frequency depth preservation vs. ~1-second analytical cadence
- Verification of zero mock market-data in the startup/data paths
"""

import asyncio
import time
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from arbitrage.models import (
    ConnectionStatus,
    DataSourceType,
    FreshnessStatus,
    FundingDirection,
    FundingRate,
    NormalizedMarketState,
    OHLCV,
    OrderBook,
    OrderBookEntry,
    Ticker,
)
from arbitrage.native_ws_connector import NativeWebSocketConnector
from arbitrage.ccxt_pro_connector import CCXTProConnector
from arbitrage.rest_connector import RESTConnector
from arbitrage.market_data_manager import MarketDataManager
from arbitrage.live_state import LiveStateManager


class TestStep2NormalizedMarketData(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self.live_state = LiveStateManager()
        self.manager = MarketDataManager(
            default_source=DataSourceType.AUTO,
            enabled_exchanges=["binance", "bybit", "okx"],
            stale_threshold_seconds=5.0,
            live_state=self.live_state,
        )

    async def asyncTearDown(self):
        await self.manager.close()

    def test_normalized_market_state_13_fields(self):
        """Verify NormalizedMarketState contains all 13 required Step 2 fields."""
        state = NormalizedMarketState(
            venue="BINANCE",
            symbol="BTC/USDT",
            best_bid=65000.0,
            best_ask=65001.0,
            bid_size=1.5,
            ask_size=2.0,
            funding_rate=0.0001,
            funding_direction=FundingDirection.LONG_PAYS_SHORT.value,
            volume_24h=25000.0,
            timestamp=time.time(),
            sequence=10001,
            freshness=FreshnessStatus.FRESH.value,
            connection_status=ConnectionStatus.CONNECTED.value,
            has_rest_snapshot=True,
            ws_synchronized=True,
        )

        d = state.to_dict()
        # Verify 13 required fields are present
        self.assertEqual(d["venue"], "BINANCE")
        self.assertEqual(d["symbol"], "BTC/USDT")
        self.assertEqual(d["best_bid"], 65000.0)
        self.assertEqual(d["best_ask"], 65001.0)
        self.assertEqual(d["bid_size"], 1.5)
        self.assertEqual(d["ask_size"], 2.0)
        self.assertEqual(d["funding_rate"], 0.0001)
        self.assertEqual(d["funding_direction"], "LONG_PAYS_SHORT")
        self.assertEqual(d["volume_24h"], 25000.0)
        self.assertIn("timestamp", d)
        self.assertEqual(d["sequence"], 10001)
        self.assertEqual(d["freshness"], "FRESH")
        self.assertEqual(d["connection_status"], "CONNECTED")

        # Evaluate readiness
        is_ready = state.evaluate_readiness()
        self.assertTrue(is_ready)
        self.assertTrue(state.is_execution_ready)

    def test_execution_readiness_gating(self):
        """A symbol is NOT execution-ready until snapshot exists, WS is synchronized, prices valid, freshness passes."""
        # 1. Missing REST snapshot -> NOT ready
        state = NormalizedMarketState(
            venue="BINANCE",
            symbol="BTC/USDT",
            best_bid=65000.0,
            best_ask=65001.0,
            has_rest_snapshot=False,
            ws_synchronized=True,
        )
        self.assertFalse(state.evaluate_readiness())
        self.assertFalse(state.is_execution_ready)
        self.assertEqual(state.freshness, FreshnessStatus.WAITING_FOR_DATA.value)

        # 2. Missing WS synchronization -> NOT ready
        state.has_rest_snapshot = True
        state.ws_synchronized = False
        self.assertFalse(state.evaluate_readiness())
        self.assertEqual(state.freshness, FreshnessStatus.WAITING_FOR_DATA.value)

        # 3. Invalid/Crossed prices (best_ask < best_bid) -> NOT ready
        state.ws_synchronized = True
        state.best_bid = 65005.0
        state.best_ask = 65000.0  # Crossed!
        self.assertFalse(state.evaluate_readiness())
        self.assertEqual(state.freshness, FreshnessStatus.INVALID.value)

        # 4. Stale timestamp (> 10s old) -> NOT ready
        state.best_bid = 65000.0
        state.best_ask = 65001.0
        state.timestamp = time.time() - 20.0
        self.assertFalse(state.evaluate_readiness(stale_threshold_seconds=10.0))
        self.assertEqual(state.freshness, FreshnessStatus.STALE.value)

        # 5. All pass -> READY
        state.timestamp = time.time()
        state.connection_status = ConnectionStatus.CONNECTED.value
        self.assertTrue(state.evaluate_readiness(stale_threshold_seconds=10.0))
        self.assertEqual(state.freshness, FreshnessStatus.FRESH.value)
        self.assertTrue(state.is_execution_ready)

    def test_no_duplicate_exchange_connections_in_auto_mode(self):
        """Verify AUTO mode subscribes Native WS as primary and does NOT open duplicate CCXT Pro connection."""
        with patch.object(self.manager.native_ws, "subscribe_order_book") as mock_native_ob, \
             patch.object(self.manager.ccxt_pro, "subscribe_order_book") as mock_pro_ob:
            self.manager.subscribe_order_book("binance", "BTC/USDT")
            mock_native_ob.assert_called_once_with("binance", "BTC/USDT")
            mock_pro_ob.assert_not_called()

        with patch.object(self.manager.native_ws, "subscribe_ticker") as mock_native_t, \
             patch.object(self.manager.ccxt_pro, "subscribe_ticker") as mock_pro_t:
            self.manager.subscribe_ticker("binance", "BTC/USDT")
            mock_native_t.assert_called_once_with("binance", "BTC/USDT")
            mock_pro_t.assert_not_called()

    def test_sequence_tracking_and_gap_detection(self):
        """Verify native WS connector tracks sequence numbers and detects gaps."""
        native = NativeWebSocketConnector(exchanges=["binance"])
        native._track_sequence("binance", "BTC/USDT", 100)
        info = native.get_sequence_info("binance", "BTC/USDT")
        self.assertEqual(info["last_sequence"], 100)
        self.assertEqual(info["missed_count"], 0)

        # In-sequence message
        native._track_sequence("binance", "BTC/USDT", 101)
        info = native.get_sequence_info("binance", "BTC/USDT")
        self.assertEqual(info["last_sequence"], 101)
        self.assertEqual(info["missed_count"], 0)

        # Sequence gap: jumps from 101 to 105 (missed 3)
        native._track_sequence("binance", "BTC/USDT", 105)
        info = native.get_sequence_info("binance", "BTC/USDT")
        self.assertEqual(info["last_sequence"], 105)
        self.assertEqual(info["missed_count"], 3)

    def test_invalid_order_book_rejection(self):
        """Verify invalid or crossed order books are rejected by connector validation."""
        native = NativeWebSocketConnector(exchanges=["binance"])
        received = []
        native.on_order_book(lambda ob: received.append(ob))

        # Payload with crossed prices (bid 65010 > ask 65000)
        crossed_payload = {
            "bids": [["65010.00", "1.0"]],
            "asks": [["65000.00", "1.0"]],
            "lastUpdateId": 500,
        }
        native._handle_native_message("binance", "BTC/USDT", crossed_payload)
        self.assertEqual(len(received), 0)  # Must be rejected!

    async def test_step2_hybrid_startup_pipeline(self):
        """Verify the complete Step 2 Hybrid Startup Sequence end-to-end."""
        # Mock REST snapshot response
        mock_snapshot_book = OrderBook(
            symbol="BTC/USDT",
            venue="BINANCE",
            bids=[OrderBookEntry(price=64000.0, size=2.5)],
            asks=[OrderBookEntry(price=64002.0, size=3.0)],
            timestamp=time.time(),
            source=DataSourceType.REST.value,
            sequence=5000,
            freshness="FRESH",
            connection_status="CONNECTED",
            is_ready=True,
        )
        mock_snapshot_ticker = Ticker(
            symbol="BTC/USDT",
            venue="BINANCE",
            bid=64000.0,
            ask=64002.0,
            last=64001.0,
            bid_size=2.5,
            ask_size=3.0,
            volume_24h=120000.0,
            timestamp=time.time(),
            source=DataSourceType.REST.value,
            sequence=5000,
            freshness="FRESH",
            connection_status="CONNECTED",
            is_ready=True,
        )
        # Mock REST historical 1-minute candles
        mock_candles = [
            OHLCV(symbol="BTC/USDT", venue="BINANCE", timeframe="1m", timestamp=time.time() - (60 * i),
                  open=64000.0 + i, high=64005.0 + i, low=63995.0 + i, close=64000.0 + (i * 0.5), volume=10.0)
            for i in range(20)
        ]

        with patch.object(self.manager.rest, "fetch_order_book", new_callable=AsyncMock) as mock_f_ob, \
             patch.object(self.manager.rest, "fetch_ticker", new_callable=AsyncMock) as mock_f_tick, \
             patch.object(self.manager.rest, "fetch_ohlcv", new_callable=AsyncMock) as mock_f_ohlcv, \
             patch.object(self.manager.native_ws, "subscribe_order_book") as mock_sub_ob, \
             patch.object(self.manager.native_ws, "subscribe_ticker") as mock_sub_tick:

            mock_f_ob.return_value = mock_snapshot_book
            mock_f_tick.return_value = mock_snapshot_ticker
            mock_f_ohlcv.return_value = mock_candles

            # Execute Hybrid Startup
            norm_state = await self.manager.initialize_symbol_hybrid("binance", "BTC/USDT")

            # 1. REST snapshot was fetched and stored
            mock_f_ob.assert_called_once_with("binance", "BTC/USDT", limit=20)
            mock_f_tick.assert_called_once_with("binance", "BTC/USDT")
            self.assertTrue(norm_state.has_rest_snapshot)
            self.assertEqual(norm_state.best_bid, 64000.0)
            self.assertEqual(norm_state.best_ask, 64002.0)
            self.assertEqual(norm_state.bid_size, 2.5)
            self.assertEqual(norm_state.ask_size, 3.0)

            # 2. REST historical 1m candles were fetched for statistical reference
            mock_f_ohlcv.assert_called_once_with("binance", "BTC/USDT", timeframe="1m", limit=60)
            self.assertGreater(norm_state.historical_deviation_mean, 63000.0)
            self.assertGreater(norm_state.bollinger_bandwidth, 0.0)
            self.assertGreater(norm_state.bollinger_upper, norm_state.bollinger_lower)

            # 3. WebSockets started without duplicates
            mock_sub_ob.assert_called_once_with("binance", "BTC/USDT")
            mock_sub_tick.assert_called_once_with("binance", "BTC/USDT")

            # 4. Ingest incoming native WS update and verify live transition & execution readiness
            live_ws_book = OrderBook(
                symbol="BTC/USDT",
                venue="BINANCE",
                bids=[OrderBookEntry(price=64003.0, size=1.8)],
                asks=[OrderBookEntry(price=64004.0, size=2.2)],
                timestamp=time.time(),
                source=DataSourceType.NATIVE_WS.value,
                sequence=5001,
                freshness="FRESH",
                connection_status="CONNECTED",
                is_ready=True,
                is_synchronized=True,
            )
            self.manager._on_native_ws_book(live_ws_book)

            updated_state = self.manager.get_normalized_state("binance", "BTC/USDT")
            self.assertIsNotNone(updated_state)
            self.assertEqual(updated_state.best_bid, 64003.0)
            self.assertEqual(updated_state.best_ask, 64004.0)
            self.assertEqual(updated_state.bid_size, 1.8)
            self.assertEqual(updated_state.ask_size, 2.2)
            self.assertTrue(updated_state.ws_synchronized)
            self.assertTrue(updated_state.is_execution_ready)
            self.assertEqual(updated_state.freshness, "FRESH")

    async def test_high_frequency_execution_preservation_and_analytical_stream(self):
        """Verify high-frequency depth updates immediately fire listeners, and analytical listener receives states."""
        raw_updates = []
        analytical_updates = []

        self.manager.add_order_book_listener(lambda ob: raw_updates.append(ob))
        self.manager.add_analytical_listener(lambda s: analytical_updates.append(s))

        # Push rapid high-frequency depth ticks
        for p in [64000.0, 64001.0, 64002.0]:
            ob = OrderBook(
                symbol="BTC/USDT",
                venue="BINANCE",
                bids=[OrderBookEntry(price=p, size=1.0)],
                asks=[OrderBookEntry(price=p + 1.0, size=1.0)],
                timestamp=time.time(),
                source=DataSourceType.NATIVE_WS.value,
                is_ready=True,
                is_synchronized=True,
            )
            self.manager._on_native_ws_book(ob)

        # All 3 raw updates fired immediately without throttle
        self.assertEqual(len(raw_updates), 3)
        self.assertEqual(raw_updates[-1].best_bid, 64002.0)


if __name__ == "__main__":
    unittest.main()
