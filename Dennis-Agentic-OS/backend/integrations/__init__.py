"""Shared COSMOS integrations package.

One home for every external provider the OS can reach, so no intelligence
module owns an integration. Layout:

* ``registry``    — provider/capability registry + health (the router's data plane);
* ``permissions`` — shared approval gate over the owner approval queue;
* ``executions``  — cross-module execution audit trail;
* ``api``         — pure HTTP dispatcher (``/api/integrations/*``).

Provider adapters (Composio, native Shopee, YouTube domain services) plug in
as future submodules and register in the registry; they never bypass it.
Conventions match the rest of the backend: stdlib only, JSON stores under
``data/integrations/`` (0600, atomic, versioned), honest status reporting.
"""
