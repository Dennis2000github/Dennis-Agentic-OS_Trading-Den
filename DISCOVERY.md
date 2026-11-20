# DISCOVERY.md — Phase 2 (read-only discovery, nothing built)

Date: 2026-11-20. Target app confirmed by user: **`Dennis-Agentic-OS/`**.
User decision: the "follower / subscriber / GoHighLevel pipeline / Your skills"
cards requirement is **discarded** — not to be added or kept.

All findings below came from reading files, read-only SQLite (`mode=ro`), `ps`, `lsof`,
and GET-only requests. No write endpoint was called, no service was started/stopped,
no order was placed, and no secret value was printed. Where data was absent, the
result is recorded as **unknown** with the raw evidence.

## 0. Shared machine / infrastructure findings

| Finding | Evidence | Confidence |
| --- | --- | --- |
| App server is PID 48225, `Dennis-Agentic-OS/app.py`, listening on `*:8080` (all interfaces, **not** localhost-only) plus `127.0.0.1:55579` | `ps aux`, `lsof -nP -iTCP -sTCP:LISTEN` | HIGH |
| Local clock reports `UTC+8` (Manila-equivalent), labelled `PST` by `date` | `date` vs `date -u` on 2026-11-20 | HIGH |
| No crontab | `crontab -l` → `no crontab for macbookpro` | HIGH |
| One relevant launchd job: `ai.openclaw.gateway`, Node gateway on `127.0.0.1:18789`, `RunAtLoad`, `KeepAlive` | `~/Library/LaunchAgents/ai.openclaw.gateway.plist`, `launchctl list` | HIGH |
| No trading/cosmos/genesis launchd or cron jobs found | filtered `ls /Library/LaunchAgents /Library/LaunchDaemons`, `launchctl list` | HIGH |
| Other loopback listeners: PyCharm ports, Qoder 57010/57510/58510, `opencode serve` 49374, embeddings 49489, OpenClaw 18789 | `lsof` | HIGH |
| DB files present: `denma_history.db` (Trading Den), OpenJarvis `agents/digest/scheduler/telemetry/traces/audit.db`, OpenClaw `*.sqlite` | `find` | HIGH |
| Largest logs: `trading_den/.../server.log` 457 MB (stale, mtime Oct 23), `data/tts/dennis-server.log` 127 MB, `data/openjarvis/service.log` 6.4 MB | `du -h` | HIGH |
| `~/.jarvis` does not exist | `ls -la ~/.jarvis` | HIGH |

---

# SYSTEM 1 — TRADING DEN

## 1.1 Where it lives

| Component | Path | Confidence |
| --- | --- | --- |
| Single public HTTP/WS server | `Dennis-Agentic-OS/app.py` (PID 48225, `*:8080`) | HIGH |
| DenMa supervisor / engine launcher | `Dennis-Agentic-OS/backend/denma.py:8-12,40-42,118-169,203-268` — engine runs **in-process on an ephemeral loopback port**, no second stable port | HIGH |
| DenMa REST/WS routes | `Dennis-Agentic-OS/trading_den/MMcloneLoopEngineering/arbitrage/server.py:764-1343` | HIGH |
| Persistence layer + schema | `.../arbitrage/persistence.py:20-192` | HIGH |
| Primary trading DB | `.../MMcloneLoopEngineering/data/denma_history.db` (18,382,848 B, mtime 2026-11-20 21:06 = process start) | HIGH |
| Adjacent DBs (also queried) | `~/PycharmProjects/MarketMaking/data/denma_history.db`, `~/PycharmProjects/MarketMaking-LoopEngineering/data/denma_history.db` | HIGH |
| UI / dashboard | `.../MMcloneLoopEngineering/templates/index.html` (Matrix panel 2295-2321; hard-coded PNL 1847-1865; localStorage bots 10724-10759) | HIGH |
| Logs | `.../server.log` 479,532,445 B (mtime Oct 23, stale); `data/tts/dennis-server.log` 133,519,884 B | HIGH |
| Schedulers | None — no crontab, no launchd match, no `package.json` under `trading_den` | HIGH |

