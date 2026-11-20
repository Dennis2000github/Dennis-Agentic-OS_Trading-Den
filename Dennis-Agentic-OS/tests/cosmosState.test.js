/*
 * Node test for js/cosmosState.js.
 * Run with:  node tests/cosmosState.test.js
 *
 * Proves per-agent / per-division isolation, configuration merging, deep
 * links, recommendation action gating and formatting helpers.
 */
const assert = require("assert");
const Cosmos = require("../js/cosmosState.js");

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

ok("normalizes unknown agents and divisions", () => {
  assert.strictEqual(Cosmos.normalizeAgent("POLARIS"), "polaris");
  assert.strictEqual(Cosmos.normalizeAgent("nope"), "andromeda");
  assert.strictEqual(Cosmos.normalizeAgent("", "helios"), "helios");
  assert.strictEqual(Cosmos.normalizeDivision("YouTube"), "youtube");
  assert.strictEqual(Cosmos.normalizeDivision("email"), "ads");
});

ok("starts with five canonical agents and two divisions each", () => {
  const state = Cosmos.emptyState();
  assert.deepStrictEqual(Cosmos.AGENT_ORDER, ["andromeda", "polaris", "pleiades", "quasar", "helios"]);
  assert.strictEqual(state.activeAgent, "andromeda");
  assert.strictEqual(state.byAgent.andromeda.ads.task, "");
  assert.strictEqual(state.byAgent.andromeda.youtube.task, "");
  assert.strictEqual(Cosmos.divisionTitle("ads"), "Ads Intelligence");
  assert.strictEqual(Cosmos.divisionTitle("youtube"), "YouTube Intelligence");
});

ok("composer state is isolated per agent and per division", () => {
  const state = Cosmos.emptyState();
  Cosmos.setTask(state, "andromeda", "ads", "Find dresses");
  Cosmos.setTask(state, "andromeda", "youtube", "Find tax topics");
  Cosmos.setTask(state, "polaris", "ads", "Evaluate dresses");
  assert.strictEqual(Cosmos.divisionState(state, "andromeda", "ads").task, "Find dresses");
  assert.strictEqual(Cosmos.divisionState(state, "andromeda", "youtube").task, "Find tax topics");
  assert.strictEqual(Cosmos.divisionState(state, "polaris", "ads").task, "Evaluate dresses");
  assert.strictEqual(Cosmos.divisionState(state, "polaris", "youtube").task, "");
  Cosmos.setNotes(state, "andromeda", "ads", "owner notes");
  assert.strictEqual(Cosmos.divisionState(state, "andromeda", "ads").notes, "owner notes");
  assert.strictEqual(Cosmos.divisionState(state, "andromeda", "youtube").notes, "");
});

ok("result data replaces only the addressed division", () => {
  const state = Cosmos.emptyState();
  Cosmos.setDivisionData(state, "andromeda", "ads", {
    recommendations: [{ id: "rec_1", division: "ads" }],
    executions: [{ id: "exe_1", division: "ads" }]
  });
  Cosmos.setDivisionData(state, "andromeda", "youtube", {
    recommendations: [{ id: "rec_2", division: "youtube" }],
    executions: []
  });
  assert.strictEqual(Cosmos.divisionState(state, "andromeda", "ads").recommendations.length, 1);
  assert.strictEqual(Cosmos.divisionState(state, "andromeda", "youtube").recommendations.length, 1);
  assert.strictEqual(Cosmos.divisionState(state, "andromeda", "ads").recommendations[0].id, "rec_1");
  assert.strictEqual(Cosmos.divisionState(state, "andromeda", "youtube").executions.length, 0);
  assert.strictEqual(Cosmos.divisionState(state, "andromeda", "ads").loaded, true);
});

