from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional, Tuple, Dict, Any
import time


class DataSourceType(str, Enum):
    CCXT_PRO = "CCXT_PRO"
    NATIVE_WS = "NATIVE_WS"
    REST = "REST"
    AUTO = "AUTO"


class ConnectionStatus(str, Enum):
    DISCONNECTED = "DISCONNECTED"
    CONNECTING = "CONNECTING"
    CONNECTED = "CONNECTED"
    RECONNECTING = "RECONNECTING"
    DEGRADED = "DEGRADED"
    FAILED = "FAILED"
    UNAVAILABLE = "UNAVAILABLE"
    STALE = "STALE"
    WAITING_FOR_DATA = "WAITING_FOR_DATA"
    INVALID = "INVALID"


class FreshnessStatus(str, Enum):
    FRESH = "FRESH"
    STALE = "STALE"
    WAITING_FOR_DATA = "WAITING_FOR_DATA"
    UNAVAILABLE = "UNAVAILABLE"
    INVALID = "INVALID"


class FundingDirection(str, Enum):
    LONG_PAYS_SHORT = "LONG_PAYS_SHORT"
    SHORT_PAYS_LONG = "SHORT_PAYS_LONG"
    NEUTRAL = "NEUTRAL"


@dataclass
class OrderBookEntry:
    price: float
    size: float

    @property
    def value(self) -> float:
        return self.price * self.size


@dataclass
class OrderBook:
    symbol: str
    venue: str
    bids: List[OrderBookEntry] = field(default_factory=list)  # Sorted descending by price
    asks: List[OrderBookEntry] = field(default_factory=list)  # Sorted ascending by price
    timestamp: float = field(default_factory=time.time)
    source: str = "UNKNOWN"
    sequence: Optional[int] = None
    last_update_id: Optional[int] = None
    freshness: str = FreshnessStatus.FRESH.value
    connection_status: str = ConnectionStatus.CONNECTED.value
    is_ready: bool = False
    is_synchronized: bool = False

    @property
    def best_bid(self) -> Optional[float]:
        return self.bids[0].price if self.bids else None

    @property
    def best_bid_size(self) -> Optional[float]:
        return self.bids[0].size if self.bids else None

    @property
    def best_ask(self) -> Optional[float]:
        return self.asks[0].price if self.asks else None

    @property
    def best_ask_size(self) -> Optional[float]:
        return self.asks[0].size if self.asks else None

    @property
    def mid_price(self) -> Optional[float]:
        if self.best_bid is not None and self.best_ask is not None:
            return (self.best_bid + self.best_ask) / 2.0
        return None

    @property
    def internal_spread(self) -> Optional[float]:
        if self.best_bid is not None and self.best_ask is not None:
            return self.best_ask - self.best_bid
        return None

    @property
    def internal_spread_pct(self) -> Optional[float]:
        if self.best_bid and self.best_ask and self.best_bid > 0:
            return ((self.best_ask - self.best_bid) / self.best_bid) * 100.0
        return None

    @property
    def internal_spread_bps(self) -> Optional[float]:
        pct = self.internal_spread_pct
        return pct * 100.0 if pct is not None else None

    def is_valid(self) -> bool:
        """Validate order book correctness (non-empty, positive prices, uncrossed)."""
        if not self.bids or not self.asks:
            return False
        if self.best_bid is None or self.best_ask is None:
            return False
        if self.best_bid <= 0 or self.best_ask <= 0:
            return False
        if self.best_ask < self.best_bid:
            return False  # Crossed market is invalid
        return True

    def get_vwap(self, side: str, target_size: float) -> Tuple[float, float, bool]:
        """
        Calculate Volume Weighted Average Price (VWAP) for a given target order size.

        :param side: 'buy' (consumes asks) or 'sell' (consumes bids)
        :param target_size: Target asset quantity to fill
        :return: (vwap_price, total_cost_or_proceeds, is_fully_filled)
        """
        levels = self.asks if side.lower() == 'buy' else self.bids
        if not levels or target_size <= 0:
            return 0.0, 0.0, False

        remaining = target_size
        total_value = 0.0
        total_filled = 0.0

        for level in levels:
            fill_qty = min(remaining, level.size)
            total_value += fill_qty * level.price
            total_filled += fill_qty
            remaining -= fill_qty
            if remaining <= 1e-9:
                break

        if total_filled > 0:
            vwap = total_value / total_filled
            is_filled = remaining <= 1e-9
            return vwap, total_value, is_filled
        return 0.0, 0.0, False


@dataclass
class Ticker:
    """Normalized real-time ticker quote across all connectors."""
    symbol: str
    venue: str
    bid: float
    ask: float
    last: float
    volume_24h: float = 0.0
    high_24h: float = 0.0
    low_24h: float = 0.0
    change_24h_pct: float = 0.0
    bid_size: float = 0.0
    ask_size: float = 0.0
    timestamp: float = field(default_factory=time.time)
    source: str = "UNKNOWN"
    sequence: Optional[int] = None
    freshness: str = FreshnessStatus.FRESH.value
    connection_status: str = ConnectionStatus.CONNECTED.value
    is_ready: bool = False
    is_synchronized: bool = False

    @property
    def mid(self) -> float:
        if self.bid > 0 and self.ask > 0:
            return (self.bid + self.ask) / 2.0
        return self.last

    @property
    def spread(self) -> float:
        return max(0.0, self.ask - self.bid)

    @property
    def spread_pct(self) -> float:
        if self.bid > 0:
            return (self.spread / self.bid) * 100.0
        return 0.0

    def is_valid(self) -> bool:
        """Validate ticker prices are positive and non-crossed."""
        if self.bid <= 0 or self.ask <= 0:
            return False
        if self.ask < self.bid:
            return False
        return True


@dataclass
class Trade:
    """Normalized public market trade."""
    id: str
    symbol: str
    venue: str
    side: str  # 'buy' or 'sell'
    price: float
    amount: float
    cost: float = 0.0
    timestamp: float = field(default_factory=time.time)
    source: str = "UNKNOWN"

    def __post_init__(self):
        if self.cost == 0.0 and self.price > 0 and self.amount > 0:
            self.cost = self.price * self.amount


