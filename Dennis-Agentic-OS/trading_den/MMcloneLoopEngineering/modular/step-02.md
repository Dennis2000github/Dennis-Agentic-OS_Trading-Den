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
