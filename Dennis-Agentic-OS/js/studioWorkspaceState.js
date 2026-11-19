// Studio workspace routing helpers. Pure, dependency-free and node-testable.
// Shared by js/studioWorkspaces.js and tests/studioWorkspaceState.test.js.
(function (root, factory) {
  var api = factory();
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  if (root) root.StudioWorkspaceState = api;
})(typeof window !== "undefined" ? window : null, function () {
  "use strict";

  var WORKSPACES = ["ads", "youtube"];

  // Existing Studio tabs. These keys must never change: the Ads workspace
  // relies on them.
  // Create and Product Backlog are additive; the five-step Generate workflow
  // remains the default Ads landing section.
  var ADS_SECTIONS = ["fashion", "create", "backlog", "assets", "library", "playlists", "preset", "history", "setup"];

  // YouTube Studio sections (master spec v1.0). The default landing page is
  // Create. Settings holds channel connection, defaults and providers.
  var YOUTUBE_SECTIONS = [
    "create",
    "templates",
    "projects",
    "backlog",
    "publishing",
    "analytics",
    "intelligence",
    "settings"
  ];

  // Pre-restructure deep links keep working: legacy section ids map onto the
  // closest new home for their content.
  var LEGACY_SECTIONS = {
    overview: "create",
    "channel-manager": "settings",
    research: "backlog",
    topics: "backlog",
    scripts: "projects",
    storyboard: "projects",
    assets: "projects",
    voice: "projects",
    animation: "projects",
    thumbnails: "projects",
    calendar: "publishing"
  };

  var DEFAULTS = {
    ads: "fashion",
    youtube: "create"
  };

  // Backlog and Projects expose secondary tabs once inside the section.
  var BACKLOG_TABS = ["topics", "research"];
  var PROJECT_TABS = ["projects", "scripts"];

  var SECTION_LABELS = {
    create: "Create",
    templates: "Templates",
    projects: "Projects",
    backlog: "Topic Backlog",
    publishing: "Publishing",
    analytics: "Analytics",
    intelligence: "YouTube Intelligence",
    settings: "Settings",
    overview: "Overview",
    "channel-manager": "Channel Manager",
    research: "Niche Research",
    topics: "Topic Studio",
    scripts: "Script Studio",
    storyboard: "Storyboard Studio",
    assets: "Asset Studio",
    voice: "Voice Studio",
    animation: "Animation Studio",
    thumbnails: "Thumbnail Studio",
    calendar: "Publishing Calendar",
    fashion: "Generate",
    library: "Library",
    playlists: "Playlists",
    preset: "Preset Video",
    history: "History",
    setup: "Provider Setup"
  };

  function normalizeWorkspace(value, fallback) {
    var text = String(value || "").trim().toLowerCase();
    if (WORKSPACES.indexOf(text) !== -1) return text;
    return fallback == null ? "ads" : fallback;
  }

  function sectionsFor(workspace) {
    return workspace === "youtube" ? YOUTUBE_SECTIONS.slice() : ADS_SECTIONS.slice();
  }

  function normalizeSection(workspace, value) {
    var text = String(value || "").trim().toLowerCase();
    if (workspace === "youtube" && LEGACY_SECTIONS[text]) text = LEGACY_SECTIONS[text];
    if (sectionsFor(workspace).indexOf(text) !== -1) return text;
    return DEFAULTS[workspace] || DEFAULTS.ads;
  }

  function isKnownSection(workspace, value) {
    var text = String(value || "").trim().toLowerCase();
    if (workspace === "youtube" && LEGACY_SECTIONS[text]) text = LEGACY_SECTIONS[text];
    return sectionsFor(workspace).indexOf(text) !== -1;
  }

  function normalizeBacklogTab(value) {
    var text = String(value || "").trim().toLowerCase();
    return BACKLOG_TABS.indexOf(text) !== -1 ? text : "topics";
  }

  function normalizeProjectTab(value) {
    var text = String(value || "").trim().toLowerCase();
    return PROJECT_TABS.indexOf(text) !== -1 ? text : "projects";
  }

  function defaultSection(workspace) {
    return DEFAULTS[workspace] || DEFAULTS.ads;
  }

  function parseHash(hash) {
    var raw = String(hash == null ? "" : hash).trim();
    if (raw.charAt(0) === "#") raw = raw.slice(1);
    if (!raw) return null;
    var parts = raw.split("/").filter(Boolean);
    if (!parts.length || parts[0].toLowerCase() !== "studio") return null;
    var workspace = parts.length > 1 ? normalizeWorkspace(parts[1], "") : null;
    if (!workspace) {
      return { view: "studio", workspace: null, section: null, explicitWorkspace: false };
    }
    var section = parts.length > 2 ? String(parts[2]).trim().toLowerCase() : "";
    if (section && !isKnownSection(workspace, section)) section = "";
    return {
      view: "studio",
      workspace: workspace,
      section: section || null,
      explicitWorkspace: true
    };
  }

  function buildHash(workspace, section) {
    var ws = normalizeWorkspace(workspace);
    var sec = normalizeSection(ws, section);
    return "#studio/" + ws + "/" + sec;
  }

  function sectionLabel(section) {
    return SECTION_LABELS[section] || String(section || "");
  }

  function projectStatusLabel(status) {
    var labels = {
      draft: "Draft",
      in_progress: "In Progress",
      review: "Awaiting Approval",
      approved: "Approved",
      scheduled: "Scheduled",
      published: "Published",
      archived: "Archived"
    };
    return labels[status] || "Draft";
  }

  function stageLabel(stage) {
    var labels = {
      research: "Research",
      script: "Script",
      storyboard: "Storyboard",
      assets: "Assets",
      voice: "Voice",
      animation: "Animation",
      assembly: "Assembly",
      review: "Review",
      publishing: "Publishing",
      analytics: "Analytics"
    };
    return labels[stage] || "Research";
  }

  return {
    WORKSPACES: WORKSPACES,
    ADS_SECTIONS: ADS_SECTIONS,
    YOUTUBE_SECTIONS: YOUTUBE_SECTIONS,
    LEGACY_SECTIONS: LEGACY_SECTIONS,
    BACKLOG_TABS: BACKLOG_TABS,
    PROJECT_TABS: PROJECT_TABS,
    normalizeWorkspace: normalizeWorkspace,
    sectionsFor: sectionsFor,
    normalizeSection: normalizeSection,
    isKnownSection: isKnownSection,
    normalizeBacklogTab: normalizeBacklogTab,
    normalizeProjectTab: normalizeProjectTab,
    defaultSection: defaultSection,
    parseHash: parseHash,
    buildHash: buildHash,
    sectionLabel: sectionLabel,
    projectStatusLabel: projectStatusLabel,
    stageLabel: stageLabel
  };
});
