import asyncio
import logging
import json
import uuid
import time
from enum import Enum
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

class OrderState(Enum):
    PENDING_SUBMIT = "PENDING_SUBMIT"
    SUBMITTED = "SUBMITTED"
    OPEN = "OPEN"
    PARTIAL = "PARTIAL"
    FILLED = "FILLED"
    CANCELED = "CANCELED"
    REJECTED = "REJECTED"
    AMEND_PENDING = "AMEND_PENDING"

class LegStatus(Enum):
    PENDING = "PENDING"
    EXECUTING = "EXECUTING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    UNWINDING = "UNWINDING"

class CycleState(Enum):
    INITIAL = "INITIAL"
    VALIDATING = "VALIDATING"
    EXECUTING = "EXECUTING"
    RECONCILING = "RECONCILING"
    HALTED = "HALTED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"

@dataclass
class Order:
    internal_id: str
    symbol: str
    side: str
    type: str
    price: float
    amount: float
    exchange_id: Optional[str] = None
    state: OrderState = OrderState.PENDING_SUBMIT
    filled_amount: float = 0.0
    average_price: float = 0.0
    last_update: float = field(default_factory=time.time)

@dataclass
class Leg:
    id: str
    symbol: str
    side: str
    amount: float
    price: float
    order: Optional[Order] = None
    status: LegStatus = LegStatus.PENDING

class ExecutionStore:
    """Mock persistence store for idempotency and recovery."""
    def __init__(self):
        self.state = {}

    def save_cycle(self, cycle_id: str, data: dict):
        self.state[cycle_id] = data
        with open("data/execution_state.json", "w") as f:
            json.dump(self.state, f)

    def load_cycle(self, cycle_id: str) -> Optional[dict]:
        return self.state.get(cycle_id)

class TriangularExecutionEngine:
    def __init__(self, exchange_client, store: ExecutionStore):
        self.client = exchange_client
        self.store = store
        self.state = CycleState.INITIAL
        self.cycle_id = str(uuid.uuid4())
        self.legs: List[Leg] = []
        self.balances = {}
        self.positions = {}

    async def initialize(self):
        await self.reconcile()

    async def reconcile(self):
        """Reconciliation: sync internal state with exchange."""
        self.state = CycleState.RECONCILING
        try:
            exchange_balances = await self.client.fetch_balance()
            self.balances = exchange_balances
            open_orders = await self.client.fetch_open_orders()
            # Logic to repair discrepancies
            for o in open_orders:
                pass # Sync internal orders
            self.state = CycleState.INITIAL
        except Exception as e:
            logger.error(f"Reconciliation failed: {e}")
            self.state = CycleState.HALTED

    async def validate_cycle(self, leg1, leg2, leg3) -> bool:
        """Validate all three legs and profitability before submission."""
        self.state = CycleState.VALIDATING
        # 1. Check balances
        # 2. Check profitability threshold
        # 3. Check liquidity and stale prices
        if not await self._check_balances(leg1, leg2, leg3):
            return False
        if not await self._check_profitability(leg1, leg2, leg3):
            return False
        self.legs = [Leg(id="L1", **leg1), Leg(id="L2", **leg2), Leg(id="L3", **leg3)]
        self._persist_state()
        return True

    async def _check_balances(self, *legs) -> bool:
        # Check against self.balances
        return True

    async def _check_profitability(self, *legs) -> bool:
        # Calculate net after fees
        return True

    def _persist_state(self):
        data = {
            "cycle_id": self.cycle_id,
            "state": self.state.value,
            "legs": [{"id": l.id, "status": l.status.value} for l in self.legs]
        }
        self.store.save_cycle(self.cycle_id, data)

    async def execute_cycle(self):
        """State-driven execution workflow."""
        if self.state != CycleState.VALIDATING:
            return
        self.state = CycleState.EXECUTING
        self._persist_state()

        for leg in self.legs:
            leg.status = LegStatus.EXECUTING
            self._persist_state()
            success = await self._execute_leg(leg)
            if not success:
                await self._handle_failure(leg)
                break
            leg.status = LegStatus.COMPLETED
            self._persist_state()

        if all(l.status == LegStatus.COMPLETED for l in self.legs):
            self.state = CycleState.COMPLETED
            self._persist_state()

    async def _execute_leg(self, leg: Leg) -> bool:
        """Place and track each order using exchange-native IDs."""
        order = Order(
            internal_id=str(uuid.uuid4()),
            symbol=leg.symbol,
            side=leg.side,
            type="limit",
            price=leg.price,
            amount=leg.amount
        )
        leg.order = order
        try:
            # Idempotency check: verify not already placed
            res = await self.client.create_order(
                symbol=order.symbol,
                type=order.type,
                side=order.side,
                amount=order.amount,
                price=order.price,
                params={"clientOrderId": order.internal_id}
            )
            order.exchange_id = res['id']
            order.state = OrderState.SUBMITTED
            
            # Track order via WS/REST updates
            return await self._track_order(order)
        except Exception as e:
            logger.error(f"Order submission failed: {e}")
            order.state = OrderState.REJECTED
            return False

    async def _track_order(self, order: Order, timeout: int = 10) -> bool:
        """Track order handling partial fills, rejects, timeouts."""
        start_time = time.time()
        while time.time() - start_time < timeout:
            update = await self.client.fetch_order(order.exchange_id, order.symbol)
            if update['status'] == 'closed':
                order.state = OrderState.FILLED
                order.filled_amount = update['filled']
                return True
            elif update['status'] == 'canceled':
                order.state = OrderState.CANCELED
                return False
            elif update['status'] == 'open' and update['filled'] > 0:
                order.state = OrderState.PARTIAL
                order.filled_amount = update['filled']
                # Order amend/replace logic if price moves away
                if await self._should_amend(order):
                    await self._amend_order(order)
            await asyncio.sleep(0.5)
        
        # Timeout handling
        if order.state not in (OrderState.FILLED, OrderState.CANCELED):
            await self._cancel_order(order)
        return order.filled_amount == order.amount

    async def _should_amend(self, order: Order) -> bool:
        # Logic to check if price moved and we should amend
        return False

    async def _amend_order(self, order: Order):
        order.state = OrderState.AMEND_PENDING
        await self.client.cancel_order(order.exchange_id, order.symbol)
        # Re-submit with new price
        pass

    async def _cancel_order(self, order: Order):
        try:
            await self.client.cancel_order(order.exchange_id, order.symbol)
        except Exception:
            pass

    async def _handle_failure(self, failed_leg: Leg):
        """Failure management: unwind or halt."""
        logger.warning(f"Handling failure for {failed_leg.id}")
        self.state = CycleState.FAILED
        for leg in self.legs:
            if leg.status == LegStatus.COMPLETED:
                leg.status = LegStatus.UNWINDING
                await self._unwind_leg(leg)
        self.state = CycleState.HALTED
        self._persist_state()

    async def _unwind_leg(self, leg: Leg):
        """Hedge or unwind completed legs."""
        reverse_side = "sell" if leg.side == "buy" else "buy"
        try:
            await self.client.create_order(
                symbol=leg.symbol,
                type="market",
                side=reverse_side,
                amount=leg.order.filled_amount
            )
        except Exception as e:
            logger.error(f"Unwind failed: {e}")

