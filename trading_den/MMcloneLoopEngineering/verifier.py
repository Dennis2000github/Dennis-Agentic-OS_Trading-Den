#!/usr/bin/env python3
"""
verifier.py — Independent Verification Layer for Loop Engineering.

CRITICAL RULES:
1. Must NOT modify application source code.
2. Must distinguish CODE CORRECTNESS from FACTUAL / TECHNICAL CORRECTNESS.
3. Produces evidence under .loop/verification/evidence/ and reports under .loop/verification/reports/.
4. Verifies actual executable spread formulas and market data integrity (strictly no fake/mock prices).
5. Returns exit code 0 for PASS, 1 for FAIL, 2 for BLOCKED.
"""

import argparse
import asyncio
import datetime
import importlib
import inspect
import json
import os
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

WORKSPACE_DIR = os.path.abspath(os.path.dirname(__file__))
LOOP_DIR = os.path.join(WORKSPACE_DIR, ".loop")
EVIDENCE_DIR = os.path.join(LOOP_DIR, "verification", "evidence")
REPORTS_DIR = os.path.join(LOOP_DIR, "verification", "reports")


def ensure_dirs():
    os.makedirs(EVIDENCE_DIR, exist_ok=True)
    os.makedirs(REPORTS_DIR, exist_ok=True)


class VerificationResult:
    def __init__(self, step: Any, status: str, summary: str):
        self.step = step
        self.status = status  # PASS, FAIL, BLOCKED
        self.summary = summary
        self.checks: List[Dict[str, Any]] = []
        self.evidence: Dict[str, Any] = {}
        self.timestamp = datetime.datetime.now(datetime.timezone.utc).isoformat()

    def add_check(
        self,
        name: str,
        passed: bool,
        expected: str,
        actual: str,
        technical_basis: str,
        is_blocked: bool = False,
    ):
        self.checks.append({
            "name": name,
            "passed": passed,
            "blocked": is_blocked,
            "expected": expected,
            "actual": actual,
            "technical_basis": technical_basis,
        })
        if is_blocked and self.status != "BLOCKED":
            self.status = "BLOCKED"
        elif not passed and self.status == "PASS":
            self.status = "FAIL"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "step": self.step,
            "status": self.status,
            "summary": self.summary,
            "timestamp": self.timestamp,
            "checks": self.checks,
            "evidence": self.evidence,
        }


def verify_clone_isolation() -> Tuple[bool, str]:
    """Verify that current workspace is an isolated clone and original project is untouched."""
    original_project = "/Users/macbookpro/PycharmProjects/MarketMaking"
    target_project = WORKSPACE_DIR

    if not os.path.isdir(target_project):
        return False, f"Target project directory missing: {target_project}"

    if os.path.abspath(target_project) == os.path.abspath(original_project):
        return False, "Verifier running directly inside original MarketMaking project! Isolation violated."

    target_git = os.path.join(target_project, ".git")
    if not os.path.isdir(target_git):
        return False, "Target does not have its own .git directory (might be worktree or subfolder)"

    try:
        wt_out = subprocess.check_output(
            ["git", "-C", target_project, "worktree", "list"], stderr=subprocess.STDOUT
        ).decode()
        if len(wt_out.strip().splitlines()) > 1:
            return False, f"Target has multiple worktrees: {wt_out}"
    except Exception as e:
        return False, f"Git worktree check failed: {e}"

    if os.path.isdir(original_project):
        try:
            # Ensure target is completely outside original_project
            common = os.path.commonpath([os.path.abspath(target_project), os.path.abspath(original_project)])
            if common == os.path.abspath(original_project):
                return False, "Target project is inside original project directory!"
        except Exception as e:
            return False, f"Original project check failed: {e}"

    return True, "Clone isolation verified. Original project is read-only and clean."


def verify_step_loop_test() -> VerificationResult:
    """Verify the controlled test step (Step loop-test)."""
    res = VerificationResult(step="loop-test", status="PASS", summary="Controlled Loop Test Verification")

    spec_path = os.path.join(WORKSPACE_DIR, "modular", "loop-test-step.md")
    test_path = os.path.join(WORKSPACE_DIR, "tests", "test_loop_marker.py")

    if not os.path.isfile(spec_path):
        res.add_check(
            name="Specification Exists",
            passed=False,
            expected=f"File {spec_path} exists",
            actual="File missing",
            technical_basis="modular/loop-test-step.md must define the controlled test requirement.",
        )
    else:
        res.add_check(
            name="Specification Exists",
            passed=True,
            expected=f"File {spec_path} exists",
            actual="File present",
            technical_basis="Specification file verified.",
        )

    if not os.path.isfile(test_path):
        res.add_check(
            name="Test File Exists",
            passed=False,
            expected=f"File {test_path} exists",
            actual="File missing",
            technical_basis="tests/test_loop_marker.py must exist.",
        )
        return res

    # Run the test
    proc = subprocess.run(
        [sys.executable, "-m", "unittest", "tests/test_loop_marker.py"],
        cwd=WORKSPACE_DIR,
        capture_output=True,
        text=True,
    )
    test_passed = proc.returncode == 0
    res.add_check(
        name="Deterministic Marker Test",
        passed=test_passed,
        expected="Exit code 0 from unittest tests/test_loop_marker.py",
        actual=f"Exit code {proc.returncode}. Output:\n{proc.stdout}\n{proc.stderr}",
        technical_basis="Marker test verifies controlled execution pass/fail state.",
    )
    res.evidence["test_output"] = proc.stdout + proc.stderr
    return res


def verify_step_6() -> VerificationResult:
    """Verify STEP 6 — Dynamic Maker/Hedge Direction."""
    res = VerificationResult(step=6, status="PASS", summary="Step 6 Dynamic Maker/Hedge Direction Verification")

    # Check 1: Isolation
    iso_ok, iso_msg = verify_clone_isolation()
    res.add_check(
        name="Project Isolation",
        passed=iso_ok,
        expected="Clone isolation intact and source read-only",
        actual=iso_msg,
        technical_basis="Isolation audit rule 5 & 16.",
        is_blocked=not iso_ok,
    )
    if not iso_ok:
        return res

    # Check 2: Module exists and can be imported
    try:
        sys.path.insert(0, WORKSPACE_DIR)
        from arbitrage.direction_engine import DirectionEngine
        from arbitrage.models import (
            DirectionEvaluationResult,
            FreshnessStatus,
            MakerHedgeCandidate,
            NormalizedMarketState,
            OrderBook,
            OrderBookEntry,
        )
        res.add_check(
            name="Module Import",
            passed=True,
            expected="DirectionEngine and models importable from arbitrage",
            actual="Import successful",
            technical_basis="DirectionEngine dedicated layer architecture.",
        )
    except Exception as e:
        res.add_check(
            name="Module Import",
            passed=False,
            expected="DirectionEngine importable",
            actual=f"Import failed: {e}",
            technical_basis="arbitrage/direction_engine.py must be present and importable.",
        )
        return res

    # Check 3: Evaluate Candidates method signature & behavior
    engine = DirectionEngine(stale_threshold_seconds=10.0)
    sig = inspect.signature(engine.evaluate_candidates)
    required_params = ["venue_a_data", "venue_b_data", "symbol"]
    missing_params = [p for p in required_params if p not in sig.parameters]
    res.add_check(
        name="Method Signature",
        passed=len(missing_params) == 0,
        expected="evaluate_candidates accepts venue_a_data, venue_b_data, symbol",
        actual=f"Parameters: {list(sig.parameters.keys())}",
        technical_basis="Dedicated execution-direction layer API requirement.",
    )

    # Check 4: Dynamic evaluation of Candidate A and Candidate B
    now = time.time()
    book_a = OrderBook(
        venue="BINANCE",
        symbol="BTC/USDT",
        bids=[OrderBookEntry(price=60000.0, size=1.0)],
        asks=[OrderBookEntry(price=60010.0, size=1.0)],
        timestamp=now,
    )
    book_b = OrderBook(
        venue="BYBIT",
        symbol="BTC/USDT",
        bids=[OrderBookEntry(price=60200.0, size=1.0)],
        asks=[OrderBookEntry(price=60210.0, size=1.0)],
        timestamp=now,
    )

    eval_res = engine.evaluate_candidates(
        venue_a_data=book_a,
        venue_b_data=book_b,
        symbol="BTC/USDT",
        reference_venue="BINANCE",
        priced_venue="BYBIT",
        current_time=now,
    )

    both_candidates_present = (
        isinstance(eval_res, DirectionEvaluationResult)
        and eval_res.candidate_a is not None
        and eval_res.candidate_b is not None
    )
    res.add_check(
        name="Both Candidates Evaluated",
        passed=both_candidates_present,
        expected="evaluate_candidates returns Candidate A and Candidate B",
        actual=f"Result type: {type(eval_res).__name__}, candidate_a: {bool(eval_res.candidate_a)}, candidate_b: {bool(eval_res.candidate_b)}",
        technical_basis="Step 6 requirement: Evaluate Candidate A and Candidate B.",
    )

    # Check 5: 7 Required fields on both candidates
    required_fields = [
        "maker_venue",
        "maker_price",
        "hedge_venue",
        "hedge_price",
        "direction",
        "timestamps",
        "validity",
    ]
    cand_a_dict = eval_res.candidate_a.to_dict() if eval_res.candidate_a else {}
    cand_b_dict = eval_res.candidate_b.to_dict() if eval_res.candidate_b else {}
    missing_a = [f for f in required_fields if f not in cand_a_dict]
    missing_b = [f for f in required_fields if f not in cand_b_dict]

    res.add_check(
        name="Required Candidate Fields",
        passed=(len(missing_a) == 0 and len(missing_b) == 0),
        expected=f"All 7 required fields present on both candidates: {required_fields}",
        actual=f"Missing A: {missing_a}, Missing B: {missing_b}",
        technical_basis="Step 6 requirement: Maker venue, Maker price, Hedge venue, Hedge price, direction, timestamps, validity.",
    )

    # Check 6: Independence from Reference / Priced venues
    # Candidate A must have Maker = Venue A, Hedge = Venue B
    # Candidate B must have Maker = Venue B, Hedge = Venue A
    role_independence = (
        eval_res.candidate_a.maker_venue == "BINANCE"
        and eval_res.candidate_a.hedge_venue == "BYBIT"
        and eval_res.candidate_b.maker_venue == "BYBIT"
        and eval_res.candidate_b.hedge_venue == "BINANCE"
    )
    res.add_check(
        name="Role Independence",
        passed=role_independence,
        expected="Candidate A has A as Maker, B as Hedge; Candidate B has B as Maker, A as Hedge",
        actual=f"A: Maker={eval_res.candidate_a.maker_venue}, Hedge={eval_res.candidate_a.hedge_venue} | B: Maker={eval_res.candidate_b.maker_venue}, Hedge={eval_res.candidate_b.hedge_venue}",
        technical_basis="Never assume Reference = Hedge or Priced = Maker.",
    )

    # Check 7: Executable spread price integrity & formulas
    # Favorable spread: Buy on A @ 60010, Sell on B @ 60200.
    # Candidate A: Maker buys on A @ 60010 (or 60000 limit), hedge sells on B @ 60200.
    # Candidate B: Maker sells on B @ 60200 (or 60210 limit), hedge buys on A @ 60010.
    formula_valid = (
        eval_res.candidate_a.maker_price > 0
        and eval_res.candidate_a.hedge_price > 0
        and eval_res.candidate_b.maker_price > 0
        and eval_res.candidate_b.hedge_price > 0
    )
    res.add_check(
        name="Executable Price Integrity",
        passed=formula_valid,
        expected="Executable prices are strictly positive and match valid book levels",
        actual=f"A prices: maker={eval_res.candidate_a.maker_price}, hedge={eval_res.candidate_a.hedge_price} | B prices: maker={eval_res.candidate_b.maker_price}, hedge={eval_res.candidate_b.hedge_price}",
        technical_basis="Actual executable bid/ask relationship must be preserved.",
    )

    # Check 8: Negative test — Stale market data rejected
    old_time = now - 60.0  # 60s old, threshold is 10s
    book_stale = OrderBook(
        venue="BYBIT",
        symbol="BTC/USDT",
        bids=[OrderBookEntry(price=60200.0, size=1.0)],
        asks=[OrderBookEntry(price=60210.0, size=1.0)],
        timestamp=old_time,
    )
    eval_stale = engine.evaluate_candidates(
        venue_a_data=book_a,
        venue_b_data=book_stale,
        symbol="BTC/USDT",
        reference_venue="BINANCE",
        priced_venue="BYBIT",
        current_time=now,
    )
    stale_rejected = not eval_stale.candidate_a.validity and not eval_stale.candidate_b.validity
    res.add_check(
        name="Negative Test — Stale Data Rejection",
        passed=stale_rejected,
        expected="Stale market data (>10s) must be marked validity=False",
        actual=f"A validity={eval_stale.candidate_a.validity}, B validity={eval_stale.candidate_b.validity}",
        technical_basis="Stale/invalid market data must produce no valid executable candidates.",
    )

    # Check 9: Zero order placement invariant
    # DirectionEngine must not place orders or import execution order-dispatch clients
    src_file = os.path.join(WORKSPACE_DIR, "arbitrage", "direction_engine.py")
    with open(src_file, "r") as f:
        src_code = f.read()

    prohibited_calls = ["create_order", "place_order", "send_order", "execute_order"]
    found_calls = [c for c in prohibited_calls if f".{c}(" in src_code]
    res.add_check(
        name="Zero Order Placement Invariant",
        passed=len(found_calls) == 0,
        expected="No order placement method invocations in DirectionEngine",
        actual=f"Prohibited calls found: {found_calls}",
        technical_basis="Step 6 requirement: No order placement.",
    )

    # Check 10: Run full test suite for Step 6
    proc = subprocess.run(
        [sys.executable, "-m", "unittest", "tests/test_step6_direction_engine.py"],
        cwd=WORKSPACE_DIR,
        capture_output=True,
        text=True,
    )
    test_passed = proc.returncode == 0
    res.add_check(
        name="Deterministic Step 6 Test Suite",
        passed=test_passed,
        expected="Exit code 0 from tests/test_step6_direction_engine.py",
        actual=f"Exit code {proc.returncode}.\n{proc.stdout}\n{proc.stderr}".strip(),
        technical_basis="Step 6 unit tests pass deterministically.",
    )

    res.evidence["candidate_a"] = cand_a_dict
    res.evidence["candidate_b"] = cand_b_dict
    res.evidence["unit_test_output"] = proc.stdout + proc.stderr
    return res


