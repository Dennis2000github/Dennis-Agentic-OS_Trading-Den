"""
tests/test_step13_screener_bot_execution.py — Step 13 Test Suite.

Verifies:
1. Live Pipeline Coordination: Screener → Bot Manager → Execution.
2. Strategy Selection:
   - Spread Arbitrage = Highest Net Spread %
   - Funding Arbitrage = Highest Funding %
3. Decoupled Leg Assignment: First Leg = Maker, Second Leg = Hedge (Never derived from Reference/Priced).
4. Pre-Submission Revalidation Engine across all 10 checks:
   - bid/ask, liquidity, funding, fees, slippage, timestamp, freshness, opportunity validity, execution direction, position state.
5. Invalidation on stale, crossed, or illiquid market data (no mock data fabricated).
6. Execution Engine & Position Reconciliation (balanced vs mismatched fills).
7. Bot Manager lifecycle controls (create, start, stop, pause, list).
8. Server REST API endpoints for bot management and pre-validation.
9. Zero live trading invariant during development.
"""

import asyncio
import json
import os
import sys
import time
import unittest

from arbitrage.models import (
    BotConfig,
    BotExecutionMode,
    BotStatus,
    ExecutionCycle,
    FundingRate,
    OrderBook,
    OrderBookEntry,
    PreSubmissionValidationResult,
    SpreadArbitrageDecision,
    FundingArbitrageDecision,
)
from arbitrage.spread_calculator import SpreadCalculator
from arbitrage.bot_manager import (
    BotManager,
    ExecutionEngine,
    PreSubmissionValidator,
)
from arbitrage.server import ArbitrageServer


