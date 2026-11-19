"""
Tests for Step 12: Arbitrage Tab + Charts Integration.

Requirements from modular/step-12.md:
- Integrate completed components into the existing Arbitrage UI / Server.
- Use: Reference Venue, Priced Venue.
- Show actual execution: Maker / First Leg -> Taker / Hedge.
- Invariant: Do not imply Reference/Priced -> execution role.
- Integrate real:
    market data;
    Raw Spread;
    Net Spread;
    funding;
    funding direction;
    selected direction;
    Maker/Hedge;
    strategy mode.
- Charts must use real normalized market data.
- Chart relationship must correctly represent Reference/Priced pricing.
- Strategy Settings must dynamically support: Spread Arbitrage, Funding Arbitrage.
- Preserve existing chart layout, order books, Strategy Settings, Execution Console, styling.
- Zero live trading invariant.
"""

import asyncio
import json
import time
import unittest

from arbitrage.models import (
    FundingRate,
    OrderBook,
    OrderBookEntry,
    NormalizedMarketState,
    RawSpreadResult,
    SpreadArbitrageDecision,
    FundingArbitrageDecision,
)
from arbitrage.spread_calculator import SpreadCalculator
from arbitrage.spread_arbitrage import SpreadArbitrageEngine
from arbitrage.funding_arbitrage import FundingArbitrageEngine
from arbitrage.live_state import LiveStateManager
from arbitrage.server import ArbitrageServer