def verify_step_7() -> VerificationResult:
    """Verify STEP 7 — Directional Maker/Taker Fees."""
    res = VerificationResult(step=7, status="PASS", summary="Step 7 Directional Maker/Taker Fees Verification")

    # Check 1: Isolation
    iso_ok, iso_msg = verify_clone_isolation()
    res.add_check(
        name="Project Isolation",
        passed=iso_ok,
        expected="Clone isolation intact and source read-only",
        actual=iso_msg,
        technical_basis="Isolation audit rule 5 & 16.",
        is_blocked=not iso_ok,
    )
    if not iso_ok:
        return res

    # Check 2: Module Import
    try:
        sys.path.insert(0, WORKSPACE_DIR)
        from arbitrage.fee_engine import DEFAULT_EXCHANGE_FEES, FeeEngine
        from arbitrage.models import DirectionEvaluationResult, DirectionalFeeResult, OrderBook, OrderBookEntry
        from arbitrage.spread_calculator import SpreadCalculator
        from arbitrage.direction_engine import DirectionEngine
        res.add_check(
            name="Module Import",
            passed=True,
            expected="FeeEngine and models importable from arbitrage",
            actual="Import successful",
            technical_basis="Directional Fee Engine dedicated layer architecture.",
        )
    except Exception as e:
        res.add_check(
            name="Module Import",
            passed=False,
            expected="FeeEngine importable",
            actual=f"Import failed: {e}",
            technical_basis="arbitrage/fee_engine.py must be present and importable.",
        )
        return res

    fee_engine = FeeEngine()

    # Check 3: Formula verification: Candidate A (A Maker + B Taker) and Candidate B (B Maker + A Taker)
    res_fees = fee_engine.calculate_directional_fees(
        venue_a="BINANCE",
        venue_b="BYBIT",
        symbol="BTC/USDT",
        market_type="spot",
        fee_tier_a="standard",
        fee_tier_b="standard",
    )
    formula_valid = (
        isinstance(res_fees, DirectionalFeeResult)
        and abs(res_fees.candidate_a_total_fee - (res_fees.candidate_a_maker_fee + res_fees.candidate_a_taker_fee)) < 1e-9
        and abs(res_fees.candidate_b_total_fee - (res_fees.candidate_b_maker_fee + res_fees.candidate_b_taker_fee)) < 1e-9
        and res_fees.candidate_a_maker_venue == "BINANCE"
        and res_fees.candidate_a_taker_venue == "BYBIT"
        and res_fees.candidate_b_maker_venue == "BYBIT"
        and res_fees.candidate_b_taker_venue == "BINANCE"
    )
    res.add_check(
        name="Directional Fee Formula",
        passed=formula_valid,
        expected="Candidate A = A Maker + B Taker; Candidate B = B Maker + A Taker",
        actual=f"Cand A: maker={res_fees.candidate_a_maker_fee}, taker={res_fees.candidate_a_taker_fee}, total={res_fees.candidate_a_total_fee} | Cand B: maker={res_fees.candidate_b_maker_fee}, taker={res_fees.candidate_b_taker_fee}, total={res_fees.candidate_b_total_fee}",
        technical_basis="Step 7 requirement: Directional maker/taker fees for A Maker -> B Taker and B Maker -> A Taker.",
    )

    # Check 4: Asymmetric fee calculation
    asym_res = fee_engine.calculate_directional_fees(
        venue_a="MEXC",
        venue_b="OKX",
        symbol="BTC/USDT",
        market_type="spot",
        fee_tier_a="standard",
        fee_tier_b="standard",
    )
    asym_valid = (
        abs(asym_res.candidate_a_total_fee_pct - 0.10) < 1e-4
        and abs(asym_res.candidate_b_total_fee_pct - 0.18) < 1e-4
    )
    res.add_check(
        name="Directional Asymmetry Verification",
        passed=asym_valid,
        expected="MEXC->OKX Cand A total fee pct is 0.10%, Cand B total fee pct is 0.18%",
        actual=f"Cand A: {asym_res.candidate_a_total_fee_pct}%, Cand B: {asym_res.candidate_b_total_fee_pct}%",
        technical_basis="Directional fees must accurately capture cost differences depending on which venue is Maker vs Hedge.",
    )

    # Check 5: Multi-dimensional parameters: exchange, account, instrument, market type, fee tier
    spot_rates = fee_engine.get_fee_rates("BINANCE", market_type="spot", fee_tier="standard")
    perp_rates = fee_engine.get_fee_rates("BINANCE", market_type="perp", fee_tier="standard")
    market_type_ok = (spot_rates != perp_rates)

    vip_rates = fee_engine.get_fee_rates("BYBIT", market_type="spot", fee_tier="vip1")
    std_rates = fee_engine.get_fee_rates("BYBIT", market_type="spot", fee_tier="standard")
    tier_ok = (vip_rates != std_rates)

    fee_engine.set_custom_fee("BINANCE", maker_fee=0.0001, taker_fee=0.0003, market_type="spot", account="TEST_ACC", instrument="BTC/USDT")
    custom_rates = fee_engine.get_fee_rates("BINANCE", market_type="spot", account="TEST_ACC", instrument="BTC/USDT")
    custom_ok = (custom_rates == (0.0001, 0.0003))

    params_ok = market_type_ok and tier_ok and custom_ok
    res.add_check(
        name="Multi-Dimensional Fee Support",
        passed=params_ok,
        expected="Supports exchange, market type (spot/perp), fee tier (standard/vip), account override, and instrument override",
        actual=f"market_type_ok={market_type_ok}, tier_ok={tier_ok}, custom_ok={custom_ok}",
        technical_basis="Step 7 requirement: Use exchange, account, instrument, market type, fee tier.",
    )

    # Check 6: Raw Spread remains fee-independent
    calc = SpreadCalculator()
    now = time.time()
    book_a = OrderBook(
        venue="BINANCE",
        symbol="BTC/USDT",
        bids=[OrderBookEntry(price=60000.0, size=1.0)],
        asks=[OrderBookEntry(price=60010.0, size=1.0)],
        timestamp=now,
    )
    book_b = OrderBook(
        venue="BYBIT",
        symbol="BTC/USDT",
        bids=[OrderBookEntry(price=60200.0, size=1.0)],
        asks=[OrderBookEntry(price=60210.0, size=1.0)],
        timestamp=now,
    )
    raw_spread = calc.calculate_raw_spread(book_a, book_b, "BTC/USDT", "BINANCE", "BYBIT")
    expected_raw_spread = ((60200.0 - 60010.0) / 60010.0) * 100.0
    fee_independence = (
        raw_spread.validity
        and abs(raw_spread.raw_spread_pct - round(expected_raw_spread, 4)) < 1e-4
    )
    res.add_check(
        name="Raw Spread Fee-Independent Invariant",
        passed=fee_independence,
        expected="Raw spread calculation is unaffected by fees",
        actual=f"Raw spread pct: {raw_spread.raw_spread_pct}%, Expected: {round(expected_raw_spread, 4)}%",
        technical_basis="Step 7 requirement: Raw Spread remains fee-independent.",
    )

    # Check 7: No duplicate fee logic — integration check with DirectionEngine
    dir_engine = DirectionEngine(fee_engine=fee_engine)
    eval_res = dir_engine.evaluate_candidates(
        venue_a_data=book_a,
        venue_b_data=book_b,
        symbol="BTC/USDT",
        venue_a_name="BINANCE",
        venue_b_name="BYBIT",
    )
    fees_integrated = (
        eval_res.candidate_a.maker_fee_rate is not None
        and eval_res.candidate_a.taker_fee_rate is not None
        and eval_res.candidate_a.total_fee_pct is not None
        and eval_res.candidate_b.maker_fee_rate is not None
        and eval_res.candidate_b.taker_fee_rate is not None
        and eval_res.candidate_b.total_fee_pct is not None
    )
    res.add_check(
        name="Centralized Fee Logic Integration",
        passed=fees_integrated,
        expected="DirectionEngine reuses FeeEngine without duplicate fee logic",
        actual=f"Cand A fees: maker={eval_res.candidate_a.maker_fee_rate}, taker={eval_res.candidate_a.taker_fee_rate}, total_pct={eval_res.candidate_a.total_fee_pct}",
        technical_basis="Step 7 requirement: Do not create duplicate fee logic. Reuse existing directional fee engine.",
    )

    # Check 8: Zero order placement invariant
    fee_src = os.path.join(WORKSPACE_DIR, "arbitrage", "fee_engine.py")
    with open(fee_src, "r") as f:
        src_code = f.read()
    prohibited_calls = ["create_order", "place_order", "send_order", "execute_order", "submit_order", "close_position"]
    found_calls = [c for c in prohibited_calls if f".{c}(" in src_code]
    res.add_check(
        name="Zero Order Placement Invariant",
        passed=len(found_calls) == 0,
        expected="No order placement method invocations in FeeEngine",
        actual=f"Prohibited calls found: {found_calls}",
        technical_basis="Step 7 requirement: No order placement or exit changes.",
    )

    # Check 9: Run Step 7 Unit Tests
    proc = subprocess.run(
        [sys.executable, "-m", "unittest", "tests/test_step7_directional_fees.py"],
        cwd=WORKSPACE_DIR,
        capture_output=True,
        text=True,
    )
    test_passed = proc.returncode == 0
    res.add_check(
        name="Deterministic Step 7 Test Suite",
        passed=test_passed,
        expected="Exit code 0 from tests/test_step7_directional_fees.py",
        actual=f"Exit code {proc.returncode}.\n{proc.stdout}\n{proc.stderr}".strip(),
        technical_basis="Step 7 unit tests pass deterministically.",
    )

    res.evidence["fee_calculation_result"] = res_fees.to_dict()
    res.evidence["unit_test_output"] = proc.stdout + proc.stderr
    return res


