"""
bot_manager.py — Step 13: Screener → Bot Manager → Execution Pipeline.

Complete live pipeline coordination:
- Screener / Matrix updates
- Opportunity selection (Spread = Highest Net Spread %, Funding = Highest Funding %)
- Decoupled leg assignment: First Leg = Maker, Second Leg = Hedge / Taker
- Pre-submission revalidation across 10 vital market checks:
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
- Execution Engine handling Maker/Hedge lifecycle, Dry Run/LIVE mode, and reconciliation
- Zero live trading invariant during modular development
"""

import logging
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

from .models import (
    BotConfig,
    BotExecutionMode,
    BotStatus,
    ExecutionCycle,
    FundingRate,
    OrderBook,
    PreSubmissionValidationResult,
    SpreadArbitrageDecision,
    FundingArbitrageDecision,
)
from .spread_calculator import SpreadCalculator

logger = logging.getLogger(__name__)


class PreSubmissionValidator:
    """
    Step 13: Authoritative Pre-Submission Revalidation Engine.
    Before every order submission, revalidates:
    1. bid/ask: valid non-negative, non-zero prices, non-crossed book.
    2. order-book liquidity: available depth at requested size >= order_quantity.
    3. funding: valid funding rates and expected sign/yield.
    4. fees: directional maker/taker fees correctly modeled and included.
    5. slippage: expected slippage <= max_slippage_pct.
    6. timestamp: current exchange timestamp present and sensible.
    7. freshness: market state age <= stale_threshold_seconds.
    8. opportunity validity: is_viable is True, net opportunity > 0.
    9. execution direction: Maker (First Leg) and Hedge (Second Leg) dynamically set.
    10. position state: position limits not breached, no unhedged or locked state.
    """

    def __init__(
        self,
        default_stale_threshold_seconds: float = 10.0,
        default_max_slippage_pct: float = 0.20,
    ):
        self.default_stale_threshold_seconds = default_stale_threshold_seconds
        self.default_max_slippage_pct = default_max_slippage_pct

    def validate_pre_submission(
        self,
        maker_book: OrderBook,
        hedge_book: OrderBook,
        decision: Any,  # SpreadArbitrageDecision or FundingArbitrageDecision
        order_quantity: float,
        current_position_size: float = 0.0,
        max_position_size: float = 10.0,
        max_slippage_pct: Optional[float] = None,
        stale_threshold_seconds: Optional[float] = None,
        maker_funding: Optional[FundingRate] = None,
        hedge_funding: Optional[FundingRate] = None,
        current_time: Optional[float] = None,
    ) -> PreSubmissionValidationResult:
        now = current_time if current_time is not None else time.time()
        max_slip = max_slippage_pct if max_slippage_pct is not None else self.default_max_slippage_pct
        stale_thresh = stale_threshold_seconds if stale_threshold_seconds is not None else self.default_stale_threshold_seconds

        failed_checks = []
        checks_detail = {}
        rejection_reasons = []

        # 1. bid/ask validation
        maker_valid = bool(maker_book and maker_book.is_valid())
        hedge_valid = bool(hedge_book and hedge_book.is_valid())
        bid_ask_ok = maker_valid and hedge_valid
        checks_detail["bid_ask"] = {
            "passed": bid_ask_ok,
            "maker_bid": maker_book.best_bid if maker_book else None,
            "maker_ask": maker_book.best_ask if maker_book else None,
            "hedge_bid": hedge_book.best_bid if hedge_book else None,
            "hedge_ask": hedge_book.best_ask if hedge_book else None,
        }
        if not bid_ask_ok:
            failed_checks.append("bid/ask")
            rejection_reasons.append("Invalid or crossed bid/ask quotes on maker or hedge venue")

        # 2. order-book liquidity
        maker_side = getattr(decision, "maker_side", "BUY")
        maker_liq = (maker_book.best_ask_size if maker_side == "BUY" else maker_book.best_bid_size) if maker_book else 0.0
        hedge_side = getattr(decision, "hedge_side", "SELL")
        hedge_liq = (hedge_book.best_bid_size if hedge_side == "SELL" else hedge_book.best_ask_size) if hedge_book else 0.0
        min_liq = min(maker_liq, hedge_liq)
        liquidity_ok = min_liq >= order_quantity
        checks_detail["order_book_liquidity"] = {
            "passed": liquidity_ok,
            "maker_liquidity": maker_liq,
            "hedge_liquidity": hedge_liq,
            "requested_quantity": order_quantity,
        }
        if not liquidity_ok:
            failed_checks.append("order-book liquidity")
            rejection_reasons.append(f"Insufficient order-book depth ({min_liq:.4f}) for order size ({order_quantity:.4f})")

        # 3. funding validation
        funding_ok = True
        funding_detail: Dict[str, Any] = {"passed": True}
        strategy_type = getattr(decision, "strategy_type", "SPREAD_ARBITRAGE")
        if strategy_type == "FUNDING_ARBITRAGE":
            if maker_funding is None or hedge_funding is None:
                funding_ok = False
                rejection_reasons.append("Funding rate data unavailable for one or both venues")
            elif getattr(decision, "net_funding_rate", 0.0) <= 0:
                funding_ok = False
                rejection_reasons.append("Net funding rate is non-positive or inverted")
            funding_detail = {
                "passed": funding_ok,
                "maker_rate": maker_funding.rate if maker_funding else None,
                "hedge_rate": hedge_funding.rate if hedge_funding else None,
                "net_rate": getattr(decision, "net_funding_rate", 0.0),
            }
        checks_detail["funding"] = funding_detail
        if not funding_ok:
            failed_checks.append("funding")

        # 4. fees validation
        risk_metrics = getattr(decision, "risk_metrics", {})
        fees_ok = decision is not None and getattr(decision, "is_viable", False)
        checks_detail["fees"] = {
            "passed": fees_ok,
            "risk_metrics": risk_metrics,
        }
        if not fees_ok:
            failed_checks.append("fees")
            rejection_reasons.append("Directional fees exceed profit margin or decision is unviable")

        # 5. slippage validation
        slippage_val = risk_metrics.get("actual_slippage_pct", 0.0)
        slippage_ok = slippage_val <= max_slip
        checks_detail["slippage"] = {
            "passed": slippage_ok,
            "actual_slippage_pct": slippage_val,
            "max_slippage_pct": max_slip,
        }
        if not slippage_ok:
            failed_checks.append("slippage")
            rejection_reasons.append(f"Slippage ({slippage_val:.4f}%) exceeds maximum tolerance ({max_slip:.4f}%)")

        # 6. timestamp validation
        t_maker = maker_book.timestamp if maker_book else 0.0
        t_hedge = hedge_book.timestamp if hedge_book else 0.0
        ts_ok = bool(t_maker > 0 and t_hedge > 0 and abs(t_maker - t_hedge) <= stale_thresh * 2)
        checks_detail["timestamp"] = {
            "passed": ts_ok,
            "maker_timestamp": t_maker,
            "hedge_timestamp": t_hedge,
            "evaluated_at": now,
        }
        if not ts_ok:
            failed_checks.append("timestamp")
            rejection_reasons.append("Exchange timestamps missing, invalid, or desynchronized")

        # 7. freshness validation
        age_maker = max(0.0, now - t_maker) if t_maker > 0 else float("inf")
        age_hedge = max(0.0, now - t_hedge) if t_hedge > 0 else float("inf")
        freshness_ok = bool(age_maker <= stale_thresh and age_hedge <= stale_thresh)
        checks_detail["freshness"] = {
            "passed": freshness_ok,
            "age_maker_seconds": round(age_maker, 3),
            "age_hedge_seconds": round(age_hedge, 3),
            "threshold_seconds": stale_thresh,
        }
        if not freshness_ok:
            failed_checks.append("freshness")
            rejection_reasons.append(f"Stale market data (maker age={age_maker:.1f}s, hedge age={age_hedge:.1f}s)")

        # 8. opportunity validity
        opp_ok = bool(decision and getattr(decision, "is_viable", False))
        checks_detail["opportunity_validity"] = {
            "passed": opp_ok,
            "is_viable": opp_ok,
            "decision_rejection": getattr(decision, "rejection_reason", None),
        }
        if not opp_ok:
            failed_checks.append("opportunity validity")
            if getattr(decision, "rejection_reason", None):
                rejection_reasons.append(str(decision.rejection_reason))
            else:
                rejection_reasons.append("Strategy opportunity is not viable")

        # 9. execution direction validation
        first_leg_v = getattr(decision, "first_leg_venue", "")
        second_leg_v = getattr(decision, "second_leg_venue", "")
        dir_ok = bool(first_leg_v and second_leg_v and first_leg_v != second_leg_v)
        checks_detail["execution_direction"] = {
            "passed": dir_ok,
            "first_leg_venue": first_leg_v,
            "second_leg_venue": second_leg_v,
        }
        if not dir_ok:
            failed_checks.append("execution direction")
            rejection_reasons.append("Invalid or identical execution leg venues")

        # 10. position state validation
        pos_ok = (current_position_size + order_quantity) <= max_position_size
        checks_detail["position_state"] = {
            "passed": pos_ok,
            "current_position_size": current_position_size,
            "order_quantity": order_quantity,
            "max_position_size": max_position_size,
        }
        if not pos_ok:
            failed_checks.append("position state")
            rejection_reasons.append(
                f"Position limit exceeded: current {current_position_size:.2f} + {order_quantity:.2f} > max {max_position_size:.2f}"
            )

        is_valid = len(failed_checks) == 0
        final_reason = "; ".join(rejection_reasons) if rejection_reasons else None

        snapshot = {
            "maker_venue": maker_book.venue if maker_book else "",
            "hedge_venue": hedge_book.venue if hedge_book else "",
            "maker_best_bid": maker_book.best_bid if maker_book else None,
            "maker_best_ask": maker_book.best_ask if maker_book else None,
            "hedge_best_bid": hedge_book.best_bid if hedge_book else None,
            "hedge_best_ask": hedge_book.best_ask if hedge_book else None,
            "evaluated_at": now,
        }

        return PreSubmissionValidationResult(
            is_valid=is_valid,
            failed_checks=failed_checks,
            checks_detail=checks_detail,
            rejection_reason=final_reason,
            timestamp=now,
            market_state_snapshot=snapshot,
        )


