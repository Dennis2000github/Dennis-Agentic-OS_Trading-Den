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