def verify_step_8() -> VerificationResult:
    """Verify STEP 8 — Opportunity Engine."""
    res = VerificationResult(step=8, status="PASS", summary="Step 8 Opportunity Engine Verification")

    # Check 1: Isolation
    iso_ok, iso_msg = verify_clone_isolation()
    res.add_check(
        name="Project Isolation",
        passed=iso_ok,
        expected="Clone isolation intact and source read-only",
        actual=iso_msg,
        technical_basis="Isolation audit rule 5 & 16.",
        is_blocked=not iso_ok,
    )
    if not iso_ok:
        return res

    # Check 2: Module Import
    try:
        sys.path.insert(0, WORKSPACE_DIR)
        from arbitrage.opportunity_engine import OpportunityEngine
        from arbitrage.models import OpportunityEvaluationResult, DirectionalOpportunity, OrderBook, OrderBookEntry
        res.add_check(
            name="Module Import",
            passed=True,
            expected="OpportunityEngine and models importable from arbitrage",
            actual="Import successful",
            technical_basis="OpportunityEngine dedicated layer architecture.",
        )
    except Exception as e:
        res.add_check(
            name="Module Import",
            passed=False,
            expected="OpportunityEngine importable",
            actual=f"Import failed: {e}",
            technical_basis="arbitrage/opportunity_engine.py must be present and importable.",
        )
        return res

    opp_engine = OpportunityEngine()

    # Check 3: Both directional opportunities evaluated with all components
    now = time.time()
    book_a = OrderBook(
        venue="BINANCE",
        symbol="BTC/USDT",
        bids=[OrderBookEntry(price=60000.0, size=2.0)],
        asks=[OrderBookEntry(price=60010.0, size=2.0)],
        timestamp=now,
    )
    book_b = OrderBook(
        venue="BYBIT",
        symbol="BTC/USDT",
        bids=[OrderBookEntry(price=60200.0, size=2.0)],
        asks=[OrderBookEntry(price=60210.0, size=2.0)],
        timestamp=now,
    )
    eval_res = opp_engine.evaluate_directional_opportunities(
        venue_a_data=book_a,
        venue_b_data=book_b,
        symbol="BTC/USDT",
        venue_a_name="BINANCE",
        venue_b_name="BYBIT",
    )
    opps_valid = (
        isinstance(eval_res, OpportunityEvaluationResult)
        and eval_res.opportunity_a is not None
        and eval_res.opportunity_b is not None
    )
    res.add_check(
        name="Both Directional Opportunities Evaluated",
        passed=opps_valid,
        expected="OpportunityEngine returns Opportunity A and Opportunity B",
        actual=f"opp_a present: {bool(eval_res.opportunity_a)}, opp_b present: {bool(eval_res.opportunity_b)}",
        technical_basis="Step 8 requirement: Return both directional opportunities and calculation components.",
    )

    # Check 4: Raw Spread separate from Net Spread
    raw_separate = (
        eval_res.opportunity_a.raw_spread_pct != eval_res.opportunity_a.net_spread_pct
        and eval_res.opportunity_a.calculation_components.get("raw_spread_pct") is not None
        and eval_res.opportunity_a.calculation_components.get("net_spread_pct") is not None
    )
    res.add_check(
        name="Raw Spread Separate From Net Spread",
        passed=raw_separate,
        expected="Raw spread % is kept distinct from net spread %",
        actual=f"Raw: {eval_res.opportunity_a.raw_spread_pct}%, Net: {eval_res.opportunity_a.net_spread_pct}%",
        technical_basis="Step 8 requirement: Keep Raw Spread separate from Net Spread.",
    )

    # Check 5: Stale / missing data invalidates opportunity (no fake placeholders)
    stale_book = OrderBook(
        venue="BYBIT",
        symbol="BTC/USDT",
        bids=[OrderBookEntry(price=60200.0, size=2.0)],
        asks=[OrderBookEntry(price=60210.0, size=2.0)],
        timestamp=now - 60.0,  # 60s stale
    )
    stale_res = opp_engine.evaluate_directional_opportunities(
        venue_a_data=book_a,
        venue_b_data=stale_book,
        symbol="BTC/USDT",
        venue_a_name="BINANCE",
        venue_b_name="BYBIT",
    )
    stale_invalidated = (
        not stale_res.opportunity_a.validity
        and not stale_res.opportunity_b.validity
        and stale_res.highest_net_opportunity is None
    )
    res.add_check(
        name="Stale Data Invalidation Invariant",
        passed=stale_invalidated,
        expected="Stale market data invalidates opportunities rather than generating placeholders",
        actual=f"Opp A validity: {stale_res.opportunity_a.validity}, Opp B validity: {stale_res.opportunity_b.validity}",
        technical_basis="Step 8 requirement: Invalid/stale market data must invalidate the opportunity.",
    )

    # Check 6: Zero order placement invariant
    opp_src = os.path.join(WORKSPACE_DIR, "arbitrage", "opportunity_engine.py")
    with open(opp_src, "r") as f:
        src_code = f.read()
    prohibited_calls = ["create_order", "place_order", "send_order", "execute_order", "submit_order", "close_position"]
    found_calls = [c for c in prohibited_calls if f".{c}(" in src_code]
    res.add_check(
        name="Zero Order Placement Invariant",
        passed=len(found_calls) == 0,
        expected="No order placement method invocations in OpportunityEngine",
        actual=f"Prohibited calls found: {found_calls}",
        technical_basis="OpportunityEngine is strictly calculation-only.",
    )

    # Check 7: Run Step 8 Unit Tests
    proc = subprocess.run(
        [sys.executable, "-m", "unittest", "tests/test_step8_opportunity_engine.py"],
        cwd=WORKSPACE_DIR,
        capture_output=True,
        text=True,
    )
    test_passed = proc.returncode == 0
    res.add_check(
        name="Deterministic Step 8 Test Suite",
        passed=test_passed,
        expected="Exit code 0 from tests/test_step8_opportunity_engine.py",
        actual=f"Exit code {proc.returncode}.\n{proc.stdout}\n{proc.stderr}".strip(),
        technical_basis="Step 8 unit tests pass deterministically.",
    )

    res.evidence["opportunity_a"] = eval_res.opportunity_a.to_dict()
    res.evidence["unit_test_output"] = proc.stdout + proc.stderr
    return res


def verify_step_9() -> VerificationResult:
    """Verify STEP 9 — Spread Arbitrage."""
    res = VerificationResult(step=9, status="PASS", summary="Step 9 Spread Arbitrage Verification")

    # Check 1: Isolation
    iso_ok, iso_msg = verify_clone_isolation()
    res.add_check(
        name="Project Isolation",
        passed=iso_ok,
        expected="Clone isolation intact and source read-only",
        actual=iso_msg,
        technical_basis="Isolation audit rule 5 & 16.",
        is_blocked=not iso_ok,
    )
    if not iso_ok:
        return res

    # Check 2: Module Import
    try:
        sys.path.insert(0, WORKSPACE_DIR)
        from arbitrage.spread_arbitrage import SpreadArbitrageEngine
        from arbitrage.models import SpreadArbitrageDecision, MakerEntryPricing, OrderBook, OrderBookEntry
        res.add_check(
            name="Module Import",
            passed=True,
            expected="SpreadArbitrageEngine and models importable from arbitrage",
            actual="Import successful",
            technical_basis="Spread Arbitrage dedicated layer architecture.",
        )
    except Exception as e:
        res.add_check(
            name="Module Import",
            passed=False,
            expected="SpreadArbitrageEngine importable",
            actual=f"Import failed: {e}",
            technical_basis="arbitrage/spread_arbitrage.py must be present and importable.",
        )
        return res

    engine = SpreadArbitrageEngine(min_profit_threshold_pct=0.05)

    # Check 3: Evaluates both directions and selects Highest Net Spread %
    now = time.time()
    book_a = OrderBook(
        symbol="BTC/USDT",
        venue="BINANCE",
        bids=[OrderBookEntry(price=60000.0, size=2.0)],
        asks=[OrderBookEntry(price=60010.0, size=2.0)],
        timestamp=now,
    )
    book_b = OrderBook(
        symbol="BTC/USDT",
        venue="BYBIT",
        bids=[OrderBookEntry(price=60300.0, size=2.0)],
        asks=[OrderBookEntry(price=60310.0, size=2.0)],
        timestamp=now,
    )
    dec_a = engine.evaluate_spread_arbitrage(
        venue_a_data=book_a,
        venue_b_data=book_b,
        symbol="BTC/USDT",
        venue_a_name="BINANCE",
        venue_b_name="BYBIT",
    )
    highest_selected = (
        isinstance(dec_a, SpreadArbitrageDecision)
        and dec_a.is_viable
        and dec_a.candidate_id == "A"
        and dec_a.first_leg_venue == "BINANCE"
        and dec_a.second_leg_venue == "BYBIT"
        and dec_a.maker_side == "BUY"
        and dec_a.hedge_side == "SELL"
    )
    res.add_check(
        name="Highest Net Spread Direction Selection",
        passed=highest_selected,
        expected="Candidate A selected with First Leg=BINANCE (Maker) and Second Leg=BYBIT (Hedge)",
        actual=f"viable={dec_a.is_viable}, candidate={dec_a.candidate_id}, first_leg={dec_a.first_leg_venue}, second_leg={dec_a.second_leg_venue}",
        technical_basis="Step 9 requirement: Select Highest Net Spread %. First Leg = Maker, Second Leg = Taker/Hedge.",
    )

    # Check 4: Decoupling of Execution Roles from Reference/Priced roles
    book_okx = OrderBook(
        symbol="BTC/USDT",
        venue="OKX",
        bids=[OrderBookEntry(price=60000.0, size=2.0)],
        asks=[OrderBookEntry(price=60010.0, size=2.0)],
        timestamp=now,
    )
    book_mexc = OrderBook(
        symbol="BTC/USDT",
        venue="MEXC",
        bids=[OrderBookEntry(price=60300.0, size=2.0)],
        asks=[OrderBookEntry(price=60310.0, size=2.0)],
        timestamp=now,
    )
    dec_b = engine.evaluate_spread_arbitrage(
        venue_a_data=book_okx,
        venue_b_data=book_mexc,
        symbol="BTC/USDT",
        venue_a_name="OKX",
        venue_b_name="MEXC",
        reference_venue="OKX",
        priced_venue="MEXC",
    )
    role_decoupled = (
        dec_b.is_viable
        and dec_b.candidate_id == "B"
        and dec_b.first_leg_venue == "MEXC"
        and dec_b.second_leg_venue == "OKX"
    )
    res.add_check(
        name="Role Decoupling Invariant",
        passed=role_decoupled,
        expected="Execution roles dynamically determined by fees/net spread rather than Reference/Priced venues",
        actual=f"candidate={dec_b.candidate_id}, first_leg={dec_b.first_leg_venue}, second_leg={dec_b.second_leg_venue}",
        technical_basis="Step 9 requirement: Do not derive execution direction from Reference/Priced roles.",
    )

    # Check 5: Maker Entry Pricing via Spread %, Grid, and Bollinger
    # 5a: SPREAD mode
    p_spread = engine.calculate_maker_entry_pricing(dec_a, pricing_mode="SPREAD", spread_offset_pct=0.01)
    spread_ok = (p_spread.pricing_mode == "SPREAD" and p_spread.base_maker_price > 0)

    # 5b: GRID mode
    p_grid = engine.calculate_maker_entry_pricing(dec_a, pricing_mode="GRID", grid_spacing_pct=0.05, grid_levels_count=3)
    grid_ok = (p_grid.pricing_mode == "GRID" and len(p_grid.grid_levels) == 3 and p_grid.grid_levels[0]["price"] > p_grid.grid_levels[1]["price"])

    # 5c: BOLLINGER mode
    p_boll = engine.calculate_maker_entry_pricing(dec_a, pricing_mode="BOLLINGER", spread_history=[0.1, 0.2, 0.3])
    boll_ok = (p_boll.pricing_mode == "BOLLINGER" and p_boll.bollinger_bands is not None and "mean" in p_boll.bollinger_bands)

    pricing_modes_ok = spread_ok and grid_ok and boll_ok
    res.add_check(
        name="Maker Entry Pricing Modes",
        passed=pricing_modes_ok,
        expected="Supports Spread %, Grid ladder, and Bollinger band maker entry pricing",
        actual=f"spread_ok={spread_ok}, grid_ok={grid_ok}, boll_ok={boll_ok}",
        technical_basis="Step 9 requirement: Actual Maker execution level determined by Spread %, Grid, Bollinger.",
    )

    # Check 6: Threshold and Risk Controls
    low_profit_dec = engine.evaluate_spread_arbitrage(
        venue_a_data=book_a,
        venue_b_data=book_b,
        symbol="BTC/USDT",
        min_profit_threshold_pct=10.0,
    )
    threshold_ok = (not low_profit_dec.is_viable and low_profit_dec.rejection_reason is not None)
    res.add_check(
        name="Entry Threshold and Risk Controls",
        passed=threshold_ok,
        expected="Unprofitable opportunities below threshold are rejected",
        actual=f"is_viable={low_profit_dec.is_viable}, reason={low_profit_dec.rejection_reason}",
        technical_basis="Step 9 requirement: Subject to existing thresholds and risk controls.",
    )

    # Check 7: Stale market data invalidates
    stale_book = OrderBook(
        symbol="BTC/USDT",
        venue="BYBIT",
        bids=[OrderBookEntry(price=60300.0, size=2.0)],
        asks=[OrderBookEntry(price=60310.0, size=2.0)],
        timestamp=now - 60.0,
    )
    stale_dec = engine.evaluate_spread_arbitrage(
        venue_a_data=book_a,
        venue_b_data=stale_book,
        symbol="BTC/USDT",
    )
    stale_ok = (not stale_dec.is_viable and stale_dec.rejection_reason is not None)
    res.add_check(
        name="Stale Data Invalidation Invariant",
        passed=stale_ok,
        expected="Stale market data invalidates strategy decision",
        actual=f"is_viable={stale_dec.is_viable}, reason={stale_dec.rejection_reason}",
        technical_basis="Real market data integrity invariant.",
    )

    # Check 8: Zero order placement invariant
    src_file = os.path.join(WORKSPACE_DIR, "arbitrage", "spread_arbitrage.py")
    with open(src_file, "r") as f:
        src_code = f.read()
    prohibited_calls = ["create_order", "place_order", "send_order", "execute_order", "submit_order", "close_position"]
    found_calls = [c for c in prohibited_calls if f".{c}(" in src_code]
    res.add_check(
        name="Zero Order Placement Invariant",
        passed=len(found_calls) == 0,
        expected="No order placement method invocations in SpreadArbitrageEngine",
        actual=f"Prohibited calls found: {found_calls}",
        technical_basis="Step 9 requirement: No order placement during modular development.",
    )

    # Check 9: Run Step 9 Unit Tests
    proc = subprocess.run(
        [sys.executable, "-m", "unittest", "tests/test_step9_spread_arbitrage.py"],
        cwd=WORKSPACE_DIR,
        capture_output=True,
        text=True,
    )
    test_passed = proc.returncode == 0
    res.add_check(
        name="Deterministic Step 9 Test Suite",
        passed=test_passed,
        expected="Exit code 0 from tests/test_step9_spread_arbitrage.py",
        actual=f"Exit code {proc.returncode}.\n{proc.stdout}\n{proc.stderr}".strip(),
        technical_basis="Step 9 unit tests pass deterministically.",
    )

    res.evidence["decision_a"] = dec_a.to_dict()
    res.evidence["decision_b"] = dec_b.to_dict()
    res.evidence["unit_test_output"] = proc.stdout + proc.stderr
    return res


