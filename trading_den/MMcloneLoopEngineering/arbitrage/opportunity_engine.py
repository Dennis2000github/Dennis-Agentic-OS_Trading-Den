"""
Step 8: Opportunity Engine (OpportunityEngine).

Integrates:
- directional spread (from Step 6 DirectionEngine / Candidate A & Candidate B)
- maker fee (from Step 7 FeeEngine)
- taker fee (from Step 7 FeeEngine)
- slippage (VWAP / order book depth model or calibrated threshold)
- liquidity (executable fill size across legs)
- other existing cost models (transfer/gas/custom threshold)

Invariants:
- Raw Spread remains strictly separate from Net Spread.
- The Opportunity Engine remains authoritative for Net Spread %.
- The frontend must not independently calculate profitability.
- Return both directional opportunities and their calculation components.
- Reuse existing fee engine and direction engine (no duplication).
- Only real market data may enter the calculation.
- Stale/invalid data invalidates the opportunity rather than generating a placeholder.
"""

import logging
import time
from typing import Any, Dict, Optional, Tuple

from .direction_engine import DirectionEngine
from .fee_engine import FeeEngine
from .models import (
    DirectionEvaluationResult,
    DirectionalOpportunity,
    MakerHedgeCandidate,
    OpportunityEvaluationResult,
    OrderBook,
    OrderBookEntry,
    FreshnessStatus,
    ConnectionStatus,
)

logger = logging.getLogger(__name__)


