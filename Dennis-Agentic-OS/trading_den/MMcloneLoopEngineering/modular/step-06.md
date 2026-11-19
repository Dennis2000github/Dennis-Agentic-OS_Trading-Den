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