## 1.2 How it stores status

- **PNL:** table `realized_pnl(id, bot_id, strategy_type, symbol, amount_usd, timestamp, date)` — `persistence.py:106-113`. **No fee column, no net/gross split, no margin or leader field.** (HIGH)
- **Fees:** `fills.fee` exists but `fills` has **0 rows**; `orders=0`, `execution_history=0`. (HIGH)
- **Open PNL:** `positions.floating_pnl` / `positions.realized_pnl` — 3 rows, all seeded demo (`persistence.py:235-240`). (HIGH)
- **Demo seeding:** `persistence.py:201-256` inserts 120 realized rows + 3 positions + seeded `bot_events` (STARTED/FILL at 246-249), dated at seed time. Workspace DB max PNL date = `2026-10-17`; adjacent DBs = `2026-09-23`. (HIGH)
- **Bots:** in-memory only — `bot_manager.py:431` `self.bots: Dict[str, BotConfig] = {}`; start/stop 483-519, list 533-534. Not persisted; engine restarted 21:06 today. (HIGH)
- **UI-only state:** `index.html:10724-10759` seeds `bot_tri_demo`, `bot_cross_demo` (`Running`, `DRY_RUN`) into browser localStorage — **not** backend state. `index.html:1847-1855` hard-codes `PNL Today +318.42 USDT` / `Realized +271.15 USDT` / NAV `38,432.77 USDT`. (HIGH)

## 1.3 How to read it (exact read-only commands)

```bash
curl -sS --max-time 10 http://127.0.0.1:8080/api/bots/list
curl -sS --max-time 10 http://127.0.0.1:8080/api/live-snapshot
curl -sS --max-time 10 http://127.0.0.1:8080/api/screener
curl -sS --max-time 10 'http://127.0.0.1:8080/api/history/pnl?limit=3'
curl -sS --max-time 10 -H \
  'Referer: http://127.0.0.1:8080/trading_den/MMcloneLoopEngineering/templates/index.html' \
  http://127.0.0.1:8080/api/status
```

Owner-gated endpoints (returned `{"error":"Unauthorized"}`): `GET /api/trading-den`,
unqualified `/api/status`. Proxy allowlist: `app.py:125-128,1033-1085,1320-1332,2837-2839`. (HIGH)

Read-only PNL query, run against each of the three DBs:

```sql
select count(*), sum(amount_usd)
from realized_pnl
where date(datetime(timestamp,'unixepoch','+8 hours')) = date('now','+8 hours');
```

Result in all three: `(0, None)`. Row counts: `realized_pnl=120`, `fills=0`, `orders=0`,
`positions=3`, `bot_events=4 (workspace)/5/32`, `execution_history=0`. (HIGH)

**Do not call** `GET /api/history/spread-candles`: although a GET, server code can fetch
REST and **write candles**. It was deliberately not called. (HIGH)

## 1.4 How to control it — NONE (read-only by design)

Trading Den has **no control surface in this upgrade**. All write/control endpoints
were identified only so they can be excluded:

- `POST /api/bots`, `/api/bots/start`, `/api/bots/run`, `/api/bots/stop` — `server.py:1238-1288`
- `POST /api/arbitrage/strategy-mode` — `server.py`
- `POST /api/history/spread-candles` (and the GET that can write) — `server.py`
- `GET /api/history/export-csv` — `server.py`
- `POST /api/trading-den/restart`, `POST /api/trading-den/stop` — `app.py:2911-2916`
- `triangular_execution.py` has `create_order` calls but they are **not wired** into `server.py` (no live order path found — MEDIUM confidence).

