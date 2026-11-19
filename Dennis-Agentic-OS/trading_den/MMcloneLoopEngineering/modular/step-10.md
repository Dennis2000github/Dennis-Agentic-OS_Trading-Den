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
