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
