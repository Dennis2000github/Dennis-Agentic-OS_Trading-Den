"""
Step 6: Dedicated Execution-Direction Layer (DirectionEngine).
Implements dynamic Maker/Hedge direction evaluation between any two venues:
- Evaluates Candidate A: A Maker / First Leg -> B Taker/Hedge / Second Leg
- Evaluates Candidate B: B Maker / First Leg -> A Taker/Hedge / Second Leg
- Never assumes Reference = Hedge or Priced = Maker.
- Returns both candidates with:
  1. Maker venue
  2. Maker price
  3. Hedge venue
  4. Hedge price
  5. direction
  6. timestamps
  7. validity
- No order placement.
- Uses only valid real market data.
"""

import logging
import time
from typing import Any, Dict, Optional, Tuple

from .models import (
    ConnectionStatus,
    DirectionEvaluationResult,
    FreshnessStatus,
    MakerHedgeCandidate,
    NormalizedMarketState,
    OrderBook,
)

logger = logging.getLogger(__name__)


class DirectionEngine:
    """
    Dedicated Execution-Direction Engine.
    Evaluates both directional execution candidates (A Maker -> B Hedge, and B Maker -> A Hedge)
    using live executable market data.
    """

    def __init__(self, stale_threshold_seconds: float = 10.0, fee_engine: Optional[Any] = None):
        self.stale_threshold_seconds = stale_threshold_seconds
        self.fee_engine = fee_engine

    def _extract_market_quote(self, data: Any) -> Optional[dict]:
        """
        Extract normalized bid, ask, sizes, timestamps, and validation status
        from OrderBook, NormalizedMarketState, Ticker, or dict.
        """
        if data is None:
            return None

        if isinstance(data, OrderBook):
            valid = data.is_valid()
            return {
                "venue": (data.venue or "").upper(),
                "symbol": data.symbol,
                "bid": data.best_bid,
                "ask": data.best_ask,
                "bid_size": data.best_bid_size or 0.0,
                "ask_size": data.best_ask_size or 0.0,
                "timestamp": data.timestamp or 0.0,
                "freshness": getattr(data, "freshness", FreshnessStatus.FRESH.value),
                "connection_status": getattr(data, "connection_status", ConnectionStatus.CONNECTED.value),
                "is_valid": valid,
            }

        if isinstance(data, NormalizedMarketState):
            valid = (data.best_bid > 0 and data.best_ask > 0 and data.best_ask >= data.best_bid)
            return {
                "venue": (data.venue or "").upper(),
                "symbol": data.symbol,
                "bid": data.best_bid,
                "ask": data.best_ask,
                "bid_size": data.bid_size,
                "ask_size": data.ask_size,
                "timestamp": data.timestamp or 0.0,
                "freshness": data.freshness,
                "connection_status": data.connection_status,
                "is_valid": valid,
            }

        if isinstance(data, dict):
            venue = (data.get("venue") or "").upper()
            sym = data.get("symbol", "")
            bid_raw = data.get("best_bid") if data.get("best_bid") is not None else data.get("bid")
            ask_raw = data.get("best_ask") if data.get("best_ask") is not None else data.get("ask")
            bid = float(bid_raw) if bid_raw is not None else None
            ask = float(ask_raw) if ask_raw is not None else None
            valid = (bid is not None and ask is not None and bid > 0 and ask > 0 and ask >= bid)
            return {
                "venue": venue,
                "symbol": sym,
                "bid": bid,
                "ask": ask,
                "bid_size": float(data.get("bid_size", 0.0) or 0.0),
                "ask_size": float(data.get("ask_size", 0.0) or 0.0),
                "timestamp": float(data.get("timestamp", 0.0) or 0.0),
                "freshness": data.get("freshness", FreshnessStatus.FRESH.value),
                "connection_status": data.get("connection_status", ConnectionStatus.CONNECTED.value),
                "is_valid": valid,
            }

        return None

    def evaluate_candidates(
        self,
        venue_a_data: Any,
        venue_b_data: Any,
        symbol: Optional[str] = None,
        venue_a_name: Optional[str] = None,
        venue_b_name: Optional[str] = None,
        reference_venue: Optional[str] = None,
        priced_venue: Optional[str] = None,
        market_type: str = "spot",
        fee_tier_a: str = "standard",
        fee_tier_b: str = "standard",
        account_a: Optional[str] = None,
        account_b: Optional[str] = None,
        instrument: Optional[str] = None,
        fee_engine: Optional[Any] = None,
        stale_threshold_seconds: Optional[float] = None,
        current_time: Optional[float] = None,
    ) -> DirectionEvaluationResult:
        """
        Step 6: Evaluate dynamic Maker/Hedge execution direction between Venue A and Venue B.

        Returns both:
        - Candidate A: Venue A is Maker / First Leg -> Venue B is Taker/Hedge / Second Leg
        - Candidate B: Venue B is Maker / First Leg -> Venue A is Taker/Hedge / Second Leg

        Never assumes Reference = Hedge or Priced = Maker.
        Returns both candidates with:
        Maker venue, Maker price, Hedge venue, Hedge price, direction, timestamps, validity.
        """
        now = current_time if current_time is not None else time.time()
        stale_limit = stale_threshold_seconds if stale_threshold_seconds is not None else self.stale_threshold_seconds

        q_a = self._extract_market_quote(venue_a_data)
        q_b = self._extract_market_quote(venue_b_data)

        v_a = (venue_a_name or (q_a["venue"] if q_a else getattr(venue_a_data, "venue", None)) or "VENUE_A").upper()
        v_b = (venue_b_name or (q_b["venue"] if q_b else getattr(venue_b_data, "venue", None)) or "VENUE_B").upper()
        sym = symbol or (q_a["symbol"] if q_a else (q_b["symbol"] if q_b else ""))
        ref_v = (reference_venue or "").upper()
        priced_v = (priced_venue or "").upper()

        # 1. Missing Data Check
        if q_a is None or q_b is None:
            missing = []
            if q_a is None:
                missing.append(v_a)
            if q_b is None:
                missing.append(v_b)
            err = f"Missing market data for venue(s): {', '.join(missing)}"

            cand_a = MakerHedgeCandidate(
                candidate_id="A",
                symbol=sym,
                maker_venue=v_a,
                maker_price=0.0,
                maker_side="INVALID",
                hedge_venue=v_b,
                hedge_price=0.0,
                hedge_side="INVALID",
                direction="INVALID",
                timestamps={"evaluated": now},
                validity=False,
                error_reason=err,
                reference_venue=ref_v,
                priced_venue=priced_v,
            )
            cand_b = MakerHedgeCandidate(
                candidate_id="B",
                symbol=sym,
                maker_venue=v_b,
                maker_price=0.0,
                maker_side="INVALID",
                hedge_venue=v_a,
                hedge_price=0.0,
                hedge_side="INVALID",
                direction="INVALID",
                timestamps={"evaluated": now},
                validity=False,
                error_reason=err,
                reference_venue=ref_v,
                priced_venue=priced_v,
            )
            return DirectionEvaluationResult(
                symbol=sym,
                venue_a=v_a,
                venue_b=v_b,
                candidate_a=cand_a,
                candidate_b=cand_b,
                reference_venue=ref_v,
                priced_venue=priced_v,
                timestamp=now,
            )

        # 2. Same Venue Diagonal Check
        if v_a.upper() == v_b.upper():
            err = "Cannot evaluate maker/hedge on same venue"
            cand_a = MakerHedgeCandidate(
                candidate_id="A",
                symbol=sym,
                maker_venue=v_a,
                maker_price=q_a["bid"] or 0.0,
                maker_side="SAME_VENUE",
                hedge_venue=v_b,
                hedge_price=q_b["ask"] or 0.0,
                hedge_side="SAME_VENUE",
                direction="SAME_VENUE",
                timestamps={"maker": q_a["timestamp"], "hedge": q_b["timestamp"], "evaluated": now},
                validity=False,
                error_reason=err,
                reference_venue=ref_v,
                priced_venue=priced_v,
            )
            cand_b = MakerHedgeCandidate(
                candidate_id="B",
                symbol=sym,
                maker_venue=v_b,
                maker_price=q_b["bid"] or 0.0,
                maker_side="SAME_VENUE",
                hedge_venue=v_a,
                hedge_price=q_a["ask"] or 0.0,
                hedge_side="SAME_VENUE",
                direction="SAME_VENUE",
                timestamps={"maker": q_b["timestamp"], "hedge": q_a["timestamp"], "evaluated": now},
                validity=False,
                error_reason=err,
                reference_venue=ref_v,
                priced_venue=priced_v,
            )
            return DirectionEvaluationResult(
                symbol=sym,
                venue_a=v_a,
                venue_b=v_b,
                candidate_a=cand_a,
                candidate_b=cand_b,
                reference_venue=ref_v,
                priced_venue=priced_v,
                timestamp=now,
            )

        # 3. Connection Status Check
        disconnected_statuses = (
            ConnectionStatus.DISCONNECTED.value,
            ConnectionStatus.FAILED.value,
            ConnectionStatus.UNAVAILABLE.value,
            "offline",
            "disconnected",
        )
        if q_a["connection_status"] in disconnected_statuses or q_b["connection_status"] in disconnected_statuses:
            err = "One or both venue connectors are disconnected/unavailable"
            cand_a = MakerHedgeCandidate(
                candidate_id="A",
                symbol=sym,
                maker_venue=v_a,
                maker_price=q_a["bid"] or 0.0,
                maker_side="DISCONNECTED",
                hedge_venue=v_b,
                hedge_price=q_b["ask"] or 0.0,
                hedge_side="DISCONNECTED",
                direction="DISCONNECTED",
                timestamps={"maker": q_a["timestamp"], "hedge": q_b["timestamp"], "evaluated": now},
                validity=False,
                error_reason=err,
                reference_venue=ref_v,
                priced_venue=priced_v,
            )
            cand_b = MakerHedgeCandidate(
                candidate_id="B",
                symbol=sym,
                maker_venue=v_b,
                maker_price=q_b["bid"] or 0.0,
                maker_side="DISCONNECTED",
                hedge_venue=v_a,
                hedge_price=q_a["ask"] or 0.0,
                hedge_side="DISCONNECTED",
                direction="DISCONNECTED",
                timestamps={"maker": q_b["timestamp"], "hedge": q_a["timestamp"], "evaluated": now},
                validity=False,
                error_reason=err,
                reference_venue=ref_v,
                priced_venue=priced_v,
            )
            return DirectionEvaluationResult(
                symbol=sym,
                venue_a=v_a,
                venue_b=v_b,
                candidate_a=cand_a,
                candidate_b=cand_b,
                reference_venue=ref_v,
                priced_venue=priced_v,
                timestamp=now,
            )

        # 4. Invalid or Crossed Prices Check
        if not q_a["is_valid"] or not q_b["is_valid"]:
            invalid_venues = []
            if not q_a["is_valid"]:
                invalid_venues.append(v_a)
            if not q_b["is_valid"]:
                invalid_venues.append(v_b)
            err = f"Invalid or crossed market data on: {', '.join(invalid_venues)}"
            cand_a = MakerHedgeCandidate(
                candidate_id="A",
                symbol=sym,
                maker_venue=v_a,
                maker_price=q_a["bid"] or 0.0,
                maker_side="INVALID",
                hedge_venue=v_b,
                hedge_price=q_b["ask"] or 0.0,
                hedge_side="INVALID",
                direction="INVALID",
                timestamps={"maker": q_a["timestamp"], "hedge": q_b["timestamp"], "evaluated": now},
                validity=False,
                error_reason=err,
                reference_venue=ref_v,
                priced_venue=priced_v,
            )
            cand_b = MakerHedgeCandidate(
                candidate_id="B",
                symbol=sym,
                maker_venue=v_b,
                maker_price=q_b["bid"] or 0.0,
                maker_side="INVALID",
                hedge_venue=v_a,
                hedge_price=q_a["ask"] or 0.0,
                hedge_side="INVALID",
                direction="INVALID",
                timestamps={"maker": q_b["timestamp"], "hedge": q_a["timestamp"], "evaluated": now},
                validity=False,
                error_reason=err,
                reference_venue=ref_v,
                priced_venue=priced_v,
            )
            return DirectionEvaluationResult(
                symbol=sym,
                venue_a=v_a,
                venue_b=v_b,
                candidate_a=cand_a,
                candidate_b=cand_b,
                reference_venue=ref_v,
                priced_venue=priced_v,
                timestamp=now,
            )

        # 5. Stale Data Check
        t_a = q_a["timestamp"] if q_a["timestamp"] < 1e11 else q_a["timestamp"] / 1000.0
        t_b = q_b["timestamp"] if q_b["timestamp"] < 1e11 else q_b["timestamp"] / 1000.0
        age_a = max(0.0, now - t_a) if t_a > 0 else float("inf")
        age_b = max(0.0, now - t_b) if t_b > 0 else float("inf")

        is_stale = (
            age_a > stale_limit
            or age_b > stale_limit
            or q_a["freshness"] == FreshnessStatus.STALE.value
            or q_b["freshness"] == FreshnessStatus.STALE.value
        )
        if is_stale:
            err = f"Stale market data detected (age: {v_a}={age_a:.1f}s, {v_b}={age_b:.1f}s)"
            cand_a = MakerHedgeCandidate(
                candidate_id="A",
                symbol=sym,
                maker_venue=v_a,
                maker_price=q_a["bid"] or 0.0,
                maker_side="STALE",
                hedge_venue=v_b,
                hedge_price=q_b["ask"] or 0.0,
                hedge_side="STALE",
                direction="STALE",
                timestamps={"maker": q_a["timestamp"], "hedge": q_b["timestamp"], "evaluated": now},
                validity=False,
                error_reason=err,
                reference_venue=ref_v,
                priced_venue=priced_v,
            )
            cand_b = MakerHedgeCandidate(
                candidate_id="B",
                symbol=sym,
                maker_venue=v_b,
                maker_price=q_b["bid"] or 0.0,
                maker_side="STALE",
                hedge_venue=v_a,
                hedge_price=q_a["ask"] or 0.0,
                hedge_side="STALE",
                direction="STALE",
                timestamps={"maker": q_b["timestamp"], "hedge": q_a["timestamp"], "evaluated": now},
                validity=False,
                error_reason=err,
                reference_venue=ref_v,
                priced_venue=priced_v,
            )
            return DirectionEvaluationResult(
                symbol=sym,
                venue_a=v_a,
                venue_b=v_b,
                candidate_a=cand_a,
                candidate_b=cand_b,
                reference_venue=ref_v,
                priced_venue=priced_v,
                timestamp=now,
            )

        # 6. Real Valid Market Quotes
        bid_a = float(q_a["bid"])
        ask_a = float(q_a["ask"])
        bid_b = float(q_b["bid"])
        ask_b = float(q_b["ask"])
        bid_sz_a = float(q_a["bid_size"])
        ask_sz_a = float(q_a["ask_size"])
        bid_sz_b = float(q_b["bid_size"])
        ask_sz_b = float(q_b["ask_size"])

        # Direction 1: Buy on A, Sell on B
        spread_buy_a_sell_b_pct = ((bid_b - ask_a) / ask_a) * 100.0
        # Direction 2: Buy on B, Sell on A
        spread_buy_b_sell_a_pct = ((bid_a - ask_b) / ask_b) * 100.0

        is_buy_a_sell_b = spread_buy_a_sell_b_pct >= spread_buy_b_sell_a_pct
        market_direction = "BUY_A_SELL_B" if is_buy_a_sell_b else "BUY_B_SELL_A"
        raw_spread_pct = round(spread_buy_a_sell_b_pct if is_buy_a_sell_b else spread_buy_b_sell_a_pct, 4)

        # ---------------------------------------------------------------------
        # Candidate A: Venue A is Maker / First Leg -> Venue B is Hedge / Second Leg
        # ---------------------------------------------------------------------
        if is_buy_a_sell_b:
            # Maker A BUYS (posted at ask_a executable or bid_a passive), Hedge B SELLS at bid_b
            maker_side_a = "BUY"
            maker_price_a = ask_a
            maker_size_a = ask_sz_a
            hedge_side_a = "SELL"
            hedge_price_a = bid_b
            hedge_size_a = bid_sz_b
        else:
            # Maker A SELLS (posted at bid_a executable or ask_a passive), Hedge B BUYS at ask_b
            maker_side_a = "SELL"
            maker_price_a = bid_a
            maker_size_a = bid_sz_a
            hedge_side_a = "BUY"
            hedge_price_a = ask_b
            hedge_size_a = ask_sz_b

        cand_a = MakerHedgeCandidate(
            candidate_id="A",
            symbol=sym,
            maker_venue=v_a,
            maker_price=maker_price_a,
            maker_side=maker_side_a,
            hedge_venue=v_b,
            hedge_price=hedge_price_a,
            hedge_side=hedge_side_a,
            direction=market_direction,
            raw_spread_pct=raw_spread_pct,
            gross_spread_pct=raw_spread_pct,
            maker_size=maker_size_a,
            hedge_size=hedge_size_a,
            timestamps={"maker": q_a["timestamp"], "hedge": q_b["timestamp"], "evaluated": now},
            validity=True,
            error_reason=None,
            reference_venue=ref_v,
            priced_venue=priced_v,
        )

        # ---------------------------------------------------------------------
        # Candidate B: Venue B is Maker / First Leg -> Venue A is Hedge / Second Leg
        # ---------------------------------------------------------------------
        if is_buy_a_sell_b:
            # Maker B SELLS at bid_b, Hedge A BUYS at ask_a
            maker_side_b = "SELL"
            maker_price_b = bid_b
            maker_size_b = bid_sz_b
            hedge_side_b = "BUY"
            hedge_price_b = ask_a
            hedge_size_b = ask_sz_a
        else:
            # Maker B BUYS at ask_b, Hedge A SELLS at bid_a
            maker_side_b = "BUY"
            maker_price_b = ask_b
            maker_size_b = ask_sz_b
            hedge_side_b = "SELL"
            hedge_price_b = bid_a
            hedge_size_b = bid_sz_a

        cand_b = MakerHedgeCandidate(
            candidate_id="B",
            symbol=sym,
            maker_venue=v_b,
            maker_price=maker_price_b,
            maker_side=maker_side_b,
            hedge_venue=v_a,
            hedge_price=hedge_price_b,
            hedge_side=hedge_side_b,
            direction=market_direction,
            raw_spread_pct=raw_spread_pct,
            gross_spread_pct=raw_spread_pct,
            maker_size=maker_size_b,
            hedge_size=hedge_size_b,
            timestamps={"maker": q_b["timestamp"], "hedge": q_a["timestamp"], "evaluated": now},
            validity=True,
            error_reason=None,
            reference_venue=ref_v,
            priced_venue=priced_v,
        )

        result = DirectionEvaluationResult(
            symbol=sym,
            venue_a=v_a,
            venue_b=v_b,
            candidate_a=cand_a,
            candidate_b=cand_b,
            reference_venue=ref_v,
            priced_venue=priced_v,
            timestamp=now,
        )

        f_engine = fee_engine or self.fee_engine
        if f_engine is not None and cand_a.validity and cand_b.validity:
            f_engine.apply_directional_fees_to_candidates(
                result,
                market_type=market_type,
                fee_tier_a=fee_tier_a,
                fee_tier_b=fee_tier_b,
                account_a=account_a,
                account_b=account_b,
                instrument=instrument or sym,
            )

        return result
