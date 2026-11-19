# LOOP — Controlled Engineering Loop Architecture

The autonomous Loop Engineering implementation follows the deterministic state machine:

```text
SPECIFICATION
      ↓
STATE
      ↓
AUDIT
      ↓
IMPLEMENTATION
      ↓
TESTING
      ↓
INDEPENDENT VERIFICATION
      ↓
┌───────────────┬───────────────┬───────────────┐
│ PASS          │ FAIL          │ BLOCKED       │
│               │               │               │
↓               ↓               ↓
NEXT STEP       RETRY SAME      STOP
(Git Checkpoint) STEP
```

## Mandatory Phase Order
1. **Phase A — Read**: Read AGENTS.md, LOOP.md, STATE.md, CONSTRAINTS.md, BUDGET.md, general-specification.md, and current step specification.
2. **Phase B — Audit**: Audit existing code against specifications. Classify each requirement as: ALREADY COMPLETE, PARTIALLY COMPLETE, MISSING, INCORRECT, UNVERIFIED.
3. **Phase C — Requirement Mapping**: Map requirements, gaps, required changes, and verification criteria.
4. **Phase D — Bounded Implementation**: Antigravity executes bounded changes inside `MarketMaking-LoopEngineering`.
5. **Phase E — Testing**: Run unit, deterministic, and regression test suites.
6. **Phase F — Independent Verification**: `verifier.py` executes without mutating code and produces PASS / FAIL / BLOCKED.
7. **Phase G — Decision**:
   - On **PASS**: Create Git checkpoint commit, advance `current_step += 1`, set status to IN_PROGRESS.
   - On **FAIL**: Increment iteration, retain current step, record failure analysis, retry.
   - On **BLOCKED**: Halt execution and request human review.
