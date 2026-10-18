"""
Step 7: Directional Maker/Taker Fee Engine (FeeEngine).
Calculates directional fees for both Maker/Hedge candidates:
- For Candidate A (A Maker -> B Taker):
    total fee = A Maker Fee + B Taker Fee
- For Candidate B (B Maker -> A Taker):
    total fee = B Maker Fee + A Taker Fee

Accounts for:
- exchange (Binance, Bybit, OKX, Kraken, MEXC, KuCoin, Coinbase)
- account / custom tier overrides
- instrument / symbol
- market type (spot vs perp/futures)
- fee tier (standard, vip1, custom)

Raw Spread remains fee-independent.
Centralizes fee logic without duplication.
No order placement or exit changes.
"""

import logging
from typing import Dict, Optional, Tuple

from .models import DirectionEvaluationResult, DirectionalFeeResult, MakerHedgeCandidate

logger = logging.getLogger(__name__)


# Exchange Default Fee Schedules (Maker Fee, Taker Fee) by Market Type & Tier
DEFAULT_EXCHANGE_FEES: Dict[str, Dict[str, Dict[str, Tuple[float, float]]]] = {
    "BINANCE": {
        "spot": {
            "standard": (0.0010, 0.0010),      # 0.10% maker, 0.10% taker
            "vip1": (0.0009, 0.0010),          # 0.09% maker, 0.10% taker
            "bnb_discount": (0.00075, 0.00075), # 0.075% maker, 0.075% taker
        },
        "perp": {
            "standard": (0.0002, 0.0005),      # 0.02% maker, 0.05% taker
            "vip1": (0.00016, 0.0004),         # 0.016% maker, 0.04% taker
        },
    },
    "BYBIT": {
        "spot": {
            "standard": (0.0010, 0.0010),      # 0.10% maker, 0.10% taker
            "vip1": (0.0004, 0.0006),          # 0.04% maker, 0.06% taker
        },
        "perp": {
            "standard": (0.0002, 0.00055),     # 0.02% maker, 0.055% taker
            "vip1": (0.00018, 0.0004),         # 0.018% maker, 0.04% taker
        },
    },
    "OKX": {
        "spot": {
            "standard": (0.0008, 0.0010),      # 0.08% maker, 0.10% taker
            "vip1": (0.0006, 0.0008),          # 0.06% maker, 0.08% taker
        },
        "perp": {
            "standard": (0.0002, 0.0005),      # 0.02% maker, 0.05% taker
            "vip1": (0.00015, 0.00035),        # 0.015% maker, 0.035% taker
        },
    },
    "KRAKEN": {
        "spot": {
            "standard": (0.0025, 0.0040),      # 0.25% maker, 0.40% taker
            "vip1": (0.0016, 0.0026),          # 0.16% maker, 0.26% taker
        },
        "perp": {
            "standard": (0.0002, 0.0005),      # 0.02% maker, 0.05% taker
        },
    },
    "MEXC": {
        "spot": {
            "standard": (0.0000, 0.0010),      # 0.00% maker (promotional), 0.10% taker
            "vip1": (0.0000, 0.0008),          # 0.00% maker, 0.08% taker
        },
        "perp": {
            "standard": (0.0000, 0.0002),      # 0.00% maker, 0.02% taker
        },
    },
    "KUCOIN": {
        "spot": {
            "standard": (0.0010, 0.0010),
            "vip1": (0.0007, 0.0009),
        },
        "perp": {
            "standard": (0.0002, 0.0006),
        },
    },
    "COINBASE": {
        "spot": {
            "standard": (0.0040, 0.0060),      # 0.40% maker, 0.60% taker
            "vip1": (0.0015, 0.0025),
        },
    },
}

FALLBACK_FEE_RATES: Tuple[float, float] = (0.0010, 0.0010)  # 0.10% maker, 0.10% taker