ok("merges overview without losing agent-local composer state", () => {
  const state = Cosmos.emptyState();
  Cosmos.setTask(state, "pleiades", "ads", "Concept for a dress");
  Cosmos.mergeOverview(state, {
    agents: [{ id: "andromeda", name: "ANDROMEDA" }, { id: "polaris", name: "POLARIS" }],
    config: {
      ads_templates: [{ id: "womens-apparel", name: "Women's Apparel" }],
      youtube_templates: [{ id: "educational-video", name: "Educational Video" }],
      active_ads_template_id: "womens-apparel",
      active_youtube_template_id: "educational-video",
      active_youtube_channel_id: "ytch_1"
    },
    channels: [{ id: "ytch_1", name: "Test Channel" }],
    llm: { configured: true, provider: "opencode", model: "glm" },
    counts: { ads_backlog: { total: 2 } }
  });
  assert.strictEqual(state.initialized, true);
  assert.strictEqual(state.config.adsTemplates.length, 1);
  assert.strictEqual(state.config.activeYoutubeChannelId, "ytch_1");
  assert.strictEqual(state.llm.configured, true);
  assert.strictEqual(Cosmos.divisionState(state, "pleiades", "ads").task, "Concept for a dress");
  assert.strictEqual(state.byAgent.pleiades.detail, null);
  assert.strictEqual(Cosmos.agentName(state, "polaris"), "POLARIS");
});

ok("configuration helpers pick the right template list", () => {
  const state = Cosmos.emptyState();
  Cosmos.mergeOverview(state, {
    agents: [],
    config: {
      ads_templates: [{ id: "home-living", name: "Home & Living" }],
      youtube_templates: [{ id: "faceless-youtube", name: "Faceless YouTube" }],
      active_ads_template_id: "home-living",
      active_youtube_template_id: "faceless-youtube"
    }
  });
  assert.strictEqual(Cosmos.activeTemplateId(state, "ads"), "home-living");
  assert.strictEqual(Cosmos.activeTemplateId(state, "youtube"), "faceless-youtube");
  assert.strictEqual(Cosmos.templatesFor(state, "youtube")[0].id, "faceless-youtube");
  assert.strictEqual(Cosmos.templateById(state, "ads", "home-living").name, "Home & Living");
  assert.strictEqual(Cosmos.templateById(state, "ads", "missing"), null);
  Cosmos.applyConfig(state, { active_ads_template_id: "home-living" });
  assert.strictEqual(Cosmos.templateById(state, "ads", "home-living").id, "home-living");
});

ok("recommendation actions respect the lifecycle", () => {
  assert.deepStrictEqual(Cosmos.recommendationActions("proposed"), {
    draft: true, reject: true, refine: true, validateAffiliate: true
  });
  assert.strictEqual(Cosmos.recommendationActions("drafted").draft, false);
  assert.strictEqual(Cosmos.recommendationActions("drafted").reject, false);
  assert.strictEqual(Cosmos.recommendationActions("drafted").refine, false);
  assert.strictEqual(Cosmos.recommendationActions("rejected").draft, false);
  assert.strictEqual(Cosmos.recommendationActions("rejected").validateAffiliate, false);
  assert.strictEqual(Cosmos.recommendationActions("refined").reject, true);
});

ok("filters and counts recommendations by division", () => {
  const rows = [
    { division: "ads" }, { division: "youtube" }, { division: "ads" }, null
  ];
  assert.strictEqual(Cosmos.filterDivision(rows, "ads").length, 2);
  assert.strictEqual(Cosmos.filterDivision(rows, "youtube").length, 1);
  assert.deepStrictEqual(Cosmos.countsByDivision(rows), { ads: 2, youtube: 1 });
});

ok("mergeAgentDetail stores activity for that agent only", () => {
  const state = Cosmos.emptyState();
  Cosmos.mergeAgentDetail(state, {
    id: "quasar",
    name: "QUASAR",
    activity: { executions: [{ id: "exe_1", division: "ads" }] },
    recommendation_counts: { proposed: 2 }
  });
  assert.strictEqual(Cosmos.agentById(state, "quasar").name, "QUASAR");
  assert.strictEqual(state.byAgent.quasar.activity.length, 1);
  assert.strictEqual(state.byAgent.quasar.recommendationCounts.proposed, 2);
  assert.strictEqual(state.byAgent.andromeda.activity.length, 0);
});

