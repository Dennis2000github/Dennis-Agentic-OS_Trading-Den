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

STEP 2 — Normalized Market Data + Hybrid Startup

Backend only.

Normalize:

Venue
Symbol
Best Bid
Best Ask
Bid Size
Ask Size
Funding Rate
Funding Direction
24H Volume
Timestamp
Sequence
Freshness
Connection Status

Use the existing native WebSocket architecture.

CCXT Pro may act as a unified/fallback gateway where appropriate.

Do not create duplicate exchange connections.

Hybrid startup

Startup sequence:

Bot Start
→ Load Configuration
→ Initialize Connectors
→ REST Current Snapshot
→ REST Historical 1-Minute Candles
→ Initialize Normalized State
→ Build Historical Deviation
→ Initialize Grid/Bollinger Statistical References
→ Start WebSockets
→ Synchronize REST Snapshot + WebSocket Updates
→ Transition to WebSocket Live Source
→ Normalized Live Market State

REST historical 1-minute closes are for:

historical deviation;
statistical initialization;
Grid/Bollinger initialization.

They are not executable market data.

WebSockets provide:

current bid/ask;
order book;
liquidity;
funding;
real-time prices;
real-time deviation.

If the exchange provides faster-than-one-second updates:

preserve raw high-frequency updates for execution;
produce approximately one-second normalized analytical updates for strategy calculations.

Implement:

reconnect;
resubscription;
snapshot synchronization;
sequence validation;
missing-sequence detection;
stale-data detection;
invalid-data rejection;
exchange capability handling;
startup readiness.

A symbol is not execution-ready until:

REST snapshot exists;
WebSocket is synchronized;
bid/ask are valid;
timestamp/freshness passes.

No strategy, order, execution, or exit changes.

Remove/replace any mock market-data source discovered in the current startup/data path.

STEP 3 — Reference Venue / Priced Venue

Replace old terminology where required.

Reference Venue

Source of reference market data/price.

Priced Venue

Venue whose market is being evaluated against the Reference Venue.

Neither role means:

Maker;
First Leg;
Taker;
Hedge.

Either venue may dynamically become:

First Leg / Maker;
Second Leg / Hedge/Taker.

Update:

variable names;
API payloads;
models;
UI labels;
data mappings.

Do not change execution-direction behavior.

Preserve Spread Arbitrage.

Replace any mock Reference/Priced values with real normalized market data.

STEP 4 — Cross-Exchange Matrix

Modify only the existing Screener/sidebar components required for real data and matrix functionality.

Y-axis

Live symbols from Reference Venue.

Favorites remain pinned to the top.

X-axis

Cross venues.

Each cell represents:

Reference Venue ↔ Priced Venue

Display real:

Best Bid
Best Ask
Bid Size
Ask Size
Funding Rate
Funding Direction
24H Volume
Timestamp
Connection/Freshness Status

Data must continuously arrive from the normalized backend market-data stream.

No manual refresh requirement.

No mock matrix values.

No artificial price/spread movement.

Do not implement final strategy/direction selection yet.

STEP 5 — Raw Spread Calculation

Implement backend Raw Spread % for every valid Reference/Priced relationship.

Raw Spread must use real live executable:

bid;
ask.

Raw Spread is independent of:

fees;
funding;
slippage;
other costs.

Evaluate both directions.

Return:

Reference Venue;
Priced Venue;
symbol;
prices;
direction;
Raw Spread %;
timestamp;
freshness;
validity.

Expose Raw Spread to the Matrix.

Stale/invalid market data must produce no valid spread.

Never fabricate Raw Spread when data is missing.

No strategy/execution/exit changes.

STEP 6 — Dynamic Maker/Hedge Direction

Implement or reuse the dedicated execution-direction layer.

Evaluate:

Candidate A

A Maker / First Leg → B Taker/Hedge / Second Leg

Candidate B

B Maker / First Leg → A Taker/Hedge / Second Leg

Never assume:

Reference = Hedge

or

Priced = Maker

Return both candidates with:

Maker venue;
Maker price;
Hedge venue;
Hedge price;
direction;
timestamps;
validity.

No order placement.

Use only valid real market data.

STEP 7 — Directional Maker/Taker Fees

Reuse the existing directional fee engine.

For:

A Maker → B Taker

calculate:

A Maker Fee + B Taker Fee