class TestStep12ArbitrageTabCharts(unittest.TestCase):
    def setUp(self):
        self.calculator = SpreadCalculator()
        self.live_state = LiveStateManager()
        self.now = time.time()

        # Venue A = BINANCE, Venue B = BYBIT
        self.book_a = OrderBook(
            symbol="BTC/USDT",
            venue="BINANCE",
            bids=[OrderBookEntry(price=60000.0, size=2.0)],
            asks=[OrderBookEntry(price=60010.0, size=2.0)],
            timestamp=self.now,
        )
        self.book_b = OrderBook(
            symbol="BTC/USDT",
            venue="BYBIT",
            bids=[OrderBookEntry(price=60200.0, size=2.0)],
            asks=[OrderBookEntry(price=60210.0, size=2.0)],
            timestamp=self.now,
        )

        self.funding_a = FundingRate(
            symbol="BTC/USDT",
            venue="BINANCE",
            rate=0.0003,
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

    def test_reference_priced_pricing_relationship(self):
        """
        Verify Reference Venue vs Priced Venue raw spread pricing relationship:
        Spread % = ((Priced - Reference) / Reference) * 100.
        """
        # Binance is Reference, Bybit is Priced
        spread_res = self.calculator.calculate_raw_spread(
            reference_data=self.book_a,
            priced_data=self.book_b,
            symbol="BTC/USDT",
            reference_venue="BINANCE",
            priced_venue="BYBIT",
        )
        self.assertTrue(spread_res.validity)
        self.assertEqual(spread_res.reference_venue, "BINANCE")
        self.assertEqual(spread_res.priced_venue, "BYBIT")
        # Formula: (Bybit bid 60200 - Binance ask 60010) / 60010 * 100
        expected_spread = ((60200.0 - 60010.0) / 60010.0) * 100.0
        self.assertAlmostEqual(spread_res.raw_spread_pct, expected_spread, places=3)

    def test_execution_roles_decoupled_from_reference_and_priced(self):
        """
        Critical Invariant: Never derive execution roles from Reference/Priced.
        Execution role (Maker / First Leg -> Hedge / Taker) must be dynamically determined
        by opportunity selection.
        """
        # Evaluate Spread Arbitrage with Binance as Reference, Bybit as Priced
        # Favorable spread: Buy on Binance @ 60010, Sell on Bybit @ 60200
        # Candidate A: Binance is Maker, Bybit is Hedge
        # Candidate B: Bybit is Maker, Binance is Hedge
        decision_spread = self.calculator.evaluate_spread_arbitrage(
            venue_a_data=self.book_a,
            venue_b_data=self.book_b,
            symbol="BTC/USDT",
        )
        self.assertTrue(decision_spread.is_viable)
        first_leg = decision_spread.maker_venue
        second_leg = decision_spread.hedge_venue

        # Both venues must be validly assigned as distinct execution legs
        self.assertIn(first_leg, ("BINANCE", "BYBIT"))
        self.assertIn(second_leg, ("BINANCE", "BYBIT"))
        self.assertNotEqual(first_leg, second_leg)

        # In Funding Arbitrage: Binance has +0.03%, Bybit has +0.01%
        # Candidate receives Maker role on Binance (shorting high funding) and Hedge on Bybit (longing lower funding)
        decision_funding = self.calculator.evaluate_funding_arbitrage(
            funding_a=self.funding_a,
            funding_b=self.funding_b,
            book_a=self.book_a,
            book_b=self.book_b,
            symbol="BTC/USDT",
        )
        self.assertTrue(decision_funding.is_viable)
        self.assertEqual(decision_funding.selected_opportunity.short_venue, "BINANCE")
        self.assertEqual(decision_funding.selected_opportunity.long_venue, "BYBIT")

    def test_strategy_settings_dynamically_supports_both_modes(self):
        """
        Strategy Settings dynamically supports:
        1. Spread Arbitrage
        2. Funding Arbitrage
        Each preserves its independent selection, pricing, and exit rules.
        """
        # 1. Spread Arbitrage Mode
        spread_dec = self.calculator.evaluate_spread_arbitrage(
            venue_a_data=self.book_a,
            venue_b_data=self.book_b,
            symbol="BTC/USDT",
            pricing_mode="GRID",
            grid_step_pct=0.20,
            grid_levels_count=3,
        )
        self.assertTrue(spread_dec.is_viable)
        self.assertEqual(spread_dec.strategy_type, "SPREAD_ARBITRAGE")
        self.assertEqual(spread_dec.pricing.pricing_mode, "GRID")
        self.assertEqual(len(spread_dec.pricing.grid_levels), 3)
        self.assertEqual(spread_dec.exit_conditions.get("exit_type"), "SPREAD_CONVERGENCE")

        # 2. Funding Arbitrage Mode
        fund_dec = self.calculator.evaluate_funding_arbitrage(
            funding_a=self.funding_a,
            funding_b=self.funding_b,
            book_a=self.book_a,
            book_b=self.book_b,
            symbol="BTC/USDT",
            pricing_mode="BOLLINGER",
            bollinger_k=2.0,
            bollinger_period=20,
        )
        self.assertTrue(fund_dec.is_viable)
        self.assertEqual(fund_dec.strategy_type, "FUNDING_ARBITRAGE")
        self.assertEqual(fund_dec.pricing.pricing_mode, "BOLLINGER")
        self.assertAlmostEqual(fund_dec.net_funding_rate, 0.0002, places=6)
        self.assertEqual(fund_dec.exit_conditions.get("exit_type"), "FUNDING_SETTLEMENT")
        self.assertTrue(fund_dec.exit_conditions.get("rate_inversion_stop"))

    def test_live_state_strategy_mode_and_decisions_tracking(self):
        """
        Verify LiveStateManager tracks strategy_mode and decisions,
        and includes them in get_live_snapshot().
        """
        self.assertEqual(self.live_state.get_strategy_mode(), "SPREAD")

        # Switch to FUNDING
        changed = self.live_state.set_strategy_mode("FUNDING")
        self.assertTrue(changed)
        self.assertEqual(self.live_state.get_strategy_mode(), "FUNDING")

        # Populate decisions
        sample_spread_dec = {"BTC/USDT": {"is_viable": True, "maker_venue": "BINANCE"}}
        sample_fund_dec = {"BTC/USDT": {"is_viable": True, "net_funding_rate": 0.0002}}
        self.live_state.set_spread_decisions(sample_spread_dec)
        self.live_state.set_funding_decisions(sample_fund_dec)

        # Snapshot generation
        snap = self.live_state.get_live_snapshot()
        self.assertEqual(snap.get("strategy_mode"), "FUNDING")
        self.assertEqual(snap.get("spread_decisions"), sample_spread_dec)
        self.assertEqual(snap.get("funding_decisions"), sample_fund_dec)

    def test_server_rest_endpoints_for_step12(self):
        """
        Verify Server REST API endpoints for Step 12:
        - /api/arbitrage/strategy-mode
        - /api/arbitrage/spread-decision
        - /api/arbitrage/funding-decision
        """
        server = ArbitrageServer()
        # Seed order books and funding rates into server's live state
        server.live_state.set_order_book("BINANCE", "BTC/USDT", self.book_a)
        server.live_state.set_order_book("BYBIT", "BTC/USDT", self.book_b)
        server.live_state.set_funding_rate("BINANCE", "BTC/USDT", self.funding_a)
        server.live_state.set_funding_rate("BYBIT", "BTC/USDT", self.funding_b)

        # 1. Strategy Mode GET
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
            w = DummyWriter()
            await server._handle_api_request(path, query or {}, w, method=method, body=body)
            header_end = w.written.find(b"\r\n\r\n")
            body_json = w.written[header_end + 4:].decode("utf-8")
            return json.loads(body_json)

        # Test strategy-mode GET
        mode_data = asyncio.run(run_req("/api/arbitrage/strategy-mode"))
        self.assertEqual(mode_data.get("strategy_mode"), "SPREAD")

        # Test strategy-mode POST
        post_data = asyncio.run(run_req("/api/arbitrage/strategy-mode", method="POST", body=json.dumps({"strategy_mode": "FUNDING"}).encode()))
        self.assertEqual(post_data.get("strategy_mode"), "FUNDING")
        self.assertEqual(server.live_state.get_strategy_mode(), "FUNDING")

        # Test spread-decision GET
        spread_data = asyncio.run(run_req("/api/arbitrage/spread-decision", query={"venue_a": ["BINANCE"], "venue_b": ["BYBIT"], "symbol": ["BTC/USDT"]}))
        self.assertTrue(spread_data.get("is_viable"))
        self.assertEqual(spread_data.get("strategy_type"), "SPREAD_ARBITRAGE")

        # Test funding-decision GET
        fund_data = asyncio.run(run_req("/api/arbitrage/funding-decision", query={"venue_a": ["BINANCE"], "venue_b": ["BYBIT"], "symbol": ["BTC/USDT"]}))
        self.assertTrue(fund_data.get("is_viable"))
        self.assertEqual(fund_data.get("strategy_type"), "FUNDING_ARBITRAGE")
        self.assertAlmostEqual(fund_data.get("net_funding_rate"), 0.0002, places=6)

    def test_stale_data_invalidation_invariant(self):
        """
        Real Market Data Integrity:
        Stale market data (>10s) must invalidate decision instead of producing fake quotes.
        """
        old_time = self.now - 60.0
        stale_book = OrderBook(
            symbol="BTC/USDT",
            venue="BYBIT",
            bids=[OrderBookEntry(price=60200.0, size=2.0)],
            asks=[OrderBookEntry(price=60210.0, size=2.0)],
            timestamp=old_time,
        )
        dec = self.calculator.evaluate_spread_arbitrage(
            venue_a_data=self.book_a,
            venue_b_data=stale_book,
            symbol="BTC/USDT",
            current_time=self.now,
        )
        self.assertFalse(dec.is_viable)
        self.assertIsNotNone(dec.rejection_reason)
        self.assertIn("Stale", dec.rejection_reason)

    def test_zero_order_placement_invariant(self):
        """
        Verify no order placement method invocations exist in UI integration.
        """
        prohibited = ["create_order", "place_order", "send_order", "execute_order"]
        for p in prohibited:
            self.assertFalse(hasattr(self.calculator, p))


if __name__ == "__main__":
    unittest.main()
