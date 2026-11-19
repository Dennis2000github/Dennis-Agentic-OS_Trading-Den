"""
Native Exchange WebSocket Connector (Fallback & Low-Level Data Feed).
Maintains direct WebSocket sessions with Binance, Bybit, OKX, Kraken, and MEXC.
Normalizes all native exchange payloads into the same internal DenMa models.
"""

import asyncio
import json
import logging
import time
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

import aiohttp

from .models import (
    ConnectionStatus,
    ConnectorHealth,
    DataSourceType,
    OrderBook,
    OrderBookEntry,
    Ticker,
    Trade,
)

logger = logging.getLogger(__name__)


class NativeWebSocketConnector:
    """
    Direct native WebSocket connector for Binance, Bybit, OKX, Kraken, MEXC.
    Acts as an independent fallback or lower-level stream provider.
    """

    SUPPORTED_EXCHANGES = ["binance", "bybit", "okx", "kraken", "mexc"]

    WS_ENDPOINTS = {
        "binance": "wss://stream.binance.com:9443/ws",
        "bybit": "wss://stream.bybit.com/v5/public/spot",
        "okx": "wss://ws.okx.com:8443/ws/v5/public",
        "kraken": "wss://ws.kraken.com",
        "mexc": "wss://wbs.mexc.com/ws",
    }

    def __init__(
        self,
        exchanges: Optional[List[str]] = None,
        heartbeat_interval: float = 20.0,
        max_reconnect_delay: float = 30.0,
    ):
        self.enabled_exchanges = [
            e.lower() for e in (exchanges or self.SUPPORTED_EXCHANGES)
            if e.lower() in self.SUPPORTED_EXCHANGES
        ]
        self.heartbeat_interval = heartbeat_interval
        self.max_reconnect_delay = max_reconnect_delay

        self._running = False
        self._session: Optional[aiohttp.ClientSession] = None
        self._ws_connections: Dict[str, aiohttp.ClientWebSocketResponse] = {}
        self._tasks: Dict[str, asyncio.Task] = {}
        self._subscriptions: Dict[str, Set[str]] = {e: set() for e in self.enabled_exchanges}

        self._health: Dict[str, ConnectorHealth] = {
            e: ConnectorHealth(venue=e.upper(), source=DataSourceType.NATIVE_WS.value)
            for e in self.enabled_exchanges
        }
        self._latest_books: Dict[str, OrderBook] = {}
        self._latest_tickers: Dict[str, Ticker] = {}
        self._last_sequence: Dict[str, int] = {}
        self._missed_sequences: Dict[str, int] = {}

        self._callbacks_ob: List[Callable[[OrderBook], None]] = []
        self._callbacks_ticker: List[Callable[[Ticker], None]] = []

    async def start(self):
        """Start native WS session."""
        from .dns_resolver import create_robust_session
        self._running = True
        self._session = create_robust_session()
        for ex in self.enabled_exchanges:
            self._health[ex].status = ConnectionStatus.CONNECTED
            self._health[ex].last_seen = time.time()
        logger.info("NativeWebSocketConnector started for %s", self.enabled_exchanges)

    async def close(self):
        """Close all native WebSockets."""
        self._running = False
        for task in list(self._tasks.values()):
            task.cancel()
        self._tasks.clear()

        for ws in list(self._ws_connections.values()):
            try:
                await ws.close()
            except Exception:
                pass
        self._ws_connections.clear()

        if self._session:
            await self._session.close()
            self._session = None

        for ex in self.enabled_exchanges:
            self._health[ex].status = ConnectionStatus.DISCONNECTED
        logger.info("NativeWebSocketConnector closed.")

    def on_order_book(self, cb: Callable[[OrderBook], None]):
        self._callbacks_ob.append(cb)

    def on_ticker(self, cb: Callable[[Ticker], None]):
        self._callbacks_ticker.append(cb)

    def subscribe_order_book(self, exchange: str, symbol: str):
        """Subscribe to order book on native WS feed."""
        exchange = exchange.lower()
        if exchange not in self.enabled_exchanges:
            return

        key = f"native_ob:{exchange}:{symbol}"
        if key in self._tasks and not self._tasks[key].done():
            return

        self._subscriptions[exchange].add(symbol)
        self._tasks[key] = asyncio.create_task(
            self._run_native_stream(exchange, symbol)
        )
        logger.info("Subscribed to Native WS OrderBook: %s on %s", symbol, exchange.upper())

    def subscribe_ticker(self, exchange: str, symbol: str):
        """Subscribe to ticker quote on native WS feed."""
        # Native stream multiplexes ticker and depth on same connection
        self.subscribe_order_book(exchange, symbol)

    async def _run_native_stream(self, exchange: str, symbol: str):
        """Main loop for native WebSocket connection with auto-reconnect."""
        delay = 1.0
        conn_key = f"{exchange}:{symbol}"
        while self._running:
            try:
                endpoint = self.WS_ENDPOINTS.get(exchange)
                if not endpoint or not self._session or self._session.closed:
                    self._session = aiohttp.ClientSession()

                # Build stream URL or subscribe message depending on exchange
                url = endpoint
                clean_sym = symbol.replace("/", "").lower()
                clean_sym_upper = symbol.replace("/", "").upper()

                if exchange == "binance":
                    url = f"{endpoint}/{clean_sym}@depth20@100ms/{clean_sym}@ticker"

                async with self._session.ws_connect(url, heartbeat=self.heartbeat_interval) as ws:
                    self._ws_connections[conn_key] = ws
                    self._health[exchange].status = ConnectionStatus.CONNECTED
                    self._health[exchange].last_seen = time.time()
                    delay = 1.0

                    # Send exchange-specific subscription messages
                    if exchange == "bybit":
                        sub_msg = {
                            "op": "subscribe",
                            "args": [f"orderbook.50.{clean_sym_upper}", f"tickers.{clean_sym_upper}"]
                        }
                        await ws.send_str(json.dumps(sub_msg))
                    elif exchange == "okx":
                        inst_id = symbol.replace("/", "-").upper()
                        sub_msg = {
                            "op": "subscribe",
                            "args": [
                                {"channel": "books5", "instId": inst_id},
                                {"channel": "tickers", "instId": inst_id}
                            ]
                        }
                        await ws.send_str(json.dumps(sub_msg))
                    elif exchange == "kraken":
                        sub_msg = {
                            "event": "subscribe",
                            "pair": [symbol],
                            "subscription": {"name": "book", "depth": 25}
                        }
                        await ws.send_str(json.dumps(sub_msg))

                    async for msg in ws:
                        if not self._running:
                            break
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            self._handle_native_message(exchange, symbol, json.loads(msg.data))
                        elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                            break

            except asyncio.CancelledError:
                break
            except Exception as e:
                self._health[exchange].status = ConnectionStatus.RECONNECTING
                self._health[exchange].error_count += 1
                self._health[exchange].error_message = str(e)
                backoff = min(self.max_reconnect_delay, delay * 2) + 0.25
                logger.warning("Native WS stream error on %s %s: %s. Reconnecting in %.1fs...", exchange.upper(), symbol, e, backoff)
                await asyncio.sleep(backoff)
                delay = backoff

    def _track_sequence(self, venue: str, symbol: str, seq: Optional[int]):
        """Detect gaps in sequence numbers and log warnings."""
        if seq is None:
            return
        key = f"{venue.upper()}:{symbol}"
        last_seq = self._last_sequence.get(key)
        if last_seq is not None and seq > last_seq + 1:
            missed = seq - last_seq - 1
            self._missed_sequences[key] = self._missed_sequences.get(key, 0) + missed
            logger.warning(
                "Sequence gap detected on %s %s: expected %d, received %d (missed %d)",
                venue.upper(), symbol, last_seq + 1, seq, missed
            )
        self._last_sequence[key] = seq

    def get_sequence_info(self, venue: str, symbol: str) -> Dict[str, Any]:
        """Return sequence information for a venue-symbol stream."""
        key = f"{venue.upper()}:{symbol}"
        return {
            "last_sequence": self._last_sequence.get(key),
            "missed_count": self._missed_sequences.get(key, 0),
        }

    def _handle_native_message(self, exchange: str, symbol: str, data: Any):
        """Parse raw native JSON into unified schemas and trigger listeners."""
        self._health[exchange].last_seen = time.time()
        self._health[exchange].message_count += 1

        try:
            # 1. Binance depth & ticker
            if exchange == "binance":
                if "bids" in data and "asks" in data:
                    seq = data.get("lastUpdateId") or data.get("u")
                    self._track_sequence("binance", symbol, seq)
                    bids = [OrderBookEntry(price=float(p), size=float(s)) for p, s in data["bids"]]
                    asks = [OrderBookEntry(price=float(p), size=float(s)) for p, s in data["asks"]]
                    bids.sort(key=lambda x: x.price, reverse=True)
                    asks.sort(key=lambda x: x.price)
                    ob = OrderBook(
                        symbol=symbol,
                        venue="BINANCE",
                        bids=bids,
                        asks=asks,
                        timestamp=time.time(),
                        source=DataSourceType.NATIVE_WS.value,
                        sequence=seq,
                        last_update_id=seq,
                        freshness="FRESH",
                        connection_status="CONNECTED",
                        is_ready=True,
                        is_synchronized=True,
                    )
                    if not ob.is_valid():
                        logger.warning("Rejecting invalid OrderBook received on BINANCE %s", symbol)
                        return
                    self._latest_books[f"BINANCE:{symbol}"] = ob
                    for cb in self._callbacks_ob:
                        cb(ob)
                elif "c" in data and "b" in data:  # Binance 24h ticker
                    seq = data.get("u") or data.get("E")
                    self._track_sequence("binance", symbol, seq)
                    bid_size = float(data.get("B") or 0.0)
                    ask_size = float(data.get("A") or 0.0)
                    t = Ticker(
                        symbol=symbol,
                        venue="BINANCE",
                        bid=float(data.get("b") or 0.0),
                        ask=float(data.get("a") or 0.0),
                        last=float(data.get("c") or 0.0),
                        bid_size=bid_size,
                        ask_size=ask_size,
                        volume_24h=float(data.get("v") or 0.0),
                        high_24h=float(data.get("h") or 0.0),
                        low_24h=float(data.get("l") or 0.0),
                        timestamp=time.time(),
                        source=DataSourceType.NATIVE_WS.value,
                        sequence=seq,
                        freshness="FRESH",
                        connection_status="CONNECTED",
                        is_ready=True,
                        is_synchronized=True,
                    )
                    if not t.is_valid():
                        logger.warning("Rejecting invalid Ticker received on BINANCE %s", symbol)
                        return
                    self._latest_tickers[f"BINANCE:{symbol}"] = t
                    for cb in self._callbacks_ticker:
                        cb(t)

            # 2. Bybit orderbook & ticker
            elif exchange == "bybit":
                topic = data.get("topic", "")
                res = data.get("data", {})
                if "orderbook" in topic and isinstance(res, dict):
                    seq = res.get("seq") or res.get("u") or (int(data.get("ts")) if data.get("ts") else None)
                    self._track_sequence("bybit", symbol, seq)
                    bids = [OrderBookEntry(price=float(p), size=float(s)) for p, s in res.get("b", [])]
                    asks = [OrderBookEntry(price=float(p), size=float(s)) for p, s in res.get("a", [])]
                    if bids and asks:
                        bids.sort(key=lambda x: x.price, reverse=True)
                        asks.sort(key=lambda x: x.price)
                        ob = OrderBook(
                            symbol=symbol,
                            venue="BYBIT",
                            bids=bids,
                            asks=asks,
                            timestamp=time.time(),
                            source=DataSourceType.NATIVE_WS.value,
                            sequence=seq,
                            last_update_id=seq,
                            freshness="FRESH",
                            connection_status="CONNECTED",
                            is_ready=True,
                            is_synchronized=True,
                        )
                        if not ob.is_valid():
                            logger.warning("Rejecting invalid OrderBook received on BYBIT %s", symbol)
                            return
                        self._latest_books[f"BYBIT:{symbol}"] = ob
                        for cb in self._callbacks_ob:
                            cb(ob)
                elif "tickers" in topic and isinstance(res, dict):
                    seq = res.get("seq") or (int(data.get("ts")) if data.get("ts") else None)
                    self._track_sequence("bybit", symbol, seq)
                    bid_size = float(res.get("bid1Size") or 0.0)
                    ask_size = float(res.get("ask1Size") or 0.0)
                    t = Ticker(
                        symbol=symbol,
                        venue="BYBIT",
                        bid=float(res.get("bid1Price") or 0.0),
                        ask=float(res.get("ask1Price") or 0.0),
                        last=float(res.get("lastPrice") or 0.0),
                        bid_size=bid_size,
                        ask_size=ask_size,
                        volume_24h=float(res.get("volume24h") or 0.0),
                        high_24h=float(res.get("highPrice24h") or 0.0),
                        low_24h=float(res.get("lowPrice24h") or 0.0),
                        timestamp=time.time(),
                        source=DataSourceType.NATIVE_WS.value,
                        sequence=seq,
                        freshness="FRESH",
                        connection_status="CONNECTED",
                        is_ready=True,
                        is_synchronized=True,
                    )
                    if not t.is_valid():
                        logger.warning("Rejecting invalid Ticker received on BYBIT %s", symbol)
                        return
                    self._latest_tickers[f"BYBIT:{symbol}"] = t
                    for cb in self._callbacks_ticker:
                        cb(t)

            # 3. OKX orderbook & ticker
            elif exchange == "okx":
                arg = data.get("arg", {})
                channel = arg.get("channel")
                rows = data.get("data", [])
                if rows:
                    row = rows[0]
                    if channel == "books5":
                        seq = int(row.get("seqId") or row.get("ts") or 0) or None
                        self._track_sequence("okx", symbol, seq)
                        bids = [OrderBookEntry(price=float(p), size=float(s)) for p, s, *_ in row.get("bids", [])]
                        asks = [OrderBookEntry(price=float(p), size=float(s)) for p, s, *_ in row.get("asks", [])]
                        ob = OrderBook(
                            symbol=symbol,
                            venue="OKX",
                            bids=bids,
                            asks=asks,
                            timestamp=time.time(),
                            source=DataSourceType.NATIVE_WS.value,
                            sequence=seq,
                            last_update_id=seq,
                            freshness="FRESH",
                            connection_status="CONNECTED",
                            is_ready=True,
                            is_synchronized=True,
                        )
                        if not ob.is_valid():
                            logger.warning("Rejecting invalid OrderBook received on OKX %s", symbol)
                            return
                        self._latest_books[f"OKX:{symbol}"] = ob
                        for cb in self._callbacks_ob:
                            cb(ob)
                    elif channel == "tickers":
                        seq = int(row.get("ts") or 0) or None
                        self._track_sequence("okx", symbol, seq)
                        bid_size = float(row.get("bidSz") or 0.0)
                        ask_size = float(row.get("askSz") or 0.0)
                        t = Ticker(
                            symbol=symbol,
                            venue="OKX",
                            bid=float(row.get("bidPx") or 0.0),
                            ask=float(row.get("askPx") or 0.0),
                            last=float(row.get("last") or 0.0),
                            bid_size=bid_size,
                            ask_size=ask_size,
                            volume_24h=float(row.get("vol24h") or 0.0),
                            high_24h=float(row.get("high24h") or 0.0),
                            low_24h=float(row.get("low24h") or 0.0),
                            timestamp=time.time(),
                            source=DataSourceType.NATIVE_WS.value,
                            sequence=seq,
                            freshness="FRESH",
                            connection_status="CONNECTED",
                            is_ready=True,
                            is_synchronized=True,
                        )
                        if not t.is_valid():
                            logger.warning("Rejecting invalid Ticker received on OKX %s", symbol)
                            return
                        self._latest_tickers[f"OKX:{symbol}"] = t
                        for cb in self._callbacks_ticker:
                            cb(t)

        except Exception as e:
            logger.debug("Error parsing native WS message on %s: %s", exchange, e)

    def get_latest_order_book(self, exchange: str, symbol: str) -> Optional[OrderBook]:
        return self._latest_books.get(f"{exchange.upper()}:{symbol}")

    def get_latest_ticker(self, exchange: str, symbol: str) -> Optional[Ticker]:
        return self._latest_tickers.get(f"{exchange.upper()}:{symbol}")

    def get_health_status(self) -> Dict[str, dict]:
        return {
            ex: {
                "venue": h.venue,
                "source": h.source,
                "status": h.status.value,
                "message_count": h.message_count,
                "error_count": h.error_count,
                "last_seen": h.last_seen,
                "error": h.error_message,
            }
            for ex, h in self._health.items()
        }