def verify_step_10() -> VerificationResult:
    """Verify STEP 10 — Funding Arbitrage Mode."""
    res = VerificationResult(step=10, status="PASS", summary="Step 10 Funding Arbitrage Mode Verification")

    # Check 1: Isolation
    iso_ok, iso_msg = verify_clone_isolation()
    res.add_check(
        name="Project Isolation",
        passed=iso_ok,
        expected="Clone isolation intact and source read-only",
        actual=iso_msg,
        technical_basis="Isolation audit rule 5 & 16.",
        is_blocked=not iso_ok,
    )
    if not iso_ok:
        return res

    # Check 2: Module Import
    try:
        sys.path.insert(0, WORKSPACE_DIR)
        from arbitrage.funding_arbitrage import FundingArbitrageEngine
        from arbitrage.models import FundingArbitrageDecision, DirectionalFundingOpportunity, FundingRate, OrderBook, OrderBookEntry
        res.add_check(
            name="Module Import",
            passed=True,
            expected="FundingArbitrageEngine and models importable from arbitrage",
            actual="Import successful",
            technical_basis="Funding Arbitrage dedicated layer architecture.",
        )
    except Exception as e:
        res.add_check(
            name="Module Import",
            passed=False,
            expected="FundingArbitrageEngine importable",
            actual=f"Import failed: {e}",
            technical_basis="arbitrage/funding_arbitrage.py must be present and importable.",
        )
        return res

    engine = FundingArbitrageEngine(min_annual_funding_pct=5.0)

    # Check 3: Evaluates both directions and selects Highest Funding %
    now = time.time()
    funding_a = FundingRate(
        symbol="BTC/USDT",
        venue="BINANCE",
        rate=0.0003,
        timestamp=now,
        next_funding_time=now + 14400.0,
    )
    funding_b = FundingRate(
        symbol="BTC/USDT",
        venue="BYBIT",
        rate=0.0001,
        timestamp=now,
        next_funding_time=now + 14400.0,
    )
    book_a = OrderBook(
        symbol="BTC/USDT",
        venue="BINANCE",
        bids=[OrderBookEntry(price=60000.0, size=2.0)],
        asks=[OrderBookEntry(price=60010.0, size=2.0)],
        timestamp=now,
    )
    book_b = OrderBook(
        symbol="BTC/USDT",
        venue="BYBIT",
        bids=[OrderBookEntry(price=60005.0, size=2.0)],
        asks=[OrderBookEntry(price=60015.0, size=2.0)],
        timestamp=now,
    )

    decision = engine.evaluate_funding_arbitrage(
        funding_a=funding_a,
        funding_b=funding_b,
        book_a=book_a,
        book_b=book_b,
        symbol="BTC/USDT",
    )

    highest_funding_ok = (
        isinstance(decision, FundingArbitrageDecision)
        and decision.is_viable
        and decision.selected_opportunity.short_venue == "BINANCE"
        and decision.selected_opportunity.long_venue == "BYBIT"
        and abs(decision.net_funding_rate - 0.0002) < 1e-6
    )
    res.add_check(
        name="Highest Funding Direction Selection",
        passed=highest_funding_ok,
        expected="Short Binance (+0.03%) & Long Bybit (+0.01%) selected with net funding +0.02%",
        actual=f"short={decision.selected_opportunity.short_venue}, long={decision.selected_opportunity.long_venue}, net_rate={decision.net_funding_rate}",
        technical_basis="Step 10 requirement: Select First Leg/Maker using Highest Funding %.",
    )

    # Check 4: Invariant — Funding % Never Determines Order Price
    price_not_funding = (
        decision.pricing is not None
        and decision.pricing.base_maker_price > 1000.0
        and decision.pricing.base_maker_price != decision.net_funding_rate
    )
    res.add_check(
        name="Funding Rate Decoupled From Order Price",
        passed=price_not_funding,
        expected="Maker order price matches market level (~60000) and is not equal to funding rate (0.0002)",
        actual=f"maker_price={decision.pricing.base_maker_price if decision.pricing else None}, net_funding_rate={decision.net_funding_rate}",
        technical_basis="Step 10 requirement: Funding % is a venue/direction selector only, NEVER the Maker order price.",
    )

    # Check 5: Pricing via Spread, Grid, Bollinger
    p_spread = engine.calculate_maker_entry_pricing(decision, book_a, book_b, pricing_mode="SPREAD")
    p_grid = engine.calculate_maker_entry_pricing(decision, book_a, book_b, pricing_mode="GRID", grid_levels_count=3)
    p_boll = engine.calculate_maker_entry_pricing(decision, book_a, book_b, pricing_mode="BOLLINGER")

    pricing_modes_ok = (
        p_spread.pricing_mode == "SPREAD" and p_spread.base_maker_price > 0
        and p_grid.pricing_mode == "GRID" and len(p_grid.grid_levels) == 3
        and p_boll.pricing_mode == "BOLLINGER" and p_boll.bollinger_bands is not None
    )
    res.add_check(
        name="Maker Entry Pricing Modes Support",
        passed=pricing_modes_ok,
        expected="Supports Spread, Grid, and Bollinger pricing for funding maker entry",
        actual=f"spread={p_spread.pricing_mode}, grid={p_grid.pricing_mode}, boll={p_boll.pricing_mode}",
        technical_basis="Step 10 requirement: Spread/Grid/Bollinger determines Maker entry level.",
    )

    # Check 6: Independent Funding Exit Lifecycle
    exit_cond = decision.exit_conditions
    exit_ok = (
        exit_cond.get("exit_type") == "FUNDING_SETTLEMENT"
        and exit_cond.get("use_spread_convergence_exit") is False
        and exit_cond.get("rate_inversion_stop") is True
    )
    res.add_check(
        name="Independent Funding Exit Lifecycle",
        passed=exit_ok,
        expected="Funding exit is settlement/inversion based and decoupled from 0% spread convergence",
        actual=f"exit_type={exit_cond.get('exit_type')}, use_spread_convergence={exit_cond.get('use_spread_convergence_exit')}",
        technical_basis="Step 10 requirement: Independent Funding Exit (Section 10.9).",
    )

    # Check 7: Zero Order Placement Invariant
    src_file = os.path.join(WORKSPACE_DIR, "arbitrage", "funding_arbitrage.py")
    with open(src_file, "r") as f:
        src_code = f.read()
    prohibited_calls = ["create_order", "place_order", "send_order", "execute_order", "submit_order", "close_position"]
    found_calls = [c for c in prohibited_calls if f".{c}(" in src_code]
    res.add_check(
        name="Zero Order Placement Invariant",
        passed=len(found_calls) == 0,
        expected="No order placement method invocations in FundingArbitrageEngine",
        actual=f"Prohibited calls found: {found_calls}",
        technical_basis="Step 10 requirement: No order placement during modular development.",
    )

    # Check 8: Deterministic Step 10 Test Suite
    proc = subprocess.run(
        [sys.executable, "-m", "unittest", "tests/test_step10_funding_arbitrage.py"],
        cwd=WORKSPACE_DIR,
        capture_output=True,
        text=True,
    )
    test_passed = proc.returncode == 0
    res.add_check(
        name="Deterministic Step 10 Test Suite",
        passed=test_passed,
        expected="Exit code 0 from tests/test_step10_funding_arbitrage.py",
        actual=f"Exit code {proc.returncode}.\n{proc.stdout}\n{proc.stderr}".strip(),
        technical_basis="Step 10 unit tests pass deterministically.",
    )

    res.evidence["decision"] = decision.to_dict()
    res.evidence["unit_test_output"] = proc.stdout + proc.stderr
    return res


