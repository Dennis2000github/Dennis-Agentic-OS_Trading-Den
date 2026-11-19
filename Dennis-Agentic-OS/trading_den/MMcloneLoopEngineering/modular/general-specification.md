# Market Making — Modular General Specification

## 1. System Overview & Architectural Separation

The system maintains strict architectural separation across the trading pipeline:

REAL MARKET DATA
      ↓
REFERENCE / PRICED RELATIONSHIP
      ↓
RAW SPREAD / FUNDING
      ↓
DIRECTION ENGINE (Dynamic Maker / Hedge)
      ↓
DIRECTIONAL MAKER / TAKER FEES
      ↓
OPPORTUNITY ENGINE
      ↓
STRATEGY SELECTION
      ↓
MAKER ENTRY PRICING (Spread / Grid / Bollinger)
      ↓
MAKER / HEDGE EXECUTION
      ↓
STRATEGY-SPECIFIC LIFECYCLE
      ↓
EXIT (0% Convergence / Funding Settlement / Stop Loss)
      ↓
RECONCILIATION

## 2. Core Architectural Principles

1. **Pricing vs Execution Roles**:
   - Reference / Priced Venue = Pricing Relationship.
   - Maker / Hedge Venue = Execution Roles.
   - Never assume Reference = Hedge or Priced = Maker.
   - Dynamic evaluation of Candidate A (A Maker -> B Hedge) and Candidate B (B Maker -> A Hedge).

2. **Market Data Integrity**:
   - Strictly no fabricated market data, no mock prices, no random jitter.
   - Stale or missing market data must produce an explicit UNAVAILABLE/STALE state, never simulated movement.
   - All spreads must reflect executable bid/ask relationships.

3. **Spread Calculation Formula**:
   - Cross-exchange executable spread:
     Venue A -> Venue B: (B.best_bid - A.best_ask) / A.best_ask * 100
     Venue B -> Venue A: (A.best_bid - B.best_ask) / B.best_ask * 100
   - Preserves actual executable bid/ask relationships; never substitute bid-to-bid, ask-to-ask, or midpoints unless explicitly specified.

4. **Strategy Integrity**:
   - Spread Arbitrage: Maker selection by highest applicable net spread %, maker pricing by spread/grid/Bollinger, hedge on opposite venue.
   - Funding Arbitrage: Funding % selects maker direction/opportunity, maker entry by spread/grid/Bollinger, funding settlement as independent exit.

5. **Safety Constraints**:
   - No live trading / no real orders during modular development.
   - Isolated environment with independent Git tracking and verification.
