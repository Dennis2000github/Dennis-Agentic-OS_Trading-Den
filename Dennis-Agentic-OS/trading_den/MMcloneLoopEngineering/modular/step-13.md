STEP 13 — Screener → Bot Manager → Execution

Complete the live pipeline:

REST Current Snapshot
        ↓
REST Historical 1-Minute Data
        ↓
Historical Deviation
        ↓
Grid/Bollinger Initialization
        ↓
WebSocket Live Feed
        ↓
Normalized Market State
        ↓
Cross-Exchange Matrix
        ↓
Reference/Priced Relationship
        ↓
Both Execution Directions
        ↓
Directional Fees
        ↓
Opportunity Engine
        ↓
Strategy Selection
        ↓
Selected Maker / First Leg
        ↓
Selected Hedge / Taker
        ↓
Bot Manager
        ↓
Execution Engine

Strategy selection:

Spread = Highest Net Spread %;
Funding = Highest Funding %.

Never derive execution roles from Reference/Priced.

Before every order submission

Revalidate latest:

bid/ask;
order-book liquidity;
funding;
fees;
slippage;
timestamp;
freshness;
opportunity validity;
execution direction;
position state.

Execution must use the latest valid real market state.

Preserve:

Dry Run/LIVE;
Bot controls;
risk management;
execution management;
reconciliation.

No mock market data anywhere in the execution pipeline.
