"""
Tests for Step 11: Funding Settlement and Independent Exit.

Requirements from modular/step-11.md:
- Implement complete Funding Arbitrage lifecycle independently from Spread Arbitrage.
- Track 10 required fields:
    1. funding interval;
    2. funding timestamp;
    3. expected direction;
    4. funding eligibility;
    5. settlement status;
    6. actual funding result where available;
    7. position state;
    8. entry timestamp;
    9. exit state;
    10. reconciliation state.
- Keep Funding position managed until:
    funding settlement;
    configured funding target;
    funding invalidation;
    funding-specific risk;
    duration limit;
    reconciliation failure.
- Isolate Funding and Spread state machines.
- Do not alter Spread exit behavior.
- Prevent:
    unknown legs;
    mismatched legs;
    unhedged positions;
    incorrect settlement attribution.
- Real funding/settlement information used; show UNAVAILABLE if missing (no fabrication).
- Zero order placement invariant.
"""

import time
import unittest

from arbitrage.funding_arbitrage import FundingArbitrageEngine
from arbitrage.funding_settlement import FundingSettlementManager
from arbitrage.models import (
    FundingArbitrageDecision,
    FundingPositionLeg,
    FundingPositionLifecycle,
    FundingRate,
    OrderBook,
    OrderBookEntry,
)


