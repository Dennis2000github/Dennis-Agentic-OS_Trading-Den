"""
Step 5 Unit Tests: Raw Spread Calculation & Matrix Exposure.
Verifies all requirements for Step 5 from modular_stepbystep.md:
1. Backend Raw Spread % for every valid Reference/Priced relationship.
2. Uses real live executable bid & ask.
3. Independent of fees, funding, slippage, and other costs.
4. Evaluates both directions:
   - Direction 1: Buy Reference, Sell Priced
   - Direction 2: Buy Priced, Sell Reference
5. Returns all 9 fields:
   - reference_venue
   - priced_venue
   - symbol
   - prices (with executable bid/ask and both direction spreads)
   - direction
   - raw_spread_pct
   - timestamp
   - freshness
   - validity
6. Stale/invalid market data produces no valid spread (validity=False, raw_spread_pct=None).
7. Missing data produces no valid spread without fabrication.
8. Same venue produces same-venue rejection (diagonal).
9. LiveStateManager stores and exposes raw spreads in get_live_snapshot().
"""

import time
import unittest

from arbitrage.models import (
    ConnectionStatus,
    FreshnessStatus,
    NormalizedMarketState,
    OrderBook,
    OrderBookEntry,
    RawSpreadResult,
)
from arbitrage.spread_calculator import SpreadCalculator
from arbitrage.live_state import LiveStateManager


