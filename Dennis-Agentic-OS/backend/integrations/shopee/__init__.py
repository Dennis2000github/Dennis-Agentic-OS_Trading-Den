"""Native Shopee Philippines Affiliate Open API connector.

Shared COSMOS capability — no intelligence module owns this integration.
Submodules:

* ``auth``     — credentials (env > 0600 store), SHA-256 request signing,
                 documented test-vector self-check;
* ``client``   — GraphQL transport over stdlib urllib with documented
                 error-code mapping and transient-only retries;
* ``products`` — ``productOfferV2`` queries (page/limit pagination) and
                 offer normalization (commission strings -> floats,
                 deprecated fields avoided);
* ``feeds``    — ``listItemFeeds`` (FULL/DELTA) + ``getItemFeedData``
                 (offset pagination <= 500) with checkpointed, idempotent
                 synchronization and FULL reconciliation;
* ``api``      — pure HTTP dispatcher for the ``shopee/`` API subtree.

Outstanding (per project brief, §13): the exact example of the Product Feed
``columns`` JSON structure — field mappings therefore stay provisional and
raw payloads are preserved verbatim; the Get Short Link and Get Conversion
Report documentation; the official rate-limit policy. The GraphQL endpoint
is configurable because the supplied documentation did not pin the URL.
"""
