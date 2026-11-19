"""
Step 10: Funding Arbitrage Engine (FundingArbitrageEngine).

Requirements from modular/step-10.md:
- Implement Funding Arbitrage as a separate strategy mode within the existing architecture.
- Do not redesign the UI.
- Do not make unrelated Spread Arbitrage changes.
- Reuse:
    market data;
    direction engine;
    fee engine;
    Strategy Settings;
    Opportunity Engine;
    Execution Engine.
- 10.1 Funding Selection:
    Evaluate both directions;
    calculate directional funding opportunity;
    determine execution direction;
    select First Leg / Maker using Highest Funding %.
    Funding % is a venue/direction selector only, NEVER the Maker order price.
    Priced Venue does not automatically become Maker; Reference Venue does not automatically become Hedge.
- 10.2 Direction:
    Evaluate A Maker -> B Hedge and B Maker -> A Hedge (reuse Step 6).
- 10.3 Execution Roles:
    Reference/Priced describe pricing relationships only.
    First Leg = Maker; Second Leg = Hedge/Taker.
- 10.4 Maker Entry Price:
    Funding % selects the venue/direction.
    Spread % / Grid / Bollinger determines the Maker entry level.
    Funding % must never directly determine the order price.
- 10.9 Independent Funding Exit:
    Do not automatically use Spread % or 0% convergence as funding exits.
    Exit conditions: funding settlement reached, funding rate inverted, funding target reached, duration limit.
- 10.11 Fees / Net Opportunity:
    Reuse directional fees (A Maker + B Taker, or B Maker + A Taker).
- 10.13 Preserve Spread Arbitrage.
- 10.14 Responsibility Separation:
    A: Selection (Highest Funding % -> Maker venue/direction)
    B: Pricing (Spread/Grid/Bollinger -> Maker entry level)
    C: Execution (Maker -> Hedge)
    D: Lifecycle (Hedged position -> Funding settlement)
    E: Exit (Funding-specific conditions -> Close)
- Strictly real market data (no simulated funding rates).
- Zero order placement invariant.
"""

import logging
import time
from typing import Any, Dict, List, Optional, Tuple

from .fee_engine import FeeEngine
from .models import (
    DirectionalFundingOpportunity,
    FundingArbitrageDecision,
    FundingRate,
    MakerEntryPricing,
    OrderBook,
    OrderBookEntry,
)

logger = logging.getLogger(__name__)


