/* =====================================================================
   COSMOS · pure state helpers for the five agent dashboards.

   Every intelligence agent owns its own independent state: two permanent
   divisions (Ads Intelligence on the left, YouTube Intelligence on the
   right), a composer per division, its own loading flags and its own loaded
   history. This module is dependency-free and node-testable; the DOM lives
   in js/cosmosDashboard.js.
   ===================================================================== */
(function (root, factory) {
  var api = factory();
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  if (root) root.CosmosState = api;
})(typeof window !== "undefined" ? window : null, function () {
  "use strict";

  var AGENT_ORDER = ["andromeda", "polaris", "pleiades", "quasar", "helios"];
  var DIVISIONS = ["ads", "youtube"];

  var DIVISION_TITLES = {
    ads: "Ads Intelligence",
    youtube: "YouTube Intelligence"
  };

  function normalizeAgent(value, fallback) {
    var text = String(value || "").trim().toLowerCase();
    if (AGENT_ORDER.indexOf(text) !== -1) return text;
    return fallback == null ? AGENT_ORDER[0] : fallback;
  }

  function normalizeDivision(value, fallback) {
    var text = String(value || "").trim().toLowerCase();
    if (DIVISIONS.indexOf(text) !== -1) return text;
    return fallback == null ? "ads" : fallback;
  }

  function divisionTitle(division) {
    return DIVISION_TITLES[normalizeDivision(division)] || DIVISION_TITLES.ads;
  }

  function emptyDivision() {
    return {
      task: "",
      notes: "",
      templateId: "",
      busy: false,
      error: null,
      loaded: false,
      recommendations: [],
      executions: []
    };
  }

  function emptyAgent(id) {
    return {
      id: normalizeAgent(id),
      detail: null,
      ads: emptyDivision(),
      youtube: emptyDivision(),
      activity: [],
      recommendationCounts: {}
    };
  }

  function emptyState() {
    var state = {
      agents: [],
      config: {
        adsTemplates: [],
        youtubeTemplates: [],
        channels: [],
        activeAdsTemplateId: null,
        activeYoutubeTemplateId: null,
        activeChannelId: null
      },
      llm: { configured: false, provider: null, model: null },
      counts: {},
      activeAgent: AGENT_ORDER[0],
      byAgent: {},
      initialized: false,
      loading: false,
      error: null,
      notice: null
    };
    AGENT_ORDER.forEach(function (id) { state.byAgent[id] = emptyAgent(id); });
    return state;
  }

  function agentState(state, agentId) {
    var id = normalizeAgent(agentId, state.activeAgent || AGENT_ORDER[0]);
    if (!state.byAgent[id]) state.byAgent[id] = emptyAgent(id);
    return state.byAgent[id];
  }

  function divisionState(state, agentId, division) {
    return agentState(state, agentId)[normalizeDivision(division)];
  }

  function setActiveAgent(state, agentId) {
    state.activeAgent = normalizeAgent(agentId, state.activeAgent);
    agentState(state, state.activeAgent);
    return state.activeAgent;
  }

  function setTask(state, agentId, division, value) {
    divisionState(state, agentId, division).task = String(value == null ? "" : value);
  }

  function setNotes(state, agentId, division, value) {
    divisionState(state, agentId, division).notes = String(value == null ? "" : value);
  }

  function setTemplateChoice(state, agentId, division, templateId) {
    divisionState(state, agentId, division).templateId = String(templateId || "");
  }

  function setBusy(state, agentId, division, busy) {
    divisionState(state, agentId, division).busy = !!busy;
  }

  function setDivisionError(state, agentId, division, message) {
    divisionState(state, agentId, division).error = message || null;
  }

  function setDivisionData(state, agentId, division, data) {
    var target = divisionState(state, agentId, division);
    var payload = data || {};
    target.recommendations = Array.isArray(payload.recommendations) ? payload.recommendations.slice() : [];
    target.executions = Array.isArray(payload.executions) ? payload.executions.slice() : [];
    target.loaded = true;
    return target;
  }

  function mergeOverview(state, overview) {
    var data = overview || {};
    state.agents = Array.isArray(data.agents) ? data.agents.slice() : [];
    if (!state.agents.length) state.agents = AGENT_ORDER.map(function (id) { return { id: id, name: id.toUpperCase() }; });
    var config = data.config || {};
    state.config.adsTemplates = Array.isArray(config.ads_templates) ? config.ads_templates.slice() : [];
    state.config.youtubeTemplates = Array.isArray(config.youtube_templates) ? config.youtube_templates.slice() : [];
    state.config.activeAdsTemplateId = config.active_ads_template_id || state.config.activeAdsTemplateId;
    state.config.activeYoutubeTemplateId = config.active_youtube_template_id || state.config.activeYoutubeTemplateId;
    state.config.activeYoutubeChannelId = config.active_youtube_channel_id || state.config.activeYoutubeChannelId;
    state.config.channels = Array.isArray(data.channels) ? data.channels.slice() : [];
    state.llm = data.llm || state.llm;
    state.counts = data.counts || state.counts;
    if (AGENT_ORDER.indexOf(state.activeAgent) === -1) state.activeAgent = AGENT_ORDER[0];
    AGENT_ORDER.concat(state.agents.map(function (agent) { return agent.id; })).forEach(function (id) {
      if (id) agentState(state, id);
    });
    state.agents.forEach(function (agent) {
      var local = agentState(state, agent.id);
      local.detail = agent;
    });
    state.initialized = true;
    return state;
  }

  function applyConfig(state, config) {
    var data = config || {};
    if (data.ads_templates) state.config.adsTemplates = data.ads_templates.slice();
    if (data.youtube_templates) state.config.youtubeTemplates = data.youtube_templates.slice();
    if (data.active_ads_template_id) state.config.activeAdsTemplateId = data.active_ads_template_id;
    if (data.active_youtube_template_id) state.config.activeYoutubeTemplateId = data.active_youtube_template_id;
    if (data.active_youtube_channel_id !== undefined) state.config.activeYoutubeChannelId = data.active_youtube_channel_id;
    return state;
  }

  function templatesFor(state, division) {
    return normalizeDivision(division) === "youtube"
      ? state.config.youtubeTemplates
      : state.config.adsTemplates;
  }

  function activeTemplateId(state, division) {
    return normalizeDivision(division) === "youtube"
      ? state.config.activeYoutubeTemplateId
      : state.config.activeAdsTemplateId;
  }

  function templateById(state, division, templateId) {
    var wanted = String(templateId || "");
    var found = null;
    templatesFor(state, division).forEach(function (template) {
      if (template && template.id === wanted) found = template;
    });
    return found;
  }

  function agentById(state, agentId) {
    if (!state) return null;
    return agentState(state, agentId).detail || null;
  }

  function agentName(state, agentId) {
    var agent = agentById(state, agentId);
    return (agent && agent.name) || normalizeAgent(agentId).toUpperCase();
  }

  function recommendationActions(status) {
    var text = String(status || "proposed");
    return {
      draft: text === "proposed",
      reject: text === "proposed" || text === "refined",
      refine: text === "proposed",
      validateAffiliate: text !== "rejected"
    };
  }

  function filterDivision(items, division) {
    var wanted = normalizeDivision(division);
    return (Array.isArray(items) ? items : []).filter(function (item) {
      return !!item && normalizeDivision(item.division, "ads") === wanted;
    });
  }

  function countsByDivision(recommendations) {
    var result = { ads: 0, youtube: 0 };
    (Array.isArray(recommendations) ? recommendations : []).forEach(function (item) {
      if (!item) return;
      var division = normalizeDivision(item.division, "ads");
      result[division] += 1;
    });
    return result;
  }

  function mergeAgentDetail(state, detail) {
    if (!detail || !detail.id) return null;
    var local = agentState(state, detail.id);
    local.detail = detail;
    local.activity = Array.isArray(detail.activity && detail.activity.executions)
      ? detail.activity.executions.slice()
      : [];
    local.recommendationCounts = detail.recommendation_counts || {};
    return local;
  }

  function pendingByDivision(state, agentId) {
    var local = agentState(state, agentId);
    return {
      ads: !!local.ads.busy,
      youtube: !!local.youtube.busy
    };
  }

  function parseHash(hash) {
    var text = String(hash || "").replace(/^#/, "");
    var parts = text.split("/").filter(function (part) { return part; });
    if (parts[0] !== "cosmos") return { view: null, agent: null };
    return { view: "cosmos", agent: parts[1] ? normalizeAgent(parts[1]) : null };
  }

  function hashForAgent(agentId) {
    return "#cosmos/" + normalizeAgent(agentId);
  }

  function formatDuration(ms) {
    if (ms === null || ms === undefined || ms === "") return "—";
    var value = Number(ms);
    if (!isFinite(value) || value < 0) return "—";
    if (value < 1000) return Math.round(value) + " ms";
    if (value < 60000) return (value / 1000).toFixed(value < 10000 ? 1 : 0) + " s";
    return Math.round(value / 60000) + " min";
  }

  function formatWhen(iso) {
    if (!iso) return "—";
    var date = new Date(iso);
    if (isNaN(date.getTime())) return String(iso);
    try {
      return date.toLocaleString(undefined, {
        month: "short", day: "numeric", hour: "2-digit", minute: "2-digit"
      });
    } catch (err) {
      return String(iso);
    }
  }

  function statusTone(status) {
    var key = String(status || "").toLowerCase();
    if (key === "succeeded" || key === "drafted" || key === "approved" || key === "validated" || key === "published") return "ok";
    if (key === "running" || key === "proposed" || key === "requested") return "info";
    if (key === "failed" || key === "rejected") return "danger";
    if (key === "insufficient" || key === "refined" || key === "unvalidated") return "warn";
    return "muted";
  }

  function evidenceLabel(value) {
    var key = String(value || "hypothesis").toLowerCase();
    var labels = {
      verified: "Verified",
      third_party_claim: "Third-party claim",
      estimate: "Estimate",
      hypothesis: "AI hypothesis",
      sourced: "Sourced"
    };
    return labels[key] || key;
  }

  return {
    AGENT_ORDER: AGENT_ORDER,
    DIVISIONS: DIVISIONS,
    normalizeAgent: normalizeAgent,
    normalizeDivision: normalizeDivision,
    divisionTitle: divisionTitle,
    emptyState: emptyState,
    emptyDivision: emptyDivision,
    agentState: agentState,
    divisionState: divisionState,
    setActiveAgent: setActiveAgent,
    setTask: setTask,
    setNotes: setNotes,
    setTemplateChoice: setTemplateChoice,
    setBusy: setBusy,
    setDivisionError: setDivisionError,
    setDivisionData: setDivisionData,
    mergeOverview: mergeOverview,
    applyConfig: applyConfig,
    templatesFor: templatesFor,
    activeTemplateId: activeTemplateId,
    templateById: templateById,
    agentById: agentById,
    agentName: agentName,
    recommendationActions: recommendationActions,
    filterDivision: filterDivision,
    countsByDivision: countsByDivision,
    mergeAgentDetail: mergeAgentDetail,
    pendingByDivision: pendingByDivision,
    parseHash: parseHash,
    hashForAgent: hashForAgent,
    formatDuration: formatDuration,
    formatWhen: formatWhen,
    statusTone: statusTone,
    evidenceLabel: evidenceLabel
  };
});
