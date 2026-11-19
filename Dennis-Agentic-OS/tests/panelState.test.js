/*
 * Node test for js/panelState.js (spec S0-S6 model).
 * Run with:  node tests/panelState.test.js
 */
const assert = require("assert");
const PanelState = require("../js/panelState.js");

let passed = 0;
function ok(name, fn) {
  try {
    fn();
    passed++;
    console.log("  ok - " + name);
  } catch (err) {
    console.error("  FAIL - " + name);
    console.error("    " + err.message);
    process.exitCode = 1;
  }
}

function expandedWithBoth() {
  let p = PanelState.openCode(PanelState.defaultPanel(), "main.py");
  p = PanelState.openPreview(p, "http://localhost:3000");
  return p;
}

function assertNeverBothHidden(panel, label) {
  const d = PanelState.describe(panel);
  if (d.expanded) {
    assert.ok(d.showCode || d.codeStrip, label + ": Code must be visible or a strip");
    assert.ok(d.showPreview || d.previewStrip, label + ": Preview must be visible or a strip");
    assert.ok(!(d.previewStrip && d.codeStrip), label + ": both panes cannot be minimized");
  }
}

ok("default panel is S1 collapsed with handle, nothing open", () => {
  const d = PanelState.describe(PanelState.defaultPanel());
  assert.strictEqual(d.state, "S1");
  assert.ok(d.collapsed);
  assert.ok(d.showHandle);
  assert.ok(!d.showPanel);
  assert.ok(!d.showCode && !d.showPreview);
});

ok("no agent selected hides the panel and the handle (S0)", () => {
  const p = PanelState.agentCleared(PanelState.defaultPanel());
  const d = PanelState.describe(p);
  assert.strictEqual(d.state, "S0");
  assert.ok(d.noAgent);
  assert.strictEqual(d.showHandle, false);
  assert.strictEqual(d.showPanel, false);
});

ok("selecting an agent goes S0 -> S1", () => {
  let p = PanelState.agentCleared(PanelState.defaultPanel());
  p = PanelState.agentSelected(p);
  assert.strictEqual(PanelState.describe(p).state, "S1");
});

ok("S1 -> S2 on handle/expand, contents kept", () => {
  let p = PanelState.openCode(PanelState.defaultPanel(), "a.py"); // S2
  p = PanelState.collapse(p);                                    // S1, file kept
  p = PanelState.expand(p);
  const d = PanelState.describe(p);
  assert.strictEqual(d.state, "S2");
  assert.ok(d.split && d.showCode && d.showPreview);
  assert.strictEqual(d.codeFile, "a.py");
});

ok("opening code from collapsed expands to S2 (handoff auto-expand)", () => {
  const p = PanelState.openCode(PanelState.defaultPanel(), "src/app.js");
  const d = PanelState.describe(p);
  assert.strictEqual(d.state, "S2");
  assert.ok(d.showCode && d.showPreview);
});

ok("opening a preview URL from collapsed also expands to S2", () => {
  const p = PanelState.openPreview(PanelState.defaultPanel(), "http://localhost:5173");
  assert.strictEqual(PanelState.describe(p).state, "S2");
});

ok("S2 -> S3 maximize Code, Preview becomes a strip", () => {
  let p = expandedWithBoth();
  p = PanelState.maximizeCode(p);
  const d = PanelState.describe(p);
  assert.strictEqual(d.state, "S3");
  assert.ok(d.showCode && !d.showPreview);
  assert.ok(d.previewStrip);
  assertNeverBothHidden(p, "S3");
});

ok("S2 -> S4 maximize Preview, Code becomes a strip", () => {
  let p = expandedWithBoth();
  p = PanelState.maximizePreview(p);
  const d = PanelState.describe(p);
  assert.strictEqual(d.state, "S4");
  assert.ok(d.showPreview && !d.showCode);
  assert.ok(d.codeStrip);
  assertNeverBothHidden(p, "S4");
});

ok("S3 -> S2 minimizing the maximized pane", () => {
  let p = PanelState.maximizeCode(expandedWithBoth());
  p = PanelState.minimizeCode(p);
  assert.strictEqual(PanelState.describe(p).state, "S2");
});

ok("S4 -> S2 minimizing the maximized pane", () => {
  let p = PanelState.maximizePreview(expandedWithBoth());
  p = PanelState.minimizePreview(p);
  assert.strictEqual(PanelState.describe(p).state, "S2");
});

ok("S2 minimizing Code maximizes Preview and vice versa (never both hidden)", () => {
  let p = PanelState.minimizeCode(expandedWithBoth());
  assert.strictEqual(PanelState.describe(p).state, "S4");
  assertNeverBothHidden(p, "minimize code in S2");
  p = PanelState.minimizePreview(expandedWithBoth());
  assert.strictEqual(PanelState.describe(p).state, "S3");
  assertNeverBothHidden(p, "minimize preview in S2");
});

