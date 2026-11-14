# Ads Studio UI Audit & Redesign — DENNIS Agent OS

**Date:** 2026-11-13
**Scope:** Ads Studio workspace UI (five-step Generate workflow, Assets, Preset Video, Ads header chrome, voiceover strip).
**Method:** Live computed-style probes in headless Chrome → full-page screenshots at 1600 px and 780 px for all five steps + Setup → CSS/markup trace of every `collart-*` component → redesign as a scoped overlay stylesheet → screenshot diff + full regression (E2E, JS tests).

**Companion docs:** `docs/STUDIO_RELIABILITY_AUDIT.md` (backend/pipeline reliability, 2026-11-12) — this document covers the Ads Studio *interface* layer only. No generation logic, IDs, route names or test hooks were changed.

---

## 1. Executive summary

The Ads Studio workflow itself (Model → Product/Props → Dressing → Reference Video → Production) is functionally sound and fully covered by `tests/e2e_ads_studio.py`. The **presentation layer is not**: the workspace still wears the pre-OS light "Collart" skin while the rest of DENNIS runs the ui-ux-pro-max OLED design system (`design-system/dennis-agentic-os/MASTER.md`, implemented in `css/theme-uiuxpro.css`). YouTube Studio already received an OS-design alignment pass (`css/studio-youtube.css`); Ads Studio is now the last light surface in the app, and the collision between the two themes produces one user-visible defect and several coherence/polish issues:

> **The Studio header renders dark while its text keeps light-theme colors — the "Dennis Studio" brand is invisible.**

Verified live: header background `rgb(11,17,32)` vs brand text `rgb(24,24,27)`. Root cause: `css/theme-uiuxpro.css` forces `header { background-color: var(--uupm-bg-deep) !important }` on every `<header>`, but the Collart skin's brand styles (written before that theme existed) still assume a white bar.

The redesign re-skins the Ads workspace onto the persisted MASTER.md OLED system — same treatment YouTube got — via a new scoped overlay (`css/studio-ads.css`), fixing the header defect, a broken dropdown-chevron layout bug present on every select, truncated provider labels, off-token accents, missing focus states, and narrow-viewport crowding. All 40+ element IDs and class hooks the E2E suite pins are preserved; behavior is untouched.

---

## 2. What already exists (verified before touching anything)

- **Ads Studio is a first-class workspace**, not a separate page: `#studio-view[data-workspace="ads"]`, sidebar `#studio-ads-sidebar`, main `#studio-ads-main` (stepper + `collart-content` panels + bottom dock). Workspace switching (`js/studioWorkspaces.js`) toggles `data-workspace` and hides the inactive main. Preserved as-is.
- **Shared pipeline:** Ads generation calls `/api/studio/free/*` (Pollinations/Gemini) and the provider registry routes; the five-step state machine lives in `js/studioFashion.js` (2 900 lines). Untouched.
- **Design system already persisted:** `design-system/dennis-agentic-os/MASTER.md` (Dark Mode OLED, green action accent, Inter, 180 ms motion, focus-visible, anti-patterns: light-mode default, invisible focus, low contrast). `css/theme-uiuxpro.css` implements the `--uupm-*` token set and loads last.
- **Precedent:** `css/studio-youtube.css` re-skinned the YouTube workspace onto the OS tokens with strictly scoped overrides and zero markup churn. The Ads redesign follows the identical pattern.

---

## 3. Findings (per defect)

Severity: Critical / High / Medium / Low. Every finding was reproduced in the live browser before the fix.

### U1 — Critical · Header brand invisible (dark text on dark header)
- **Where:** `collart-header` / `collart-brand-logo` / `collart-brand-name` (`css/studio-v2.css:4896-4934`), markup `index.html:1611-1615`.
- **Root cause:** `css/theme-uiuxpro.css:110` sets `header { background-color: var(--uupm-bg-deep) !important }` (element selector + `!important` beats `#studio-view .collart-header { background:#ffffff }`). The Collart brand text `#18181b` and logo border `#18181b` were written for the white bar.
- **Evidence (live probe):** `{hdrBg: 'rgb(11,17,32)', brandColor: 'rgb(24,24,27)'}`; visible in every "before" screenshot.
- **Fix:** Ads-scoped header styles: brand logo/name in `--uupm-fg`, dark workspace-switch and status pill, ghost icon buttons — so the forced-dark header gets correctly matching foreground.

### U2 — Critical · Split-brain theme (light Ads island in an OLED app)
- **Where:** `#studio-view` token re-mapping (`css/studio-v2.css:3687-3707`) re-points `--sv-*` to `#f7f7f8`/`#ffffff` and every collart component hardcodes light surfaces.
- **Evidence (live probe):** `#studio-view` bg `rgb(247,247,248)`, sidebar white — while the app shell, Setup/History/Library panels and YouTube workspace are OLED dark. Master file anti-pattern: "❌ Light mode default".
- **User impact:** switching Generate ↔ Setup/History in the sidebar flips white → dark; the light panel is the only non-OLED surface left in the product.
- **Fix:** full OLED re-skin of every collart component (cards, dropzones, steppers, dock, tiles, menus, modal, banners, result panel, assets/preset pages) using `--uupm-*` tokens.