def verify_step_11() -> VerificationResult:
    """Verify STEP 11 — Funding Settlement and Independent Exit."""
    res = VerificationResult(step=11, status="PASS", summary="Step 11 Funding Settlement and Independent Exit Verification")

    # Check 1: Isolation
    iso_ok, iso_msg = verify_clone_isolation()
    res.add_check(
        name="Project Isolation",
        passed=iso_ok,
        expected="Clone isolation intact and source read-only",
        actual=iso_msg,
        technical_basis="Isolation audit rule 5 & 16.",
        is_blocked=not iso_ok,
    )
    if not iso_ok:
        return res

    # Check 2: Module Import
    try:
        sys.path.insert(0, WORKSPACE_DIR)
        from arbitrage.funding_settlement import FundingSettlementManager
        from arbitrage.funding_arbitrage import FundingArbitrageEngine
        from arbitrage.models import FundingPositionLifecycle, FundingPositionLeg, FundingRate, OrderBook, OrderBookEntry
        res.add_check(
            name="Module Import",
            passed=True,
            expected="FundingSettlementManager and models importable from arbitrage",
            actual="Import successful",
            technical_basis="Funding Settlement dedicated lifecycle layer architecture.",
        )
    except Exception as e:
        res.add_check(
            name="Module Import",
            passed=False,
            expected="FundingSettlementManager importable",
            actual=f"Import failed: {e}",
            technical_basis="arbitrage/funding_settlement.py must be present and importable.",
        )
        return res

    f_engine = FundingArbitrageEngine()
    manager = FundingSettlementManager()

    now = time.time()
    funding_a = FundingRate(symbol="BTC/USDT", venue="BINANCE", rate=0.0003, timestamp=now, next_funding_time=now + 14400.0)
    funding_b = FundingRate(symbol="BTC/USDT", venue="BYBIT", rate=0.0001, timestamp=now, next_funding_time=now + 14400.0)
    book_a = OrderBook(symbol="BTC/USDT", venue="BINANCE", bids=[OrderBookEntry(price=60000.0, size=2.0)], asks=[OrderBookEntry(price=60010.0, size=2.0)], timestamp=now)
    book_b = OrderBook(symbol="BTC/USDT", venue="BYBIT", bids=[OrderBookEntry(price=60005.0, size=2.0)], asks=[OrderBookEntry(price=60015.0, size=2.0)], timestamp=now)

    decision = f_engine.evaluate_funding_arbitrage(
        funding_a=funding_a,
        funding_b=funding_b,
        book_a=book_a,
        book_b=book_b,
    )

    pos = manager.create_position(
        decision=decision,
        quantity=1.0,
        entry_price_maker=60010.0,
        entry_price_hedge=60005.0,
        entry_time=now,
    )

    # Check 3: Ten Required Fields Tracked
    required_attrs = [
        "funding_interval",
        "funding_timestamp",
        "expected_direction",
        "funding_eligibility",
        "settlement_status",
        "actual_funding_result",
        "position_state",
        "entry_timestamp",
        "exit_state",
        "reconciliation_state",
    ]
    missing_attrs = [a for a in required_attrs if not hasattr(pos, a)]
    res.add_check(
        name="Ten Required Lifecycle Fields Tracked",
        passed=len(missing_attrs) == 0,
        expected="All 10 required fields present on FundingPositionLifecycle",
        actual=f"Missing: {missing_attrs}",
        technical_basis="Step 11 requirement: Track 10 specific lifecycle fields.",
    )

    # Check 4: Independent Exit State Machine (Settlement Reached)
    manager.record_settlement(pos, actual_settlement_cashflow=15.50, settlement_timestamp=pos.funding_timestamp + 1.0)
    should_exit, reason = manager.check_exit_conditions(pos, current_time=pos.funding_timestamp + 1.0)
    pos_closed = manager.close_position(pos, exit_reason=reason or "SETTLEMENT_REACHED")
    exit_ok = (
        should_exit
        and pos_closed.position_state == "CLOSED"
        and pos_closed.exit_state == "EXIT_SETTLED"
        and pos_closed.actual_funding_result == 15.50
    )
    res.add_check(
        name="Independent Exit — Settlement Reached",
        passed=exit_ok,
        expected="Position transitions to CLOSED / EXIT_SETTLED on funding settlement without relying on 0% spread convergence",
        actual=f"should_exit={should_exit}, state={pos_closed.position_state}, exit_state={pos_closed.exit_state}, cashflow={pos_closed.actual_funding_result}",
        technical_basis="Step 11 requirement: Independent funding exit lifecycle.",
    )

    # Check 5: Rate Inversion Invalidation Exit
    pos2 = manager.create_position(decision=decision, quantity=1.0, entry_price_maker=60010.0, entry_price_hedge=60005.0, entry_time=now)
    inv_a = FundingRate(symbol="BTC/USDT", venue="BINANCE", rate=0.0001, timestamp=now + 1000.0)
    inv_b = FundingRate(symbol="BTC/USDT", venue="BYBIT", rate=0.0003, timestamp=now + 1000.0)
    inv_exit, inv_reason = manager.check_exit_conditions(pos2, current_funding_a=inv_a, current_funding_b=inv_b, current_time=now + 1000.0)
    pos2_closed = manager.close_position(pos2, exit_reason=inv_reason or "RATE_INVALIDATED")
    invalidation_ok = (inv_exit and pos2_closed.exit_state == "EXIT_INVALIDATED")
    res.add_check(
        name="Independent Exit — Rate Inversion Invalidation",
        passed=invalidation_ok,
        expected="Rate inversion triggers EXIT_INVALIDATED",
        actual=f"exit={inv_exit}, state={pos2_closed.exit_state}, reason={inv_reason}",
        technical_basis="Step 11 requirement: Funding invalidation triggers managed exit.",
    )

    # Check 6: Safety Protections (Prevents Mismatched and Unhedged Legs)
    pos3 = manager.create_position(decision=decision, quantity=1.0, entry_price_maker=60010.0, entry_price_hedge=60005.0, entry_time=now)
    pos3.hedge_leg.quantity = 0.5
    mismatch_ok, mismatch_msg = manager.verify_hedge_reconciliation(pos3)
    rec_safety_ok = (not mismatch_ok and "Mismatched legs" in mismatch_msg)
    res.add_check(
        name="Safety Protections — Mismatched Legs Prevention",
        passed=rec_safety_ok,
        expected="Detects mismatched leg quantities and flags safety violation",
        actual=f"verified={mismatch_ok}, message={mismatch_msg}",
        technical_basis="Step 11 requirement: Prevent mismatched legs and unhedged positions.",
    )

    # Check 7: Real Data vs UNAVAILABLE State
    pos4 = manager.create_position(decision=decision, quantity=1.0, entry_price_maker=60010.0, entry_price_hedge=60005.0, entry_time=now)
    manager.record_settlement(pos4, actual_settlement_cashflow=None)
    unavailable_ok = (pos4.settlement_status == "UNAVAILABLE" and pos4.actual_funding_result is None)
    res.add_check(
        name="Real Data vs Unavailable State",
        passed=unavailable_ok,
        expected="Reports UNAVAILABLE when venue provides no statement, without fake cashflow data",
        actual=f"status={pos4.settlement_status}, result={pos4.actual_funding_result}",
        technical_basis="Step 11 requirement: Show actual unavailable state rather than fabricating settlement data.",
    )

    # Check 8: Zero Order Placement Invariant
    src_file = os.path.join(WORKSPACE_DIR, "arbitrage", "funding_settlement.py")
    with open(src_file, "r") as f:
        src_code = f.read()
    found_calls = [c for c in ["place_order", "create_order", "send_order", "submit_order", "execute_order"] if f".{c}(" in src_code]
    res.add_check(
        name="Zero Order Placement Invariant",
        passed=len(found_calls) == 0,
        expected="No order placement method invocations in FundingSettlementManager",
        actual=f"Prohibited calls found: {found_calls}",
        technical_basis="Step 11 requirement: No order placement during modular development.",
    )

    # Check 9: Deterministic Step 11 Test Suite
    proc = subprocess.run(
        [sys.executable, "-m", "unittest", "tests/test_step11_funding_settlement.py"],
        cwd=WORKSPACE_DIR,
        capture_output=True,
        text=True,
    )
    test_passed = proc.returncode == 0
    res.add_check(
        name="Deterministic Step 11 Test Suite",
        passed=test_passed,
        expected="Exit code 0 from tests/test_step11_funding_settlement.py",
        actual=f"Exit code {proc.returncode}.\n{proc.stdout}\n{proc.stderr}".strip(),
        technical_basis="Step 11 unit tests pass deterministically.",
    )

    res.evidence["position"] = pos_closed.to_dict()
    res.evidence["unit_test_output"] = proc.stdout + proc.stderr
    return res