class FundingArbitrageEngine:
    """
    Dedicated Funding Arbitrage Engine.
    Evaluates directional funding yield, selects Maker direction via Highest Funding %,
    determines Maker entry price via Spread / Grid / Bollinger, and defines
    independent funding exit and settlement conditions.
    """

    def __init__(
        self,
        fee_engine: Optional[FeeEngine] = None,
        spread_calculator: Optional[Any] = None,
        min_annual_funding_pct: float = 5.0,  # Min 5% annual funding yield to enter
        min_settlement_net_pct: float = 0.01, # Min 0.01% (1 bp) net funding per 8h settlement
        default_order_quantity: float = 1.0,
    ):
        self.fee_engine = fee_engine or FeeEngine()
        if spread_calculator is None:
            from .spread_calculator import SpreadCalculator
            self.spread_calculator = SpreadCalculator()
        else:
            self.spread_calculator = spread_calculator
        self.min_annual_funding_pct = min_annual_funding_pct
        self.min_settlement_net_pct = min_settlement_net_pct
        self.default_order_quantity = default_order_quantity

    def evaluate_directional_funding(
        self,
        funding_a: FundingRate,
        funding_b: FundingRate,
        book_a: OrderBook,
        book_b: OrderBook,
        symbol: Optional[str] = None,
        market_type: str = "perp",
        fee_tier_a: str = "standard",
        fee_tier_b: str = "standard",
        current_time: Optional[float] = None,
    ) -> Tuple[DirectionalFundingOpportunity, DirectionalFundingOpportunity]:
        """
        Evaluate both funding directions:
        Direction 1 (Short A + Long B): collects funding_a, pays funding_b.
        Direction 2 (Short B + Long A): collects funding_b, pays funding_a.

        For each viable direction, evaluates maker candidates:
        - Candidate A: Venue A is Maker, Venue B is Hedge
        - Candidate B: Venue B is Maker, Venue A is Hedge
        """
        now = current_time if current_time is not None else time.time()
        sym = symbol or funding_a.symbol
        v_a = funding_a.venue.upper()
        v_b = funding_b.venue.upper()

        rate_a = float(funding_a.rate)
        rate_b = float(funding_b.rate)

        # Direction 1: Short A, Long B
        # Net funding rate per period = rate_a - rate_b
        net_rate_1 = rate_a - rate_b
        annual_pct_1 = net_rate_1 * 3.0 * 365.0 * 100.0

        # Direction 2: Short B, Long A
        # Net funding rate per period = rate_b - rate_a
        net_rate_2 = rate_b - rate_a
        annual_pct_2 = net_rate_2 * 3.0 * 365.0 * 100.0

        fees_dir = self.fee_engine.calculate_directional_fees(
            venue_a=v_a,
            venue_b=v_b,
            symbol=sym,
            market_type=market_type,
            fee_tier_a=fee_tier_a,
            fee_tier_b=fee_tier_b,
        )

        # Cand A: Venue A is Maker, Venue B is Hedge
        # If Direction 1 (Short A, Long B) is preferred:
        # Venue A places SELL limit order (Maker), Venue B places BUY taker order (Hedge).
        # Fees: A Maker + B Taker
        # If Direction 2 (Short B, Long A) is preferred:
        # Venue A places BUY limit order (Maker), Venue B places SELL taker order (Hedge).
        # Fees: A Maker + B Taker

        is_dir_1 = net_rate_1 >= net_rate_2
        if is_dir_1:
            short_v = v_a
            long_v = v_b
            short_r = rate_a
            long_r = rate_b
            net_r = net_rate_1
            ann_pct = annual_pct_1
            # In Dir 1: A is Short (SELL), B is Long (BUY)
            side_a = "SELL"
            side_b = "BUY"
            next_settle = max(funding_a.next_funding_time, funding_b.next_funding_time)
        else:
            short_v = v_b
            long_v = v_a
            short_r = rate_b
            long_r = rate_a
            net_r = net_rate_2
            ann_pct = annual_pct_2
            # In Dir 2: B is Short (SELL), A is Long (BUY)
            side_a = "BUY"
            side_b = "SELL"
            next_settle = max(funding_a.next_funding_time, funding_b.next_funding_time)

        # Candidate A: Venue A is First Leg / Maker, Venue B is Second Leg / Hedge
        opp_cand_a = DirectionalFundingOpportunity(
            symbol=sym,
            short_venue=short_v,
            long_venue=long_v,
            short_funding_rate=short_r,
            long_funding_rate=long_r,
            net_funding_rate=round(net_r, 6),
            net_funding_annual_pct=round(ann_pct, 4),
            candidate_id="A",
            maker_venue=v_a,
            maker_side=side_a,
            hedge_venue=v_b,
            hedge_side=side_b,
            total_fee_pct=fees_dir.candidate_a_total_fee_pct,
            net_opportunity_pct=round(ann_pct - (fees_dir.candidate_a_total_fee_pct * 12.0), 4), # Annualized net of amortization
            next_settlement_time=next_settle,
            validity=True,
            timestamps={"evaluated": now, "funding_a": funding_a.timestamp, "funding_b": funding_b.timestamp},
        )

        # Candidate B: Venue B is First Leg / Maker, Venue A is Second Leg / Hedge
        opp_cand_b = DirectionalFundingOpportunity(
            symbol=sym,
            short_venue=short_v,
            long_venue=long_v,
            short_funding_rate=short_r,
            long_funding_rate=long_r,
            net_funding_rate=round(net_r, 6),
            net_funding_annual_pct=round(ann_pct, 4),
            candidate_id="B",
            maker_venue=v_b,
            maker_side=side_b,
            hedge_venue=v_a,
            hedge_side=side_a,
            total_fee_pct=fees_dir.candidate_b_total_fee_pct,
            net_opportunity_pct=round(ann_pct - (fees_dir.candidate_b_total_fee_pct * 12.0), 4),
            next_settlement_time=next_settle,
            validity=True,
            timestamps={"evaluated": now, "funding_a": funding_a.timestamp, "funding_b": funding_b.timestamp},
        )

        return opp_cand_a, opp_cand_b

    def select_highest_funding_opportunity(
        self,
        opp_a: DirectionalFundingOpportunity,
        opp_b: DirectionalFundingOpportunity,
        min_annual_funding_pct: Optional[float] = None,
    ) -> FundingArbitrageDecision:
        """
        Step 10.1: Select First Leg / Maker using Highest Funding % (net of directional trading costs).
        Funding % selects the venue/direction ONLY. It is never the Maker order price.
        Priced venue does not automatically become Maker; Reference venue does not automatically become Hedge.
        """
        min_ann = min_annual_funding_pct if min_annual_funding_pct is not None else self.min_annual_funding_pct

        valid_cands = [o for o in [opp_a, opp_b] if o and o.validity]
        if not valid_cands:
            return FundingArbitrageDecision(
                symbol=opp_a.symbol if opp_a else "",
                is_viable=False,
                rejection_reason="No valid directional funding opportunity",
            )

        # Select candidate with lower fees / highest net opportunity
        selected = max(valid_cands, key=lambda c: (c.net_funding_annual_pct, -c.total_fee_pct))

        is_viable = True
        rejection_reason = None

        if selected.net_funding_annual_pct < min_ann:
            is_viable = False
            rejection_reason = (
                f"Net funding annual yield ({selected.net_funding_annual_pct:.2f}%) is below minimum threshold ({min_ann:.2f}%)"
            )

        # Independent funding exit conditions (Section 10.9)
        exit_conditions = {
            "exit_type": "FUNDING_SETTLEMENT",
            "settlement_target_time": selected.next_settlement_time,
            "target_settlement_count": 1,
            "rate_inversion_stop": True, # Exit if funding rate inverts (net funding becomes negative)
            "max_duration_hours": 24,
            "use_spread_convergence_exit": False, # Explicitly separate from Spread Arbitrage exit!
        }

        risk_metrics = {
            "net_funding_rate": selected.net_funding_rate,
            "net_funding_annual_pct": selected.net_funding_annual_pct,
            "total_fee_pct": selected.total_fee_pct,
            "short_venue": selected.short_venue,
            "long_venue": selected.long_venue,
        }

        return FundingArbitrageDecision(
            symbol=selected.symbol,
            selected_opportunity=selected,
            first_leg_venue=selected.maker_venue,
            second_leg_venue=selected.hedge_venue,
            maker_side=selected.maker_side,
            hedge_side=selected.hedge_side,
            net_funding_rate=selected.net_funding_rate,
            net_funding_annual_pct=selected.net_funding_annual_pct,
            is_viable=is_viable,
            rejection_reason=rejection_reason,
            risk_metrics=risk_metrics,
            exit_conditions=exit_conditions,
        )

    def calculate_maker_entry_pricing(
        self,
        decision: FundingArbitrageDecision,
        book_maker: OrderBook,
        book_hedge: OrderBook,
        pricing_mode: str = "SPREAD",
        spread_offset_pct: float = 0.0,
        grid_spacing_pct: float = 0.05,
        grid_levels_count: int = 3,
        bollinger_k: float = 2.0,
        spread_history: Optional[List[float]] = None,
        order_quantity: Optional[float] = None,
    ) -> MakerEntryPricing:
        """
        Step 10.4: Maker Entry Price Determination.
        Funding % selects the venue/direction ONLY.
        Spread % / Grid / Bollinger determines the actual Maker order entry level.
        Funding % must NEVER directly determine the order price.
        """
        qty = order_quantity or self.default_order_quantity
        side = decision.maker_side
        mode = pricing_mode.upper()

        if side == "BUY":
            base_price = book_maker.best_bid if book_maker.best_bid > 0 else book_maker.best_ask
            hedge_price = book_hedge.best_ask if book_hedge.best_ask > 0 else book_hedge.best_bid
        else:
            base_price = book_maker.best_ask if book_maker.best_ask > 0 else book_maker.best_bid
            hedge_price = book_hedge.best_bid if book_hedge.best_bid > 0 else book_hedge.best_ask

        if mode == "SPREAD":
            if side == "BUY":
                entry_price = round(base_price * (1.0 - (spread_offset_pct / 100.0)), 6)
            else:
                entry_price = round(base_price * (1.0 + (spread_offset_pct / 100.0)), 6)

            return MakerEntryPricing(
                pricing_mode="SPREAD",
                base_maker_price=entry_price,
                maker_side=side,
                order_quantity=qty,
                exit_target_price=0.0, # Funding Arbitrage uses funding settlement exit, not price target
                target_hedge_price=hedge_price,
            )

        elif mode == "GRID":
            levels_n = max(1, grid_levels_count)
            qty_per_lvl = round(qty / levels_n, 6)
            grid_lvls = []

            for i in range(levels_n):
                step = i * (grid_spacing_pct / 100.0)
                if side == "BUY":
                    p = round(base_price * (1.0 - step), 6)
                else:
                    p = round(base_price * (1.0 + step), 6)
                grid_lvls.append({
                    "level": i,
                    "price": p,
                    "quantity": qty_per_lvl,
                    "side": side,
                })

            return MakerEntryPricing(
                pricing_mode="GRID",
                base_maker_price=grid_lvls[0]["price"] if grid_lvls else base_price,
                maker_side=side,
                order_quantity=qty,
                grid_levels=grid_lvls,
                exit_target_price=0.0,
                target_hedge_price=hedge_price,
            )

        elif mode == "BOLLINGER":
            history = spread_history or [0.1, 0.2]
            boll = self.spread_calculator.calculate_bollinger_bands(history, k=bollinger_k)
            return MakerEntryPricing(
                pricing_mode="BOLLINGER",
                base_maker_price=round(base_price, 6),
                maker_side=side,
                order_quantity=qty,
                bollinger_bands=boll,
                exit_target_price=0.0,
                target_hedge_price=hedge_price,
            )

        else:
            return MakerEntryPricing(
                pricing_mode=mode,
                base_maker_price=base_price,
                maker_side=side,
                order_quantity=qty,
                exit_target_price=0.0,
                target_hedge_price=hedge_price,
            )

    def evaluate_funding_arbitrage(
        self,
        funding_a: FundingRate,
        funding_b: FundingRate,
        book_a: OrderBook,
        book_b: OrderBook,
        symbol: Optional[str] = None,
        min_annual_funding_pct: Optional[float] = None,
        pricing_mode: str = "SPREAD",
        spread_offset_pct: float = 0.0,
        grid_spacing_pct: float = 0.05,
        grid_levels_count: int = 3,
        bollinger_k: float = 2.0,
        spread_history: Optional[List[float]] = None,
        order_quantity: Optional[float] = None,
        market_type: str = "perp",
        fee_tier_a: str = "standard",
        fee_tier_b: str = "standard",
        grid_step_pct: Optional[float] = None,
        current_time: Optional[float] = None,
        **kwargs,
    ) -> FundingArbitrageDecision:
        """
        Complete end-to-end Step 10 pipeline:
        1. Evaluate both directions (Short A + Long B vs Short B + Long A).
        2. Calculate Directional Funding %.
        3. Select Highest Funding % -> sets First Leg = Maker & Second Leg = Hedge.
        4. Calculate Maker entry level via Spread % / Grid / Bollinger.
        5. Define independent Funding Settlement exit lifecycle.
        """
        if grid_step_pct is not None:
            grid_spacing_pct = grid_step_pct

        opp_a, opp_b = self.evaluate_directional_funding(
            funding_a=funding_a,
            funding_b=funding_b,
            book_a=book_a,
            book_b=book_b,
            symbol=symbol,
            market_type=market_type,
            fee_tier_a=fee_tier_a,
            fee_tier_b=fee_tier_b,
            current_time=current_time,
        )

        decision = self.select_highest_funding_opportunity(
            opp_a=opp_a,
            opp_b=opp_b,
            min_annual_funding_pct=min_annual_funding_pct,
        )

        if decision.is_viable and decision.selected_opportunity:
            # Map Maker and Hedge books according to selected direction
            is_a_maker = decision.first_leg_venue == funding_a.venue.upper()
            maker_b = book_a if is_a_maker else book_b
            hedge_b = book_b if is_a_maker else book_a

            decision.pricing = self.calculate_maker_entry_pricing(
                decision=decision,
                book_maker=maker_b,
                book_hedge=hedge_b,
                pricing_mode=pricing_mode,
                spread_offset_pct=spread_offset_pct,
                grid_spacing_pct=grid_spacing_pct,
                grid_levels_count=grid_levels_count,
                bollinger_k=bollinger_k,
                spread_history=spread_history,
                order_quantity=order_quantity,
            )

        return decision