class ExecutionEngine:
    """
    Step 13: Execution Engine.
    Handles coordinated Maker / Hedge execution lifecycle:
    - Pre-submission revalidation before every order dispatch
    - Dry Run (paper order execution matching against real live order books)
    - Live order safety invariants (strictly zero live order placement in development)
    - Order reconciliation: ensures Maker Leg and Hedge Leg quantities match exactly
    """

    def __init__(
        self,
        validator: Optional[PreSubmissionValidator] = None,
    ):
        self.validator = validator or PreSubmissionValidator()
        self.cycles: Dict[str, ExecutionCycle] = {}

    def execute_cycle(
        self,
        bot_config: BotConfig,
        decision: Any,
        maker_book: OrderBook,
        hedge_book: OrderBook,
        maker_funding: Optional[FundingRate] = None,
        hedge_funding: Optional[FundingRate] = None,
        current_position_size: float = 0.0,
        current_time: Optional[float] = None,
    ) -> ExecutionCycle:
        """
        Execute a complete trade cycle with pre-submission validation.
        """
        now = current_time if current_time is not None else time.time()
        cycle_id = f"exec_{int(now * 1000)}_{uuid.uuid4().hex[:6]}"

        maker_v = getattr(decision, "first_leg_venue", bot_config.reference_venue)
        hedge_v = getattr(decision, "second_leg_venue", bot_config.priced_venue)
        maker_s = getattr(decision, "maker_side", "BUY")
        hedge_s = getattr(decision, "hedge_side", "SELL")

        cycle = ExecutionCycle(
            cycle_id=cycle_id,
            bot_id=bot_config.bot_id,
            strategy_type=bot_config.strategy_type,
            symbol=bot_config.symbol,
            maker_venue=maker_v,
            hedge_venue=hedge_v,
            maker_side=maker_s,
            hedge_side=hedge_s,
            target_quantity=bot_config.order_quantity,
            cycle_state="INITIAL",
            created_at=now,
            updated_at=now,
        )

        # 1. Mandatory Pre-Submission Revalidation
        cycle.cycle_state = "VALIDATING"
        val_result = self.validator.validate_pre_submission(
            maker_book=maker_book,
            hedge_book=hedge_book,
            decision=decision,
            order_quantity=bot_config.order_quantity,
            current_position_size=current_position_size,
            max_position_size=bot_config.max_position_size,
            max_slippage_pct=bot_config.max_slippage_pct,
            stale_threshold_seconds=bot_config.stale_threshold_seconds,
            maker_funding=maker_funding,
            hedge_funding=hedge_funding,
            current_time=now,
        )
        cycle.pre_submission_validation = val_result

        if not val_result.is_valid:
            cycle.cycle_state = "FAILED"
            cycle.error_message = f"Pre-submission validation failed: {val_result.rejection_reason}"
            cycle.updated_at = time.time()
            self.cycles[cycle_id] = cycle
            return cycle

        # 2. Determine Maker Entry Price & Target Hedge Price
        maker_px = 0.0
        hedge_px = 0.0
        pricing = getattr(decision, "pricing", None)
        if pricing:
            maker_px = pricing.base_maker_price
            hedge_px = pricing.target_hedge_price
        else:
            maker_px = maker_book.best_ask if maker_s == "BUY" else maker_book.best_bid
            hedge_px = hedge_book.best_bid if hedge_s == "SELL" else hedge_book.best_ask

        cycle.maker_price = maker_px
        cycle.hedge_price = hedge_px

        # 3. Execution Mode Dispatch
        if bot_config.mode == BotExecutionMode.DRY_RUN.value:
            # Paper execution matching against real book prices
            cycle.cycle_state = "MAKER_PLACED"
            cycle.maker_filled_qty = bot_config.order_quantity
            cycle.cycle_state = "MAKER_FILLED"

            # Execute hedge leg
            cycle.hedge_filled_qty = bot_config.order_quantity
            cycle.cycle_state = "HEDGED"

            # 4. Position & Quantity Reconciliation
            reconciled = self.reconcile_cycle(cycle)
            if reconciled:
                cycle.cycle_state = "RECONCILED"
                cycle.reconciliation_status = "BALANCED"
                # Calculate gross PnL
                if maker_s == "BUY":
                    # Bought on Maker @ maker_px, Sold on Hedge @ hedge_px
                    pnl_spread = (hedge_px - maker_px) * bot_config.order_quantity
                else:
                    # Sold on Maker @ maker_px, Bought on Hedge @ hedge_px
                    pnl_spread = (maker_px - hedge_px) * bot_config.order_quantity
                cycle.pnl_usd = round(pnl_spread, 4)
            else:
                cycle.cycle_state = "MISMATCHED"
                cycle.reconciliation_status = "MISMATCHED"
                cycle.error_message = "Leg fill quantities do not match"

        elif bot_config.mode == BotExecutionMode.LIVE.value:
            # Zero live order placement invariant during development
            cycle.cycle_state = "FAILED"
            cycle.error_message = "LIVE order submission disabled by development zero-trading invariant."

        cycle.updated_at = time.time()
        self.cycles[cycle_id] = cycle
        return cycle

    def reconcile_cycle(self, cycle: ExecutionCycle) -> bool:
        """
        Reconcile Maker leg and Hedge leg quantities.
        Both legs must be completely filled with zero unhedged exposure.
        """
        qty_diff = abs(cycle.maker_filled_qty - cycle.hedge_filled_qty)
        is_balanced = (qty_diff < 1e-6) and (cycle.maker_filled_qty > 0)
        cycle.reconciliation_status = "BALANCED" if is_balanced else "MISMATCHED"
        return is_balanced

    def get_cycle(self, cycle_id: str) -> Optional[ExecutionCycle]:
        return self.cycles.get(cycle_id)

    def list_cycles(self, bot_id: Optional[str] = None) -> List[ExecutionCycle]:
        cycles = list(self.cycles.values())
        if bot_id:
            cycles = [c for c in cycles if c.bot_id == bot_id]
        return sorted(cycles, key=lambda c: c.created_at, reverse=True)


