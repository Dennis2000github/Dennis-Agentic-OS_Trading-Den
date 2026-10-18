"""
Tests for Step 9: Spread Arbitrage.

Requirements from modular/step-09.md:
- Use directional opportunities.
- Evaluate both directions.
- Calculate Net Spread.
- Select: Highest Net Spread % subject to existing thresholds and risk controls.
- Selected direction becomes:
    First Leg = Maker;
    Second Leg = Taker/Hedge.
- Do not derive execution direction from Reference/Priced roles.
- Preserve:
    Grid;
    Bollinger;
    Quantity;
    Risk;
    Entry thresholds;
    Existing exit behavior.
- The highest Net Spread identifies/selects the Maker direction/order opportunity.
- The actual Maker execution level remains determined by:
    Spread %;
    Grid;
    Grid Spacing;
    Bollinger;
    other configured entry controls.
- Do not alter Spread Arbitrage lifecycle unnecessarily.
- Zero order placement invariant.
"""

import time
import unittest

from arbitrage.models import (
    DirectionEvaluationResult,
    DirectionalOpportunity,
    MakerEntryPricing,
    OpportunityEvaluationResult,
    OrderBook,
    OrderBookEntry,
    SpreadArbitrageDecision,
)
from arbitrage.opportunity_engine import OpportunityEngine
from arbitrage.spread_arbitrage import SpreadArbitrageEngine