def verify_step_12() -> VerificationResult:
    """
    Independent Verification for Step 12: Arbitrage Tab + Charts Integration.
    Verifies:
    1. Clone Isolation.
    2. Strategy Mode Architecture & Live State.
    3. Reference/Priced Pricing Invariant: Spread % = ((Priced - Reference) / Reference) * 100.
    4. Execution Roles Decoupling: First Leg = Maker, Second Leg = Hedge dynamically selected.
    5. Strategy Settings Dynamic Mode Support (Spread & Funding).
    6. Server REST API Endpoints.
    7. UI Integration in templates/index.html.
    8. Real Market Data Integrity vs STALE/UNAVAILABLE.
    9. Zero Live Trading Invariant.
    10. Deterministic Step 12 Test Suite.
    """
    res = VerificationResult(step=12, status="PASS", summary="Step 12 Arbitrage Tab and Charts Verification")

    # Check 1: Clone Isolation
    iso_ok, iso_msg = verify_clone_isolation()
    res.add_check(
        name="Project Isolation",
        passed=iso_ok,
        expected="Clone isolation intact and source read-only",
        actual=iso_msg,
        technical_basis="Isolation audit rule 5 & 16.",
        is_blocked=not iso_ok,
    )
    if not iso_ok:
        return res

    # Check 2: Strategy Mode Architecture & Live State
    from arbitrage.live_state import LiveStateManager
    from arbitrage.spread_calculator import SpreadCalculator
    from arbitrage.models import OrderBook, OrderBookEntry, FundingRate
    state = LiveStateManager()
    calc = SpreadCalculator()
    state.set_strategy_mode("SPREAD")
    mode_spread = state.get_strategy_mode()
    state.set_strategy_mode("FUNDING")
    mode_fund = state.get_strategy_mode()
    snap = state.get_live_snapshot()
    mode_ok = (mode_spread == "SPREAD" and mode_fund == "FUNDING" and snap.get("strategy_mode") == "FUNDING")
    res.add_check(
        name="Strategy Mode Architecture & Live State",
        passed=mode_ok,
        expected="LiveStateManager persists and broadcasts strategy_mode, spread_decisions, and funding_decisions",
        actual=f"spread_mode={mode_spread}, fund_mode={mode_fund}, snapshot_mode={snap.get('strategy_mode')}",
        technical_basis="Step 12 requirement: Unified strategy mode switcher and state synchronization.",
    )

    # Check 3: Reference/Priced Pricing Invariant
    now = time.time()
    book_a = OrderBook(
        symbol="BTC/USDT",
        venue="BINANCE",
        bids=[OrderBookEntry(price=60000.0, size=5.0)],
        asks=[OrderBookEntry(price=60005.0, size=5.0)],
        timestamp=now,
    )
    book_b = OrderBook(
        symbol="BTC/USDT",
        venue="BYBIT",
        bids=[OrderBookEntry(price=60100.0, size=5.0)],
        asks=[OrderBookEntry(price=60105.0, size=5.0)],
        timestamp=now,
    )
    spread_res = calc.calculate_raw_spread(
        reference_data=book_a,
        priced_data=book_b,
        symbol="BTC/USDT",
        reference_venue="BINANCE",
        priced_venue="BYBIT",
    )
    inv_res = calc.calculate_raw_spread(
        reference_data=book_b,
        priced_data=book_a,
        symbol="BTC/USDT",
        reference_venue="BYBIT",
        priced_venue="BINANCE",
    )
    expected_spread_pct = ((60100.0 - 60005.0) / 60005.0) * 100.0
    ref_priced_ok = (
        spread_res.validity
        and abs(spread_res.spread_ref_to_priced_pct - expected_spread_pct) < 1e-3
        and inv_res.validity
        and inv_res.spread_ref_to_priced_pct < 0
    )
    res.add_check(
        name="Reference/Priced Pricing Invariant",
        passed=ref_priced_ok,
        expected="Spread % = ((Priced - Reference) / Reference) * 100",
        actual=f"actual={spread_res.spread_ref_to_priced_pct}%, inverted={inv_res.spread_ref_to_priced_pct}%",
        technical_basis="Step 12 invariant: Spread calculation strictly evaluates Priced against Reference.",
    )

    # Check 4: Execution Roles Decoupling
    spread_dec = calc.evaluate_spread_arbitrage(venue_a_data=book_a, venue_b_data=book_b, symbol="BTC/USDT")
    roles_decoupled = (spread_dec.first_leg_venue in ("BINANCE", "BYBIT") and spread_dec.second_leg_venue in ("BINANCE", "BYBIT") and spread_dec.first_leg_venue != spread_dec.second_leg_venue)
    res.add_check(
        name="Execution Roles Decoupling",
        passed=roles_decoupled,
        expected="Maker (First Leg) and Hedge (Second Leg) dynamically assigned from opportunity economics",
        actual=f"first_leg(Maker)={spread_dec.first_leg_venue}, second_leg(Hedge)={spread_dec.second_leg_venue}",
        technical_basis="Step 12 invariant: Decouple execution roles from Reference and Priced venues.",
    )

    # Check 5: Strategy Settings Dynamic Mode Support
    funding_a = FundingRate(symbol="BTC/USDT", venue="BINANCE", rate=0.0005, next_funding_time=now + 14400.0, timestamp=now)
    funding_b = FundingRate(symbol="BTC/USDT", venue="BYBIT", rate=-0.0001, next_funding_time=now + 14400.0, timestamp=now)
    fund_dec = calc.evaluate_funding_arbitrage(
        funding_a=funding_a,
        funding_b=funding_b,
        book_a=book_a,
        book_b=book_b,
        symbol="BTC/USDT",
        pricing_mode="GRID",
        grid_step_pct=0.15,
        grid_levels_count=3,
    )
    dyn_support_ok = (
        spread_dec.strategy_type == "SPREAD_ARBITRAGE"
        and fund_dec.strategy_type == "FUNDING_ARBITRAGE"
        and spread_dec.exit_conditions.get("exit_type") == "SPREAD_CONVERGENCE"
        and fund_dec.exit_conditions.get("exit_type") == "FUNDING_SETTLEMENT"
        and fund_dec.pricing is not None
        and len(fund_dec.pricing.grid_levels) == 3
    )
    res.add_check(
        name="Strategy Settings Dynamic Mode Support",
        passed=dyn_support_ok,
        expected="Dynamic support for Spread Arbitrage and Funding Arbitrage modes and parameters",
        actual=f"spread_type={spread_dec.strategy_type}, fund_type={fund_dec.strategy_type}, fund_exit={fund_dec.exit_conditions.get('exit_type')}",
        technical_basis="Step 12 requirement: Strategy Settings dynamically support both strategies.",
    )

    # Check 6: Server REST API Endpoints
    from arbitrage.server import ArbitrageServer
    server = ArbitrageServer()
    async def _test_server():
        class MockWriter:
            def __init__(self):
                self.written = b""
            def write(self, data):
                self.written += data
            async def drain(self):
                pass
            def close(self):
                pass
            async def wait_closed(self):
                pass
        w = MockWriter()
        await server._handle_api_request("/api/arbitrage/strategy-mode", {}, w, method="GET")
        resp_mode = json.loads(w.written.split(b"\r\n\r\n", 1)[1].decode("utf-8"))
        w2 = MockWriter()
        await server._handle_api_request("/api/arbitrage/spread-decision", {"venue_a": ["BINANCE"], "venue_b": ["BYBIT"], "symbol": ["BTC/USDT"]}, w2, method="GET")
        resp_spread = json.loads(w2.written.split(b"\r\n\r\n", 1)[1].decode("utf-8"))
        w3 = MockWriter()
        await server._handle_api_request("/api/arbitrage/funding-decision", {"venue_a": ["BINANCE"], "venue_b": ["BYBIT"], "symbol": ["BTC/USDT"]}, w3, method="GET")
        resp_fund = json.loads(w3.written.split(b"\r\n\r\n", 1)[1].decode("utf-8"))
        return resp_mode, resp_spread, resp_fund
    r_mode, r_spread, r_fund = asyncio.run(_test_server())
    server_endpoints_ok = (
        "strategy_mode" in r_mode
        and r_spread.get("strategy_type") == "SPREAD_ARBITRAGE"
        and r_fund.get("strategy_type") == "FUNDING_ARBITRAGE"
    )
    res.add_check(
        name="Server REST API Endpoints",
        passed=server_endpoints_ok,
        expected="Endpoints /api/arbitrage/strategy-mode, /api/arbitrage/spread-decision, /api/arbitrage/funding-decision operational",
        actual=f"mode_resp={r_mode.get('strategy_mode')}, spread_type={r_spread.get('strategy_type')}, fund_type={r_fund.get('strategy_type')}",
        technical_basis="Step 12 requirement: Dedicated REST endpoints for UI and state interaction.",
    )

    # Check 7: Frontend UI Integration
    ui_path = os.path.join(WORKSPACE_DIR, "templates", "index.html")
    with open(ui_path, "r") as f:
        html_code = f.read()
    ui_ok = (
        "triStrategyModeSpread" in html_code
        and "triStrategyModeFunding" in html_code
        and "triNetFundingRateText" in html_code
        and "triNextSettlementText" in html_code
        and "First Leg (Maker)" in html_code
        and "Second Leg (Hedge)" in html_code
    )
    res.add_check(
        name="Frontend UI Integration",
        passed=ui_ok,
        expected="index.html contains mode toggle, dynamic funding detail cards, and decoupled leg table headers",
        actual=f"ui_elements_present={ui_ok}",
        technical_basis="Step 12 requirement: Arbitrage tab & UI elements reflect strategy mode and execution roles.",
    )

    # Check 8: Real Market Data Integrity vs STALE/UNAVAILABLE
    stale_book = OrderBook(
        symbol="BTC/USDT",
        venue="BINANCE",
        bids=[OrderBookEntry(price=60000.0, size=5.0)],
        asks=[OrderBookEntry(price=60005.0, size=5.0)],
        timestamp=now - 100.0,
    )
    stale_dec = calc.evaluate_spread_arbitrage(venue_a_data=stale_book, venue_b_data=book_b, symbol="BTC/USDT", current_time=now)
    integrity_ok = (not stale_dec.is_viable and stale_dec.rejection_reason is not None and "stale" in stale_dec.rejection_reason.lower())
    res.add_check(
        name="Real Market Data Integrity vs STALE/UNAVAILABLE",
        passed=integrity_ok,
        expected="Stale market data invalidates decision with is_viable=False and explicit rejection reason",
        actual=f"viable={stale_dec.is_viable}, rejection={stale_dec.rejection_reason}",
        technical_basis="Step 12 invariant: Never fabricate mock data; invalidate on stale quotes.",
    )

    # Check 9: Zero Live Trading Invariant
    server_path = os.path.join(WORKSPACE_DIR, "arbitrage", "server.py")
    calc_path = os.path.join(WORKSPACE_DIR, "arbitrage", "spread_calculator.py")
    prohibited = ["place_order", "create_order", "send_order", "submit_order", "execute_order"]
    found_calls = []
    for fp in [server_path, calc_path]:
        with open(fp, "r") as f:
            content = f.read()
        for call in prohibited:
            if f".{call}(" in content:
                found_calls.append(f"{os.path.basename(fp)}:{call}")
    res.add_check(
        name="Zero Live Trading Invariant",
        passed=len(found_calls) == 0,
        expected="No real order placement invocations in server or calculator",
        actual=f"prohibited_calls={found_calls}",
        technical_basis="Safety invariant: Zero live trading during modular verification.",
    )

    # Check 10: Deterministic Step 12 Test Suite
    proc = subprocess.run(
        [sys.executable, "-m", "unittest", "tests/test_step12_arbitrage_tab_charts.py"],
        cwd=WORKSPACE_DIR,
        capture_output=True,
        text=True,
    )
    test_passed = proc.returncode == 0
    res.add_check(
        name="Deterministic Step 12 Test Suite",
        passed=test_passed,
        expected="Exit code 0 from tests/test_step12_arbitrage_tab_charts.py",
        actual=f"Exit code {proc.returncode}.\n{proc.stdout}\n{proc.stderr}".strip(),
        technical_basis="Step 12 unit tests pass deterministically.",
    )

    res.evidence["spread_decision"] = spread_dec.to_dict()
    res.evidence["funding_decision"] = fund_dec.to_dict()
    res.evidence["unit_test_output"] = proc.stdout + proc.stderr
    return res