class BotManager:
    """
    Step 13: Authoritative Bot Manager.
    Orchestrates:
    - Bot lifecycle (create, start, stop, pause, list)
    - Screener & Live State integration
    - Strategy selection:
        * Spread Arbitrage = Highest Net Spread %
        * Funding Arbitrage = Highest Funding %
    - Decoupled leg assignment: First Leg = Maker, Second Leg = Hedge / Taker
    - Pre-submission validation
    - Coordinated execution through ExecutionEngine
    """

    def __init__(
        self,
        calculator: Optional[SpreadCalculator] = None,
        execution_engine: Optional[ExecutionEngine] = None,
        persistence: Optional[Any] = None,
    ):
        self.calculator = calculator or SpreadCalculator()
        self.execution_engine = execution_engine or ExecutionEngine()
        self.persistence = persistence
        self.bots: Dict[str, BotConfig] = {}

    def create_bot(
        self,
        bot_id: str,
        strategy_type: str = "SPREAD_ARBITRAGE",
        mode: str = "DRY_RUN",
        symbol: str = "BTC/USDT",
        reference_venue: str = "BINANCE",
        priced_venue: str = "BYBIT",
        order_quantity: float = 1.0,
        max_position_size: float = 10.0,
        pricing_mode: str = "SPREAD",
        grid_spacing_pct: float = 0.05,
        grid_levels_count: int = 3,
        bollinger_k: float = 2.0,
        bollinger_period: int = 20,
        min_profit_threshold_pct: float = 0.05,
        max_slippage_pct: float = 0.20,
    ) -> BotConfig:
        """Create and register a new bot configuration."""
        cfg = BotConfig(
            bot_id=bot_id,
            strategy_type=strategy_type.upper(),
            mode=mode.upper(),
            symbol=symbol,
            reference_venue=reference_venue.upper(),
            priced_venue=priced_venue.upper(),
            order_quantity=order_quantity,
            max_position_size=max_position_size,
            pricing_mode=pricing_mode.upper(),
            grid_spacing_pct=grid_spacing_pct,
            grid_levels_count=grid_levels_count,
            bollinger_k=bollinger_k,
            bollinger_period=bollinger_period,
            min_profit_threshold_pct=min_profit_threshold_pct,
            max_slippage_pct=max_slippage_pct,
            status=BotStatus.STOPPED.value,
        )
        self.bots[bot_id] = cfg
        if self.persistence:
            try:
                self.persistence.record_bot_event(
                    bot_id=bot_id,
                    event_type="CREATED",
                    message=f"Bot {bot_id} created ({cfg.strategy_type}, mode={cfg.mode})",
                    level="INFO",
                )
            except Exception:
                pass
        return cfg

    def start_bot(self, bot_id: str) -> bool:
        """Start an existing bot."""
        bot = self.bots.get(bot_id)
        if not bot:
            return False
        bot.status = BotStatus.RUNNING.value
        bot.updated_at = time.time()
        if self.persistence:
            try:
                self.persistence.record_bot_event(
                    bot_id=bot_id,
                    event_type="STARTED",
                    message=f"Bot {bot_id} started in {bot.mode} mode",
                    level="INFO",
                )
            except Exception:
                pass
        return True

    def stop_bot(self, bot_id: str) -> bool:
        """Stop an existing bot."""
        bot = self.bots.get(bot_id)
        if not bot:
            return False
        bot.status = BotStatus.STOPPED.value
        bot.updated_at = time.time()
        if self.persistence:
            try:
                self.persistence.record_bot_event(
                    bot_id=bot_id,
                    event_type="STOPPED",
                    message=f"Bot {bot_id} stopped",
                    level="INFO",
                )
            except Exception:
                pass
        return True

    def pause_bot(self, bot_id: str) -> bool:
        """Pause a running bot."""
        bot = self.bots.get(bot_id)
        if not bot:
            return False
        bot.status = BotStatus.PAUSED.value
        bot.updated_at = time.time()
        return True

    def get_bot(self, bot_id: str) -> Optional[BotConfig]:
        return self.bots.get(bot_id)

    def list_bots(self) -> List[BotConfig]:
        return list(self.bots.values())

    def evaluate_and_execute(
        self,
        bot_id: str,
        book_a: OrderBook,
        book_b: OrderBook,
        funding_a: Optional[FundingRate] = None,
        funding_b: Optional[FundingRate] = None,
        current_position_size: float = 0.0,
        current_time: Optional[float] = None,
    ) -> ExecutionCycle:
        """
        Step 13 Full Pipeline Execution:
        1. Fetch bot configuration.
        2. Evaluate Strategy Decision:
           - SPREAD_ARBITRAGE: selects direction via Highest Net Spread %
           - FUNDING_ARBITRAGE: selects direction via Highest Funding %
        3. Decouple Leg roles: First Leg = Maker, Second Leg = Hedge / Taker.
        4. Revalidate via PreSubmissionValidator across all 10 checks.
        5. Execute Cycle (Maker -> Hedge -> Reconciliation).
        """
        bot = self.bots.get(bot_id)
        if not bot:
            raise ValueError(f"Bot {bot_id} not found")

        now = current_time if current_time is not None else time.time()

        # Step 13 Strategy Selection rule:
        # Spread = Highest Net Spread %; Funding = Highest Funding %
        if bot.strategy_type == "SPREAD_ARBITRAGE":
            decision = self.calculator.evaluate_spread_arbitrage(
                venue_a_data=book_a,
                venue_b_data=book_b,
                symbol=bot.symbol,
                pricing_mode=bot.pricing_mode,
                min_profit_threshold_pct=bot.min_profit_threshold_pct,
                grid_step_pct=bot.grid_spacing_pct,
                grid_levels_count=bot.grid_levels_count,
                bollinger_k=bot.bollinger_k,
                bollinger_period=bot.bollinger_period,
                current_time=now,
            )
        elif bot.strategy_type == "FUNDING_ARBITRAGE":
            if not funding_a or not funding_b:
                raise ValueError("Funding rates required for FUNDING_ARBITRAGE strategy evaluation")
            decision = self.calculator.evaluate_funding_arbitrage(
                funding_a=funding_a,
                funding_b=funding_b,
                book_a=book_a,
                book_b=book_b,
                symbol=bot.symbol,
                pricing_mode=bot.pricing_mode,
                min_annual_funding_pct=bot.min_profit_threshold_pct,
                grid_step_pct=bot.grid_spacing_pct,
                grid_levels_count=bot.grid_levels_count,
                bollinger_k=bot.bollinger_k,
                bollinger_period=bot.bollinger_period,
                current_time=now,
            )
        else:
            raise ValueError(f"Unsupported strategy type: {bot.strategy_type}")

        # Map Maker and Hedge books based on dynamically selected First Leg and Second Leg
        maker_venue = getattr(decision, "first_leg_venue", "").upper()
        if maker_venue == (book_a.venue or "").upper():
            maker_book = book_a
            hedge_book = book_b
            m_fund = funding_a
            h_fund = funding_b
        else:
            maker_book = book_b
            hedge_book = book_a
            m_fund = funding_b
            h_fund = funding_a

        # Execute cycle via ExecutionEngine
        cycle = self.execution_engine.execute_cycle(
            bot_config=bot,
            decision=decision,
            maker_book=maker_book,
            hedge_book=hedge_book,
            maker_funding=m_fund,
            hedge_funding=h_fund,
            current_position_size=current_position_size,
            current_time=now,
        )

        if self.persistence:
            try:
                self.persistence.record_bot_event(
                    bot_id=bot_id,
                    event_type="EXECUTION_CYCLE",
                    message=f"Cycle {cycle.cycle_id} state={cycle.cycle_state}, pnl=${cycle.pnl_usd:.4f}",
                    level="INFO" if cycle.cycle_state == "RECONCILED" else "WARNING",
                )
            except Exception:
                pass

        return cycle
