"""
REST Connector for Snapshots, Metadata, Historical Data, and Recovery Fallback.
Provides unified REST access to Binance, Bybit, OKX, Kraken, and MEXC,
normalizing all responses into the same internal DenMa schemas.
"""

import asyncio
import logging
import time
from typing import Any, Dict, List, Optional

import ccxt.async_support as ccxt_async

from .models import (
    ConnectionStatus,
    ConnectorHealth,
    DataSourceType,
    FundingRate,
    OHLCV,
    OrderBook,
    OrderBookEntry,
    Ticker,
)

logger = logging.getLogger(__name__)


class RESTConnector:
    """
    Unified REST connector handling initial snapshots, exchange metadata,
    and reliable fallback polling when WebSockets are reconnecting.
    """

    SUPPORTED_EXCHANGES = ["binance", "bybit", "okx", "kraken", "mexc"]

    def __init__(
        self,
        exchanges: Optional[List[str]] = None,
        timeout: float = 6.0,
    ):
        self.enabled_exchanges = [
            e.lower() for e in (exchanges or self.SUPPORTED_EXCHANGES)
            if e.lower() in self.SUPPORTED_EXCHANGES
        ]
        self.timeout = timeout
        self._clients: Dict[str, Any] = {}
        self._health: Dict[str, ConnectorHealth] = {
            e: ConnectorHealth(venue=e.upper(), source=DataSourceType.REST.value)
            for e in self.enabled_exchanges
        }
        self._markets_cache: Dict[str, dict] = {}
        self._sessions: Dict[str, Any] = {}

    def _get_client(self, exchange: str) -> Any:
        exchange = exchange.lower()
        if exchange not in self._clients:
            if not hasattr(ccxt_async, exchange):
                raise ValueError(f"Exchange {exchange} not supported by CCXT")
            cls = getattr(ccxt_async, exchange)
            config = {
                'enableRateLimit': True,
                'timeout': int(self.timeout * 1000),
                'options': {'defaultType': 'spot'},
            }
            try:
                from .dns_resolver import create_robust_session
                session = create_robust_session()
                config['session'] = session
                self._sessions[exchange] = session
            except Exception as e:
                logger.debug("Custom session init for %s: %s", exchange, e)
            self._clients[exchange] = cls(config)
        return self._clients[exchange]

    async def close(self):
        """Close opened CCXT client sessions."""
        for ex, client in list(self._clients.items()):
            try:
                await client.close()
            except Exception:
                pass
        self._clients.clear()
        for ex, session in list(self._sessions.items()):
            try:
                await session.close()
            except Exception:
                pass
        self._sessions.clear()

    def _ensure_symbol_market(self, client: Any, symbol: str):
        """Ensure market definition exists for the requested symbol so CCXT doesn't block loading all markets."""
        if not client.markets or symbol not in client.markets:
            if not client.markets:
                client.markets = {}
            parts = symbol.split("/")
            base = parts[0] if len(parts) > 1 else symbol.replace("USDT", "")
            quote = parts[1] if len(parts) > 1 else "USDT"
            clean_id = symbol.replace("/", "")
            client.markets[symbol] = {
                'id': clean_id,
                'symbol': symbol,
                'base': base,
                'quote': quote,
                'spot': True,
                'active': True,
                'precision': {'amount': 8, 'price': 8},
                'limits': {'amount': {'min': 0.0001, 'max': None}, 'price': {'min': 0.0001, 'max': None}},
            }

    async def fetch_order_book(
        self,
        exchange: str,
        symbol: str,
        limit: int = 20,
    ) -> Optional[OrderBook]:
        """Fetch snapshot of order book via REST."""
        exchange = exchange.lower()
        t0 = time.time()
        try:
            client = self._get_client(exchange)
            self._ensure_symbol_market(client, symbol)
            raw = await client.fetch_order_book(symbol, limit=limit)
            latency = (time.time() - t0) * 1000.0

            bids = [OrderBookEntry(price=float(level[0]), size=float(level[1])) for level in raw.get("bids", [])[:limit] if len(level) >= 2]
            asks = [OrderBookEntry(price=float(level[0]), size=float(level[1])) for level in raw.get("asks", [])[:limit] if len(level) >= 2]
            bids.sort(key=lambda x: x.price, reverse=True)
            asks.sort(key=lambda x: x.price)

            seq = raw.get("nonce") or raw.get("lastUpdateId")
            ob = OrderBook(
                symbol=symbol,
                venue=exchange.upper(),
                bids=bids,
                asks=asks,
                timestamp=float(raw.get("timestamp") or (time.time() * 1000)) / 1000.0,
                source=DataSourceType.REST.value,
                sequence=int(seq) if seq is not None else None,
                last_update_id=int(seq) if seq is not None else None,
                freshness="FRESH",
                connection_status=ConnectionStatus.CONNECTED.value,
                is_ready=True,
                is_synchronized=False,
            )

            self._health[exchange].status = ConnectionStatus.CONNECTED
            self._health[exchange].last_seen = time.time()
            self._health[exchange].latency_ms = round(latency, 2)
            self._health[exchange].message_count += 1
            return ob

        except Exception as e:
            self._health[exchange].error_count += 1
            self._health[exchange].error_message = str(e)
            logger.debug("REST fetch_order_book failed on %s %s: %s", exchange, symbol, e)
            return None

    async def fetch_ticker(
        self,
        exchange: str,
        symbol: str,
    ) -> Optional[Ticker]:
        """Fetch latest ticker via REST."""
        exchange = exchange.lower()
        t0 = time.time()
        try:
            client = self._get_client(exchange)
            self._ensure_symbol_market(client, symbol)
            raw = await client.fetch_ticker(symbol)
            latency = (time.time() - t0) * 1000.0

            bid = float(raw.get("bid") or 0.0)
            ask = float(raw.get("ask") or 0.0)
            last = float(raw.get("last") or raw.get("close") or 0.0)
            bid_size = float(raw.get("bidVolume") or 0.0)
            ask_size = float(raw.get("askVolume") or 0.0)

            t = Ticker(
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
                timestamp=float(raw.get("timestamp") or (time.time() * 1000)) / 1000.0,
                source=DataSourceType.REST.value,
                freshness="FRESH",
                connection_status=ConnectionStatus.CONNECTED.value,
                is_ready=True,
                is_synchronized=False,
            )

            self._health[exchange].status = ConnectionStatus.CONNECTED
            self._health[exchange].last_seen = time.time()
            self._health[exchange].latency_ms = round(latency, 2)
            self._health[exchange].message_count += 1
            return t

        except Exception as e:
            self._health[exchange].error_count += 1
            self._health[exchange].error_message = str(e)
            logger.debug("REST fetch_ticker failed on %s %s: %s", exchange, symbol, e)
            return None

    async def fetch_funding_rate(
        self,
        exchange: str,
        symbol: str,
    ) -> Optional[FundingRate]:
        """Fetch latest funding rate for perpetual swaps."""
        exchange = exchange.lower()
        try:
            client = self._get_client(exchange)
            self._ensure_symbol_market(client, symbol)
            if hasattr(client, "fetch_funding_rate"):
                raw = await client.fetch_funding_rate(symbol)
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
                    timestamp=float(raw.get("timestamp") or (time.time() * 1000)) / 1000.0,
                    source=DataSourceType.REST.value,
                )
        except Exception as e:
            logger.debug("REST fetch_funding_rate failed on %s %s: %s", exchange, symbol, e)
        return None

    async def fetch_ohlcv(
        self,
        exchange: str,
        symbol: str,
        timeframe: str = "1m",
        limit: int = 100,
    ) -> List[OHLCV]:
        """Fetch historical candlestick bars with automatic pagination when limit > 1000."""
        exchange = exchange.lower()
        try:
            client = self._get_client(exchange)
            self._ensure_symbol_market(client, symbol)
            first_batch_limit = min(limit, 1000)
            raw_candles = await client.fetch_ohlcv(symbol, timeframe=timeframe, limit=first_batch_limit)
            if limit > 1000 and raw_candles and len(raw_candles) >= 900:
                remaining = limit - len(raw_candles)
                earliest_ms = int(raw_candles[0][0])
                interval_ms = 60 * 1000 if timeframe == "1m" else (3600 * 1000 if timeframe == "1h" else 86400 * 1000)
                since_ms = earliest_ms - (remaining * interval_ms)
                try:
                    earlier_candles = await client.fetch_ohlcv(symbol, timeframe=timeframe, since=since_ms, limit=remaining)
                    if earlier_candles:
                        seen = set()
                        merged = []
                        for c in (earlier_candles + raw_candles):
                            if c[0] not in seen:
                                seen.add(c[0])
                                merged.append(c)
                        merged.sort(key=lambda x: x[0])
                        raw_candles = merged[-limit:]
                except Exception as ex_page:
                    logger.debug("Pagination fetch failed for %s %s: %s", exchange, symbol, ex_page)
            return [
                OHLCV(
                    symbol=symbol,
                    venue=exchange.upper(),
                    timeframe=timeframe,
                    timestamp=float(c[0]) / 1000.0,
                    open=float(c[1]),
                    high=float(c[2]),
                    low=float(c[3]),
                    close=float(c[4]),
                    volume=float(c[5]),
                    source=DataSourceType.REST.value,
                )
                for c in raw_candles
            ]
        except Exception as e:
            logger.debug("REST fetch_ohlcv failed on %s %s: %s", exchange, symbol, e)
            return []

    async def fetch_metadata(self, exchange: str) -> dict:
        """Fetch exchange metadata (fees, precision, active pairs)."""
        exchange = exchange.lower()
        if exchange in self._markets_cache:
            return self._markets_cache[exchange]

        try:
            client = self._get_client(exchange)
            markets = await client.load_markets()
            meta = {
                "symbols_count": len(markets),
                "has_fetch_order_book": client.has.get("fetchOrderBook", False),
                "has_fetch_ticker": client.has.get("fetchTicker", False),
                "has_fetch_funding_rate": client.has.get("fetchFundingRate", False),
                "has_fetch_ohlcv": client.has.get("fetchOHLCV", False),
            }
            self._markets_cache[exchange] = meta
            return meta
        except Exception as e:
            logger.debug("REST load_markets failed on %s: %s", exchange, e)
            return {}

    def get_health_status(self) -> Dict[str, dict]:
        return {
            ex: {
                "venue": h.venue,
                "source": h.source,
                "status": h.status.value,
                "latency_ms": h.latency_ms,
                "message_count": h.message_count,
                "error_count": h.error_count,
                "last_seen": h.last_seen,
                "error": h.error_message,
            }
            for ex, h in self._health.items()
        }