For:

B Maker → A Taker

calculate:

B Maker Fee + A Taker Fee

Use:

exchange;
account;
instrument;
market type;
fee tier.

Raw Spread remains fee-independent.

Do not create duplicate fee logic.

No order placement or exit changes.

STEP 8 — Opportunity Engine

Integrate:

directional spread;
maker fee;
taker fee;
slippage;
liquidity;
other existing cost models.

Keep:

Raw Spread

separate from:

Net Spread

The existing Opportunity Engine remains authoritative for Net Spread %.

The frontend must not independently calculate profitability.

Return both directional opportunities and their calculation components.

Reuse the existing Opportunity Engine.

Do not redefine it.

Only real market data may enter the opportunity calculation.

Invalid/stale market data must invalidate the opportunity rather than generating a placeholder.

STEP 9 — Spread Arbitrage

Use directional opportunities.

Evaluate both directions.

Calculate Net Spread.

Select:

Highest Net Spread %

subject to existing thresholds and risk controls.

Selected direction becomes:

First Leg = Maker;
Second Leg = Taker/Hedge.

Do not derive execution direction from Reference/Priced roles.

Preserve:

Grid;
Bollinger;
Quantity;
risk;
entry thresholds;
existing exit behavior.
Important

The highest Net Spread identifies/selects the Maker direction/order opportunity.

The actual Maker execution level remains determined by the existing:

Spread %;
Grid;
Grid Spacing;
Bollinger;
other configured entry controls.

Do not alter Spread Arbitrage lifecycle unnecessarily.

STEP 10 — Funding Arbitrage Mode

Implement Funding Arbitrage as a separate strategy mode within the existing architecture.

Do not redesign the UI.

Do not make unrelated Spread Arbitrage changes.

Reuse:

market data;
direction engine;
fee engine;
Strategy Settings;
Opportunity Engine;
Execution Engine.
10.1 Funding Selection

When Funding Arbitrage is selected:

evaluate both directions;
calculate the directional funding opportunity;
determine the corresponding execution direction;
select the First Leg/Maker using Highest Funding %.

Funding % is a venue/direction selector only.

It is never the Maker order price.

Priced Venue does not automatically become Maker.

Reference Venue does not automatically become Hedge.

10.2 Direction

Evaluate:

A Maker / First Leg → B Taker/Hedge

and

B Maker / First Leg → A Taker/Hedge

Reuse Step 6.

10.3 Execution Roles

Reference/Priced describe pricing relationships only.

Execution roles are:

First Leg = Maker;
Second Leg = Hedge/Taker.

They are selected dynamically.

10.4 Maker Entry Price

After Funding selection identifies the Maker/direction, determine the actual Maker order level using existing:

Spread %;
Grid;
Grid Spacing;
Bollinger;
other Maker-entry controls.

Therefore:

Funding % selects the venue/direction.

Spread/Grid/Bollinger determines the Maker entry level.

Funding % must never directly determine the order price.

10.5 Spread %

Spread % is an entry/order-placement parameter.

It is not automatically a Funding Arbitrage exit condition.

10.6 Grid

Grid controls:

Maker entry ladder;
spacing between levels;
maximum Maker entry orders.

Grid is not:

a funding selector;
an automatic Funding Arbitrage exit.
10.7 Bollinger

Bollinger is used as an existing Maker-entry-level control after Funding selection.

It is not:

a venue selector;
an automatic Funding Arbitrage exit.
10.8 Exact Flow
Live Market Data
      ↓
Normalize
      ↓
Calculate Funding
      ↓
Evaluate Both Directions
      ↓
Calculate Directional Funding %
      ↓
Select Highest Funding %
      ↓
Select First Leg / Maker
      ↓
Identify Hedge / Taker
      ↓
Calculate Maker Entry via Spread/Grid/Bollinger
      ↓
Submit Maker
      ↓
Monitor Maker
      ↓
Execute / Manage Hedge
      ↓
Establish Hedged Position
      ↓
Monitor Funding / Settlement
      ↓
Funding Arbitrage Exit
      ↓
Reconcile Both Legs
      ↓
Close / Finalize
10.9 Independent Funding Exit

Do not automatically use:

Spread %;
Grid;
Bollinger;
Raw Spread

as Funding Arbitrage exits unless explicitly configured as funding-specific risk/exit controls.

