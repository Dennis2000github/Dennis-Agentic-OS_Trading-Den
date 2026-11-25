# COSMOS Shared Integrations — Implementation

> Shopee Affiliate (native connector) + YouTube (existing Composio connection),
> delivered as shared COSMOS-level capabilities. No intelligence module owns an
> integration; every authorized module consumes the same services.

**Status: implemented (Phases 1–6 + connection unblock + live COSMOS channel
identity), 2026-11-25.** All integration tests pass (**103 tests across 8
files**); the pre-existing
unrelated failures in `test_upif`, `test_plugins`, `test_skill_builder_ui` and
the `freellmapi` import errors were documented against HEAD before the
connection patch and were not touched by it.

---

## 1. Architecture

```
COSMOS module / agent / future module
        │
        ▼
backend/integrations/                ← THE shared layer
├── registry.py    provider+capability registry, health, validation
├── permissions.py approval gate (shared owner queue, hard gates)
├── executions.py  cross-module execution audit (data/integrations/executions.json)
├── store.py       0600 atomic JSON stores, versioned lazy defaults
├── composio_adapter.py  Composio provider: discovery (REST) + execution (session MCP)
├── youtube.py     YouTube domain service (68 ops, 32 read / 36 write)
├── youtube_api.py /api/integrations/youtube/*
├── links.py       cross-platform product ↔ video campaign links
└── shopee/        native Shopee Affiliate connector
    ├── auth.py    SHA-256 signing (documented formula), credentials, self-test
    ├── client.py  GraphQL transport, documented error mapping, transient-only retries
    ├── products.py productOfferV2 (page/limit), normalization, upsert store
    ├── feeds.py   listItemFeeds/getItemFeedData (offset ≤500), FULL/DELTA sync,
    │              checkpoints, idempotency, validated FULL reconciliation
    └── api.py     /api/integrations/shopee/*
```

Providers register once; status is derived live and never fabricated. GENESIS's
existing direct-OAuth publisher is untouched and will register under the same
router when it next runs (single approval surface, no re-authorization).

## 2. What was verified live (read-only)

