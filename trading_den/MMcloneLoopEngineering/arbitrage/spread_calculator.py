"""
Arbitrage Spread Calculator.
Calculates cross-exchange spreads, triangular arbitrage loops,
and MT5 vs Crypto reference spreads, factoring in taker fees and order book depth (VWAP).
"""

import logging
import time
from typing import Any, Dict, List, Optional, Tuple, Union

from .models import (
    OrderBook,
    SpreadResult,
    RawSpreadResult,
    MakerHedgeCandidate,
    DirectionEvaluationResult,
    DirectionalFeeResult,
    TriangularOpportunity,
    ArbitrageOpportunity,
    FreshnessStatus,
    ConnectionStatus,
    NormalizedMarketState,
    DirectionalOpportunity,
    OpportunityEvaluationResult,
    SpreadArbitrageDecision,
    FundingArbitrageDecision,
    MakerEntryPricing,
    FundingRate,
)
from .direction_engine import DirectionEngine
from .fee_engine import FeeEngine
from .opportunity_engine import OpportunityEngine
from .spread_arbitrage import SpreadArbitrageEngine
from .funding_arbitrage import FundingArbitrageEngine

logger = logging.getLogger(__name__)


class SpreadCalculator:
    """
    Core engine for computing market making spreads and arbitrage margins.
    """

    def __init__(
        self,
        default_taker_fee: float = 0.001,  # 0.1% per leg (standard taker fee)
        min_profit_threshold_pct: float = 0.05,  # 0.05% minimum net profit
    ):
        self.default_taker_fee = default_taker_fee
        self.min_profit_threshold_pct = min_profit_threshold_pct
        self.fee_engine = FeeEngine(default_taker_fee=default_taker_fee)
        self.direction_engine = DirectionEngine(fee_engine=self.fee_engine)
        self.opportunity_engine = OpportunityEngine(
            fee_engine=self.fee_engine,
            direction_engine=self.direction_engine,
            min_profit_threshold_pct=min_profit_threshold_pct,
        )
        self.spread_arbitrage_engine = SpreadArbitrageEngine(
            opportunity_engine=self.opportunity_engine,
            spread_calculator=self,
            min_profit_threshold_pct=min_profit_threshold_pct,
        )
        self.funding_arbitrage_engine = FundingArbitrageEngine(
            fee_engine=self.fee_engine,
            spread_calculator=self,
            min_annual_funding_pct=5.0,
        )

    @staticmethod
    def _extract_market_quote(data: Any) -> Optional[dict]:
        """
        Extract normalized bid, ask, and metadata from OrderBook, NormalizedMarketState, Ticker, or dict.
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

    def calculate_raw_spread(
        self,
        reference_data: Any,
        priced_data: Any,
        symbol: Optional[str] = None,
        reference_venue: Optional[str] = None,
        priced_venue: Optional[str] = None,
        stale_threshold_seconds: float = 10.0,
        current_time: Optional[float] = None,
    ) -> RawSpreadResult:
        """
        Step 5: Backend Raw Spread % for every valid Reference/Priced relationship.
        Uses real live executable bid & ask.
        Independent of fees, funding, slippage, and other costs.
        Evaluates both directions.
        Stale/invalid market data produces no valid spread (validity=False, raw_spread_pct=None).
        Never fabricates spread when data is missing.
        """
        now = current_time if current_time is not None else time.time()

        q_ref = self._extract_market_quote(reference_data)
        q_priced = self._extract_market_quote(priced_data)

        v_ref = (reference_venue or (q_ref["venue"] if q_ref else (getattr(reference_data, "venue", None) or "REFERENCE"))).upper()
        v_priced = (priced_venue or (q_priced["venue"] if q_priced else (getattr(priced_data, "venue", None) or "PRICED"))).upper()
        sym = symbol or (q_ref["symbol"] if q_ref else (q_priced["symbol"] if q_priced else ""))

        # 1. Missing data check
        if q_ref is None or q_priced is None:
            missing = []
            if q_ref is None:
                missing.append(v_ref)
            if q_priced is None:
                missing.append(v_priced)
            return RawSpreadResult(
                reference_venue=v_ref,
                priced_venue=v_priced,
                symbol=sym,
                prices={},
                direction="INVALID",
                raw_spread_pct=None,
                timestamp=now,
                freshness=FreshnessStatus.WAITING_FOR_DATA.value,
                validity=False,
                error_reason=f"Missing market data for venue(s): {', '.join(missing)}",
            )

        # 2. Same venue check (diagonal cell)
        if v_ref.upper() == v_priced.upper():
            return RawSpreadResult(
                reference_venue=v_ref,
                priced_venue=v_priced,
                symbol=sym,
                prices={
                    "reference_bid": q_ref["bid"],
                    "reference_ask": q_ref["ask"],
                    "priced_bid": q_priced["bid"],
                    "priced_ask": q_priced["ask"],
                },
                direction="SAME_VENUE",
                raw_spread_pct=None,
                timestamp=now,
                freshness=FreshnessStatus.FRESH.value,
                validity=False,
                error_reason="Reference and priced venues are identical",
            )

        # 3. Connection status check
        disconnected_statuses = (
            ConnectionStatus.DISCONNECTED.value,
            ConnectionStatus.FAILED.value,
            ConnectionStatus.UNAVAILABLE.value,
            "offline",
            "disconnected",
        )
        if q_ref["connection_status"] in disconnected_statuses or q_priced["connection_status"] in disconnected_statuses:
            return RawSpreadResult(
                reference_venue=v_ref,
                priced_venue=v_priced,
                symbol=sym,
                prices={
                    "reference_bid": q_ref["bid"],
                    "reference_ask": q_ref["ask"],
                    "priced_bid": q_priced["bid"],
                    "priced_ask": q_priced["ask"],
                },
                direction="INVALID",
                raw_spread_pct=None,
                timestamp=now,
                freshness=FreshnessStatus.UNAVAILABLE.value,
                validity=False,
                error_reason="One or both venue connectors are disconnected/unavailable",
            )

        # 4. Invalid prices check (None, non-positive, crossed book)
        if not q_ref["is_valid"] or not q_priced["is_valid"]:
            invalid_venues = []
            if not q_ref["is_valid"]:
                invalid_venues.append(v_ref)
            if not q_priced["is_valid"]:
                invalid_venues.append(v_priced)
            return RawSpreadResult(
                reference_venue=v_ref,
                priced_venue=v_priced,
                symbol=sym,
                prices={
                    "reference_bid": q_ref["bid"],
                    "reference_ask": q_ref["ask"],
                    "priced_bid": q_priced["bid"],
                    "priced_ask": q_priced["ask"],
                },
                direction="INVALID",
                raw_spread_pct=None,
                timestamp=now,
                freshness=FreshnessStatus.INVALID.value,
                validity=False,
                error_reason=f"Invalid or crossed market data on: {', '.join(invalid_venues)}",
            )

        # 5. Stale data check
        t_ref = q_ref["timestamp"] if q_ref["timestamp"] < 1e11 else q_ref["timestamp"] / 1000.0
        t_priced = q_priced["timestamp"] if q_priced["timestamp"] < 1e11 else q_priced["timestamp"] / 1000.0
        age_ref = max(0.0, now - t_ref) if t_ref > 0 else float("inf")
        age_priced = max(0.0, now - t_priced) if t_priced > 0 else float("inf")

        is_stale = (
            age_ref > stale_threshold_seconds
            or age_priced > stale_threshold_seconds
            or q_ref["freshness"] == FreshnessStatus.STALE.value
            or q_priced["freshness"] == FreshnessStatus.STALE.value
        )
        if is_stale:
            return RawSpreadResult(
                reference_venue=v_ref,
                priced_venue=v_priced,
                symbol=sym,
                prices={
                    "reference_bid": q_ref["bid"],
                    "reference_ask": q_ref["ask"],
                    "priced_bid": q_priced["bid"],
                    "priced_ask": q_priced["ask"],
                },
                direction="INVALID",
                raw_spread_pct=None,
                timestamp=min(q_ref["timestamp"], q_priced["timestamp"]),
                freshness=FreshnessStatus.STALE.value,
                validity=False,
                error_reason=f"Stale market data detected (age: {v_ref}={age_ref:.1f}s, {v_priced}={age_priced:.1f}s)",
            )

        # 6. Valid Raw Spread Computation (Both directions)
        ref_bid = q_ref["bid"]
        ref_ask = q_ref["ask"]
        priced_bid = q_priced["bid"]
        priced_ask = q_priced["ask"]

        ref_mid = (ref_bid + ref_ask) / 2.0
        priced_mid = (priced_bid + priced_ask) / 2.0

        # Direction 1: Buy Reference (ref_ask), Sell Priced (priced_bid)
        spread_ref_to_priced_usd = priced_bid - ref_ask
        spread_ref_to_priced_pct = ((priced_bid - ref_ask) / ref_ask) * 100.0

        # Direction 2: Buy Priced (priced_ask), Sell Reference (ref_bid)
        spread_priced_to_ref_usd = ref_bid - priced_ask
        spread_priced_to_ref_pct = ((ref_bid - priced_ask) / priced_ask) * 100.0

        if spread_ref_to_priced_pct >= spread_priced_to_ref_pct:
            direction = "BUY_REF_SELL_PRICED"
            raw_spread_pct = round(spread_ref_to_priced_pct, 4)
            raw_spread_usd = round(spread_ref_to_priced_usd, 6)
        else:
            direction = "BUY_PRICED_SELL_REF"
            raw_spread_pct = round(spread_priced_to_ref_pct, 4)
            raw_spread_usd = round(spread_priced_to_ref_usd, 6)

        exec_bid = priced_bid if direction == "BUY_REF_SELL_PRICED" else ref_bid
        exec_ask = ref_ask if direction == "BUY_REF_SELL_PRICED" else priced_ask

        prices = {
            "reference_bid": ref_bid,
            "reference_ask": ref_ask,
            "priced_bid": priced_bid,
            "priced_ask": priced_ask,
            "exec_bid": exec_bid,
            "exec_ask": exec_ask,
            "reference_mid": round(ref_mid, 6),
            "priced_mid": round(priced_mid, 6),
            "reference_bid_size": q_ref["bid_size"],
            "reference_ask_size": q_ref["ask_size"],
            "priced_bid_size": q_priced["bid_size"],
            "priced_ask_size": q_priced["ask_size"],
            "spread_ref_to_priced_pct": round(spread_ref_to_priced_pct, 4),
            "spread_priced_to_ref_pct": round(spread_priced_to_ref_pct, 4),
        }

        return RawSpreadResult(
            reference_venue=v_ref,
            priced_venue=v_priced,
            symbol=sym,
            prices=prices,
            direction=direction,
            raw_spread_pct=raw_spread_pct,
            raw_spread_usd=raw_spread_usd,
            spread_ref_to_priced_pct=round(spread_ref_to_priced_pct, 4),
            spread_priced_to_ref_pct=round(spread_priced_to_ref_pct, 4),
            timestamp=max(q_ref["timestamp"], q_priced["timestamp"]),
            freshness=FreshnessStatus.FRESH.value,
            validity=True,
            error_reason=None,
        )

    def evaluate_maker_hedge_candidates(
        self,
        venue_a_data: Any,
        venue_b_data: Any,
        symbol: Optional[str] = None,
        venue_a_name: Optional[str] = None,
        venue_b_name: Optional[str] = None,
        reference_venue: Optional[str] = None,
        priced_venue: Optional[str] = None,
        stale_threshold_seconds: Optional[float] = None,
        current_time: Optional[float] = None,
    ) -> DirectionEvaluationResult:
        """
        Step 6: Evaluate dynamic Maker/Hedge execution direction between Venue A and Venue B.

        Returns Candidate A (A Maker -> B Hedge) and Candidate B (B Maker -> A Hedge).
        Never assumes Reference = Hedge or Priced = Maker.
        """
        return self.direction_engine.evaluate_candidates(
            venue_a_data=venue_a_data,
            venue_b_data=venue_b_data,
            symbol=symbol,
            venue_a_name=venue_a_name,
            venue_b_name=venue_b_name,
            reference_venue=reference_venue,
            priced_venue=priced_venue,
            stale_threshold_seconds=stale_threshold_seconds,
            current_time=current_time,
        )

    def calculate_directional_fees(
        self,
        venue_a: str,
        venue_b: str,
        symbol: str = "BTC/USDT",
        market_type: str = "spot",
        fee_tier_a: str = "standard",
        fee_tier_b: str = "standard",
        account_a: Optional[str] = None,
        account_b: Optional[str] = None,
        instrument: Optional[str] = None,
    ) -> DirectionalFeeResult:
        """
        Step 7: Calculate Directional Maker/Taker Fees:
        - Candidate A (A Maker -> B Taker): A Maker Fee + B Taker Fee
        - Candidate B (B Maker -> A Taker): B Maker Fee + A Taker Fee

        Raw Spread remains fee-independent.
        """
        return self.fee_engine.calculate_directional_fees(
            venue_a=venue_a,
            venue_b=venue_b,
            symbol=symbol,
            market_type=market_type,
            fee_tier_a=fee_tier_a,
            fee_tier_b=fee_tier_b,
            account_a=account_a,
            account_b=account_b,
            instrument=instrument,
        )

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
        Step 8: Authoritative Opportunity Engine evaluation.
        Integrates:
        - directional spread
        - maker fee
        - taker fee
        - slippage
        - liquidity
        - other cost models

        Raw Spread remains strictly separate from Net Spread.
        Returns both directional opportunities (Candidate A & B) and their calculation components.
        """
        return self.opportunity_engine.evaluate_directional_opportunities(
            venue_a_data=venue_a_data,
            venue_b_data=venue_b_data,
            symbol=symbol,
            venue_a_name=venue_a_name,
            venue_b_name=venue_b_name,
            reference_venue=reference_venue,
            priced_venue=priced_venue,
            target_size=target_size,
            min_profit_threshold_pct=min_profit_threshold_pct,
            market_type=market_type,
            fee_tier_a=fee_tier_a,
            fee_tier_b=fee_tier_b,
            account_a=account_a,
            account_b=account_b,
            instrument=instrument,
            other_costs_pct=other_costs_pct,
            stale_threshold_seconds=stale_threshold_seconds,
            current_time=current_time,
        )

    def calculate_cross_exchange_spread(
        self,
        book_a: Optional[OrderBook] = None,
        book_b: Optional[OrderBook] = None,
        fee_rate_a: Optional[float] = None,
        fee_rate_b: Optional[float] = None,
        target_size: Optional[float] = None,
        *,
        reference_book: Optional[OrderBook] = None,
        priced_book: Optional[OrderBook] = None,
        fee_rate_ref: Optional[float] = None,
        fee_rate_priced: Optional[float] = None,
    ) -> Optional[SpreadResult]:
        """
        Compute top-of-book or VWAP-adjusted cross-exchange spread evaluating a Priced Venue
        against a Reference Venue.

        Neither venue role implies fixed Maker/Hedge roles; both venues dynamically become
        first leg (Maker) or second leg (Hedge/Taker) depending on direction:
        - Route Ref->Priced: Buy Reference (ask_ref), Sell Priced (bid_priced).
        - Route Priced->Ref: Buy Priced (ask_priced), Sell Reference (bid_ref).
        """
        ref_b = reference_book if reference_book is not None else book_a
        priced_b = priced_book if priced_book is not None else book_b
        if not ref_b or not priced_b:
            return None

        fee_ref = fee_rate_ref if fee_rate_ref is not None else (self.default_taker_fee if fee_rate_a is None else fee_rate_a)
        fee_priced = fee_rate_priced if fee_rate_priced is not None else (self.default_taker_fee if fee_rate_b is None else fee_rate_b)
        total_fee_pct = (fee_ref + fee_priced) * 100.0

        if target_size and target_size > 0:
            # Depth-adjusted VWAP pricing
            ask_ref, _, ok_ref_ask = ref_b.get_vwap('buy', target_size)
            bid_ref, _, ok_ref_bid = ref_b.get_vwap('sell', target_size)
            ask_priced, _, ok_priced_ask = priced_b.get_vwap('buy', target_size)
            bid_priced, _, ok_priced_bid = priced_b.get_vwap('sell', target_size)

            if not (ok_ref_ask and ok_ref_bid and ok_priced_ask and ok_priced_bid):
                # Fallback to top-of-book if depth was insufficient
                ask_ref = ref_b.best_ask
                bid_ref = ref_b.best_bid
                ask_priced = priced_b.best_ask
                bid_priced = priced_b.best_bid
        else:
            ask_ref = ref_b.best_ask
            bid_ref = ref_b.best_bid
            ask_priced = priced_b.best_ask
            bid_priced = priced_b.best_bid

        if None in (ask_ref, bid_ref, ask_priced, bid_priced):
            return None

        # Route Ref -> Priced: Buy on Ref, Sell on Priced
        spread_ref_to_priced = bid_priced - ask_ref
        spread_pct_ref_to_priced = ((bid_priced - ask_ref) / ask_ref) * 100.0

        # Route Priced -> Ref: Buy on Priced, Sell on Ref
        spread_priced_to_ref = bid_ref - ask_priced
        spread_pct_priced_to_ref = ((bid_ref - ask_priced) / ask_priced) * 100.0

        if spread_pct_ref_to_priced >= spread_pct_priced_to_ref:
            best_route = f"{ref_b.venue}->{priced_b.venue}"
            gross_pct = spread_pct_ref_to_priced
            buy_venue = ref_b.venue
            sell_venue = priced_b.venue
            buy_price = ask_ref
            sell_price = bid_priced
            first_leg_venue = ref_b.venue
            second_leg_venue = priced_b.venue
        else:
            best_route = f"{priced_b.venue}->{ref_b.venue}"
            gross_pct = spread_pct_priced_to_ref
            buy_venue = priced_b.venue
            sell_venue = ref_b.venue
            buy_price = ask_priced
            sell_price = bid_ref
            first_leg_venue = priced_b.venue
            second_leg_venue = ref_b.venue

        net_pct = gross_pct - total_fee_pct
        is_profitable = net_pct >= self.min_profit_threshold_pct

        return SpreadResult(
            symbol=ref_b.symbol,
            reference_venue=ref_b.venue,
            priced_venue=priced_b.venue,
            reference_bid=bid_ref,
            reference_ask=ask_ref,
            priced_bid=bid_priced,
            priced_ask=ask_priced,
            spread_ref_to_priced=round(spread_ref_to_priced, 6),
            spread_pct_ref_to_priced=round(spread_pct_ref_to_priced, 4),
            spread_priced_to_ref=round(spread_priced_to_ref, 6),
            spread_pct_priced_to_ref=round(spread_pct_priced_to_ref, 4),
            best_route=best_route,
            gross_spread_pct=round(gross_pct, 4),
            net_spread_pct=round(net_pct, 4),
            is_profitable=is_profitable,
            buy_venue=buy_venue,
            sell_venue=sell_venue,
            buy_price=buy_price,
            sell_price=sell_price,
            first_leg_venue=first_leg_venue,
            second_leg_venue=second_leg_venue,
            reference_source=getattr(ref_b, "source", "UNKNOWN"),
            priced_source=getattr(priced_b, "source", "UNKNOWN"),
        )

    def calculate_triangular_arbitrage(
        self,
        venue: str,
        book_ab: OrderBook,  # e.g., BTC/USDT (Base: BTC, Quote: USDT)
        book_cb: OrderBook,  # e.g., ETH/USDT (Base: ETH, Quote: USDT)
        book_ca: OrderBook,  # e.g., ETH/BTC  (Base: ETH, Quote: BTC)
        fee_rate: Optional[float] = None,
    ) -> List[TriangularOpportunity]:
        """
        Calculate Triangular Arbitrage across 3 pairs on the same venue.
        Loop 1 (Forward):  USDT -> Buy BTC -> Buy ETH with BTC -> Sell ETH for USDT
        Loop 2 (Reverse):  USDT -> Buy ETH -> Sell ETH for BTC -> Sell BTC for USDT
        """
        fee = self.default_taker_fee if fee_rate is None else fee_rate
        fee_multiplier = (1.0 - fee) ** 3  # 3 trading legs
        opportunities: List[TriangularOpportunity] = []

        # Validate prices
        if not (book_ab.best_ask and book_ab.best_bid and
                book_cb.best_ask and book_cb.best_bid and
                book_ca.best_ask and book_ca.best_bid):
            return opportunities

        # Loop 1: USDT -> BTC -> ETH -> USDT
        # 1. Buy BTC at ask_ab (USDT/BTC)
        # 2. Buy ETH at ask_ca (BTC/ETH)
        # 3. Sell ETH at bid_cb (ETH/USDT)
        init_amount = 1.0
        btc_amount = (init_amount / book_ab.best_ask)
        eth_amount = (btc_amount / book_ca.best_ask)
        final_usdt_gross = eth_amount * book_cb.best_bid
        final_usdt_net = final_usdt_gross * fee_multiplier

        gross_profit_pct_1 = (final_usdt_gross - init_amount) * 100.0
        net_profit_pct_1 = (final_usdt_net - init_amount) * 100.0

        opportunities.append(TriangularOpportunity(
            venue=venue,
            route=[book_ab.symbol.split('/')[1], book_ab.symbol.split('/')[0], book_ca.symbol.split('/')[0], book_cb.symbol.split('/')[1]],
            pairs=[book_ab.symbol, book_ca.symbol, book_cb.symbol],
            actions=["buy", "buy", "sell"],
            rates=[book_ab.best_ask, book_ca.best_ask, book_cb.best_bid],
            gross_profit_pct=round(gross_profit_pct_1, 4),
            net_profit_pct=round(net_profit_pct_1, 4),
            is_profitable=net_profit_pct_1 >= self.min_profit_threshold_pct,
        ))

        # Loop 2: USDT -> ETH -> BTC -> USDT
        # 1. Buy ETH at ask_cb (USDT/ETH)
        # 2. Sell ETH at bid_ca (ETH/BTC) -> receive BTC
        # 3. Sell BTC at bid_ab (BTC/USDT) -> receive USDT
        eth_amount_2 = (init_amount / book_cb.best_ask)
        btc_amount_2 = eth_amount_2 * book_ca.best_bid
        final_usdt_gross_2 = btc_amount_2 * book_ab.best_bid
        final_usdt_net_2 = final_usdt_gross_2 * fee_multiplier

        gross_profit_pct_2 = (final_usdt_gross_2 - init_amount) * 100.0
        net_profit_pct_2 = (final_usdt_net_2 - init_amount) * 100.0

        opportunities.append(TriangularOpportunity(
            venue=venue,
            route=[book_cb.symbol.split('/')[1], book_cb.symbol.split('/')[0], book_ca.symbol.split('/')[1], book_ab.symbol.split('/')[1]],
            pairs=[book_cb.symbol, book_ca.symbol, book_ab.symbol],
            actions=["buy", "sell", "sell"],
            rates=[book_cb.best_ask, book_ca.best_bid, book_ab.best_bid],
            gross_profit_pct=round(gross_profit_pct_2, 4),
            net_profit_pct=round(net_profit_pct_2, 4),
            is_profitable=net_profit_pct_2 >= self.min_profit_threshold_pct,
        ))

        return opportunities

    def calculate_mt5_crypto_spread(
        self,
        crypto_book: OrderBook,
        mt5_book: Optional[OrderBook],
        fx_conversion_rate: float = 1.0,
    ) -> Optional[Dict[str, float]]:
        """
        Calculate reference spread between a crypto order book (e.g. Binance)
        and an MT5 broker order book (or tick).
        Spread % = ((Crypto_Mid - MT5_Mid_Converted) / MT5_Mid_Converted) * 100.
        """
        if not mt5_book or not crypto_book.mid_price or not mt5_book.mid_price:
            return None

        crypto_mid = crypto_book.mid_price
        mt5_converted = mt5_book.mid_price * fx_conversion_rate

        spread_usd = crypto_mid - mt5_converted
        spread_pct = (spread_usd / mt5_converted) * 100.0

        return {
            "symbol": crypto_book.symbol,
            "reference_venue": crypto_book.venue,
            "priced_venue": getattr(mt5_book, "venue", "MT5"),
            "reference_mid": round(crypto_mid, 4),
            "priced_mid": round(mt5_book.mid_price, 4),
            "priced_mid_converted": round(mt5_converted, 4),
            "reference_best_bid": crypto_book.best_bid,
            "reference_best_ask": crypto_book.best_ask,
            "priced_best_bid": mt5_book.best_bid,
            "priced_best_ask": mt5_book.best_ask,
            "crypto_mid": round(crypto_mid, 4),
            "mt5_mid": round(mt5_book.mid_price, 4),
            "mt5_mid_converted": round(mt5_converted, 4),
            "spread_usd": round(spread_usd, 4),
            "spread_pct": round(spread_pct, 4),
            "crypto_best_bid": crypto_book.best_bid,
            "crypto_best_ask": crypto_book.best_ask,
            "mt5_best_bid": mt5_book.best_bid,
            "mt5_best_ask": mt5_book.best_ask,
        }

    def calculate_bollinger_bands(
        self,
        spread_history: List[float],
        k: float = 2.0,
        period: int = 20,
        min_width_pct: float = 0.001,
    ) -> Dict[str, float]:
        """
        Compute rolling Bollinger Bands over historical spread series (TASK-CORE-101).

        Formula:
            mean = (1 / N) * sum(x_i)
            variance = (1 / (N - 1)) * sum((x_i - mean)^2)  (unbiased sample variance)
            sd = sqrt(variance)
            upper = mean + max(k * sd, min_width_pct)
            lower = mean - max(k * sd, min_width_pct)
            bandwidth = upper - lower
            %b = (current - lower) / bandwidth

        :param spread_history: Sequence of historical spread percentage points
        :param k: Standard deviation multiplier (k-sigma, default 2.0, clamped [0.5, 4.0])
        :param period: Rolling window size (default 20, clamped [5, 60])
        :param min_width_pct: Minimum half-bandwidth floor in % (default 0.001% = 0.1 bps)
        :return: Dict containing mean, upper, lower, sd, bandwidth, pct_b
        """
        p = max(5, min(60, int(period)))
        k_val = max(0.5, min(4.0, float(k)))
        window = [float(x) for x in spread_history[-p:]] if spread_history else []
        n = len(window)

        if n == 0:
            return {
                "mean": 0.0,
                "upper": round(min_width_pct, 6),
                "lower": round(-min_width_pct, 6),
                "sd": 0.0,
                "bandwidth": round(2.0 * min_width_pct, 6),
                "pct_b": 0.5,
            }

        mean = sum(window) / n
        if n > 1:
            variance = sum((x - mean) ** 2 for x in window) / (n - 1)
        else:
            variance = 0.0
        sd = max(0.0, variance) ** 0.5

        half_w = max(k_val * sd, min_width_pct)
        upper = mean + half_w
        lower = mean - half_w
        bandwidth = upper - lower
        current = window[-1]
        pct_b = (current - lower) / bandwidth if bandwidth > 1e-9 else 0.5

        return {
            "mean": round(mean, 6),
            "upper": round(upper, 6),
            "lower": round(lower, 6),
            "sd": round(sd, 6),
            "bandwidth": round(bandwidth, 6),
            "pct_b": round(pct_b, 6),
        }

    def evaluate_bollinger_strategy(
        self,
        current_spread: float,
        spread_history: List[float],
        k: float = 2.0,
        period: int = 20,
        min_width_pct: float = 0.001,
    ) -> Dict[str, any]:
        """
        Evaluate Bollinger Band arbitrage entry/exit signals (TASK-CORE-101):
        - Buy signal (direction = -1): current_spread <= lower band (buy cheap leg, expect mean-reversion)
        - Sell signal (direction = +1): current_spread >= upper band (sell rich leg, expect mean-reversion)
        - Exit target: mean (0% or rolling spread mean)
        """
        bands = self.calculate_bollinger_bands(
            spread_history, k=k, period=period, min_width_pct=min_width_pct
        )
        should_enter = False
        direction = 0

        if current_spread <= bands["lower"]:
            should_enter = True
            direction = -1
        elif current_spread >= bands["upper"]:
            should_enter = True
            direction = 1

        return {
            "should_enter": should_enter,
            "direction": direction,
            "current_spread": current_spread,
            "bands": bands,
            "exit_target": bands["mean"],
        }

    def evaluate_spread_arbitrage(
        self,
        venue_a_data: Any,
        venue_b_data: Any,
        symbol: str,
        pricing_mode: str = "SPREAD",
        min_profit_threshold_pct: Optional[float] = None,
        grid_step_pct: float = 0.25,
        grid_levels_count: int = 3,
        bollinger_k: float = 2.0,
        bollinger_period: int = 20,
        current_time: Optional[float] = None,
    ) -> SpreadArbitrageDecision:
        """
        Evaluate Spread Arbitrage strategy for a venue pair and symbol (Step 9).
        Selects direction via Highest Net Spread %, determines Maker entry pricing.
        """
        return self.spread_arbitrage_engine.evaluate_spread_arbitrage(
            venue_a_data=venue_a_data,
            venue_b_data=venue_b_data,
            symbol=symbol,
            pricing_mode=pricing_mode,
            min_profit_threshold_pct=min_profit_threshold_pct,
            grid_step_pct=grid_step_pct,
            grid_levels_count=grid_levels_count,
            bollinger_k=bollinger_k,
            bollinger_period=bollinger_period,
            current_time=current_time,
        )

    def evaluate_funding_arbitrage(
        self,
        funding_a: FundingRate,
        funding_b: FundingRate,
        book_a: Optional[Any] = None,
        book_b: Optional[Any] = None,
        symbol: str = "BTC/USDT",
        pricing_mode: str = "SPREAD",
        min_annual_funding_pct: Optional[float] = None,
        grid_step_pct: float = 0.25,
        grid_levels_count: int = 3,
        bollinger_k: float = 2.0,
        bollinger_period: int = 20,
        current_time: Optional[float] = None,
    ) -> FundingArbitrageDecision:
        """
        Evaluate Funding Arbitrage strategy for a venue pair and symbol (Step 10).
        Selects direction via Highest Funding %, Maker pricing via Spread/Grid/Bollinger,
        and provides independent funding exit conditions.
        """
        return self.funding_arbitrage_engine.evaluate_funding_arbitrage(
            funding_a=funding_a,
            funding_b=funding_b,
            book_a=book_a,
            book_b=book_b,
            symbol=symbol,
            pricing_mode=pricing_mode,
            min_annual_funding_pct=min_annual_funding_pct,
            grid_step_pct=grid_step_pct,
            grid_levels_count=grid_levels_count,
            bollinger_k=bollinger_k,
            bollinger_period=bollinger_period,
            current_time=current_time,
        )

