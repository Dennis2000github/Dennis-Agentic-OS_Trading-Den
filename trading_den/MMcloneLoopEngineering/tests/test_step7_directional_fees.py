"""
Tests for Step 7: Directional Maker/Taker Fees.

Requirements:
- Reuse existing directional fee engine (FeeEngine).
- For Candidate A (A Maker -> B Taker): calculate A Maker Fee + B Taker Fee.
- For Candidate B (B Maker -> A Taker): calculate B Maker Fee + A Taker Fee.
- Account for exchange, account, instrument, market type, fee tier.
- Raw Spread remains strictly fee-independent.
- Centralize fee logic without duplication.
- No order placement or exit changes.
"""

import time
import unittest

from arbitrage.direction_engine import DirectionEngine
from arbitrage.fee_engine import DEFAULT_EXCHANGE_FEES, FeeEngine
from arbitrage.models import (
    DirectionEvaluationResult,
    DirectionalFeeResult,
    NormalizedMarketState,
    OrderBook,
    OrderBookEntry,
    RawSpreadResult,
)
from arbitrage.spread_calculator import SpreadCalculator


class TestStep7DirectionalFees(unittest.TestCase):
    def setUp(self):
        self.fee_engine = FeeEngine()
        self.calculator = SpreadCalculator()
        self.direction_engine = DirectionEngine(fee_engine=self.fee_engine)

    def test_default_exchange_fee_schedules_loaded(self):
        """Verify standard exchanges (Binance, Bybit, OKX, Kraken, MEXC, KuCoin, Coinbase) are defined."""
        exchanges = ["BINANCE", "BYBIT", "OKX", "KRAKEN", "MEXC", "KUCOIN", "COINBASE"]
        for ex in exchanges:
            self.assertIn(ex, DEFAULT_EXCHANGE_FEES)
            maker, taker = self.fee_engine.get_fee_rates(ex, market_type="spot", fee_tier="standard")
            self.assertGreaterEqual(maker, 0.0)
            self.assertGreater(taker, 0.0)

    def test_candidate_a_and_b_directional_fee_calculation(self):
        """
        Verify the directional fee formula:
        - Candidate A (A Maker -> B Taker): A Maker Fee + B Taker Fee
        - Candidate B (B Maker -> A Taker): B Maker Fee + A Taker Fee
        """
        # Binance (0.10% maker, 0.10% taker) vs Bybit (0.10% maker, 0.10% taker)
        res = self.fee_engine.calculate_directional_fees(
            venue_a="BINANCE",
            venue_b="BYBIT",
            symbol="BTC/USDT",
            market_type="spot",
            fee_tier_a="standard",
            fee_tier_b="standard",
        )
        self.assertIsInstance(res, DirectionalFeeResult)
        self.assertEqual(res.venue_a, "BINANCE")
        self.assertEqual(res.venue_b, "BYBIT")
        self.assertEqual(res.candidate_a_maker_venue, "BINANCE")
        self.assertEqual(res.candidate_a_taker_venue, "BYBIT")
        self.assertAlmostEqual(res.candidate_a_maker_fee, 0.0010)
        self.assertAlmostEqual(res.candidate_a_taker_fee, 0.0010)
        self.assertAlmostEqual(res.candidate_a_total_fee, 0.0020)
        self.assertAlmostEqual(res.candidate_a_total_fee_pct, 0.20)

        self.assertEqual(res.candidate_b_maker_venue, "BYBIT")
        self.assertEqual(res.candidate_b_taker_venue, "BINANCE")
        self.assertAlmostEqual(res.candidate_b_maker_fee, 0.0010)
        self.assertAlmostEqual(res.candidate_b_taker_fee, 0.0010)
        self.assertAlmostEqual(res.candidate_b_total_fee, 0.0020)
        self.assertAlmostEqual(res.candidate_b_total_fee_pct, 0.20)

    def test_asymmetric_directional_fees_mexc_vs_okx(self):
        """
        Asymmetric Fee Test:
        MEXC spot: 0.00% maker, 0.10% taker
        OKX spot: 0.08% maker, 0.10% taker

        Candidate A (MEXC Maker -> OKX Taker): 0.00% + 0.10% = 0.10%
        Candidate B (OKX Maker -> MEXC Taker): 0.08% + 0.10% = 0.18%
        """
        res = self.fee_engine.calculate_directional_fees(
            venue_a="MEXC",
            venue_b="OKX",
            symbol="BTC/USDT",
            market_type="spot",
            fee_tier_a="standard",
            fee_tier_b="standard",
        )
        self.assertEqual(res.candidate_a_maker_venue, "MEXC")
        self.assertEqual(res.candidate_a_taker_venue, "OKX")
        self.assertAlmostEqual(res.candidate_a_maker_fee, 0.0000)
        self.assertAlmostEqual(res.candidate_a_taker_fee, 0.0010)
        self.assertAlmostEqual(res.candidate_a_total_fee_pct, 0.10)

        self.assertEqual(res.candidate_b_maker_venue, "OKX")
        self.assertEqual(res.candidate_b_taker_venue, "MEXC")
        self.assertAlmostEqual(res.candidate_b_maker_fee, 0.0008)
        self.assertAlmostEqual(res.candidate_b_taker_fee, 0.0010)
        self.assertAlmostEqual(res.candidate_b_total_fee_pct, 0.18)

        # Proves directional asymmetry: Candidate A saves 0.08% (8 bps) in trading fees!
        fee_diff = res.candidate_b_total_fee_pct - res.candidate_a_total_fee_pct
        self.assertAlmostEqual(fee_diff, 0.08)

    def test_market_type_perp_vs_spot(self):
        """
        Perpetuals / Futures fee schedules:
        Binance perp: 0.02% maker, 0.05% taker
        Bybit perp: 0.02% maker, 0.055% taker
        """
        res_spot = self.fee_engine.calculate_directional_fees(
            venue_a="BINANCE",
            venue_b="BYBIT",
            market_type="spot",
        )
        res_perp = self.fee_engine.calculate_directional_fees(
            venue_a="BINANCE",
            venue_b="BYBIT",
            market_type="perp",
        )

        # Spot fees: 0.10% + 0.10% = 0.20%
        self.assertAlmostEqual(res_spot.candidate_a_total_fee_pct, 0.20)

        # Perp fees: Candidate A (Binance Maker 0.02% + Bybit Taker 0.055%) = 0.075%
        self.assertAlmostEqual(res_perp.candidate_a_maker_fee, 0.0002)
        self.assertAlmostEqual(res_perp.candidate_a_taker_fee, 0.00055)
        self.assertAlmostEqual(res_perp.candidate_a_total_fee_pct, 0.075)

        # Perp fees: Candidate B (Bybit Maker 0.02% + Binance Taker 0.05%) = 0.070%
        self.assertAlmostEqual(res_perp.candidate_b_maker_fee, 0.0002)
        self.assertAlmostEqual(res_perp.candidate_b_taker_fee, 0.0005)
        self.assertAlmostEqual(res_perp.candidate_b_total_fee_pct, 0.070)

    def test_vip_and_custom_tier_overrides(self):
        """Custom fee schedules and VIP tiers correctly apply."""
        # VIP1 on Bybit spot: 0.04% maker, 0.06% taker
        maker, taker = self.fee_engine.get_fee_rates("BYBIT", market_type="spot", fee_tier="vip1")
        self.assertAlmostEqual(maker, 0.0004)
        self.assertAlmostEqual(taker, 0.0006)

        # Set custom fee override
        self.fee_engine.set_custom_fee("KRAKEN", maker_fee=0.0005, taker_fee=0.0015, market_type="spot", fee_tier="custom")
        c_maker, c_taker = self.fee_engine.get_fee_rates("KRAKEN", market_type="spot", fee_tier="custom")
        self.assertAlmostEqual(c_maker, 0.0005)
        self.assertAlmostEqual(c_taker, 0.0015)

    def test_raw_spread_remains_strictly_fee_independent(self):
        """
        Step 7 requirement: Raw Spread remains fee-independent.
        Neither fee rates nor fee tiers alter raw spread calculation.
        """
        now = time.time()
        ref_state = NormalizedMarketState(
            venue="BINANCE",
            symbol="BTC/USDT",
            best_bid=60000.0,
            best_ask=60005.0,
            timestamp=now,
            has_rest_snapshot=True,
            ws_synchronized=True,
            connection_status="CONNECTED",
        )
        priced_state = NormalizedMarketState(
            venue="KRAKEN",
            symbol="BTC/USDT",
            best_bid=60100.0,
            best_ask=60105.0,
            timestamp=now,
            has_rest_snapshot=True,
            ws_synchronized=True,
            connection_status="CONNECTED",
        )

        raw_res = self.calculator.calculate_raw_spread(
            reference_data=ref_state,
            priced_data=priced_state,
            symbol="BTC/USDT",
            reference_venue="BINANCE",
            priced_venue="KRAKEN",
        )
        self.assertTrue(raw_res.validity)
        # Raw spread = (60100 - 60005) / 60005 * 100 = 0.1583%
        expected_raw_pct = round(((60100.0 - 60005.0) / 60005.0) * 100.0, 4)
        self.assertAlmostEqual(raw_res.raw_spread_pct, expected_raw_pct)

        # Directional fees are calculated independently
        fees = self.calculator.calculate_directional_fees("BINANCE", "KRAKEN")
        self.assertAlmostEqual(fees.candidate_a_total_fee_pct, 0.50)  # Binance maker 0.1% + Kraken taker 0.4% = 0.5%
        # The raw spread result must NOT have any fee deducted
        self.assertAlmostEqual(raw_res.raw_spread_pct, expected_raw_pct)

    def test_direction_candidates_annotated_with_directional_fees(self):
        """Direction engine automatically annotates Candidate A & B with directional fees."""
        now = time.time()
        book_a = OrderBook(
            symbol="ETH/USDT",
            venue="BINANCE",
            bids=[OrderBookEntry(price=3000.0, size=5.0)],
            asks=[OrderBookEntry(price=3001.0, size=5.0)],
            timestamp=now,
        )
        book_b = OrderBook(
            symbol="ETH/USDT",
            venue="BYBIT",
            bids=[OrderBookEntry(price=3005.0, size=5.0)],
            asks=[OrderBookEntry(price=3006.0, size=5.0)],
            timestamp=now,
        )

        direction_res = self.direction_engine.evaluate_candidates(
            venue_a_data=book_a,
            venue_b_data=book_b,
            symbol="ETH/USDT",
            venue_a_name="BINANCE",
            venue_b_name="BYBIT",
        )

        cand_a = direction_res.candidate_a
        cand_b = direction_res.candidate_b

        # Candidate A: Binance Maker (0.10%) + Bybit Taker (0.10%) = 0.20%
        self.assertAlmostEqual(cand_a.maker_fee_rate, 0.0010)
        self.assertAlmostEqual(cand_a.taker_fee_rate, 0.0010)
        self.assertAlmostEqual(cand_a.total_fee_pct, 0.20)

        # Candidate B: Bybit Maker (0.10%) + Binance Taker (0.10%) = 0.20%
        self.assertAlmostEqual(cand_b.maker_fee_rate, 0.0010)
        self.assertAlmostEqual(cand_b.taker_fee_rate, 0.0010)
        self.assertAlmostEqual(cand_b.total_fee_pct, 0.20)

    def test_to_dict_serialization_of_directional_fee_result(self):
        """DirectionalFeeResult converts cleanly to dictionary for REST and WebSocket serialization."""
        res = self.fee_engine.calculate_directional_fees("BINANCE", "OKX")
        d = res.to_dict()
        self.assertIn("candidate_a", d)
        self.assertIn("candidate_b", d)
        self.assertEqual(d["candidate_a"]["maker_venue"], "BINANCE")
        self.assertEqual(d["candidate_a"]["taker_venue"], "OKX")
        self.assertIn("total_fee_pct", d["candidate_a"])
        self.assertIn("timestamp", d)

    def test_account_and_instrument_specific_custom_fees(self):
        """Verify custom fees by account and instrument are resolved with highest specificity."""
        # Standard fee for Binance spot is 0.10% maker, 0.10% taker
        m, t = self.fee_engine.get_fee_rates("BINANCE", "spot", "standard")
        self.assertAlmostEqual(m, 0.0010)
        self.assertAlmostEqual(t, 0.0010)

        # 1. Custom fee for specific instrument (e.g. promotional zero maker fee pair)
        self.fee_engine.set_custom_fee("BINANCE", maker_fee=0.0000, taker_fee=0.0005, market_type="spot", instrument="BTC/FDUSD")
        m_inst, t_inst = self.fee_engine.get_fee_rates("BINANCE", "spot", "standard", instrument="BTC/FDUSD")
        self.assertAlmostEqual(m_inst, 0.0000)
        self.assertAlmostEqual(t_inst, 0.0005)

        # 2. Custom fee for institutional account
        self.fee_engine.set_custom_fee("BINANCE", maker_fee=-0.00005, taker_fee=0.0002, market_type="spot", account="INST_ACC_1")
        m_acc, t_acc = self.fee_engine.get_fee_rates("BINANCE", "spot", "standard", account="INST_ACC_1")
        self.assertAlmostEqual(m_acc, -0.00005) # Maker rebate
        self.assertAlmostEqual(t_acc, 0.0002)

        # Directional fees using account and instrument
        fees = self.fee_engine.calculate_directional_fees(
            venue_a="BINANCE",
            venue_b="BYBIT",
            account_a="INST_ACC_1",
            instrument="BTC/FDUSD",
        )
        self.assertEqual(fees.account_a, "INST_ACC_1")
        self.assertEqual(fees.instrument, "BTC/FDUSD")
        # Candidate A: Binance Maker (-0.005%) + Bybit standard Taker (0.10%) = 0.095%
        self.assertAlmostEqual(fees.candidate_a_maker_fee, -0.00005)
        self.assertAlmostEqual(fees.candidate_a_taker_fee, 0.0010)
        self.assertAlmostEqual(fees.candidate_a_total_fee_pct, 0.095)

    def test_zero_order_placement_invariant(self):
        """Verify that FeeEngine is strictly a calculation engine with zero order placement or exit changes."""
        self.assertFalse(hasattr(self.fee_engine, "place_order"))
        self.assertFalse(hasattr(self.fee_engine, "create_order"))
        self.assertFalse(hasattr(self.fee_engine, "submit_order"))
        self.assertFalse(hasattr(self.fee_engine, "close_position"))
        self.assertFalse(hasattr(self.fee_engine, "exit_order"))


if __name__ == "__main__":
    unittest.main()
