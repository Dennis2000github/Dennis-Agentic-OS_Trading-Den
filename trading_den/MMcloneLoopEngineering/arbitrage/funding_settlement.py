"""
Step 11: Funding Settlement and Independent Exit (FundingSettlementManager).

Requirements from modular/step-11.md:
- Implement complete Funding Arbitrage lifecycle independently from Spread Arbitrage.
- Track all 10 required fields:
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
    execution failure;
    partial-fill failure;
    reconciliation failure.
- Isolate Funding and Spread state machines.
- Do not alter Spread exit behavior (0% convergence, stop loss).
- Prevent:
    unknown legs;
    mismatched legs;
    unhedged positions;
    incorrect settlement attribution.
- Real funding/settlement information must be used whenever the venue provides it.
- If unavailable, show the actual unavailable state rather than fabricating settlement data.
- Zero order placement invariant during modular development.
"""

import logging
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

from .models import (
    FundingArbitrageDecision,
    FundingPositionLeg,
    FundingPositionLifecycle,
    FundingRate,
)

logger = logging.getLogger(__name__)


class FundingSettlementManager:
    """
    Dedicated Funding Settlement and Independent Exit Lifecycle Manager.
    Manages hedged funding positions until settlement, funding target, or invalidation,
    strictly isolated from Spread Arbitrage state machines.
    """

    def __init__(
        self,
        default_funding_interval_hours: int = 8,
        default_max_duration_hours: float = 24.0,
        default_target_settlements: int = 1,
    ):
        self.default_funding_interval_hours = default_funding_interval_hours
        self.default_max_duration_hours = default_max_duration_hours
        self.default_target_settlements = default_target_settlements
        self._positions: Dict[str, FundingPositionLifecycle] = {}

    def create_position(
        self,
        decision: FundingArbitrageDecision,
        quantity: float,
        entry_price_maker: float,
        entry_price_hedge: float,
        entry_time: Optional[float] = None,
        position_id: Optional[str] = None,
    ) -> FundingPositionLifecycle:
        """
        Create and initialize a new hedged Funding Arbitrage position.
        Tracks all 10 required fields and sets state to HEDGED.
        """
        now = entry_time if entry_time is not None else time.time()
        pid = position_id or f"fund_pos_{uuid.uuid4().hex[:8]}"

        opp = decision.selected_opportunity
        settle_time = opp.next_settlement_time if (opp and opp.next_settlement_time > now) else (now + 8 * 3600.0)
        expected_dir = f"SHORT_{decision.selected_opportunity.short_venue}_LONG_{decision.selected_opportunity.long_venue}" if opp else "UNKNOWN"

        maker_leg = FundingPositionLeg(
            venue=decision.first_leg_venue,
            symbol=decision.symbol,
            side=decision.maker_side,
            role="MAKER",
            quantity=quantity,
            entry_price=entry_price_maker,
            filled=True,
            timestamp=now,
        )

        hedge_leg = FundingPositionLeg(
            venue=decision.second_leg_venue,
            symbol=decision.symbol,
            side=decision.hedge_side,
            role="HEDGE",
            quantity=quantity,
            entry_price=entry_price_hedge,
            filled=True,
            timestamp=now,
        )

        pos = FundingPositionLifecycle(
            position_id=pid,
            symbol=decision.symbol,
            maker_leg=maker_leg,
            hedge_leg=hedge_leg,
            funding_interval=self.default_funding_interval_hours,
            funding_timestamp=settle_time,
            expected_direction=expected_dir,
            funding_eligibility=True, # Position opened and hedged
            settlement_status="PENDING_SETTLEMENT",
            actual_funding_result=None, # Initially unavailable until venue provides it
            position_state="HEDGED",
            entry_timestamp=now,
            exit_state="ACTIVE",
            reconciliation_state="BALANCED",
        )

        # Immediate reconciliation safety check on creation
        ok, msg = self.verify_hedge_reconciliation(pos)
        if not ok:
            pos.reconciliation_state = "MISMATCHED"
            pos.unhedged_risk = True

        self._positions[pid] = pos
        return pos

    def verify_hedge_reconciliation(
        self,
        position: FundingPositionLifecycle,
    ) -> Tuple[bool, str]:
        """
        Enforce safety invariants from Step 11:
        Prevent:
        - unknown legs
        - mismatched legs (quantity mismatch)
        - unhedged positions (same side or incomplete leg)
        - incorrect settlement attribution
        """
        m_leg = position.maker_leg
        h_leg = position.hedge_leg

        # 1. Prevent unknown legs
        if not m_leg or not h_leg or not m_leg.venue or not h_leg.venue:
            return False, "Unknown legs: One or both legs lack venue identification"
        if not m_leg.symbol or not h_leg.symbol:
            return False, "Unknown legs: One or both legs lack symbol identification"

        # 2. Prevent mismatched legs
        qty_diff = abs(m_leg.quantity - h_leg.quantity)
        if qty_diff > 1e-6:
            return False, f"Mismatched legs: Maker quantity ({m_leg.quantity}) != Hedge quantity ({h_leg.quantity})"

        # 3. Prevent unhedged positions (must be opposing sides: one BUY, one SELL)
        if m_leg.side == h_leg.side:
            return False, f"Unhedged position: Both legs have same side ({m_leg.side})"
        if not m_leg.filled or not h_leg.filled:
            return False, "Unhedged position: One or both legs are not filled"

        # 4. Prevent incorrect settlement attribution
        # Check expected direction matches short and long venues
        exp_dir = position.expected_direction
        if "SHORT_" in exp_dir and "LONG_" in exp_dir:
            # Short venue must have SELL side, Long venue must have BUY side
            if m_leg.side == "SELL":
                short_v = m_leg.venue
                long_v = h_leg.venue
            else:
                short_v = h_leg.venue
                long_v = m_leg.venue
            expected_str = f"SHORT_{short_v}_LONG_{long_v}"
            if expected_str != exp_dir:
                return False, f"Incorrect settlement attribution: Expected {exp_dir}, got {expected_str}"

        return True, "Reconciliation verified: Perfectly hedged and attributed."

    def check_exit_conditions(
        self,
        position: FundingPositionLifecycle,
        current_funding_a: Optional[FundingRate] = None,
        current_funding_b: Optional[FundingRate] = None,
        current_time: Optional[float] = None,
        max_duration_hours: Optional[float] = None,
        target_settlements: Optional[int] = None,
        funding_target_usd: Optional[float] = None,
    ) -> Tuple[bool, Optional[str]]:
        """
        Evaluate independent Funding Arbitrage exit conditions.
        Does NOT use Spread % or 0% convergence as funding exit.
        Triggers on:
        - funding settlement reached
        - configured funding target reached
        - funding invalidation (rate inversion)
        - duration limit reached
        - reconciliation failure
        """
        now = current_time if current_time is not None else time.time()
        max_dur = (max_duration_hours or self.default_max_duration_hours) * 3600.0
        tgt_settles = target_settlements or self.default_target_settlements

        # 1. Reconciliation failure safety check
        rec_ok, rec_msg = self.verify_hedge_reconciliation(position)
        if not rec_ok:
            return True, f"RECONCILIATION_FAILURE: {rec_msg}"

        # 2. Funding Invalidation Check: Rate Inversion
        if current_funding_a and current_funding_b:
            rate_a = current_funding_a.rate
            rate_b = current_funding_b.rate
            # Check if expected short venue still has higher rate than long venue
            if "SHORT_" in position.expected_direction:
                if position.maker_leg.side == "SELL":
                    r_short = rate_a if position.maker_leg.venue == current_funding_a.venue.upper() else rate_b
                    r_long = rate_b if position.hedge_leg.venue == current_funding_b.venue.upper() else rate_a
                else:
                    r_short = rate_b if position.hedge_leg.venue == current_funding_b.venue.upper() else rate_a
                    r_long = rate_a if position.maker_leg.venue == current_funding_a.venue.upper() else rate_b

                net_current = r_short - r_long
                if net_current <= 0.0:
                    return True, "RATE_INVALIDATED: Funding rate inverted (net funding yield <= 0)"

        # 3. Target Settlements Reached Check
        if position.settlement_count >= tgt_settles:
            return True, f"SETTLEMENT_REACHED: Target settlements ({tgt_settles}) achieved"

        # 4. Settlement Timestamp Passed
        if now >= position.funding_timestamp and position.settlement_status == "SETTLED":
            return True, "SETTLEMENT_REACHED: Next funding settlement passed and attributed"

        # 5. Configured Funding Target Reached Check
        if funding_target_usd and position.accumulated_funding_usd >= funding_target_usd:
            return True, f"TARGET_REACHED: Target profit ${funding_target_usd:.2f} reached"

        # 6. Duration Limit Check
        age_seconds = now - position.entry_timestamp
        if age_seconds >= max_dur:
            return True, f"DURATION_LIMIT: Max holding time ({max_dur/3600.0:.1f}h) exceeded"

        return False, None

    def record_settlement(
        self,
        position: FundingPositionLifecycle,
        actual_settlement_cashflow: Optional[float] = None,
        settlement_timestamp: Optional[float] = None,
    ) -> FundingPositionLifecycle:
        """
        Record actual settlement event from real exchange data.
        If unavailable from venue, record actual UNAVAILABLE state rather than fabricating data.
        """
        now = settlement_timestamp if settlement_timestamp is not None else time.time()
        position.settlement_count += 1

        if actual_settlement_cashflow is not None:
            position.settlement_status = "SETTLED"
            position.actual_funding_result = actual_settlement_cashflow
            position.accumulated_funding_usd += actual_settlement_cashflow
            logger.info("Recorded real funding settlement for position %s: $%s", position.position_id, actual_settlement_cashflow)
        else:
            # Real venue did not provide settlement statement -> report UNAVAILABLE
            position.settlement_status = "UNAVAILABLE"
            position.actual_funding_result = None
            logger.warning("Venue settlement data unavailable for position %s", position.position_id)

        # Advance to next 8h interval
        position.funding_timestamp = now + (position.funding_interval * 3600.0)
        return position

    def close_position(
        self,
        position: FundingPositionLifecycle,
        exit_reason: str,
        exit_time: Optional[float] = None,
    ) -> FundingPositionLifecycle:
        """
        Transition Funding Arbitrage position to CLOSED with specified exit state.
        Preserves Spread Arbitrage exit state machine without overlap.
        """
        now = exit_time if exit_time is not None else time.time()
        position.position_state = "CLOSED"
        position.exit_timestamp = now
        position.exit_reason = exit_reason

        if "SETTLEMENT" in exit_reason:
            position.exit_state = "EXIT_SETTLED"
        elif "TARGET" in exit_reason:
            position.exit_state = "EXIT_TARGET_REACHED"
        elif "INVALIDATED" in exit_reason:
            position.exit_state = "EXIT_INVALIDATED"
        elif "DURATION" in exit_reason:
            position.exit_state = "EXIT_DURATION_LIMIT"
        elif "RECONCILIATION" in exit_reason or "FAILURE" in exit_reason:
            position.exit_state = "EXIT_FAILED"
        else:
            position.exit_state = "EXIT_COMPLETED"

        logger.info("Funding position %s closed: %s (%s)", position.position_id, position.exit_state, exit_reason)
        return position
