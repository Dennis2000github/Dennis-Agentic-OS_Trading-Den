"""
Step 6 Unit Tests: Dynamic Maker/Hedge Direction Layer.
Verifies all requirements for Step 6 from modular_stepbystep.md:
1. Implements dedicated execution-direction layer (DirectionEngine).
2. Evaluates both Candidate A and Candidate B:
   - Candidate A: A Maker / First Leg -> B Taker/Hedge / Second Leg
   - Candidate B: B Maker / First Leg -> A Taker/Hedge / Second Leg
3. Never assumes Reference = Hedge or Priced = Maker:
   - In Candidate A: Maker is Venue A, Hedge is Venue B
   - In Candidate B: Maker is Venue B, Hedge is Venue A
   - Dynamic assignment regardless of Reference/Priced role
4. Returns both candidates with all required fields:
   - Maker venue
   - Maker price
   - Hedge venue
   - Hedge price
   - direction
   - timestamps
   - validity
5. Rejects stale, crossed, missing, disconnected, or same-venue data.
6. Zero order placement (evaluation only).
7. Uses only real market data (OrderBook, NormalizedMarketState).
"""

import time
import unittest

from arbitrage.direction_engine import DirectionEngine
from arbitrage.live_state import LiveStateManager
from arbitrage.models import (
    ConnectionStatus,
    DirectionEvaluationResult,
    FreshnessStatus,
    MakerHedgeCandidate,
    NormalizedMarketState,
    OrderBook,
    OrderBookEntry,
)
from arbitrage.spread_calculator import SpreadCalculator


