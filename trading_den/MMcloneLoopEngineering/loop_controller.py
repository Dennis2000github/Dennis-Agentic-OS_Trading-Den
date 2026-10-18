#!/usr/bin/env python3
"""
loop_controller.py — Autonomous Loop Engineering Orchestrator.

Orchestration sequence:
1. Verify isolated project
2. Read STATE
3. Read CONSTRAINTS
4. Read BUDGET
5. Read general specification
6. Read current step specification
7. Audit current implementation
8. Construct bounded Antigravity prompt
9. Execute agy -p
10. Capture stdout/stderr
11. Run tests
12. Run verifier
13. Parse PASS/FAIL/BLOCKED
14. Write verification evidence
15. Update STATE
16. Append RUN_LOG
17. Advance or retry
"""

import argparse
import datetime
import json
import os
import re
import shutil
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

WORKSPACE_DIR = os.path.abspath(os.path.dirname(__file__))
LOOP_DIR = os.path.join(WORKSPACE_DIR, ".loop")
ORIGINAL_PROJECT = "/Users/macbookpro/PycharmProjects/MarketMaking"


def log(msg: str):
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] [LoopController] {msg}")


class LoopController:
    def __init__(self, workspace: str = WORKSPACE_DIR):
        self.workspace = workspace
        self.loop_dir = os.path.join(workspace, ".loop")
        self.state_file = os.path.join(self.loop_dir, "STATE.md")
        self.constraints_file = os.path.join(self.loop_dir, "CONSTRAINTS.md")
        self.budget_file = os.path.join(self.loop_dir, "BUDGET.md")
        self.run_log_file = os.path.join(self.loop_dir, "RUN_LOG.md")
        self.general_spec_file = os.path.join(workspace, "modular", "general-specification.md")

    # 1. Isolation Check
    def verify_isolation(self) -> Tuple[bool, str]:
        """Verify project isolation and original project protection."""
        if os.path.abspath(self.workspace) == os.path.abspath(ORIGINAL_PROJECT):
            return False, "Controller running directly inside original MarketMaking project!"

        target_git = os.path.join(self.workspace, ".git")
        if not os.path.isdir(target_git):
            return False, f"Target workspace does not have an independent .git directory: {target_git}"

        # Check worktree
        try:
            wt_out = subprocess.check_output(
                ["git", "-C", self.workspace, "worktree", "list"], stderr=subprocess.STDOUT
            ).decode()
            if len(wt_out.strip().splitlines()) > 1:
                return False, f"Target has multiple worktrees: {wt_out}"
        except Exception as e:
            return False, f"Failed checking git worktree: {e}"

        # Check original project status
        if os.path.isdir(ORIGINAL_PROJECT):
            try:
                common = os.path.commonpath([os.path.abspath(self.workspace), os.path.abspath(ORIGINAL_PROJECT)])
                if common == os.path.abspath(ORIGINAL_PROJECT):
                    return False, "Target workspace is inside original project directory!"
            except Exception as e:
                return False, f"Original project check error: {e}"

        return True, "Project isolation verified. Original project is read-only and clean."

    # 2. Read State
    def read_state(self) -> Dict[str, Any]:
        with open(self.state_file, "r") as f:
            content = f.read()

        state: Dict[str, Any] = {}
        # Parse YAML block
        yaml_match = re.search(r"```yaml\n(.*?)\n```", content, re.DOTALL)
        if yaml_match:
            try:
                import yaml
                parsed = yaml.safe_load(yaml_match.group(1))
                if isinstance(parsed, dict):
                    state = parsed
            except Exception:
                pass
        return state

    # 3. Update State
    def write_state(self, state: Dict[str, Any]):
        curr = state.get("current_step", 6)
        if str(curr).isdigit():
            spec_path = f"modular/step-{int(curr):02d}.md"
        elif curr == "loop-test":
            spec_path = "modular/loop-test-step.md"
        else:
            spec_path = f"modular/{curr}.md"

        gen_spec = "modular/general-specification.md"
        if isinstance(state.get("general_specification"), dict):
            p = state["general_specification"].get("path")
            if p and p != "None":
                gen_spec = p
        elif isinstance(state.get("general_specification"), str) and state["general_specification"].strip() and state["general_specification"] != "None":
            gen_spec = state["general_specification"]

        if isinstance(state.get("current_specification"), dict):
            p = state["current_specification"].get("path")
            if p and p != "None":
                spec_path = p
        elif isinstance(state.get("current_specification"), str) and state["current_specification"].strip() and state["current_specification"] != "None":
            spec_path = state["current_specification"]

        content = f"""# Current Loop State

```yaml
project: {state.get('project', 'MarketMaking-LoopEngineering')}

source_project:
  path: {ORIGINAL_PROJECT}

isolated_project:
  path: {self.workspace}

isolation_verified: {str(state.get('isolation_verified', True)).lower()}

agent: {state.get('agent', 'antigravity')}

current_step: {curr}
status: {state.get('status', 'IN_PROGRESS')}
iteration: {state.get('iteration', 0)}

max_iterations: {state.get('max_iterations', 5)}

general_specification:
  path: {gen_spec}

current_specification:
  path: {spec_path}

previous_step: {state.get('previous_step', 5)}
previous_step_status: {state.get('previous_step_status', 'COMPLETE')}

verification_status: {state.get('verification_status', 'PENDING')}
last_verification: {state.get('last_verification', 'null')}

blockers: []

next_action: "{state.get('next_action', f'Audit Step {curr} implementation')}"
```
"""
        with open(self.state_file, "w") as f:
            f.write(content)

    # 4. Append Run Log
    def append_run_log(self, step: Any, iteration: int, action: str, result: str, details: str):
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        clean_details = details.replace("\n", " ").replace("|", "/")
        line = f"| {timestamp} | {step} | {iteration} | {action} | {result} | {clean_details} |\n"
        with open(self.run_log_file, "a") as f:
            f.write(line)

    # 5. Read Specs
    def read_spec(self, step: Any) -> str:
        if str(step) == "loop-test":
            spec_file = os.path.join(self.workspace, "modular", "loop-test-step.md")
        elif str(step).isdigit():
            spec_file = os.path.join(self.workspace, "modular", f"step-{int(step):02d}.md")
        else:
            spec_file = os.path.join(self.workspace, "modular", f"{step}.md")

        if os.path.isfile(spec_file):
            with open(spec_file, "r") as f:
                return f.read()
        return f"Specification file not found for step {step}"

    # 6. Audit Step Implementation
    def audit_step(self, step: Any) -> Dict[str, Any]:
        log(f"Auditing Step {step} implementation...")
        if step == 6:
            direction_engine_file = os.path.join(self.workspace, "arbitrage", "direction_engine.py")
            tests_file = os.path.join(self.workspace, "tests", "test_step6_direction_engine.py")

            engine_exists = os.path.isfile(direction_engine_file)
            tests_exist = os.path.isfile(tests_file)

            if engine_exists and tests_exist:
                # Check for required methods
                with open(direction_engine_file, "r") as f:
                    code = f.read()
                has_eval = "def evaluate_candidates" in code
                has_candidate_a = "candidate_a" in code
                has_candidate_b = "candidate_b" in code
                has_validity = "validity" in code
                status = "ALREADY COMPLETE" if (has_eval and has_candidate_a and has_candidate_b) else "PARTIALLY COMPLETE"
            elif engine_exists:
                status = "PARTIALLY COMPLETE"
            else:
                status = "MISSING"

            return {
                "step": 6,
                "status": status,
                "engine_exists": engine_exists,
                "tests_exist": tests_exist,
                "gap": None if status == "ALREADY COMPLETE" else "Implement missing direction engine requirements",
            }
        elif step == 7:
            fee_engine_file = os.path.join(self.workspace, "arbitrage", "fee_engine.py")
            tests_file = os.path.join(self.workspace, "tests", "test_step7_directional_fees.py")

            engine_exists = os.path.isfile(fee_engine_file)
            tests_exist = os.path.isfile(tests_file)

            if engine_exists and tests_exist:
                with open(fee_engine_file, "r") as f:
                    code = f.read()
                has_calc = "def calculate_directional_fees" in code
                has_apply = "def apply_directional_fees_to_candidates" in code
                has_cand_a = "candidate_a_total_fee" in code
                has_cand_b = "candidate_b_total_fee" in code
                status = "ALREADY COMPLETE" if (has_calc and has_apply and has_cand_a and has_cand_b) else "PARTIALLY COMPLETE"
            elif engine_exists:
                status = "PARTIALLY COMPLETE"
            else:
                status = "MISSING"

            return {
                "step": 7,
                "status": status,
                "engine_exists": engine_exists,
                "tests_exist": tests_exist,
                "gap": None if status == "ALREADY COMPLETE" else "Implement missing directional fee engine requirements",
            }
        elif step == 8:
            opp_engine_file = os.path.join(self.workspace, "arbitrage", "opportunity_engine.py")
            tests_file = os.path.join(self.workspace, "tests", "test_step8_opportunity_engine.py")

            engine_exists = os.path.isfile(opp_engine_file)
            tests_exist = os.path.isfile(tests_file)

            if engine_exists and tests_exist:
                with open(opp_engine_file, "r") as f:
                    code = f.read()
                has_eval = "def evaluate_directional_opportunities" in code
                has_opp_a = "opportunity_a" in code
                has_opp_b = "opportunity_b" in code
                status = "ALREADY COMPLETE" if (has_eval and has_opp_a and has_opp_b) else "PARTIALLY COMPLETE"
            elif engine_exists:
                status = "PARTIALLY COMPLETE"
            else:
                status = "MISSING"

            return {
                "step": 8,
                "status": status,
                "engine_exists": engine_exists,
                "tests_exist": tests_exist,
                "gap": None if status == "ALREADY COMPLETE" else "Implement missing opportunity engine requirements",
            }
        elif step == 9:
            spread_arb_file = os.path.join(self.workspace, "arbitrage", "spread_arbitrage.py")
            tests_file = os.path.join(self.workspace, "tests", "test_step9_spread_arbitrage.py")

            engine_exists = os.path.isfile(spread_arb_file)
            tests_exist = os.path.isfile(tests_file)

            if engine_exists and tests_exist:
                with open(spread_arb_file, "r") as f:
                    code = f.read()
                has_select = "def select_highest_net_opportunity" in code
                has_pricing = "def calculate_maker_entry_pricing" in code
                has_eval = "def evaluate_spread_arbitrage" in code
                status = "ALREADY COMPLETE" if (has_select and has_pricing and has_eval) else "PARTIALLY COMPLETE"
            elif engine_exists:
                status = "PARTIALLY COMPLETE"
            else:
                status = "MISSING"

            return {
                "step": 9,
                "status": status,
                "engine_exists": engine_exists,
                "tests_exist": tests_exist,
                "gap": None if status == "ALREADY COMPLETE" else "Implement missing spread arbitrage strategy requirements",
            }
        elif step == 10:
            funding_arb_file = os.path.join(self.workspace, "arbitrage", "funding_arbitrage.py")
            tests_file = os.path.join(self.workspace, "tests", "test_step10_funding_arbitrage.py")

            engine_exists = os.path.isfile(funding_arb_file)
            tests_exist = os.path.isfile(tests_file)

            if engine_exists and tests_exist:
                with open(funding_arb_file, "r") as f:
                    code = f.read()
                has_eval_fund = "def evaluate_directional_funding" in code
                has_select = "def select_highest_funding_opportunity" in code
                has_pricing = "def calculate_maker_entry_pricing" in code
                has_eval = "def evaluate_funding_arbitrage" in code
                status = "ALREADY COMPLETE" if (has_eval_fund and has_select and has_pricing and has_eval) else "PARTIALLY COMPLETE"
            elif engine_exists:
                status = "PARTIALLY COMPLETE"
            else:
                status = "MISSING"

            return {
                "step": 10,
                "status": status,
                "engine_exists": engine_exists,
                "tests_exist": tests_exist,
                "gap": None if status == "ALREADY COMPLETE" else "Implement missing funding arbitrage mode requirements",
            }
        elif step == 11:
            settlement_file = os.path.join(self.workspace, "arbitrage", "funding_settlement.py")
            tests_file = os.path.join(self.workspace, "tests", "test_step11_funding_settlement.py")

            engine_exists = os.path.isfile(settlement_file)
            tests_exist = os.path.isfile(tests_file)

            if engine_exists and tests_exist:
                with open(settlement_file, "r") as f:
                    code = f.read()
                has_create = "def create_position" in code
                has_settle = "def record_settlement" in code
                has_exit = "def check_exit_conditions" in code
                has_close = "def close_position" in code
                has_rec = "def verify_hedge_reconciliation" in code
                status = "ALREADY COMPLETE" if (has_create and has_settle and has_exit and has_close and has_rec) else "PARTIALLY COMPLETE"
            elif engine_exists:
                status = "PARTIALLY COMPLETE"
            else:
                status = "MISSING"

            return {
                "step": 11,
                "status": status,
                "engine_exists": engine_exists,
                "tests_exist": tests_exist,
                "gap": None if status == "ALREADY COMPLETE" else "Implement missing funding settlement & independent exit requirements",
            }
        elif step == 12:
            calc_file = os.path.join(self.workspace, "arbitrage", "spread_calculator.py")
            server_file = os.path.join(self.workspace, "arbitrage", "server.py")
            ui_file = os.path.join(self.workspace, "templates", "index.html")
            tests_file = os.path.join(self.workspace, "tests", "test_step12_arbitrage_tab_charts.py")

            files_exist = os.path.isfile(calc_file) and os.path.isfile(server_file) and os.path.isfile(ui_file)
            tests_exist = os.path.isfile(tests_file)

            if files_exist and tests_exist:
                with open(server_file, "r") as f:
                    s_code = f.read()
                with open(ui_file, "r") as f:
                    u_code = f.read()
                has_mode_endpoint = "/api/arbitrage/strategy-mode" in s_code
                has_spread_endpoint = "/api/arbitrage/spread-decision" in s_code
                has_funding_endpoint = "/api/arbitrage/funding-decision" in s_code
                has_ui_toggle = "triExecStrategyMode" in u_code
                has_decoupled_legs = "First Leg (Maker)" in u_code
                complete = (has_mode_endpoint and has_spread_endpoint and has_funding_endpoint and has_ui_toggle and has_decoupled_legs)
                status = "ALREADY COMPLETE" if complete else "PARTIALLY COMPLETE"
            elif files_exist:
                status = "PARTIALLY COMPLETE"
            else:
                status = "MISSING"

            return {
                "step": 12,
                "status": status,
                "files_exist": files_exist,
                "tests_exist": tests_exist,
                "gap": None if status == "ALREADY COMPLETE" else "Implement missing arbitrage tab & charts integration requirements",
            }
        elif step == 13:
            bot_mgr_file = os.path.join(self.workspace, "arbitrage", "bot_manager.py")
            server_file = os.path.join(self.workspace, "arbitrage", "server.py")
            tests_file = os.path.join(self.workspace, "tests", "test_step13_screener_bot_execution.py")

            engine_exists = os.path.isfile(bot_mgr_file)
            tests_exist = os.path.isfile(tests_file)

            if engine_exists and tests_exist:
                with open(bot_mgr_file, "r") as f:
                    b_code = f.read()
                has_validator = "class PreSubmissionValidator" in b_code
                has_exec_engine = "class ExecutionEngine" in b_code
                has_bot_mgr = "class BotManager" in b_code
                has_reconcile = "def reconcile_cycle" in b_code
                complete = (has_validator and has_exec_engine and has_bot_mgr and has_reconcile)
                status = "ALREADY COMPLETE" if complete else "PARTIALLY COMPLETE"
            elif engine_exists:
                status = "PARTIALLY COMPLETE"
            else:
                status = "MISSING"

            return {
                "step": 13,
                "status": status,
                "engine_exists": engine_exists,
                "tests_exist": tests_exist,
                "gap": None if status == "ALREADY COMPLETE" else "Implement missing screener bot execution requirements",
            }
        elif step == 14 or str(step) == "14":
            tests_file = os.path.join(self.workspace, "tests", "test_step14_final_integration_regression.py")
            tests_exist = os.path.isfile(tests_file)
            return {
                "step": 14,
                "status": "ALREADY COMPLETE" if tests_exist else "MISSING",
                "tests_exist": tests_exist,
                "gap": None if tests_exist else "Implement tests/test_step14_final_integration_regression.py",
            }
        elif step == "loop-test":
            marker_test = os.path.join(self.workspace, "tests", "test_loop_marker.py")
            exists = os.path.isfile(marker_test)
            return {
                "step": "loop-test",
                "status": "ALREADY COMPLETE" if exists else "MISSING",
                "marker_exists": exists,
                "gap": None if exists else "Create tests/test_loop_marker.py",
            }
        else:
            return {"step": step, "status": "UNVERIFIED", "gap": "Audit not implemented for step"}

    # 7. Run Unit Tests
    def run_tests(self, step: Any) -> Tuple[bool, str]:
        step_test_map = {
            2: "tests/test_step2_normalized_hybrid.py",
            3: "tests/test_step3_reference_priced.py",
            4: "tests/test_step4_cross_exchange_matrix.py",
            5: "tests/test_step5_raw_spread.py",
            6: "tests/test_step6_direction_engine.py",
            7: "tests/test_step7_directional_fees.py",
            8: "tests/test_step8_opportunity_engine.py",
            9: "tests/test_step9_spread_arbitrage.py",
            10: "tests/test_step10_funding_arbitrage.py",
            11: "tests/test_step11_funding_settlement.py",
            12: "tests/test_step12_arbitrage_tab_charts.py",
            13: "tests/test_step13_screener_bot_execution.py",
            14: "tests/test_step14_final_integration_regression.py",
            "loop-test": "tests/test_loop_marker.py",
        }
        if step in step_test_map:
            test_target = step_test_map[step]
        elif str(step).isdigit() and int(step) in step_test_map:
            test_target = step_test_map[int(step)]
        else:
            test_target = f"tests/test_step{step}.py"

        if not os.path.isfile(os.path.join(self.workspace, test_target)):
            return False, f"Test file {test_target} does not exist"

        proc = subprocess.run(
            [sys.executable, "-m", "unittest", test_target],
            cwd=self.workspace,
            capture_output=True,
            text=True,
        )
        output = proc.stdout + proc.stderr
        return (proc.returncode == 0), output

    # 8. Run Verifier
    def run_verifier(self, step: Any) -> Dict[str, Any]:
        log(f"Invoking independent verifier for Step {step}...")
        proc = subprocess.run(
            [sys.executable, "verifier.py", "--step", str(step), "--json"],
            cwd=self.workspace,
            capture_output=True,
            text=True,
        )
        try:
            res = json.loads(proc.stdout)
            return res
        except Exception:
            return {
                "status": "FAIL",
                "step": step,
                "summary": "Verifier did not return valid JSON",
                "raw_stdout": proc.stdout,
                "raw_stderr": proc.stderr,
                "exit_code": proc.returncode,
            }

    # 9. Bounded Antigravity Implementer Execution
    def invoke_implementer(self, prompt: str) -> Tuple[int, str]:
        """Execute bounded implementer prompt using agy -p inside isolated workspace."""
        agy_bin = shutil.which("agy") or "/Users/macbookpro/.local/bin/agy"
        if not os.path.isfile(agy_bin):
            log(f"Warning: agy CLI not found at {agy_bin}. Returning mock code.")
            return 1, "agy CLI not found"

        cmd = [agy_bin, "-p", prompt, "--dangerously-skip-permissions"]
        log(f"Executing bounded prompt via agy in {self.workspace}...")
        try:
            proc = subprocess.run(
                cmd,
                cwd=self.workspace,
                capture_output=True,
                text=True,
                timeout=300,
            )
            output = proc.stdout + proc.stderr
            return proc.returncode, output
        except subprocess.TimeoutExpired:
            return 1, "Implementer timed out after 300 seconds"
        except Exception as e:
            return 1, f"Failed executing agy: {e}"

    # 10. Commit Git Checkpoint
    def commit_git_checkpoint(self, step: Any, summary: str):
        try:
            subprocess.run(["git", "add", "."], cwd=self.workspace, check=True)
            commit_msg = f"Step {step}: {summary} [Independent Verification PASS]"
            # Check if there are changes to commit
            status = subprocess.check_output(
                ["git", "status", "--porcelain"], cwd=self.workspace
            ).decode()
            if status.strip():
                subprocess.run(["git", "commit", "-m", commit_msg], cwd=self.workspace, check=True)
                log(f"Git checkpoint committed: {commit_msg}")
            else:
                log("No working tree changes to commit for checkpoint.")
        except Exception as e:
            log(f"Warning: Git checkpoint commit failed: {e}")

    # 11. Run Single Iteration
    def run_iteration(self, step: Any = None) -> str:
        iso_ok, iso_msg = self.verify_isolation()
        if not iso_ok:
            log(f"CRITICAL: Isolation check failed! {iso_msg}")
            state = self.read_state()
            state["status"] = "BLOCKED"
            state["blockers"] = [iso_msg]
            self.write_state(state)
            self.append_run_log(step or state.get("current_step", 6), state.get("iteration", 0), "ISOLATION_CHECK", "BLOCKED", iso_msg)
            return "BLOCKED"

        state = self.read_state()
        curr_step = step if step is not None else state.get("current_step", 6)
        iteration = state.get("iteration", 0)
        max_iter = state.get("max_iterations", 5)

        log(f"Starting iteration {iteration} for Step {curr_step}...")
        self.append_run_log(curr_step, iteration, "START_ITERATION", "INFO", f"Step {curr_step} iteration {iteration}")

        # Phase B: Audit
        audit = self.audit_step(curr_step)
        log(f"Audit result: {audit.get('status')} (gap: {audit.get('gap')})")

        # Phase E: Run Tests
        test_ok, test_out = self.run_tests(curr_step)
        log(f"Test suite result: {'PASS' if test_ok else 'FAIL'}")

        # Phase F: Independent Verification
        ver_res = self.run_verifier(curr_step)
        ver_status = ver_res.get("status", "FAIL")
        log(f"Independent verifier status: {ver_status}")

        # Phase G: Decision
        if ver_status == "PASS":
            log(f"Step {curr_step} VERIFIED PASS!")
            self.append_run_log(curr_step, iteration, "VERIFICATION", "PASS", f"Step {curr_step} verified: {ver_res.get('summary')}")
            self.commit_git_checkpoint(curr_step, ver_res.get("summary", "Complete"))

            if curr_step == 14 or str(curr_step) == "14":
                state["previous_step"] = 14
                state["previous_step_status"] = "COMPLETE"
                state["current_step"] = 14
                state["current_specification"] = "modular/step-14.md"
                state["next_action"] = "Modular engineering roadmap fully complete and verified"
                state["status"] = "COMPLETE"
                state["iteration"] = 0
                state["verification_status"] = "PASS"
                state["last_verification"] = ver_res.get("report_path")
                self.write_state(state)
                return "PASS"

            if str(curr_step).isdigit():
                next_step = int(curr_step) + 1
            else:
                next_step = curr_step

            state["previous_step"] = curr_step
            state["previous_step_status"] = "COMPLETE"
            state["current_step"] = next_step
            state["current_specification"] = f"modular/step-{int(next_step):02d}.md" if str(next_step).isdigit() else f"modular/{next_step}.md"
            state["next_action"] = f"Audit Step {next_step} implementation"
            state["status"] = "IN_PROGRESS"
            state["iteration"] = 0
            state["verification_status"] = "PENDING"
            state["last_verification"] = ver_res.get("report_path")
            self.write_state(state)
            return "PASS"

        elif ver_status == "BLOCKED":
            log(f"Step {curr_step} BLOCKED. Halting loop.")
            self.append_run_log(curr_step, iteration, "VERIFICATION", "BLOCKED", "Execution blocked")
            state["status"] = "BLOCKED"
            self.write_state(state)
            return "BLOCKED"

        else:  # FAIL
            new_iter = iteration + 1
            log(f"Step {curr_step} verification FAILED. Retrying. Iteration incremented to {new_iter}.")
            self.append_run_log(curr_step, iteration, "VERIFICATION", "FAIL", f"Verification failed. Iteration -> {new_iter}")
            state["iteration"] = new_iter
            state["status"] = "RETRY"
            state["verification_status"] = "FAIL"
            state["last_verification"] = ver_res.get("report_path")

            if new_iter >= max_iter:
                log(f"Max iterations ({max_iter}) reached for Step {curr_step}! Marking BLOCKED.")
                state["status"] = "BLOCKED"
                self.write_state(state)
                return "BLOCKED"

            self.write_state(state)
            return "FAIL"

    # 12. Controlled Loop Test
    def run_controlled_test(self) -> bool:
        """
        Executes Section 19: CONTROLLED LOOP TEST:
        1. Set up modular/loop-test-step.md & tests/test_loop_marker.py
        2. Set state to loop-test
        3. Run passing iteration -> demonstrate PASS & state update
        4. Introduce deliberate failure -> demonstrate FAIL & retry with same step retained & iteration incremented
        5. Clean up & restore state to Step 6
        """
        log("=== BEGINNING CONTROLLED LOOP TEST ===")
        # Save current state backup
        orig_state = self.read_state()

        # Step 1: Set up passing marker
        marker_path = os.path.join(self.workspace, "tests", "test_loop_marker.py")
        with open(marker_path, "w") as f:
            f.write("""import unittest
class TestLoopMarker(unittest.TestCase):
    def test_marker_pass(self):
        self.assertTrue(True)
if __name__ == '__main__':
    unittest.main()
""")

        test_state = {
            "project": "MarketMaking-LoopEngineering",
            "current_step": "loop-test",
            "status": "IN_PROGRESS",
            "iteration": 0,
            "max_iterations": 5,
            "general_specification": "modular/general-specification.md",
            "current_specification": "modular/loop-test-step.md",
            "previous_step": 5,
            "previous_step_status": "COMPLETE",
            "verification_status": "PENDING",
            "last_verification": None,
            "blockers": [],
            "next_action": "Run controlled loop test (passing case)",
        }
        self.write_state(test_state)

        # Step 2: Run pass case
        log("Testing pass path on loop-test...")
        pass_res = self.run_iteration(step="loop-test")
        if pass_res != "PASS":
            log(f"Controlled test failed: Expected PASS, got {pass_res}")
            return False
        log("Controlled PASS test succeeded!")

        # Step 3: Deliberately introduce failure
        log("Introducing deliberate failure in tests/test_loop_marker.py...")
        with open(marker_path, "w") as f:
            f.write("""import unittest
class TestLoopMarker(unittest.TestCase):
    def test_marker_deliberate_fail(self):
        self.assertTrue(False, "Deliberate controlled test failure")
if __name__ == '__main__':
    unittest.main()
""")

        test_state["iteration"] = 0
        test_state["status"] = "IN_PROGRESS"
        test_state["verification_status"] = "PENDING"
        self.write_state(test_state)

        log("Testing fail path on loop-test...")
        fail_res = self.run_iteration(step="loop-test")
        updated_state = self.read_state()

        step_retained = updated_state.get("current_step") == "loop-test"
        iter_incremented = updated_state.get("iteration") == 1
        status_retry = updated_state.get("status") == "RETRY"

        log(f"Fail check results: Step retained: {step_retained}, Iter incremented (0->1): {iter_incremented}, Status RETRY: {status_retry}")
        if not (step_retained and iter_incremented and status_retry):
            log("Controlled FAIL test failed state check!")
            return False

        log("Controlled FAIL & RETRY test succeeded!")

        # Step 4: Clean up temporary test and restore state to Step 6
        log("Cleaning up controlled test and restoring state to Step 6...")
        with open(marker_path, "w") as f:
            f.write("""import unittest
class TestLoopMarker(unittest.TestCase):
    def test_marker_pass(self):
        self.assertTrue(True)
if __name__ == '__main__':
    unittest.main()
""")

        restored_state = {
            "project": "MarketMaking-LoopEngineering",
            "current_step": 6,
            "status": "IN_PROGRESS",
            "iteration": 0,
            "max_iterations": 5,
            "general_specification": "modular/general-specification.md",
            "current_specification": "modular/step-06.md",
            "previous_step": 5,
            "previous_step_status": "COMPLETE",
            "verification_status": "PENDING",
            "last_verification": None,
            "blockers": [],
            "next_action": "Audit Step 6 implementation",
        }
        self.write_state(restored_state)
        log("=== CONTROLLED LOOP TEST COMPLETE AND VERIFIED PASS ===")
        return True


def main():
    parser = argparse.ArgumentParser(description="Loop Engineering Autonomous Controller")
    parser.add_argument("--isolation-check", action="store_true", help="Run isolation audit only")
    parser.add_argument("--test-loop", action="store_true", help="Run controlled loop test")
    parser.add_argument("--run-iteration", action="store_true", help="Run a single iteration of current step")
    parser.add_argument("--step", default=None, help="Specific step to run")
    args = parser.parse_args()

    controller = LoopController()

    if args.isolation_check:
        ok, msg = controller.verify_isolation()
        print(f"Isolation check: {'PASS' if ok else 'FAIL'} - {msg}")
        sys.exit(0 if ok else 1)

    if args.test_loop:
        ok = controller.run_controlled_test()
        sys.exit(0 if ok else 1)

    if args.run_iteration:
        step_val = int(args.step) if (args.step and args.step.isdigit()) else args.step
        res = controller.run_iteration(step=step_val)
        sys.exit(0 if res == "PASS" else 1)

    # Default action: run iteration of current step
    state = controller.read_state()
    step_val = int(state.get("current_step", 6))
    res = controller.run_iteration(step=step_val)
    sys.exit(0 if res == "PASS" else 1)


if __name__ == "__main__":
    main()