class TestStep5RawSpreadCalculation(unittest.TestCase):

    def setUp(self):
        # Fee configured at 0.10% (0.001) to verify raw spread is strictly fee-independent
        self.calc = SpreadCalculator(default_taker_fee=0.001, min_profit_threshold_pct=0.05)
        self.live_state = LiveStateManager()
        self.now = 1727770000.0

    def test_raw_spread_returns_all_nine_fields(self):
        """Test that RawSpreadResult dataclass and to_dict() contain all 9 required fields."""
        ref_book = OrderBook(
            venue="BINANCE",
            symbol="BTC/USDT",
            bids=[OrderBookEntry(price=60000.0, size=2.0)],
            asks=[OrderBookEntry(price=60010.0, size=2.0)],
            timestamp=self.now,
        )
        priced_book = OrderBook(
            venue="BYBIT",
            symbol="BTC/USDT",
            bids=[OrderBookEntry(price=60100.0, size=2.0)],
            asks=[OrderBookEntry(price=60110.0, size=2.0)],
            timestamp=self.now,
        )

        res = self.calc.calculate_raw_spread(
            reference_data=ref_book,
            priced_data=priced_book,
            symbol="BTC/USDT",
            current_time=self.now,
        )

        self.assertIsInstance(res, RawSpreadResult)
        d = res.to_dict()

        # 9 Required Fields
        required_fields = [
            "reference_venue",
            "priced_venue",
            "symbol",
            "prices",
            "direction",
            "raw_spread_pct",
            "timestamp",
            "freshness",
            "validity",
        ]
        for field in required_fields:
            self.assertIn(field, d, f"Missing required field: {field}")

        self.assertEqual(res.reference_venue, "BINANCE")
        self.assertEqual(res.priced_venue, "BYBIT")
        self.assertEqual(res.symbol, "BTC/USDT")
        self.assertTrue(res.validity)
        self.assertEqual(res.freshness, FreshnessStatus.FRESH.value)
        self.assertIsNotNone(res.raw_spread_pct)

    def test_both_directions_evaluated_direction_1_higher(self):
        """
        Direction 1 (Buy Ref, Sell Priced):
        Ref Ask = 60,000, Priced Bid = 60,300 -> spread = (60300 - 60000) / 60000 * 100 = +0.50%
        Direction 2 (Buy Priced, Sell Ref):
        Priced Ask = 60,310, Ref Bid = 59,990 -> spread = (59990 - 60310) / 60310 * 100 = -0.53%
        Higher direction is BUY_REF_SELL_PRICED (+0.50%).
        """
        ref_book = OrderBook(
            venue="BINANCE",
            symbol="BTC/USDT",
            bids=[OrderBookEntry(price=59990.0, size=1.0)],
            asks=[OrderBookEntry(price=60000.0, size=1.0)],
            timestamp=self.now,
        )
        priced_book = OrderBook(
            venue="BYBIT",
            symbol="BTC/USDT",
            bids=[OrderBookEntry(price=60300.0, size=1.0)],
            asks=[OrderBookEntry(price=60310.0, size=1.0)],
            timestamp=self.now,
        )

        res = self.calc.calculate_raw_spread(
            reference_data=ref_book,
            priced_data=priced_book,
            symbol="BTC/USDT",
            current_time=self.now,
        )

        self.assertTrue(res.validity)
        self.assertEqual(res.direction, "BUY_REF_SELL_PRICED")
        self.assertAlmostEqual(res.raw_spread_pct, 0.50, places=4)
        self.assertAlmostEqual(res.prices["spread_ref_to_priced_pct"], 0.50, places=4)
        self.assertTrue(res.prices["spread_priced_to_ref_pct"] < 0)
        self.assertEqual(res.prices["exec_ask"], 60000.0)
        self.assertEqual(res.prices["exec_bid"], 60300.0)

    def test_both_directions_evaluated_direction_2_higher(self):
        """
        Direction 2 (Buy Priced, Sell Ref):
        Priced Ask = 59,700, Ref Bid = 60,000 -> spread = (60000 - 59700) / 59700 * 100 = +0.5025%
        Direction 1 (Buy Ref, Sell Priced):
        Ref Ask = 60,010, Priced Bid = 59,690 -> spread = (59690 - 60010) / 60010 * 100 = -0.533%
        Higher direction is BUY_PRICED_SELL_REF (+0.5025%).
        """
        ref_book = OrderBook(
            venue="BINANCE",
            symbol="BTC/USDT",
            bids=[OrderBookEntry(price=60000.0, size=1.0)],
            asks=[OrderBookEntry(price=60010.0, size=1.0)],
            timestamp=self.now,
        )
        priced_book = OrderBook(
            venue="BYBIT",
            symbol="BTC/USDT",
            bids=[OrderBookEntry(price=59690.0, size=1.0)],
            asks=[OrderBookEntry(price=59700.0, size=1.0)],
            timestamp=self.now,
        )

        res = self.calc.calculate_raw_spread(
            reference_data=ref_book,
            priced_data=priced_book,
            symbol="BTC/USDT",
            current_time=self.now,
        )

        self.assertTrue(res.validity)
        self.assertEqual(res.direction, "BUY_PRICED_SELL_REF")
        expected_spread = ((60000.0 - 59700.0) / 59700.0) * 100.0
        self.assertAlmostEqual(res.raw_spread_pct, expected_spread, places=4)
        self.assertEqual(res.prices["exec_ask"], 59700.0)
        self.assertEqual(res.prices["exec_bid"], 60000.0)

    def test_raw_spread_is_independent_of_fees_and_slippage(self):
        """
        Raw Spread is pure executable price difference:
        (P_sell - P_buy) / P_buy * 100 without fee/slippage/funding deduction.
        Compare against net spread which subtracts taker fees (0.20%).
        """
        ref_book = OrderBook(
            venue="BINANCE",
            symbol="BTC/USDT",
            bids=[OrderBookEntry(price=59990.0, size=1.0)],
            asks=[OrderBookEntry(price=60000.0, size=1.0)],
            timestamp=self.now,
        )
        priced_book = OrderBook(
            venue="BYBIT",
            symbol="BTC/USDT",
            bids=[OrderBookEntry(price=60300.0, size=1.0)],
            asks=[OrderBookEntry(price=60310.0, size=1.0)],
            timestamp=self.now,
        )

        raw_res = self.calc.calculate_raw_spread(
            reference_data=ref_book,
            priced_data=priced_book,
            symbol="BTC/USDT",
            current_time=self.now,
        )
        net_res = self.calc.calculate_cross_exchange_spread(
            reference_book=ref_book,
            priced_book=priced_book,
        )

        self.assertAlmostEqual(raw_res.raw_spread_pct, 0.50, places=4)
        # Net spread subtracts 2 * 0.10% = 0.20% fee
        self.assertAlmostEqual(net_res.gross_spread_pct, 0.50, places=4)
        self.assertAlmostEqual(net_res.net_spread_pct, 0.30, places=4)
        self.assertNotEqual(raw_res.raw_spread_pct, net_res.net_spread_pct)

    def test_same_venue_diagonal_produces_no_valid_spread(self):
        """Crossing same venue with itself must return validity=False, direction=SAME_VENUE."""
        book = OrderBook(
            venue="BINANCE",
            symbol="BTC/USDT",
            bids=[OrderBookEntry(price=60000.0, size=1.0)],
            asks=[OrderBookEntry(price=60010.0, size=1.0)],
            timestamp=self.now,
        )

        res = self.calc.calculate_raw_spread(
            reference_data=book,
            priced_data=book,
            symbol="BTC/USDT",
            current_time=self.now,
        )

        self.assertFalse(res.validity)
        self.assertIsNone(res.raw_spread_pct)
        self.assertEqual(res.direction, "SAME_VENUE")

    def test_stale_data_produces_no_valid_spread(self):
        """When either side's market data is older than stale_threshold_seconds (10s), validity=False and raw_spread_pct=None."""
        fresh_book = OrderBook(
            venue="BINANCE",
            symbol="BTC/USDT",
            bids=[OrderBookEntry(price=60000.0, size=1.0)],
            asks=[OrderBookEntry(price=60010.0, size=1.0)],
            timestamp=self.now,
        )
        stale_book = OrderBook(
            venue="BYBIT",
            symbol="BTC/USDT",
            bids=[OrderBookEntry(price=60200.0, size=1.0)],
            asks=[OrderBookEntry(price=60210.0, size=1.0)],
            timestamp=self.now - 15.0,  # 15s old
        )

        res = self.calc.calculate_raw_spread(
            reference_data=fresh_book,
            priced_data=stale_book,
            symbol="BTC/USDT",
            stale_threshold_seconds=10.0,
            current_time=self.now,
        )

        self.assertFalse(res.validity)
        self.assertIsNone(res.raw_spread_pct)
        self.assertEqual(res.freshness, FreshnessStatus.STALE.value)
        self.assertIn("Stale market data", res.error_reason)

    def test_crossed_book_produces_no_valid_spread(self):
        """When ask < bid (crossed book), validity=False and raw_spread_pct=None."""
        invalid_book = OrderBook(
            venue="BINANCE",
            symbol="BTC/USDT",
            bids=[OrderBookEntry(price=60050.0, size=1.0)],
            asks=[OrderBookEntry(price=60000.0, size=1.0)],  # Crossed ask < bid
            timestamp=self.now,
        )
        valid_book = OrderBook(
            venue="BYBIT",
            symbol="BTC/USDT",
            bids=[OrderBookEntry(price=60100.0, size=1.0)],
            asks=[OrderBookEntry(price=60110.0, size=1.0)],
            timestamp=self.now,
        )

        res = self.calc.calculate_raw_spread(
            reference_data=invalid_book,
            priced_data=valid_book,
            symbol="BTC/USDT",
            current_time=self.now,
        )

        self.assertFalse(res.validity)
        self.assertIsNone(res.raw_spread_pct)
        self.assertEqual(res.freshness, FreshnessStatus.INVALID.value)

    def test_missing_market_data_produces_no_valid_spread(self):
        """Never fabricate Raw Spread when data is missing."""
        valid_book = OrderBook(
            venue="BINANCE",
            symbol="BTC/USDT",
            bids=[OrderBookEntry(price=60000.0, size=1.0)],
            asks=[OrderBookEntry(price=60010.0, size=1.0)],
            timestamp=self.now,
        )

        res = self.calc.calculate_raw_spread(
            reference_data=valid_book,
            priced_data=None,
            symbol="BTC/USDT",
            reference_venue="BINANCE",
            priced_venue="KRAKEN",
            current_time=self.now,
        )

        self.assertFalse(res.validity)
        self.assertIsNone(res.raw_spread_pct)
        self.assertEqual(res.freshness, FreshnessStatus.WAITING_FOR_DATA.value)
        self.assertIn("KRAKEN", res.error_reason)

    def test_works_with_normalized_market_state(self):
        """Calculate raw spread using NormalizedMarketState objects from Step 2/4."""
        state_binance = NormalizedMarketState(
            venue="BINANCE",
            symbol="BTC/USDT",
            best_bid=60000.0,
            best_ask=60005.0,
            bid_size=3.5,
            ask_size=2.1,
            timestamp=self.now,
            freshness=FreshnessStatus.FRESH.value,
            connection_status=ConnectionStatus.CONNECTED.value,
        )
        state_okx = NormalizedMarketState(
            venue="OKX",
            symbol="BTC/USDT",
            best_bid=60080.0,
            best_ask=60085.0,
            bid_size=1.2,
            ask_size=4.0,
            timestamp=self.now,
            freshness=FreshnessStatus.FRESH.value,
            connection_status=ConnectionStatus.CONNECTED.value,
        )

        res = self.calc.calculate_raw_spread(
            reference_data=state_binance,
            priced_data=state_okx,
            symbol="BTC/USDT",
            current_time=self.now,
        )

        self.assertTrue(res.validity)
        self.assertEqual(res.reference_venue, "BINANCE")
        self.assertEqual(res.priced_venue, "OKX")
        self.assertEqual(res.direction, "BUY_REF_SELL_PRICED")
        expected_spread = ((60080.0 - 60005.0) / 60005.0) * 100.0
        self.assertAlmostEqual(res.raw_spread_pct, expected_spread, places=4)

    def test_live_state_storage_and_snapshot_exposure(self):
        """Verify LiveStateManager stores raw spreads and exposes them in get_live_snapshot()."""
        ref_book = OrderBook(
            venue="BINANCE",
            symbol="BTC/USDT",
            bids=[OrderBookEntry(price=60000.0, size=1.0)],
            asks=[OrderBookEntry(price=60010.0, size=1.0)],
            timestamp=self.now,
        )
        priced_book = OrderBook(
            venue="BYBIT",
            symbol="BTC/USDT",
            bids=[OrderBookEntry(price=60200.0, size=1.0)],
            asks=[OrderBookEntry(price=60210.0, size=1.0)],
            timestamp=self.now,
        )

        raw = self.calc.calculate_raw_spread(
            reference_data=ref_book,
            priced_data=priced_book,
            symbol="BTC/USDT",
            current_time=self.now,
        )

        # Store in live state
        changed = self.live_state.set_raw_spread(raw)
        self.assertTrue(changed)

        # Lookup with slash
        fetched = self.live_state.get_raw_spread("BINANCE", "BYBIT", "BTC/USDT")
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched.symbol, "BTC/USDT")

        # Lookup without slash
        fetched_no_slash = self.live_state.get_raw_spread("BINANCE", "BYBIT", "BTCUSDT")
        self.assertIsNotNone(fetched_no_slash)

        # Snapshot exposure
        snap = self.live_state.get_live_snapshot()
        self.assertIn("raw_spreads", snap)
        self.assertIn("raw_spreads_dict", snap)
        self.assertEqual(len(snap["raw_spreads"]), 1)
        self.assertIn("BINANCE->BYBIT:BTC/USDT", snap["raw_spreads_dict"])
        self.assertIn("BINANCE->BYBIT:BTCUSDT", snap["raw_spreads_dict"])


if __name__ == "__main__":
    unittest.main()
