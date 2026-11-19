# Controlled Loop Test Step Specification

## Objective
Demonstrate that the Loop Controller and Independent Verifier can reliably execute the full engineering cycle:
1. Read state
2. Execute audit & implementation validation
3. Run deterministic tests
4. Invoke independent verifier
5. Record evidence and reports
6. Advance state on PASS
7. Retain step and increment iteration on FAIL

## Requirements
1. `tests/test_loop_marker.py` must exist and pass its assertions.
2. Invariants must be satisfied without mutating application source files.