class FeeEngine:
    """
    Centralized Directional Fee Engine.
    Computes Maker and Taker fees directionally:
    - Candidate A: A Maker Fee + B Taker Fee
    - Candidate B: B Maker Fee + A Taker Fee
    """

    def __init__(self, default_maker_fee: float = 0.0010, default_taker_fee: float = 0.0010):
        self.default_maker_fee = default_maker_fee
        self.default_taker_fee = default_taker_fee
        # Custom user / account fee schedule overrides: key = f"{VENUE}:{MARKET_TYPE}:{TIER}"
        self._custom_schedules: Dict[str, Tuple[float, float]] = {}

    def set_custom_fee(
        self,
        exchange: str,
        maker_fee: float,
        taker_fee: float,
        market_type: str = "spot",
        fee_tier: str = "custom",
        account: Optional[str] = None,
        instrument: Optional[str] = None,
    ):
        """Register custom fee rate override for an exchange/tier/account/instrument."""
        ex = exchange.upper()
        m_type = market_type.lower()
        tier = fee_tier.lower()
        if account and instrument:
            self._custom_schedules[f"{account.upper()}:{ex}:{instrument.upper()}:{m_type}:{tier}"] = (maker_fee, taker_fee)
            self._custom_schedules[f"{account.upper()}:{ex}:{instrument.upper()}:{m_type}"] = (maker_fee, taker_fee)
        elif account:
            self._custom_schedules[f"{account.upper()}:{ex}:{m_type}:{tier}"] = (maker_fee, taker_fee)
            self._custom_schedules[f"{account.upper()}:{ex}:{m_type}"] = (maker_fee, taker_fee)
        elif instrument:
            self._custom_schedules[f"{ex}:{instrument.upper()}:{m_type}:{tier}"] = (maker_fee, taker_fee)
            self._custom_schedules[f"{ex}:{instrument.upper()}:{m_type}"] = (maker_fee, taker_fee)
        else:
            self._custom_schedules[f"{ex}:{m_type}:{tier}"] = (maker_fee, taker_fee)

    def get_fee_rates(
        self,
        exchange: str,
        market_type: str = "spot",
        fee_tier: str = "standard",
        account: Optional[str] = None,
        instrument: Optional[str] = None,
    ) -> Tuple[float, float]:
        """
        Get (maker_fee, taker_fee) rates for an exchange, market type, and fee tier.
        Returns rates as decimals, e.g. (0.0008, 0.0010) for 0.08% maker and 0.10% taker.
        """
        ex = (exchange or "").upper()
        m_type = (market_type or "spot").lower()
        tier = (fee_tier or "standard").lower()
        inst = (instrument or "").upper() if instrument else None
        acc = (account or "").upper() if account else None

        # 1. Check custom overrides in hierarchical order of specificity:
        # a) Account + Exchange + Instrument + MarketType (+ Tier)
        if acc and inst:
            for k in [f"{acc}:{ex}:{inst}:{m_type}:{tier}", f"{acc}:{ex}:{inst}:{m_type}"]:
                if k in self._custom_schedules:
                    return self._custom_schedules[k]
        # b) Account + Exchange + MarketType (+ Tier)
        if acc:
            for k in [f"{acc}:{ex}:{m_type}:{tier}", f"{acc}:{ex}:{m_type}"]:
                if k in self._custom_schedules:
                    return self._custom_schedules[k]
        # c) Exchange + Instrument + MarketType (+ Tier)
        if inst:
            for k in [f"{ex}:{inst}:{m_type}:{tier}", f"{ex}:{inst}:{m_type}"]:
                if k in self._custom_schedules:
                    return self._custom_schedules[k]
        # d) Exchange + MarketType + Tier
        for k in [f"{ex}:{m_type}:{tier}", f"{ex}:{m_type}:custom"]:
            if k in self._custom_schedules:
                return self._custom_schedules[k]

        # 2. Check standard exchange fee table
        if ex in DEFAULT_EXCHANGE_FEES:
            ex_types = DEFAULT_EXCHANGE_FEES[ex]
            type_key = "perp" if ("perp" in m_type or "future" in m_type or "swap" in m_type) else "spot"
            if type_key in ex_types:
                tiers = ex_types[type_key]
                if tier in tiers:
                    return tiers[tier]
                if "standard" in tiers:
                    return tiers["standard"]

        # 3. Fallback default
        return (self.default_maker_fee, self.default_taker_fee)

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
        Step 7: Calculate Directional Maker/Taker Fees for both candidates:
        - Candidate A (A Maker -> B Taker): A Maker Fee + B Taker Fee
        - Candidate B (B Maker -> A Taker): B Maker Fee + A Taker Fee

        Raw Spread remains fee-independent.
        """
        v_a = (venue_a or "").upper()
        v_b = (venue_b or "").upper()
        inst = instrument or symbol

        maker_a, taker_a = self.get_fee_rates(v_a, market_type=market_type, fee_tier=fee_tier_a, account=account_a, instrument=inst)
        maker_b, taker_b = self.get_fee_rates(v_b, market_type=market_type, fee_tier=fee_tier_b, account=account_b, instrument=inst)

        # Candidate A: A Maker + B Taker
        cand_a_total = maker_a + taker_b
        cand_a_total_pct = cand_a_total * 100.0

        # Candidate B: B Maker + A Taker
        cand_b_total = maker_b + taker_a
        cand_b_total_pct = cand_b_total * 100.0

        return DirectionalFeeResult(
            symbol=symbol,
            instrument=inst,
            venue_a=v_a,
            venue_b=v_b,
            market_type=market_type,
            fee_tier_a=fee_tier_a,
            fee_tier_b=fee_tier_b,
            account_a=account_a,
            account_b=account_b,
            candidate_a_maker_venue=v_a,
            candidate_a_taker_venue=v_b,
            candidate_a_maker_fee=maker_a,
            candidate_a_taker_fee=taker_b,
            candidate_a_total_fee=round(cand_a_total, 6),
            candidate_a_total_fee_pct=round(cand_a_total_pct, 4),
            candidate_b_maker_venue=v_b,
            candidate_b_taker_venue=v_a,
            candidate_b_maker_fee=maker_b,
            candidate_b_taker_fee=taker_a,
            candidate_b_total_fee=round(cand_b_total, 6),
            candidate_b_total_fee_pct=round(cand_b_total_pct, 4),
        )

    def apply_directional_fees_to_candidates(
        self,
        direction_result: DirectionEvaluationResult,
        market_type: str = "spot",
        fee_tier_a: str = "standard",
        fee_tier_b: str = "standard",
        account_a: Optional[str] = None,
        account_b: Optional[str] = None,
        instrument: Optional[str] = None,
    ) -> DirectionEvaluationResult:
        """
        Annotate both Candidate A and Candidate B with their directional maker/taker fees.
        Raw Spread % remains strictly fee-independent!
        """
        fees = self.calculate_directional_fees(
            venue_a=direction_result.venue_a,
            venue_b=direction_result.venue_b,
            symbol=direction_result.symbol,
            market_type=market_type,
            fee_tier_a=fee_tier_a,
            fee_tier_b=fee_tier_b,
            account_a=account_a,
            account_b=account_b,
            instrument=instrument or direction_result.symbol,
        )

        cand_a = direction_result.candidate_a
        cand_b = direction_result.candidate_b

        cand_a.maker_fee_rate = fees.candidate_a_maker_fee
        cand_a.taker_fee_rate = fees.candidate_a_taker_fee
        cand_a.total_fee_rate = fees.candidate_a_total_fee
        cand_a.total_fee_pct = fees.candidate_a_total_fee_pct

        cand_b.maker_fee_rate = fees.candidate_b_maker_fee
        cand_b.taker_fee_rate = fees.candidate_b_taker_fee
        cand_b.total_fee_rate = fees.candidate_b_total_fee
        cand_b.total_fee_pct = fees.candidate_b_total_fee_pct

        return direction_result