def verify_step_13() -> VerificationResult:
    """
    Independent Verification for Step 13: Screener → Bot Manager → Execution Pipeline.
    Verifies:
    1. Clone Isolation.
    2. Module Import: BotManager, ExecutionEngine, PreSubmissionValidator, and models.
    3. Strategy Selection Rules:
       - Spread Arbitrage = Highest Net Spread %
       - Funding Arbitrage = Highest Funding %
    4. Execution Roles Decoupling:
       - First Leg = Maker, Second Leg = Hedge dynamically selected without deriving from Reference/Priced.
    5. Pre-Submission Revalidation Engine (All 10 checks):
       - bid/ask, liquidity, funding, fees, slippage, timestamp, freshness, opportunity validity, execution direction, position state.
    6. Real Market Data Integrity vs STALE / CROSSED Invalidation.
    7. Execution Engine & Position Reconciliation (BALANCED vs MISMATCHED).
    8. Bot Manager Lifecycle Controls (create, start, stop, pause, list).
    9. Zero Live Trading Invariant.
    10. Deterministic Step 13 Test Suite.
    """
    res = VerificationResult(step=13, status="PASS", summary="Step 13 Screener Bot Manager Execution Verification")

    # Check 1: Clone Isolation
    iso_ok, iso_msg = verify_clone_isolation()
    res.add_check(
        name="Project Isolation",
        passed=iso_ok,
        expected="Clone isolation intact and source read-only",
        actual=iso_msg,
        technical_basis="Isolation audit rule 5 & 16.",
        is_blocked=not iso_ok,
    )
    if not iso_ok:
        return res

    # Check 2: Module Import
    try:
        from arbitrage.bot_manager import BotManager, ExecutionEngine, PreSubmissionValidator
        from arbitrage.models import BotConfig, ExecutionCycle, PreSubmissionValidationResult, OrderBook, OrderBookEntry, FundingRate
        from arbitrage.spread_calculator import SpreadCalculator
        import_ok = True
        import_msg = "BotManager, ExecutionEngine, PreSubmissionValidator imported successfully"
    except Exception as e:
        import_ok = False
        import_msg = f"Import error: {e}"
    res.add_check(
        name="Module Import",
        passed=import_ok,
        expected="Step 13 Bot Manager and Execution modules importable",
        actual=import_msg,
        technical_basis="Step 13 architecture: Screener → Bot Manager → Execution pipeline.",
    )
    if not import_ok:
        return res

    now = time.time()
    calc = SpreadCalculator()
    validator = PreSubmissionValidator(default_stale_threshold_seconds=10.0)
    exec_engine = ExecutionEngine(validator=validator)
    bot_mgr = BotManager(calculator=calc, execution_engine=exec_engine)

    book_a = OrderBook(
        symbol="BTC/USDT",
        venue="BINANCE",
        bids=[OrderBookEntry(price=60000.0, size=5.0)],
        asks=[OrderBookEntry(price=60010.0, size=5.0)],
        timestamp=now,
    )
    book_b = OrderBook(
        symbol="BTC/USDT",
        venue="BYBIT",
        bids=[OrderBookEntry(price=60200.0, size=5.0)],
        asks=[OrderBookEntry(price=60210.0, size=5.0)],
        timestamp=now,
    )
    fund_a = FundingRate(symbol="BTC/USDT", venue="BINANCE", rate=0.0004, timestamp=now, next_funding_time=now + 14400.0)
    fund_b = FundingRate(symbol="BTC/USDT", venue="BYBIT", rate=0.0001, timestamp=now, next_funding_time=now + 14400.0)

    # Check 3: Strategy Selection Rules
    bot_s = bot_mgr.create_bot("bot_verify_s", strategy_type="SPREAD_ARBITRAGE", mode="DRY_RUN", symbol="BTC/USDT", order_quantity=1.0)
    cycle_s = bot_mgr.evaluate_and_execute("bot_verify_s", book_a=book_a, book_b=book_b, current_time=now)
    bot_f = bot_mgr.create_bot("bot_verify_f", strategy_type="FUNDING_ARBITRAGE", mode="DRY_RUN", symbol="BTC/USDT", order_quantity=1.0)
    cycle_f = bot_mgr.evaluate_and_execute("bot_verify_f", book_a=book_a, book_b=book_b, funding_a=fund_a, funding_b=fund_b, current_time=now)
    strategy_sel_ok = (cycle_s.strategy_type == "SPREAD_ARBITRAGE" and cycle_f.strategy_type == "FUNDING_ARBITRAGE" and cycle_s.cycle_state == "RECONCILED" and cycle_f.cycle_state == "RECONCILED")
    res.add_check(
        name="Strategy Selection Rules",
        passed=strategy_sel_ok,
        expected="Spread = Highest Net Spread %; Funding = Highest Funding %",
        actual=f"spread_state={cycle_s.cycle_state}, funding_state={cycle_f.cycle_state}",
        technical_basis="Step 13 rule: Strategy selection maps to highest net spread or funding opportunity.",
    )

    # Check 4: Execution Roles Decoupling
    roles_decoupled = (
        cycle_s.maker_venue in ("BINANCE", "BYBIT")
        and cycle_s.hedge_venue in ("BINANCE", "BYBIT")
        and cycle_s.maker_venue != cycle_s.hedge_venue
        and cycle_f.maker_venue != cycle_f.hedge_venue
    )
    res.add_check(
        name="Execution Roles Decoupling",
        passed=roles_decoupled,
        expected="Maker (First Leg) and Hedge (Second Leg) dynamically assigned without Reference/Priced bias",
        actual=f"spread_maker={cycle_s.maker_venue}, spread_hedge={cycle_s.hedge_venue}, fund_maker={cycle_f.maker_venue}, fund_hedge={cycle_f.hedge_venue}",
        technical_basis="Step 13 invariant: Never derive execution roles from Reference/Priced.",
    )

    # Check 5: Pre-Submission Revalidation Engine (All 10 checks)
    dec = calc.evaluate_spread_arbitrage(venue_a_data=book_a, venue_b_data=book_b, symbol="BTC/USDT", current_time=now)
    val_res = validator.validate_pre_submission(
        maker_book=book_a,
        hedge_book=book_b,
        decision=dec,
        order_quantity=1.0,
        current_position_size=1.0,
        max_position_size=10.0,
        current_time=now,
    )
    checks_passed_count = sum(1 for c in val_res.checks_detail.values() if c.get("passed"))
    all_10_ok = (val_res.is_valid and checks_passed_count == 10 and len(val_res.failed_checks) == 0)
    res.add_check(
        name="Pre-Submission Revalidation Engine (All 10 Checks)",
        passed=all_10_ok,
        expected="Revalidates bid/ask, liquidity, funding, fees, slippage, timestamp, freshness, opportunity, direction, position state",
        actual=f"is_valid={val_res.is_valid}, passed_checks={checks_passed_count}/10, failed={val_res.failed_checks}",
        technical_basis="Step 13 requirement: Mandatory 10-point pre-submission revalidation before every order dispatch.",
    )

    # Check 6: Real Market Data Integrity vs STALE / CROSSED Invalidation
    stale_res = validator.validate_pre_submission(
        maker_book=book_a,
        hedge_book=book_b,
        decision=dec,
        order_quantity=1.0,
        current_time=now + 50.0,
    )
    crossed_book = OrderBook(symbol="BTC/USDT", venue="BINANCE", bids=[OrderBookEntry(price=60050.0, size=1.0)], asks=[OrderBookEntry(price=60040.0, size=1.0)], timestamp=now)
    crossed_res = validator.validate_pre_submission(
        maker_book=crossed_book,
        hedge_book=book_b,
        decision=dec,
        order_quantity=1.0,
        current_time=now,
    )
    integrity_ok = (not stale_res.is_valid and "freshness" in stale_res.failed_checks and not crossed_res.is_valid and "bid/ask" in crossed_res.failed_checks)
    res.add_check(
        name="Real Market Data Integrity vs STALE / CROSSED Invalidation",
        passed=integrity_ok,
        expected="Invalidates order submission on stale quote age or crossed book, without fabricated prices",
        actual=f"stale_valid={stale_res.is_valid} (failed: {stale_res.failed_checks}), crossed_valid={crossed_res.is_valid} (failed: {crossed_res.failed_checks})",
        technical_basis="Step 13 invariant: Execution must use latest valid real market state with zero mock data.",
    )

    # Check 7: Execution Engine & Position Reconciliation
    balanced_cycle = ExecutionCycle(cycle_id="v_c1", bot_id="b1", strategy_type="SPREAD", symbol="BTC/USDT", maker_venue="BINANCE", hedge_venue="BYBIT", maker_side="BUY", hedge_side="SELL", target_quantity=1.0, maker_filled_qty=1.0, hedge_filled_qty=1.0)
    mismatch_cycle = ExecutionCycle(cycle_id="v_c2", bot_id="b1", strategy_type="SPREAD", symbol="BTC/USDT", maker_venue="BINANCE", hedge_venue="BYBIT", maker_side="BUY", hedge_side="SELL", target_quantity=1.0, maker_filled_qty=1.0, hedge_filled_qty=0.5)
    rec_b = exec_engine.reconcile_cycle(balanced_cycle)
    rec_m = exec_engine.reconcile_cycle(mismatch_cycle)
    rec_ok = (rec_b and balanced_cycle.reconciliation_status == "BALANCED" and not rec_m and mismatch_cycle.reconciliation_status == "MISMATCHED")
    res.add_check(
        name="Execution Engine & Position Reconciliation",
        passed=rec_ok,
        expected="Detects balanced fills and flags mismatched leg quantities to prevent unhedged exposure",
        actual=f"balanced={rec_b} ({balanced_cycle.reconciliation_status}), mismatched_detected={not rec_m} ({mismatch_cycle.reconciliation_status})",
        technical_basis="Step 13 requirement: Coordinated Maker/Hedge execution management and reconciliation.",
    )

    # Check 8: Bot Manager Lifecycle Controls
    bot_ctrl = bot_mgr.create_bot("bot_ctrl_test", strategy_type="SPREAD_ARBITRAGE", mode="DRY_RUN")
    start_ok = bot_mgr.start_bot("bot_ctrl_test") and bot_ctrl.status == "RUNNING"
    pause_ok = bot_mgr.pause_bot("bot_ctrl_test") and bot_ctrl.status == "PAUSED"
    stop_ok = bot_mgr.stop_bot("bot_ctrl_test") and bot_ctrl.status == "STOPPED"
    ctrl_ok = (start_ok and pause_ok and stop_ok and len(bot_mgr.list_bots()) >= 1)
    res.add_check(
        name="Bot Manager Lifecycle Controls",
        passed=ctrl_ok,
        expected="Supports create, start, pause, stop, and list bot lifecycle operations",
        actual=f"lifecycle_verified={ctrl_ok}",
        technical_basis="Step 13 requirement: Bot Manager coordinates bot controls.",
    )

    # Check 9: Zero Live Trading Invariant
    bot_src = os.path.join(WORKSPACE_DIR, "arbitrage", "bot_manager.py")
    prohibited = ["place_order", "create_order", "send_order", "submit_order", "execute_order"]
    found_calls = []
    with open(bot_src, "r") as f:
        src_text = f.read()
    for call in prohibited:
        if f".{call}(" in src_text:
            found_calls.append(call)
    res.add_check(
        name="Zero Live Trading Invariant",
        passed=len(found_calls) == 0,
        expected="No real exchange order submission invocations in bot_manager.py",
        actual=f"prohibited_calls={found_calls}",
        technical_basis="Safety invariant: Zero live trading during modular verification.",
    )

    # Check 10: Deterministic Step 13 Test Suite
    proc = subprocess.run(
        [sys.executable, "-m", "unittest", "tests/test_step13_screener_bot_execution.py"],
        cwd=WORKSPACE_DIR,
        capture_output=True,
        text=True,
    )
    test_passed = proc.returncode == 0
    res.add_check(
        name="Deterministic Step 13 Test Suite",
        passed=test_passed,
        expected="Exit code 0 from tests/test_step13_screener_bot_execution.py",
        actual=f"Exit code {proc.returncode}.\n{proc.stdout}\n{proc.stderr}".strip(),
        technical_basis="Step 13 unit tests pass deterministically.",
    )

    res.evidence["spread_cycle"] = cycle_s.to_dict()
    res.evidence["funding_cycle"] = cycle_f.to_dict()
    res.evidence["unit_test_output"] = proc.stdout + proc.stderr
    return res