class TestStep11FundingSettlement(unittest.TestCase):
    def setUp(self):
        self.funding_engine = FundingArbitrageEngine()
        self.manager = FundingSettlementManager(
            default_funding_interval_hours=8,
            default_max_duration_hours=24.0,
            default_target_settlements=1,
        )

    def _create_sample_decision(self, rate_a=0.0003, rate_b=0.0001):
        now = time.time()
        funding_a = FundingRate(
            symbol="BTC/USDT",
            venue="BINANCE",
            rate=rate_a,
            timestamp=now,
            next_funding_time=now + 14400.0,
        )
        funding_b = FundingRate(
            symbol="BTC/USDT",
            venue="BYBIT",
            rate=rate_b,
            timestamp=now,
            next_funding_time=now + 14400.0,
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
        return self.funding_engine.evaluate_funding_arbitrage(
            funding_a=funding_a,
            funding_b=funding_b,
            book_a=book_a,
            book_b=book_b,
        )

    def test_ten_required_lifecycle_fields_tracked(self):
        """Verify all 10 required fields are tracked on position creation."""
        now = time.time()
        decision = self._create_sample_decision()
        pos = self.manager.create_position(
            decision=decision,
            quantity=1.0,
            entry_price_maker=60010.0,
            entry_price_hedge=60005.0,
            entry_time=now,
        )

        # 1. funding interval
        self.assertEqual(pos.funding_interval, 8)
        # 2. funding timestamp
        self.assertGreater(pos.funding_timestamp, now)
        # 3. expected direction
        self.assertEqual(pos.expected_direction, "SHORT_BINANCE_LONG_BYBIT")
        # 4. funding eligibility
        self.assertTrue(pos.funding_eligibility)
        # 5. settlement status
        self.assertEqual(pos.settlement_status, "PENDING_SETTLEMENT")
        # 6. actual funding result (None initially until settlement recorded)
        self.assertIsNone(pos.actual_funding_result)
        # 7. position state
        self.assertEqual(pos.position_state, "HEDGED")
        # 8. entry timestamp
        self.assertEqual(pos.entry_timestamp, now)
        # 9. exit state
        self.assertEqual(pos.exit_state, "ACTIVE")
        # 10. reconciliation state
        self.assertEqual(pos.reconciliation_state, "BALANCED")

    def test_independent_exit_funding_settlement_reached(self):
        """Independent exit triggers when funding settlement is achieved."""
        now = time.time()
        decision = self._create_sample_decision()
        pos = self.manager.create_position(
            decision=decision,
            quantity=1.0,
            entry_price_maker=60010.0,
            entry_price_hedge=60005.0,
            entry_time=now,
        )

        # Before settlement: should not exit
        should_exit, reason = self.manager.check_exit_conditions(pos, current_time=now + 1000.0)
        self.assertFalse(should_exit)

        # Record real settlement event from exchange ($18.00 received)
        settle_time = pos.funding_timestamp + 1.0
        self.manager.record_settlement(pos, actual_settlement_cashflow=18.00, settlement_timestamp=settle_time)
        self.assertEqual(pos.settlement_status, "SETTLED")
        self.assertEqual(pos.actual_funding_result, 18.00)
        self.assertEqual(pos.settlement_count, 1)

        # After settlement: should trigger exit
        should_exit, reason = self.manager.check_exit_conditions(pos, current_time=settle_time)
        self.assertTrue(should_exit)
        self.assertIn("SETTLEMENT_REACHED", reason)

        # Close position
        pos_closed = self.manager.close_position(pos, exit_reason=reason, exit_time=settle_time)
        self.assertEqual(pos_closed.position_state, "CLOSED")
        self.assertEqual(pos_closed.exit_state, "EXIT_SETTLED")

    def test_funding_invalidation_rate_inversion_exit(self):
        """Funding invalidation: exits if net funding rate inverts (becomes <= 0)."""
        now = time.time()
        decision = self._create_sample_decision()
        pos = self.manager.create_position(
            decision=decision,
            quantity=1.0,
            entry_price_maker=60010.0,
            entry_price_hedge=60005.0,
            entry_time=now,
        )

        # Rate inverts: Binance drops to 0.0001, Bybit jumps to 0.0003 (net funding is now -0.0002!)
        inverted_funding_a = FundingRate(symbol="BTC/USDT", venue="BINANCE", rate=0.0001, timestamp=now + 1800.0)
        inverted_funding_b = FundingRate(symbol="BTC/USDT", venue="BYBIT", rate=0.0003, timestamp=now + 1800.0)

        should_exit, reason = self.manager.check_exit_conditions(
            pos,
            current_funding_a=inverted_funding_a,
            current_funding_b=inverted_funding_b,
            current_time=now + 1800.0,
        )
        self.assertTrue(should_exit)
        self.assertIn("RATE_INVALIDATED", reason)

        pos_closed = self.manager.close_position(pos, exit_reason=reason)
        self.assertEqual(pos_closed.exit_state, "EXIT_INVALIDATED")

    def test_duration_limit_exit(self):
        """Exits when position age exceeds max_duration_hours."""
        now = time.time()
        decision = self._create_sample_decision()
        pos = self.manager.create_position(
            decision=decision,
            quantity=1.0,
            entry_price_maker=60010.0,
            entry_price_hedge=60005.0,
            entry_time=now,
        )

        # 25 hours later (max duration is 24h)
        future_time = now + (25.0 * 3600.0)
        should_exit, reason = self.manager.check_exit_conditions(
            pos,
            current_time=future_time,
            max_duration_hours=24.0,
        )
        self.assertTrue(should_exit)
        self.assertIn("DURATION_LIMIT", reason)

        pos_closed = self.manager.close_position(pos, exit_reason=reason)
        self.assertEqual(pos_closed.exit_state, "EXIT_DURATION_LIMIT")

    def test_safety_reconciliation_detects_mismatched_and_unhedged_legs(self):
        """
        Step 11 Safety Invariant:
        Prevent mismatched legs, unknown legs, and unhedged positions.
        """
        now = time.time()
        decision = self._create_sample_decision()
        pos = self.manager.create_position(
            decision=decision,
            quantity=1.0,
            entry_price_maker=60010.0,
            entry_price_hedge=60005.0,
            entry_time=now,
        )

        # Healthy position: passes reconciliation
        ok, msg = self.manager.verify_hedge_reconciliation(pos)
        self.assertTrue(ok)

        # Introduce quantity mismatch
        pos.hedge_leg.quantity = 0.5 # 1.0 maker vs 0.5 hedge!
        ok_mismatch, err_mismatch = self.manager.verify_hedge_reconciliation(pos)
        self.assertFalse(ok_mismatch)
        self.assertIn("Mismatched legs", err_mismatch)

        # Restore quantity but introduce same-side unhedged flaw
        pos.hedge_leg.quantity = 1.0
        pos.hedge_leg.side = pos.maker_leg.side # Both SELL!
        ok_unhedged, err_unhedged = self.manager.verify_hedge_reconciliation(pos)
        self.assertFalse(ok_unhedged)
        self.assertIn("Unhedged position", err_unhedged)

    def test_real_settlement_data_vs_unavailable_state(self):
        """
        When exchange settlement statement is unavailable, report UNAVAILABLE state
        rather than fabricating fake/mock settlement cashflows.
        """
        now = time.time()
        decision = self._create_sample_decision()
        pos = self.manager.create_position(
            decision=decision,
            quantity=1.0,
            entry_price_maker=60010.0,
            entry_price_hedge=60005.0,
            entry_time=now,
        )

        # Venue provides no settlement statement (None)
        self.manager.record_settlement(pos, actual_settlement_cashflow=None)
        self.assertEqual(pos.settlement_status, "UNAVAILABLE")
        self.assertIsNone(pos.actual_funding_result)
        self.assertEqual(pos.accumulated_funding_usd, 0.0)

    def test_zero_order_placement_invariant(self):
        """Verify FundingSettlementManager does not submit or execute orders."""
        prohibited = ["place_order", "create_order", "send_order", "submit_order", "execute_order", "close_trade"]
        for p in prohibited:
            self.assertFalse(hasattr(self.manager, p))


if __name__ == "__main__":
    unittest.main()
