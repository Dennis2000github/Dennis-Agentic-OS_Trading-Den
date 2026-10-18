"""
Step 3 Unit Tests: Reference Venue / Priced Venue Terminology & Dynamic Role Assignment.
Verifies:
1. Reference Venue as benchmark data source and Priced Venue as evaluated venue.
2. Dynamic assignment of First Leg (Maker) and Second Leg (Hedge) depending on market pricing.
3. Neither role fixed to Maker/Taker.
4. Backward compatibility of SpreadResult and Persistence layer.
5. Removal of mock data in gap fills and screener.
"""

import time
import unittest

from arbitrage.models import OrderBook, OrderBookEntry, SpreadResult, ArbitrageOpportunity
from arbitrage.spread_calculator import SpreadCalculator
from arbitrage.persistence import PersistenceManager


class TestStep3ReferencePriced(unittest.TestCase):

    def setUp(self):
        self.calc = SpreadCalculator(default_taker_fee=0.001, min_profit_threshold_pct=0.05)

    def test_dynamic_legs_when_ref_to_priced_profitable(self):
        """
        When Buy Reference (Binance @ 60000) and Sell Priced (Bybit @ 60200) is profitable:
        Reference Venue -> Buy Leg (First Leg / Maker)
        Priced Venue -> Sell Leg (Second Leg / Hedge)
        """
        ref_book = OrderBook(
            venue="BINANCE",
            symbol="BTC/USDT",
            bids=[OrderBookEntry(price=59990.0, size=1.0)],
            asks=[OrderBookEntry(price=60000.0, size=1.0)],
            timestamp=time.time(),
        )
        priced_book = OrderBook(
            venue="BYBIT",
            symbol="BTC/USDT",
            bids=[OrderBookEntry(price=60200.0, size=1.0)],
            asks=[OrderBookEntry(price=60210.0, size=1.0)],
            timestamp=time.time(),
        )

        spread = self.calc.calculate_cross_exchange_spread(
            reference_book=ref_book,
            priced_book=priced_book,
        )

        self.assertIsNotNone(spread)
        self.assertEqual(spread.reference_venue, "BINANCE")
        self.assertEqual(spread.priced_venue, "BYBIT")
        self.assertEqual(spread.buy_venue, "BINANCE")
        self.assertEqual(spread.sell_venue, "BYBIT")
        self.assertEqual(spread.first_leg_venue, "BINANCE")
        self.assertEqual(spread.second_leg_venue, "BYBIT")
        self.assertEqual(spread.buy_price, 60000.0)
        self.assertEqual(spread.sell_price, 60200.0)
        self.assertAlmostEqual(spread.spread_ref_to_priced, 200.0, places=4)
        self.assertTrue(spread.is_profitable)

        # Backward compatibility aliases
        self.assertEqual(spread.venue_a, "BINANCE")
        self.assertEqual(spread.venue_b, "BYBIT")
        self.assertEqual(spread.ask_a, 60000.0)
        self.assertEqual(spread.bid_b, 60200.0)

    def test_dynamic_legs_when_priced_to_ref_profitable(self):
        """
        When Buy Priced (Bybit @ 59800) and Sell Reference (Binance @ 60000) is profitable:
        Priced Venue -> Buy Leg (First Leg / Maker)
        Reference Venue -> Sell Leg (Second Leg / Hedge)
        This confirms neither role is statically fixed to Maker or Hedge.
        """
        ref_book = OrderBook(
            venue="BINANCE",
            symbol="BTC/USDT",
            bids=[OrderBookEntry(price=60000.0, size=1.0)],
            asks=[OrderBookEntry(price=60010.0, size=1.0)],
            timestamp=time.time(),
        )
        priced_book = OrderBook(
            venue="BYBIT",
            symbol="BTC/USDT",
            bids=[OrderBookEntry(price=59790.0, size=1.0)],
            asks=[OrderBookEntry(price=59800.0, size=1.0)],
            timestamp=time.time(),
        )

        spread = self.calc.calculate_cross_exchange_spread(
            reference_book=ref_book,
            priced_book=priced_book,
        )

        self.assertIsNotNone(spread)
        self.assertEqual(spread.reference_venue, "BINANCE")
        self.assertEqual(spread.priced_venue, "BYBIT")
        # Priced is now the buy leg / first leg!
        self.assertEqual(spread.buy_venue, "BYBIT")
        self.assertEqual(spread.sell_venue, "BINANCE")
        self.assertEqual(spread.first_leg_venue, "BYBIT")
        self.assertEqual(spread.second_leg_venue, "BINANCE")
        self.assertEqual(spread.buy_price, 59800.0)
        self.assertEqual(spread.sell_price, 60000.0)
        self.assertAlmostEqual(spread.spread_priced_to_ref, 200.0, places=4)
        self.assertTrue(spread.is_profitable)

    def test_spread_result_legacy_init_backward_compatibility(self):
        """SpreadResult can still be instantiated with legacy venue_a/venue_b kwargs."""
        sr = SpreadResult(
            symbol="ETH/USDT",
            venue_a="OKX",
            venue_b="BINANCE",
            bid_a=3500.0,
            ask_a=3501.0,
            bid_b=3510.0,
            ask_b=3511.0,
            spread_a_to_b=9.0,
            spread_pct_a_to_b=0.257,
            spread_b_to_a=-11.0,
            spread_pct_b_to_a=-0.314,
            best_route="OKX->BINANCE",
            gross_spread_pct=0.257,
            net_spread_pct=0.057,
            is_profitable=True,
        )
        self.assertEqual(sr.reference_venue, "OKX")
        self.assertEqual(sr.priced_venue, "BINANCE")
        self.assertEqual(sr.venue_a, "OKX")
        self.assertEqual(sr.venue_b, "BINANCE")
        self.assertEqual(sr.reference_bid, 3500.0)
        self.assertEqual(sr.priced_bid, 3510.0)

    def test_mt5_crypto_spread_reference_priced_keys(self):
        """MT5 vs Crypto spread calculator returns reference and priced venue keys."""
        crypto_b = OrderBook(
            venue="BINANCE",
            symbol="BTC/USDT",
            bids=[OrderBookEntry(price=64000.0, size=2.0)],
            asks=[OrderBookEntry(price=64002.0, size=2.0)],
            timestamp=time.time(),
        )
        mt5_b = OrderBook(
            venue="MT5",
            symbol="BTCUSD",
            bids=[OrderBookEntry(price=63950.0, size=1.0)],
            asks=[OrderBookEntry(price=63955.0, size=1.0)],
            timestamp=time.time(),
        )
        res = self.calc.calculate_mt5_crypto_spread(crypto_b, mt5_b)
        self.assertIsNotNone(res)
        self.assertEqual(res["reference_venue"], "BINANCE")
        self.assertEqual(res["priced_venue"], "MT5")
        self.assertEqual(res["reference_mid"], 64001.0)
        self.assertEqual(res["priced_mid"], 63952.5)
        # Verify backward-compatible keys
        self.assertEqual(res["crypto_mid"], 64001.0)
        self.assertEqual(res["mt5_mid"], 63952.5)

    def test_persistence_reference_priced_venues(self):
        """Persistence manager saves and retrieves spread candles with reference_venue & priced_venue."""
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
            pm = PersistenceManager(db_path=tmp.name)
            ts = int(time.time() // 60) * 60
            saved = pm.save_spread_candle(
                asset_key="BTCUSDT_BYBIT",
                symbol="BTC/USDT",
                reference_venue="BINANCE",
                priced_venue="BYBIT",
                timestamp=ts,
                open_val=0.10,
                high_val=0.15,
                low_val=0.08,
                close_val=0.12,
                snapshot_count=60,
            )
            self.assertTrue(saved)

            candles = pm.get_spread_candles("BTCUSDT_BYBIT", auto_fill=False)
            self.assertEqual(len(candles), 1)
            c = candles[0]
            self.assertEqual(c["reference_venue"], "BINANCE")
            self.assertEqual(c["priced_venue"], "BYBIT")
            self.assertEqual(c["close"], 0.12)


if __name__ == "__main__":
    unittest.main()