Exchange keys: **no server-side keys or env-var names found**. `rest_connector.py:60-72`
passes only public CCXT config; `mt5_client.py:38-44,73-76` accepts optional credentials
but none are configured. The UI "API Management" (`index.html:9928,10024-10047`) is a
prototype storing `{apiKey,apiSecret}` / `{loginNumber,path}` in browser localStorage
with `Read/Trade/Withdraw` labels and "prototype only" / "Withdrawal access excluded"
text. **No read-only/no-trade/no-withdraw server-side configuration exists.** (HIGH)

## 1.5 Answers to the four brief questions (evidence-based, no invention)

### (1) PNL TODAY — net / realized / unrealized, fees, midnight Manila → now
**unknown.** Raw evidence: `(0, None)` today in all 3 DBs; `fills=0`; no fee/net column
in `realized_pnl`; `positions` rows are seeded demo; UI numbers are literal HTML at
`index.html:1847-1855`. Confidence **HIGH** that no genuine today PNL exists in any
source inspected; the values themselves are **unknown**.

### (2) Running vs stopped bots, with names
Backend `{"status":"ok","bots":[],"count":0}` → **0 running, no backend names**.
UI demo names `bot_tri_demo`, `bot_cross_demo` are localStorage seeds, not authoritative.
Stopped-bot names: **unknown** (no persisted registry; in-memory state lost on 21:06 restart).
Confidence **HIGH** for empty backend list; **MEDIUM** for pre-restart history.

### (3) Outperforming arbitrage type — today's PNL rank, leader, margin
**Cannot rank — unknown.** Today's PNL rows = 0; no `leader` or `margin` field exists.
All-history **seeded demo** totals: `cross 557.7`, `tri 360.0`, `asset 253.5`, `fund 150.0`
(30 rows each, workspace DB); UI type mapping `index.html:14377-14380`.
Confidence **HIGH** that no today data exists; **LOW** for any ranking/leader/margin claim.

### (4) Cross-exchange Matrix — top 3 high-spread / price-discrepancy pairs
From `GET /api/screener` at epoch `1795184545` = `2026-11-20 22:22:25 UTC+8`; server sorts
by gross `pct` (`server.py:487`). All three: `profitable:false`, `updated:"just now"`,
`totalFeePct:0.18`, `slippagePct:0.0`.

| Asset | Buy venue / price | Sell venue / price | Gross spread | Est. net after fees | Data timestamp (epoch s) |
| --- | --- | --- | --- | --- | --- |
| ETH/USDT | Okx @ 1751.39 | Binance @ 1751.61 | 0.0126% | -0.1674% | maker 1795184544.8573482 / hedge 1795184544.868423 / evaluated 1795184545.094978 |
| ETH/USDT | Okx @ 1751.39 | Bybit @ 1751.59 | 0.0114% | -0.1686% | maker 1795184544.868423 / hedge 1795184544.888874 / evaluated 1795184545.095789 |
| BTC/USDT | Okx @ 61938.70 | Binance @ 61939.96 | 0.0020% | -0.1780% | maker 1795184544.8572092 / hedge 1795184544.767782 / evaluated 1795184545.062773 |

Snapshot timestamp `1795184517912` ms from `/api/live-snapshot`. The flattened opportunity
has only `updated:"just now"`; per-leg timestamps are in `opportunityA/B.timestamps`
(`models.py:539-575,842-910`). Values are volatile — re-poll for a fresh snapshot. (HIGH at poll time)

**Staleness thresholds found:** backend `stale_threshold_seconds = 10.0`
(`market_data_manager.py:49`, `models.py:312,1308`, `direction_engine.py:42`,
`bot_manager.py:62,84`, `spread_calculator.py:133`; invalidation 251-257);
UI Matrix `STALE_MS = 15000` (`index.html:12588`, age checks 12948/13403);
separate 4s guard for WS candle recording (4717/5168). (HIGH)

