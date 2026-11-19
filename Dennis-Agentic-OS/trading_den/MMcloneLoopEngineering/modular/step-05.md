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
