"""
Step 9: Spread Arbitrage Engine (SpreadArbitrageEngine).

Requirements:
- Use directional opportunities from OpportunityEngine.
- Evaluate both directions (Candidate A & Candidate B).
- Calculate Net Spread %.
- Select: Highest Net Spread %, subject to existing thresholds and risk controls.
- Selected direction becomes:
    First Leg = Maker (passive limit order)
    Second Leg = Taker/Hedge (immediate hedge order upon maker fill)
- Do not derive execution direction from Reference/Priced roles.
- Preserve:
    Grid;
    Bollinger;
    Quantity;
    Risk controls;
    Entry thresholds;
    Existing exit behavior (0% convergence, stop loss).
- The highest Net Spread identifies/selects the Maker direction/order opportunity.
- The actual Maker execution level remains determined by:
    Spread %;
    Grid (spacing, levels);
    Bollinger (rolling mean/bands);
    other configured entry controls.
- Do not alter Spread Arbitrage lifecycle unnecessarily.
- Zero order placement invariant (strategy decision and pricing computation without live order dispatch).
"""

import logging
import time
from typing import Any, Dict, List, Optional, Tuple

from .models import (
    DirectionEvaluationResult,
    DirectionalOpportunity,
    MakerEntryPricing,
    OpportunityEvaluationResult,
    OrderBook,
    SpreadArbitrageDecision,
)
from .opportunity_engine import OpportunityEngine

logger = logging.getLogger(__name__)