Important: the on-screen Matrix panel is rendered client-side from WS
`normalized_states` / `raw_spreads_dict` (`index.html:12592-12594,13454-13465,16709-16710`),
not directly from `/api/screener`. The table above uses the server-ranked screener feed —
closest authoritative cross-exchange list with net-of-fee fields. Confidence **MEDIUM**
that it is byte-for-byte what the panel renders.

## 1.6 Could not find (Trading Den)

- Any genuine 2026-11-20 PNL row, net/realized/unrealized split, fee amount, margin, or leader field — raw evidence `(0, None)`, `fills=0`, schema `persistence.py:106-113`.
- A persisted bot registry or stopped-bot names — `bot_manager.py:431` is in-memory; endpoint `count:0`.
- Server-side exchange credentials or any read-only/no-trade/no-withdraw permission config — only public CCXT config at `rest_connector.py:60-72` and prototype localStorage at `index.html:9928,10024-10047`.
- `Asia/Manila` timezone conversion in Trading Den Python — none found; PNL filters by `date` string (`persistence.py:728-754`), seeding used `datetime.date.today()` (`persistence.py:209`).
- Any backend source for UI's `+318.42 / +271.15 / 38,432.77` — hard-coded at `index.html:1847-1865`.
- Authorized access to `/api/trading-den` or unqualified `/api/status` — both `Unauthorized`.

---

# SYSTEM 2 — COSMOS / GENESIS

## 2.1 Where it lives

### Backend — confidence HIGH (direct file reads)

- `app.py` — imports COSMOS/GENESIS modules (~100-102); `PORT=8080` (121); GET forward 1400-1414; POST forward 3127-3130; PATCH 5402-5405; DELETE 5481-5484; Genesis runner/scheduler startup 5883-5887; **owner gate** 964-975 (all `/api/*` except `/api/auth/session` and the YouTube OAuth callback); token via `X-Dennis-Token` or `?token=` (858-862); session issued by `GET /api/auth/session` (1780-1783).
- `backend/cosmos_api.py` — dispatcher for `/api/cosmos`; Genesis delegation 52-58; reads 298-378; execute 384-397.
- `backend/genesis_api.py` — Genesis dispatcher `/api/cosmos/genesis`; GET 71-149; POST 170-208; PATCH/DELETE 225-234.
- `backend/cosmos_common.py` — data paths 29-33; `iso()` 65-66 = **local naive ISO, no offset**; divisions `("ads","youtube")` (40).
- `backend/cosmos_registry.py` — static registry of 5 intelligences; `AGENT_ORDER` (16); roles 199-491.
- `backend/cosmos_engine.py` — pipeline 1-12 (`config → role prompt → studio_llm → strict JSON → validate → persist execution/recommendations`); `run()` 440-499; failures saved as `failed` with `error{code,message}` (486-496).
- `backend/cosmos_store.py` — execution schema 37-61; recommendation schema 199-232; `agent_activity()` 387-411.
- `backend/genesis_store.py` — JSON paths 25-30; project/render/publication status enums 38-49; owner-only 0700 dir / 0600 files.
- `backend/genesis.py` — intake, queue, overview, QA, approval, archive/reopen; approval gate 1524-1592.
- `backend/genesis_render.py` — render records 328-382; failure fields 417-432; ffmpeg failure with exit code ~932.
- `backend/genesis_publish.py` — publish gates 487-634; publication record 576-634; attempt history 726-801; in-process scheduler 810-834; UTC schedule parsing 348-349, 509-517.

### Frontend — confidence HIGH

- `index.html` Genesis mount points 3028-3048; script includes 4308, 4331-4333.
- `js/cosmosState.js` — constants 17-23; `toLocaleString(undefined)` time display 279-290; tone map 292-321.
- `js/cosmosDashboard.js` — agent/overview loads 167-214; execute 1218.
- `js/cosmosGenesis.js` — panel state 22-49; loaders 177-260; render/QA/approval/publish 676-759; approve/withdraw/reopen/publish 1046-1116; binding + 4s polling 1150-1268.
- `js/cosmos.js` is **canvas animation only**, not business logic. CSS: `css/cosmos.css`.

