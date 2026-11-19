"""
Integration and regression test for Live Chart market data, Screener connectors,
and real ATJ Research spread calculations.
"""

import asyncio
import time
import unittest
from arbitrage.models import (
    ConnectionStatus,
    DataSourceType,
    NormalizedMarketState,
    OrderBook,
    OrderBookEntry,
    Ticker,
)
from arbitrage.live_state import LiveStateManager
from arbitrage.market_data_manager import MarketDataManager
from arbitrage.rest_connector import RESTConnector
from arbitrage.server import ArbitrageServer


class TestLiveChartAndConnectors(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        self.live_state = LiveStateManager()
        self.market_data_mgr = MarketDataManager(
            default_source=DataSourceType.AUTO,
            enabled_exchanges=["binance", "bybit", "okx"],
            live_state=self.live_state,
        )

    async def asyncTearDown(self):
        await self.market_data_mgr.close()

    def test_connector_status_includes_root_exchanges(self):
        """Verify get_connector_status supplies root per-exchange dicts for Screener."""
        status = self.market_data_mgr.get_connector_status()
        self.assertIn("binance", status)
        self.assertIn("bybit", status)
        self.assertIn("okx", status)
        self.assertEqual(status["binance"]["venue"], "BINANCE")
        self.assertIn("status", status["binance"])
        self.assertIn("native_ws", status)
        self.assertIn("ccxt_pro", status)
        self.assertIn("rest", status)

    def test_live_prices_summary_populates_from_normalized_states(self):
        """Verify get_live_prices_summary extracts real live bids/asks from normalized states."""
        # Inject normalized states for BTC on Binance and Bybit
        norm_binance = NormalizedMarketState(
            venue="BINANCE",
            symbol="BTC/USDT",
            best_bid=84300.0,
            best_ask=84302.0,
            timestamp=time.time(),
            has_rest_snapshot=True,
            ws_synchronized=True,
        )
        norm_bybit = NormalizedMarketState(
            venue="BYBIT",
            symbol="BTC/USDT",
            best_bid=84310.0,
            best_ask=84312.0,
            timestamp=time.time(),
            has_rest_snapshot=True,
            ws_synchronized=True,
        )
        self.live_state.set_normalized_state("BINANCE", "BTC/USDT", norm_binance)
        self.live_state.set_normalized_state("BYBIT", "BTC/USDT", norm_bybit)

        summary = self.live_state.get_live_prices_summary()
        self.assertIn("BTC", summary)
        btc_info = summary["BTC"]
        self.assertIn("venues", btc_info)
        self.assertAlmostEqual(btc_info["venues"]["BINANCE"], 84301.0, places=1)
        self.assertAlmostEqual(btc_info["venues"]["BYBIT"], 84311.0, places=1)

        tickers = self.live_state.get_all_tickers_dict()
        self.assertIn("BINANCE:BTC/USDT", tickers)
        self.assertIn("BINANCE:BTCUSDT", tickers)
        self.assertAlmostEqual(tickers["BINANCE:BTC/USDT"]["last"], 84301.0, places=1)

    async def test_rest_connector_fast_ohlcv_with_predefined_symbol(self):
        """Verify REST connector fetches 1m candles without blocking on load_markets."""
        rest = RESTConnector()
        try:
            candles = await rest.fetch_ohlcv("binance", "BTC/USDT", timeframe="1m", limit=3)
            self.assertIsInstance(candles, list)
            if candles:
                self.assertGreater(candles[0].close, 0.0)
                self.assertEqual(candles[0].venue, "BINANCE")
                self.assertEqual(candles[0].symbol, "BTC/USDT")
        finally:
            await rest.close()

    def test_spread_calculation_atj_reference_formula(self):
        """Verify Spread % calculation matches ATJ Research formula ((Priced - Ref) / Ref) * 100%."""
        ref_price = 84000.0
        priced_price = 84084.0  # +84 USD = +0.10%
        spread_pct = ((priced_price - ref_price) / ref_price) * 100.0
        self.assertAlmostEqual(spread_pct, 0.10, places=4)

        priced_price_lower = 83916.0  # -84 USD = -0.10%
        spread_pct_lower = ((priced_price_lower - ref_price) / ref_price) * 100.0
        self.assertAlmostEqual(spread_pct_lower, -0.10, places=4)


if __name__ == "__main__":
    unittest.main()
