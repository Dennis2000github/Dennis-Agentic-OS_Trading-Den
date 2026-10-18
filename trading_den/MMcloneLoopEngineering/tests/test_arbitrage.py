"""
Unit tests for Arbitrage Models, Spread Calculations, and MT5 safe guards.
"""

import unittest
from arbitrage.models import OrderBook, OrderBookEntry
from arbitrage.spread_calculator import SpreadCalculator
from arbitrage.mt5_client import MT5Client, MT5_AVAILABLE


class TestArbitrageSpread(unittest.TestCase):

    def setUp(self):
        self.calc = SpreadCalculator(default_taker_fee=0.001, min_profit_threshold_pct=0.05)

        # Synthetic order book A: Bid=100.0, Ask=100.5
        self.book_a = OrderBook(
            symbol="BTC/USDT",
            venue="BINANCE",
            bids=[OrderBookEntry(price=100.0, size=2.0), OrderBookEntry(price=99.5, size=5.0)],
            asks=[OrderBookEntry(price=100.5, size=2.0), OrderBookEntry(price=101.0, size=5.0)],
        )

        # Synthetic order book B: Bid=101.5, Ask=102.0
        self.book_b = OrderBook(
            symbol="BTC/USDT",
            venue="BYBIT",
            bids=[OrderBookEntry(price=101.5, size=2.0), OrderBookEntry(price=101.0, size=5.0)],
            asks=[OrderBookEntry(price=102.0, size=2.0), OrderBookEntry(price=102.5, size=5.0)],
        )

    def test_order_book_properties(self):
        self.assertEqual(self.book_a.best_bid, 100.0)
        self.assertEqual(self.book_a.best_ask, 100.5)
        self.assertEqual(self.book_a.mid_price, 100.25)
        self.assertAlmostEqual(self.book_a.internal_spread, 0.5)
        self.assertAlmostEqual(self.book_a.internal_spread_pct, 0.5)

    def test_vwap_calculation(self):
        # Target size 1.0 -> fills completely at top level (100.5)
        vwap, cost, filled = self.book_a.get_vwap('buy', 1.0)
        self.assertTrue(filled)
        self.assertEqual(vwap, 100.5)
        self.assertEqual(cost, 100.5)

        # Target size 3.0 -> consumes 2.0 @ 100.5 and 1.0 @ 101.0
        # Total cost = 201.0 + 101.0 = 302.0 -> vwap = 302.0 / 3.0 = 100.6667
        vwap, cost, filled = self.book_a.get_vwap('buy', 3.0)
        self.assertTrue(filled)
        self.assertAlmostEqual(cost, 302.0)
        self.assertAlmostEqual(vwap, 302.0 / 3.0)

    def test_cross_exchange_spread_profitable(self):
        # Buy on Binance @ 100.5, Sell on Bybit @ 101.5
        # Gross spread = (101.5 - 100.5) / 100.5 * 100 = 0.9950%
        # Fees = 0.2% -> Net spread ~ 0.7950% > min threshold (0.05%)
        spread = self.calc.calculate_cross_exchange_spread(self.book_a, self.book_b)
        self.assertIsNotNone(spread)
        self.assertEqual(spread.best_route, "BINANCE->BYBIT")
        self.assertAlmostEqual(spread.spread_a_to_b, 1.0)
        self.assertAlmostEqual(spread.gross_spread_pct, 0.995, places=3)
        self.assertTrue(spread.is_profitable)

    def test_triangular_arbitrage(self):
        # Setup profitable loop:
        # BTC/USDT: Ask=100.0, Bid=99.9
        # ETH/BTC:  Ask=0.05,  Bid=0.049
        # ETH/USDT: Ask=5.5,   Bid=5.4
        # 1 USDT -> Buy BTC @ 100 => 0.01 BTC
        # 0.01 BTC -> Buy ETH @ 0.05 => 0.2 ETH
        # 0.2 ETH -> Sell for USDT @ 5.4 => 1.08 USDT (Gross: +8%)
        book_btc_usdt = OrderBook(
            symbol="BTC/USDT", venue="BINANCE",
            bids=[OrderBookEntry(99.9, 10.0)], asks=[OrderBookEntry(100.0, 10.0)]
        )
        book_eth_btc = OrderBook(
            symbol="ETH/BTC", venue="BINANCE",
            bids=[OrderBookEntry(0.049, 100.0)], asks=[OrderBookEntry(0.05, 100.0)]
        )
        book_eth_usdt = OrderBook(
            symbol="ETH/USDT", venue="BINANCE",
            bids=[OrderBookEntry(5.4, 1000.0)], asks=[OrderBookEntry(5.5, 1000.0)]
        )

        opps = self.calc.calculate_triangular_arbitrage(
            venue="BINANCE",
            book_ab=book_btc_usdt,
            book_cb=book_eth_usdt,
            book_ca=book_eth_btc,
        )
        self.assertEqual(len(opps), 2)
        forward_loop = opps[0]
        self.assertTrue(forward_loop.gross_profit_pct > 0)
        self.assertTrue(forward_loop.is_profitable)

    def test_mt5_safe_guard_on_mac(self):
        client = MT5Client(mock_if_unavailable=False)
        # On macOS, MT5_AVAILABLE should be False unless running on Windows
        if not MT5_AVAILABLE:
            self.assertFalse(client.initialize())
            self.assertIsNone(client.get_order_book("BTCUSD"))

        # In mock mode, it gracefully returns simulated order books for local dev
        mock_client = MT5Client(mock_if_unavailable=True)
        self.assertTrue(mock_client.initialize())
        book = mock_client.get_order_book("BTCUSD")
        self.assertIsNotNone(book)
        self.assertEqual(book.symbol, "BTCUSD")
        self.assertTrue(len(book.bids) > 0)
        self.assertTrue(len(book.asks) > 0)

    def test_bollinger_bands_empty_and_single_point(self):
        # Empty history
        res_empty = self.calc.calculate_bollinger_bands([], k=2.0, period=20, min_width_pct=0.001)
        self.assertEqual(res_empty["mean"], 0.0)
        self.assertEqual(res_empty["upper"], 0.001)
        self.assertEqual(res_empty["lower"], -0.001)
        self.assertEqual(res_empty["sd"], 0.0)
        self.assertEqual(res_empty["bandwidth"], 0.002)
        self.assertEqual(res_empty["pct_b"], 0.5)

        # Single point: variance should be 0, half width should be clamped to min_width_pct
        res_single = self.calc.calculate_bollinger_bands([0.05], k=2.0, period=20, min_width_pct=0.001)
        self.assertAlmostEqual(res_single["mean"], 0.05)
        self.assertAlmostEqual(res_single["upper"], 0.051)
        self.assertAlmostEqual(res_single["lower"], 0.049)
        self.assertAlmostEqual(res_single["sd"], 0.0)

    def test_bollinger_bands_exact_calculation(self):
        # 5 samples: [1.0, 2.0, 3.0, 4.0, 5.0]
        # mean = 3.0
        # sum of sq diffs = (4 + 1 + 0 + 1 + 4) = 10
        # sample variance (N-1 = 4) = 10 / 4 = 2.5
        # sample std = sqrt(2.5) ~= 1.58113883
        # k = 2.0 => half_w = 2 * 1.58113883 = 3.16227766
        # upper = 3.0 + 3.162278 = 6.162278
        # lower = 3.0 - 3.162278 = -0.162278
        # bandwidth = 6.324555
        # %b = (5.0 - (-0.162278)) / 6.324555 = 5.162278 / 6.324555 = 0.816228
        history = [1.0, 2.0, 3.0, 4.0, 5.0]
        res = self.calc.calculate_bollinger_bands(history, k=2.0, period=5, min_width_pct=0.001)

        self.assertAlmostEqual(res["mean"], 3.0, places=4)
        self.assertAlmostEqual(res["sd"], 2.5 ** 0.5, places=4)
        self.assertAlmostEqual(res["upper"], 3.0 + 2.0 * (2.5 ** 0.5), places=4)
        self.assertAlmostEqual(res["lower"], 3.0 - 2.0 * (2.5 ** 0.5), places=4)
        self.assertAlmostEqual(res["bandwidth"], 4.0 * (2.5 ** 0.5), places=4)
        self.assertAlmostEqual(res["pct_b"], (5.0 - res["lower"]) / res["bandwidth"], places=4)

    def test_bollinger_strategy_signals(self):
        history = [0.01, 0.02, 0.015, 0.018, 0.012]
        bands = self.calc.calculate_bollinger_bands(history, k=2.0, period=5)

        # Spread below lower band -> Buy signal (direction = -1)
        res_buy = self.calc.evaluate_bollinger_strategy(bands["lower"] - 0.005, history, k=2.0, period=5)
        self.assertTrue(res_buy["should_enter"])
        self.assertEqual(res_buy["direction"], -1)

        # Spread above upper band -> Sell signal (direction = +1)
        res_sell = self.calc.evaluate_bollinger_strategy(bands["upper"] + 0.005, history, k=2.0, period=5)
        self.assertTrue(res_sell["should_enter"])
        self.assertEqual(res_sell["direction"], 1)

        # Spread inside bands -> No entry
        res_neutral = self.calc.evaluate_bollinger_strategy(bands["mean"], history, k=2.0, period=5)
        self.assertFalse(res_neutral["should_enter"])
        self.assertEqual(res_neutral["direction"], 0)


if __name__ == "__main__":
    unittest.main()