@dataclass
class OHLCV:
    """Normalized OHLCV candlestick bar."""
    symbol: str
    venue: str
    timeframe: str
    timestamp: float
    open: float
    high: float
    low: float
    close: float
    volume: float
    source: str = "UNKNOWN"


@dataclass
class FundingRate:
    """Normalized funding rate for perpetual swaps across exchanges."""
    symbol: str
    venue: str
    rate: float  # e.g. 0.0001 = 0.01%
    annual_pct: float = 0.0  # rate * 3 * 365 * 100
    next_funding_time: float = 0.0
    interval_hours: int = 8
    predicted_rate: Optional[float] = None
    funding_direction: str = FundingDirection.NEUTRAL.value
    freshness: str = FreshnessStatus.FRESH.value
    connection_status: str = ConnectionStatus.CONNECTED.value
    timestamp: float = field(default_factory=time.time)
    source: str = "UNKNOWN"

    def __post_init__(self):
        if self.annual_pct == 0.0 and self.rate != 0.0:
            periods_per_day = 24 / (self.interval_hours or 8)
            self.annual_pct = self.rate * periods_per_day * 365.0 * 100.0
        if self.rate > 0:
            self.funding_direction = FundingDirection.LONG_PAYS_SHORT.value
        elif self.rate < 0:
            self.funding_direction = FundingDirection.SHORT_PAYS_LONG.value
        else:
            self.funding_direction = FundingDirection.NEUTRAL.value


@dataclass
class NormalizedMarketState:
    """
    Unified normalized market state per venue & symbol.
    Fulfills Step 2 schema:
    Venue, Symbol, Best Bid, Best Ask, Bid Size, Ask Size,
    Funding Rate, Funding Direction, 24H Volume, Timestamp,
    Sequence, Freshness, Connection Status.
    """
    venue: str
    symbol: str
    best_bid: float = 0.0
    best_ask: float = 0.0
    bid_size: float = 0.0
    ask_size: float = 0.0
    funding_rate: Optional[float] = None
    funding_direction: str = FundingDirection.NEUTRAL.value
    volume_24h: float = 0.0
    timestamp: float = field(default_factory=time.time)
    sequence: Optional[int] = None
    freshness: str = FreshnessStatus.WAITING_FOR_DATA.value
    connection_status: str = ConnectionStatus.CONNECTING.value

    # Startup & Execution Readiness Gating
    has_rest_snapshot: bool = False
    ws_synchronized: bool = False
    is_execution_ready: bool = False

    # Statistical Reference Metrics (from REST 1-minute historical candles)
    historical_deviation_mean: float = 0.0
    historical_deviation_sd: float = 0.0
    bollinger_upper: float = 0.0
    bollinger_lower: float = 0.0
    bollinger_bandwidth: float = 0.0

    # Diagnostic counters
    last_rest_snapshot_time: float = 0.0
    last_ws_update_time: float = 0.0
    missed_sequence_count: int = 0

    @property
    def mid_price(self) -> float:
        if self.best_bid > 0 and self.best_ask > 0:
            return (self.best_bid + self.best_ask) / 2.0
        return 0.0

    @property
    def spread(self) -> float:
        return max(0.0, self.best_ask - self.best_bid)

    @property
    def spread_pct(self) -> float:
        if self.best_bid > 0:
            return (self.spread / self.best_bid) * 100.0
        return 0.0

    def evaluate_readiness(self, stale_threshold_seconds: float = 10.0) -> bool:
        """
        Symbol is NOT execution-ready until:
        - REST snapshot exists
        - WebSocket is synchronized
        - Bid/Ask are valid (bid > 0, ask > 0, ask >= bid)
        - Timestamp/freshness passes
        - Connection status is CONNECTED
        """
        now = time.time()
        is_fresh = (now - self.timestamp) <= stale_threshold_seconds

        valid_prices = (
            self.best_bid > 0
            and self.best_ask > 0
            and self.best_ask >= self.best_bid
        )

        valid_connection = self.connection_status in (
            ConnectionStatus.CONNECTED.value,
            "CONNECTED"
        )

        if not is_fresh:
            self.freshness = FreshnessStatus.STALE.value
        elif not valid_prices:
            self.freshness = FreshnessStatus.INVALID.value
        elif not (self.has_rest_snapshot and self.ws_synchronized):
            self.freshness = FreshnessStatus.WAITING_FOR_DATA.value
        else:
            self.freshness = FreshnessStatus.FRESH.value

        self.is_execution_ready = bool(
            self.has_rest_snapshot
            and self.ws_synchronized
            and valid_prices
            and is_fresh
            and valid_connection
        )
        return self.is_execution_ready

    def to_dict(self) -> Dict[str, Any]:
        return {
            "venue": self.venue,
            "symbol": self.symbol,
            "best_bid": self.best_bid,
            "best_ask": self.best_ask,
            "bid_size": self.bid_size,
            "ask_size": self.ask_size,
            "mid_price": self.mid_price,
            "spread": self.spread,
            "spread_pct": self.spread_pct,
            "funding_rate": self.funding_rate,
            "funding_direction": self.funding_direction,
            "volume_24h": self.volume_24h,
            "timestamp": self.timestamp,
            "sequence": self.sequence,
            "freshness": self.freshness,
            "connection_status": self.connection_status,
            "has_rest_snapshot": self.has_rest_snapshot,
            "ws_synchronized": self.ws_synchronized,
            "is_execution_ready": self.is_execution_ready,
            "historical_deviation_mean": self.historical_deviation_mean,
            "historical_deviation_sd": self.historical_deviation_sd,
            "bollinger_upper": self.bollinger_upper,
            "bollinger_lower": self.bollinger_lower,
            "bollinger_bandwidth": self.bollinger_bandwidth,
        }


@dataclass
class ConnectorHealth:
    """Connector health & latency monitoring."""
    venue: str
    source: str
    status: ConnectionStatus = ConnectionStatus.DISCONNECTED
    latency_ms: float = 0.0
    message_count: int = 0
    error_count: int = 0
    last_seen: float = 0.0
    error_message: Optional[str] = None


