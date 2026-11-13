"""
CCXT Pro Unified Real-Time Market Data Connector.
Provides unified WebSocket streams for Order Books, Tickers, Trades, OHLCV, and Funding Rates
from Binance, Bybit, OKX, Kraken, and MEXC without requiring API keys.
Normalizes all events into the internal DenMa market data schema.
"""

import asyncio
import logging
import time
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

import ccxt.pro as ccxtpro
import ccxt

from .models import (
    ConnectionStatus,
    ConnectorHealth,
    DataSourceType,
    FundingRate,
    OHLCV,
    OrderBook,
    OrderBookEntry,
    Ticker,
    Trade,
)

logger = logging.getLogger(__name__)


class CCXTProConnector:
    """
    Unified public real-time WebSocket connector powered by CCXT Pro.
    Maintains subscriptions, connection health, reconnection backoff,
    and normalizes all streams into the unified market-data models.
    """

    SUPPORTED_EXCHANGES = ["binance", "bybit", "okx", "kraken", "mexc"]

    # Exchange-specific order book limits accepted by each exchange's WS API
    ORDERBOOK_LIMITS = {
        "binance": 20,
        "bybit": 50,
        "okx": 20,
        "kraken": 25,
        "mexc": 20,
    }

    def __init__(
        self,
        exchanges: Optional[List[str]] = None,
        heartbeat_timeout: float = 15.0,
        max_reconnect_delay: float = 30.0,
    ):
        self.enabled_exchanges = [
            e.lower() for e in (exchanges or self.SUPPORTED_EXCHANGES)
            if e.lower() in self.SUPPORTED_EXCHANGES
        ]
        self.heartbeat_timeout = heartbeat_timeout
        self.max_reconnect_delay = max_reconnect_delay

        self._exchange_instances: Dict[str, Any] = {}
        self._sessions: Dict[str, Any] = {}
        self._running = False
        self._tasks: Dict[str, asyncio.Task] = {}  # key -> Task
        self._subscriptions: Dict[str, Set[Tuple[str, str]]] = {
            e: set() for e in self.enabled_exchanges
        }  # exchange -> {(channel, symbol)}

        # Health & State
        self._health: Dict[str, ConnectorHealth] = {
            e: ConnectorHealth(venue=e.upper(), source=DataSourceType.CCXT_PRO.value)
            for e in self.enabled_exchanges
        }
        self._reconnect_attempts: Dict[str, int] = {e: 0 for e in self.enabled_exchanges}
        self._latest_books: Dict[str, OrderBook] = {}  # "EXCHANGE:SYMBOL" -> OrderBook
        self._latest_tickers: Dict[str, Ticker] = {}  # "EXCHANGE:SYMBOL" -> Ticker
        self._latest_funding: Dict[str, FundingRate] = {}  # "EXCHANGE:SYMBOL" -> FundingRate

        # Event callbacks
        self._callbacks_ob: List[Callable[[OrderBook], None]] = []
        self._callbacks_ticker: List[Callable[[Ticker], None]] = []
        self._callbacks_trade: List[Callable[[Trade], None]] = []
        self._callbacks_funding: List[Callable[[FundingRate], None]] = []

    def _get_or_create_client(self, exchange_id: str) -> Any:
        """Create or return a CCXT Pro client configured for public WS access."""
        exchange_id = exchange_id.lower()
        if exchange_id not in self._exchange_instances:
            if not hasattr(ccxtpro, exchange_id):
                raise ValueError(f"Exchange '{exchange_id}' is not supported by CCXT Pro.")

            client_cls = getattr(ccxtpro, exchange_id)
            config = {
                'enableRateLimit': True,
                'timeout': 10000,
                'newUpdates': True,
                'options': {
                    'defaultType': 'spot',
                },
            }
            try:
                from .dns_resolver import create_robust_session
                session = create_robust_session()
                config['session'] = session
                self._sessions[exchange_id] = session
            except Exception as e:
                logger.debug("Custom session init for CCXT Pro %s: %s", exchange_id, e)
            client = client_cls(config)
            self._exchange_instances[exchange_id] = client
        return self._exchange_instances[exchange_id]

    async def start(self):
        """Start connector and connection monitor loop."""
        self._running = True
        for ex in self.enabled_exchanges:
            self._health[ex].status = ConnectionStatus.CONNECTED
            self._health[ex].last_seen = time.time()
        logger.info("CCXTProConnector started for exchanges: %s", self.enabled_exchanges)

    async def close(self):
        """Cleanly close all CCXT Pro WebSocket connections and cancel tasks."""
        self._running = False
        for task in list(self._tasks.values()):
            task.cancel()
        self._tasks.clear()

        for ex_id, client in list(self._exchange_instances.items()):
            try:
                await client.close()
            except Exception as e:
                logger.debug("Error closing CCXT Pro client %s: %s", ex_id, e)
        self._exchange_instances.clear()

        for ex, session in list(self._sessions.items()):
            try:
                await session.close()
            except Exception:
                pass
        self._sessions.clear()

        for ex in self.enabled_exchanges:
            self._health[ex].status = ConnectionStatus.DISCONNECTED
        logger.info("CCXTProConnector closed.")

    # -------------------------------------------------------------------------
    # Subscription Management
    # -------------------------------------------------------------------------

    def on_order_book(self, callback: Callable[[OrderBook], None]):
        """Register a callback for normalized order book updates."""
        self._callbacks_ob.append(callback)

    def on_ticker(self, callback: Callable[[Ticker], None]):
        """Register a callback for normalized ticker updates."""
        self._callbacks_ticker.append(callback)

    def on_trade(self, callback: Callable[[Trade], None]):
        """Register a callback for normalized trade updates."""
        self._callbacks_trade.append(callback)

    def on_funding_rate(self, callback: Callable[[FundingRate], None]):
        """Register a callback for normalized funding rate updates."""
        self._callbacks_funding.append(callback)

    def subscribe_order_book(self, exchange: str, symbol: str):
        """Subscribe to real-time order book for an exchange and symbol."""
        exchange = exchange.lower()
        if exchange not in self.enabled_exchanges:
            logger.warning("Exchange %s not enabled in CCXT Pro connector", exchange)
            return

        key = f"ob:{exchange}:{symbol}"
        if key in self._tasks and not self._tasks[key].done():
            return  # Already subscribed

        self._subscriptions[exchange].add(("order_book", symbol))
        self._tasks[key] = asyncio.create_task(
            self._stream_order_book(exchange, symbol)
        )
        logger.info("Subscribed to CCXT Pro OrderBook: %s on %s", symbol, exchange.upper())

    def subscribe_ticker(self, exchange: str, symbol: str):
        """Subscribe to real-time ticker quotes for an exchange and symbol."""
        exchange = exchange.lower()
        if exchange not in self.enabled_exchanges:
            return

        key = f"ticker:{exchange}:{symbol}"
        if key in self._tasks and not self._tasks[key].done():
            return

        self._subscriptions[exchange].add(("ticker", symbol))
        self._tasks[key] = asyncio.create_task(
            self._stream_ticker(exchange, symbol)
        )
        logger.info("Subscribed to CCXT Pro Ticker: %s on %s", symbol, exchange.upper())

    def subscribe_funding_rate(self, exchange: str, symbol: str):
        """Subscribe to real-time funding rates (perps) for an exchange and symbol."""
        exchange = exchange.lower()
        if exchange not in self.enabled_exchanges:
            return

        key = f"funding:{exchange}:{symbol}"
        if key in self._tasks and not self._tasks[key].done():
            return

        self._subscriptions[exchange].add(("funding", symbol))
        self._tasks[key] = asyncio.create_task(
            self._stream_funding_rate(exchange, symbol)
        )
        logger.info("Subscribed to CCXT Pro Funding Rate: %s on %s", symbol, exchange.upper())

    # -------------------------------------------------------------------------
    # Stream Consumers with Exponential Backoff
    # -------------------------------------------------------------------------

    async def _stream_order_book(self, exchange: str, symbol: str):
        """Continuous watch_order_book loop with auto-reconnection and exponential backoff."""
        limit = self.ORDERBOOK_LIMITS.get(exchange, 20)
        delay = 1.0

        while self._running:
            try:
                client = self._get_or_create_client(exchange)
                self._health[exchange].status = ConnectionStatus.CONNECTED

                while self._running:
                    t0 = time.time()
                    raw_ob = await client.watch_order_book(symbol, limit=limit)
                    latency = (time.time() - t0) * 1000.0

                    normalized = self.normalize_order_book(raw_ob, exchange, symbol)
                    self._latest_books[f"{exchange.upper()}:{symbol}"] = normalized

                    # Update health stats
                    self._health[exchange].last_seen = time.time()
                    self._health[exchange].latency_ms = round(latency, 2)
                    self._health[exchange].message_count += 1
                    self._reconnect_attempts[exchange] = 0
                    delay = 1.0  # Reset backoff on success

                    # Notify callbacks
                    for cb in self._callbacks_ob:
                        try:
                            cb(normalized)
                        except Exception as e:
                            logger.error("Callback error in order book listener: %s", e)

            except asyncio.CancelledError:
                break
            except Exception as e:
                self._health[exchange].status = ConnectionStatus.RECONNECTING
                self._health[exchange].error_count += 1
                self._health[exchange].error_message = str(e)
                self._reconnect_attempts[exchange] += 1

                backoff = min(self.max_reconnect_delay, delay * 2) + 0.25
                logger.warning(
                    "CCXT Pro OrderBook stream error on %s %s: %s. Reconnecting in %.1fs...",
                    exchange.upper(), symbol, e, backoff
                )
                await asyncio.sleep(backoff)
                delay = backoff

    async def _stream_ticker(self, exchange: str, symbol: str):
        """Continuous watch_ticker loop."""
        delay = 1.0
        while self._running:
            try:
                client = self._get_or_create_client(exchange)
                while self._running:
                    t0 = time.time()
                    raw_ticker = await client.watch_ticker(symbol)
                    latency = (time.time() - t0) * 1000.0

                    normalized = self.normalize_ticker(raw_ticker, exchange, symbol)
                    self._latest_tickers[f"{exchange.upper()}:{symbol}"] = normalized

                    self._health[exchange].last_seen = time.time()
                    self._health[exchange].latency_ms = round(latency, 2)
                    self._health[exchange].message_count += 1
                    delay = 1.0

                    for cb in self._callbacks_ticker:
                        try:
                            cb(normalized)
                        except Exception as e:
                            logger.error("Callback error in ticker listener: %s", e)

            except asyncio.CancelledError:
                break
            except Exception as e:
                backoff = min(self.max_reconnect_delay, delay * 2) + 0.25
                logger.warning("CCXT Pro Ticker stream error on %s %s: %s. Retrying in %.1fs...", exchange.upper(), symbol, e, backoff)
                await asyncio.sleep(backoff)
                delay = backoff

    async def _stream_funding_rate(self, exchange: str, symbol: str):
        """Continuous watch_funding_rate or polling fallback."""
        delay = 1.0
        while self._running:
            try:
                client = self._get_or_create_client(exchange)
                while self._running:
                    if hasattr(client, 'watch_funding_rate'):
                        try:
                            raw_fr = await client.watch_funding_rate(symbol)
                        except Exception:
                            raw_fr = await client.fetch_funding_rate(symbol)
                            await asyncio.sleep(15.0)  # Funding rates change every 8h; poll gently
                    else:
                        raw_fr = await client.fetch_funding_rate(symbol)
                        await asyncio.sleep(15.0)

                    normalized = self.normalize_funding_rate(raw_fr, exchange, symbol)
                    self._latest_funding[f"{exchange.upper()}:{symbol}"] = normalized

                    for cb in self._callbacks_funding:
                        try:
                            cb(normalized)
                        except Exception as e:
                            logger.error("Callback error in funding listener: %s", e)

            except asyncio.CancelledError:
                break
            except Exception as e:
                await asyncio.sleep(min(self.max_reconnect_delay, delay * 2))

    # -------------------------------------------------------------------------
    # Normalizers (Raw CCXT dict -> Internal DenMa Schemas)
    # -------------------------------------------------------------------------

    @classmethod
    def normalize_order_book(cls, raw: dict, exchange: str, symbol: str) -> OrderBook:
        """Transform raw CCXT order book dict into unified OrderBook model."""
        bids = [
            OrderBookEntry(price=float(level[0]), size=float(level[1]))
            for level in raw.get("bids", []) if len(level) >= 2
        ]
        asks = [
            OrderBookEntry(price=float(level[0]), size=float(level[1]))
            for level in raw.get("asks", []) if len(level) >= 2
        ]
        # Sort guarantee: bids descending, asks ascending
        bids.sort(key=lambda x: x.price, reverse=True)
        asks.sort(key=lambda x: x.price)

        ts = raw.get("timestamp")
        timestamp = (float(ts) / 1000.0) if ts else time.time()
        seq = raw.get("nonce") or raw.get("lastUpdateId")

        return OrderBook(
            symbol=symbol,
            venue=exchange.upper(),
            bids=bids,
            asks=asks,
            timestamp=timestamp,
            source=DataSourceType.CCXT_PRO.value,
            sequence=int(seq) if seq is not None else None,
            last_update_id=int(seq) if seq is not None else None,
            freshness="FRESH",
            connection_status=ConnectionStatus.CONNECTED.value,
            is_ready=True,
            is_synchronized=True,
        )

    @classmethod
    def normalize_ticker(cls, raw: dict, exchange: str, symbol: str) -> Ticker:
        """Transform raw CCXT ticker dict into unified Ticker model."""
        ts = raw.get("timestamp")
        timestamp = (float(ts) / 1000.0) if ts else time.time()

        bid = float(raw.get("bid") or 0.0)
        ask = float(raw.get("ask") or 0.0)
        last = float(raw.get("last") or raw.get("close") or ((bid + ask) / 2.0 if bid and ask else 0.0))
        bid_size = float(raw.get("bidVolume") or 0.0)
        ask_size = float(raw.get("askVolume") or 0.0)

        return Ticker(
            symbol=symbol,
            venue=exchange.upper(),
            bid=bid,
            ask=ask,
            last=last,
            bid_size=bid_size,
            ask_size=ask_size,
            volume_24h=float(raw.get("baseVolume") or raw.get("quoteVolume") or 0.0),
            high_24h=float(raw.get("high") or 0.0),
            low_24h=float(raw.get("low") or 0.0),
            change_24h_pct=float(raw.get("percentage") or 0.0),
            timestamp=timestamp,
            source=DataSourceType.CCXT_PRO.value,
            freshness="FRESH",
            connection_status=ConnectionStatus.CONNECTED.value,
            is_ready=True,
            is_synchronized=True,
        )

    @classmethod
    def normalize_trade(cls, raw: dict, exchange: str, symbol: str) -> Trade:
        """Transform raw CCXT trade dict into unified Trade model."""
        ts = raw.get("timestamp")
        timestamp = (float(ts) / 1000.0) if ts else time.time()

        return Trade(
            id=str(raw.get("id") or ""),
            symbol=symbol,
            venue=exchange.upper(),
            side=str(raw.get("side") or "buy").lower(),
            price=float(raw.get("price") or 0.0),
            amount=float(raw.get("amount") or 0.0),
            cost=float(raw.get("cost") or 0.0),
            timestamp=timestamp,
            source=DataSourceType.CCXT_PRO.value,
        )

    @classmethod
    def normalize_ohlcv(cls, raw: list, exchange: str, symbol: str, timeframe: str) -> OHLCV:
        """Transform raw CCXT OHLCV candle [ts, o, h, l, c, v] into unified OHLCV model."""
        ts = float(raw[0]) / 1000.0 if raw[0] > 1e11 else float(raw[0])
        return OHLCV(
            symbol=symbol,
            venue=exchange.upper(),
            timeframe=timeframe,
            timestamp=ts,
            open=float(raw[1]),
            high=float(raw[2]),
            low=float(raw[3]),
            close=float(raw[4]),
            volume=float(raw[5]),
            source=DataSourceType.CCXT_PRO.value,
        )

    @classmethod
    def normalize_funding_rate(cls, raw: dict, exchange: str, symbol: str) -> FundingRate:
        """Transform raw CCXT funding rate dict into unified FundingRate model."""
        ts = raw.get("timestamp")
        timestamp = (float(ts) / 1000.0) if ts else time.time()

        rate = float(raw.get("fundingRate") or 0.0)
        next_ts = raw.get("nextFundingTimestamp")
        next_time = (float(next_ts) / 1000.0) if next_ts else 0.0

        return FundingRate(
            symbol=symbol,
            venue=exchange.upper(),
            rate=rate,
            next_funding_time=next_time,
            interval_hours=int(raw.get("interval") or 8),
            predicted_rate=float(raw["predictedFundingRate"]) if raw.get("predictedFundingRate") is not None else None,
            timestamp=timestamp,
            source=DataSourceType.CCXT_PRO.value,
        )

    # -------------------------------------------------------------------------
    # Getters
    # -------------------------------------------------------------------------

    def get_latest_order_book(self, exchange: str, symbol: str) -> Optional[OrderBook]:
        """Get the cached latest order book for exchange and symbol."""
        return self._latest_books.get(f"{exchange.upper()}:{symbol}")

    def get_latest_ticker(self, exchange: str, symbol: str) -> Optional[Ticker]:
        """Get the cached latest ticker for exchange and symbol."""
        return self._latest_tickers.get(f"{exchange.upper()}:{symbol}")

    def get_latest_funding_rate(self, exchange: str, symbol: str) -> Optional[FundingRate]:
        """Get the cached latest funding rate for exchange and symbol."""
        return self._latest_funding.get(f"{exchange.upper()}:{symbol}")

    def get_health_status(self) -> Dict[str, dict]:
        """Return connectivity and health statistics per exchange."""
        return {
            ex: {
                "venue": h.venue,
                "source": h.source,
                "status": h.status.value,
                "latency_ms": h.latency_ms,
                "message_count": h.message_count,
                "error_count": h.error_count,
                "last_seen": h.last_seen,
                "active_subscriptions": len(self._subscriptions.get(ex, set())),
                "error": h.error_message,
            }
            for ex, h in self._health.items()
        }
