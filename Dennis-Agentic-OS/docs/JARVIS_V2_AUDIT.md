# JARVIS V2 — Audit of the current stack (Hermes Apollo)

_Audited: 2026-11-12, before the JARVIS V2 setup guide is applied._

The "Jarvis OS" today is the **Hermes Apollo Voice Core** in `js/galaxy.js`
(~1200 lines), its voice engine in `tts_service/` + `backend/tts.py`, and a set
of bridges in `js/app.js`. This document lists what V2 should **reuse**, what
to **fix**, and what to **remove**.

## Verdict summary

| Area | Verdict |
| --- | --- |
| Voice wake + recognition loop | **Reuse** (works, hardened against self-hearing) |
| Kokoro TTS router + JARVIS preset | **Reuse** (central engine, 3-tier fallback) |
| Voice Provider Registry UI | **Reuse** |
| Navigation voice commands | **Reuse + extend** (new views added 2026-11-12) |
| Agent bridge (`/api/chat`) | **Fix applied** — now follows the live Workspace selection |
| `triggerHeaderTab` commands | **Removed** (target DOM no longer exists) |
| JARVIS dashboard surface | **New** — full home view, default on launch |
| Repo-root audio test artifacts | **Remove** (untracked leftovers) |

## Keep (working, verified)

1. **Wake word system** — `webkitSpeechRecognition` loop with "Hey Apollo /
   Apollo / Hermes / Jarvis / wake up" triggers; mic is suspended while Jarvis
   speaks so he never transcribes himself; auto-sleep after inactivity.
2. **Kokoro TTS service** (`tts_service/`, `backend/tts.py`, `js/tts.js`) —
   persistent `JARVIS — BRITISH MALE` preset (`bm_george`, fallback
   `bm_lewis`), 3-level provider selection (Kokoro → ElevenLabs → browser
   fallback), per-studio presets (Apollo = jarvis). Tested by
   `tests/test_tts.py`.
3. **Voice Provider Registry** — centralized provider UI
   (`js/voiceRegistry.js`, section 6 of the workspace view).
4. **Acoustic identity** — dual-harmonic wake/ack chimes via WebAudio.
5. **State engine** — `standby | listening | awake | speaking | muted` with the
   reactive galaxy visual (prefers-reduced-motion + offscreen pausing).
6. **Navigation protocols** — workspace, trading den, vault, hermes, studio,
   new session, status, time, identity, sleep — all drive real views via
   `switchMainView`.
7. **`/api/chat` one-shot CLI pipeline** — runs the selected agent CLI in
   `--print` mode with its real model; still fully functional.

## Fixed on 2026-11-12 (V2 pre-work)

1. **Dead header-tab commands removed.** "Hermes Chat" and "Bots" voice
   commands called `triggerHeaderTab()`, which queried `.header-nav-tab` —
   elements that no longer exist (top tabs were removed earlier). The helper
   and its two call sites are gone.
2. **New views wired into the voice protocols.** "memory galaxy / galaxy /
   star map" → Memory Galaxy page; "dashboard / home view" → JARVIS home;
   "skill", "plugin", "free llm api" → their views.
3. **Agent bridge follows the live selection.** `DennisAgentBridge` used to
   read `activeAgentState` from the removed DENEVO chat panel (stuck on
   `antigravity` + `gemini-2.5-flash`). `window.Workspace.voiceContext()` now
   exposes the live Agent + Model toolbar selection and the bridge uses it.
4. **JARVIS dashboard is the OS landing view.** `#jarvis-view` (core visual,
   live status cards, recent memory, quick commands) loads first; the agent
   workspace moved behind the "Workspace" sidebar entry.

## Remove

1. **Repo-root test artifacts** — `jarvis_test.wav`, `s1.pcm`, `s1.txt`,
   `s2.wav`, `s3.pcm`, `s3.txt`, `st.txt` (TTS development leftovers, untracked).
2. Nothing else: after the fixes above there is no dead Jarvis code path left.

## V2 hook points (for the Skool guide's 4 steps)

- **Voice core** → `js/galaxy.js` (`HermesGalaxy`, exported as
  `window.hermesApollo`); dashboard surface → `js/jarvisDashboard.js`.
- **TTS** → `tts_service/` (Kokoro), `backend/tts.py` (router), presets in
  `data/tts.json`.
- **Commands** → `HermesGalaxy.executeVoiceCommand()`; the general-brain path
  is `submitToSelectedAgent()` → `window.DennisAgentBridge` → `/api/chat`.
- **Default view** → `switchMainView('jarvis')` in `js/app.js`
  (`DOMContentLoaded`), and the `VIEWS` fallback in `switchMainView`.

When the guide's exact steps and download links are provided, map each step
onto one of the hook points above instead of duplicating functionality.
