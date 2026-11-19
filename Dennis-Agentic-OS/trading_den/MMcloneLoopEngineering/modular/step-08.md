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
