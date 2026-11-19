/*
 * Code & Preview panel state machine (pure, dependency-free).
 *
 * States (exactly as specified for the Workspace rebuild):
 *
 *   S0  No agent selected      - panel hidden, handle hidden.
 *   S1  Collapsed (default)    - agent screen uses the full row width; a slim
 *                                "Code & Preview" handle sits on the right edge.
 *   S2  Split (expanded)       - agent screen narrows; Code and Preview side by side.
 *   S3  Code maximized in panel- Code fills the panel, Preview is a slim strip.
 *   S4  Preview maximized      - Preview fills the panel, Code is a slim strip.
 *   S5  Code in OS fullscreen  - Code covers the whole app window.
 *   S6  Preview in OS fullscreen.
 *
 * Invariants:
 *   - Code and Preview are never both hidden while the panel is expanded
 *     (S2 shows both; S3/S4 show one plus the other's restorable strip).
 *   - S5/S6 remember `prev` and return to it exactly on exit.
 *   - The panel state, open file and preview URL survive collapse/expand.
 *   - reset() clears the file, editor position, preview URL and pane state.
 *
 * The module is side-effect free: loaded in the browser and unit tested under
 * node.
 */
(function (root, factory) {
  if (typeof module === "object" && module.exports) {
    module.exports = factory();
  } else {
    root.PanelState = factory();
  }
})(typeof self !== "undefined" ? self : this, function () {
  var CONTENT_STATES = ["S2", "S3", "S4"];
  var PANES = ["code", "preview"];

  var S0 = "S0", S1 = "S1", S2 = "S2", S3 = "S3", S4 = "S4", S5 = "S5", S6 = "S6";

  function defaultPanel() {
    return {
      state: S1,
      pane: "split",
      code_file: null,
      editor: null,
      preview_url: null,
      prev: null,
    };
  }

  function normalize(panel) {
    var base = defaultPanel();
    if (panel && typeof panel === "object") {
      if ([S0, S1, S2, S3, S4, S5, S6].indexOf(panel.state) !== -1) base.state = panel.state;
      if (["split", "code", "preview"].indexOf(panel.pane) !== -1) base.pane = panel.pane;
      if (panel.code_file != null) base.code_file = panel.code_file;
      if (panel.editor != null) base.editor = panel.editor;
      if (panel.preview_url != null) base.preview_url = panel.preview_url;
      if (CONTENT_STATES.indexOf(panel.prev) !== -1) base.prev = panel.prev;
    }
    if (base.state === S5 || base.state === S6) {
      if (CONTENT_STATES.indexOf(base.prev) === -1) base.prev = S2;
    } else {
      base.prev = null;
    }
    return base;
  }

  function isOverlay(state) { return state === S5 || state === S6; }
  function isContent(state) { return CONTENT_STATES.indexOf(state) !== -1; }
  function hasCode(panel) { return !!panel.code_file; }
  function hasPreview(panel) { return !!panel.preview_url; }

  /* Selecting an agent moves S0 -> S1; nothing else changes. */
  function agentSelected(panel) {
    var next = normalize(panel);
    if (next.state === S0) next.state = S1;
    return next;
  }

  /* Deselecting the last agent hides the panel and its handle. */
  function agentCleared(panel) {
    var next = normalize(panel);
    next.state = S0;
    next.prev = null;
    return next;
  }

  /* S1 -> S2, keeping whatever is open. */
  function expand(panel) {
    var next = normalize(panel);
    if (next.state === S1 || next.state === S0) {
      next.state = S2;
      next.pane = "split";
    }
    return next;
  }

  /* Any -> S1. Panel state, open file and URL are kept. */
  function collapse(panel) {
    var next = normalize(panel);
    next.state = S1;
    next.prev = null;
    return next;
  }

  function toggleCollapse(panel) {
    var p = normalize(panel);
    if (p.state === S1 || p.state === S0) return expand(p);
    return collapse(p);
  }

  /* Open a file. From collapsed/empty states this expands to S2 (spec:
   * auto-expand on handoff is S1 -> S2). While expanded, the current
   * pane arrangement is preserved. */
  function openCode(panel, file, editor) {
    var next = normalize(panel);
    next.code_file = file;
    if (editor != null) next.editor = editor;
    if (next.state === S0 || next.state === S1) {
      next.state = S2;
      next.pane = "split";
    }
    return next;
  }

  function openPreview(panel, url) {
    var next = normalize(panel);
    next.preview_url = url;
    if (next.state === S0 || next.state === S1) {
      next.state = S2;
      next.pane = "split";
    }
    return next;
  }

  function closeCode(panel) {
    var next = normalize(panel);
    next.code_file = null;
    next.editor = null;
    if (next.state === S3) next.state = S2;
    return next;
  }

  function closePreview(panel) {
    var next = normalize(panel);
    next.preview_url = null;
    if (next.state === S4) next.state = S2;
    return next;
  }

  /* Maximize one pane inside the panel: S2/S1 -> S3 (code) / S4 (preview). */
  function maximizeCode(panel) {
    var next = normalize(panel);
    next.state = S3;
    next.pane = "code";
    return next;
  }

  function maximizePreview(panel) {
    var next = normalize(panel);
    next.state = S4;
    next.pane = "preview";
    return next;
  }

  /* Minimize a pane. Never both hidden at once:
   *   code:    S3 -> S2 (split)  | S2 -> S4 (preview maximized)
   *   preview: S4 -> S2 (split)  | S2 -> S3 (code maximized) */
  function minimizeCode(panel) {
    var next = normalize(panel);
    if (next.state === S3) { next.state = S2; next.pane = "split"; }
    else if (next.state === S2) { next.state = S4; next.pane = "preview"; }
    return next;
  }

  function minimizePreview(panel) {
    var next = normalize(panel);
    if (next.state === S4) { next.state = S2; next.pane = "split"; }
    else if (next.state === S2) { next.state = S3; next.pane = "code"; }
    return next;
  }

  /* Enter OS fullscreen for one pane, remembering the exact state to restore.
   * Opening fullscreen from S3 returns to S3, from S4 to S4, from S2 to S2. */
  function fullscreen(panel, pane) {
    var next = normalize(panel);
    pane = pane === "preview" ? "preview" : "code";
    if (isOverlay(next.state)) {
      next.state = pane === "preview" ? S6 : S5;
      next.pane = pane;
      return next;
    }
    next.prev = isContent(next.state) ? next.state : S2;
    next.state = pane === "preview" ? S6 : S5;
    next.pane = pane;
    return next;
  }

  /* Leave OS fullscreen and return to the exact previous state. */
  function exitFullscreen(panel) {
    var next = normalize(panel);
    if (!isOverlay(next.state)) return next;
    next.state = CONTENT_STATES.indexOf(next.prev) !== -1 ? next.prev : S2;
    next.pane = next.state === S3 ? "code" : next.state === S4 ? "preview" : "split";
    next.prev = null;
    return next;
  }

  function toggleFullscreen(panel, pane) {
    var p = normalize(panel);
    if (isOverlay(p.state)) {
      if ((p.state === S5 && pane === "code") || (p.state === S6 && pane === "preview")) {
        return exitFullscreen(p);
      }
      return fullscreen(p, pane);
    }
    return fullscreen(p, pane);
  }

  /* X button: back to collapsed and fresh (file, editor, URL, pane state gone).
   * The agent session itself is never touched. */
  function reset(panel) {
    var next = defaultPanel();
    next.state = S1;
    return next;
  }

  /* Escape: exits OS fullscreen first, then panel maximize on a second press. */
  function escape(panel) {
    var p = normalize(panel);
    if (isOverlay(p.state)) {
      return { panel: exitFullscreen(p), handled: true };
    }
    if (p.state === S3 || p.state === S4) {
      return { panel: normalize(Object.assign({}, p, { state: S2, pane: "split" })), handled: true };
    }
    return { panel: p, handled: false };
  }

  /* Everything the view layer needs; never inspect `state` directly. */
  function describe(panel) {
    var p = normalize(panel);
    var state = p.state;
    var overlay = isOverlay(state);
    var codeMax = state === S3;
    var previewMax = state === S4;
    var split = state === S2;
    return {
      state: state,
      pane: p.pane,
      noAgent: state === S0,
      collapsed: state === S1,
      expanded: !(state === S0 || state === S1),
      split: split,
      codeMax: codeMax,
      previewMax: previewMax,
      overlay: overlay,
      fullscreenPane: state === S5 ? "code" : state === S6 ? "preview" : null,
      prev: p.prev,
      showHandle: state !== S0,
      showPanel: !(state === S0 || state === S1),
      showCode: !(state === S0 || state === S1) &&
        (split || codeMax || state === S5),
      showPreview: !(state === S0 || state === S1) &&
        (split || previewMax || state === S6),
      codeStrip: previewMax && !overlay,
      previewStrip: codeMax && !overlay,
      hasCode: hasCode(p),
      hasPreview: hasPreview(p),
      codeFile: p.code_file,
      previewUrl: p.preview_url,
    };
  }

  return {
    S0: S0, S1: S1, S2: S2, S3: S3, S4: S4, S5: S5, S6: S6,
    CONTENT_STATES: CONTENT_STATES,
    PANES: PANES,
    defaultPanel: defaultPanel,
    normalize: normalize,
    agentSelected: agentSelected,
    agentCleared: agentCleared,
    expand: expand,
    collapse: collapse,
    toggleCollapse: toggleCollapse,
    openCode: openCode,
    openPreview: openPreview,
    closeCode: closeCode,
    closePreview: closePreview,
    maximizeCode: maximizeCode,
    maximizePreview: maximizePreview,
    minimizeCode: minimizeCode,
    minimizePreview: minimizePreview,
    fullscreen: fullscreen,
    exitFullscreen: exitFullscreen,
    toggleFullscreen: toggleFullscreen,
    reset: reset,
    escape: escape,
    describe: describe,
  };
});
