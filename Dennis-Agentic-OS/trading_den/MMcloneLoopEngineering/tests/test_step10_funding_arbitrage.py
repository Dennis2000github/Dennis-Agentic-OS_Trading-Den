"""
Tests for Step 10: Funding Arbitrage Mode.

Requirements from modular/step-10.md:
- Implement Funding Arbitrage as a separate strategy mode within the existing architecture.
- Do not redesign UI or make unrelated Spread Arbitrage changes.
- Reuse: market data, direction engine, fee engine, Strategy Settings, Opportunity Engine, Execution Engine.
- 10.1 Funding Selection:
    Evaluate both directions;
    calculate directional funding opportunity;
    select First Leg / Maker using Highest Funding %.
    Funding % is venue/direction selector only; NEVER the Maker order price.
    Priced Venue does not automatically become Maker; Reference Venue does not automatically become Hedge.
- 10.3 Execution Roles: First Leg = Maker, Second Leg = Hedge/Taker.
- 10.4 Maker Entry Price: Spread % / Grid / Bollinger determines Maker entry level.
- 10.9 Independent Funding Exit: Do not use Spread convergence / 0% as exit.
- 10.11 Directional fees reused.
- 10.13 Preserve Spread Arbitrage.
- 10.15 Real market data only, zero order placement invariant.
"""

import time
import unittest

from arbitrage.fee_engine import FeeEngine
from arbitrage.funding_arbitrage import FundingArbitrageEngine
from arbitrage.models import (
    DirectionalFundingOpportunity,
    FundingArbitrageDecision,
    FundingRate,
    MakerEntryPricing,
    OrderBook,
    OrderBookEntry,
)