### Live state files — confidence HIGH

Source of truth is **JSON, not SQL**:

- Present: `data/cosmos/config.json`, `data/cosmos/executions.json`, `data/cosmos/ads/backlog.json`, `data/cosmos/genesis/projects.json`, `data/cosmos/genesis/renders.json`.
- Defined in code but **absent**: `data/cosmos/recommendations.json`, `data/cosmos/genesis/publications.json`, `data/cosmos/genesis/oauth-tokens.json`.
- No COSMOS/GENESIS SQLite/Postgres found; only DB in tree is the unrelated Trading Den `denma_history.db`. (HIGH for checked tree, MEDIUM for whole machine.)
- Runtime: `app.py` PID 48225 on `*:8080`; no crontab; no launchd job; publishing scheduler is an in-process thread.

## 2.2 How it stores status

Versioned JSON list stores with atomic writes + process locks. Common keys: `version`, `updated_at`, `updated_iso`, plus primary list key (`executions`, `projects`, `renders`, …). (HIGH)

**COSMOS `config.json`** keys: `active_ads_template_id`, `active_youtube_template_id`, `active_youtube_channel_id`, `ads_templates`, `youtube_templates`, `updated_at`, `updated_iso`, `version`. Observed: `womens-apparel`, `educational-video`, `null`, `2026-11-17T11:07:06`. (HIGH)

**Execution record** (`cosmos_store.py:37-61`): `id, agent_id, division, capability, task, status, model, provider, prompt_sha256, config_snapshot, result, result_sha256, recommendation_ids, duplicates_skipped, filtered_out, insufficient_results, error{code,message}, started_at, started_iso, finished_at, finished_iso, duration_ms`. Status: `running | succeeded | failed`. (HIGH)

**Recommendation record** (`cosmos_store.py:199-232`): `id, agent_id, division, execution_id, capability, template_id, config_snapshot, title, summary, payload, sources, relevance_score, opportunity, competition, evidence_status, status, dedupe_key, user_decision, destination, affiliate_validation, refined_from, refined_to, last_draft_error, created_at, created_iso, updated_at, updated_iso`. Status: `proposed | drafted | rejected | refined`. (HIGH)

**Genesis project status:** `in_production, rendering, quality_review, awaiting_approval, approved, published, failed, archived`. (HIGH)
**Render status:** `queued, preparing, waiting_media, assembling, complete, failed, cancelled`. (HIGH)
**Publication status:** `awaiting_approval, scheduled, publishing, succeeded, failed, cancelled`. (HIGH)
Project record keys observed include `approval`, `quality_review`, `stage`, `status`, `history[]`, `render_ids`, `publication_ids`, `source`, `provenance`. Render keys include `error`, `detail`, `warnings`, `stage`, `engine`, `provider_jobs`, `manifest`. (HIGH)

**Current snapshot (2026-11-20, HIGH):**
- `executions.json`: 9 rows, all `andromeda` (7 ads, 2 youtube); **8 failed, 1 `running` stale since `2026-11-20T11:33:17`**. Errors: `llm_timeout: The OpenCode CLI did not answer in time.` and `llm_request_failed: Insufficient Balance (request_id: 12293f09-c879-4b6d-9425-21eb6317449c)`.
- `projects.json`: 1 project `gp_4d739f6344704564` ("Aurora Lamp — Meta Ads 15s"), `ads`, `status=in_production`, `stage=assets`, `quality_review=null`, `approval=null`, `updated_iso=2026-11-20T15:25:52`; history: quality review pass 2026-11-19T20:58:29, approved 2026-11-19T20:58:29, `returned_to_production` 2026-11-20T15:25:52.
- `renders.json`: 3 renders — `gr_9e1a81a9d6de4701` `failed`, `error.code=ffmpeg_failed`, `error.message="ffmpeg exited with code 8: Error opening input files: Option not found"`; other two `complete`.

