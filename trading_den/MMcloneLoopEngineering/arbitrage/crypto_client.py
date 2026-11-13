"""
Crypto Exchange Order Book Fetcher.
Supports concurrent fetching across exchanges (Binance, Bybit, OKX, Kraken, etc.)
using CCXT async or direct REST fallbacks.
"""

import asyncio
import logging
import time
from typing import Any, Dict, List, Optional, Tuple

from .models import OrderBook, OrderBookEntry

logger = logging.getLogger(__name__)

try:
    import ccxt.async_support as ccxt
    CCXT_AVAILABLE = True
except (ImportError, Exception):
    ccxt = None
    CCXT_AVAILABLE = False

try:
    import aiohttp
    AIOHTTP_AVAILABLE = True
except (ImportError, Exception):
    aiohttp = None
    AIOHTTP_AVAILABLE = False


class CryptoClient:
    """
    Asynchronous client for fetching order books across major cryptocurrency exchanges.
    """

    SUPPORTED_EXCHANGES = ["binance", "bybit", "okx", "kraken", "gateio"]

    # Direct public REST endpoints for resilient fallback without CCXT
    FALLBACK_ENDPOINTS = {
        "binance": "https://api.binance.com/api/v3/depth?symbol={symbol}&limit={limit}",
        "bybit": "https://api.bybit.com/v5/market/orderbook?category=spot&symbol={symbol}&limit={limit}",
        "okx": "https://www.okx.com/api/v5/market/books?instId={symbol}&sz={limit}",
    }

    def __init__(self, exchanges: Optional[List[str]] = None, timeout: float = 5.0):
        self.requested_exchanges = exchanges or self.SUPPORTED_EXCHANGES
        self.timeout = timeout
        self.exchange_instances: Dict[str, Any] = {}

    async def _get_or_init_ccxt(self, exchange_id: str):
        if not CCXT_AVAILABLE:
            return None
        exchange_id = exchange_id.lower()
        if exchange_id not in self.exchange_instances:
            if hasattr(ccxt, exchange_id):
                exchange_class = getattr(ccxt, exchange_id)
                instance = exchange_class({
                    'enableRateLimit': True,
                    'timeout': int(self.timeout * 1000),
                })
                self.exchange_instances[exchange_id] = instance
        return self.exchange_instances.get(exchange_id)

    async def close(self):
        """Close any opened CCXT async exchange sessions."""
        for ex in self.exchange_instances.values():
            try:
                await ex.close()
            except Exception as e:
                logger.debug("Error closing exchange: %s", e)
        self.exchange_instances.clear()

    async def fetch_order_book(
        self,
        exchange: str,
        symbol: str,
        limit: int = 20,
    ) -> Optional[OrderBook]:
        """
        Fetch order book for a given exchange and symbol.
        Normalizes outputs into an OrderBook instance.
        """
        exchange = exchange.lower()
        ccxt_instance = await self._get_or_init_ccxt(exchange)

        if ccxt_instance:
            try:
                ob = await ccxt_instance.fetch_order_book(symbol, limit=limit)
                bids = [OrderBookEntry(price=float(p), size=float(s)) for p, s in ob.get('bids', [])[:limit]]
                asks = [OrderBookEntry(price=float(p), size=float(s)) for p, s in ob.get('asks', [])[:limit]]
                return OrderBook(
                    symbol=symbol,
                    venue=exchange.upper(),
                    bids=bids,
                    asks=asks,
                    timestamp=float(ob.get('timestamp', time.time() * 1000)) / 1000.0,
                )
            except Exception as e:
                logger.debug("CCXT fetch failed for %s on %s: %s. Trying REST fallback.", symbol, exchange, e)

        # Fallback to direct asynchronous REST call if CCXT fails or is unavailable
        return await self._fetch_order_book_rest_fallback(exchange, symbol, limit)

    def _fetch_rest_sync(self, url: str) -> Optional[dict]:
        import urllib.request
        import json
        try:
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "Mozilla/5.0 (compatible; ArbitrageEngine/1.0)"}
            )
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                if resp.status == 200:
                    return json.loads(resp.read().decode())
        except Exception as e:
            logger.debug("Sync urllib fetch error: %s", e)
        return None

    async def _fetch_order_book_rest_fallback(
        self,
        exchange: str,
        symbol: str,
        limit: int = 20,
    ) -> Optional[OrderBook]:
        """Direct lightweight HTTP fallback for common exchanges."""
        # Clean symbol for specific exchanges (e.g. BTC/USDT -> BTCUSDT for Binance)
        raw_symbol = symbol.replace("/", "").replace("-", "").upper()
        okx_symbol = symbol.replace("/", "-").upper()

        url = None
        if exchange == "binance":
            url = self.FALLBACK_ENDPOINTS["binance"].format(symbol=raw_symbol, limit=limit)
        elif exchange == "bybit":
            url = self.FALLBACK_ENDPOINTS["bybit"].format(symbol=raw_symbol, limit=limit)
        elif exchange == "okx":
            url = self.FALLBACK_ENDPOINTS["okx"].format(symbol=okx_symbol, limit=limit)

        if not url:
            return None

        data = None
        if AIOHTTP_AVAILABLE:
            try:
                async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=self.timeout)) as session:
                    async with session.get(url) as resp:
                        if resp.status == 200:
                            data = await resp.json()
            except Exception as e:
                logger.debug("aiohttp fetch error: %s, falling back to urllib", e)

        if data is None:
            # Resilient zero-dependency fallback via standard library urllib
            data = await asyncio.to_thread(self._fetch_rest_sync, url)

        if not data:
            return None

        try:
            bids, asks = [], []
            if exchange == "binance":
                bids = [OrderBookEntry(price=float(p), size=float(s)) for p, s in data.get('bids', [])[:limit]]
                asks = [OrderBookEntry(price=float(p), size=float(s)) for p, s in data.get('asks', [])[:limit]]
            elif exchange == "bybit":
                result = data.get('result', {})
                bids = [OrderBookEntry(price=float(p), size=float(s)) for p, s in result.get('b', [])[:limit]]
                asks = [OrderBookEntry(price=float(p), size=float(s)) for p, s in result.get('a', [])[:limit]]
            elif exchange == "okx":
                result = (data.get('data') or [{}])[0]
                bids = [OrderBookEntry(price=float(p[0]), size=float(p[1])) for p in result.get('bids', [])[:limit]]
                asks = [OrderBookEntry(price=float(p[0]), size=float(p[1])) for p in result.get('asks', [])[:limit]]

            return OrderBook(
                symbol=symbol,
                venue=exchange.upper(),
                bids=bids,
                asks=asks,
                timestamp=time.time(),
            )
        except Exception as e:
            logger.error("REST parse error for %s on %s: %s", symbol, exchange, e)
            return None

    async def fetch_order_books_batch(
        self,
        targets: List[Tuple[str, str]],
        limit: int = 20,
    ) -> Dict[str, OrderBook]:
        """
        Fetch multiple order books concurrently across exchanges.
        :param targets: list of (exchange, symbol) tuples, e.g. [('binance', 'BTC/USDT'), ('bybit', 'BTC/USDT')]
        :return: dict mapping "EXCHANGE:SYMBOL" -> OrderBook
        """
        async def _fetch(ex: str, sym: str):
            book = await self.fetch_order_book(ex, sym, limit=limit)
            return f"{ex.upper()}:{sym}", book

        results = await asyncio.gather(*[_fetch(ex, sym) for ex, sym in targets], return_exceptions=True)
        books: Dict[str, OrderBook] = {}
        for res in results:
            if isinstance(res, tuple) and res[1] is not None:
                books[res[0]] = res[1]
        return books