class TestStep9SpreadArbitrage(unittest.TestCase):
    def setUp(self):
        self.opportunity_engine = OpportunityEngine(min_profit_threshold_pct=0.05)
        self.engine = SpreadArbitrageEngine(
            opportunity_engine=self.opportunity_engine,
            min_profit_threshold_pct=0.05,
            max_slippage_pct=0.20,
            default_order_quantity=1.0,
        )

    def test_evaluate_both_directions_and_select_highest_net_candidate_a(self):
        """
        When Candidate A has a higher Net Spread than Candidate B:
        - Candidate A: Buy on Binance @ 60000, Sell on Bybit @ 60300 (+0.50% gross, ~0.30% net)
        - Selected opportunity must be Candidate A.
        - First Leg = Binance (Maker), Second Leg = Bybit (Hedge/Taker).
        """
        now = time.time()
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
            bids=[OrderBookEntry(price=60300.0, size=2.0)],
            asks=[OrderBookEntry(price=60310.0, size=2.0)],
            timestamp=now,
        )

        decision = self.engine.evaluate_spread_arbitrage(
            venue_a_data=book_a,
            venue_b_data=book_b,
            symbol="BTC/USDT",
            venue_a_name="BINANCE",
            venue_b_name="BYBIT",
            reference_venue="BINANCE",
            priced_venue="BYBIT",
        )

        self.assertTrue(decision.is_viable)
        self.assertEqual(decision.candidate_id, "A")
        self.assertEqual(decision.first_leg_venue, "BINANCE")
        self.assertEqual(decision.second_leg_venue, "BYBIT")
        self.assertEqual(decision.maker_side, "BUY")
        self.assertEqual(decision.hedge_side, "SELL")
        self.assertGreater(decision.net_spread_pct, 0.05)
        self.assertIsNotNone(decision.pricing)

    def test_evaluate_both_directions_and_select_highest_net_candidate_b(self):
        """
        When Candidate B has a higher Net Spread than Candidate A:
        - Venue A = OKX (0.08% maker, 0.10% taker)
        - Venue B = MEXC (0.00% maker, 0.10% taker)
        - Candidate A (OKX Maker + MEXC Taker): 0.18% fee
        - Candidate B (MEXC Maker + OKX Taker): 0.10% fee
        - Candidate B saves 0.08% in fees -> Higher Net Spread!
        - Selected opportunity must be Candidate B.
        - First Leg = MEXC (Maker / Venue B), Second Leg = OKX (Hedge / Venue A).
        - Proves Reference/Priced roles do NOT dictate execution roles!
        """
        now = time.time()
        book_a = OrderBook(
            symbol="BTC/USDT",
            venue="OKX",
            bids=[OrderBookEntry(price=60000.0, size=2.0)],
            asks=[OrderBookEntry(price=60010.0, size=2.0)],
            timestamp=now,
        )
        book_b = OrderBook(
            symbol="BTC/USDT",
            venue="MEXC",
            bids=[OrderBookEntry(price=60300.0, size=2.0)],
            asks=[OrderBookEntry(price=60310.0, size=2.0)],
            timestamp=now,
        )

        decision = self.engine.evaluate_spread_arbitrage(
            venue_a_data=book_a,
            venue_b_data=book_b,
            symbol="BTC/USDT",
            venue_a_name="OKX",
            venue_b_name="MEXC",
            reference_venue="OKX",
            priced_venue="MEXC",
        )

        self.assertTrue(decision.is_viable)
        self.assertEqual(decision.candidate_id, "B")
        # First Leg is MEXC (Venue B / Priced venue!), NOT Reference venue!
        self.assertEqual(decision.first_leg_venue, "MEXC")
        self.assertEqual(decision.second_leg_venue, "OKX")
        self.assertEqual(decision.maker_side, "SELL")
        self.assertEqual(decision.hedge_side, "BUY")
        self.assertGreater(decision.net_spread_pct, 0.05)

    def test_rejection_when_below_min_profit_threshold(self):
        """When net spread is below configured min_profit_threshold_pct, mark is_viable=False."""
        now = time.time()
        # Tight spread: 60000.0 vs 60005.0 -> net spread after 0.20% fees is negative!
        book_a = OrderBook(
            symbol="BTC/USDT",
            venue="BINANCE",
            bids=[OrderBookEntry(price=60000.0, size=1.0)],
            asks=[OrderBookEntry(price=60005.0, size=1.0)],
            timestamp=now,
        )
        book_b = OrderBook(
            symbol="BTC/USDT",
            venue="BYBIT",
            bids=[OrderBookEntry(price=60005.0, size=1.0)],
            asks=[OrderBookEntry(price=60010.0, size=1.0)],
            timestamp=now,
        )

        decision = self.engine.evaluate_spread_arbitrage(
            venue_a_data=book_a,
            venue_b_data=book_b,
            symbol="BTC/USDT",
            min_profit_threshold_pct=0.10,  # Requires at least +0.10% net profit
        )

        self.assertFalse(decision.is_viable)
        self.assertIn("below minimum threshold", decision.rejection_reason)

    def test_stale_market_data_invalidates_opportunity(self):
        """Stale market data (> 10s) must strictly invalidate decision with no placeholders."""
        now = time.time()
        stale_time = now - 60.0  # 60s old
        book_a = OrderBook(
            symbol="BTC/USDT",
            venue="BINANCE",
            bids=[OrderBookEntry(price=60000.0, size=1.0)],
            asks=[OrderBookEntry(price=60010.0, size=1.0)],
            timestamp=stale_time,
        )
        book_b = OrderBook(
            symbol="BTC/USDT",
            venue="BYBIT",
            bids=[OrderBookEntry(price=60300.0, size=1.0)],
            asks=[OrderBookEntry(price=60310.0, size=1.0)],
            timestamp=now,
        )

        decision = self.engine.evaluate_spread_arbitrage(
            venue_a_data=book_a,
            venue_b_data=book_b,
            symbol="BTC/USDT",
        )

        self.assertFalse(decision.is_viable)
        self.assertIsNotNone(decision.rejection_reason)

    def test_maker_entry_pricing_spread_mode(self):
        """
        Spread Mode pricing:
        Maker limit order placed at top-of-book with optional spread_offset_pct.
        """
        now = time.time()
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
            bids=[OrderBookEntry(price=60400.0, size=2.0)],
            asks=[OrderBookEntry(price=60410.0, size=2.0)],
            timestamp=now,
        )

        decision = self.engine.evaluate_spread_arbitrage(
            venue_a_data=book_a,
            venue_b_data=book_b,
            symbol="BTC/USDT",
            pricing_mode="SPREAD",
            spread_offset_pct=0.01,  # 1 bp offset
            stop_loss_pct=0.50,
        )

        pricing = decision.pricing
        self.assertIsNotNone(pricing)
        self.assertEqual(pricing.pricing_mode, "SPREAD")
        self.assertEqual(pricing.maker_side, "BUY")
        # Entry price adjusted by spread offset
        expected_price = round(60010.0 * (1.0 - 0.0001), 6)
        self.assertAlmostEqual(pricing.base_maker_price, expected_price, places=2)
        self.assertIsNotNone(pricing.stop_loss_price)
        self.assertAlmostEqual(pricing.target_hedge_price, 60400.0)

    def test_maker_entry_pricing_grid_mode(self):
        """
        Grid Mode pricing:
        Generates ladder of Maker entry limit orders spaced by grid_spacing_pct.
        """
        now = time.time()
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
            bids=[OrderBookEntry(price=60500.0, size=3.0)],
            asks=[OrderBookEntry(price=60510.0, size=3.0)],
            timestamp=now,
        )

        decision = self.engine.evaluate_spread_arbitrage(
            venue_a_data=book_a,
            venue_b_data=book_b,
            symbol="BTC/USDT",
            pricing_mode="GRID",
            grid_spacing_pct=0.05,  # 5 bps between levels
            grid_levels_count=3,
            target_size=1.5,
        )

        pricing = decision.pricing
        self.assertIsNotNone(pricing)
        self.assertEqual(pricing.pricing_mode, "GRID")
        self.assertEqual(len(pricing.grid_levels), 3)

        # Check grid levels monotonicity
        lvl0 = pricing.grid_levels[0]
        lvl1 = pricing.grid_levels[1]
        lvl2 = pricing.grid_levels[2]

        self.assertEqual(lvl0["level"], 0)
        self.assertEqual(lvl1["level"], 1)
        self.assertEqual(lvl2["level"], 2)

        # For BUY: level 0 > level 1 > level 2
        self.assertGreater(lvl0["price"], lvl1["price"])
        self.assertGreater(lvl1["price"], lvl2["price"])
        # Quantity divided across levels: 1.5 / 3 = 0.5
        self.assertAlmostEqual(lvl0["quantity"], 0.5)

    def test_maker_entry_pricing_bollinger_mode(self):
        """
        Bollinger Mode pricing:
        Uses rolling spread history to determine Bollinger bands and mean convergence target.
        """
        now = time.time()
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
            bids=[OrderBookEntry(price=60400.0, size=2.0)],
            asks=[OrderBookEntry(price=60410.0, size=2.0)],
            timestamp=now,
        )

        # Synthetic historical spread series around 0.20%
        history = [0.15, 0.18, 0.20, 0.22, 0.19, 0.25, 0.21, 0.28, 0.35, 0.40]

        decision = self.engine.evaluate_spread_arbitrage(
            venue_a_data=book_a,
            venue_b_data=book_b,
            symbol="BTC/USDT",
            pricing_mode="BOLLINGER",
            bollinger_k=2.0,
            bollinger_period=10,
            spread_history=history,
        )

        pricing = decision.pricing
        self.assertIsNotNone(pricing)
        self.assertEqual(pricing.pricing_mode, "BOLLINGER")
        self.assertIsNotNone(pricing.bollinger_bands)
        self.assertIn("upper", pricing.bollinger_bands)
        self.assertIn("lower", pricing.bollinger_bands)
        self.assertIn("mean", pricing.bollinger_bands)
        self.assertGreater(pricing.bollinger_bands["upper"], pricing.bollinger_bands["lower"])

    def test_serialization_to_dict(self):
        """Decision and Pricing models convert cleanly to JSON-compatible dictionaries."""
        now = time.time()
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
            bids=[OrderBookEntry(price=60350.0, size=2.0)],
            asks=[OrderBookEntry(price=60360.0, size=2.0)],
            timestamp=now,
        )

        decision = self.engine.evaluate_spread_arbitrage(
            venue_a_data=book_a,
            venue_b_data=book_b,
            symbol="BTC/USDT",
        )

        d = decision.to_dict()
        self.assertIn("candidate_id", d)
        self.assertIn("first_leg_venue", d)
        self.assertIn("second_leg_venue", d)
        self.assertIn("net_spread_pct", d)
        self.assertIn("pricing", d)
        self.assertIn("risk_metrics", d)

    def test_zero_order_placement_invariant(self):
        """Verify SpreadArbitrageEngine is strictly decision/pricing calculation with no order placement."""
        prohibited = ["place_order", "create_order", "send_order", "submit_order", "execute_order", "close_position"]
        for p in prohibited:
            self.assertFalse(hasattr(self.engine, p), f"SpreadArbitrageEngine must not have method {p}")


if __name__ == "__main__":
    unittest.main()