class TestStep6DirectionEngine(unittest.TestCase):

    def setUp(self):
        self.engine = DirectionEngine(stale_threshold_seconds=10.0)
        self.calc = SpreadCalculator(default_taker_fee=0.001, min_profit_threshold_pct=0.05)
        self.live_state = LiveStateManager()
        self.now = 1727770000.0

    def test_both_candidates_returned_with_required_fields(self):
        """Test that evaluate_candidates returns Candidate A and Candidate B with all required fields."""
        book_a = OrderBook(
            venue="BINANCE",
            symbol="BTC/USDT",
            bids=[OrderBookEntry(price=60000.0, size=2.5)],
            asks=[OrderBookEntry(price=60010.0, size=3.0)],
            timestamp=self.now,
        )
        book_b = OrderBook(
            venue="BYBIT",
            symbol="BTC/USDT",
            bids=[OrderBookEntry(price=60200.0, size=1.8)],
            asks=[OrderBookEntry(price=60210.0, size=2.2)],
            timestamp=self.now,
        )

        res = self.engine.evaluate_candidates(
            venue_a_data=book_a,
            venue_b_data=book_b,
            symbol="BTC/USDT",
            reference_venue="BINANCE",
            priced_venue="BYBIT",
            current_time=self.now,
        )

        self.assertIsInstance(res, DirectionEvaluationResult)
        self.assertIsInstance(res.candidate_a, MakerHedgeCandidate)
        self.assertIsInstance(res.candidate_b, MakerHedgeCandidate)

        # 7 Required Fields on both candidates
        required_fields = [
            "maker_venue",
            "maker_price",
            "hedge_venue",
            "hedge_price",
            "direction",
            "timestamps",
            "validity",
        ]
        for cand in [res.candidate_a, res.candidate_b]:
            d = cand.to_dict()
            for f in required_fields:
                self.assertIn(f, d, f"Missing required field: {f}")
            self.assertTrue(cand.validity)
            self.assertGreater(cand.maker_price, 0.0)
            self.assertGreater(cand.hedge_price, 0.0)

    def test_candidate_a_and_b_maker_hedge_assignment_buy_a_sell_b(self):
        """
        When Buy A @ 60,010 and Sell B @ 60,200 is favorable:
        Direction: BUY_A_SELL_B

        Candidate A (A Maker / First Leg -> B Hedge / Second Leg):
        - Maker venue = BINANCE (A)
        - Maker side = BUY
        - Maker price = 60,010 (executable ask on A)
        - Hedge venue = BYBIT (B)
        - Hedge side = SELL
        - Hedge price = 60,200 (executable bid on B)

        Candidate B (B Maker / First Leg -> A Hedge / Second Leg):
        - Maker venue = BYBIT (B)
        - Maker side = SELL
        - Maker price = 60,200 (executable bid on B)
        - Hedge venue = BINANCE (A)
        - Hedge side = BUY
        - Hedge price = 60,010 (executable ask on A)
        """
        book_a = OrderBook(
            venue="BINANCE",
            symbol="BTC/USDT",
            bids=[OrderBookEntry(price=60000.0, size=2.5)],
            asks=[OrderBookEntry(price=60010.0, size=3.0)],
            timestamp=self.now,
        )
        book_b = OrderBook(
            venue="BYBIT",
            symbol="BTC/USDT",
            bids=[OrderBookEntry(price=60200.0, size=1.8)],
            asks=[OrderBookEntry(price=60210.0, size=2.2)],
            timestamp=self.now,
        )

        res = self.engine.evaluate_candidates(
            venue_a_data=book_a,
            venue_b_data=book_b,
            symbol="BTC/USDT",
            current_time=self.now,
        )

        cand_a = res.candidate_a
        self.assertEqual(cand_a.candidate_id, "A")
        self.assertEqual(cand_a.maker_venue, "BINANCE")
        self.assertEqual(cand_a.maker_side, "BUY")
        self.assertEqual(cand_a.maker_price, 60010.0)
        self.assertEqual(cand_a.hedge_venue, "BYBIT")
        self.assertEqual(cand_a.hedge_side, "SELL")
        self.assertEqual(cand_a.hedge_price, 60200.0)
        self.assertEqual(cand_a.direction, "BUY_A_SELL_B")
        self.assertTrue(cand_a.validity)

        cand_b = res.candidate_b
        self.assertEqual(cand_b.candidate_id, "B")
        self.assertEqual(cand_b.maker_venue, "BYBIT")
        self.assertEqual(cand_b.maker_side, "SELL")
        self.assertEqual(cand_b.maker_price, 60200.0)
        self.assertEqual(cand_b.hedge_venue, "BINANCE")
        self.assertEqual(cand_b.hedge_side, "BUY")
        self.assertEqual(cand_b.hedge_price, 60010.0)
        self.assertEqual(cand_b.direction, "BUY_A_SELL_B")
        self.assertTrue(cand_b.validity)

    def test_candidate_a_and_b_maker_hedge_assignment_buy_b_sell_a(self):
        """
        When Buy B @ 59,710 and Sell A @ 60,000 is favorable:
        Direction: BUY_B_SELL_A

        Candidate A (A Maker / First Leg -> B Hedge / Second Leg):
        - Maker venue = BINANCE (A)
        - Maker side = SELL
        - Maker price = 60,000 (executable bid on A)
        - Hedge venue = BYBIT (B)
        - Hedge side = BUY
        - Hedge price = 59,710 (executable ask on B)

        Candidate B (B Maker / First Leg -> A Hedge / Second Leg):
        - Maker venue = BYBIT (B)
        - Maker side = BUY
        - Maker price = 59,710 (executable ask on B)
        - Hedge venue = BINANCE (A)
        - Hedge side = SELL
        - Hedge price = 60,000 (executable bid on A)
        """
        book_a = OrderBook(
            venue="BINANCE",
            symbol="BTC/USDT",
            bids=[OrderBookEntry(price=60000.0, size=2.5)],
            asks=[OrderBookEntry(price=60010.0, size=3.0)],
            timestamp=self.now,
        )
        book_b = OrderBook(
            venue="BYBIT",
            symbol="BTC/USDT",
            bids=[OrderBookEntry(price=59700.0, size=1.8)],
            asks=[OrderBookEntry(price=59710.0, size=2.2)],
            timestamp=self.now,
        )

        res = self.engine.evaluate_candidates(
            venue_a_data=book_a,
            venue_b_data=book_b,
            symbol="BTC/USDT",
            current_time=self.now,
        )

        cand_a = res.candidate_a
        self.assertEqual(cand_a.candidate_id, "A")
        self.assertEqual(cand_a.maker_venue, "BINANCE")
        self.assertEqual(cand_a.maker_side, "SELL")
        self.assertEqual(cand_a.maker_price, 60000.0)
        self.assertEqual(cand_a.hedge_venue, "BYBIT")
        self.assertEqual(cand_a.hedge_side, "BUY")
        self.assertEqual(cand_a.hedge_price, 59710.0)
        self.assertEqual(cand_a.direction, "BUY_B_SELL_A")

        cand_b = res.candidate_b
        self.assertEqual(cand_b.candidate_id, "B")
        self.assertEqual(cand_b.maker_venue, "BYBIT")
        self.assertEqual(cand_b.maker_side, "BUY")
        self.assertEqual(cand_b.maker_price, 59710.0)
        self.assertEqual(cand_b.hedge_venue, "BINANCE")
        self.assertEqual(cand_b.hedge_side, "SELL")
        self.assertEqual(cand_b.hedge_price, 60000.0)
        self.assertEqual(cand_b.direction, "BUY_B_SELL_A")

    def test_never_assume_reference_is_hedge_or_priced_is_maker(self):
        """
        Explicit invariant test:
        Never assume:
        Reference = Hedge
        or
        Priced = Maker.

        Verify both combinations:
        1. When Reference = Binance, Priced = Bybit:
           Candidate A has Maker = Reference, Hedge = Priced
           Candidate B has Maker = Priced, Hedge = Reference
        2. When Reference = Bybit, Priced = Binance:
           Candidate A has Maker = Priced, Hedge = Reference
           Candidate B has Maker = Reference, Hedge = Priced
        """
        book_binance = OrderBook(
            venue="BINANCE",
            symbol="ETH/USDT",
            bids=[OrderBookEntry(price=3000.0, size=5.0)],
            asks=[OrderBookEntry(price=3001.0, size=5.0)],
            timestamp=self.now,
        )
        book_bybit = OrderBook(
            venue="BYBIT",
            symbol="ETH/USDT",
            bids=[OrderBookEntry(price=3020.0, size=5.0)],
            asks=[OrderBookEntry(price=3021.0, size=5.0)],
            timestamp=self.now,
        )

        # Case 1: Reference = BINANCE, Priced = BYBIT
        res1 = self.engine.evaluate_candidates(
            venue_a_data=book_binance,
            venue_b_data=book_bybit,
            symbol="ETH/USDT",
            reference_venue="BINANCE",
            priced_venue="BYBIT",
            current_time=self.now,
        )
        # In Candidate A, BINANCE (Reference) is Maker, NOT Hedge!
        self.assertEqual(res1.candidate_a.maker_venue, "BINANCE")
        self.assertEqual(res1.candidate_a.hedge_venue, "BYBIT")
        self.assertNotEqual(res1.candidate_a.hedge_venue, res1.reference_venue)

        # In Candidate B, BYBIT (Priced) is Maker, NOT Hedge!
        self.assertEqual(res1.candidate_b.maker_venue, "BYBIT")
        self.assertEqual(res1.candidate_b.hedge_venue, "BINANCE")
        self.assertNotEqual(res1.candidate_b.hedge_venue, res1.priced_venue)

        # Case 2: Reference = BYBIT, Priced = BINANCE
        res2 = self.engine.evaluate_candidates(
            venue_a_data=book_binance,
            venue_b_data=book_bybit,
            symbol="ETH/USDT",
            reference_venue="BYBIT",
            priced_venue="BINANCE",
            current_time=self.now,
        )
        # In Candidate A, BINANCE (Priced) is Maker, BYBIT (Reference) is Hedge
        self.assertEqual(res2.candidate_a.maker_venue, "BINANCE")
        self.assertEqual(res2.candidate_a.hedge_venue, "BYBIT")

        # In Candidate B, BYBIT (Reference) is Maker, BINANCE (Priced) is Hedge
        self.assertEqual(res2.candidate_b.maker_venue, "BYBIT")
        self.assertEqual(res2.candidate_b.hedge_venue, "BINANCE")

    def test_stale_market_data_rejects_both_candidates(self):
        """When either side's market data is stale (> 10s), both candidates return validity=False."""
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
            timestamp=self.now - 12.0,  # 12 seconds old
        )

        res = self.engine.evaluate_candidates(
            venue_a_data=fresh_book,
            venue_b_data=stale_book,
            symbol="BTC/USDT",
            stale_threshold_seconds=10.0,
            current_time=self.now,
        )

        self.assertFalse(res.candidate_a.validity)
        self.assertFalse(res.candidate_b.validity)
        self.assertIn("Stale market data", res.candidate_a.error_reason)
        self.assertIn("Stale market data", res.candidate_b.error_reason)

    def test_crossed_book_rejects_both_candidates(self):
        """When ask < bid on either venue, both candidates return validity=False."""
        crossed_book = OrderBook(
            venue="BINANCE",
            symbol="BTC/USDT",
            bids=[OrderBookEntry(price=60050.0, size=1.0)],
            asks=[OrderBookEntry(price=60000.0, size=1.0)],  # Crossed
            timestamp=self.now,
        )
        valid_book = OrderBook(
            venue="BYBIT",
            symbol="BTC/USDT",
            bids=[OrderBookEntry(price=60100.0, size=1.0)],
            asks=[OrderBookEntry(price=60110.0, size=1.0)],
            timestamp=self.now,
        )

        res = self.engine.evaluate_candidates(
            venue_a_data=crossed_book,
            venue_b_data=valid_book,
            symbol="BTC/USDT",
            current_time=self.now,
        )

        self.assertFalse(res.candidate_a.validity)
        self.assertFalse(res.candidate_b.validity)
        self.assertIn("Invalid or crossed", res.candidate_a.error_reason)

    def test_missing_data_rejects_both_candidates(self):
        """When either side's data is missing (None), both candidates return validity=False."""
        valid_book = OrderBook(
            venue="BINANCE",
            symbol="BTC/USDT",
            bids=[OrderBookEntry(price=60000.0, size=1.0)],
            asks=[OrderBookEntry(price=60010.0, size=1.0)],
            timestamp=self.now,
        )

        res = self.engine.evaluate_candidates(
            venue_a_data=valid_book,
            venue_b_data=None,
            symbol="BTC/USDT",
            venue_b_name="KRAKEN",
            current_time=self.now,
        )

        self.assertFalse(res.candidate_a.validity)
        self.assertFalse(res.candidate_b.validity)
        self.assertIn("KRAKEN", res.candidate_a.error_reason)

    def test_same_venue_diagonal_rejects_both_candidates(self):
        """Evaluating same venue against itself returns validity=False."""
        book = OrderBook(
            venue="BINANCE",
            symbol="BTC/USDT",
            bids=[OrderBookEntry(price=60000.0, size=1.0)],
            asks=[OrderBookEntry(price=60010.0, size=1.0)],
            timestamp=self.now,
        )

        res = self.engine.evaluate_candidates(
            venue_a_data=book,
            venue_b_data=book,
            symbol="BTC/USDT",
            current_time=self.now,
        )

        self.assertFalse(res.candidate_a.validity)
        self.assertFalse(res.candidate_b.validity)
        self.assertEqual(res.candidate_a.direction, "SAME_VENUE")

    def test_works_with_normalized_market_states(self):
        """Verify DirectionEngine works with NormalizedMarketState objects seamlessly."""
        norm_a = NormalizedMarketState(
            venue="BINANCE",
            symbol="SOL/USDT",
            best_bid=150.0,
            best_ask=150.05,
            bid_size=100.0,
            ask_size=80.0,
            timestamp=self.now,
            freshness=FreshnessStatus.FRESH.value,
            connection_status=ConnectionStatus.CONNECTED.value,
        )
        norm_b = NormalizedMarketState(
            venue="OKX",
            symbol="SOL/USDT",
            best_bid=150.40,
            best_ask=150.45,
            bid_size=50.0,
            ask_size=60.0,
            timestamp=self.now,
            freshness=FreshnessStatus.FRESH.value,
            connection_status=ConnectionStatus.CONNECTED.value,
        )

        res = self.engine.evaluate_candidates(
            venue_a_data=norm_a,
            venue_b_data=norm_b,
            symbol="SOL/USDT",
            current_time=self.now,
        )

        self.assertTrue(res.candidate_a.validity)
        self.assertTrue(res.candidate_b.validity)
        self.assertEqual(res.candidate_a.maker_venue, "BINANCE")
        self.assertEqual(res.candidate_a.hedge_venue, "OKX")
        self.assertEqual(res.candidate_b.maker_venue, "OKX")
        self.assertEqual(res.candidate_b.hedge_venue, "BINANCE")

    def test_spread_calculator_delegation_to_direction_engine(self):
        """Verify SpreadCalculator.evaluate_maker_hedge_candidates() delegates seamlessly."""
        book_a = OrderBook(
            venue="BINANCE",
            symbol="BTC/USDT",
            bids=[OrderBookEntry(price=60000.0, size=2.0)],
            asks=[OrderBookEntry(price=60010.0, size=2.0)],
            timestamp=self.now,
        )
        book_b = OrderBook(
            venue="BYBIT",
            symbol="BTC/USDT",
            bids=[OrderBookEntry(price=60200.0, size=2.0)],
            asks=[OrderBookEntry(price=60210.0, size=2.0)],
            timestamp=self.now,
        )

        res = self.calc.evaluate_maker_hedge_candidates(
            venue_a_data=book_a,
            venue_b_data=book_b,
            symbol="BTC/USDT",
            current_time=self.now,
        )

        self.assertIsInstance(res, DirectionEvaluationResult)
        self.assertEqual(res.candidate_a.maker_venue, "BINANCE")
        self.assertEqual(res.candidate_b.maker_venue, "BYBIT")

    def test_live_state_storage_and_snapshot_exposure(self):
        """Verify LiveStateManager stores and exposes direction candidates in get_live_snapshot()."""
        book_a = OrderBook(
            venue="BINANCE",
            symbol="BTC/USDT",
            bids=[OrderBookEntry(price=60000.0, size=2.0)],
            asks=[OrderBookEntry(price=60010.0, size=2.0)],
            timestamp=self.now,
        )
        book_b = OrderBook(
            venue="BYBIT",
            symbol="BTC/USDT",
            bids=[OrderBookEntry(price=60200.0, size=2.0)],
            asks=[OrderBookEntry(price=60210.0, size=2.0)],
            timestamp=self.now,
        )

        eval_res = self.engine.evaluate_candidates(
            venue_a_data=book_a,
            venue_b_data=book_b,
            symbol="BTC/USDT",
            current_time=self.now,
        )

        changed = self.live_state.set_direction_candidates(eval_res)
        self.assertTrue(changed)

        fetched = self.live_state.get_direction_candidates("BINANCE", "BYBIT", "BTC/USDT")
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched.candidate_a.maker_venue, "BINANCE")

        # Reverse lookup order also works
        fetched_rev = self.live_state.get_direction_candidates("BYBIT", "BINANCE", "BTC/USDT")
        self.assertIsNotNone(fetched_rev)

        # Snapshot exposure
        snap = self.live_state.get_live_snapshot()
        self.assertIn("direction_candidates", snap)
        self.assertIn("BINANCE<->BYBIT:BTC/USDT", snap["direction_candidates"])

    def test_zero_order_placement_invariant(self):
        """Verify that DirectionEngine is strictly evaluative with zero order placement."""
        # 1. Engine possesses no order execution methods
        self.assertFalse(hasattr(self.engine, "place_order"))
        self.assertFalse(hasattr(self.engine, "create_order"))
        self.assertFalse(hasattr(self.engine, "submit_order"))
        self.assertFalse(hasattr(self.engine, "execute"))

        book_a = OrderBook(
            venue="BINANCE",
            symbol="BTC/USDT",
            bids=[OrderBookEntry(price=60000.0, size=2.0)],
            asks=[OrderBookEntry(price=60010.0, size=2.0)],
            timestamp=self.now,
        )
        book_b = OrderBook(
            venue="BYBIT",
            symbol="BTC/USDT",
            bids=[OrderBookEntry(price=60200.0, size=2.0)],
            asks=[OrderBookEntry(price=60210.0, size=2.0)],
            timestamp=self.now,
        )

        res = self.engine.evaluate_candidates(
            venue_a_data=book_a,
            venue_b_data=book_b,
            symbol="BTC/USDT",
            current_time=self.now,
        )

        # 2. Evaluation returns data-only model instances with no side effects
        self.assertIsInstance(res, DirectionEvaluationResult)
        self.assertIsInstance(res.candidate_a, MakerHedgeCandidate)
        self.assertIsInstance(res.candidate_b, MakerHedgeCandidate)
        # Result contains pure market evaluations
        self.assertTrue(res.candidate_a.validity)
        self.assertTrue(res.candidate_b.validity)


if __name__ == "__main__":
    unittest.main()