@dataclass
class SpreadResult:
    """
    Normalized result of evaluating a Priced Venue against a Reference Venue.
    Step 3 semantics:
    - reference_venue: Benchmark market data source
    - priced_venue: Venue evaluated against the reference venue
    - Neither role inherently means Maker, First Leg, Taker, or Hedge.
    - Either venue dynamically becomes First Leg / Maker or Second Leg / Hedge/Taker.
    """
    symbol: str
    reference_venue: str
    priced_venue: str
    reference_bid: float
    reference_ask: float
    priced_bid: float
    priced_ask: float
    spread_ref_to_priced: float  # Buy Ref, Sell Priced: priced_bid - ref_ask
    spread_pct_ref_to_priced: float  # ((priced_bid - ref_ask) / ref_ask) * 100
    spread_priced_to_ref: float  # Buy Priced, Sell Ref: ref_bid - priced_ask
    spread_pct_priced_to_ref: float  # ((ref_bid - priced_ask) / priced_ask) * 100
    best_route: str  # e.g. 'BINANCE->BYBIT' or 'BYBIT->BINANCE'
    gross_spread_pct: float
    net_spread_pct: float  # Gross minus total taker fees
    is_profitable: bool

    # Dynamic execution legs (determined by market pricing / maker-hedge strategy)
    buy_venue: str = ""
    sell_venue: str = ""
    buy_price: float = 0.0
    sell_price: float = 0.0
    first_leg_venue: str = ""   # Dynamic Maker venue
    second_leg_venue: str = ""  # Dynamic Hedge venue

    timestamp: float = field(default_factory=time.time)
    reference_source: str = "UNKNOWN"
    priced_source: str = "UNKNOWN"

    def __init__(
        self,
        symbol: str,
        reference_venue: Optional[str] = None,
        priced_venue: Optional[str] = None,
        reference_bid: Optional[float] = None,
        reference_ask: Optional[float] = None,
        priced_bid: Optional[float] = None,
        priced_ask: Optional[float] = None,
        spread_ref_to_priced: Optional[float] = None,
        spread_pct_ref_to_priced: Optional[float] = None,
        spread_priced_to_ref: Optional[float] = None,
        spread_pct_priced_to_ref: Optional[float] = None,
        best_route: str = "",
        gross_spread_pct: float = 0.0,
        net_spread_pct: float = 0.0,
        is_profitable: bool = False,
        buy_venue: str = "",
        sell_venue: str = "",
        buy_price: float = 0.0,
        sell_price: float = 0.0,
        first_leg_venue: str = "",
        second_leg_venue: str = "",
        timestamp: Optional[float] = None,
        reference_source: str = "UNKNOWN",
        priced_source: str = "UNKNOWN",
        **kwargs
    ):
        self.symbol = symbol
        self.reference_venue = reference_venue if reference_venue is not None else kwargs.get("venue_a", "")
        self.priced_venue = priced_venue if priced_venue is not None else kwargs.get("venue_b", "")
        self.reference_bid = reference_bid if reference_bid is not None else float(kwargs.get("bid_a", 0.0))
        self.reference_ask = reference_ask if reference_ask is not None else float(kwargs.get("ask_a", 0.0))
        self.priced_bid = priced_bid if priced_bid is not None else float(kwargs.get("bid_b", 0.0))
        self.priced_ask = priced_ask if priced_ask is not None else float(kwargs.get("ask_b", 0.0))
        self.spread_ref_to_priced = spread_ref_to_priced if spread_ref_to_priced is not None else float(kwargs.get("spread_a_to_b", 0.0))
        self.spread_pct_ref_to_priced = spread_pct_ref_to_priced if spread_pct_ref_to_priced is not None else float(kwargs.get("spread_pct_a_to_b", 0.0))
        self.spread_priced_to_ref = spread_priced_to_ref if spread_priced_to_ref is not None else float(kwargs.get("spread_b_to_a", 0.0))
        self.spread_pct_priced_to_ref = spread_pct_priced_to_ref if spread_pct_priced_to_ref is not None else float(kwargs.get("spread_pct_b_to_a", 0.0))
        self.best_route = best_route
        self.gross_spread_pct = gross_spread_pct
        self.net_spread_pct = net_spread_pct
        self.is_profitable = is_profitable

        self.buy_venue = buy_venue
        self.sell_venue = sell_venue
        self.buy_price = buy_price
        self.sell_price = sell_price
        self.first_leg_venue = first_leg_venue
        self.second_leg_venue = second_leg_venue

        self.timestamp = timestamp if timestamp is not None else time.time()
        self.reference_source = reference_source if reference_source != "UNKNOWN" else kwargs.get("source_a", "UNKNOWN")
        self.priced_source = priced_source if priced_source != "UNKNOWN" else kwargs.get("source_b", "UNKNOWN")

    # Backward compatibility properties
    @property
    def venue_a(self) -> str:
        return self.reference_venue

    @property
    def venue_b(self) -> str:
        return self.priced_venue

    @property
    def bid_a(self) -> float:
        return self.reference_bid

    @property
    def ask_a(self) -> float:
        return self.reference_ask

    @property
    def bid_b(self) -> float:
        return self.priced_bid

    @property
    def ask_b(self) -> float:
        return self.priced_ask

    @property
    def spread_a_to_b(self) -> float:
        return self.spread_ref_to_priced

    @property
    def spread_pct_a_to_b(self) -> float:
        return self.spread_pct_ref_to_priced

    @property
    def spread_b_to_a(self) -> float:
        return self.spread_priced_to_ref

    @property
    def spread_pct_b_to_a(self) -> float:
        return self.spread_pct_priced_to_ref

    @property
    def source_a(self) -> str:
        return self.reference_source

    @property
    def source_b(self) -> str:
        return self.priced_source