**Audit / confirmation:** no separate audit module found (MEDIUM-HIGH). Confirmation is embedded in `project.history[]{at,iso,event,note}`, `project.approval{approved,by,at,at_iso,note,review_verdict,render_id,media_id}`, `quality_review{verdict,checks[],ran_iso,media_id}`, render `detail/warnings/error/provider_jobs/manifest`. UI Approve/withdraw (`cosmosGenesis.js:1046-1067`) has **no confirmation dialog**; Publish has an explicit checkbox (741, 1081-1108). (HIGH)

**Timezone:** `/etc/localtime → Asia/Manila`; `date` prints misleading `PST +0800`. Stored COSMOS timestamps are naive Manila-local from `cosmos_common.iso()`; publish scheduler uses UTC; frontend uses browser locale. (HIGH)

## 2.3 How to read it (exact commands)

`jq` is **not installed**; use `python3 -m json.tool`.

```bash
ROOT="/Users/macbookpro/PycharmProjects/DENNIS AGENTIC OS_Trading Den/Dennis-Agentic-OS"
find "$ROOT/data/cosmos" -maxdepth 3 -type f -print
python3 -m json.tool "$ROOT/data/cosmos/config.json"
python3 -m json.tool "$ROOT/data/cosmos/executions.json"
python3 -m json.tool "$ROOT/data/cosmos/ads/backlog.json"
python3 -m json.tool "$ROOT/data/cosmos/genesis/projects.json"
python3 -m json.tool "$ROOT/data/cosmos/genesis/renders.json"
for f in "$ROOT/data/cosmos/recommendations.json" \
         "$ROOT/data/cosmos/genesis/publications.json" \
         "$ROOT/data/cosmos/genesis/oauth-tokens.json"; do
  [ -e "$f" ] && echo "PRESENT $f" || echo "MISSING $f"
done
```

HTTP GET (read-only; most need owner token from `GET /api/auth/session`, passed as `X-Dennis-Token` or `?token=`):

```bash
TOKEN='<from /api/auth/session>'
curl -s -H "X-Dennis-Token: $TOKEN" http://127.0.0.1:8080/api/cosmos/overview
curl -s -H "X-Dennis-Token: $TOKEN" http://127.0.0.1:8080/api/cosmos/agents
curl -s -H "X-Dennis-Token: $TOKEN" http://127.0.0.1:8080/api/cosmos/agents/andromeda
curl -s -H "X-Dennis-Token: $TOKEN" 'http://127.0.0.1:8080/api/cosmos/agents/andromeda/executions?division=ads&limit=12'
curl -s -H "X-Dennis-Token: $TOKEN" http://127.0.0.1:8080/api/cosmos/config
curl -s -H "X-Dennis-Token: $TOKEN" http://127.0.0.1:8080/api/cosmos/genesis/overview
curl -s -H "X-Dennis-Token: $TOKEN" 'http://127.0.0.1:8080/api/cosmos/genesis/projects?division=ads&limit=100'
curl -s -H "X-Dennis-Token: $TOKEN" 'http://127.0.0.1:8080/api/cosmos/genesis/renders?limit=60'
```

Full COSMOS GET list: `/api/cosmos/{overview,agents,agents/<id>,agents/<id>/executions,agents/<id>/recommendations,agents/<id>/activity,executions/<id>,recommendations,recommendations/<id>,recommendations/<id>/execution,config,backlog/ads,backlog/ads/<id>,backlog/youtube,backlog/youtube/<id>}`. (HIGH)
Full Genesis GET list: `/api/cosmos/genesis/{overview,queue,projects,projects/<id>,media,renders,renders/<id>,render-engine,publications,publications/<id>,publishing/destinations}`. (HIGH)

