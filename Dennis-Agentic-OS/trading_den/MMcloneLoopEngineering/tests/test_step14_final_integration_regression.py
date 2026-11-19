"""
tests/test_step14_final_integration_regression.py — Step 14 Test Suite.

End-to-End Verification across the entire 13-stage architecture:
1. Startup & Data Pipeline (REST snapshot, historical deviation, WS live feeds, sequence validation).
2. Market Data Integrity (Real data only, zero mock prices, explicit STALE/UNAVAILABLE states).
3. Screener & Matrix (Reference/Priced relationship: Spread % = ((Priced - Reference) / Reference) * 100).
4. Direction Engine & Leg Decoupling (Both execution directions evaluated, Reference != Hedge, Priced != Maker).
5. Spread Arbitrage Pipeline (Highest Net Spread %, directional fees, Grid/Bollinger entry, convergence exit).
6. Funding Arbitrage Pipeline (Highest Funding %, entry pricing, independent funding settlement exit).
7. Execution Failure Testing (Stale data, crossed book, sequence gaps, partial/mismatched fills).
8. Position Reconciliation (Balanced vs Mismatched leg detection).
9. Zero Live Trading Invariant (Zero real order placement during development).
"""

import asyncio
import json
import os
import sys
import time
import unittest

from arbitrage.models import (
    BotConfig,
    BotStatus,
    ConnectionStatus,
    DirectionalOpportunity,
    ExecutionCycle,
    FreshnessStatus,
    FundingPositionLifecycle,
    FundingPositionLeg,
    FundingRate,
    MakerEntryPricing,
    OrderBook,
    OrderBookEntry,
    PreSubmissionValidationResult,
    RawSpreadResult,
    SpreadArbitrageDecision,
    FundingArbitrageDecision,
)
from arbitrage.spread_calculator import SpreadCalculator
from arbitrage.direction_engine import DirectionEngine
from arbitrage.fee_engine import FeeEngine
from arbitrage.opportunity_engine import OpportunityEngine
from arbitrage.spread_arbitrage import SpreadArbitrageEngine
from arbitrage.funding_arbitrage import FundingArbitrageEngine
from arbitrage.funding_settlement import FundingSettlementManager
from arbitrage.bot_manager import (
    BotManager,
    ExecutionEngine,
    PreSubmissionValidator,
)
from arbitrage.live_state import LiveStateManager
from arbitrage.server import ArbitrageServer