class OpportunityEngine:
    """
    Authoritative Opportunity Engine.
    Evaluates both Candidate A (A Maker -> B Hedge) and Candidate B (B Maker -> A Hedge),
    integrating directional spreads, maker/taker fees, slippage, liquidity, and cost models.
    """

    def __init__(
        self,
        fee_engine: Optional[FeeEngine] = None,
        direction_engine: Optional[DirectionEngine] = None,
        min_profit_threshold_pct: float = 0.05,
        default_slippage_pct: float = 0.0,
        other_costs_pct: float = 0.0,
    ):
        self.fee_engine = fee_engine or FeeEngine()
        self.direction_engine = direction_engine or DirectionEngine(fee_engine=self.fee_engine)
        self.min_profit_threshold_pct = min_profit_threshold_pct
        self.default_slippage_pct = default_slippage_pct
        self.other_costs_pct = other_costs_pct

    def calculate_depth_slippage(
        self,
        order_book: Optional[OrderBook],
        side: str,
        target_size: float,
        top_of_book_price: float,
    ) -> float:
        """
        Calculate depth-based slippage % from order book VWAP vs top-of-book price.
        Returns slippage as a percentage (e.g. 0.02 for 0.02%).
        """
        if not order_book or not target_size or target_size <= 0 or top_of_book_price <= 0:
            return self.default_slippage_pct

        vwap, _, ok = order_book.get_vwap(side, target_size)
        if not ok or vwap <= 0:
            # Insufficient depth at target_size -> high slippage penalty
            return 0.10  # 10 bps default penalty if book lacks requested depth

        # For buy (consuming asks), slippage = ((vwap - top_ask) / top_ask) * 100
        # For sell (consuming bids), slippage = ((top_bid - vwap) / top_bid) * 100
        if side.lower() == "buy":
            slippage = max(0.0, ((vwap - top_of_book_price) / top_of_book_price) * 100.0)
        else:
            slippage = max(0.0, ((top_of_book_price - vwap) / top_of_book_price) * 100.0)

        return round(slippage, 4)

    def evaluate_directional_opportunities(
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
        market_type: str = "spot",
        fee_tier_a: str = "standard",
        fee_tier_b: str = "standard",
        account_a: Optional[str] = None,
        account_b: Optional[str] = None,
        instrument: Optional[str] = None,
        other_costs_pct: Optional[float] = None,
        stale_threshold_seconds: Optional[float] = None,
        current_time: Optional[float] = None,
    ) -> OpportunityEvaluationResult:
        """
        Step 8: Authoritative evaluation of both directional opportunities.

        Integrates:
        - Directional spread from DirectionEngine (Candidate A & Candidate B)
        - Maker fee for First Leg venue (from FeeEngine)
        - Taker fee for Second Leg venue (from FeeEngine)
        - Order book depth slippage (or calibrated model)
        - Executable liquidity = min(maker_size, hedge_size)
        - Other cost models

        Keeps:
        - raw_spread_pct: strictly fee-independent
        - net_spread_pct: authoritative backend net spread

        Invalid/stale market data strictly invalidates the opportunity without generating placeholders.
        """
        now = current_time if current_time is not None else time.time()
        min_thresh = min_profit_threshold_pct if min_profit_threshold_pct is not None else self.min_profit_threshold_pct
        costs_pct = other_costs_pct if other_costs_pct is not None else self.other_costs_pct

        # 1. Step 6: Evaluate Maker/Hedge direction candidates (includes Step 7 directional fees)
        direction_result: DirectionEvaluationResult = self.direction_engine.evaluate_candidates(
            venue_a_data=venue_a_data,
            venue_b_data=venue_b_data,
            symbol=symbol,
            venue_a_name=venue_a_name,
            venue_b_name=venue_b_name,
            reference_venue=reference_venue,
            priced_venue=priced_venue,
            market_type=market_type,
            fee_tier_a=fee_tier_a,
            fee_tier_b=fee_tier_b,
            account_a=account_a,
            account_b=account_b,
            instrument=instrument or symbol,
            fee_engine=self.fee_engine,
            stale_threshold_seconds=stale_threshold_seconds,
            current_time=now,
        )

        cand_a = direction_result.candidate_a
        cand_b = direction_result.candidate_b
        v_a = direction_result.venue_a
        v_b = direction_result.venue_b
        sym = direction_result.symbol
        ref_v = direction_result.reference_venue
        priced_v = direction_result.priced_venue

        # If direction candidates are invalid (missing data, stale data, crossed quotes, same venue):
        # Strict requirement: "Invalid/stale market data must invalidate the opportunity rather than generating a placeholder."
        if not cand_a.validity and not cand_b.validity:
            error_reason = cand_a.error_reason or cand_b.error_reason or "Invalid market data"
            invalid_opp_a = DirectionalOpportunity(
                candidate_id="A",
                symbol=sym,
                maker_venue=v_a,
                maker_price=cand_a.maker_price,
                maker_side=cand_a.maker_side,
                maker_size=0.0,
                hedge_venue=v_b,
                hedge_price=cand_a.hedge_price,
                hedge_side=cand_a.hedge_side,
                hedge_size=0.0,
                direction=cand_a.direction,
                raw_spread_pct=None,
                raw_spread_usd=None,
                gross_spread_pct=None,
                net_spread_pct=None,
                net_spread_usd=None,
                is_profitable=False,
                calculation_components={},
                timestamps=cand_a.timestamps,
                validity=False,
                error_reason=error_reason,
                reference_venue=ref_v,
                priced_venue=priced_v,
            )
            invalid_opp_b = DirectionalOpportunity(
                candidate_id="B",
                symbol=sym,
                maker_venue=v_b,
                maker_price=cand_b.maker_price,
                maker_side=cand_b.maker_side,
                maker_size=0.0,
                hedge_venue=v_a,
                hedge_price=cand_b.hedge_price,
                hedge_side=cand_b.hedge_side,
                hedge_size=0.0,
                direction=cand_b.direction,
                raw_spread_pct=None,
                raw_spread_usd=None,
                gross_spread_pct=None,
                net_spread_pct=None,
                net_spread_usd=None,
                is_profitable=False,
                calculation_components={},
                timestamps=cand_b.timestamps,
                validity=False,
                error_reason=error_reason,
                reference_venue=ref_v,
                priced_venue=priced_v,
            )
            return OpportunityEvaluationResult(
                symbol=sym,
                venue_a=v_a,
                venue_b=v_b,
                reference_venue=ref_v,
                priced_venue=priced_v,
                opportunity_a=invalid_opp_a,
                opportunity_b=invalid_opp_b,
                highest_net_opportunity=None,
                timestamp=now,
                validity=False,
                error_reason=error_reason,
            )

        # 2. Depth slippage evaluation if OrderBook depth is available
        book_a = venue_a_data if isinstance(venue_a_data, OrderBook) else None
        book_b = venue_b_data if isinstance(venue_b_data, OrderBook) else None

        # Opportunity A: Candidate A (A Maker -> B Hedge)
        opp_a = self._build_directional_opportunity(
            candidate=cand_a,
            order_book_maker=book_a,
            order_book_hedge=book_b,
            target_size=target_size,
            min_profit_threshold_pct=min_thresh,
            other_costs_pct=costs_pct,
            reference_venue=ref_v,
            priced_venue=priced_v,
        )

        # Opportunity B: Candidate B (B Maker -> A Hedge)
        opp_b = self._build_directional_opportunity(
            candidate=cand_b,
            order_book_maker=book_b,
            order_book_hedge=book_a,
            target_size=target_size,
            min_profit_threshold_pct=min_thresh,
            other_costs_pct=costs_pct,
            reference_venue=ref_v,
            priced_venue=priced_v,
        )

        # 3. Select Highest Net Opportunity (Step 8 / Step 9 preview)
        highest_opp: Optional[DirectionalOpportunity] = None
        if opp_a.validity and opp_b.validity:
            net_a = opp_a.net_spread_pct if opp_a.net_spread_pct is not None else -999999.0
            net_b = opp_b.net_spread_pct if opp_b.net_spread_pct is not None else -999999.0
            highest_opp = opp_a if net_a >= net_b else opp_b
        elif opp_a.validity:
            highest_opp = opp_a
        elif opp_b.validity:
            highest_opp = opp_b

        overall_validity = bool((opp_a.validity or opp_b.validity) and highest_opp is not None)

        return OpportunityEvaluationResult(
            symbol=sym,
            venue_a=v_a,
            venue_b=v_b,
            reference_venue=ref_v,
            priced_venue=priced_v,
            opportunity_a=opp_a,
            opportunity_b=opp_b,
            highest_net_opportunity=highest_opp,
            timestamp=now,
            validity=overall_validity,
            error_reason=None if overall_validity else (opp_a.error_reason or opp_b.error_reason),
        )

    def _build_directional_opportunity(
        self,
        candidate: MakerHedgeCandidate,
        order_book_maker: Optional[OrderBook],
        order_book_hedge: Optional[OrderBook],
        target_size: Optional[float],
        min_profit_threshold_pct: float,
        other_costs_pct: float,
        reference_venue: str,
        priced_venue: str,
    ) -> DirectionalOpportunity:
        """
        Build full DirectionalOpportunity object with authoritative Net Spread %
        and complete calculation components breakdown.
        """
        if not candidate.validity:
            return DirectionalOpportunity(
                candidate_id=candidate.candidate_id,
                symbol=candidate.symbol,
                maker_venue=candidate.maker_venue,
                maker_price=candidate.maker_price,
                maker_side=candidate.maker_side,
                maker_size=candidate.maker_size,
                hedge_venue=candidate.hedge_venue,
                hedge_price=candidate.hedge_price,
                hedge_side=candidate.hedge_side,
                hedge_size=candidate.hedge_size,
                direction=candidate.direction,
                raw_spread_pct=candidate.raw_spread_pct,
                gross_spread_pct=candidate.gross_spread_pct,
                net_spread_pct=None,
                net_spread_usd=None,
                is_profitable=False,
                calculation_components={},
                timestamps=candidate.timestamps,
                validity=False,
                error_reason=candidate.error_reason or "Invalid candidate",
                reference_venue=reference_venue,
                priced_venue=priced_venue,
            )

        # 1. Slippage calculation (VWAP against hedge order book)
        hedge_book_side = "buy" if candidate.hedge_side == "BUY" else "sell"
        slippage_pct = 0.0
        if target_size and target_size > 0 and order_book_hedge:
            slippage_pct = self.calculate_depth_slippage(
                order_book=order_book_hedge,
                side=hedge_book_side,
                target_size=target_size,
                top_of_book_price=candidate.hedge_price,
            )
        else:
            slippage_pct = self.default_slippage_pct

        # 2. Liquidity (Available executable fill size = min of maker and hedge depth)
        available_liquidity = min(candidate.maker_size, candidate.hedge_size)
        if available_liquidity <= 0:
            available_liquidity = max(candidate.maker_size, candidate.hedge_size)
        liquidity_usd = round(available_liquidity * candidate.maker_price, 2) if candidate.maker_price > 0 else 0.0

        # 3. Spreads
        # Gross Spread % from execution prices
        gross_spread_pct = candidate.gross_spread_pct if candidate.gross_spread_pct is not None else 0.0
        raw_spread_pct = candidate.raw_spread_pct  # Kept strictly fee-independent!

        # Raw Spread USD
        raw_spread_usd = round(abs(candidate.hedge_price - candidate.maker_price), 6)

        # Total deductions % = total_fee_pct + slippage_pct + other_costs_pct
        total_fee_pct = candidate.total_fee_pct
        total_deductions_pct = total_fee_pct + slippage_pct + other_costs_pct

        # Net Spread % = Gross Spread % - Total Deductions %
        net_spread_pct = round(gross_spread_pct - total_deductions_pct, 4)

        # Net Spread USD
        base_price = min(candidate.maker_price, candidate.hedge_price) if min(candidate.maker_price, candidate.hedge_price) > 0 else candidate.maker_price
        net_spread_usd = round((net_spread_pct / 100.0) * base_price, 4)

        # Authoritative Profitability Gating
        is_profitable = bool(net_spread_pct >= min_profit_threshold_pct)

        # Complete Calculation Components Breakdown
        calculation_components = {
            "candidate_id": candidate.candidate_id,
            "direction": candidate.direction,
            "maker_venue": candidate.maker_venue,
            "maker_price": candidate.maker_price,
            "maker_side": candidate.maker_side,
            "hedge_venue": candidate.hedge_venue,
            "hedge_price": candidate.hedge_price,
            "hedge_side": candidate.hedge_side,
            "raw_spread_pct": raw_spread_pct,
            "gross_spread_pct": gross_spread_pct,
            "maker_fee_rate": candidate.maker_fee_rate,
            "maker_fee_pct": round(candidate.maker_fee_rate * 100.0, 4),
            "taker_fee_rate": candidate.taker_fee_rate,
            "taker_fee_pct": round(candidate.taker_fee_rate * 100.0, 4),
            "total_fee_rate": candidate.total_fee_rate,
            "total_fee_pct": round(candidate.total_fee_pct, 4),
            "slippage_pct": round(slippage_pct, 4),
            "other_costs_pct": round(other_costs_pct, 4),
            "total_deductions_pct": round(total_deductions_pct, 4),
            "net_spread_pct": net_spread_pct,
            "net_spread_usd": net_spread_usd,
            "min_profit_threshold_pct": min_profit_threshold_pct,
            "is_profitable": is_profitable,
            "executable_liquidity_size": available_liquidity,
            "executable_liquidity_usd": liquidity_usd,
        }

        return DirectionalOpportunity(
            candidate_id=candidate.candidate_id,
            symbol=candidate.symbol,
            maker_venue=candidate.maker_venue,
            maker_price=candidate.maker_price,
            maker_side=candidate.maker_side,
            maker_size=candidate.maker_size,
            hedge_venue=candidate.hedge_venue,
            hedge_price=candidate.hedge_price,
            hedge_side=candidate.hedge_side,
            hedge_size=candidate.hedge_size,
            direction=candidate.direction,
            raw_spread_pct=raw_spread_pct,
            raw_spread_usd=raw_spread_usd,
            gross_spread_pct=gross_spread_pct,
            net_spread_pct=net_spread_pct,
            net_spread_usd=net_spread_usd,
            maker_fee_rate=candidate.maker_fee_rate,
            taker_fee_rate=candidate.taker_fee_rate,
            total_fee_rate=candidate.total_fee_rate,
            total_fee_pct=candidate.total_fee_pct,
            slippage_pct=round(slippage_pct, 4),
            other_costs_pct=round(other_costs_pct, 4),
            liquidity_size=available_liquidity,
            liquidity_usd=liquidity_usd,
            is_profitable=is_profitable,
            calculation_components=calculation_components,
            timestamps=candidate.timestamps,
            validity=True,
            error_reason=None,
            reference_venue=reference_venue,
            priced_venue=priced_venue,
        )