Runtime checks: `lsof -nP -iTCP:8080 -sTCP:LISTEN`; `pgrep -fl 'Dennis-Agentic-OS/app.py'`; `crontab -l`; `launchctl list | grep -iE 'cosmos|genesis|dennis|agentic|trading'`. (HIGH)

## 2.4 How to control — Genesis only (endpoints found, NOT called)

All require owner token; YouTube OAuth callback is public by design. Confidence HIGH from dispatcher + frontend call sites.

**Projects:** `POST /api/cosmos/genesis/projects` (create); `PATCH /api/cosmos/genesis/projects/<id>` (edit); `DELETE .../projects/<id>` (soft delete); `POST .../projects/<id>/render`; `POST .../projects/<id>/quality-review`; `POST .../projects/<id>/approval` with `{"approved":true,"note":"..."}` or `{"approved":false}`; `POST .../projects/<id>/publish`; `POST .../projects/<id>/archive`; `POST .../projects/<id>/reopen`. (`genesis_api.py:171-187,225-234`; `cosmosGenesis.js:887-1013,1046-1067,1081-1108`)

Render guard (`genesis_api.py:211-219`): archived projects cannot render; render allowed only when status `in_production` or `failed`.

Publish payload (UI shape): `{"destination":"youtube","approved":true,"approval_note":"...","schedule_at":"2026-11-25T15:00:00Z","metadata":{"title":"..."}}`. Gates: quality review exists and not `fail`, `review.media_id == project.media_id` (`genesis.py:1529-1541`), body `approved is true`, project approval, render exists, destination authorized, no duplicate/in-progress publication (`genesis_publish.py:497-569`).

**Other:** `POST .../renders/<id>/cancel`; `POST .../publications/<id>/retry`; `POST .../publications/<id>/cancel` (`genesis_api.py:190-197`); `POST .../publishing/youtube/authorize[?port=]`; `POST .../publishing/youtube/disconnect`; `GET .../publishing/youtube/callback` (public).

**No Genesis "Restart" endpoint found (HIGH).** Nearest equivalents: `POST .../projects/<id>/reopen`, `POST .../projects/<id>/render`, `POST .../publications/<id>/retry`. **This is a gap for Phase 4A's Restart button** — it must map to an existing equivalent or be implemented, with confirmation + audit.

Related non-Genesis controls (not to be exposed as Jarvis write actions): `POST /api/cosmos/agents/<id>/execute` (`cosmos_api.py:385-397`), `/api/cosmos/recommendations/<id>/{draft,reject,refine,validate-affiliate}`, `POST /api/cosmos/config/active`.

## 2.5 The five intelligences

Registry (`cosmos_registry.py:16,199-491`), confidence HIGH:

| ID | Name | Role | Ads capability | YouTube capability |
| --- | --- | --- | --- | --- |
| `andromeda` | ANDROMEDA | Discovery Intelligence | Product Discovery | Topic Discovery |
| `polaris` | POLARIS | Strategy Intelligence | Commercial Strategy | Channel Strategy |
| `pleiades` | PLEIADES | Creative Intelligence | Product Creative | Video Creative |
| `quasar` | QUASAR | Research & Evidence Intelligence | Product Evidence | Fact Research |
| `helios` | HELIOS | Performance & Learning Intelligence | Ads Performance | YouTube Performance |

Each operates in divisions `ads` and `youtube`. **No persistent per-agent status row, heartbeat, or `agents.json`** exists; status is derived from `executions.json` (and `recommendations.json` when present) via `cosmos_store.agent_activity()` (387-411) and `cosmos_api._agent_detail()` (287-292). (HIGH)

Current derived state (HIGH): only ANDROMEDA has records (9 executions: 8 failed, 1 stale running; no recommendations file). POLARIS, PLEIADES, QUASAR, HELIOS have **no persisted activity** — registered in code but no records. Do not infer healthy/disabled from absence.