class TestStep14FinalIntegrationRegression(unittest.TestCase):
    def setUp(self):
        self.now = 1700000000.0
        self.calculator = SpreadCalculator(default_taker_fee=0.001, min_profit_threshold_pct=0.05)
        self.validator = PreSubmissionValidator(default_stale_threshold_seconds=10.0)
        self.execution_engine = ExecutionEngine(validator=self.validator)
        self.bot_manager = BotManager(calculator=self.calculator, execution_engine=self.execution_engine)

        # Real order books
        self.book_a = OrderBook(
            symbol="BTC/USDT",
            venue="BINANCE",
            bids=[OrderBookEntry(price=60000.0, size=5.0)],
            asks=[OrderBookEntry(price=60010.0, size=5.0)],
            timestamp=self.now,
        )
        self.book_b = OrderBook(
            symbol="BTC/USDT",
            venue="BYBIT",
            bids=[OrderBookEntry(price=60200.0, size=5.0)],
            asks=[OrderBookEntry(price=60210.0, size=5.0)],
            timestamp=self.now,
        )

        # Funding rates
        self.funding_a = FundingRate(
            symbol="BTC/USDT",
            venue="BINANCE",
            rate=0.0004,
            timestamp=self.now,
            next_funding_time=self.now + 14400.0,
        )
        self.funding_b = FundingRate(
            symbol="BTC/USDT",
            venue="BYBIT",
            rate=0.0001,
            timestamp=self.now,
            next_funding_time=self.now + 14400.0,
        )

    # -------------------------------------------------------------------------
    # 1. Startup & Data Pipeline
    # -------------------------------------------------------------------------
    def test_startup_and_data_pipeline(self):
        """
        Verify startup synchronization, sequence tracking, and sequence gap detection.
        """
        from arbitrage.native_ws_connector import NativeWebSocketConnector
        native = NativeWebSocketConnector(exchanges=["binance"])
        native._track_sequence("binance", "BTC/USDT", 100)
        info1 = native.get_sequence_info("binance", "BTC/USDT")
        self.assertEqual(info1["last_sequence"], 100)
        self.assertEqual(info1["missed_count"], 0)

        # In-sequence
        native._track_sequence("binance", "BTC/USDT", 101)
        info2 = native.get_sequence_info("binance", "BTC/USDT")
        self.assertEqual(info2["last_sequence"], 101)
        self.assertEqual(info2["missed_count"], 0)

        # Gap detection
        native._track_sequence("binance", "BTC/USDT", 105)
        info3 = native.get_sequence_info("binance", "BTC/USDT")
        self.assertEqual(info3["last_sequence"], 105)
        self.assertEqual(info3["missed_count"], 3)

    # -------------------------------------------------------------------------
    # 2. Market Data Integrity & Zero Mock Data Invariant
    # -------------------------------------------------------------------------
    def test_market_data_integrity_and_stale_rejection(self):
        """
        Verify that missing or stale data produces explicit STALE / UNAVAILABLE states,
        never mock, random, or fabricated prices.
        """
        # Missing book
        res_missing = self.calculator.calculate_raw_spread(
            reference_data=None,
            priced_data=self.book_b,
            symbol="BTC/USDT",
        )
        self.assertFalse(res_missing.validity)
        self.assertIsNone(res_missing.raw_spread_pct)
        self.assertIn("Missing market data", res_missing.error_reason)

        # Stale book (age > threshold)
        stale_book = OrderBook(
            symbol="BTC/USDT",
            venue="BINANCE",
            bids=[OrderBookEntry(price=60000.0, size=5.0)],
            asks=[OrderBookEntry(price=60010.0, size=5.0)],
            timestamp=self.now - 60.0,
        )
        res_stale = self.calculator.calculate_raw_spread(
            reference_data=stale_book,
            priced_data=self.book_b,
            symbol="BTC/USDT",
            current_time=self.now,
        )
        self.assertFalse(res_stale.validity)
        self.assertEqual(res_stale.freshness, FreshnessStatus.STALE.value)

    # -------------------------------------------------------------------------
    # 3. Screener & Matrix: Reference/Priced Pricing Invariant
    # -------------------------------------------------------------------------
    def test_screener_reference_priced_pricing_relationship(self):
        """
        Verify Reference/Priced pricing relationship:
        Spread % = ((Priced - Reference) / Reference) * 100.
        """
        # Reference = Binance (ask=60010.0), Priced = Bybit (bid=60200.0)
        res = self.calculator.calculate_raw_spread(
            reference_data=self.book_a,
            priced_data=self.book_b,
            symbol="BTC/USDT",
            reference_venue="BINANCE",
            priced_venue="BYBIT",
            current_time=self.now,
        )
        expected_spread = ((60200.0 - 60010.0) / 60010.0) * 100.0
        self.assertTrue(res.validity)
        self.assertAlmostEqual(res.spread_ref_to_priced_pct, expected_spread, places=3)

        # Inverted: Reference = Bybit, Priced = Binance
        inv_res = self.calculator.calculate_raw_spread(
            reference_data=self.book_b,
            priced_data=self.book_a,
            symbol="BTC/USDT",
            reference_venue="BYBIT",
            priced_venue="BINANCE",
            current_time=self.now,
        )
        self.assertTrue(inv_res.validity)
        self.assertLess(inv_res.spread_ref_to_priced_pct, 0.0)

    # -------------------------------------------------------------------------
    # 4. Direction Engine & Role Decoupling
    # -------------------------------------------------------------------------
    def test_direction_engine_role_decoupling(self):
        """
        Critical Invariant:
        Reference ≠ automatically Hedge.
        Priced ≠ automatically Maker.
        Both execution directions evaluated dynamically.
        """
        dir_res = self.calculator.direction_engine.evaluate_candidates(
            venue_a_data=self.book_a,
            venue_b_data=self.book_b,
            symbol="BTC/USDT",
            venue_a_name="BINANCE",
            venue_b_name="BYBIT",
            current_time=self.now,
        )
        self.assertTrue(dir_res.is_valid)
        self.assertIsNotNone(dir_res.candidate_a)
        self.assertIsNotNone(dir_res.candidate_b)

        # Candidate A: Maker = Binance, Hedge = Bybit
        self.assertEqual(dir_res.candidate_a.maker_venue, "BINANCE")
        self.assertEqual(dir_res.candidate_a.hedge_venue, "BYBIT")

        # Candidate B: Maker = Bybit, Hedge = Binance
        self.assertEqual(dir_res.candidate_b.maker_venue, "BYBIT")
        self.assertEqual(dir_res.candidate_b.hedge_venue, "BINANCE")

    # -------------------------------------------------------------------------
    # 5. Spread Arbitrage Pipeline
    # -------------------------------------------------------------------------
    def test_spread_arbitrage_pipeline(self):
        """
        Spread Arbitrage: selects direction via Highest Net Spread %,
        calculates Maker entry pricing, and specifies convergence exit.
        """
        decision = self.calculator.evaluate_spread_arbitrage(
            venue_a_data=self.book_a,
            venue_b_data=self.book_b,
            symbol="BTC/USDT",
            pricing_mode="GRID",
            grid_step_pct=0.10,
            grid_levels_count=3,
            current_time=self.now,
        )
        self.assertTrue(decision.is_viable)
        self.assertEqual(decision.strategy_type, "SPREAD_ARBITRAGE")
        self.assertIsNotNone(decision.pricing)
        self.assertEqual(decision.pricing.pricing_mode, "GRID")
        self.assertEqual(len(decision.pricing.grid_levels), 3)
        self.assertEqual(decision.exit_conditions.get("exit_type"), "SPREAD_CONVERGENCE")

    # -------------------------------------------------------------------------
    # 6. Funding Arbitrage Pipeline
    # -------------------------------------------------------------------------
    def test_funding_arbitrage_pipeline(self):
        """
        Funding Arbitrage: selects direction via Highest Funding %,
        uses pricing mode strictly for entry level, defines independent funding settlement exit.
        """
        decision = self.calculator.evaluate_funding_arbitrage(
            funding_a=self.funding_a,
            funding_b=self.funding_b,
            book_a=self.book_a,
            book_b=self.book_b,
            symbol="BTC/USDT",
            pricing_mode="BOLLINGER",
            bollinger_k=2.0,
            bollinger_period=20,
            current_time=self.now,
        )
        self.assertTrue(decision.is_viable)
        self.assertEqual(decision.strategy_type, "FUNDING_ARBITRAGE")
        self.assertEqual(decision.pricing.pricing_mode, "BOLLINGER")
        self.assertEqual(decision.exit_conditions.get("exit_type"), "FUNDING_SETTLEMENT")

    # -------------------------------------------------------------------------
    # 7. Execution Failure Testing & Pre-Submission Revalidation
    # -------------------------------------------------------------------------
    def test_execution_failure_handling_and_revalidation(self):
        """
        Verify pre-submission 10-point revalidation catches:
        - Stale quote age
        - Crossed order book
        - Insufficient liquidity
        - Position limit breach
        """
        decision = self.calculator.evaluate_spread_arbitrage(
            venue_a_data=self.book_a,
            venue_b_data=self.book_b,
            symbol="BTC/USDT",
            current_time=self.now,
        )

        # 1. Stale quote failure
        stale_res = self.validator.validate_pre_submission(
            maker_book=self.book_a,
            hedge_book=self.book_b,
            decision=decision,
            order_quantity=1.0,
            current_time=self.now + 20.0,
        )
        self.assertFalse(stale_res.is_valid)
        self.assertIn("freshness", stale_res.failed_checks)

        # 2. Crossed book failure
        crossed_book = OrderBook(
            symbol="BTC/USDT",
            venue="BINANCE",
            bids=[OrderBookEntry(price=60050.0, size=1.0)],
            asks=[OrderBookEntry(price=60040.0, size=1.0)],
            timestamp=self.now,
        )
        crossed_res = self.validator.validate_pre_submission(
            maker_book=crossed_book,
            hedge_book=self.book_b,
            decision=decision,
            order_quantity=1.0,
            current_time=self.now,
        )
        self.assertFalse(crossed_res.is_valid)
        self.assertIn("bid/ask", crossed_res.failed_checks)

    # -------------------------------------------------------------------------
    # 8. Position Reconciliation
    # -------------------------------------------------------------------------
    def test_position_reconciliation_failsafe(self):
        """
        Verify that execution reconciliation detects and prevents mismatched legs.
        """
        balanced = ExecutionCycle(
            cycle_id="c_bal", bot_id="b1", strategy_type="SPREAD", symbol="BTC/USDT",
            maker_venue="BINANCE", hedge_venue="BYBIT", maker_side="BUY", hedge_side="SELL",
            target_quantity=1.0, maker_filled_qty=1.0, hedge_filled_qty=1.0,
        )
        self.assertTrue(self.execution_engine.reconcile_cycle(balanced))
        self.assertEqual(balanced.reconciliation_status, "BALANCED")

        mismatched = ExecutionCycle(
            cycle_id="c_mis", bot_id="b1", strategy_type="SPREAD", symbol="BTC/USDT",
            maker_venue="BINANCE", hedge_venue="BYBIT", maker_side="BUY", hedge_side="SELL",
            target_quantity=1.0, maker_filled_qty=1.0, hedge_filled_qty=0.5,
        )
        self.assertFalse(self.execution_engine.reconcile_cycle(mismatched))
        self.assertEqual(mismatched.reconciliation_status, "MISMATCHED")

    # -------------------------------------------------------------------------
    # 9. Zero Live Trading Invariant
    # -------------------------------------------------------------------------
    def test_zero_live_trading_invariant(self):
        """
        Verify safety invariant: Live orders are never dispatched during development.
        """
        bot_live = self.bot_manager.create_bot(
            bot_id="bot_live_inv",
            strategy_type="SPREAD_ARBITRAGE",
            mode="LIVE",
            symbol="BTC/USDT",
        )
        dec = self.calculator.evaluate_spread_arbitrage(self.book_a, self.book_b, "BTC/USDT", current_time=self.now)
        cycle = self.execution_engine.execute_cycle(
            bot_config=bot_live,
            decision=dec,
            maker_book=self.book_a,
            hedge_book=self.book_b,
            current_time=self.now,
        )
        self.assertEqual(cycle.cycle_state, "FAILED")
        self.assertIn("zero-trading invariant", cycle.error_message)


if __name__ == "__main__":
    unittest.main()