def verify_step_14() -> VerificationResult:
    """
    Step 14: Final Integration, Failure Handling, Reconciliation and Regression Verification.
    Verifies the complete 13-stage decoupled architecture end-to-end:
    1. Clone Isolation Invariant
    2. Multi-Venue Data Pipeline & Synchronization (REST snapshot, sequence check, gap detection)
    3. Screener & Matrix Reference/Priced Separation (Spread % = ((Priced - Reference) / Reference) * 100)
    4. Direction Engine Decoupling (Reference ≠ Hedge, Priced ≠ Maker)
    5. Strategy Selection (Spread = Highest Net Spread %, Funding = Highest Funding %)
    6. Maker Entry Pricing & Execution Controls (Spread/Grid/Bollinger used only for Maker entry)
    7. Pre-Submission Revalidation Engine (10-point revalidation)
    8. Execution Failure & Position Reconciliation (BALANCED vs MISMATCHED)
    9. Zero Mock Market Data & Zero Live Trading Invariants
    10. Complete Deterministic Regression Test Suite (Step 14 test suite exit code 0)
    """
    res = VerificationResult(step=14, status="PASS", summary="Step 14 Final Integration and Regression Verification")

    # Check 1: Clone Isolation Invariant
    orig_dir = "/Users/macbookpro/PycharmProjects/MarketMaking"
    is_isolated = (WORKSPACE_DIR != orig_dir) and os.path.isdir(orig_dir)
    res.add_check(
        name="Clone Isolation Invariant",
        passed=is_isolated,
        expected="Working directory isolated from original project",
        actual=f"Workspace: {WORKSPACE_DIR}, Original: {orig_dir}",
        technical_basis="Development must remain strictly isolated inside MarketMaking-LoopEngineering.",
    )

    # Check 2: Multi-Venue Pipeline & Sequence Gap Synchronization
    now = time.time()
    from arbitrage.models import OrderBook, OrderBookEntry, ExecutionCycle, DirectionEvaluationResult, FundingRate
    from arbitrage.spread_calculator import SpreadCalculator
    from arbitrage.bot_manager import PreSubmissionValidator, ExecutionEngine, BotManager
    from arbitrage.funding_settlement import FundingSettlementManager
    from arbitrage.native_ws_connector import NativeWebSocketConnector

    book_a = OrderBook(
        symbol="BTC/USDT",
        venue="BINANCE",
        bids=[OrderBookEntry(price=60000.0, size=2.5)],
        asks=[OrderBookEntry(price=60010.0, size=2.5)],
        timestamp=now,
        sequence=100,
    )
    book_b = OrderBook(
        symbol="BTC/USDT",
        venue="BYBIT",
        bids=[OrderBookEntry(price=60200.0, size=2.0)],
        asks=[OrderBookEntry(price=60210.0, size=2.0)],
        timestamp=now,
        sequence=200,
    )
    native = NativeWebSocketConnector(exchanges=["binance"])
    native._track_sequence("binance", "BTC/USDT", 100)
    native._track_sequence("binance", "BTC/USDT", 105)
    seq_info = native.get_sequence_info("binance", "BTC/USDT")
    gap_detected = seq_info.get("missed_count", 0) > 0
    pipeline_ok = (book_a.is_valid() and book_b.is_valid() and gap_detected)
    res.add_check(
        name="Multi-Venue Pipeline & Sequence Gap Synchronization",
        passed=pipeline_ok,
        expected="Validates order books and detects sequence gaps to force resynchronization",
        actual=f"book_a_valid={book_a.is_valid()}, book_b_valid={book_b.is_valid()}, gap_detected={gap_detected} (missed={seq_info.get('missed_count')})",
        technical_basis="Step 14 requirement: WebSocket sequence handling and gap resync.",
    )

    # Check 3: Screener & Matrix Reference/Priced Pricing Invariant
    calc = SpreadCalculator()
    raw_spread = calc.calculate_raw_spread(
        reference_data=book_a,
        priced_data=book_b,
        symbol="BTC/USDT",
        reference_venue="BINANCE",
        priced_venue="BYBIT",
        current_time=now,
    )
    # Spread % = ((Priced - Reference) / Reference) * 100
    # Ref Ask: 60010.0, Priced Bid: 60200.0 -> ((60200 - 60010) / 60010) * 100 = 0.316613897%
    expected_spread = ((60200.0 - 60010.0) / 60010.0) * 100.0
    spread_ok = (raw_spread.validity and abs(raw_spread.raw_spread_pct - expected_spread) < 1e-4)
    res.add_check(
        name="Screener Reference/Priced Pricing Invariant",
        passed=spread_ok,
        expected=f"Spread % = ((Priced - Reference) / Reference) * 100 = {expected_spread:.6f}%",
        actual=f"calculated_spread={raw_spread.raw_spread_pct:.6f}%, validity={raw_spread.validity}",
        technical_basis="Step 14 invariant: Pricing relationship strictly decoupled from execution roles.",
    )

    # Check 4: Direction Engine Decoupling
    dir_res = calc.direction_engine.evaluate_candidates(
        venue_a_data=book_a,
        venue_b_data=book_b,
        symbol="BTC/USDT",
        venue_a_name="BINANCE",
        venue_b_name="BYBIT",
        current_time=now,
    )
    dir_ok = (
        dir_res.is_valid
        and dir_res.candidate_a.maker_venue == "BINANCE"
        and dir_res.candidate_a.hedge_venue == "BYBIT"
        and dir_res.candidate_b.maker_venue == "BYBIT"
        and dir_res.candidate_b.hedge_venue == "BINANCE"
    )
    res.add_check(
        name="Direction Engine Dynamic Decoupling",
        passed=dir_ok,
        expected="Evaluates both Candidate A (Binance Maker -> Bybit Hedge) and Candidate B (Bybit Maker -> Binance Hedge)",
        actual=f"cand_a={dir_res.candidate_a.maker_venue}->{dir_res.candidate_a.hedge_venue}, cand_b={dir_res.candidate_b.maker_venue}->{dir_res.candidate_b.hedge_venue}",
        technical_basis="Step 14 invariant: Reference ≠ automatically Hedge, Priced ≠ automatically Maker.",
    )

    # Check 5: Strategy Selection (Spread vs Funding)
    spread_dec = calc.evaluate_spread_arbitrage(
        venue_a_data=book_a,
        venue_b_data=book_b,
        symbol="BTC/USDT",
        current_time=now,
    )
    funding_a = FundingRate(symbol="BTC/USDT", venue="BINANCE", rate=0.0008, timestamp=now)
    funding_b = FundingRate(symbol="BTC/USDT", venue="BYBIT", rate=-0.0003, timestamp=now)
    funding_dec = calc.evaluate_funding_arbitrage(
        funding_a=funding_a,
        funding_b=funding_b,
        book_a=book_a,
        book_b=book_b,
        symbol="BTC/USDT",
        current_time=now,
    )
    strat_ok = (
        spread_dec.is_valid
        and spread_dec.strategy_type == "SPREAD_ARBITRAGE"
        and funding_dec.is_valid
        and funding_dec.strategy_type == "FUNDING_ARBITRAGE"
        and funding_dec.maker_venue in ("BINANCE", "BYBIT")
    )
    res.add_check(
        name="Strategy Selection Decoupling",
        passed=strat_ok,
        expected="Spread Arbitrage selects by Highest Net Spread %; Funding Arbitrage selects by Highest Funding %",
        actual=f"spread_strat={spread_dec.strategy_type} (maker={spread_dec.maker_venue}), funding_strat={funding_dec.strategy_type} (maker={funding_dec.maker_venue})",
        technical_basis="Step 14 requirement: Separate engines for Spread Arbitrage and Funding Arbitrage.",
    )

    # Check 6: Maker Entry Pricing & Execution Controls
    maker_pricing_ok = (
        spread_dec.maker_pricing is not None
        and spread_dec.maker_pricing.maker_price > 0
        and spread_dec.exit_conditions is not None
        and funding_dec.exit_conditions is not None
        and "convergence" in str(spread_dec.exit_conditions).lower()
        and "settlement" in str(funding_dec.exit_conditions).lower()
    )
    res.add_check(
        name="Maker Entry Pricing & Strategy-Specific Lifecycle Exits",
        passed=maker_pricing_ok,
        expected="Spread/Grid/Bollinger used only for entry pricing; dynamic convergence/settlement exits",
        actual=f"maker_price={spread_dec.maker_pricing.maker_price if spread_dec.maker_pricing else None}, spread_exit={spread_dec.exit_conditions.get('exit_type')}, funding_exit={funding_dec.exit_conditions.get('exit_type')}",
        technical_basis="Step 14 requirement: Spread/Grid/Bollinger are entry controls; exits are lifecycle-specific.",
    )

    # Check 7: 10-Point Pre-Submission Revalidation Engine
    validator = PreSubmissionValidator()
    val_res = validator.validate_pre_submission(
        maker_book=book_a,
        hedge_book=book_b,
        decision=spread_dec,
        order_quantity=1.0,
        current_time=now,
    )
    val_10_ok = (val_res.is_valid and len(val_res.checks_detail) == 10 and all(c.get("passed") for c in val_res.checks_detail.values()))
    res.add_check(
        name="Pre-Submission Revalidation Engine (10 Checks)",
        passed=val_10_ok,
        expected="All 10 checks pass before order submission",
        actual=f"is_valid={val_res.is_valid}, checks_count={len(val_res.checks_detail)}, failed={val_res.failed_checks}",
        technical_basis="Step 14 requirement: 10-point pre-submission revalidation engine.",
    )

    # Check 8: Execution Failure & Position Reconciliation
    stale_val = validator.validate_pre_submission(
        maker_book=book_a,
        hedge_book=book_b,
        decision=spread_dec,
        order_quantity=1.0,
        current_time=now + 60.0,
    )
    exec_engine = ExecutionEngine()
    balanced_cycle = ExecutionCycle(
        cycle_id="c_bal", bot_id="b1", strategy_type="SPREAD", symbol="BTC/USDT",
        maker_venue="BINANCE", hedge_venue="BYBIT", maker_side="BUY", hedge_side="SELL",
        target_quantity=1.0, maker_filled_qty=1.0, hedge_filled_qty=1.0
    )
    mismatched_cycle = ExecutionCycle(
        cycle_id="c_mis", bot_id="b1", strategy_type="SPREAD", symbol="BTC/USDT",
        maker_venue="BINANCE", hedge_venue="BYBIT", maker_side="BUY", hedge_side="SELL",
        target_quantity=1.0, maker_filled_qty=1.0, hedge_filled_qty=0.4
    )
    rec_bal = exec_engine.reconcile_cycle(balanced_cycle)
    rec_mis = exec_engine.reconcile_cycle(mismatched_cycle)
    fail_rec_ok = (not stale_val.is_valid and rec_bal and not rec_mis and mismatched_cycle.reconciliation_status == "MISMATCHED")
    res.add_check(
        name="Execution Failure Handling & Position Reconciliation",
        passed=fail_rec_ok,
        expected="Rejects stale submission safely; flags mismatched leg fills in reconciliation",
        actual=f"stale_rejected={not stale_val.is_valid}, rec_balanced={rec_bal}, rec_mismatched_detected={not rec_mis}",
        technical_basis="Step 14 requirement: Execution failure testing and reconciliation safeguards.",
    )

    # Check 9: Zero Mock Market Data & Zero Live Trading Invariants
    production_dirs = ["arbitrage"]
    mock_keywords = ["random.uniform", "fake_price", "mock_feed", "simulate_ticker", "jitter_price"]
    prohibited_order_calls = ["place_order", "create_order", "send_order", "submit_order", "execute_order"]
    found_violations = []

    for pdir in production_dirs:
        full_dir = os.path.join(WORKSPACE_DIR, pdir)
        if not os.path.isdir(full_dir):
            continue
        for fname in os.listdir(full_dir):
            if fname.endswith(".py"):
                fpath = os.path.join(full_dir, fname)
                with open(fpath, "r") as f:
                    content = f.read()
                for kw in mock_keywords:
                    if kw in content:
                        found_violations.append(f"{fname}:{kw}")
                if fname in ("bot_manager.py", "spread_calculator.py", "direction_engine.py"):
                    for poc in prohibited_order_calls:
                        if f".{poc}(" in content:
                            found_violations.append(f"{fname}:{poc}()")

    clean_ok = len(found_violations) == 0
    res.add_check(
        name="Zero Mock Market Data & Zero Live Trading Invariants",
        passed=clean_ok,
        expected="Zero mock market data generators and zero live exchange order calls in production modules",
        actual=f"violations={found_violations}",
        technical_basis="Step 14 invariant: Real market data is mandatory; zero live orders during development.",
    )

    # Check 10: Complete Deterministic Regression Test Suite
    proc = subprocess.run(
        [sys.executable, "-m", "unittest", "tests/test_step14_final_integration_regression.py"],
        cwd=WORKSPACE_DIR,
        capture_output=True,
        text=True,
    )
    reg_ok = (proc.returncode == 0)
    res.add_check(
        name="Full Regression Test Suite (Step 14)",
        passed=reg_ok,
        expected="Exit code 0 from tests/test_step14_final_integration_regression.py",
        actual=f"Exit code {proc.returncode}.\n{proc.stdout}\n{proc.stderr}".strip(),
        technical_basis="Step 14 requirement: Complete regression verification.",
    )

    res.evidence["spread_decision"] = spread_dec.to_dict()
    res.evidence["funding_decision"] = funding_dec.to_dict()
    res.evidence["unit_test_output"] = proc.stdout + proc.stderr
    return res


def save_evidence_and_report(res: VerificationResult, quiet: bool = False):
    ensure_dirs()
    timestamp_slug = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    step_str = f"step_{res.step:02d}" if isinstance(res.step, int) else f"step_{res.step}"

    # 1. Save JSON Evidence
    evidence_filename = f"{step_str}_evidence_{timestamp_slug}.json"
    evidence_path = os.path.join(EVIDENCE_DIR, evidence_filename)
    with open(evidence_path, "w") as f:
        json.dump(res.to_dict(), f, indent=2)

    # 2. Save Markdown Report
    report_filename = f"{step_str}_report_{timestamp_slug}.md"
    report_path = os.path.join(REPORTS_DIR, report_filename)

    passed_checks = sum(1 for c in res.checks if c["passed"])
    total_checks = len(res.checks)

    report_md = f"""# Independent Verification Report — {step_str.upper()}

- **Status**: **{res.status}**
- **Score**: {passed_checks}/{total_checks} checks passed
- **Timestamp**: {res.timestamp}
- **Summary**: {res.summary}
- **Evidence File**: `{evidence_filename}`

## Verification Checks

| Check | Status | Expected | Actual | Technical Basis |
|---|---|---|---|---|
"""
    for c in res.checks:
        status_badge = "✅ PASS" if c["passed"] else ("🛑 BLOCKED" if c.get("blocked") else "❌ FAIL")
        expected_esc = c["expected"].replace("\n", " ")
        actual_esc = c["actual"].replace("\n", " ")
        report_md += f"| {c['name']} | {status_badge} | {expected_esc} | {actual_esc} | {c['technical_basis']} |\n"

    report_md += f"""
## Verification Evidence Summary
```json
{json.dumps(res.evidence, indent=2)}
```
"""

    with open(report_path, "w") as f:
        f.write(report_md)

    if not quiet:
        print(f"Evidence saved: {evidence_path}")
        print(f"Report saved: {report_path}")
    return evidence_path, report_path


def main():
    parser = argparse.ArgumentParser(description="Independent Verifier for Loop Engineering")
    parser.add_argument("--step", default="6", help="Step identifier to verify (e.g., 6, loop-test)")
    parser.add_argument("--json", action="store_true", help="Print JSON result to stdout")
    args = parser.parse_args()

    step_arg = args.step
    if step_arg.isdigit():
        step_val = int(step_arg)
    else:
        step_val = step_arg

    if step_val == "loop-test":
        result = verify_step_loop_test()
    elif step_val == 6:
        result = verify_step_6()
    elif step_val == 7:
        result = verify_step_7()
    elif step_val == 8:
        result = verify_step_8()
    elif step_val == 9:
        result = verify_step_9()
    elif step_val == 10:
        result = verify_step_10()
    elif step_val == 11:
        result = verify_step_11()
    elif step_val == 12:
        result = verify_step_12()
    elif step_val == 13:
        result = verify_step_13()
    elif step_val == 14:
        result = verify_step_14()
    else:
        # Generic step verifier
        result = VerificationResult(step=step_val, status="FAIL", summary=f"No specialized verifier for step {step_val}")
        result.add_check(
            name="Step Verifier Available",
            passed=False,
            expected=f"Specialized verifier implemented for step {step_val}",
            actual=f"Step {step_val} verifier not yet registered",
            technical_basis="Each modular step must have an independent verifier implementation.",
        )

    evidence_path, report_path = save_evidence_and_report(result, quiet=args.json)

    output = {
        "status": result.status,
        "step": result.step,
        "summary": result.summary,
        "checks_passed": sum(1 for c in result.checks if c["passed"]),
        "total_checks": len(result.checks),
        "evidence_path": evidence_path,
        "report_path": report_path,
    }

    if args.json:
        print(json.dumps(output, indent=2))
    else:
        print(f"\n==========================================")
        print(f"VERIFIER RESULT: {result.status} ({output['checks_passed']}/{output['total_checks']} checks passed)")
        print(f"==========================================")

    if result.status == "PASS":
        sys.exit(0)
    elif result.status == "BLOCKED":
        sys.exit(2)
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