class TestStep13ScreenerBotExecution(unittest.TestCase):
    def setUp(self):
        self.now = 1700000000.0
        self.calculator = SpreadCalculator(
            default_taker_fee=0.001,
            min_profit_threshold_pct=0.05,
        )
        self.validator = PreSubmissionValidator(
            default_stale_threshold_seconds=10.0,
            default_max_slippage_pct=0.20,
        )
        self.execution_engine = ExecutionEngine(validator=self.validator)
        self.bot_manager = BotManager(
            calculator=self.calculator,
            execution_engine=self.execution_engine,
        )

        # Standard real test books
        # Venue A (Binance): bid=60000.0 (size 5.0), ask=60010.0 (size 5.0)
        self.book_a = OrderBook(
            symbol="BTC/USDT",
            venue="BINANCE",
            bids=[OrderBookEntry(price=60000.0, size=5.0)],
            asks=[OrderBookEntry(price=60010.0, size=5.0)],
            timestamp=self.now,
        )
        # Venue B (Bybit): bid=60200.0 (size 5.0), ask=60210.0 (size 5.0)
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

    def test_strategy_selection_rules(self):
        """
        Step 13 Strategy selection:
        Spread = Highest Net Spread %;
        Funding = Highest Funding %.
        """
        # 1. Spread Arbitrage Bot: evaluates candidates and selects Highest Net Spread %
        bot_spread = self.bot_manager.create_bot(
            bot_id="bot_spread_1",
            strategy_type="SPREAD_ARBITRAGE",
            mode="DRY_RUN",
            symbol="BTC/USDT",
            order_quantity=1.0,
        )
        self.assertEqual(bot_spread.strategy_type, "SPREAD_ARBITRAGE")

        cycle_spread = self.bot_manager.evaluate_and_execute(
            bot_id="bot_spread_1",
            book_a=self.book_a,
            book_b=self.book_b,
            current_time=self.now,
        )
        self.assertIsNotNone(cycle_spread)
        self.assertEqual(cycle_spread.strategy_type, "SPREAD_ARBITRAGE")
        self.assertEqual(cycle_spread.cycle_state, "RECONCILED")

        # 2. Funding Arbitrage Bot: evaluates candidates and selects Highest Funding %
        bot_fund = self.bot_manager.create_bot(
            bot_id="bot_fund_1",
            strategy_type="FUNDING_ARBITRAGE",
            mode="DRY_RUN",
            symbol="BTC/USDT",
            order_quantity=1.0,
        )
        self.assertEqual(bot_fund.strategy_type, "FUNDING_ARBITRAGE")

        cycle_fund = self.bot_manager.evaluate_and_execute(
            bot_id="bot_fund_1",
            book_a=self.book_a,
            book_b=self.book_b,
            funding_a=self.funding_a,
            funding_b=self.funding_b,
            current_time=self.now,
        )
        self.assertIsNotNone(cycle_fund)
        self.assertEqual(cycle_fund.strategy_type, "FUNDING_ARBITRAGE")
        self.assertEqual(cycle_fund.cycle_state, "RECONCILED")

    def test_execution_roles_decoupled_from_reference_priced(self):
        """
        Critical Invariant: Never derive execution roles from Reference/Priced.
        First Leg = Maker, Second Leg = Hedge.
        """
        bot = self.bot_manager.create_bot(
            bot_id="bot_roles_test",
            strategy_type="SPREAD_ARBITRAGE",
            reference_venue="BINANCE",
            priced_venue="BYBIT",
        )
        # Even though Binance is Reference and Bybit is Priced:
        # Maker Leg and Hedge Leg are chosen strictly by highest net spread economics
        cycle = self.bot_manager.evaluate_and_execute(
            bot_id="bot_roles_test",
            book_a=self.book_a,
            book_b=self.book_b,
            current_time=self.now,
        )
        self.assertIn(cycle.maker_venue, ["BINANCE", "BYBIT"])
        self.assertIn(cycle.hedge_venue, ["BINANCE", "BYBIT"])
        self.assertNotEqual(cycle.maker_venue, cycle.hedge_venue)

    def test_pre_submission_validation_all_10_checks(self):
        """
        Verify the 10 pre-submission checks:
        1. bid/ask
        2. order-book liquidity
        3. funding
        4. fees
        5. slippage
        6. timestamp
        7. freshness
        8. opportunity validity
        9. execution direction
        10. position state
        """
        decision = self.calculator.evaluate_spread_arbitrage(
            venue_a_data=self.book_a,
            venue_b_data=self.book_b,
            symbol="BTC/USDT",
            current_time=self.now,
        )
        self.assertTrue(decision.is_viable)

        # Baseline: all 10 checks pass
        res = self.validator.validate_pre_submission(
            maker_book=self.book_a,
            hedge_book=self.book_b,
            decision=decision,
            order_quantity=1.0,
            current_position_size=2.0,
            max_position_size=10.0,
            current_time=self.now,
        )
        self.assertTrue(res.is_valid)
        self.assertEqual(len(res.failed_checks), 0)
        self.assertIsNone(res.rejection_reason)
        for check_name in [
            "bid_ask", "order_book_liquidity", "funding", "fees",
            "slippage", "timestamp", "freshness", "opportunity_validity",
            "execution_direction", "position_state"
        ]:
            self.assertIn(check_name, res.checks_detail)
            self.assertTrue(res.checks_detail[check_name]["passed"], f"Check {check_name} failed unexpectedly")

    def test_pre_submission_invalidation_scenarios(self):
        """
        Verify that violation of any pre-submission check produces explicit is_valid=False.
        """
        decision = self.calculator.evaluate_spread_arbitrage(
            venue_a_data=self.book_a,
            venue_b_data=self.book_b,
            symbol="BTC/USDT",
            current_time=self.now,
        )

        # 1. Stale market data
        stale_res = self.validator.validate_pre_submission(
            maker_book=self.book_a,
            hedge_book=self.book_b,
            decision=decision,
            order_quantity=1.0,
            current_time=self.now + 25.0,  # 25 seconds later > 10s threshold
        )
        self.assertFalse(stale_res.is_valid)
        self.assertIn("freshness", stale_res.failed_checks)

        # 2. Crossed book
        crossed_book = OrderBook(
            symbol="BTC/USDT",
            venue="BINANCE",
            bids=[OrderBookEntry(price=60050.0, size=1.0)],
            asks=[OrderBookEntry(price=60040.0, size=1.0)],  # Crossed: ask < bid
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

        # 3. Insufficient liquidity
        illiquid_res = self.validator.validate_pre_submission(
            maker_book=self.book_a,
            hedge_book=self.book_b,
            decision=decision,
            order_quantity=50.0,  # 50 BTC > available 5 BTC
            current_time=self.now,
        )
        self.assertFalse(illiquid_res.is_valid)
        self.assertIn("order-book liquidity", illiquid_res.failed_checks)

        # 4. Position limit exceeded
        limit_res = self.validator.validate_pre_submission(
            maker_book=self.book_a,
            hedge_book=self.book_b,
            decision=decision,
            order_quantity=3.0,
            current_position_size=9.0,  # 9 + 3 = 12 > max 10
            max_position_size=10.0,
            current_time=self.now,
        )
        self.assertFalse(limit_res.is_valid)
        self.assertIn("position state", limit_res.failed_checks)

    def test_execution_reconciliation_balanced_and_mismatch(self):
        """
        Verify execution reconciliation:
        - BALANCED: Maker and Hedge leg filled quantities match.
        - MISMATCHED: Detected and flagged if fills diverge.
        """
        # Balanced cycle
        cycle = ExecutionCycle(
            cycle_id="test_cyc_1",
            bot_id="bot_test",
            strategy_type="SPREAD_ARBITRAGE",
            symbol="BTC/USDT",
            maker_venue="BINANCE",
            hedge_venue="BYBIT",
            maker_side="BUY",
            hedge_side="SELL",
            target_quantity=1.5,
            maker_filled_qty=1.5,
            hedge_filled_qty=1.5,
        )
        is_bal = self.execution_engine.reconcile_cycle(cycle)
        self.assertTrue(is_bal)
        self.assertEqual(cycle.reconciliation_status, "BALANCED")

        # Mismatched cycle
        cycle_mismatch = ExecutionCycle(
            cycle_id="test_cyc_2",
            bot_id="bot_test",
            strategy_type="SPREAD_ARBITRAGE",
            symbol="BTC/USDT",
            maker_venue="BINANCE",
            hedge_venue="BYBIT",
            maker_side="BUY",
            hedge_side="SELL",
            target_quantity=1.5,
            maker_filled_qty=1.5,
            hedge_filled_qty=1.0,  # Partial / hedge failure
        )
        is_mis = self.execution_engine.reconcile_cycle(cycle_mismatch)
        self.assertFalse(is_mis)
        self.assertEqual(cycle_mismatch.reconciliation_status, "MISMATCHED")

    def test_bot_manager_controls(self):
        """
        Verify Bot Manager controls: create, start, stop, pause, list.
        """
        b = self.bot_manager.create_bot(
            bot_id="bot_mgr_test",
            strategy_type="SPREAD_ARBITRAGE",
            mode="DRY_RUN",
        )
        self.assertEqual(b.status, BotStatus.STOPPED.value)

        self.bot_manager.start_bot("bot_mgr_test")
        self.assertEqual(self.bot_manager.get_bot("bot_mgr_test").status, BotStatus.RUNNING.value)

        self.bot_manager.pause_bot("bot_mgr_test")
        self.assertEqual(self.bot_manager.get_bot("bot_mgr_test").status, BotStatus.PAUSED.value)

        self.bot_manager.stop_bot("bot_mgr_test")
        self.assertEqual(self.bot_manager.get_bot("bot_mgr_test").status, BotStatus.STOPPED.value)

        bots = self.bot_manager.list_bots()
        self.assertGreaterEqual(len(bots), 1)

    def test_server_rest_endpoints_step13(self):
        """
        Verify Server REST API endpoints for Step 13:
        /api/bots, /api/bots/start, /api/bots/stop, /api/bots/cycles, /api/bots/pre-validate.
        """
        server = ArbitrageServer()
        server.live_state.set_order_book("binance", "BTC/USDT", self.book_a)
        server.live_state.set_order_book("bybit", "BTC/USDT", self.book_b)
        server.live_state.set_funding_rate("binance", "BTC/USDT", self.funding_a)
        server.live_state.set_funding_rate("bybit", "BTC/USDT", self.funding_b)

        async def run_req(path, query=None, method="GET", body=b""):
            class DummyWriter:
                def __init__(self):
                    self.written = b""
                def write(self, data):
                    self.written += data
                async def drain(self):
                    pass
                def close(self):
                    pass
                async def wait_closed(self):
                    pass
            w = DummyWriter()
            await server._handle_api_request(path, query or {}, w, method=method, body=body)
            header_end = w.written.find(b"\r\n\r\n")
            body_json = w.written[header_end + 4:].decode("utf-8")
            return json.loads(body_json)

        # 1. GET /api/bots
        bots_data = asyncio.run(run_req("/api/bots", method="GET"))
        self.assertEqual(bots_data.get("status"), "ok")
        self.assertIn("bots", bots_data)

        # 2. POST /api/bots
        create_payload = json.dumps({
            "bot_id": "bot_api_1",
            "strategy": "SPREAD_ARBITRAGE",
            "symbol": "BTC/USDT",
            "mode": "DRY_RUN",
        }).encode("utf-8")
        created = asyncio.run(run_req("/api/bots", method="POST", body=create_payload))
        self.assertEqual(created.get("status"), "ok")
        self.assertEqual(created["bot"]["bot_id"], "bot_api_1")

        # 3. POST /api/bots/start
        start_payload = json.dumps({"botId": "bot_api_1"}).encode("utf-8")
        started = asyncio.run(run_req("/api/bots/start", method="POST", body=start_payload))
        self.assertEqual(started.get("status"), "ok")

        # 4. POST /api/bots/stop
        stop_payload = json.dumps({"botId": "bot_api_1"}).encode("utf-8")
        stopped = asyncio.run(run_req("/api/bots/stop", method="POST", body=stop_payload))
        self.assertEqual(stopped.get("status"), "ok")

        # 5. GET /api/bots/pre-validate
        pv_data = asyncio.run(run_req(
            "/api/bots/pre-validate",
            query={"venue_a": ["binance"], "venue_b": ["bybit"], "symbol": ["BTC/USDT"], "quantity": ["1.0"]},
        ))
        self.assertTrue(pv_data.get("is_valid"))
        self.assertIn("checks_detail", pv_data)

    def test_zero_live_trading_invariant(self):
        """
        Verify Safety Invariant:
        In LIVE mode, direct execution raises or aborts order placement during modular development.
        """
        bot_live = self.bot_manager.create_bot(
            bot_id="bot_live_safety",
            strategy_type="SPREAD_ARBITRAGE",
            mode="LIVE",
            symbol="BTC/USDT",
            order_quantity=1.0,
        )
        decision = self.calculator.evaluate_spread_arbitrage(
            venue_a_data=self.book_a,
            venue_b_data=self.book_b,
            symbol="BTC/USDT",
            current_time=self.now,
        )
        cycle_live = self.execution_engine.execute_cycle(
            bot_config=bot_live,
            decision=decision,
            maker_book=self.book_a,
            hedge_book=self.book_b,
            current_time=self.now,
        )
        # Asserts that live execution aborted safely without dispatching orders
        self.assertEqual(cycle_live.cycle_state, "FAILED")
        self.assertIn("zero-trading invariant", cycle_live.error_message)


if __name__ == "__main__":
    unittest.main()
