STEP 14 — Final Integration, Failure Handling, Reconciliation and Regression

Perform complete end-to-end verification.

Startup

Verify:

REST current snapshot;
REST 1-minute candles;
historical deviation;
Grid/Bollinger initialization;
WebSocket startup;
no waiting for first WebSocket tick after REST initialization;
snapshot synchronization;
sequence validation;
sequence-gap resync;
reconnect;
resubscription;
stale-data rejection;
invalid-data rejection.
Market Data

Verify:

multiple venues;
no duplicate connections;
normalized updates;
native WebSocket feeds;
CCXT/CCXT Pro fallback where applicable;
raw high-frequency execution updates;
approximately one-second analytical stream.

Verify every displayed market value originates from a real source.

Search the final codebase again for remaining:

random market prices;
fake spreads;
mock order books;
fake funding;
simulated volume;
generated timestamps;
artificial WebSocket ticks;
market-data jitter.

Remove or replace remaining mock market-data paths where they belong to the production data pipeline.

Do not replace legitimate non-market UI placeholders unless required.

Screener

Verify:

continuous Matrix updates;
Reference/Priced terminology;
favorites;
Raw Spread;
funding;
volume;
freshness;
connection state.
Direction

Test:

A Maker → B Hedge

and:

B Maker → A Hedge

Verify:

Reference ≠ automatically Hedge

and:

Priced ≠ automatically Maker

Spread Arbitrage

Verify:

both directions;
Highest Net Spread selection;
directional fees;
Raw Spread independence;
Net Spread;
Grid;
Bollinger;
risk;
entry;
existing exits.
Funding Arbitrage

Verify:

both directions;
Highest Funding selection;
dynamic Maker/Hedge;
correct funding direction;
Spread/Grid/Bollinger used only for entry;
Funding % is not order price;
Funding % is not an automatic exit;
independent funding lifecycle;
settlement tracking;
funding-specific exit;
reconciliation.
Execution Failure Testing

Test:

stale opportunity;
WebSocket disconnect;
WebSocket reconnect;
missing sequence;
partial Maker fill;
failed Maker;
partial Hedge;
failed Hedge;
price movement before submission;
insufficient liquidity;
fee change;
funding change;
mismatched legs;
reconciliation failure.

The system must fail safely rather than substitute mock data.

Regression

Verify unrelated functionality remains unchanged.

Fix only issues caused by the implementation.

Do not perform unrelated refactoring.

Do not redesign the UI merely to accommodate real market data.

FINAL ARCHITECTURAL PRINCIPLE

The completed system must maintain strict separation between:

REAL MARKET DATA
      ↓
REFERENCE / PRICED RELATIONSHIP
      ↓
RAW SPREAD / FUNDING
      ↓
DIRECTION ENGINE
      ↓
OPPORTUNITY ENGINE
      ↓
STRATEGY SELECTION
      ↓
MAKER ENTRY PRICING
      ↓
MAKER / HEDGE EXECUTION
      ↓
STRATEGY-SPECIFIC LIFECYCLE
      ↓
EXIT
      ↓
RECONCILIATION

The key separation is:

Reference/Priced = pricing relationship

Highest Net Spread / Highest Funding = strategy selection

Maker/Hedge = execution roles

Spread/Grid/Bollinger = Maker entry pricing controls

Funding settlement = Funding Arbitrage lifecycle

Exit = strategy-specific lifecycle logic

And throughout development:

Real market data is mandatory. Missing market data must produce an explicit unavailable/stale/disconnected state, never simulated movement, jitter, random values, or fabricated market conditions.