ok("restoring a strip maximizes that pane in the panel", () => {
  let p = PanelState.minimizePreview(expandedWithBoth());   // S3, preview strip
  p = PanelState.maximizePreview(p);
  assert.strictEqual(PanelState.describe(p).state, "S4");
});

ok("S2 -> S5 fullscreen Code; exit returns to S2 exactly", () => {
  let p = expandedWithBoth();
  p = PanelState.fullscreen(p, "code");
  let d = PanelState.describe(p);
  assert.strictEqual(d.state, "S5");
  assert.ok(d.overlay && d.showCode && !d.showPreview);
  p = PanelState.exitFullscreen(p);
  d = PanelState.describe(p);
  assert.strictEqual(d.state, "S2");
  assert.strictEqual(d.prev, null);
});

ok("fullscreen from S3 returns to S3 on exit", () => {
  let p = PanelState.maximizeCode(expandedWithBoth());
  p = PanelState.fullscreen(p, "code");
  assert.strictEqual(PanelState.describe(p).state, "S5");
  p = PanelState.exitFullscreen(p);
  const d = PanelState.describe(p);
  assert.strictEqual(d.state, "S3");
  assert.ok(d.previewStrip);
});

ok("fullscreen from S4 (preview) returns to S4 on exit", () => {
  let p = PanelState.maximizePreview(expandedWithBoth());
  p = PanelState.fullscreen(p, "preview");
  assert.strictEqual(PanelState.describe(p).state, "S6");
  p = PanelState.exitFullscreen(p);
  const d = PanelState.describe(p);
  assert.strictEqual(d.state, "S4");
  assert.ok(d.codeStrip);
});

ok("S6 shows Preview only, S5 shows Code only", () => {
  let p = PanelState.fullscreen(expandedWithBoth(), "preview");
  let d = PanelState.describe(p);
  assert.ok(d.showPreview && !d.showCode, "S6 shows preview only");
  p = PanelState.exitFullscreen(p);
  p = PanelState.fullscreen(p, "code");
  d = PanelState.describe(p);
  assert.ok(d.showCode && !d.showPreview, "S5 shows code only");
});

ok("toggleFullscreen on the same pane exits", () => {
  let p = PanelState.fullscreen(expandedWithBoth(), "code");
  p = PanelState.toggleFullscreen(p, "code");
  assert.strictEqual(PanelState.describe(p).state, "S2");
});

ok("Escape exits OS fullscreen first", () => {
  let p = PanelState.fullscreen(PanelState.maximizeCode(expandedWithBoth()), "code");
  const first = PanelState.escape(p);
  assert.strictEqual(first.handled, true);
  assert.strictEqual(PanelState.describe(first.panel).state, "S3");
  const second = PanelState.escape(first.panel);
  assert.strictEqual(second.handled, true);
  assert.strictEqual(PanelState.describe(second.panel).state, "S2");
  const third = PanelState.escape(second.panel);
  assert.strictEqual(third.handled, false);
});

ok("collapse from S4 -> S1 and expand restores the split", () => {
  let p = expandedWithBoth();
  p = PanelState.collapse(p);
  assert.strictEqual(PanelState.describe(p).state, "S1");
  p = PanelState.expand(p);
  const d = PanelState.describe(p);
  assert.strictEqual(d.state, "S2");
  assert.ok(d.hasCode && d.hasPreview);
});

ok("X reset clears file, editor, URL and pane state, lands on S1", () => {
  let p = expandedWithBoth();
  p.editor = { scrollTop: 120 };
  p = PanelState.reset(p);
  const d = PanelState.describe(p);
  assert.strictEqual(d.state, "S1");
  assert.strictEqual(d.codeFile, null);
  assert.strictEqual(d.previewUrl, null);
  assert.strictEqual(d.prev, null);
  assert.strictEqual(p.editor, null);
});

ok("normalize keeps valid fields, drops junk, clamps overlay prev", () => {
  const p = PanelState.normalize({ state: "S4", pane: "bogus", code_file: "x", junk: 1 });
  assert.strictEqual(p.state, "S4");
  assert.strictEqual(p.code_file, "x");
  assert.strictEqual(p.junk, undefined);
  const s5 = PanelState.normalize({ state: "S5" });
  assert.strictEqual(s5.prev, "S2"); // overlay without a valid prev falls back to S2
});

ok("closing code while preview-maximized keeps Preview maximized", () => {
  let p = PanelState.maximizePreview(expandedWithBoth());
  p = PanelState.closeCode(p);
  const d = PanelState.describe(p);
  assert.strictEqual(d.state, "S4");
  assert.ok(d.hasPreview && !d.hasCode);
  assertNeverBothHidden(p, "close code in S4");
});

ok("closing code while code-maximized falls back to the split", () => {
  let p = PanelState.maximizeCode(expandedWithBoth());
  p = PanelState.closeCode(p);
  assert.strictEqual(PanelState.describe(p).state, "S2");
});

console.log("\n" + passed + " checks passed");
