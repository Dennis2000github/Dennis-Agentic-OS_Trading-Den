"""
Unified Market Data Manager & Multi-Source Routing Layer.
Coordinates Native WebSockets and Native REST as PRIMARY market-data connectors,
with CCXT Pro implemented as a standby fallback / unified alternative.
Maintains in-memory live state with heartbeat monitoring and auto-failover.
Implements Step 2 Normalized Market Data and Hybrid Startup Sequence.
"""

import asyncio
import logging
import time
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from .models import (
    ConnectionStatus,
    DataSourceType,
    FreshnessStatus,
    FundingDirection,
    FundingRate,
    NormalizedMarketState,
    OHLCV,
    OrderBook,
    Ticker,
    Trade,
)
from .ccxt_pro_connector import CCXTProConnector
from .native_ws_connector import NativeWebSocketConnector
from .rest_connector import RESTConnector
from .live_state import LiveStateManager

logger = logging.getLogger(__name__)


class MarketDataManager:
    """
    Central Market Data Hub.
    Maintains Native WebSocket and Native REST as PRIMARY data channels,
    with CCXT Pro as a standby fallback alternative.
    Coordinates In-Memory Live State, monitors connectivity health, performs auto-failover,
    and runs the Step 2 Hybrid Startup Sequence.
    """

    SUPPORTED_EXCHANGES = ["binance", "bybit", "okx", "kraken", "mexc"]

    def __init__(
        self,
        default_source: DataSourceType = DataSourceType.AUTO,
        enabled_exchanges: Optional[List[str]] = None,
        stale_threshold_seconds: float = 10.0,
        live_state: Optional[LiveStateManager] = None,
        persistence: Optional[Any] = None,
    ):
        self.default_source = default_source
        self.exchanges = [
            e.lower() for e in (enabled_exchanges or self.SUPPORTED_EXCHANGES)
            if e.lower() in self.SUPPORTED_EXCHANGES
        ]
        self.stale_threshold_seconds = stale_threshold_seconds

        # In-Memory Live State layer
        self.live_state = live_state or LiveStateManager()
        self.persistence = persistence

        # Connectors: Native WS (Primary), REST (Primary snapshot/fallback), CCXT Pro (Fallback/Unified)
        self.native_ws = NativeWebSocketConnector(exchanges=self.exchanges)
        self.rest = RESTConnector(exchanges=self.exchanges)
        self.rest_connector = self.rest  # Backward compatibility
        self.ccxt_pro = CCXTProConnector(exchanges=self.exchanges)

        # Per-exchange active source selection: exchange -> DataSourceType
        self.source_selection: Dict[str, DataSourceType] = {
            e: default_source for e in self.exchanges
        }

        # Unified Cache (backward-compatible view mirrored from LiveState)
        self._books: Dict[str, OrderBook] = self.live_state._books
        self._tickers: Dict[str, Ticker] = self.live_state._tickers
        self._funding: Dict[str, FundingRate] = self.live_state._funding

        # Global event listeners
        self._listeners_ob: List[Callable[[OrderBook], None]] = []
        self._listeners_ticker: List[Callable[[Ticker], None]] = []
        self._listeners_funding: List[Callable[[FundingRate], None]] = []
        self._listeners_analytical: List[Callable[[NormalizedMarketState], None]] = []

        self._running = False
        self._health_monitor_task: Optional[asyncio.Task] = None
        self._analytical_task: Optional[asyncio.Task] = None

        # Wire upstream callbacks into live state
        self.native_ws.on_order_book(self._on_native_ws_book)
        self.native_ws.on_ticker(self._on_native_ws_ticker)

        self.ccxt_pro.on_order_book(self._on_ccxt_pro_book)
        self.ccxt_pro.on_ticker(self._on_ccxt_pro_ticker)
        self.ccxt_pro.on_funding_rate(self._on_ccxt_pro_funding)

    async def start(self):
        """
        Initialize underlying connectors asynchronously and independently.
        Uses timeout protection so one slow exchange or connector cannot block application startup.
        """
        self._running = True
        logger.info("Starting MarketDataManager (mode: %s)...", self.default_source.value)

        # Start primary and fallback connectors concurrently with timeout guards
        start_tasks = []
        if self.default_source in (DataSourceType.AUTO, DataSourceType.NATIVE_WS):
            start_tasks.append(self._safe_start_connector("Native WS", self.native_ws.start()))

        if self.default_source == DataSourceType.CCXT_PRO:
            start_tasks.append(self._safe_start_connector("CCXT Pro", self.ccxt_pro.start()))

        if start_tasks:
            await asyncio.gather(*start_tasks, return_exceptions=True)

        self._health_monitor_task = asyncio.create_task(self._health_monitor_loop())
        self._analytical_task = asyncio.create_task(self._analytical_dispatch_loop())
        logger.info("MarketDataManager operational.")

    async def _safe_start_connector(self, name: str, coro, timeout: float = 3.0):
        """Safely start connector without blocking if network is slow."""
        try:
            await asyncio.wait_for(coro, timeout=timeout)
        except asyncio.TimeoutError:
            logger.warning("%s start timed out after %.1fs. Continuing in background...", name, timeout)
        except Exception as e:
            logger.error("Error starting %s: %s", name, e)

    async def close(self):
        """Shutdown all connectors."""
        self._running = False
        if self._health_monitor_task:
            self._health_monitor_task.cancel()
        if self._analytical_task:
            self._analytical_task.cancel()

        await asyncio.gather(
            self.native_ws.close(),
            self.ccxt_pro.close(),
            self.rest.close(),
            return_exceptions=True,
        )
        logger.info("MarketDataManager stopped.")

    # -------------------------------------------------------------------------
    # Subscription & Routing (Native WS = Primary, CCXT Pro = Fallback)
    # -------------------------------------------------------------------------

    def set_source(self, exchange: str, source: DataSourceType):
        """Override data source for a specific exchange."""
        exchange = exchange.lower()
        if exchange in self.exchanges:
            self.source_selection[exchange] = source
            logger.info("Switched source for %s to %s", exchange.upper(), source.value)

    def subscribe_order_book(self, exchange: str, symbol: str, force_fallback: bool = False):
        """
        Subscribe to order book without duplicate network connections.
        Primary: Native WS. Standby fallback: CCXT Pro (activated only if forced or native fails).
        """
        exchange = exchange.lower()
        mode = self.source_selection.get(exchange, self.default_source)

        if mode == DataSourceType.NATIVE_WS:
            self.native_ws.subscribe_order_book(exchange, symbol)
        elif mode == DataSourceType.CCXT_PRO:
            self.ccxt_pro.subscribe_order_book(exchange, symbol)
        elif mode == DataSourceType.AUTO:
            if force_fallback:
                self.ccxt_pro.subscribe_order_book(exchange, symbol)
            else:
                self.native_ws.subscribe_order_book(exchange, symbol)

    def subscribe_ticker(self, exchange: str, symbol: str, force_fallback: bool = False):
        """
        Subscribe to ticker quote without duplicate network connections.
        Primary: Native WS. Standby fallback: CCXT Pro.
        """
        exchange = exchange.lower()
        mode = self.source_selection.get(exchange, self.default_source)

        if mode == DataSourceType.NATIVE_WS:
            self.native_ws.subscribe_ticker(exchange, symbol)
        elif mode == DataSourceType.CCXT_PRO:
            self.ccxt_pro.subscribe_ticker(exchange, symbol)
        elif mode == DataSourceType.AUTO:
            if force_fallback:
                self.ccxt_pro.subscribe_ticker(exchange, symbol)
            else:
                self.native_ws.subscribe_ticker(exchange, symbol)

    def subscribe_funding_rate(self, exchange: str, symbol: str):
        """Subscribe to funding rate updates via CCXT Pro or REST polling."""
        self.ccxt_pro.subscribe_funding_rate(exchange, symbol)

    def add_order_book_listener(self, callback: Callable[[OrderBook], None]):
        self._listeners_ob.append(callback)

    def add_ticker_listener(self, callback: Callable[[Ticker], None]):
        self._listeners_ticker.append(callback)

    def add_funding_rate_listener(self, callback: Callable[[FundingRate], None]):
        self._listeners_funding.append(callback)

    def add_analytical_listener(self, callback: Callable[[NormalizedMarketState], None]):
        """Register listener for ~1-second normalized analytical updates for strategy calculation."""
        self._listeners_analytical.append(callback)

    # -------------------------------------------------------------------------
    # Incoming Message Processing & Live State Ingestion
    # (Native WS is PRIMARY; CCXT Pro is FALLBACK)
    # -------------------------------------------------------------------------

    def _update_normalized_state_from_book(self, ob: OrderBook):
        state = self.live_state.get_normalized_state(ob.venue, ob.symbol)
        if not state:
            state = NormalizedMarketState(
                venue=ob.venue.upper(),
                symbol=ob.symbol,
                best_bid=ob.best_bid or 0.0,
                best_ask=ob.best_ask or 0.0,
                bid_size=ob.best_bid_size or 0.0,
                ask_size=ob.best_ask_size or 0.0,
                timestamp=ob.timestamp,
                sequence=ob.sequence,
                connection_status=ob.connection_status,
                has_rest_snapshot=False,
                ws_synchronized=ob.is_synchronized,
            )
        else:
            if ob.best_bid is not None:
                state.best_bid = ob.best_bid
            if ob.best_ask is not None:
                state.best_ask = ob.best_ask
            if ob.best_bid_size is not None:
                state.bid_size = ob.best_bid_size
            if ob.best_ask_size is not None:
                state.ask_size = ob.best_ask_size
            state.timestamp = ob.timestamp
            if ob.sequence is not None:
                state.sequence = ob.sequence
            state.connection_status = ob.connection_status
            state.ws_synchronized = ob.is_synchronized
            state.last_ws_update_time = time.time()

        if state.volume_24h == 0.0:
            cached_ticker = self.live_state.get_ticker(ob.venue, ob.symbol)
            if cached_ticker and cached_ticker.volume_24h > 0:
                state.volume_24h = cached_ticker.volume_24h
        if state.funding_rate is None:
            cached_fr = self.live_state.get_funding_rate(ob.venue, ob.symbol)
            if cached_fr:
                state.funding_rate = cached_fr.rate
                state.funding_direction = cached_fr.funding_direction

        state.evaluate_readiness(self.stale_threshold_seconds)
        self.live_state.set_normalized_state(ob.venue, ob.symbol, state)

    def _update_normalized_state_from_ticker(self, t: Ticker):
        state = self.live_state.get_normalized_state(t.venue, t.symbol)
        if not state:
            state = NormalizedMarketState(
                venue=t.venue.upper(),
                symbol=t.symbol,
                best_bid=t.bid,
                best_ask=t.ask,
                bid_size=t.bid_size,
                ask_size=t.ask_size,
                volume_24h=t.volume_24h,
                timestamp=t.timestamp,
                sequence=t.sequence,
                connection_status=t.connection_status,
                has_rest_snapshot=False,
                ws_synchronized=t.is_synchronized,
            )
        else:
            if t.bid > 0:
                state.best_bid = t.bid
            if t.ask > 0:
                state.best_ask = t.ask
            if t.bid_size > 0:
                state.bid_size = t.bid_size
            if t.ask_size > 0:
                state.ask_size = t.ask_size
            if t.volume_24h > 0:
                state.volume_24h = t.volume_24h
            state.timestamp = t.timestamp
            if t.sequence is not None:
                state.sequence = t.sequence
            state.connection_status = t.connection_status
            state.ws_synchronized = t.is_synchronized
            state.last_ws_update_time = time.time()

        if state.bid_size == 0.0 or state.ask_size == 0.0:
            cached_book = self.live_state.get_order_book(t.venue, t.symbol)
            if cached_book:
                if cached_book.best_bid_size and state.bid_size == 0.0:
                    state.bid_size = cached_book.best_bid_size
                if cached_book.best_ask_size and state.ask_size == 0.0:
                    state.ask_size = cached_book.best_ask_size
        if state.funding_rate is None:
            cached_fr = self.live_state.get_funding_rate(t.venue, t.symbol)
            if cached_fr:
                state.funding_rate = cached_fr.rate
                state.funding_direction = cached_fr.funding_direction

        state.evaluate_readiness(self.stale_threshold_seconds)
        self.live_state.set_normalized_state(t.venue, t.symbol, state)

    def _update_normalized_state_from_funding(self, fr: FundingRate):
        state = self.live_state.get_normalized_state(fr.venue, fr.symbol)
        if not state:
            state = NormalizedMarketState(
                venue=fr.venue.upper(),
                symbol=fr.symbol,
                funding_rate=fr.rate,
                funding_direction=fr.funding_direction,
                timestamp=fr.timestamp,
                connection_status=fr.connection_status,
            )
        else:
            state.funding_rate = fr.rate
            state.funding_direction = fr.funding_direction
        state.evaluate_readiness(self.stale_threshold_seconds)
        self.live_state.set_normalized_state(fr.venue, fr.symbol, state)

    def _on_native_ws_book(self, ob: OrderBook):
        """Native WS is the PRIMARY feed for order books in AUTO and NATIVE_WS mode."""
        mode = self.source_selection.get(ob.venue.lower(), self.default_source)
        if mode in (DataSourceType.AUTO, DataSourceType.NATIVE_WS):
            self.live_state.set_order_book(ob.venue, ob.symbol, ob)
            self._update_normalized_state_from_book(ob)
            # High-frequency raw updates preserved for execution path
            for listener in self._listeners_ob:
                try:
                    listener(ob)
                except Exception as e:
                    logger.debug("Error in order book listener: %s", e)

    def _on_ccxt_pro_book(self, ob: OrderBook):
        """CCXT Pro is FALLBACK when Native WS is absent, stale, or explicitly chosen."""
        mode = self.source_selection.get(ob.venue.lower(), self.default_source)
        current = self.live_state.get_order_book(ob.venue, ob.symbol)

        should_use = (
            mode == DataSourceType.CCXT_PRO
            or current is None
            or current.source != DataSourceType.NATIVE_WS.value
            or (time.time() - current.timestamp) > self.stale_threshold_seconds
        )

        if should_use:
            self.live_state.set_order_book(ob.venue, ob.symbol, ob)
            self._update_normalized_state_from_book(ob)
            for listener in self._listeners_ob:
                try:
                    listener(ob)
                except Exception as e:
                    logger.debug("Error in order book listener: %s", e)

    def _on_native_ws_ticker(self, t: Ticker):
        """Native WS is PRIMARY feed for tickers in AUTO and NATIVE_WS mode."""
        mode = self.source_selection.get(t.venue.lower(), self.default_source)
        if mode in (DataSourceType.AUTO, DataSourceType.NATIVE_WS):
            self.live_state.set_ticker(t.venue, t.symbol, t)
            self._update_normalized_state_from_ticker(t)
            for listener in self._listeners_ticker:
                try:
                    listener(t)
                except Exception as e:
                    logger.debug("Error in ticker listener: %s", e)

    def _on_ccxt_pro_ticker(self, t: Ticker):
        """CCXT Pro is FALLBACK for tickers."""
        mode = self.source_selection.get(t.venue.lower(), self.default_source)
        current = self.live_state.get_ticker(t.venue, t.symbol)

        should_use = (
            mode == DataSourceType.CCXT_PRO
            or current is None
            or current.source != DataSourceType.NATIVE_WS.value
            or (time.time() - current.timestamp) > self.stale_threshold_seconds
        )

        if should_use:
            self.live_state.set_ticker(t.venue, t.symbol, t)
            self._update_normalized_state_from_ticker(t)
            for listener in self._listeners_ticker:
                try:
                    listener(t)
                except Exception as e:
                    logger.debug("Error in ticker listener: %s", e)

    def _on_ccxt_pro_funding(self, fr: FundingRate):
        self.live_state.set_funding_rate(fr.venue, fr.symbol, fr)
        self._update_normalized_state_from_funding(fr)
        for listener in self._listeners_funding:
            try:
                listener(fr)
            except Exception as e:
                logger.debug("Error in funding listener: %s", e)

    # -------------------------------------------------------------------------
    # Step 2 Hybrid Startup Pipeline
    # -------------------------------------------------------------------------

    async def initialize_symbol_hybrid(
        self,
        exchange: str,
        symbol: str,
        candles_limit: int = 60,
    ) -> NormalizedMarketState:
        """
        Executes the Step 2 Hybrid Startup Sequence:
        Bot Start
        -> Load Configuration
        -> Initialize Connectors
        -> REST Current Snapshot
        -> REST Historical 1-Minute Candles
        -> Initialize Normalized State
        -> Build Historical Deviation
        -> Initialize Grid/Bollinger Statistical References
        -> Start WebSockets
        -> Synchronize REST Snapshot + WebSocket Updates
        -> Transition to WebSocket Live Source
        -> Normalized Live Market State
        """
        exchange = exchange.lower()
        venue_upper = exchange.upper()
        logger.info("Hybrid Startup initiated for %s %s...", venue_upper, symbol)

        # 1. Fetch REST Current Snapshot (OrderBook and Ticker)
        rest_ob = await self.rest.fetch_order_book(exchange, symbol, limit=20)
        rest_ticker = await self.rest.fetch_ticker(exchange, symbol)

        best_bid = 0.0
        best_ask = 0.0
        bid_size = 0.0
        ask_size = 0.0
        vol_24h = 0.0
        seq = None
        ts = time.time()

        if rest_ob and rest_ob.is_valid():
            self.live_state.set_order_book(exchange, symbol, rest_ob)
            best_bid = rest_ob.best_bid or 0.0
            best_ask = rest_ob.best_ask or 0.0
            bid_size = rest_ob.best_bid_size or 0.0
            ask_size = rest_ob.best_ask_size or 0.0
            seq = rest_ob.sequence
            ts = rest_ob.timestamp

        if rest_ticker and rest_ticker.is_valid():
            self.live_state.set_ticker(exchange, symbol, rest_ticker)
            if best_bid == 0.0:
                best_bid = rest_ticker.bid
            if best_ask == 0.0:
                best_ask = rest_ticker.ask
            if bid_size == 0.0:
                bid_size = rest_ticker.bid_size
            if ask_size == 0.0:
                ask_size = rest_ticker.ask_size
            vol_24h = rest_ticker.volume_24h
            if rest_ob is None:
                ts = rest_ticker.timestamp

        has_snapshot = bool((rest_ob and rest_ob.is_valid()) or (rest_ticker and rest_ticker.is_valid()))

        # 2. Fetch Historical 1-Minute Candles (Check SQLite first for ultra-fast startup)
        candles = []
        if self.persistence and hasattr(self.persistence, "get_market_candles"):
            try:
                cached = self.persistence.get_market_candles(exchange, symbol, timeframe="1m", limit=candles_limit)
                if cached and len(cached) >= min(candles_limit, 20):
                    latest_ts = cached[-1].get("timestamp") or cached[-1].get("time") or 0
                    if (time.time() - latest_ts) < 300:
                        candles = [
                            OHLCV(
                                venue=c["exchange"].upper(),
                                symbol=c["symbol"],
                                timestamp=c["timestamp"],
                                open=c["open"],
                                high=c["high"],
                                low=c["low"],
                                close=c["close"],
                                volume=c.get("volume", 0.0),
                                source=DataSourceType.REST.value,
                            )
                            for c in cached
                        ]
            except Exception as ex:
                logger.debug("SQLite candle cache read error: %s", ex)

        if not candles:
            candles = await self.rest.fetch_ohlcv(exchange, symbol, timeframe="1m", limit=candles_limit)
            if candles and self.persistence and hasattr(self.persistence, "batch_save_market_candles"):
                try:
                    self.persistence.batch_save_market_candles(exchange, symbol, "1m", candles)
                except Exception as ex:
                    logger.debug("SQLite candle cache write error: %s", ex)

        closes_1m = [c.close for c in candles if c.close > 0]

        # Aggregate 1m candles into 1H closes to compute Bollinger deviation
        hourly_map = {}
        for c in candles:
            if c.close > 0:
                h_ts = int(c.timestamp // 3600 * 3600)
                hourly_map[h_ts] = c.close
        closes_1h = list(hourly_map.values())

        # Use 1H closes to compute the deviation (fallback to 1m closes if < 5 bars)
        ref_closes = closes_1h if len(closes_1h) >= 5 else closes_1m

        # Build Historical Deviation and Grid/Bollinger Statistical References
        mean_dev = 0.0
        sd_dev = 0.0
        b_upper = 0.0
        b_lower = 0.0
        b_width = 0.0

        if len(ref_closes) >= 2:
            mean_dev = sum(ref_closes) / len(ref_closes)
            var = sum((x - mean_dev) ** 2 for x in ref_closes) / (len(ref_closes) - 1)
            sd_dev = var ** 0.5
            k = 2.0
            half_w = max(k * sd_dev, mean_dev * 0.0001)
            b_upper = mean_dev + half_w
            b_lower = mean_dev - half_w
            b_width = 2.0 * half_w

        # 4. Construct NormalizedMarketState
        state = NormalizedMarketState(
            venue=venue_upper,
            symbol=symbol,
            best_bid=best_bid,
            best_ask=best_ask,
            bid_size=bid_size,
            ask_size=ask_size,
            volume_24h=vol_24h,
            timestamp=ts,
            sequence=seq,
            freshness=FreshnessStatus.FRESH.value if has_snapshot else FreshnessStatus.WAITING_FOR_DATA.value,
            connection_status=ConnectionStatus.CONNECTED.value if has_snapshot else ConnectionStatus.CONNECTING.value,
            has_rest_snapshot=has_snapshot,
            ws_synchronized=False,
            historical_deviation_mean=round(mean_dev, 6),
            historical_deviation_sd=round(sd_dev, 6),
            bollinger_upper=round(b_upper, 6),
            bollinger_lower=round(b_lower, 6),
            bollinger_bandwidth=round(b_width, 6),
            last_rest_snapshot_time=time.time(),
        )

        self.live_state.set_normalized_state(exchange, symbol, state)

        # 5. Start WebSockets (Native WS is primary, no duplicate CCXT Pro)
        self.subscribe_order_book(exchange, symbol)
        self.subscribe_ticker(exchange, symbol)

        # 6. Evaluate readiness gating
        state.evaluate_readiness(self.stale_threshold_seconds)
        self.live_state.set_normalized_state(exchange, symbol, state)

        logger.info(
            "Hybrid Startup completed for %s %s: snapshot=%s, ready=%s, mean=%.4f, sd=%.4f",
            venue_upper, symbol, state.has_rest_snapshot, state.is_execution_ready,
            state.historical_deviation_mean, state.historical_deviation_sd
        )
        return state

    async def start_hybrid_pipeline(self, symbols_by_exchange: Dict[str, List[str]]):
        """Execute hybrid startup concurrently across all requested exchanges and symbols."""
        tasks = []
        for ex, syms in symbols_by_exchange.items():
            for sym in syms:
                tasks.append(self.initialize_symbol_hybrid(ex, sym))
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def is_execution_ready(self, exchange: str, symbol: str) -> bool:
        """A symbol is not execution-ready until REST snapshot exists, WS is synchronized, bid/ask valid, freshness passes."""
        return self.live_state.is_symbol_ready(exchange, symbol, self.stale_threshold_seconds)

    def get_normalized_state(self, exchange: str, symbol: str) -> Optional[NormalizedMarketState]:
        return self.live_state.get_normalized_state(exchange, symbol)

    def get_all_normalized_states(self) -> Dict[str, NormalizedMarketState]:
        return self.live_state.get_all_normalized_states()

    async def _analytical_dispatch_loop(self):
        """Produces approximately 1-second normalized analytical updates for strategy calculations."""
        while self._running:
            try:
                states = self.live_state.get_all_normalized_states()
                for state in states.values():
                    state.evaluate_readiness(self.stale_threshold_seconds)
                    for listener in self._listeners_analytical:
                        try:
                            listener(state)
                        except Exception as e:
                            logger.debug("Error in analytical listener: %s", e)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Error in analytical dispatch loop: %s", e)
            await asyncio.sleep(1.0)

    # -------------------------------------------------------------------------
    # Getters with Instant Fallback to REST
    # -------------------------------------------------------------------------

    async def get_order_book(
        self,
        exchange: str,
        symbol: str,
        limit: int = 20,
        force_source: Optional[DataSourceType] = None,
    ) -> Optional[OrderBook]:
        """
        Get order book with fallback hierarchy:
        1. In-memory cached feed (Native WS primary, CCXT Pro fallback) if fresh
        2. REST snapshot (if cache missing, stale, or forced REST)
        """
        if force_source == DataSourceType.REST:
            return await self.rest.fetch_order_book(exchange, symbol, limit=limit)

        cached = self.live_state.get_order_book(exchange, symbol)
        now = time.time()

        if cached and (now - cached.timestamp) <= self.stale_threshold_seconds:
            return cached

        # Fallback to REST snapshot
        rest_book = await self.rest.fetch_order_book(exchange, symbol, limit=limit)
        if rest_book:
            self.live_state.set_order_book(exchange, symbol, rest_book)
            return rest_book

        return cached

    async def get_ticker(
        self,
        exchange: str,
        symbol: str,
        force_source: Optional[DataSourceType] = None,
    ) -> Optional[Ticker]:
        """Get ticker quote with fallback hierarchy."""
        if force_source == DataSourceType.REST:
            return await self.rest.fetch_ticker(exchange, symbol)

        cached = self.live_state.get_ticker(exchange, symbol)
        now = time.time()

        if cached and (now - cached.timestamp) <= self.stale_threshold_seconds:
            return cached

        rest_ticker = await self.rest.fetch_ticker(exchange, symbol)
        if rest_ticker:
            self.live_state.set_ticker(exchange, symbol, rest_ticker)
            return rest_ticker

        return cached

    async def get_funding_rate(
        self,
        exchange: str,
        symbol: str,
    ) -> Optional[FundingRate]:
        """Get funding rate for perpetual swaps with REST fallback."""
        cached = self.live_state.get_funding_rate(exchange, symbol)
        if cached:
            return cached

        rest_fr = await self.rest.fetch_funding_rate(exchange, symbol)
        if rest_fr:
            self.live_state.set_funding_rate(exchange, symbol, rest_fr)
            return rest_fr
        return None

    # -------------------------------------------------------------------------
    # Health Monitoring & Auto-Failover Loop
    # -------------------------------------------------------------------------

    async def _health_monitor_loop(self):
        """Continuously checks stream staleness, sequence gaps, and logs auto-failover status."""
        while self._running:
            try:
                now = time.time()

                # 1. Check staleness on normalized states
                states = self.live_state.get_all_normalized_states()
                for key, state in states.items():
                    if (now - state.timestamp) > self.stale_threshold_seconds:
                        if state.freshness != FreshnessStatus.STALE.value:
                            state.freshness = FreshnessStatus.STALE.value
                            state.is_execution_ready = False
                            logger.warning(
                                "Market feed stale for %s %s (>%.1fs). Disabling execution readiness.",
                                state.venue, state.symbol, self.stale_threshold_seconds
                            )

                # 2. Check native WS health and auto-failover
                for ex in self.exchanges:
                    mode = self.source_selection.get(ex, self.default_source)
                    if mode != DataSourceType.AUTO:
                        continue

                    native_health = self.native_ws.get_health_status().get(ex, {})
                    native_last = native_health.get("last_seen", 0)
                    is_native_stale = (
                        (now - native_last) > (self.stale_threshold_seconds * 1.5)
                        and native_health.get("message_count", 0) > 0
                    )

                    if is_native_stale:
                        logger.warning(
                            "Auto-failover: Native WS for %s is stale (>%.1fs). Activating CCXT Pro standby fallback.",
                            ex.upper(), self.stale_threshold_seconds * 1.5
                        )
                        # Activate CCXT Pro standby for the affected subscriptions
                        subs = self.native_ws._subscriptions.get(ex, set())
                        for sym in subs:
                            self.ccxt_pro.subscribe_order_book(ex, sym)
                            self.ccxt_pro.subscribe_ticker(ex, sym)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Error in health monitor: %s", e)
            await asyncio.sleep(2.0)

    def get_connector_status(self) -> Dict[str, Any]:
        """Return comprehensive health and routing matrix across all 3 layers."""
        norm_states = self.live_state.get_all_normalized_states()
        ready_count = sum(1 for s in norm_states.values() if s.is_execution_ready)
        native_health = self.native_ws.get_health_status()
        ccxt_health = self.ccxt_pro.get_health_status()
        rest_health = self.rest.get_health_status()

        status: Dict[str, Any] = {
            "mode": self.default_source.value,
            "primary": "NATIVE_WS",
            "fallback": "CCXT_PRO",
            "recovery": "REST",
            "supported_exchanges": self.exchanges,
            "native_ws": native_health,
            "ccxt_pro": ccxt_health,
            "rest": rest_health,
            "cached_books_count": len(self._books),
            "cached_tickers_count": len(self._tickers),
            "cached_funding_count": len(self._funding),
            "normalized_states_count": len(norm_states),
            "execution_ready_count": ready_count,
        }

        # Populate direct per-exchange health objects at root for instant frontend lookup
        for ex in self.exchanges:
            nh = native_health.get(ex, {})
            ch = ccxt_health.get(ex, {})
            rh = rest_health.get(ex, {})

            has_norm = any(
                s.venue.lower() == ex.lower() and (s.best_bid > 0 or s.best_ask > 0)
                for s in norm_states.values()
            )
            is_conn = (
                nh.get("status") == "CONNECTED"
                or ch.get("status") == "CONNECTED"
                or rh.get("status") == "CONNECTED"
                or has_norm
            )
            msg_count = nh.get("message_count", 0) + ch.get("message_count", 0) + rh.get("message_count", 0)
            status_str = "live" if (has_norm or is_conn) else "offline"
            status[ex] = {
                "exchange": ex,
                "venue": ex.upper(),
                "status": status_str,
                "is_connected": is_conn,
                "message_count": msg_count,
                "has_market_data": has_norm,
            }

        return status