class TestStep10FundingArbitrage(unittest.TestCase):
    def setUp(self):
        self.fee_engine = FeeEngine()
        self.engine = FundingArbitrageEngine(
            fee_engine=self.fee_engine,
            min_annual_funding_pct=5.0,
        )

    def test_evaluate_both_directions_and_select_highest_funding_rate(self):
        """
        Direction 1: Short Binance (rate = +0.03% = 0.0003), Long Bybit (rate = +0.01% = 0.0001).
        Net funding = +0.02% per 8h period = +21.9% annual yield!
        Direction 2: Short Bybit, Long Binance -> Net funding = -0.02% (negative yield).

        Selected direction must be Short Binance + Long Bybit.
        Highest Funding % selects Maker/Hedge execution direction.
        """
        now = time.time()
        funding_a = FundingRate(
            symbol="BTC/USDT",
            venue="BINANCE",
            rate=0.0003, # +0.03% per 8h
            timestamp=now,
            next_funding_time=now + 3600.0,
        )
        funding_b = FundingRate(
            symbol="BTC/USDT",
            venue="BYBIT",
            rate=0.0001, # +0.01% per 8h
            timestamp=now,
            next_funding_time=now + 3600.0,
        )
        book_a = OrderBook(
            symbol="BTC/USDT",
            venue="BINANCE",
            bids=[OrderBookEntry(price=60000.0, size=2.0)],
            asks=[OrderBookEntry(price=60010.0, size=2.0)],
            timestamp=now,
        )
        book_b = OrderBook(
            symbol="BTC/USDT",
            venue="BYBIT",
            bids=[OrderBookEntry(price=60005.0, size=2.0)],
            asks=[OrderBookEntry(price=60015.0, size=2.0)],
            timestamp=now,
        )

        decision = self.engine.evaluate_funding_arbitrage(
            funding_a=funding_a,
            funding_b=funding_b,
            book_a=book_a,
            book_b=book_b,
            symbol="BTC/USDT",
        )

        self.assertTrue(decision.is_viable)
        self.assertEqual(decision.selected_opportunity.short_venue, "BINANCE")
        self.assertEqual(decision.selected_opportunity.long_venue, "BYBIT")
        self.assertAlmostEqual(decision.net_funding_rate, 0.0002) # 0.02%
        self.assertGreater(decision.net_funding_annual_pct, 20.0)

        # Funding % selects venue/direction ONLY, NEVER the order price!
        self.assertNotEqual(decision.pricing.base_maker_price, decision.net_funding_rate)
        self.assertGreater(decision.pricing.base_maker_price, 1000.0) # Matches actual market price level

    def test_funding_percentage_never_determines_maker_order_price(self):
        """
        Section 10.1 & 10.4:
        Funding % is a venue/direction selector only.
        It is never the Maker order price.
        Maker entry level is determined by Spread / Grid / Bollinger.
        """
        now = time.time()
        funding_a = FundingRate(
            symbol="ETH/USDT",
            venue="BINANCE",
            rate=0.0005, # 0.05% funding rate
            timestamp=now,
        )
        funding_b = FundingRate(
            symbol="ETH/USDT",
            venue="BYBIT",
            rate=0.0001,
            timestamp=now,
        )
        book_a = OrderBook(
            symbol="ETH/USDT",
            venue="BINANCE",
            bids=[OrderBookEntry(price=3000.0, size=5.0)],
            asks=[OrderBookEntry(price=3002.0, size=5.0)],
            timestamp=now,
        )
        book_b = OrderBook(
            symbol="ETH/USDT",
            venue="BYBIT",
            bids=[OrderBookEntry(price=3001.0, size=5.0)],
            asks=[OrderBookEntry(price=3003.0, size=5.0)],
            timestamp=now,
        )

        decision = self.engine.evaluate_funding_arbitrage(
            funding_a=funding_a,
            funding_b=funding_b,
            book_a=book_a,
            book_b=book_b,
            symbol="ETH/USDT",
            pricing_mode="SPREAD",
            spread_offset_pct=0.05,
        )

        # Funding rate is 0.0004
        self.assertAlmostEqual(decision.net_funding_rate, 0.0004)
        # Order price must be ~3000 (top of book), NOT 0.0004!
        self.assertGreater(decision.pricing.base_maker_price, 2500.0)
        self.assertLess(decision.pricing.base_maker_price, 3500.0)

    def test_maker_entry_pricing_grid_and_bollinger_support(self):
        """
        Section 10.4, 10.6, 10.7:
        Spread / Grid / Bollinger determines Maker entry level after Funding selection.
        """
        now = time.time()
        funding_a = FundingRate(
            symbol="BTC/USDT",
            venue="BINANCE",
            rate=0.0004,
            timestamp=now,
        )
        funding_b = FundingRate(
            symbol="BTC/USDT",
            venue="BYBIT",
            rate=0.0001,
            timestamp=now,
        )
        book_a = OrderBook(
            symbol="BTC/USDT",
            venue="BINANCE",
            bids=[OrderBookEntry(price=60000.0, size=3.0)],
            asks=[OrderBookEntry(price=60010.0, size=3.0)],
            timestamp=now,
        )
        book_b = OrderBook(
            symbol="BTC/USDT",
            venue="BYBIT",
            bids=[OrderBookEntry(price=60005.0, size=3.0)],
            asks=[OrderBookEntry(price=60015.0, size=3.0)],
            timestamp=now,
        )

        # 1. Grid Mode
        dec_grid = self.engine.evaluate_funding_arbitrage(
            funding_a=funding_a,
            funding_b=funding_b,
            book_a=book_a,
            book_b=book_b,
            pricing_mode="GRID",
            grid_spacing_pct=0.05,
            grid_levels_count=3,
        )
        self.assertEqual(dec_grid.pricing.pricing_mode, "GRID")
        self.assertEqual(len(dec_grid.pricing.grid_levels), 3)

        # 2. Bollinger Mode
        dec_boll = self.engine.evaluate_funding_arbitrage(
            funding_a=funding_a,
            funding_b=funding_b,
            book_a=book_a,
            book_b=book_b,
            pricing_mode="BOLLINGER",
            bollinger_k=2.0,
            spread_history=[0.05, 0.10, 0.15],
        )
        self.assertEqual(dec_boll.pricing.pricing_mode, "BOLLINGER")
        self.assertIsNotNone(dec_boll.pricing.bollinger_bands)

    def test_independent_funding_exit_not_spread_convergence(self):
        """
        Section 10.9: Independent Funding Exit.
        Funding Arbitrage exits on funding settlement / rate inversion, NOT 0% spread convergence!
        """
        now = time.time()
        settle_time = now + 14400.0 # 4h until settlement
        funding_a = FundingRate(
            symbol="BTC/USDT",
            venue="BINANCE",
            rate=0.0003,
            timestamp=now,
            next_funding_time=settle_time,
        )
        funding_b = FundingRate(
            symbol="BTC/USDT",
            venue="BYBIT",
            rate=0.0001,
            timestamp=now,
            next_funding_time=settle_time,
        )
        book_a = OrderBook(
            symbol="BTC/USDT",
            venue="BINANCE",
            bids=[OrderBookEntry(price=60000.0, size=1.0)],
            asks=[OrderBookEntry(price=60010.0, size=1.0)],
            timestamp=now,
        )
        book_b = OrderBook(
            symbol="BTC/USDT",
            venue="BYBIT",
            bids=[OrderBookEntry(price=60005.0, size=1.0)],
            asks=[OrderBookEntry(price=60015.0, size=1.0)],
            timestamp=now,
        )

        decision = self.engine.evaluate_funding_arbitrage(
            funding_a=funding_a,
            funding_b=funding_b,
            book_a=book_a,
            book_b=book_b,
        )

        exit_cond = decision.exit_conditions
        self.assertEqual(exit_cond["exit_type"], "FUNDING_SETTLEMENT")
        self.assertEqual(exit_cond["settlement_target_time"], settle_time)
        self.assertFalse(exit_cond["use_spread_convergence_exit"])
        self.assertTrue(exit_cond["rate_inversion_stop"])

    def test_rejection_below_min_annual_funding_threshold(self):
        """Unprofitable or negligible funding opportunities below threshold are rejected."""
        now = time.time()
        funding_a = FundingRate(
            symbol="BTC/USDT",
            venue="BINANCE",
            rate=0.00001, # Only 0.001%
            timestamp=now,
        )
        funding_b = FundingRate(
            symbol="BTC/USDT",
            venue="BYBIT",
            rate=0.00001,
            timestamp=now,
        )
        book_a = OrderBook(
            symbol="BTC/USDT",
            venue="BINANCE",
            bids=[OrderBookEntry(price=60000.0, size=1.0)],
            asks=[OrderBookEntry(price=60010.0, size=1.0)],
            timestamp=now,
        )
        book_b = OrderBook(
            symbol="BTC/USDT",
            venue="BYBIT",
            bids=[OrderBookEntry(price=60005.0, size=1.0)],
            asks=[OrderBookEntry(price=60015.0, size=1.0)],
            timestamp=now,
        )

        decision = self.engine.evaluate_funding_arbitrage(
            funding_a=funding_a,
            funding_b=funding_b,
            book_a=book_a,
            book_b=book_b,
            min_annual_funding_pct=5.0, # Requires 5% annual
        )

        self.assertFalse(decision.is_viable)
        self.assertIn("below minimum threshold", decision.rejection_reason)

    def test_zero_order_placement_invariant(self):
        """Verify FundingArbitrageEngine contains no order execution methods."""
        prohibited = ["place_order", "create_order", "send_order", "submit_order", "execute_order", "close_position"]
        for p in prohibited:
            self.assertFalse(hasattr(self.engine, p), f"FundingArbitrageEngine must not have method {p}")


if __name__ == "__main__":
    unittest.main()
