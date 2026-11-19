# CONSTRAINTS — Safety & Trading Integrity Rules

```yaml[step_loop-test_evidence_20261006_170358.json](verification/evidence/step_loop-test_evidence_20261006_170358.json)
MAX_ITERATIONS: 5
MAX_AGENT_RUNS_PER_ITERATION: 2
MAX_TOTAL_AGENT_RUNS: 20

ORIGINAL_PROJECT_READ_ONLY: true
ISOLATED_PROJECT_REQUIRED: true
WORKTREE_MODE: false
INDEPENDENT_GIT_REPOSITORY: true

NO_LIVE_TRADING: true
NO_REAL_ORDERS: true
NO_API_CREDENTIAL_CHANGES: true
NO_SECRET_EXPOSURE: true
NO_DESTRUCTIVE_DATABASE_MIGRATIONS: true

NO_MOCK_MARKET_DATA: true
NO_RANDOM_PRICE_JITTER: true

NO_STEP_SKIPPING: true
NO_SELF_APPROVAL: true
NO_UNRELATED_REFACTORING: true

REQUIRE_GIT_CHECKPOINT: true
```

## Market Data & Strategy Rules
1. **No Fake Prices**: Strictly no mock prices, fabricated order books, random jitter, or synthetic spreads.
2. **Missing Data Handling**: When real data is unavailable, it must be reported as UNAVAILABLE or STALE.
3. **Executable Spread Formula**:
   - A -> B: `(B.best_bid - A.best_ask) / A.best_ask * 100`
   - B -> A: `(A.best_bid - B.best_ask) / B.best_ask * 100`
4. **Strategy Roles**:
   - Spread Arbitrage: Maker venue selected by highest applicable net spread %, maker pricing by spread/grid/Bollinger, hedge on opposite venue.
   - Funding Arbitrage: Funding % selects maker direction/opportunity, maker entry by spread/grid/Bollinger, funding settlement as independent exit.