Potential funding-specific conditions include:

funding settlement reached;
funding target reached;
funding opportunity invalidated;
funding risk threshold;
maximum duration;
execution failure;
partial-fill failure;
reconciliation failure;
other explicitly configured funding conditions.

Do not copy the Spread Arbitrage exit engine.

Do not implement the complete Step 11 settlement architecture yet.

10.10 Funding Direction

The displayed funding opportunity must correspond to the actual:

Maker/First Leg → Hedge/Taker

flow.

Do not simply display a raw funding value from one venue without establishing its directional meaning.

10.11 Fees / Net Opportunity

Reuse directional fees.

For:

A Maker + B Taker

use:

A Maker Fee + B Taker Fee

For the reverse:

B Maker + A Taker

use:

B Maker Fee + A Taker Fee

Pass applicable costs through the existing Opportunity Engine.

Do not redefine the Opportunity Engine.

10.12 Strategy Settings

Reuse the existing Strategy Settings panel.

Funding mode supports existing controls for:

Quantity;
Spread %;
Grid/Bollinger;
Grid Spacing;
Max Grids;
Add;
Add + Run;
Dry Run/LIVE.

Meanings:

Funding % = venue/direction selector;
Spread % = Maker entry parameter;
Grid = Maker entry ladder;
Grid Spacing = distance between Maker levels;
Max Grids = maximum Maker entry orders;
Bollinger = Maker entry control;
funding settlement/lifecycle = Funding-specific.

Do not create duplicate controls.

10.13 Preserve Spread Arbitrage

Do not modify:

Spread selection;
Spread exit;
Spread risk;
Spread Maker-entry behavior;
Spread UI.

Any shared component must isolate strategy-specific behavior.

10.14 Responsibility Separation
A — Selection

Highest Funding % → Maker venue/direction

B — Pricing

Spread/Grid/Bollinger → Maker entry level

C — Execution

Maker → Hedge

D — Lifecycle

Hedged position → Funding settlement

E — Exit

Funding-specific conditions → Close/Reconcile

Never combine these responsibilities.

10.15 Constraints

Before implementation:

inspect Steps 1–9;
reuse existing market-data layer;
reuse direction engine;
reuse fee engine;
reuse Strategy Settings;
reuse Opportunity Engine;
reuse Execution architecture.

Verify:

both directions;
Highest Funding selection;
Reference/Priced do not determine execution;
selection occurs before pricing;
Spread/Grid/Bollinger determine entry level;
Funding % does not determine order price;
Funding % does not automatically become an exit;
directional fees are correct;
funding direction corresponds to the selected flow;
Spread Arbitrage remains unchanged.

All funding values must originate from real exchange/broker data.

No simulated funding values.

STEP 11 — Funding Settlement and Independent Exit

Implement the complete Funding Arbitrage lifecycle independently from Spread Arbitrage.

Track:

funding interval;
funding timestamp;
expected direction;
funding eligibility;
settlement status;
actual funding result where available;
position state;
entry timestamp;
exit state;
reconciliation state.

Keep the Funding position managed until:

funding settlement;
configured funding target;
funding invalidation;
funding-specific risk;
duration limit;
execution failure;
partial-fill failure;
reconciliation failure.

Isolate Funding and Spread state machines.

Do not alter Spread exit behavior.

Prevent:

unknown legs;
mismatched legs;
unhedged positions;
incorrect settlement attribution.

Real funding/settlement information must be used whenever the venue provides it.

If unavailable, show the actual unavailable state rather than fabricating settlement data.

STEP 12 — Arbitrage Tab + Charts

Integrate the completed components into the existing Arbitrage UI.

Use:

Reference Venue;
Priced Venue.

Show actual execution:

Maker / First Leg → Taker / Hedge

Do not imply:

Reference/Priced → execution role

Integrate real:

market data;
Raw Spread;
Net Spread;
funding;
funding direction;
selected direction;
Maker/Hedge;
strategy mode.

Charts must use real normalized market data.

Chart relationship must correctly represent Reference/Priced pricing.

Strategy Settings must dynamically support:

Spread Arbitrage;
Funding Arbitrage.

Preserve existing:

chart layout;
order books;
Strategy Settings;
Execution Console;
styling.

No unrelated UI redesign.

If live data is unavailable, show the actual connection/stale state instead of simulated chart movement.

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