## 2.6 Genesis panel sources for Phase 4A

- Loaders (`cosmosGenesis.js:177-260`): overview → `GET /api/cosmos/genesis/overview`; queue → `/queue`; projects → `/projects`; project detail → `/projects/<id>` (returns `project`, `renders`, `publications`, `media` — `genesis_api.py:92-99`); renders → `/renders?limit=60`; publications → `/publications?limit=60`; destinations → `/publishing/destinations`; engine → `/render-engine`; media → `/api/studio/library` and `/media?ids=...`.
- **Working on:** project `status`/`stage`, render `status`/`stage`/`detail`, publication `status`, engine availability, destination authorization.
- **Needs approval:** `status=awaiting_approval`, `approval.approved != true`, `quality_review.verdict`; display `approval.by/at_iso/note` (`cosmosGenesis.js:710-723`).
- **Stopped:** `status=failed` on projects/renders/publications; `error{code,message}`, `detail`, `warnings[]`, `history[].note`, plus COSMOS execution `error{code,message}`. Example last line: `ffmpeg exited with code 8: Error opening input files: Option not found`. **No separate Genesis log file** — the "last log line" is `render.detail` (`cosmosGenesis.js:681`) / `error.message`.
- Polling: every 4s, only when visible and only on renders/overview/projects tabs and only for in-flight render/publication states (`cosmosGenesis.js:1257-1268`).

## 2.7 Confidence summary (COSMOS)

| Finding | Confidence | Basis |
| --- | --- | --- |
| File/module map, routing, owner gate | HIGH | direct reads |
| Control endpoints + payload shapes | HIGH | dispatcher + call sites; not executed |
| Execution/recommendation/project/render schemas | HIGH | direct reads |
| Current JSON snapshot | HIGH | direct parse |
| Five intelligences + roles | HIGH | registry read |
| No persistent agent-status row | HIGH | derived in code; no state file found |
| Timezone handling | HIGH | zoneinfo + code reads |
| No COSMOS SQL store | HIGH (tree) / MEDIUM (machine) | only unrelated DB found |
| No separate audit module | MEDIUM-HIGH | none found in `backend/{cosmos,genesis}*.py` |
| No OS scheduler | HIGH (user context) | empty crontab, no launchd match |
| No Restart endpoint | HIGH | no route found |
| External publication truth | UNKNOWN | no `publications.json`; external APIs not called |

## 2.8 Could not find (COSMOS)

1. COSMOS/GENESIS SQLite/Postgres store — none; only unrelated Trading Den DB.
2. Persistent agent-status file or heartbeat — none; status derived from execution/recommendation records.
3. Defined-but-absent files: `recommendations.json`, `publications.json`, `oauth-tokens.json`.
4. COSMOS/GENESIS-specific log file — none; render "logs" are record fields `detail`, `warnings`, `error.message`.
5. OS scheduler job — none; publishing runs in-process.
6. Genesis Restart endpoint — none; nearest: reopen / new render / publication retry.
7. Approval confirmation dialog in existing UI — none; publish has a checkbox gate.
8. `js/cosmos.js` as system logic — it is only canvas animation.
9. External platform truth for publications — no local file, no external calls made.
10. Hidden/deployed variants elsewhere on the machine — not proven absent; this reflects the checked repo and running process.

---

## 3. Phase 2 gaps to resolve in later phases (no guessing now)

- **Restart action:** no native Genesis restart endpoint; needs mapping to reopen/render/retry or an explicit implementation.
- **PNL/bot/arbitrage data:** no genuine today data; the brief must render "unknown + raw evidence", not invented numbers.
- **Owner token:** `/api/*` reads need a session token; the browser must only ever receive connection status, never provider keys — token handling needs care in Phase 3/4.
- **Audit log:** embedded history exists but no append-only audit module; Phase 3/4 write actions need a dedicated append-only log.