@dataclass
class RawSpreadResult:
    """
    Step 5: Backend Raw Spread % evaluation for Reference/Priced relationships.
    Uses real live executable bid/ask.
    Independent of fees, funding, slippage, and other costs.
    Evaluates both directions.
    """
    reference_venue: str
    priced_venue: str
    symbol: str
    prices: Dict[str, Optional[float]]
    direction: str  # 'BUY_REF_SELL_PRICED', 'BUY_PRICED_SELL_REF', 'SAME_VENUE', or 'INVALID'
    raw_spread_pct: Optional[float]
    raw_spread_usd: Optional[float] = None
    spread_ref_to_priced_pct: Optional[float] = None
    spread_priced_to_ref_pct: Optional[float] = None
    timestamp: float = field(default_factory=time.time)
    freshness: str = FreshnessStatus.FRESH.value
    validity: bool = True
    error_reason: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "reference_venue": self.reference_venue,
            "priced_venue": self.priced_venue,
            "symbol": self.symbol,
            "prices": self.prices,
            "direction": self.direction,
            "raw_spread_pct": self.raw_spread_pct,
            "raw_spread_usd": self.raw_spread_usd,
            "spread_ref_to_priced_pct": self.spread_ref_to_priced_pct,
            "spread_priced_to_ref_pct": self.spread_priced_to_ref_pct,
            "timestamp": self.timestamp,
            "freshness": self.freshness,
            "validity": self.validity,
            "error_reason": self.error_reason,
        }


@dataclass
class MakerHedgeCandidate:
    """
    Step 6: Dynamic Maker/Hedge Candidate.
    Represents an execution direction candidate between two venues:
    - Candidate A: Venue A is Maker / First Leg -> Venue B is Taker/Hedge / Second Leg
    - Candidate B: Venue B is Maker / First Leg -> Venue A is Taker/Hedge / Second Leg

    Never assumes Reference = Hedge or Priced = Maker.
    Contains: Maker venue, Maker price, Hedge venue, Hedge price, direction, timestamps, validity.
    """
    candidate_id: str                      # "A" or "B"
    symbol: str                            # e.g. "BTC/USDT"
    maker_venue: str                       # Venue placing passive Maker order (First Leg)
    maker_price: float                     # Executable price on Maker venue
    maker_side: str                        # "BUY" or "SELL"
    hedge_venue: str                       # Venue placing immediate Hedge order (Second Leg / Taker)
    hedge_price: float                     # Executable taker price on Hedge venue
    hedge_side: str                        # "SELL" or "BUY"
    direction: str                         # e.g. "BUY_A_SELL_B" or "BUY_B_SELL_A"
    raw_spread_pct: Optional[float] = None
    gross_spread_pct: Optional[float] = None
    maker_size: float = 0.0
    hedge_size: float = 0.0
    maker_fee_rate: float = 0.0
    taker_fee_rate: float = 0.0
    total_fee_rate: float = 0.0
    total_fee_pct: float = 0.0
    timestamps: Dict[str, float] = field(default_factory=dict)
    validity: bool = True
    error_reason: Optional[str] = None
    reference_venue: str = ""
    priced_venue: str = ""

    def to_dict(self) -> dict:
        return {
            "candidate_id": self.candidate_id,
            "symbol": self.symbol,
            "maker_venue": self.maker_venue,
            "maker_price": self.maker_price,
            "maker_side": self.maker_side,
            "hedge_venue": self.hedge_venue,
            "hedge_price": self.hedge_price,
            "hedge_side": self.hedge_side,
            "direction": self.direction,
            "raw_spread_pct": self.raw_spread_pct,
            "gross_spread_pct": self.gross_spread_pct,
            "maker_size": self.maker_size,
            "hedge_size": self.hedge_size,
            "maker_fee_rate": self.maker_fee_rate,
            "taker_fee_rate": self.taker_fee_rate,
            "total_fee_rate": self.total_fee_rate,
            "total_fee_pct": self.total_fee_pct,
            "timestamps": dict(self.timestamps),
            "validity": self.validity,
            "error_reason": self.error_reason,
            "reference_venue": self.reference_venue,
            "priced_venue": self.priced_venue,
        }


@dataclass
class DirectionEvaluationResult:
    """
    Step 6: Result of evaluating both dynamic Maker/Hedge candidates.
    Returns Candidate A (A Maker -> B Hedge) and Candidate B (B Maker -> A Hedge).
    """
    symbol: str
    venue_a: str
    venue_b: str
    candidate_a: MakerHedgeCandidate
    candidate_b: MakerHedgeCandidate
    reference_venue: str = ""
    priced_venue: str = ""
    timestamp: float = field(default_factory=time.time)

    @property
    def validity(self) -> bool:
        return bool(self.candidate_a and self.candidate_a.validity and self.candidate_b and self.candidate_b.validity)

    @property
    def is_valid(self) -> bool:
        return self.validity

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "venue_a": self.venue_a,
            "venue_b": self.venue_b,
            "reference_venue": self.reference_venue,
            "priced_venue": self.priced_venue,
            "candidate_a": self.candidate_a.to_dict(),
            "candidate_b": self.candidate_b.to_dict(),
            "timestamp": self.timestamp,
            "validity": self.validity,
        }


@dataclass
class DirectionalFeeResult:
    """
    Step 7: Directional Maker/Taker Fees.
    For Candidate A (A Maker -> B Taker):
      total fee = A Maker Fee + B Taker Fee
    For Candidate B (B Maker -> A Taker):
      total fee = B Maker Fee + A Taker Fee

    Raw Spread remains fee-independent.
    """
    symbol: str
    venue_a: str
    venue_b: str
    market_type: str = "spot"  # "spot", "perp", "futures"
    fee_tier_a: str = "standard"
    fee_tier_b: str = "standard"
    account_a: Optional[str] = None
    account_b: Optional[str] = None
    instrument: Optional[str] = None

    # Candidate A: A Maker -> B Taker
    candidate_a_maker_venue: str = ""
    candidate_a_taker_venue: str = ""
    candidate_a_maker_fee: float = 0.0
    candidate_a_taker_fee: float = 0.0
    candidate_a_total_fee: float = 0.0
    candidate_a_total_fee_pct: float = 0.0

    # Candidate B: B Maker -> A Taker
    candidate_b_maker_venue: str = ""
    candidate_b_taker_venue: str = ""
    candidate_b_maker_fee: float = 0.0
    candidate_b_taker_fee: float = 0.0
    candidate_b_total_fee: float = 0.0
    candidate_b_total_fee_pct: float = 0.0

    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "instrument": self.instrument or self.symbol,
            "venue_a": self.venue_a,
            "venue_b": self.venue_b,
            "account_a": self.account_a,
            "account_b": self.account_b,
            "market_type": self.market_type,
            "fee_tier_a": self.fee_tier_a,
            "fee_tier_b": self.fee_tier_b,
            "candidate_a": {
                "maker_venue": self.candidate_a_maker_venue,
                "taker_venue": self.candidate_a_taker_venue,
                "maker_fee": self.candidate_a_maker_fee,
                "taker_fee": self.candidate_a_taker_fee,
                "total_fee": self.candidate_a_total_fee,
                "total_fee_pct": self.candidate_a_total_fee_pct,
            },
            "candidate_b": {
                "maker_venue": self.candidate_b_maker_venue,
                "taker_venue": self.candidate_b_taker_venue,
                "maker_fee": self.candidate_b_maker_fee,
                "taker_fee": self.candidate_b_taker_fee,
                "total_fee": self.candidate_b_total_fee,
                "total_fee_pct": self.candidate_b_total_fee_pct,
            },
            "timestamp": self.timestamp,
        }


