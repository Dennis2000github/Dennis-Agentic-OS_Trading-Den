/*
 * Node test for js/studioWorkspaceState.js.
 * Run with:  node tests/studioWorkspaceState.test.js
 *
 * Pure workspace routing helpers: hash parsing/building and label maps used
 * by the shared Studio workspace shell.
 */
const assert = require("assert");
const WSS = require("../js/studioWorkspaceState.js");

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

ok("parses a plain studio hash with no workspace", () => {
  assert.deepStrictEqual(WSS.parseHash("#studio"), {
    view: "studio", workspace: null, section: null, explicitWorkspace: false
  });
});

ok("parses ads deep links", () => {
  const route = WSS.parseHash("#studio/ads/history");
  assert.strictEqual(route.workspace, "ads");
  assert.strictEqual(route.section, "history");
  assert.strictEqual(route.explicitWorkspace, true);
});

ok("parses youtube deep links", () => {
  const route = WSS.parseHash("#studio/youtube/overview");
  assert.strictEqual(route.workspace, "youtube");
  assert.strictEqual(route.section, "overview");
});

ok("rejects unknown workspaces as implicit", () => {
  assert.strictEqual(WSS.parseHash("#studio/unknown/overview").explicitWorkspace, false);
  assert.strictEqual(WSS.parseHash("#studio/unknown/overview").workspace, null);
});

ok("falls back to the default section for unknown youtube sections", () => {
  assert.strictEqual(WSS.parseHash("#studio/youtube/not-a-section").section, null);
});

ok("ignores hashes outside the studio", () => {
  assert.strictEqual(WSS.parseHash("#trading-den"), null);
  assert.strictEqual(WSS.parseHash(""), null);
  assert.strictEqual(WSS.parseHash(null), null);
});

ok("builds canonical hashes", () => {
  assert.strictEqual(WSS.buildHash("youtube", "overview"), "#studio/youtube/overview");
  assert.strictEqual(WSS.buildHash("ads", "history"), "#studio/ads/history");
  assert.strictEqual(WSS.buildHash("bogus"), "#studio/ads/fashion");
});

ok("normalizes workspaces and sections", () => {
  assert.strictEqual(WSS.normalizeWorkspace("YOUTUBE"), "youtube");
  assert.strictEqual(WSS.normalizeWorkspace("nope", "ads"), "ads");
  assert.strictEqual(WSS.normalizeSection("youtube", "SCRIPTS"), "scripts");
  assert.strictEqual(WSS.normalizeSection("youtube", "bogus"), "overview");
  assert.strictEqual(WSS.normalizeSection("ads", "bogus"), "fashion");
});

ok("ads and youtube keep separate section lists", () => {
  assert.ok(WSS.isKnownSection("ads", "fashion"));
  assert.ok(!WSS.isKnownSection("youtube", "fashion"));
  assert.ok(WSS.isKnownSection("youtube", "animation"));
  assert.ok(!WSS.isKnownSection("ads", "animation"));
});

ok("existing ads tab keys are preserved", () => {
  for (const tab of ["fashion", "assets", "library", "playlists", "preset", "history", "setup"]) {
    assert.ok(WSS.ADS_SECTIONS.indexOf(tab) !== -1, tab);
  }
});

ok("labels cover project statuses and stages", () => {
  assert.strictEqual(WSS.projectStatusLabel("in_progress"), "In Progress");
  assert.strictEqual(WSS.projectStatusLabel("missing"), "Draft");
  assert.strictEqual(WSS.stageLabel("assembly"), "Assembly");
  assert.strictEqual(WSS.stageLabel("missing"), "Research");
});

console.log(`\n${passed} checks passed`);
