/*
 * Node test for js/studioWorkspaceState.js.
 * Run with:  node tests/studioWorkspaceState.test.js
 *
 * Pure workspace routing helpers: hash parsing/building and label maps used
 * by the shared Studio workspace shell. After the COSMOS consolidation the
 * Ads Product Backlog and the YouTube Topic Backlog / Publishing /
 * YouTube Intelligence sections are removed from the Studio workspaces and
 * their deep links redirect into COSMOS.
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
  const route = WSS.parseHash("#studio/youtube/create");
  assert.strictEqual(route.workspace, "youtube");
  assert.strictEqual(route.section, "create");
});

ok("legacy youtube section ids redirect to their new homes", () => {
  const legacy = {
    overview: "create",
    "channel-manager": "settings",
    scripts: "projects",
    storyboard: "projects",
    assets: "projects",
    voice: "projects",
    animation: "projects",
    thumbnails: "projects"
  };
  for (const [from, to] of Object.entries(legacy)) {
    assert.strictEqual(WSS.normalizeSection("youtube", from), to, from);
    assert.ok(WSS.isKnownSection("youtube", from), from + " should resolve");
  }
});

ok("the Ads Product Backlog deep link redirects into COSMOS", () => {
  const route = WSS.parseHash("#studio/ads/backlog");
  assert.strictEqual(route.view, "cosmos");
  assert.strictEqual(route.target, "andromeda");
  assert.ok(route.note && route.note.length > 0);
});

ok("the YouTube Topic Backlog deep link redirects into COSMOS", () => {
  const route = WSS.parseHash("#studio/youtube/backlog");
  assert.strictEqual(route.view, "cosmos");
  assert.strictEqual(route.target, "andromeda");
});

ok("legacy research/topics deep links redirect to QUASAR or the backlog", () => {
  assert.strictEqual(WSS.parseHash("#studio/youtube/research").target, "quasar");
  assert.strictEqual(WSS.parseHash("#studio/youtube/topics").target, "andromeda");
});

ok("the YouTube Publishing deep link redirects into GENESIS", () => {
  const route = WSS.parseHash("#studio/youtube/publishing");
  assert.strictEqual(route.view, "cosmos");
  assert.strictEqual(route.target, "genesis");
});

ok("the calendar deep link follows publishing into GENESIS", () => {
  assert.strictEqual(WSS.parseHash("#studio/youtube/calendar").target, "genesis");
});

ok("the YouTube Intelligence deep link redirects to QUASAR", () => {
  const route = WSS.parseHash("#studio/youtube/intelligence");
  assert.strictEqual(route.view, "cosmos");
  assert.strictEqual(route.target, "quasar");
});

ok("removed sections are no longer part of the workspace section lists", () => {
  assert.ok(WSS.ADS_SECTIONS.indexOf("backlog") === -1, "ads backlog removed");
  for (const section of ["backlog", "publishing", "intelligence"]) {
    assert.ok(WSS.YOUTUBE_SECTIONS.indexOf(section) === -1, section + " removed");
  }
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
  assert.strictEqual(WSS.buildHash("youtube", "create"), "#studio/youtube/create");
  assert.strictEqual(WSS.buildHash("youtube", "overview"), "#studio/youtube/create");
  assert.strictEqual(WSS.buildHash("ads", "history"), "#studio/ads/history");
  assert.strictEqual(WSS.buildHash("bogus"), "#studio/ads/fashion");
});

ok("normalizes workspaces and sections", () => {
  assert.strictEqual(WSS.normalizeWorkspace("YOUTUBE"), "youtube");
  assert.strictEqual(WSS.normalizeWorkspace("nope", "ads"), "ads");
  assert.strictEqual(WSS.normalizeSection("youtube", "overview"), "create");
  assert.strictEqual(WSS.normalizeSection("youtube", "bogus"), "create");
  assert.strictEqual(WSS.normalizeSection("ads", "bogus"), "fashion");
});

ok("the retained YouTube sections exist", () => {
  for (const section of ["create", "templates", "projects", "analytics", "settings"]) {
    assert.ok(WSS.YOUTUBE_SECTIONS.indexOf(section) !== -1, section);
  }
  assert.strictEqual(WSS.defaultSection("youtube"), "create");
});

ok("project subtabs normalize", () => {
  assert.strictEqual(WSS.normalizeProjectTab("scripts"), "scripts");
  assert.strictEqual(WSS.normalizeProjectTab("bogus"), "projects");
});

ok("ads and youtube keep separate section lists", () => {
  assert.ok(WSS.isKnownSection("ads", "fashion"));
  assert.ok(!WSS.isKnownSection("youtube", "fashion"));
  assert.ok(WSS.isKnownSection("youtube", "analytics"));
  assert.ok(!WSS.isKnownSection("ads", "intelligence"));
  assert.ok(!WSS.isKnownSection("youtube", "intelligence"));
});

ok("existing ads tab keys are preserved and the five-step flow stays default", () => {
  for (const tab of ["fashion", "assets", "library", "playlists", "preset", "history", "setup"]) {
    assert.ok(WSS.ADS_SECTIONS.indexOf(tab) !== -1, tab);
  }
  assert.strictEqual(WSS.defaultSection("ads"), "fashion");
});

ok("labels cover project statuses and stages", () => {
  assert.strictEqual(WSS.projectStatusLabel("in_progress"), "In Progress");
  assert.strictEqual(WSS.projectStatusLabel("missing"), "Draft");
  assert.strictEqual(WSS.stageLabel("assembly"), "Assembly");
  assert.strictEqual(WSS.stageLabel("missing"), "Research");
});

console.log(`\n${passed} checks passed`);