@dataclass
class TriangularOpportunity:
    venue: str
    route: List[str]  # e.g., ['USDT', 'BTC', 'ETH', 'USDT']
    pairs: List[str]  # e.g., ['BTC/USDT', 'ETH/BTC', 'ETH/USDT']
    actions: List[str]  # ['buy', 'buy', 'sell']
    rates: List[float]
    gross_profit_pct: float
    net_profit_pct: float  # After deducting 3 leg trading fees
    is_profitable: bool
    timestamp: float = field(default_factory=time.time)
    source: str = "UNKNOWN"


@dataclass
class ArbitrageOpportunity:
    arb_type: str  # 'CROSS_EXCHANGE', 'TRIANGULAR', 'MT5_CRYPTO', 'FUNDING'
    symbol_or_path: str
    reference_venue: str = ""
    priced_venue: str = ""
    buy_venue: str = ""
    sell_venue: str = ""
    first_leg_venue: str = ""   # Dynamic Maker/First Leg
    second_leg_venue: str = ""  # Dynamic Hedge/Taker
    buy_price: float = 0.0
    sell_price: float = 0.0
    reference_price: float = 0.0
    priced_price: float = 0.0
    spread_usd: float = 0.0
    spread_pct: float = 0.0
    net_profit_pct: float = 0.0
    estimated_fill_size: float = 0.0
    timestamp: float = field(default_factory=time.time)
    source: str = "UNKNOWN"


@dataclass
class DirectionalOpportunity:
    """
    Step 8: Directional Opportunity representing one execution direction:
    - Candidate A: A Maker -> B Taker/Hedge
    - Candidate B: B Maker -> A Taker/Hedge

    Integrates:
    - directional spread
    - maker fee (from FeeEngine)
    - taker fee (from FeeEngine)
    - slippage (VWAP / depth model)
    - liquidity (executable fill size)
    - other cost models

    Maintains:
    - raw_spread_pct: Strictly fee-independent!
    - net_spread_pct: Authoritative net spread after fees, slippage, and costs.
    """
    candidate_id: str                              # "A" or "B"
    symbol: str                                    # e.g. "BTC/USDT"
    maker_venue: str                               # Venue placing passive Maker order (First Leg)
    maker_price: float                             # Executable price on Maker venue
    maker_side: str                                # "BUY" or "SELL"
    maker_size: float                              # Available maker liquidity
    hedge_venue: str                               # Venue placing immediate Hedge order (Second Leg / Taker)
    hedge_price: float                             # Executable taker price on Hedge venue
    hedge_side: str                                # "SELL" or "BUY"
    hedge_size: float                              # Available hedge liquidity
    direction: str                                 # e.g. "BUY_A_SELL_B" or "BUY_B_SELL_A"

    # Spreads: Raw Spread is fee-independent, Net Spread is after all deductions
    raw_spread_pct: Optional[float] = None         # Independent of fees/slippage/costs
    raw_spread_usd: Optional[float] = None
    gross_spread_pct: Optional[float] = None
    net_spread_pct: Optional[float] = None         # Authoritative backend calculation
    net_spread_usd: Optional[float] = None

    # Fee and Cost Components
    maker_fee_rate: float = 0.0
    taker_fee_rate: float = 0.0
    total_fee_rate: float = 0.0
    total_fee_pct: float = 0.0
    slippage_pct: float = 0.0
    other_costs_pct: float = 0.0

    # Liquidity & Execution sizing
    liquidity_size: float = 0.0                    # Executable size = min(maker_size, hedge_size)
    liquidity_usd: float = 0.0                     # Executable value in USD

    # Authoritative viability
    is_profitable: bool = False
    calculation_components: Dict[str, Any] = field(default_factory=dict)

    timestamps: Dict[str, float] = field(default_factory=dict)
    validity: bool = True
    error_reason: Optional[str] = None
    reference_venue: str = ""
    priced_venue: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "symbol": self.symbol,
            "maker_venue": self.maker_venue,
            "maker_price": self.maker_price,
            "maker_side": self.maker_side,
            "maker_size": self.maker_size,
            "hedge_venue": self.hedge_venue,
            "hedge_price": self.hedge_price,
            "hedge_side": self.hedge_side,
            "hedge_size": self.hedge_size,
            "direction": self.direction,
            "raw_spread_pct": self.raw_spread_pct,
            "raw_spread_usd": self.raw_spread_usd,
            "gross_spread_pct": self.gross_spread_pct,
            "net_spread_pct": self.net_spread_pct,
            "net_spread_usd": self.net_spread_usd,
            "maker_fee_rate": self.maker_fee_rate,
            "taker_fee_rate": self.taker_fee_rate,
            "total_fee_rate": self.total_fee_rate,
            "total_fee_pct": self.total_fee_pct,
            "slippage_pct": self.slippage_pct,
            "other_costs_pct": self.other_costs_pct,
            "liquidity_size": self.liquidity_size,
            "liquidity_usd": self.liquidity_usd,
            "is_profitable": self.is_profitable,
            "calculation_components": dict(self.calculation_components),
            "timestamps": dict(self.timestamps),
            "validity": self.validity,
            "error_reason": self.error_reason,
            "reference_venue": self.reference_venue,
            "priced_venue": self.priced_venue,
        }


