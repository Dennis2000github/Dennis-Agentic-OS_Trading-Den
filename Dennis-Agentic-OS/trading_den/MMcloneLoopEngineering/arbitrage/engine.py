"""
Unified Arbitrage & Market Making Engine.
Orchestrates live order book fetching across Crypto exchanges and optional MT5,
evaluates arbitrage spreads, and outputs real-time opportunities.
Seamlessly interfaces with CCXT Pro, Native WebSockets, and REST connectors.
"""

import asyncio
import logging
from typing import Dict, List, Optional, Tuple

from .models import (
    DataSourceType,
    FundingRate,
    OrderBook,
    SpreadResult,
    TriangularOpportunity,
)
from .crypto_client import CryptoClient
from .market_data_manager import MarketDataManager
from .mt5_client import MT5Client, MT5_AVAILABLE
from .spread_calculator import SpreadCalculator

logger = logging.getLogger(__name__)


class ArbitrageEngine:
    """
    Unified Engine coordinating live crypto order books, MT5 feeds (with Mac-friendly fallback),
    and arbitrage spread calculations across CCXT Pro, Native WebSockets, and REST.
    """

    def __init__(
        self,
        crypto_exchanges: Optional[List[str]] = None,
        mt5_mock_on_mac: bool = False,
        taker_fee: float = 0.001,
        min_profit_threshold: float = 0.05,
        market_data_manager: Optional[MarketDataManager] = None,
        data_source: DataSourceType = DataSourceType.AUTO,
    ):
        self.exchanges = crypto_exchanges or ["binance", "bybit", "okx", "kraken", "mexc"]
        self.crypto_client = CryptoClient(exchanges=self.exchanges)
        self.market_data_mgr = market_data_manager or MarketDataManager(
            default_source=data_source,
            enabled_exchanges=self.exchanges,
        )
        self.mt5_client = MT5Client(mock_if_unavailable=mt5_mock_on_mac)
        self.spread_calculator = SpreadCalculator(
            default_taker_fee=taker_fee,
            min_profit_threshold_pct=min_profit_threshold,
        )
        self.is_mt5_enabled = False

    async def start(self) -> None:
        """Start underlying clients and MT5 connection if applicable."""
        await self.market_data_mgr.start()

        if MT5_AVAILABLE:
            self.is_mt5_enabled = self.mt5_client.initialize()
        elif self.mt5_client.mock_if_unavailable:
            self.is_mt5_enabled = self.mt5_client.initialize()
            logger.info("Operating with simulated MT5 feed for local Mac testing.")
        else:
            logger.info("MT5 disabled. Running in pure crypto mode.")

    async def stop(self) -> None:
        """Cleanup network connections and close clients."""
        await self.market_data_mgr.close()
        await self.crypto_client.close()
        if self.is_mt5_enabled:
            self.mt5_client.shutdown()

    async def get_cross_exchange_spread(
        self,
        symbol: str,
        venues: Tuple[str, str] = ("binance", "bybit"),
        target_size: Optional[float] = None,
        source: Optional[DataSourceType] = None,
        *,
        reference_venue: Optional[str] = None,
        priced_venue: Optional[str] = None,
    ) -> Optional[SpreadResult]:
        """
        Fetch order books from Reference Venue and Priced Venue and calculate spread.
        Either venue dynamically becomes First Leg / Maker or Second Leg / Hedge depending
        on pricing.
        """
        ref_v = reference_venue or venues[0]
        priced_v = priced_venue or venues[1]
        book_ref, book_priced = await asyncio.gather(
            self.market_data_mgr.get_order_book(ref_v, symbol, force_source=source),
            self.market_data_mgr.get_order_book(priced_v, symbol, force_source=source),
            return_exceptions=True,
        )

        # Fallback to direct crypto_client if manager encountered unexpected error
        if isinstance(book_ref, Exception) or not book_ref:
            book_ref = await self.crypto_client.fetch_order_book(ref_v, symbol)
        if isinstance(book_priced, Exception) or not book_priced:
            book_priced = await self.crypto_client.fetch_order_book(priced_v, symbol)

        if not book_ref or not book_priced:
            logger.warning("Could not fetch books for both %s and %s", ref_v, priced_v)
            return None

        return self.spread_calculator.calculate_cross_exchange_spread(
            reference_book=book_ref,
            priced_book=book_priced,
            target_size=target_size,
        )

    async def get_triangular_arbitrage(
        self,
        venue: str = "binance",
        pair_ab: str = "BTC/USDT",
        pair_cb: str = "ETH/USDT",
        pair_ca: str = "ETH/BTC",
        source: Optional[DataSourceType] = None,
    ) -> List[TriangularOpportunity]:
        """
        Fetch triangular trio order books and calculate loop profitability.
        """
        bk_ab, bk_cb, bk_ca = await asyncio.gather(
            self.market_data_mgr.get_order_book(venue, pair_ab, force_source=source),
            self.market_data_mgr.get_order_book(venue, pair_cb, force_source=source),
            self.market_data_mgr.get_order_book(venue, pair_ca, force_source=source),
            return_exceptions=True,
        )

        if any(isinstance(b, Exception) or b is None for b in (bk_ab, bk_cb, bk_ca)):
            # Fallback to batch REST
            tasks = [(venue, pair_ab), (venue, pair_cb), (venue, pair_ca)]
            books = await self.crypto_client.fetch_order_books_batch(tasks)
            bk_ab = books.get(f"{venue.upper()}:{pair_ab}")
            bk_cb = books.get(f"{venue.upper()}:{pair_cb}")
            bk_ca = books.get(f"{venue.upper()}:{pair_ca}")

        if not (bk_ab and bk_cb and bk_ca):
            logger.warning("Missing books for triangular arbitrage on %s", venue)
            return []

        return self.spread_calculator.calculate_triangular_arbitrage(
            venue=venue.upper(),
            book_ab=bk_ab,
            book_cb=bk_cb,
            book_ca=bk_ca,
        )

    async def get_funding_arbitrage(
        self,
        symbol: str = "BTC/USDT",
        venue_long: str = "binance",
        venue_short: str = "bybit",
    ) -> Optional[Dict[str, Any]]:
        """
        Calculate funding rate arbitrage between two perpetual swap venues.
        Long on low/negative funding venue, Short on high positive funding venue.
        """
        fr_long = await self.market_data_mgr.get_funding_rate(venue_long, symbol)
        fr_short = await self.market_data_mgr.get_funding_rate(venue_short, symbol)

        if not fr_long or not fr_short:
            return None

        # Net 8h yield = rate_short - rate_long
        diff_8h = fr_short.rate - fr_long.rate
        net_annual_pct = diff_8h * 3 * 365 * 100.0

        return {
            "symbol": symbol,
            "venue_long": venue_long.upper(),
            "rate_long": fr_long.rate,
            "annual_long": round(fr_long.annual_pct, 2),
            "venue_short": venue_short.upper(),
            "rate_short": fr_short.rate,
            "annual_short": round(fr_short.annual_pct, 2),
            "net_rate_8h": round(diff_8h, 6),
            "net_annual_yield_pct": round(net_annual_pct, 2),
            "profitable": net_annual_pct > 5.0,  # e.g. > 5% APY
        }

    async def get_mt5_vs_crypto_spread(
        self,
        crypto_exchange: str = "binance",
        crypto_symbol: str = "BTC/USDT",
        mt5_symbol: str = "BTCUSD",
        fx_conversion: float = 1.0,
    ) -> Optional[Dict[str, float]]:
        """
        Compare crypto exchange order book against MT5 symbol.
        Safe on Mac: if MT5 is disabled/unavailable, returns None gracefully.
        """
        crypto_book = await self.market_data_mgr.get_order_book(crypto_exchange, crypto_symbol)
        if not crypto_book:
            crypto_book = await self.crypto_client.fetch_order_book(crypto_exchange, crypto_symbol)
        if not crypto_book:
            return None

        mt5_book = self.mt5_client.get_order_book(mt5_symbol)
        if not mt5_book:
            return None

        return self.spread_calculator.calculate_mt5_crypto_spread(
            crypto_book=crypto_book,
            mt5_book=mt5_book,
            fx_conversion_rate=fx_conversion,
        )
