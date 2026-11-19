"""
Tests for Step 8: Opportunity Engine.

Requirements:
- Integrate directional spread, maker fee, taker fee, slippage, liquidity, and cost models.
- Keep Raw Spread strictly separate from Net Spread.
- Authoritative backend calculation of Net Spread % and profitability (frontend must not independently compute profitability).
- Return both directional opportunities (Candidate A & Candidate B) and their calculation components.
- Reuse existing direction engine and fee engine.
- Only real market data may enter the calculation.
- Stale/invalid data invalidates the opportunity rather than generating placeholders.
"""

import time
import unittest

from arbitrage.direction_engine import DirectionEngine
from arbitrage.fee_engine import FeeEngine
from arbitrage.live_state import LiveStateManager
from arbitrage.models import (
    DirectionEvaluationResult,
    DirectionalOpportunity,
    FreshnessStatus,
    NormalizedMarketState,
    OpportunityEvaluationResult,
    OrderBook,
    OrderBookEntry,
)
from arbitrage.opportunity_engine import OpportunityEngine
from arbitrage.spread_calculator import SpreadCalculator


class TestStep8OpportunityEngine(unittest.TestCase):
    def setUp(self):
        self.fee_engine = FeeEngine()
        self.direction_engine = DirectionEngine(fee_engine=self.fee_engine)
        self.opp_engine = OpportunityEngine(
            fee_engine=self.fee_engine,
            direction_engine=self.direction_engine,
            min_profit_threshold_pct=0.05,
        )
        self.calculator = SpreadCalculator(min_profit_threshold_pct=0.05)
        self.live_state = LiveStateManager()

    def test_authoritative_net_spread_and_deductions_calculation(self):
        """
        Verify authoritative Net Spread % formula:
        Net Spread % = Gross Spread % - Total Directional Fee % - Slippage % - Other Costs %
        """
        now = time.time()
        # Binance: bid 50000, ask 50005
        # Bybit: bid 50100, ask 50105
        book_binance = OrderBook(
            symbol="BTC/USDT",
            venue="BINANCE",
            bids=[OrderBookEntry(price=50000.0, size=2.0)],
            asks=[OrderBookEntry(price=50005.0, size=2.0)],
            timestamp=now,
        )
        book_bybit = OrderBook(
            symbol="BTC/USDT",
            venue="BYBIT",
            bids=[OrderBookEntry(price=50100.0, size=2.0)],
            asks=[OrderBookEntry(price=50105.0, size=2.0)],
            timestamp=now,
        )

        res = self.opp_engine.evaluate_directional_opportunities(
            venue_a_data=book_binance,
            venue_b_data=book_bybit,
            symbol="BTC/USDT",
            venue_a_name="BINANCE",
            venue_b_name="BYBIT",
            min_profit_threshold_pct=0.05,
            other_costs_pct=0.01,  # 1 bps transfer/other cost
        )

        self.assertTrue(res.validity)
        self.assertIsNotNone(res.opportunity_a)
        self.assertIsNotNone(res.opportunity_b)
        self.assertIsNotNone(res.highest_net_opportunity)

        opp_a = res.opportunity_a
        # Opportunity A: Binance Maker (Buy at 50005) -> Bybit Taker (Sell at 50100)
        # Gross Spread % = (50100 - 50005) / 50005 * 100 = 0.18998% = 0.19%
        # Directional fees: Binance Maker (0.10%) + Bybit Taker (0.10%) = 0.20%
        # Other costs = 0.01%
        # Net Spread % = 0.19% - 0.20% - 0.0% - 0.01% = -0.02%
        self.assertEqual(opp_a.maker_venue, "BINANCE")
        self.assertEqual(opp_a.hedge_venue, "BYBIT")
        expected_gross = round(((50100.0 - 50005.0) / 50005.0) * 100.0, 4)
        expected_net = round(expected_gross - 0.20 - 0.01, 4)
        self.assertAlmostEqual(opp_a.gross_spread_pct, expected_gross, places=3)
        self.assertAlmostEqual(opp_a.total_fee_pct, 0.20, places=3)
        self.assertAlmostEqual(opp_a.other_costs_pct, 0.01, places=3)
        self.assertAlmostEqual(opp_a.net_spread_pct, expected_net, places=3)
        self.assertFalse(opp_a.is_profitable)  # negative < 0.05% threshold

    def test_both_directional_opportunities_and_components_returned(self):
        """Verify both directional opportunities (A and B) and their calculation components are returned."""
        now = time.time()
        # OKX (rich venue): bid 3100, ask 3101
        # MEXC (cheap venue): bid 3000, ask 3001
        book_mexc = OrderBook(
            symbol="ETH/USDT",
            venue="MEXC",
            bids=[OrderBookEntry(price=3000.0, size=10.0)],
            asks=[OrderBookEntry(price=3001.0, size=10.0)],
            timestamp=now,
        )
        book_okx = OrderBook(
            symbol="ETH/USDT",
            venue="OKX",
            bids=[OrderBookEntry(price=3100.0, size=15.0)],
            asks=[OrderBookEntry(price=3101.0, size=15.0)],
            timestamp=now,
        )

        res = self.opp_engine.evaluate_directional_opportunities(
            venue_a_data=book_mexc,
            venue_b_data=book_okx,
            symbol="ETH/USDT",
            venue_a_name="MEXC",
            venue_b_name="OKX",
        )

        self.assertTrue(res.validity)
        # Opportunity A: MEXC Maker -> OKX Hedge
        self.assertEqual(res.opportunity_a.candidate_id, "A")
        self.assertEqual(res.opportunity_a.maker_venue, "MEXC")
        self.assertEqual(res.opportunity_a.hedge_venue, "OKX")
        self.assertIn("raw_spread_pct", res.opportunity_a.calculation_components)
        self.assertIn("gross_spread_pct", res.opportunity_a.calculation_components)
        self.assertIn("maker_fee_pct", res.opportunity_a.calculation_components)
        self.assertIn("taker_fee_pct", res.opportunity_a.calculation_components)
        self.assertIn("total_deductions_pct", res.opportunity_a.calculation_components)
        self.assertIn("net_spread_pct", res.opportunity_a.calculation_components)

        # Opportunity B: OKX Maker -> MEXC Hedge
        self.assertEqual(res.opportunity_b.candidate_id, "B")
        self.assertEqual(res.opportunity_b.maker_venue, "OKX")
        self.assertEqual(res.opportunity_b.hedge_venue, "MEXC")
        self.assertIn("raw_spread_pct", res.opportunity_b.calculation_components)
        self.assertIn("gross_spread_pct", res.opportunity_b.calculation_components)

    def test_raw_spread_remains_strictly_separate_from_net_spread(self):
        """
        Step 8 requirement: Raw Spread remains strictly separate from Net Spread.
        Raw Spread has 0 fees deducted.
        """
        now = time.time()
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
            bids=[OrderBookEntry(price=60200.0, size=1.0)],
            asks=[OrderBookEntry(price=60210.0, size=1.0)],
            timestamp=now,
        )

        res = self.opp_engine.evaluate_directional_opportunities(
            venue_a_data=book_a,
            venue_b_data=book_b,
            symbol="BTC/USDT",
            venue_a_name="BINANCE",
            venue_b_name="BYBIT",
        )

        opp_a = res.opportunity_a
        # Raw Spread % = (60200 - 60010) / 60010 * 100 = 0.3166%
        # Total Fees = 0.10% + 0.10% = 0.20%
        # Net Spread % = Gross Spread - Fees = 0.3333% - 0.20% = 0.1333%
        self.assertIsNotNone(opp_a.raw_spread_pct)
        self.assertIsNotNone(opp_a.net_spread_pct)
        self.assertNotEqual(opp_a.raw_spread_pct, opp_a.net_spread_pct)
        # Raw spread remains strictly independent of fee deduction
        expected_raw = round(((60200.0 - 60010.0) / 60010.0) * 100.0, 4)
        self.assertAlmostEqual(opp_a.raw_spread_pct, expected_raw)

    def test_highest_net_opportunity_selected_with_asymmetric_fees(self):
        """
        Highest Net Spread % selects the Maker direction.
        Proves asymmetric directional fee advantage:
        MEXC (0.00% maker, 0.10% taker) vs OKX (0.08% maker, 0.10% taker)
        """
        now = time.time()
        # Symmetric price gap:
        # MEXC: 50000 bid, 50010 ask
        # OKX:  50000 bid, 50010 ask
        # Suppose MEXC bid is 50000, OKX ask is 50010
        book_mexc = OrderBook(
            symbol="BTC/USDT",
            venue="MEXC",
            bids=[OrderBookEntry(price=50000.0, size=2.0)],
            asks=[OrderBookEntry(price=50005.0, size=2.0)],
            timestamp=now,
        )
        book_okx = OrderBook(
            symbol="BTC/USDT",
            venue="OKX",
            bids=[OrderBookEntry(price=50150.0, size=2.0)],
            asks=[OrderBookEntry(price=50155.0, size=2.0)],
            timestamp=now,
        )

        res = self.opp_engine.evaluate_directional_opportunities(
            venue_a_data=book_mexc,
            venue_b_data=book_okx,
            symbol="BTC/USDT",
            venue_a_name="MEXC",
            venue_b_name="OKX",
        )

        # Cand A: MEXC Maker (0.00%) + OKX Taker (0.10%) = 0.10% fee
        # Gross Spread = (50150 - 50000) / 50000 * 100 = 0.30%
        # Net Spread A = 0.30% - 0.10% = 0.20%
        self.assertIsNotNone(res.highest_net_opportunity)
        self.assertEqual(res.highest_net_opportunity.candidate_id, "A")
        self.assertEqual(res.highest_net_opportunity.maker_venue, "MEXC")
        self.assertGreater(res.opportunity_a.net_spread_pct, res.opportunity_b.net_spread_pct)

    def test_liquidity_sizing_across_legs(self):
        """Executable liquidity equals the minimum available size between maker and hedge venues."""
        now = time.time()
        book_a = OrderBook(
            symbol="SOL/USDT",
            venue="BINANCE",
            bids=[OrderBookEntry(price=150.0, size=40.0)],
            asks=[OrderBookEntry(price=150.1, size=35.0)],
            timestamp=now,
        )
        book_b = OrderBook(
            symbol="SOL/USDT",
            venue="BYBIT",
            bids=[OrderBookEntry(price=152.0, size=15.0)],  # Limited hedge liquidity
            asks=[OrderBookEntry(price=152.1, size=50.0)],
            timestamp=now,
        )

        res = self.opp_engine.evaluate_directional_opportunities(
            venue_a_data=book_a,
            venue_b_data=book_b,
            symbol="SOL/USDT",
            venue_a_name="BINANCE",
            venue_b_name="BYBIT",
        )

        opp_a = res.opportunity_a
        # Maker size on Binance is 40.0, but Hedge size on Bybit is 15.0
        # Executable liquidity must be min(40.0, 15.0) = 15.0
        self.assertEqual(opp_a.liquidity_size, 15.0)
        self.assertEqual(opp_a.liquidity_usd, 15.0 * 150.1)

    def test_depth_slippage_calculation(self):
        """When order book depth is available and target_size requested, VWAP slippage is computed."""
        book_depth = OrderBook(
            symbol="BTC/USDT",
            venue="BYBIT",
            bids=[
                OrderBookEntry(price=50000.0, size=1.0),
                OrderBookEntry(price=49900.0, size=1.0),  # Deeper level at lower price
            ],
            asks=[
                OrderBookEntry(price=50010.0, size=1.0),
                OrderBookEntry(price=50100.0, size=1.0),  # Deeper level at higher price
            ],
            timestamp=time.time(),
        )

        # For selling 2.0 BTC into bids: top bid = 50000.0 (1.0) + 49900.0 (1.0) -> VWAP = 49950.0
        # Slippage = (50000 - 49950) / 50000 * 100 = 0.10% (10 bps)
        slippage = self.opp_engine.calculate_depth_slippage(
            order_book=book_depth,
            side="sell",
            target_size=2.0,
            top_of_book_price=50000.0,
        )
        self.assertAlmostEqual(slippage, 0.10, places=2)

    def test_stale_market_data_invalidates_opportunity_without_placeholder(self):
        """
        Step 8 requirement: Stale market data must invalidate the opportunity
        rather than generating a placeholder!
        """
        old_time = time.time() - 30.0  # 30 seconds ago (stale > 10s)
        book_fresh = OrderBook(
            symbol="BTC/USDT",
            venue="BINANCE",
            bids=[OrderBookEntry(price=60000.0, size=1.0)],
            asks=[OrderBookEntry(price=60005.0, size=1.0)],
            timestamp=time.time(),
        )
        book_stale = OrderBook(
            symbol="BTC/USDT",
            venue="BYBIT",
            bids=[OrderBookEntry(price=60100.0, size=1.0)],
            asks=[OrderBookEntry(price=60105.0, size=1.0)],
            timestamp=old_time,
        )

        res = self.opp_engine.evaluate_directional_opportunities(
            venue_a_data=book_fresh,
            venue_b_data=book_stale,
            symbol="BTC/USDT",
            venue_a_name="BINANCE",
            venue_b_name="BYBIT",
            stale_threshold_seconds=10.0,
        )

        # Invariant: Must invalidate the opportunity!
        self.assertFalse(res.validity)
        self.assertIsNone(res.highest_net_opportunity)
        self.assertFalse(res.opportunity_a.validity)
        self.assertFalse(res.opportunity_b.validity)
        self.assertIsNone(res.opportunity_a.net_spread_pct)
        self.assertIsNone(res.opportunity_b.net_spread_pct)
        self.assertIn("stale", res.error_reason.lower())

    def test_crossed_or_invalid_market_data_invalidates_opportunity(self):
        """Crossed order book (bid > ask) must invalidate the opportunity."""
        now = time.time()
        book_valid = OrderBook(
            symbol="BTC/USDT",
            venue="BINANCE",
            bids=[OrderBookEntry(price=60000.0, size=1.0)],
            asks=[OrderBookEntry(price=60005.0, size=1.0)],
            timestamp=now,
        )
        book_crossed = OrderBook(
            symbol="BTC/USDT",
            venue="BYBIT",
            bids=[OrderBookEntry(price=60200.0, size=1.0)],
            asks=[OrderBookEntry(price=60100.0, size=1.0)],  # Crossed! ask < bid
            timestamp=now,
        )

        res = self.opp_engine.evaluate_directional_opportunities(
            venue_a_data=book_valid,
            venue_b_data=book_crossed,
            symbol="BTC/USDT",
            venue_a_name="BINANCE",
            venue_b_name="BYBIT",
        )

        self.assertFalse(res.validity)
        self.assertFalse(res.opportunity_a.validity)
        self.assertFalse(res.opportunity_b.validity)
        self.assertIn("crossed", res.error_reason.lower())

    def test_spread_calculator_delegates_to_opportunity_engine(self):
        """SpreadCalculator has evaluate_directional_opportunities method properly wired."""
        now = time.time()
        book_a = OrderBook(
            symbol="ETH/USDT",
            venue="BINANCE",
            bids=[OrderBookEntry(price=3000.0, size=5.0)],
            asks=[OrderBookEntry(price=3001.0, size=5.0)],
            timestamp=now,
        )
        book_b = OrderBook(
            symbol="ETH/USDT",
            venue="BYBIT",
            bids=[OrderBookEntry(price=3010.0, size=5.0)],
            asks=[OrderBookEntry(price=3011.0, size=5.0)],
            timestamp=now,
        )

        res = self.calculator.evaluate_directional_opportunities(
            venue_a_data=book_a,
            venue_b_data=book_b,
            symbol="ETH/USDT",
            venue_a_name="BINANCE",
            venue_b_name="BYBIT",
        )

        self.assertIsInstance(res, OpportunityEvaluationResult)
        self.assertTrue(res.validity)
        self.assertIsNotNone(res.highest_net_opportunity)

    def test_live_state_manager_directional_opportunities_caching(self):
        """LiveStateManager stores, retrieves by dual-key, and includes in live snapshot."""
        now = time.time()
        book_a = OrderBook(
            symbol="BTC/USDT",
            venue="BINANCE",
            bids=[OrderBookEntry(price=50000.0, size=1.0)],
            asks=[OrderBookEntry(price=50005.0, size=1.0)],
            timestamp=now,
        )
        book_b = OrderBook(
            symbol="BTC/USDT",
            venue="OKX",
            bids=[OrderBookEntry(price=50100.0, size=1.0)],
            asks=[OrderBookEntry(price=50105.0, size=1.0)],
            timestamp=now,
        )

        res = self.opp_engine.evaluate_directional_opportunities(
            venue_a_data=book_a,
            venue_b_data=book_b,
            symbol="BTC/USDT",
            venue_a_name="BINANCE",
            venue_b_name="OKX",
        )

        changed = self.live_state.set_directional_opportunities(res)
        self.assertTrue(changed)

        # Dual-key lookup: BINANCE <-> OKX or OKX <-> BINANCE
        lookup_forward = self.live_state.get_directional_opportunities("BINANCE", "OKX", "BTC/USDT")
        lookup_reverse = self.live_state.get_directional_opportunities("OKX", "BINANCE", "BTC/USDT")
        self.assertIsNotNone(lookup_forward)
        self.assertIsNotNone(lookup_reverse)
        self.assertEqual(lookup_forward.symbol, "BTC/USDT")
        self.assertEqual(lookup_reverse.symbol, "BTC/USDT")

        # Snapshot exposure
        snap = self.live_state.get_live_snapshot()
        self.assertIn("directional_opportunities", snap)
        self.assertIn("BINANCE<->OKX:BTC/USDT", snap["directional_opportunities"])

    def test_normalized_market_state_input_evaluation(self):
        """OpportunityEngine works seamlessly with NormalizedMarketState inputs."""
        now = time.time()
        norm_a = NormalizedMarketState(
            venue="BINANCE",
            symbol="BTC/USDT",
            best_bid=50000.0,
            best_ask=50005.0,
            bid_size=3.0,
            ask_size=4.0,
            timestamp=now,
            freshness=FreshnessStatus.FRESH.value,
        )
        norm_b = NormalizedMarketState(
            venue="BYBIT",
            symbol="BTC/USDT",
            best_bid=50200.0,
            best_ask=50205.0,
            bid_size=2.5,
            ask_size=5.0,
            timestamp=now,
            freshness=FreshnessStatus.FRESH.value,
        )

        res = self.opp_engine.evaluate_directional_opportunities(
            venue_a_data=norm_a,
            venue_b_data=norm_b,
            symbol="BTC/USDT",
            venue_a_name="BINANCE",
            venue_b_name="BYBIT",
        )

        self.assertTrue(res.validity)
        self.assertIsNotNone(res.opportunity_a)
        self.assertIsNotNone(res.opportunity_b)
        self.assertEqual(res.opportunity_a.maker_venue, "BINANCE")
        self.assertEqual(res.opportunity_a.hedge_venue, "BYBIT")
        # Liquidity size must be min(4.0, 2.5) = 2.5
        self.assertEqual(res.opportunity_a.liquidity_size, 2.5)
        # Raw Spread % must be separate and fee-independent
        expected_raw = round(((50200.0 - 50005.0) / 50005.0) * 100.0, 4)
        self.assertAlmostEqual(res.opportunity_a.raw_spread_pct, expected_raw)

    def test_custom_account_and_instrument_fees_in_opportunity_engine(self):
        """Custom account-level and instrument-level fees resolve correctly in OpportunityEngine."""
        now = time.time()
        # Register institutional VIP maker rebate for account 'VIP_MAKER' on Binance
        self.fee_engine.set_custom_fee(
            "BINANCE",
            maker_fee=-0.0001,  # -1 bps maker rebate
            taker_fee=0.0002,
            market_type="spot",
            account="VIP_MAKER",
        )

        book_a = OrderBook(
            symbol="BTC/FDUSD",
            venue="BINANCE",
            bids=[OrderBookEntry(price=50000.0, size=2.0)],
            asks=[OrderBookEntry(price=50005.0, size=2.0)],
            timestamp=now,
        )
        book_b = OrderBook(
            symbol="BTC/FDUSD",
            venue="BYBIT",
            bids=[OrderBookEntry(price=50150.0, size=2.0)],
            asks=[OrderBookEntry(price=50155.0, size=2.0)],
            timestamp=now,
        )

        res = self.opp_engine.evaluate_directional_opportunities(
            venue_a_data=book_a,
            venue_b_data=book_b,
            symbol="BTC/FDUSD",
            venue_a_name="BINANCE",
            venue_b_name="BYBIT",
            account_a="VIP_MAKER",
            instrument="BTC/FDUSD",
        )

        self.assertTrue(res.validity)
        opp_a = res.opportunity_a
        # Binance Maker fee (-0.01%) + Bybit Taker fee (0.10%) = 0.09% total fee
        self.assertAlmostEqual(opp_a.maker_fee_rate, -0.0001)
        self.assertAlmostEqual(opp_a.taker_fee_rate, 0.0010)
        self.assertAlmostEqual(opp_a.total_fee_pct, 0.09)

        # Net Spread % = Gross Spread - Total Fee %
        expected_gross = round(((50150.0 - 50005.0) / 50005.0) * 100.0, 4)
        expected_net = round(expected_gross - 0.09, 4)
        self.assertAlmostEqual(opp_a.gross_spread_pct, expected_gross)
        self.assertAlmostEqual(opp_a.net_spread_pct, expected_net)

    def test_zero_order_placement_invariant(self):
        """OpportunityEngine is purely analytical and cannot place or modify orders."""
        self.assertFalse(hasattr(self.opp_engine, "place_order"))
        self.assertFalse(hasattr(self.opp_engine, "create_order"))
        self.assertFalse(hasattr(self.opp_engine, "submit_order"))
        self.assertFalse(hasattr(self.opp_engine, "execute"))


if __name__ == "__main__":
    unittest.main()