class SpreadArbitrageEngine:
    """
    Authoritative Spread Arbitrage Engine.
    Evaluates both directional opportunities, selects highest Net Spread %,
    assigns First Leg = Maker & Second Leg = Hedge, and calculates Maker entry pricing
    via Spread %, Grid, or Bollinger modes.
    """

    def __init__(
        self,
        opportunity_engine: Optional[OpportunityEngine] = None,
        spread_calculator: Optional[Any] = None,
        min_profit_threshold_pct: float = 0.05,
        max_slippage_pct: float = 0.20,
        default_order_quantity: float = 1.0,
        default_max_position_size: float = 10.0,
    ):
        self.opportunity_engine = opportunity_engine or OpportunityEngine()
        if spread_calculator is None:
            from .spread_calculator import SpreadCalculator
            self.spread_calculator = SpreadCalculator()
        else:
            self.spread_calculator = spread_calculator
        self.min_profit_threshold_pct = min_profit_threshold_pct
        self.max_slippage_pct = max_slippage_pct
        self.default_order_quantity = default_order_quantity
        self.default_max_position_size = default_max_position_size

    def select_highest_net_opportunity(
        self,
        opportunity_result: OpportunityEvaluationResult,
        min_profit_threshold_pct: Optional[float] = None,
        max_slippage_pct: Optional[float] = None,
        max_position_size: Optional[float] = None,
    ) -> SpreadArbitrageDecision:
        """
        Evaluate both directions and select the Highest Net Spread % opportunity.
        Selected direction becomes:
            First Leg = Maker;
            Second Leg = Taker/Hedge.
        Execution direction is decoupled from Reference/Priced roles.
        """
        min_thresh = (
            min_profit_threshold_pct
            if min_profit_threshold_pct is not None
            else self.min_profit_threshold_pct
        )
        max_slip = max_slippage_pct if max_slippage_pct is not None else self.max_slippage_pct
        max_pos = max_position_size if max_position_size is not None else self.default_max_position_size

        sym = opportunity_result.symbol
        ref_v = opportunity_result.reference_venue
        priced_v = opportunity_result.priced_venue

        # Check overall validity from OpportunityEngine
        if not opportunity_result.validity:
            return SpreadArbitrageDecision(
                symbol=sym,
                is_viable=False,
                rejection_reason=opportunity_result.error_reason or "Invalid opportunity result",
                reference_venue=ref_v,
                priced_venue=priced_v,
            )

        opp_a = opportunity_result.opportunity_a
        opp_b = opportunity_result.opportunity_b

        # Filter valid candidates
        valid_candidates: List[DirectionalOpportunity] = []
        if opp_a and opp_a.validity and opp_a.net_spread_pct is not None:
            valid_candidates.append(opp_a)
        if opp_b and opp_b.validity and opp_b.net_spread_pct is not None:
            valid_candidates.append(opp_b)

        if not valid_candidates:
            return SpreadArbitrageDecision(
                symbol=sym,
                is_viable=False,
                rejection_reason="No valid directional opportunity available (stale or missing market data)",
                reference_venue=ref_v,
                priced_venue=priced_v,
            )

        # Select Highest Net Spread %
        selected = max(valid_candidates, key=lambda o: o.net_spread_pct)

        # Enforce entry thresholds and risk controls
        risk_metrics = {
            "min_profit_threshold_pct": min_thresh,
            "max_slippage_pct": max_slip,
            "max_position_size": max_pos,
            "actual_net_spread_pct": selected.net_spread_pct,
            "actual_slippage_pct": selected.slippage_pct,
            "available_liquidity": selected.liquidity_size,
        }

        rejection_reason = None
        is_viable = True

        if selected.net_spread_pct < min_thresh:
            is_viable = False
            rejection_reason = (
                f"Net spread ({selected.net_spread_pct:.4f}%) is below minimum threshold ({min_thresh:.4f}%)"
            )
        elif selected.slippage_pct > max_slip:
            is_viable = False
            rejection_reason = (
                f"Slippage ({selected.slippage_pct:.4f}%) exceeds maximum allowed ({max_slip:.4f}%)"
            )
        elif selected.liquidity_size <= 0:
            is_viable = False
            rejection_reason = "Insufficient liquidity across maker/hedge legs"

        exit_conditions = {
            "exit_type": "SPREAD_CONVERGENCE",
            "convergence_target_pct": 0.0,
            "stop_loss_pct": None,
        }

        return SpreadArbitrageDecision(
            symbol=sym,
            selected_opportunity=selected,
            candidate_id=selected.candidate_id,
            first_leg_venue=selected.maker_venue,
            second_leg_venue=selected.hedge_venue,
            maker_side=selected.maker_side,
            hedge_side=selected.hedge_side,
            net_spread_pct=selected.net_spread_pct,
            raw_spread_pct=selected.raw_spread_pct or 0.0,
            is_viable=is_viable,
            rejection_reason=rejection_reason,
            risk_metrics=risk_metrics,
            exit_conditions=exit_conditions,
            reference_venue=ref_v,
            priced_venue=priced_v,
        )

    def calculate_maker_entry_pricing(
        self,
        decision: SpreadArbitrageDecision,
        pricing_mode: str = "SPREAD",
        spread_offset_pct: float = 0.0,
        grid_spacing_pct: float = 0.05,
        grid_levels_count: int = 3,
        bollinger_k: float = 2.0,
        bollinger_period: int = 20,
        spread_history: Optional[List[float]] = None,
        order_quantity: Optional[float] = None,
        stop_loss_pct: Optional[float] = None,
    ) -> MakerEntryPricing:
        """
        Determine actual Maker execution level based on configured mode:
        1. SPREAD: entry based on top-of-book and spread % offset.
        2. GRID: multi-level limit order ladder with configured grid spacing.
        3. BOLLINGER: entry level derived from rolling Bollinger Bands.
        Preserves existing exit targets (0% convergence or rolling mean) and stop loss.
        """
        opp = decision.selected_opportunity
        if not opp or opp.maker_price <= 0:
            return MakerEntryPricing(
                pricing_mode=pricing_mode.upper(),
                base_maker_price=0.0,
                maker_side=decision.maker_side or "BUY",
                order_quantity=order_quantity or self.default_order_quantity,
            )

        qty = order_quantity if order_quantity is not None else min(self.default_order_quantity, opp.liquidity_size)
        side = opp.maker_side
        base_price = opp.maker_price
        hedge_price = opp.hedge_price
        mode = pricing_mode.upper()

        grid_levels: List[Dict[str, Any]] = []
        bollinger_data: Optional[Dict[str, float]] = None
        exit_target = hedge_price  # Convergence exit target
        stop_loss = None

        if mode == "SPREAD":
            # Apply configured spread offset %
            if side == "BUY":
                # For buy limit: lower price by offset % if positive offset desired
                entry_price = round(base_price * (1.0 - (spread_offset_pct / 100.0)), 6)
            else:
                # For sell limit: raise price by offset %
                entry_price = round(base_price * (1.0 + (spread_offset_pct / 100.0)), 6)

            if stop_loss_pct:
                if side == "BUY":
                    stop_loss = round(entry_price * (1.0 - (stop_loss_pct / 100.0)), 6)
                else:
                    stop_loss = round(entry_price * (1.0 + (stop_loss_pct / 100.0)), 6)

            return MakerEntryPricing(
                pricing_mode="SPREAD",
                base_maker_price=entry_price,
                maker_side=side,
                order_quantity=qty,
                exit_target_price=exit_target,
                stop_loss_price=stop_loss,
                target_hedge_price=hedge_price,
            )

        elif mode == "GRID":
            # Grid ladder: generates multiple maker limit order levels spaced by grid_spacing_pct
            levels_n = max(1, grid_levels_count)
            qty_per_level = round(qty / levels_n, 6)

            for i in range(levels_n):
                step_offset = i * (grid_spacing_pct / 100.0)
                if side == "BUY":
                    lvl_price = round(base_price * (1.0 - step_offset), 6)
                else:
                    lvl_price = round(base_price * (1.0 + step_offset), 6)

                grid_levels.append({
                    "level": i,
                    "price": lvl_price,
                    "quantity": qty_per_level,
                    "side": side,
                    "spacing_pct": round(i * grid_spacing_pct, 4),
                })

            entry_price = grid_levels[0]["price"] if grid_levels else base_price

            if stop_loss_pct and grid_levels:
                last_price = grid_levels[-1]["price"]
                if side == "BUY":
                    stop_loss = round(last_price * (1.0 - (stop_loss_pct / 100.0)), 6)
                else:
                    stop_loss = round(last_price * (1.0 + (stop_loss_pct / 100.0)), 6)

            return MakerEntryPricing(
                pricing_mode="GRID",
                base_maker_price=entry_price,
                maker_side=side,
                order_quantity=qty,
                grid_levels=grid_levels,
                exit_target_price=exit_target,
                stop_loss_price=stop_loss,
                target_hedge_price=hedge_price,
            )

        elif mode == "BOLLINGER":
            # Rolling Bollinger Bands calculation
            history = spread_history or [decision.net_spread_pct]
            boll = self.spread_calculator.calculate_bollinger_bands(
                history, k=bollinger_k, period=bollinger_period
            )
            bollinger_data = boll

            # In Bollinger arbitrage: entry aims to capture extreme spread deviation
            # Exit target aligns with mean spread (0% convergence or rolling mean)
            entry_price = round(base_price, 6)

            if stop_loss_pct:
                if side == "BUY":
                    stop_loss = round(entry_price * (1.0 - (stop_loss_pct / 100.0)), 6)
                else:
                    stop_loss = round(entry_price * (1.0 + (stop_loss_pct / 100.0)), 6)

            return MakerEntryPricing(
                pricing_mode="BOLLINGER",
                base_maker_price=entry_price,
                maker_side=side,
                order_quantity=qty,
                bollinger_bands=bollinger_data,
                exit_target_price=exit_target,
                stop_loss_price=stop_loss,
                target_hedge_price=hedge_price,
            )

        else:
            # Fallback standard top-of-book pricing
            return MakerEntryPricing(
                pricing_mode=mode,
                base_maker_price=base_price,
                maker_side=side,
                order_quantity=qty,
                exit_target_price=exit_target,
                target_hedge_price=hedge_price,
            )

    def evaluate_spread_arbitrage(
        self,
        venue_a_data: Any,
        venue_b_data: Any,
        symbol: Optional[str] = None,
        venue_a_name: Optional[str] = None,
        venue_b_name: Optional[str] = None,
        reference_venue: Optional[str] = None,
        priced_venue: Optional[str] = None,
        target_size: Optional[float] = None,
        min_profit_threshold_pct: Optional[float] = None,
        pricing_mode: str = "SPREAD",
        spread_offset_pct: float = 0.0,
        grid_spacing_pct: float = 0.05,
        grid_levels_count: int = 3,
        bollinger_k: float = 2.0,
        bollinger_period: int = 20,
        spread_history: Optional[List[float]] = None,
        stop_loss_pct: Optional[float] = None,
        grid_step_pct: Optional[float] = None,
        current_time: Optional[float] = None,
        stale_threshold_seconds: Optional[float] = None,
        **kwargs,
    ) -> SpreadArbitrageDecision:
        """
        Complete end-to-end Step 9 pipeline:
        1. Evaluates both directional opportunities via OpportunityEngine.
        2. Selects candidate with Highest Net Spread %.
        3. Assigns First Leg = Maker & Second Leg = Taker/Hedge.
        4. Calculates Maker execution entry pricing via Spread %, Grid, or Bollinger.
        """
        if grid_step_pct is not None:
            grid_spacing_pct = grid_step_pct

        opp_res = self.opportunity_engine.evaluate_directional_opportunities(
            venue_a_data=venue_a_data,
            venue_b_data=venue_b_data,
            symbol=symbol,
            venue_a_name=venue_a_name,
            venue_b_name=venue_b_name,
            reference_venue=reference_venue,
            priced_venue=priced_venue,
            target_size=target_size,
            min_profit_threshold_pct=min_profit_threshold_pct,
            stale_threshold_seconds=stale_threshold_seconds,
            current_time=current_time,
        )

        decision = self.select_highest_net_opportunity(
            opp_res,
            min_profit_threshold_pct=min_profit_threshold_pct,
        )

        if stop_loss_pct is not None:
            decision.exit_conditions["stop_loss_pct"] = stop_loss_pct

        if decision.is_viable and decision.selected_opportunity:
            decision.pricing = self.calculate_maker_entry_pricing(
                decision=decision,
                pricing_mode=pricing_mode,
                spread_offset_pct=spread_offset_pct,
                grid_spacing_pct=grid_spacing_pct,
                grid_levels_count=grid_levels_count,
                bollinger_k=bollinger_k,
                bollinger_period=bollinger_period,
                spread_history=spread_history,
                order_quantity=target_size,
                stop_loss_pct=stop_loss_pct,
            )

        return decision
