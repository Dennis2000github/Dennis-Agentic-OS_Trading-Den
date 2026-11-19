"""
Comprehensive unit tests for CCXT Pro Connector, Native WS Connector, REST Connector,
and MarketDataManager multi-source routing and normalization.
"""

import asyncio
import time
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from arbitrage.models import (
    ConnectionStatus,
    DataSourceType,
    FundingRate,
    OHLCV,
    OrderBook,
    OrderBookEntry,
    Ticker,
    Trade,
)
from arbitrage.ccxt_pro_connector import CCXTProConnector
from arbitrage.native_ws_connector import NativeWebSocketConnector
from arbitrage.rest_connector import RESTConnector
from arbitrage.market_data_manager import MarketDataManager


class TestConnectors(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self.pro = CCXTProConnector(exchanges=["binance", "bybit", "okx", "kraken", "mexc"])
        self.native = NativeWebSocketConnector(exchanges=["binance", "bybit", "okx"])
        self.rest = RESTConnector(exchanges=["binance", "bybit", "okx"])
        self.manager = MarketDataManager(
            default_source=DataSourceType.AUTO,
            enabled_exchanges=["binance", "bybit", "okx"],
        )

    async def asyncTearDown(self):
        await self.pro.close()
        await self.native.close()
        await self.rest.close()
        await self.manager.close()

    def test_ccxt_pro_order_book_normalization(self):
        raw_ob = {
            "bids": [[84000.0, 1.5], [83990.0, 2.0]],
            "asks": [[84010.0, 1.2], [84020.0, 3.0]],
            "timestamp": 1720000000000,
        }
        ob = CCXTProConnector.normalize_order_book(raw_ob, "binance", "BTC/USDT")
        self.assertEqual(ob.symbol, "BTC/USDT")
        self.assertEqual(ob.venue, "BINANCE")
        self.assertEqual(ob.source, DataSourceType.CCXT_PRO.value)
        self.assertEqual(ob.best_bid, 84000.0)
        self.assertEqual(ob.best_ask, 84010.0)
        self.assertEqual(ob.mid_price, 84005.0)
        self.assertEqual(len(ob.bids), 2)
        self.assertEqual(len(ob.asks), 2)

    def test_ccxt_pro_ticker_normalization(self):
        raw_ticker = {
            "bid": 84000.0,
            "ask": 84005.0,
            "last": 84002.5,
            "baseVolume": 15000.0,
            "high": 85000.0,
            "low": 83000.0,
            "percentage": 1.25,
            "timestamp": 1720000000000,
        }
        ticker = CCXTProConnector.normalize_ticker(raw_ticker, "bybit", "BTC/USDT")
        self.assertEqual(ticker.venue, "BYBIT")
        self.assertEqual(ticker.bid, 84000.0)
        self.assertEqual(ticker.ask, 84005.0)
        self.assertEqual(ticker.mid, 84002.5)
        self.assertEqual(ticker.spread, 5.0)
        self.assertEqual(ticker.source, DataSourceType.CCXT_PRO.value)

    def test_ccxt_pro_funding_rate_normalization(self):
        raw_fr = {
            "fundingRate": 0.0001,  # 0.01% per 8h
            "nextFundingTimestamp": 1720028800000,
            "interval": 8,
            "predictedFundingRate": 0.00012,
            "timestamp": 1720000000000,
        }
        fr = CCXTProConnector.normalize_funding_rate(raw_fr, "okx", "BTC/USDT")
        self.assertEqual(fr.venue, "OKX")
        self.assertEqual(fr.rate, 0.0001)
        self.assertAlmostEqual(fr.annual_pct, 10.95, places=2)  # 0.01% * 3 * 365 = 10.95%
        self.assertEqual(fr.interval_hours, 8)
        self.assertEqual(fr.predicted_rate, 0.00012)
        self.assertEqual(fr.source, DataSourceType.CCXT_PRO.value)

    def test_ccxt_pro_ohlcv_normalization(self):
        raw_candle = [1720000000000, 84000.0, 84100.0, 83900.0, 84050.0, 120.5]
        candle = CCXTProConnector.normalize_ohlcv(raw_candle, "kraken", "BTC/USDT", "1m")
        self.assertEqual(candle.venue, "KRAKEN")
        self.assertEqual(candle.open, 84000.0)
        self.assertEqual(candle.high, 84100.0)
        self.assertEqual(candle.low, 83900.0)
        self.assertEqual(candle.close, 84050.0)
        self.assertEqual(candle.volume, 120.5)

    def test_native_ws_message_handling(self):
        # Simulate incoming Binance native message
        raw_binance = {
            "bids": [["84000.00", "1.50"]],
            "asks": [["84002.00", "2.10"]],
        }
        received = []
        self.native.on_order_book(lambda ob: received.append(ob))
        self.native._handle_native_message("binance", "BTC/USDT", raw_binance)

        self.assertEqual(len(received), 1)
        ob = received[0]
        self.assertEqual(ob.venue, "BINANCE")
        self.assertEqual(ob.source, DataSourceType.NATIVE_WS.value)
        self.assertEqual(ob.best_bid, 84000.0)
        self.assertEqual(ob.best_ask, 84002.0)

    async def test_market_data_manager_routing_and_fallback(self):
        # Inject fresh mock order book into manager from CCXT Pro
        mock_pro_book = OrderBook(
            symbol="ETH/USDT",
            venue="BINANCE",
            bids=[OrderBookEntry(2600.0, 5.0)],
            asks=[OrderBookEntry(2601.0, 5.0)],
            timestamp=time.time(),
            source=DataSourceType.CCXT_PRO.value,
        )
        self.manager._on_ccxt_pro_book(mock_pro_book)

        # 1. Fetching should return the fresh CCXT Pro book
        book = await self.manager.get_order_book("binance", "ETH/USDT")
        self.assertIsNotNone(book)
        self.assertEqual(book.source, DataSourceType.CCXT_PRO.value)
        self.assertEqual(book.best_bid, 2600.0)

        # 2. Test manual source override to REST
        with patch.object(self.manager.rest, "fetch_order_book", new_callable=AsyncMock) as mock_rest:
            mock_rest.return_value = OrderBook(
                symbol="ETH/USDT",
                venue="BINANCE",
                bids=[OrderBookEntry(2599.0, 1.0)],
                asks=[OrderBookEntry(2602.0, 1.0)],
                timestamp=time.time(),
                source=DataSourceType.REST.value,
            )
            rest_book = await self.manager.get_order_book("binance", "ETH/USDT", force_source=DataSourceType.REST)
            self.assertEqual(rest_book.source, DataSourceType.REST.value)
            self.assertEqual(rest_book.best_bid, 2599.0)

    def test_manager_health_status(self):
        status = self.manager.get_connector_status()
        self.assertIn("mode", status)
        self.assertIn("ccxt_pro", status)
        self.assertIn("native_ws", status)
        self.assertIn("rest", status)
        self.assertIn("binance", status["supported_exchanges"])
        self.assertIn("bybit", status["supported_exchanges"])
        self.assertIn("okx", status["supported_exchanges"])


if __name__ == "__main__":
    unittest.main()