@dataclass
class OpportunityEvaluationResult:
    """
    Step 8: Result of evaluating both directional opportunities between two venues.
    Authoritative source for Net Spread % and execution direction profitability.
    """
    symbol: str
    venue_a: str
    venue_b: str
    opportunity_a: DirectionalOpportunity       # Candidate A: A Maker -> B Hedge
    opportunity_b: DirectionalOpportunity       # Candidate B: B Maker -> A Hedge
    highest_net_opportunity: Optional[DirectionalOpportunity] = None
    reference_venue: str = ""
    priced_venue: str = ""
    timestamp: float = field(default_factory=time.time)
    validity: bool = True
    error_reason: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "venue_a": self.venue_a,
            "venue_b": self.venue_b,
            "reference_venue": self.reference_venue,
            "priced_venue": self.priced_venue,
            "opportunity_a": self.opportunity_a.to_dict(),
            "opportunity_b": self.opportunity_b.to_dict(),
            "highest_net_opportunity": self.highest_net_opportunity.to_dict() if self.highest_net_opportunity else None,
            "timestamp": self.timestamp,
            "validity": self.validity,
            "error_reason": self.error_reason,
        }


@dataclass
class MakerEntryPricing:
    """
    Step 9: Maker execution pricing parameters and levels.
    Determined by Spread %, Grid, Grid Spacing, Bollinger, or configured entry controls.
    """
    pricing_mode: str                             # "SPREAD", "GRID", "BOLLINGER"
    base_maker_price: float                       # Primary Maker entry price
    maker_side: str                               # "BUY" or "SELL"
    order_quantity: float                         # Base order quantity
    grid_levels: List[Dict[str, Any]] = field(default_factory=list)  # If GRID mode
    bollinger_bands: Optional[Dict[str, float]] = None                # If BOLLINGER mode
    exit_target_price: float = 0.0                # Exit target (e.g. 0% convergence or mean)
    stop_loss_price: Optional[float] = None       # Optional stop loss level
    target_hedge_price: float = 0.0               # Expected hedge price on Second Leg venue

    @property
    def maker_price(self) -> float:
        return self.base_maker_price

    def to_dict(self) -> Dict[str, Any]:
        return {
            "pricing_mode": self.pricing_mode,
            "base_maker_price": self.base_maker_price,
            "maker_side": self.maker_side,
            "order_quantity": self.order_quantity,
            "grid_levels": self.grid_levels,
            "bollinger_bands": self.bollinger_bands,
            "exit_target_price": self.exit_target_price,
            "stop_loss_price": self.stop_loss_price,
            "target_hedge_price": self.target_hedge_price,
        }


@dataclass
class SpreadArbitrageDecision:
    """
    Step 9: Authoritative Spread Arbitrage strategy decision.
    Selects direction via Highest Net Spread %, sets First Leg = Maker & Second Leg = Hedge,
    and determines actual Maker entry pricing via Spread %, Grid, or Bollinger.
    """
    symbol: str
    selected_opportunity: Optional[DirectionalOpportunity] = None
    candidate_id: Optional[str] = None            # "A" or "B"
    first_leg_venue: str = ""                     # Maker venue
    second_leg_venue: str = ""                    # Taker / Hedge venue
    maker_side: str = ""                          # "BUY" or "SELL"
    hedge_side: str = ""                          # "SELL" or "BUY"
    net_spread_pct: float = 0.0
    raw_spread_pct: float = 0.0
    is_viable: bool = False
    rejection_reason: Optional[str] = None
    pricing: Optional[MakerEntryPricing] = None
    risk_metrics: Dict[str, Any] = field(default_factory=dict)
    exit_conditions: Dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)
    reference_venue: str = ""
    priced_venue: str = ""
    strategy_type: str = "SPREAD_ARBITRAGE"

    @property
    def maker_venue(self) -> str:
        return self.first_leg_venue

    @property
    def hedge_venue(self) -> str:
        return self.second_leg_venue

    @property
    def is_valid(self) -> bool:
        return self.is_viable

    @property
    def maker_pricing(self) -> Optional[MakerEntryPricing]:
        return self.pricing

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "strategy_type": self.strategy_type,
            "candidate_id": self.candidate_id,
            "first_leg_venue": self.first_leg_venue,
            "second_leg_venue": self.second_leg_venue,
            "maker_venue": self.maker_venue,
            "hedge_venue": self.hedge_venue,
            "maker_side": self.maker_side,
            "hedge_side": self.hedge_side,
            "net_spread_pct": self.net_spread_pct,
            "raw_spread_pct": self.raw_spread_pct,
            "is_viable": self.is_viable,
            "rejection_reason": self.rejection_reason,
            "pricing": self.pricing.to_dict() if self.pricing else None,
            "risk_metrics": dict(self.risk_metrics),
            "exit_conditions": dict(self.exit_conditions),
            "timestamp": self.timestamp,
            "reference_venue": self.reference_venue,
            "priced_venue": self.priced_venue,
            "selected_opportunity": self.selected_opportunity.to_dict() if self.selected_opportunity else None,
        }


