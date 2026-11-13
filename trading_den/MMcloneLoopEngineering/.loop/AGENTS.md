# AGENTS — Roles and Responsibilities

## 1. Antigravity (Implementer)
- **Rol[loop_controller.py](../loop_controller.py)e**: Bounded Implementer.
- **Authority**: Antigravity is NOT the final authority on whether a step is complete.
- **Constraints**:
  - Operates strictly within `MarketMaking-LoopEngineering`.
  - Never touches or modifies `MarketMaking` (which is strictly READ-ONLY).
  - Implements ONLY the identified requirements for the current active modular step.
  - No unrelated refactoring or redesign.
  - No self-approval: cannot declare a step complete or alter verification status.
  - No mock/fabricated market data; missing data must be handled as UNAVAILABLE.

## 2. Verifier (verifier.py)
- **Role**: Independent Verification Layer.
- **Authority**: Decides PASS, FAIL, or BLOCKED based on rigorous, objective checks.
- **Constraints**:
  - Must NOT modify application source code.
  - Evaluates code correctness, factual/technical correctness, invariants, formulas, deterministic tests, and negative tests.
  - Generates verifiable evidence under `.loop/verification/evidence/` and structured reports under `.loop/verification/reports/`.
  - Verifies executable spread formulas and market data lineage.

## 3. Loop Controller (loop_controller.py)
- **Role**: Autonomous Orchestrator and State Machine Guard.
- **Authority**: Coordinates loop phases, verifies project isolation, enforces budget, executes implementer prompts, runs verification, records logs, and handles state advancement or retry.