* Composio project REST (backend's own key): YouTube connected account
  **ACTIVE** (`ca_ekDxcgs…`, user `dennis-os-local`, Composio-managed OAuth2,
  auth config `ac_6QLZpij…`).
* **Granted scopes**: `youtube`, `youtube.force-ssl`, `youtube.readonly`,
  `youtube.upload`, `youtubepartner`, `youtubepartner-channel-audit`,
  `youtube.channel-memberships.creator` — full coverage; no reconnection needed.
* **68/68 YouTube tools available** in the live catalog with schemas, scopes
  and versions (`GET /tools?tool_slugs=`). Only `YOUTUBE_MARK_COMMENT_AS_SPAM`
  is catalog-deprecated; its replacement (`YOUTUBE_SET_COMMENT_MODERATION_STATUS`)
  is implemented as `moderate_comment`.
* Session tool-router (MCP JSON-RPC) executes mechanically (structured
  results + `log_id`). The owner completed the one-time session link; the
  active connection is `ca_-ma0DxidfbfK` and execution is available.
* Read-only `list_channels` with `mine=true` returns the real channel
  **MANOSA, DENNIS M.** (`UCnnEJ6CejkxDS0hxZHGFcvQ`, `@manosadennism.8883`)
  and its `snippet.thumbnails.high.url`. This is now the identity shown in
  COSMOS headers; the seeded Studio profile is not used as proof of connection.

## 3. Platform limitation — resolved by one-time session link

The current Composio tier does not bind project-level connected accounts into
a tool-router session automatically; it wants a session-scoped connect link.
The owner opened that link once. Discovery, schemas, execution status and
read-only YouTube calls now work through the existing connection. No second
OAuth client, duplicate integration or direct REST execution path was added.
The link flow remains two-step and explicit: disclose the URL, then the owner
chooses to open it and refresh. The registry derives readiness from the live
transport instead of a hardcoded phase flag.

## 4. Guarded autonomy

* Read ops (32) run autonomously and are audited.
* Write ops (36) never execute without owner approval: they create a pending
  approval in the **one shared queue** (`data/studio/approvals.json`, decided
  via the existing `POST /api/studio/approvals/{id}`), with the approval id
  injected into the executor for audit linkage.
* Hard gates (never downgradable): uploads, publishing/scheduling, deletes,
  comments, moderation, channel settings, live control, video metadata,
  captions.
* Upload paths are validated before approval; `uploadLimitExceeded` maps to
  `channel_upload_limit` (429) and is never retried or bypassed.
* Instructions inside external data (listings, comments, API responses) are
  never treated as permission.

## 5. Shopee specifics

* Signing: `SHA256(AppId + Timestamp + Payload + Secret)` over the exact
  serialized body; a fresh timestamp/signature is generated on **every retry**;
  the response `Date` header is checked against the signed timestamp (±10 min).
* **Test vector**: the documented expected digest (`dc88d72f…45412` for
  AppId 123456 / ts 1577836800 / secret demo) could not be reproduced — the
  documentation does not state the vector's example payload. `verify_test_vector()`
  reports the comparison honestly and is the confirmation hook once the
  payload example arrives (outstanding item). The formula contract itself is
  unit-tested against locally-computed digests.
* Endpoint is configurable (`SHOPEE_API_ENDPOINT` env > credentials store >
  PH default) because the supplied documentation did not pin the URL.
* Credentials: `SHOPEE_APP_ID` / `SHOPEE_AFFILIATE_SECRET` env or
  `data/integrations/shopee/credentials.json` (0600). An owner-gated
  `POST /api/integrations/shopee/credentials` now saves them locally without
  making a Shopee call or returning the secret. Still not configured as of
  validation — deliver out-of-band, never in chat.
* Feed `columns` mappings are **provisional**: raw payloads are stored
  verbatim, common id fields detected defensively, malformed JSON flagged
  (never crashing sync). FULL reconciliation replaces the catalog only after
  full-page consumption and `totalCount` validation.
* Error codes 10020/10030/10031–10035/11000–11002 map to stable local errors;
  permanent errors are never retried; 10030 → 429.

## 6. API surface (owner-gated)

```
GET  /api/cosmos/integrations                            shared COSMOS projection
GET  /api/integrations                                   registry + health
GET  /api/integrations/providers/{id}                    provider detail
GET  /api/integrations/executions                        audit (filters)
GET  /api/integrations/health | /permissions             health · gate policy
POST /api/integrations/providers/{id}/validate|enable|disable

GET  /api/integrations/youtube                           capability + state
GET  /api/integrations/youtube/discover                  live op availability
GET  /api/integrations/youtube/connection                connection + session state
GET  /api/integrations/youtube/channel[?refresh=1]       live connected-channel identity
GET  /api/integrations/youtube/ops[/{op}]                op table · live schema
GET  /api/integrations/youtube/run/{read-op}             autonomous read
POST /api/integrations/youtube/run/{op}                  read: run · write: approval
POST /api/integrations/youtube/session/link              disclose session connect link

GET  /api/integrations/shopee                            state + self-test
GET  /api/integrations/shopee/auth/self-test             signing vector report
POST /api/integrations/shopee/credentials                save AppId/secret locally (0600)
GET  /api/integrations/shopee/feeds | /products          sync status · local catalog
POST /api/integrations/shopee/offers/search|query        productOfferV2 (live)
POST /api/integrations/shopee/feeds/list|sync|reconcile  feed synchronization

GET  /api/integrations/links[/{id}]                      campaign links
POST /api/integrations/links                             create (product provenance)
POST /api/integrations/links/{id}/video|status           attach video · lifecycle
GET  /api/integrations/links/{id}/performance            side-by-side snapshot
POST /api/integrations/links/{id}/performance            record snapshot
```

## 7. Connection unblock patch (2026-11-25)

The approved patch closes the gap between "API exists" and "owner can
connect", without duplicating integrations or exposing secrets:

1. **Static-file guard.** `backend/static_guard.py` denies static GETs to
   `/data/`, `/backend/`, `/tests/`, dotfiles/dot-directories, `.venv`,
   `node_modules` and vendored plugin sources. This runs before the
   `SimpleHTTPRequestHandler` fallback, so a credentials file written under
   `data/integrations/shopee/` is never downloadable.
2. **Shopee connection path.** The owner-gated credentials POST writes the
   0600 store, records a redacted audit row, and revalidates local registry
   state. It makes no external Shopee call. The Settings panel has a
   write-only form; the secret is never echoed and is cleared on completion.
3. **Shopee correctness.** Retries now re-sign with a fresh timestamp over the
   same exact serialized body; response `Date` skew fails closed; batch upsert
   no longer uses stale indexes when several offers arrive in one call.
4. **COSMOS wiring.** `GET /api/cosmos/integrations` projects the one router
   into every agent prompt as
   `context.shared_integrations`; only the five shared capability IDs are
   exposed. YouTube/Shopee execution accepts optional `agent`/`module`
   attribution, and those fields are stripped from tool arguments.
5. **YouTube connection path.** The Settings panel exposes provider/session
   state and a strict two-step flow: **Get connect link** discloses the URL;
   **Open connect link** is a separate explicit click (host validated,
   `noopener,noreferrer`). Nothing opens automatically and no channel write
   happens during validation.
6. **Shared approval surface.** The panel lists pending actions from the one
   Studio queue. Approve/Reject requires a confirmation because approval can
   execute an external action; history comes from the shared integration audit.
7. **Live COSMOS channel identity.** `composio_adapter.execute_tools()` now
   unwraps the live nested `response.successful` / `response.data` envelope
   (and still accepts the older flat shape). `youtube.connected_channel()`
   reads `list_channels` with `mine=true`, sanitizes the title/handle/avatar,
   caches for 120 seconds, and degrades honestly to `not_connected` or
   `unavailable`. `GET /api/cosmos/overview` projects that identity as
   `youtube_channel`; every COSMOS agent replaces the YouTube division title
   with the connected channel avatar and name. ANDROMEDA gets a compact
   Connect/Refresh chip using the same shared router; disclosing the link never
   opens it, and the old direct-OAuth button was removed from COSMOS.

Read-only validation after restarting the app (2026-11-25):

* `GET /data/integrations/shopee/credentials.json` → **404**, zero bytes.
* `GET /api/cosmos/integrations` → **200**, five shared capabilities, router
  `/api/integrations`.
* Shopee → `not_configured`, `credentials.source=none`; signing self-test
  still reports `match=false` honestly until the vector payload arrives.
* Composio → `active`; YouTube connection `ACTIVE`, session `active`,
  `execution_available=true`; 68/68 ops remained discoverable.
* Raw MCP results are normalized from the nested response envelope; the live
  channel resolver returned `connected`, the real channel name and avatar URL.
* `GET /api/cosmos/overview` included the `youtube_channel` projection.
* Integration audit contains only read-only YouTube probes (including earlier
  failed attempts before the nested-response fix); no pending approvals were
  created.
* No Shopee live call was made and no YouTube channel write was attempted.
  The assistant did not open any connect link; the owner did that explicitly.

## 8. New stores (all 0600, versioned, git-ignored)

`data/integrations/{registry,executions,links}.json`,
`data/integrations/shopee/{credentials,products,feeds}.json`.
No existing store was changed; no migration needed.

## 9. Tests

`tests/test_integrations_{registry,executions,permissions,composio_adapter,youtube,shopee,links,unblock}.py`
— **103 tests**, all passing. Network is fully mocked (SSE frames, GraphQL,
schemas mirror the live-verified shapes); signing, pagination (both schemes),
DELTA NEW/UPDATE/DELETE, checkpoint resume, idempotency, validated
reconciliation, error mapping, approval enforcement (approve-executes /
reject-never-executes / unapproved-fails-closed / upload-limit-not-retried),
nested MCP response normalization, connected-channel parsing/caching, COSMOS
overview projection and honest-unavailability reporting are all covered.
`node tests/cosmosState.test.js` also verifies shared identity projection and
the header fallback. Read-only live checks were run against the real Composio
project during development.

## 10. Outstanding items

1. Shopee **AppId + Secret** (out-of-band delivery or the local UI form) and
   endpoint confirmation → unlocks live Shopee. No secret has been supplied.
2. Product Feed **`columns` JSON example** → finalize field mappings
   (currently provisional + raw-preserved).
3. Shopee **Get Short Link** and **Get Conversion Report** documentation;
   official rate-limit policy.
4. The vector's example payload → confirm signing via `verify_test_vector`.
5. YouTube session binding is complete and the live channel identity is
   projected into COSMOS. Keep the two-step Connect/Refresh flow: disclose the
   link, let the owner open it explicitly, then refresh. No channel write was
   attempted during validation.
6. After Shopee credentials are supplied, run one read-only verification per
   provider (`auth/self-test` / `youtube/channel`) before any approved write.
