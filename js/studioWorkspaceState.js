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
  var ADS_SECTIONS = ["fashion", "assets", "library", "playlists", "preset", "history", "setup"];

  var YOUTUBE_SECTIONS = [
    "overview",
    "projects",
    "channel-manager",
    "research",
    "topics",
    "scripts",
    "storyboard",
    "assets",
    "voice",
    "animation",
    "thumbnails",
    "publishing",
    "analytics",
    "calendar"
  ];

  var DEFAULTS = {
    ads: "fashion",
    youtube: "overview"
  };

  var SECTION_LABELS = {
    overview: "Overview",
    projects: "Projects",
    "channel-manager": "Channel Manager",
    research: "Niche Research",
    topics: "Topic Studio",
    scripts: "Script Studio",
    storyboard: "Storyboard Studio",
    assets: "Asset Studio",
    voice: "Voice Studio",
    animation: "Animation Studio",
    thumbnails: "Thumbnail Studio",
    publishing: "Publishing Manager",
    analytics: "Analytics",
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
    if (sectionsFor(workspace).indexOf(text) !== -1) return text;
    return DEFAULTS[workspace] || DEFAULTS.ads;
  }

  function isKnownSection(workspace, value) {
    return sectionsFor(workspace).indexOf(String(value || "").trim().toLowerCase()) !== -1;
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
    normalizeWorkspace: normalizeWorkspace,
    sectionsFor: sectionsFor,
    normalizeSection: normalizeSection,
    isKnownSection: isKnownSection,
    defaultSection: defaultSection,
    parseHash: parseHash,
    buildHash: buildHash,
    sectionLabel: sectionLabel,
    projectStatusLabel: projectStatusLabel,
    stageLabel: stageLabel
  };
});
