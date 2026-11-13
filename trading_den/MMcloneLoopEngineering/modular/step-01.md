STEP 1 — Architecture Audit

No code changes.

Read the general modular .md file and inspect the entire current project.

Audit:

Arbitrage tab
Screener
Cross-Exchange
Cross-Asset
Triangular
Strategy Settings
Bot Manager
REST handlers
WebSocket handlers
native exchange connectors
CCXT
CCXT Pro
Opportunity Engine
direction engine
fee engine
risk management
Execution Engine
reconciliation
charts
order books
funding
historical data
strategy state
order/position state

Trace:

Exchange → Connector → Market Data → Normalization → Screener → Opportunity → Strategy → Execution → Orders → Positions → PNL

Also audit startup:

REST current snapshot
REST 1-minute historical candles
historical deviation
Grid/Bollinger initialization
WebSocket startup
snapshot synchronization
sequence handling
stale data
reconnect/resubscription
duplicate connections
readiness state

Identify:

EXECUTE ON
REFERENCE VENUE
PRICED VENUE
Spread Arbitrage
Funding Arbitrage
execution direction
Maker/Hedge
Opportunity Engine
Mandatory mock-data audit

Locate every mock/simulated market-data source in the current code and document:

file;
function/component;
type of mock data;
consumers;
intended real data source;
replacement required.

Do not modify code during Step 1.

Deliver an architecture map and implementation insertion points.