### U3 — High · Dropdown chevrons detached (render below every select)
- **Where:** chevron positioning rules target the placeholder element: `#studio-view .collart-select i` (`studio-v2.css:4059`) and `.collart-darkselect i` (`studio-v2.css:5110`).
- **Root cause:** Lucide's `createIcons()` replaces `<i data-lucide="chevron-down">` with an `<svg>` (keeping classes). The `i` selectors stop matching, so the chevron loses `position:absolute` and flows beneath the select. Affects every Provider/Model pair, filter select and template dropdown.
- **Evidence:** chevrons visible under selects in all "before" screenshots (e.g. step-2, step-5).
- **Fix:** position both `i` and `svg` in the overlay.

### U4 — High · Provider labels truncated in the per-step genpair
- **Where:** `.collart-select-narrow { width:150px }` / `.collart-select { width:170px }` + 42 px horizontal padding.
- **Evidence:** "Auto (free engines" clipped in step 2/3/5 provider selects.
- **Fix:** widen to 190/170 px; full-width model select already via `.collart-genmodel`.

### U5 — Medium · Voiceover strip off-system accent
- **Where:** `js/voiceRegistry.js` builds the Ads strip with `bg-blue-600` CTA.
- **Fix (CSS-only, scoped `#ads-voiceover`):** green `--uupm-accent` CTA with on-accent text, slate border/status tones. The shared builder is untouched (Apollo/YouTube mounts unaffected).

### U6 — Medium · Cyan focus/checkbox accents contradict the design system
- **Where:** `.collart-darkselect select:focus { border-color:#22d3ee }`, `accent-color:#22d3ee` (`studio-v2.css:5108,5134`). MASTER.md: sky is *status/info only, never CTA/action*.
- **Fix:** focus = action accent (green) + visible focus ring; checkbox `accent-color: var(--uupm-accent)`.

### U7 — Medium · Invisible keyboard focus on collart controls
- **Where:** `outline:none` on selects/textareas (`studio-v2.css:4006,4055,5104`) suppresses the global focus pass.
- **Fix:** `:focus-visible` outline (accent, 2 px) on all collart interactive controls.

### U8 — Medium · Narrow-viewport header crowding (≤ 780 px)
- **Where:** `css/studio-youtube.css:817` hides workspace-switch labels for both workspaces; the brand has no narrow treatment and wraps.
- **Evidence:** step-1-narrow.png — two icon-only pills, brand clipped.
- **Fix:** ads-scoped narrow rules: hide the brand wordmark (keep logo mark), keep the workspace switch labels visible down to 640 px, allow the build-agent chip to collapse.

### U9 — Low · Stale "Collart" copy
- **Where:** sidebar contact card says "Nothing is sent to Collart." (`index.html:1672`).
- **Fix:** copy corrected to the actual product.

### U10 — Low · Light-theme artifacts on dark surfaces
- White kebab menu/modal/dockthumbs, white badge backgrounds, `#fff5f8` hover tints, white `glass-panel` override (`studio-v2.css:4850`) — all re-mapped to OLED equivalents in the overlay.

---

## 4. Redesign implementation

- **`css/studio-ads.css`** (new, loaded after `theme-uiuxpro.css`): the OLED re-skin.
  - Shared chrome scoped to `#studio-view[data-workspace="ads"]` (header, brand, workspace switch, status pill, build-agent chip, voiceover strip) so YouTube's overrides stay authoritative on its workspace.
  - Ads-owned components (sidebar, stepper, step panels, dock, kebab, modal, assets/preset pages) scoped under `#studio-view` / `#studio-ads-main`, mirroring the component order of `studio-v2.css` so every override is auditable.
  - Token discipline: only `--uupm-*` variables + slate alpha borders; radii 10/14/16 + pill; one motion speed (`--uupm-speed`); green = action, amber = warning, red = danger, sky = untouched info accents.
- **`index.html`:** stylesheet link + the U9 copy fix. No IDs, classes, data-attributes or structure changed.
- **Behavior:** none. The E2E contract (`tests/e2e_ads_studio.py`) pins IDs/classes/counts — all preserved by construction.

---

## 5. Verification

| Check | Result |
|---|---|
| Live computed probes (header brand contrast, select chevron geometry, focus outline) | pass after fix |
| Full-page screenshots, 5 steps × 1600 px + 780 px + Setup, before/after | coherent OLED surfaces, no truncated labels, no detached chevrons |
| `tests/e2e_ads_studio.py --serve` (headless Chrome, all phases) | ALL ADS STUDIO E2E CHECKS PASSED |
| JS suites (`studioState`, `studioWorkspaceState`) | pass |
| Python studio suite | pass (no backend changes expected or made) |
| Master-file pre-delivery checklist (contrast ≥ 4.5:1 for text roles, visible focus, cursor affordance, 150–300 ms transitions, SVG icons only, reduced motion) | pass |

---

## 6. Remaining limitations

- The old light Collart rules remain in `studio-v2.css` underneath the overlay (deliberately: same approach as the YouTube pass — lowest-risk, reviewable, reversible). A future cleanup can delete the superseded light declarations once both overlays are proven.
- The voiceover strip markup (Tailwind utilities in `js/voiceRegistry.js`) is only re-accented via CSS; restructuring that builder is a cross-studio change out of scope here.
- OLED surfaces inherit the global `prefers-reduced-motion` guard from `theme-uiuxpro.css`; the collart fade/spin keyframes are kept but respect it.