@dataclass
class DirectionalFundingOpportunity:
    """
    Step 10: Directional funding opportunity between two venues.
    """
    symbol: str
    short_venue: str                              # Venue where position is SHORT (collecting funding if rate > 0)
    long_venue: str                               # Venue where position is LONG
    short_funding_rate: float                     # Funding rate on short venue (per period)
    long_funding_rate: float                      # Funding rate on long venue (per period, 0.0 if spot)
    net_funding_rate: float                       # Net funding rate per period (short_rate - long_rate)
    net_funding_annual_pct: float                 # Annualized net funding %
    candidate_id: str                             # "A" or "B"
    maker_venue: str                              # Venue placing First Leg / Maker order
    maker_side: str                               # "SELL" (if short venue) or "BUY" (if long venue)
    hedge_venue: str                              # Venue placing Second Leg / Hedge order
    hedge_side: str                               # "BUY" or "SELL"
    total_fee_pct: float = 0.0                    # Directional maker/taker fees %
    net_opportunity_pct: float = 0.0              # Net funding % after fees or per-settlement yield
    next_settlement_time: float = 0.0             # Timestamp of next funding settlement
    validity: bool = True
    error_reason: Optional[str] = None
    timestamps: Dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "short_venue": self.short_venue,
            "long_venue": self.long_venue,
            "short_funding_rate": self.short_funding_rate,
            "long_funding_rate": self.long_funding_rate,
            "net_funding_rate": self.net_funding_rate,
            "net_funding_annual_pct": self.net_funding_annual_pct,
            "candidate_id": self.candidate_id,
            "maker_venue": self.maker_venue,
            "maker_side": self.maker_side,
            "hedge_venue": self.hedge_venue,
            "hedge_side": self.hedge_side,
            "total_fee_pct": self.total_fee_pct,
            "net_opportunity_pct": self.net_opportunity_pct,
            "next_settlement_time": self.next_settlement_time,
            "validity": self.validity,
            "error_reason": self.error_reason,
            "timestamps": dict(self.timestamps),
        }


@dataclass
class FundingArbitrageDecision:
    """
    Step 10: Complete Funding Arbitrage decision.
    - Direction selected via Highest Funding % (net of directional costs)
    - First Leg = Maker, Second Leg = Hedge
    - Actual Maker execution level determined by Spread %, Grid, or Bollinger
    - Independent funding exit / settlement lifecycle
    """
    symbol: str
    selected_opportunity: Optional[DirectionalFundingOpportunity] = None
    first_leg_venue: str = ""                     # Maker venue
    second_leg_venue: str = ""                    # Hedge venue
    maker_side: str = ""                          # "BUY" or "SELL"
    hedge_side: str = ""                          # "SELL" or "BUY"
    net_funding_rate: float = 0.0                 # Per period
    net_funding_annual_pct: float = 0.0           # Annualized %
    is_viable: bool = False
    rejection_reason: Optional[str] = None
    pricing: Optional[MakerEntryPricing] = None
    risk_metrics: Dict[str, Any] = field(default_factory=dict)
    exit_conditions: Dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)
    reference_venue: str = ""
    priced_venue: str = ""
    strategy_type: str = "FUNDING_ARBITRAGE"

    @property
    def maker_venue(self) -> str:
        return self.first_leg_venue

    @property
    def hedge_venue(self) -> str:
        return self.second_leg_venue

    @property
    def is_valid(self) -> bool:
        return self.is_viable

    @property
    def maker_pricing(self) -> Optional[MakerEntryPricing]:
        return self.pricing

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "strategy_type": self.strategy_type,
            "first_leg_venue": self.first_leg_venue,
            "second_leg_venue": self.second_leg_venue,
            "maker_venue": self.maker_venue,
            "hedge_venue": self.hedge_venue,
            "maker_side": self.maker_side,
            "hedge_side": self.hedge_side,
            "net_funding_rate": self.net_funding_rate,
            "net_funding_annual_pct": self.net_funding_annual_pct,
            "is_viable": self.is_viable,
            "rejection_reason": self.rejection_reason,
            "pricing": self.pricing.to_dict() if self.pricing else None,
            "risk_metrics": dict(self.risk_metrics),
            "exit_conditions": dict(self.exit_conditions),
            "timestamp": self.timestamp,
            "reference_venue": self.reference_venue,
            "priced_venue": self.priced_venue,
            "selected_opportunity": self.selected_opportunity.to_dict() if self.selected_opportunity else None,
        }


@dataclass
class FundingPositionLeg:
    venue: str
    symbol: str
    side: str                                     # "BUY" or "SELL"
    role: str                                     # "MAKER" or "HEDGE"
    quantity: float
    entry_price: float
    filled: bool = True
    order_id: Optional[str] = None
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "venue": self.venue,
            "symbol": self.symbol,
            "side": self.side,
            "role": self.role,
            "quantity": self.quantity,
            "entry_price": self.entry_price,
            "filled": self.filled,
            "order_id": self.order_id,
            "timestamp": self.timestamp,
        }


@dataclass
class FundingPositionLifecycle:
    """
    Step 11: Complete Funding Arbitrage lifecycle state machine tracking.
    Tracks all 10 required fields independently from Spread Arbitrage:
    1. funding_interval
    2. funding_timestamp
    3. expected_direction
    4. funding_eligibility
    5. settlement_status
    6. actual_funding_result
    7. position_state
    8. entry_timestamp
    9. exit_state
    10. reconciliation_state
    """
    position_id: str
    symbol: str
    maker_leg: FundingPositionLeg
    hedge_leg: FundingPositionLeg

    # 1. funding interval (hours, e.g. 8)
    funding_interval: int = 8

    # 2. funding timestamp (next expected settlement unix timestamp)
    funding_timestamp: float = 0.0

    # 3. expected direction (e.g. "SHORT_BINANCE_LONG_BYBIT")
    expected_direction: str = ""

    # 4. funding eligibility (held across settlement cut-off)
    funding_eligibility: bool = False

    # 5. settlement status ("PENDING_SETTLEMENT", "SETTLED", "UNAVAILABLE", "FAILED")
    settlement_status: str = "PENDING_SETTLEMENT"

    # 6. actual funding result where available (accumulated funding cashflow)
    actual_funding_result: Optional[float] = None

    # 7. position state ("OPENING", "HEDGED", "MONITORING", "CLOSING", "CLOSED", "FAILED")
    position_state: str = "HEDGED"

    # 8. entry timestamp
    entry_timestamp: float = field(default_factory=time.time)

    # 9. exit state ("ACTIVE", "EXIT_SETTLED", "EXIT_TARGET_REACHED", "EXIT_INVALIDATED", "EXIT_RISK", "EXIT_DURATION_LIMIT", "EXIT_FAILED")
    exit_state: str = "ACTIVE"

    # 10. reconciliation state ("BALANCED", "MISMATCHED", "UNHEDGED", "RECONCILED")
    reconciliation_state: str = "BALANCED"

    # Additional audit & risk tracking
    accumulated_funding_usd: float = 0.0
    accumulated_funding_pct: float = 0.0
    settlement_count: int = 0
    exit_timestamp: Optional[float] = None
    exit_reason: Optional[str] = None
    unhedged_risk: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "position_id": self.position_id,
            "symbol": self.symbol,
            "maker_leg": self.maker_leg.to_dict(),
            "hedge_leg": self.hedge_leg.to_dict(),
            "funding_interval": self.funding_interval,
            "funding_timestamp": self.funding_timestamp,
            "expected_direction": self.expected_direction,
            "funding_eligibility": self.funding_eligibility,
            "settlement_status": self.settlement_status,
            "actual_funding_result": self.actual_funding_result,
            "position_state": self.position_state,
            "entry_timestamp": self.entry_timestamp,
            "exit_state": self.exit_state,
            "reconciliation_state": self.reconciliation_state,
            "accumulated_funding_usd": self.accumulated_funding_usd,
            "accumulated_funding_pct": self.accumulated_funding_pct,
            "settlement_count": self.settlement_count,
            "exit_timestamp": self.exit_timestamp,
            "exit_reason": self.exit_reason,
            "unhedged_risk": self.unhedged_risk,
        }


