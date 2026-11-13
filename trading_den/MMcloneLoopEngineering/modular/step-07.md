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