ok("busy flags are per agent and per division", () => {
  const state = Cosmos.emptyState();
  Cosmos.setBusy(state, "helios", "ads", true);
  assert.deepStrictEqual(Cosmos.pendingByDivision(state, "helios"), { ads: true, youtube: false });
  assert.deepStrictEqual(Cosmos.pendingByDivision(state, "andromeda"), { ads: false, youtube: false });
});

ok("deep links round-trip and reject non-cosmos hashes", () => {
  assert.deepStrictEqual(Cosmos.parseHash("#cosmos/pleiades"), { view: "cosmos", agent: "pleiades" });
  assert.deepStrictEqual(Cosmos.parseHash("#cosmos"), { view: "cosmos", agent: null });
  assert.deepStrictEqual(Cosmos.parseHash("#studio/ads/fashion"), { view: null, agent: null });
  assert.strictEqual(Cosmos.hashForAgent("quasar"), "#cosmos/quasar");
});

ok("GENESIS is a first-class view route, not a sixth agent", () => {
  assert.strictEqual(Cosmos.GENESIS_ID, "genesis");
  assert.strictEqual(Cosmos.AGENT_ORDER.length, 5);
  assert.deepStrictEqual(Cosmos.parseHash("#cosmos/genesis"), { view: "cosmos", agent: "genesis" });
  assert.strictEqual(Cosmos.hashForAgent("genesis"), "#cosmos/genesis");
  assert.strictEqual(Cosmos.hashForAgent("GENESIS"), "#cosmos/genesis");
});

ok("the GENESIS view survives overview refreshes", () => {
  const state = Cosmos.emptyState();
  Cosmos.setActiveAgent(state, "genesis");
  assert.strictEqual(state.activeAgent, "genesis");
  Cosmos.mergeOverview(state, { agents: [], counts: {} });
  assert.strictEqual(state.activeAgent, "genesis", "mergeOverview must not reset GENESIS to andromeda");
  assert.ok(!state.byAgent.genesis, "GENESIS never gets an agent dashboard state");
  // Unknown values keep the current selection (pre-existing normalizeAgent
  // semantics); mergeOverview itself resets a truly unknown stored agent.
  Cosmos.setActiveAgent(state, "bogus");
  assert.strictEqual(state.activeAgent, "genesis");
  state.activeAgent = "bogus";
  Cosmos.mergeOverview(state, { agents: [], counts: {} });
  assert.strictEqual(state.activeAgent, "andromeda");
});

ok("formatting and status helpers stay deterministic", () => {
  assert.strictEqual(Cosmos.formatDuration(850), "850 ms");
  assert.strictEqual(Cosmos.formatDuration(4200), "4.2 s");
  assert.strictEqual(Cosmos.formatDuration(null), "—");
  assert.strictEqual(Cosmos.formatWhen(null), "—");
  assert.strictEqual(Cosmos.statusTone("succeeded"), "ok");
  assert.strictEqual(Cosmos.statusTone("failed"), "danger");
  assert.strictEqual(Cosmos.statusTone("proposed"), "info");
  assert.strictEqual(Cosmos.evidenceLabel("third_party_claim"), "Third-party claim");
  assert.strictEqual(Cosmos.evidenceLabel("hypothesis"), "AI hypothesis");
});

ok("GENESIS production statuses get honest tones", () => {
  assert.strictEqual(Cosmos.statusToneExtended("complete"), "ok");
  assert.strictEqual(Cosmos.statusToneExtended("succeeded"), "ok");
  assert.strictEqual(Cosmos.statusToneExtended("awaiting_approval"), "warn");
  assert.strictEqual(Cosmos.statusToneExtended("failed"), "danger");
  assert.strictEqual(Cosmos.statusToneExtended("assembling"), "info");
  assert.strictEqual(Cosmos.statusToneExtended("queued"), "muted");
  assert.strictEqual(Cosmos.statusToneExtended("published"), "ok");
});

console.log("\n" + passed + " COSMOS state tests passed.");