# =============================================================================
# Step 13 Models — Screener → Bot Manager → Execution Pipeline
# =============================================================================

class BotExecutionMode(str, Enum):
    DRY_RUN = "DRY_RUN"
    LIVE = "LIVE"


class BotStatus(str, Enum):
    STOPPED = "STOPPED"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    HALTED = "HALTED"
    ERROR = "ERROR"


@dataclass
class PreSubmissionValidationResult:
    """
    Step 13: Authoritative pre-submission revalidation result.
    Mandatory revalidation before every order dispatch:
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
    """
    is_valid: bool
    failed_checks: List[str] = field(default_factory=list)
    checks_detail: Dict[str, Any] = field(default_factory=dict)
    rejection_reason: Optional[str] = None
    timestamp: float = field(default_factory=time.time)
    market_state_snapshot: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "is_valid": self.is_valid,
            "failed_checks": list(self.failed_checks),
            "checks_detail": dict(self.checks_detail),
            "rejection_reason": self.rejection_reason,
            "timestamp": self.timestamp,
            "market_state_snapshot": dict(self.market_state_snapshot),
        }


@dataclass
class BotConfig:
    """
    Step 13: Bot Manager configuration definition.
    """
    bot_id: str
    strategy_type: str = "SPREAD_ARBITRAGE"     # "SPREAD_ARBITRAGE" | "FUNDING_ARBITRAGE" | "TRIANGULAR"
    mode: str = "DRY_RUN"                       # "DRY_RUN" | "LIVE"
    symbol: str = "BTC/USDT"
    reference_venue: str = "BINANCE"
    priced_venue: str = "BYBIT"
    order_quantity: float = 1.0
    max_position_size: float = 10.0
    pricing_mode: str = "SPREAD"                # "SPREAD" | "GRID" | "BOLLINGER"
    grid_spacing_pct: float = 0.05
    grid_levels_count: int = 3
    bollinger_k: float = 2.0
    bollinger_period: int = 20
    min_profit_threshold_pct: float = 0.05
    max_slippage_pct: float = 0.20
    stale_threshold_seconds: float = 10.0
    status: str = "STOPPED"                     # "STOPPED" | "RUNNING" | "PAUSED" | "HALTED"
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "bot_id": self.bot_id,
            "strategy_type": self.strategy_type,
            "mode": self.mode,
            "symbol": self.symbol,
            "reference_venue": self.reference_venue,
            "priced_venue": self.priced_venue,
            "order_quantity": self.order_quantity,
            "max_position_size": self.max_position_size,
            "pricing_mode": self.pricing_mode,
            "grid_spacing_pct": self.grid_spacing_pct,
            "grid_levels_count": self.grid_levels_count,
            "bollinger_k": self.bollinger_k,
            "bollinger_period": self.bollinger_period,
            "min_profit_threshold_pct": self.min_profit_threshold_pct,
            "max_slippage_pct": self.max_slippage_pct,
            "stale_threshold_seconds": self.stale_threshold_seconds,
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass
class ExecutionCycle:
    """
    Step 13: Execution Engine coordinated cycle tracking Maker (First Leg) and Hedge (Second Leg).
    """
    cycle_id: str
    bot_id: str
    strategy_type: str
    symbol: str
    maker_venue: str
    hedge_venue: str
    maker_side: str
    hedge_side: str
    target_quantity: float
    maker_price: float = 0.0
    hedge_price: float = 0.0
    maker_filled_qty: float = 0.0
    hedge_filled_qty: float = 0.0
    cycle_state: str = "INITIAL"                # "INITIAL", "VALIDATING", "MAKER_PLACED", "MAKER_FILLED", "HEDGED", "RECONCILED", "UNWOUND", "FAILED"
    reconciliation_status: str = "BALANCED"     # "BALANCED", "MISMATCHED", "UNHEDGED"
    pre_submission_validation: Optional[PreSubmissionValidationResult] = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    pnl_usd: float = 0.0
    error_message: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "cycle_id": self.cycle_id,
            "bot_id": self.bot_id,
            "strategy_type": self.strategy_type,
            "symbol": self.symbol,
            "maker_venue": self.maker_venue,
            "hedge_venue": self.hedge_venue,
            "maker_side": self.maker_side,
            "hedge_side": self.hedge_side,
            "target_quantity": self.target_quantity,
            "maker_price": self.maker_price,
            "hedge_price": self.hedge_price,
            "maker_filled_qty": self.maker_filled_qty,
            "hedge_filled_qty": self.hedge_filled_qty,
            "cycle_state": self.cycle_state,
            "reconciliation_status": self.reconciliation_status,
            "pre_submission_validation": self.pre_submission_validation.to_dict() if self.pre_submission_validation else None,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "pnl_usd": self.pnl_usd,
            "error_message": self.error_message,
        }

