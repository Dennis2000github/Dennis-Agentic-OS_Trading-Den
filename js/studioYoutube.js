/* =====================================================================
   YOUTUBE STUDIO — master-spec workspace (Phase 2).

   Seven sections + Settings, per the YouTube Studio master specification:
     Create · Templates · Projects · Topic Backlog · Publishing ·
     Analytics · YouTube Intelligence · Settings.

   Real data only:
     - workspace descriptor / projects: /api/studio/workspaces[/youtube/...];
     - editorial backend: /api/studio/youtube/* (channels, research,
       competitors, topics, scripts, llm, features, overview);
     - the composer creates a backlog topic; it never fakes templates,
       packaging, publishing or analytics.

   Templates (Phase 3), packaging (Phase 6), production (Phase 8) and
   publishing/analytics (Phase 10, OAuth-gated) render honest placeholders.
   ===================================================================== */
(function () {
  "use strict";

  var WS = window.StudioWorkspaceState;
  if (!WS) return;

  var COMPOSER_KEY = "dennis.yt.composer";

  var S = {
    initialized: false,
    workspace: "youtube",
    section: "create",
    descriptor: null,
    counts: null,
    settings: null,
    projects: [],
    recentProjects: [],
    projectsLoaded: false,
    loading: false,
    query: "",
    status: "",
    includeArchived: false,
    lastError: null,
    /* editorial */
    overview: null,
    features: null,
    channels: [],
    activeChannel: null,
    selectedChannelId: null,
    channelPromptVersions: [],
    topics: [],
    topicCounts: {},
    topicStatuses: [],
    topicQuery: { q: "", status: "", pillar: "", priority: "", min_score: "" },
    validations: [],
    competitors: [],
    scripts: [],
    scriptQuery: { q: "", status: "" },
    currentScript: null,
    currentVersions: [],
    llm: null,
    backlogTab: "topics",
    projectTab: "projects"
  };

  function $(id) { return document.getElementById(id); }

  function esc(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function setText(id, text) {
    var el = $(id);
    if (el) el.textContent = text == null ? "" : String(text);
  }

  function toggle(el, visible, hiddenClass) {
    if (!el) return;
    el.classList.toggle(hiddenClass || "is-hidden", !visible);
  }

  function icons() {
    if (window.lucide && window.lucide.createIcons) {
      try { window.lucide.createIcons(); } catch (err) { /* icons are optional */ }
    }
  }

  function toast(message) {
    if (window.Studio && window.Studio.toast) return window.Studio.toast(message);
    return null;
  }

  function api(path, options) {
    var opts = options || {};
    var init = {
      method: opts.method || "GET",
      cache: "no-store",
      headers: new Headers(opts.headers || {})
    };
    if (opts.body !== undefined) {
      init.headers.set("Content-Type", "application/json");
      init.body = JSON.stringify(opts.body);
    }
    return fetch(path, init).then(function (response) {
      return response.json().catch(function () { return {}; }).then(function (data) {
        if (!response.ok) {
          var message = (data && (data.error || data.message)) || ("Request failed (" + response.status + ")");
          var error = new Error(message);
          error.status = response.status;
          error.code = data && data.code;
          throw error;
        }
        return data || {};
      });
    });
  }

  function yt(path, options) {
    return api("/api/studio/youtube" + path, options);
  }

  function dateLabel(value) {
    if (!value) return "—";
    var date = new Date(value);
    if (isNaN(date.getTime())) return "—";
    try {
      return date.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
    } catch (err) {
      return date.toISOString().slice(0, 10);
    }
  }

  function statusBadge(status) {
    var tone = {
      draft: "muted",
      in_progress: "info",
      review: "warn",
      approved: "ok",
      scheduled: "info",
      published: "ok",
      archived: "muted"
    }[status] || "muted";
    return '<span class="yt-badge yt-badge-' + tone + '">' + esc(WS.projectStatusLabel(status)) + "</span>";
  }

  function stageBadge(stage) {
    return '<span class="yt-badge yt-badge-stage">' + esc(WS.stageLabel(stage)) + "</span>";
  }

  function emptyState(title, note, action) {
    return '<div class="yt-empty">' +
      '<div class="yt-empty-icon"><i data-lucide="' + (action && action.icon ? action.icon : "inbox") + '"></i></div>' +
      "<h3>" + esc(title) + "</h3>" +
      "<p>" + esc(note) + "</p>" +
      (action ? '<button type="button" class="collart-btn" data-yt-empty-action="' + esc(action.id) + '">' + esc(action.label) + "</button>" : "") +
      "</div>";
  }

  function panelError(id, message) {
    var banner = $(id);
    if (!banner) return;
    var text = banner.querySelector("span");
    if (text) text.textContent = message || "";
    banner.classList.toggle("is-hidden", !message);
  }

  function setBusy(button, on, label) {
    if (!button) return;
    if (on) {
      button.dataset.label = button.textContent;
      button.disabled = true;
      if (label) button.textContent = label;
    } else {
      button.disabled = false;
      if (button.dataset.label) button.textContent = button.dataset.label;
    }
  }

  /* ------------------------------------------------------------------ */
  /* Data loaders                                                        */
  /* ------------------------------------------------------------------ */
  function loadDescriptor() {
    return api("/api/studio/workspaces").then(function (payload) {
      var list = payload.workspaces || [];
      var found = null;
      for (var i = 0; i < list.length; i += 1) {
        if (list[i] && list[i].id === "youtube") { found = list[i]; break; }
      }
      S.descriptor = found;
      S.counts = (found && found.counts) || null;
      return found;
    });
  }

  function loadSettings() {
    return api("/api/studio/workspaces/youtube/settings").then(function (payload) {
      S.settings = payload.settings || null;
      return S.settings;
    });
  }

  function loadProjects() {
    var params = [];
    if (S.status) params.push("status=" + encodeURIComponent(S.status));
    if (S.includeArchived) params.push("include_archived=1");
    if (S.query) params.push("q=" + encodeURIComponent(S.query));
    var suffix = params.length ? "?" + params.join("&") : "";
    return api("/api/studio/workspaces/youtube/projects" + suffix).then(function (payload) {
      S.projects = payload.projects || [];
      S.counts = payload.counts || S.counts;
      S.projectsLoaded = true;
      return S.projects;
    });
  }

  function loadRecentProjects() {
    return api("/api/studio/workspaces/youtube/projects").then(function (payload) {
      S.recentProjects = payload.projects || [];
      S.counts = payload.counts || S.counts;
      return S.recentProjects;
    });
  }

  function loadFeatures() {
    return yt("/features").then(function (payload) {
      S.features = payload;
      return payload;
    });
  }

  function loadLLM() {
    return yt("/llm/status").then(function (payload) {
      S.llm = payload;
      return payload;
    });
  }

  function loadChannels() {
    return yt("/channels").then(function (payload) {
      S.channels = payload.channels || [];
      if (!S.activeChannel && S.channels.length) S.activeChannel = S.channels[0];
      return S.channels;
    });
  }

  function loadOverview() {
    if (S.loading) return;
    S.loading = true;
    clearError();
    setText("yt-overview-loading", "Loading workspace…");
    Promise.all([loadDescriptor(), loadSettings(), loadRecentProjects(), loadChannels(), loadOverviewEditorial(), loadLLM()])
      .then(function () {
        renderCreate();
      })
      .catch(function (error) {
        showError(error.message);
      })
      .then(function () {
        S.loading = false;
        setText("yt-overview-loading", "");
      });
  }

  function loadOverviewEditorial() {
    return yt("/overview").then(function (payload) {
      S.overview = payload;
      if (payload && payload.channel) S.activeChannel = payload.channel;
      return payload;
    }).catch(function () {
      /* editorial backend unavailable — the workspace still works */
      S.overview = null;
      return null;
    });
  }

  function loadProjectsPage() {
    if (S.loading) return;
    S.loading = true;
    clearError();
    loadProjects().then(function () {
      renderProjects();
    }).catch(function (error) {
      showError(error.message);
    }).then(function () {
      S.loading = false;
    });
  }

  function loadTopics() {
    var q = S.topicQuery;
    var params = [];
    if (q.status) params.push("status=" + encodeURIComponent(q.status));
    if (q.pillar) params.push("pillar=" + encodeURIComponent(q.pillar));
    if (q.priority) params.push("priority=" + encodeURIComponent(q.priority));
    if (q.min_score !== "" && q.min_score != null) params.push("min_score=" + encodeURIComponent(q.min_score));
    if (q.q) params.push("q=" + encodeURIComponent(q.q));
    var suffix = params.length ? "?" + params.join("&") : "";
    return yt("/topics" + suffix).then(function (payload) {
      S.topics = payload.topics || [];
      S.topicCounts = payload.counts || {};
      S.topicStatuses = payload.statuses || [];
      return payload;
    }).catch(function (error) {
      panelError("yt-topics-error", error.message);
    });
  }

  function loadBacklogPage() {
    var chain = loadTopics().then(function () {
      renderTopicPipeline();
      renderTopics();
    });
    if (S.backlogTab === "research") {
      chain = Promise.all([loadValidations(), loadCompetitors()]).then(function () {
        renderResearch();
      });
    }
    return chain;
  }

  function loadValidations() {
    return yt("/research").then(function (payload) {
      S.validations = payload.validations || [];
      return S.validations;
    }).catch(function (error) {
      panelError("yt-research-error", error.message);
    });
  }

  function loadCompetitors() {
    return yt("/competitors").then(function (payload) {
      S.competitors = payload.competitors || [];
      return S.competitors;
    }).catch(function (error) {
      panelError("yt-research-error", error.message);
    });
  }

  function loadScripts() {
    var q = S.scriptQuery;
    var params = [];
    if (q.status) params.push("status=" + encodeURIComponent(q.status));
    if (q.q) params.push("q=" + encodeURIComponent(q.q));
    var suffix = params.length ? "?" + params.join("&") : "";
    return yt("/scripts" + suffix).then(function (payload) {
      S.scripts = payload.scripts || [];
      return S.scripts;
    }).catch(function (error) {
      panelError("yt-scripts-error", error.message);
    });
  }

  function loadScriptsPage() {
    return loadScripts().then(function () {
      renderScriptList();
    });
  }

  function loadSettingsPage() {
    return Promise.all([loadChannels(), loadLLM(), loadFeatures()])
      .then(function () {
        renderSettings();
      })
      .catch(function (error) {
        showError(error.message);
      });
  }

  function loadPromptVersions(channelId) {
    return yt("/channels/" + encodeURIComponent(channelId) + "/prompt-versions").then(function (payload) {
      S.channelPromptVersions = payload.versions || [];
      return S.channelPromptVersions;
    });
  }

  /* ------------------------------------------------------------------ */
  /* Renderers — Create                                                  */
  /* ------------------------------------------------------------------ */
  function composerPrefs() {
    try {
      return JSON.parse(localStorage.getItem(COMPOSER_KEY) || "{}") || {};
    } catch (err) {
      return {};
    }
  }

  function saveComposerPrefs() {
    var prefs = {
      channel_id: ($("yt-create-channel") || {}).value || "",
      format: ($("yt-create-format") || {}).value || "longform",
      style: ($("yt-create-style") || {}).value || "animated-explainer",
      aspect: ($("yt-create-aspect") || {}).value || "16:9",
      duration: ($("yt-create-duration") || {}).value || "12"
    };
    try { localStorage.setItem(COMPOSER_KEY, JSON.stringify(prefs)); } catch (err) { /* optional */ }
  }

  function applyComposerPrefs() {
    var prefs = composerPrefs();
    var map = { "yt-create-format": "format", "yt-create-style": "style", "yt-create-aspect": "aspect", "yt-create-duration": "duration" };
    Object.keys(map).forEach(function (id) {
      var el = $(id);
      if (el && prefs[map[id]]) el.value = prefs[map[id]];
    });
  }

  function renderCreate() {
    var select = $("yt-create-channel");
    if (select) {
      var current = select.value;
      select.innerHTML = S.channels.map(function (channel) {
        return '<option value="' + esc(channel.id) + '">' + esc(channel.name || "Channel") + "</option>";
      }).join("") || '<option value="">No channel profile — create one in Settings</option>';
      var prefs = composerPrefs();
      if (prefs.channel_id) select.value = prefs.channel_id;
      if (select.value !== prefs.channel_id && S.channels.length) select.value = S.channels[0].id;
      if (current && S.channels.some(function (c) { return c.id === current; })) select.value = current;
    }
    applyComposerPrefs();

    var model = S.llm && S.llm.model ? S.llm.model : "GLM-5.3-Flash";
    setText("yt-create-model", model);

    renderRecentProjects();
    icons();
  }

  function renderRecentProjects() {
    var host = $("yt-recent-projects");
    var empty = $("yt-recent-empty");
    if (!host) return;
    if (!S.recentProjects.length) {
      host.innerHTML = "";
      toggle(empty, true);
      icons();
      return;
    }
    toggle(empty, false);
    host.innerHTML = S.recentProjects.slice(0, 5).map(projectRow).join("");
    icons();
  }

  function projectRow(project) {
    return '<button type="button" class="yt-project-row" data-yt-project="' + esc(project.id) + '">' +
      '<span class="yt-project-mark"><i data-lucide="video"></i></span>' +
      '<span class="yt-project-copy">' +
        "<b>" + esc(project.title || "Untitled project") + "</b>" +
        "<em>" + esc(project.content_pillar || "No content pillar") + " · updated " + esc(dateLabel(project.updated_iso || project.created_iso)) + "</em>" +
      "</span>" +
      '<span class="yt-project-badges">' + statusBadge(project.status) + stageBadge(project.stage) + "</span>" +
      '<span class="yt-project-chevron"><i data-lucide="chevron-right"></i></span>' +
      "</button>";
  }

  /* ------------------------------------------------------------------ */
  /* Renderers — Projects                                                */
  /* ------------------------------------------------------------------ */
  function renderProjects() {
    var host = $("yt-projects-list");
    if (!host) return;
    setText("yt-projects-count", (S.projects.length || 0) + " project" + (S.projects.length === 1 ? "" : "s"));
    if (!S.projects.length) {
      host.innerHTML = emptyState(
        S.query || S.status ? "No projects match" : "No YouTube projects yet",
        S.query || S.status
          ? "Clear the search or status filter to see all projects."
          : "Create your first video project. Research, scripts and production live inside it.",
        { id: "create", label: "Create New Video", icon: "plus" }
      );
      icons();
      return;
    }
    host.innerHTML = S.projects.map(projectCard).join("");
    icons();
  }

  function projectCard(project) {
    return '<article class="yt-project-card" data-yt-project="' + esc(project.id) + '">' +
      '<div class="yt-project-card-head">' +
        '<div><h3>' + esc(project.title || "Untitled project") + "</h3>" +
        "<p>" + esc(project.description || "No description yet.") + "</p></div>" +
        '<div class="yt-project-badges">' + statusBadge(project.status) + stageBadge(project.stage) + "</div>" +
      "</div>" +
      '<div class="yt-project-card-foot">' +
        "<span>" + esc(project.content_pillar || "No content pillar") + "</span>" +
        "<span>Created " + esc(dateLabel(project.created_iso)) + "</span>" +
        '<span class="yt-project-actions">' +
          '<button type="button" class="yt-link" data-yt-open="' + esc(project.id) + '">Open</button>' +
        "</span>" +
      "</div></article>";
  }

  /* ------------------------------------------------------------------ */
  /* Renderers — Scripts (Projects → Scripts tab)                        */
  /* ------------------------------------------------------------------ */
  function scriptStatusLabel(status) {
    var labels = {
      draft: "Draft",
      in_review: "In Review",
      approved: "Approved",
      needs_revision: "Needs Revision"
    };
    return labels[status] || "Draft";
  }

  function scriptTone(status) {
    return { draft: "muted", in_review: "info", approved: "ok", needs_revision: "warn" }[status] || "muted";
  }

  function renderScriptList() {
    var host = $("yt-script-list");
    if (!host) return;
    if (!S.scripts.length) {
      host.innerHTML = '<p class="yt-panel-note">No scripts yet. Generate one from an approved topic or write one manually.</p>';
      return;
    }
    host.innerHTML = S.scripts.map(function (script) {
      var active = S.currentScript && S.currentScript.id === script.id;
      return '<button type="button" class="yt-script-row' + (active ? " is-active" : "") + '" data-yt-script="' + esc(script.id) + '">' +
        "<b>" + esc(script.title || "Untitled script") + "</b>" +
        "<em>v" + esc(script.version || 1) + " · " + esc(script.word_count || 0) + " words · ~" + Math.round((script.est_duration_sec || 0) / 60) + " min</em>" +
        '<span class="yt-badge yt-badge-' + scriptTone(script.status) + '">' + esc(scriptStatusLabel(script.status)) + "</span>" +
        "</button>";
    }).join("");
  }

  function renderScriptEditor() {
    var host = $("yt-script-editor");
    if (!host) return;
    var script = S.currentScript;
    if (!script) {
      host.innerHTML = '<div class="yt-empty"><div class="yt-empty-icon"><i data-lucide="file-text" class="w-6 h-6"></i></div>' +
        '<h3>Select a script</h3><p>Choose a script on the left, or create one from an approved topic.</p></div>';
      icons();
      return;
    }
    var fact = script.fact_check || {};
    var sections = script.sections || [];
    var actions = [];
    if (script.status === "draft") actions.push('<button type="button" class="collart-btn-soft" data-yt-script-status="in_review">Submit for review</button>');
    if (script.status === "in_review") {
      actions.push('<button type="button" class="collart-btn" data-yt-script-status="approved">Approve</button>');
      actions.push('<button type="button" class="collart-btn-soft" data-yt-script-status="needs_revision">Needs revision</button>');
    }
    if (script.status === "needs_revision") actions.push('<button type="button" class="collart-btn-soft" data-yt-script-status="draft">Back to draft</button>');
    host.innerHTML =
      '<div class="yt-editor-head">' +
        "<h3>" + esc(script.title || "Untitled script") + "</h3>" +
        '<div class="yt-project-badges">' +
          '<span class="yt-badge yt-badge-' + scriptTone(script.status) + '">' + esc(scriptStatusLabel(script.status)) + "</span>" +
          '<span class="yt-badge yt-badge-stage">v' + esc(script.version || 1) + "</span>" +
        "</div>" +
      "</div>" +
      '<dl class="yt-meta">' +
        "<div><dt>Words</dt><dd>" + esc(script.word_count || 0) + "</dd></div>" +
        "<div><dt>Estimated narration</dt><dd>~" + Math.round((script.est_duration_sec || 0) / 60) + " min</dd></div>" +
        "<div><dt>Target duration</dt><dd>" + esc(script.target_duration_min || "—") + " min</dd></div>" +
        "<div><dt>Fact check</dt><dd>" + (fact.complete ? "Complete" : (fact.unchecked_count || 0) + " items unchecked") + "</dd></div>" +
        "<div><dt>Citations</dt><dd>" + esc((script.citations || []).length) + "</dd></div>" +
      "</dl>" +
      (script.status === "in_review" && !fact.complete
        ? '<p class="yt-hint">Approval requires every fact-check item verified and at least one citation. The backend enforces this.</p>'
        : "") +
      '<div class="yt-editor-actions">' + actions.join(" ") +
        '<button type="button" class="yt-link" data-yt-script-versions="' + esc(script.id) + '">Version history</button>' +
        '<button type="button" class="yt-danger-btn" data-yt-script-delete="' + esc(script.id) + '">Delete</button>' +
      "</div>" +
      '<div class="yt-editor-sections">' +
        (sections.length
          ? sections.map(function (section, index) {
              return '<details class="yt-editor-section"' + (index === 0 ? " open" : "") + ">" +
                "<summary>" + esc(section.heading || ("Section " + (index + 1))) + "</summary>" +
                '<p class="yt-editor-narration">' + esc(section.narration || "") + "</p>" +
                (section.on_screen_text ? '<p class="yt-hint"><b>On screen:</b> ' + esc(section.on_screen_text) + "</p>" : "") +
                (section.animation_notes ? '<p class="yt-hint"><b>Animation:</b> ' + esc(section.animation_notes) + "</p>" : "") +
                "</details>";
            }).join("")
          : '<p class="yt-panel-note">This script has no sections yet.</p>') +
      "</div>";
    icons();
  }

  function renderScriptVersions(scriptId) {
    yt("/scripts/" + encodeURIComponent(scriptId) + "/versions").then(function (payload) {
      var versions = payload.versions || [];
      openModal(
        "Version history",
        versions.length
          ? '<div class="yt-version-list">' + versions.map(function (version) {
              return '<div class="yt-version-row">' +
                "<b>v" + esc(version.version) + "</b>" +
                "<span>" + esc(version.note || "No note") + "</span>" +
                "<em>" + esc(dateLabel(version.created_iso)) + "</em>" +
                '<button type="button" class="yt-link" data-yt-version-restore="' + esc(version.id) + '">Restore</button>' +
                "</div>";
            }).join("") + "</div>"
          : "<p class='yt-panel-note'>No stored versions yet. Every update snapshots the previous state.</p>",
        '<button type="button" class="collart-btn-soft" data-yt-close>Close</button>'
      );
    }).catch(function (error) {
      toast(error.message);
    });
  }

  /* ------------------------------------------------------------------ */
  /* Renderers — Topic Backlog                                           */
  /* ------------------------------------------------------------------ */
  var TOPIC_LABELS = {
    idea: "Idea",
    researching: "Researching",
    validated: "Validated",
    approved: "Approved",
    scripted: "Scripted",
    in_production: "In Production",
    published: "Published",
    rejected: "Rejected"
  };

  function topicTone(status) {
    return {
      idea: "muted",
      researching: "info",
      validated: "info",
      approved: "ok",
      scripted: "ok",
      in_production: "warn",
      published: "ok",
      rejected: "muted"
    }[status] || "muted";
  }

  function topicNextActions(topic) {
    var actions = [];
    switch (topic.status) {
      case "idea": actions.push(["researching", "Start research"]); break;
      case "researching": actions.push(["validated", "Mark validated"]); break;
      case "validated": actions.push(["approved", "Approve"]); break;
      case "approved": actions.push(["__script__", "New script"]); break;
      case "scripted": actions.push(["in_production", "Start production"]); break;
      case "in_production": actions.push(["published", "Mark published"]); break;
    }
    if (topic.status !== "published") actions.push(["rejected", "Reject"]);
    return actions;
  }

  function renderTopicPipeline() {
    var host = $("yt-topic-pipeline");
    if (!host) return;
    var statuses = S.topicStatuses.length ? S.topicStatuses : Object.keys(TOPIC_LABELS);
    host.innerHTML = statuses.map(function (status) {
      var value = S.topicCounts[status] || 0;
      return '<span class="yt-pipe-chip' + (value ? " has-items" : "") + '">' +
        esc(TOPIC_LABELS[status] || status) + " <b>" + value + "</b></span>";
    }).join("");
  }

  function renderTopics() {
    var host = $("yt-topics-list");
    if (!host) return;
    panelError("yt-topics-error", "");
    setText("yt-topics-count", (S.topics.length || 0) + " topic" + (S.topics.length === 1 ? "" : "s"));
    if (!S.topics.length) {
      host.innerHTML = emptyState(
        "No topics here yet",
        "Create a topic from the Create screen, or add one manually. Template recommendations attach in Phase 4.",
        { id: "topic", label: "New Topic", icon: "lightbulb" }
      );
      icons();
      return;
    }
    host.innerHTML = S.topics.map(topicCard).join("");
    icons();
  }

  function topicCard(topic) {
    var template = topic.template_name
      ? '<span class="yt-template-chip"><i data-lucide="layout-template" class="w-3 h-3"></i> ' + esc(topic.template_name) + "</span>"
      : "";
    var actions = topicNextActions(topic).map(function (pair) {
      return '<button type="button" class="yt-link" data-yt-topic-status="' + esc(pair[0]) + '" data-yt-topic-id="' + esc(topic.id) + '">' + esc(pair[1]) + "</button>";
    }).join(" ");
    var score = topic.score != null
      ? '<span class="yt-score-pill ' + (topic.score >= 18 ? "pass" : "fail") + '">' + topic.score + "/25</span>"
      : '<span class="yt-score-pill none">no validation</span>';
    return '<article class="yt-topic-card" data-yt-topic-card="' + esc(topic.id) + '">' +
      '<div class="yt-topic-head">' +
        "<h3>" + esc(topic.title) + "</h3>" +
        '<div class="yt-project-badges">' +
          '<span class="yt-badge yt-badge-' + topicTone(topic.status) + '">' + esc(TOPIC_LABELS[topic.status] || topic.status) + "</span>" +
          (topic.priority ? '<span class="yt-badge yt-badge-stage">' + esc(topic.priority) + "</span>" : "") +
          score +
        "</div>" +
      "</div>" +
      (topic.angle ? '<p class="yt-topic-angle">' + esc(topic.angle) + "</p>" : "") +
      '<div class="yt-topic-foot">' +
        "<span>" + esc(topic.pillar || "No pillar") + "</span>" +
        template +
        "<span>Updated " + esc(dateLabel(topic.updated_iso)) + "</span>" +
        '<span class="yt-topic-actions">' + actions +
          '<button type="button" class="yt-danger-btn" data-yt-topic-delete="' + esc(topic.id) + '">Delete</button>' +
        "</span>" +
      "</div>" +
      "</article>";
  }

  /* ------------------------------------------------------------------ */
  /* Renderers — Research & competitors                                  */
  /* ------------------------------------------------------------------ */
  function renderResearch() {
    panelError("yt-research-error", "");
    var rules = (S.overview && S.overview.rules && S.overview.rules.validation) || null;
    var rulesHost = $("yt-research-rules");
    if (rulesHost) {
      rulesHost.textContent = rules
        ? "Each criterion scores " + rules.min_score + "–" + rules.max_score + " (max " + rules.max_total + "); approval needs " + rules.approval_threshold + "/25. Scores are analyst estimates."
        : "Five criteria, 1–5 each, approval at 18/25. Scores are analyst estimates.";
    }
    var host = $("yt-research-list");
    if (host) {
      if (!S.validations.length) {
        host.innerHTML = emptyState("No validations yet", "Validate a niche before approving topics — the 18/25 threshold is enforced by the backend.", { id: "validation", label: "New Validation", icon: "search" });
      } else {
        host.innerHTML = S.validations.map(function (validation) {
          var tone = validation.verdict === "pass" ? "pass" : (validation.verdict === "fail" ? "fail" : "review");
          return '<article class="yt-research-card">' +
            '<div class="yt-topic-head">' +
              "<h3>" + esc(validation.subject) + "</h3>" +
              '<div class="yt-project-badges">' +
                '<span class="yt-score-pill ' + tone + '">' + esc(validation.total_score) + "/" + esc(validation.max_total) + "</span>" +
                '<span class="yt-badge yt-badge-' + (validation.verdict === "pass" ? "ok" : validation.verdict === "fail" ? "muted" : "warn") + '">' + esc(validation.verdict) + "</span>" +
              "</div>" +
            "</div>" +
            (validation.summary ? '<p class="yt-topic-angle">' + esc(validation.summary) + "</p>" : "") +
            '<div class="yt-topic-foot">' +
              "<span>" + esc(validation.pillar || "No pillar") + "</span>" +
              "<span>" + esc((validation.sources || []).length) + " source" + ((validation.sources || []).length === 1 ? "" : "s") + "</span>" +
              "<span>Updated " + esc(dateLabel(validation.updated_iso)) + "</span>" +
              '<span class="yt-topic-actions">' +
                '<button type="button" class="yt-link" data-yt-validation-topic="' + esc(validation.id) + '">Create topic</button>' +
                '<button type="button" class="yt-danger-btn" data-yt-validation-delete="' + esc(validation.id) + '">Delete</button>' +
              "</span>" +
            "</div>" +
            "</article>";
        }).join("");
      }
      icons();
    }
    var compHost = $("yt-competitor-list");
    if (compHost) {
      if (!S.competitors.length) {
        compHost.innerHTML = '<p class="yt-panel-note">No competitors recorded. Observations must be marked observed or assumed.</p>';
      } else {
        compHost.innerHTML = S.competitors.map(function (competitor) {
          var observations = (competitor.observations || []).slice(-3).map(function (observation) {
            return '<li><span class="yt-ref-chip">' + esc(observation.basis || "observed") + "</span> " + esc(observation.note) + "</li>";
          }).join("");
          return '<article class="yt-competitor-card">' +
            '<div class="yt-topic-head"><h3>' + esc(competitor.name) + "</h3>" +
            '<span class="yt-topic-actions"><button type="button" class="yt-link" data-yt-competitor-observe="' + esc(competitor.id) + '">Add observation</button></span></div>' +
            (competitor.url ? '<p class="yt-hint yt-mono">' + esc(competitor.url) + "</p>" : "") +
            (observations ? "<ul class=\"yt-competitor-notes\">" + observations + "</ul>" : "") +
            "</article>";
        }).join("");
      }
      icons();
    }
  }

  /* ------------------------------------------------------------------ */
  /* Renderers — Publishing / Analytics feature flags                    */
  /* ------------------------------------------------------------------ */
  function featureNote(kind, title, copy) {
    var feature = (S.features && S.features[kind]) || {};
    if (feature.available) {
      return '<div class="yt-empty"><div class="yt-empty-icon"><i data-lucide="check-circle-2" class="w-6 h-6"></i></div>' +
        "<h3>" + esc(title) + " is ready</h3><p>Google OAuth is configured. The " + esc(title.toLowerCase()) +
        ' workflow arrives in Phase 10.</p></div>';
    }
    return '<div class="yt-empty"><div class="yt-empty-icon"><i data-lucide="lock" class="w-6 h-6"></i></div>' +
      "<h3>Not available yet</h3><p>" + esc(feature.reason || copy || (title + " arrives in Phase 10 once Google OAuth is configured.")) + "</p>" +
      '<div class="yt-placeholder-foot"><span>Nothing is faked in the meantime — no uploads, no metrics.</span></div></div>';
  }

  function renderPublishing() {
    var host = $("yt-publishing-flag");
    if (host) host.innerHTML = featureNote("publishing", "Publishing", "Publishing arrives in Phase 10 once a Google Cloud OAuth client is configured.");
    icons();
  }

  function renderAnalytics() {
    var host = $("yt-analytics-flag");
    if (host) host.innerHTML = featureNote("analytics", "Analytics", "YouTube Analytics arrives in Phase 10 once a Google Cloud OAuth client is configured.");
    icons();
  }

  /* ------------------------------------------------------------------ */
  /* Renderers — Settings                                                */
  /* ------------------------------------------------------------------ */
  function renderConnectionCard() {
    var host = $("yt-connection-status");
    if (!host) return;
    var oauth = (S.features && S.features.oauth) || {};
    if (oauth.configured) {
      host.innerHTML = '<p class="yt-panel-note yt-ok-note"><i data-lucide="check-circle-2" class="w-4 h-4"></i> Google OAuth client configured (' +
        esc(oauth.source || "env") + '). The connect-channel flow arrives in Phase 10.</p>';
    } else {
      host.innerHTML = '<p class="yt-panel-note">No Google OAuth client configured yet. Publishing and Analytics arrive in Phase 10 behind this connection — nothing is enabled or faked in the meantime.</p>' +
        '<p class="yt-hint">Provide a Google Cloud OAuth client id/secret (env <span class="yt-mono">GOOGLE_OAUTH_CLIENT_ID</span> / <span class="yt-mono">GOOGLE_OAUTH_CLIENT_SECRET</span>) when you are ready.</p>';
    }
    icons();
  }

  function renderLLMSettings() {
    var host = $("yt-llm-status");
    if (host) {
      var llm = S.llm || {};
      var state = llm.configured
        ? '<span class="yt-ok-note"><i data-lucide="check-circle-2" class="w-4 h-4"></i> Ready</span>'
        : '<span class="yt-warn-note"><i data-lucide="alert-triangle" class="w-4 h-4"></i> ' + esc(llm.hint_setup || "Not configured") + "</span>";
      host.innerHTML = "<p class=\"yt-panel-note\">" + esc(llm.provider_label || llm.provider || "—") +
        " · model <b>" + esc(llm.model || "default") + "</b> · " + state + "</p>";
      icons();
    }
    var select = $("yt-llm-provider");
    if (select && S.llm) select.value = S.llm.provider || "opencode";
  }

  function renderChannelList() {
    var host = $("yt-channel-list");
    if (!host) return;
    if (!S.channels.length) {
      host.innerHTML = '<p class="yt-panel-note">No channel profiles. Create one to seed the master prompt and pillars.</p>';
      return;
    }
    host.innerHTML = S.channels.map(function (channel) {
      var active = S.activeChannel && S.activeChannel.id === channel.id;
      return '<button type="button" class="yt-channel-row' + (S.selectedChannelId === channel.id ? " is-active" : "") + '" data-yt-channel="' + esc(channel.id) + '">' +
        "<b>" + esc(channel.name) + "</b>" +
        "<em>" + esc(channel.content_niche || "No niche set") + "</em>" +
        (active ? '<span class="yt-badge yt-badge-ok">Active</span>' : '<button type="button" class="yt-link" data-yt-channel-activate="' + esc(channel.id) + '">Activate</button>') +
        "</button>";
    }).join("");
  }

  function renderChannelEditor() {
    var host = $("yt-channel-editor");
    if (!host) return;
    var channel = null;
    for (var i = 0; i < S.channels.length; i += 1) {
      if (S.channels[i].id === S.selectedChannelId) { channel = S.channels[i]; break; }
    }
    if (!channel) {
      host.innerHTML = '<div class="yt-empty"><div class="yt-empty-icon"><i data-lucide="user-round-cog" class="w-6 h-6"></i></div>' +
        "<h3>Select a channel</h3><p>Choose a channel profile to edit its identity and master prompt.</p></div>";
      icons();
      return;
    }
    var pillars = (channel.content_pillars || []).map(function (pillar) { return pillar.label; }).join(", ");
    host.innerHTML =
      '<form id="yt-channel-form" class="yt-form">' +
        '<div class="yt-form-grid">' +
          '<label class="yt-field"><span>Name <em>required</em></span><input id="yt-ch-name" type="text" maxlength="120" value="' + esc(channel.name) + '" required></label>' +
          '<label class="yt-field"><span>Tagline</span><input id="yt-ch-tagline" type="text" maxlength="200" value="' + esc(channel.tagline || "") + '"></label>' +
          '<label class="yt-field"><span>Handle</span><input id="yt-ch-handle" type="text" maxlength="80" value="' + esc(channel.handle || "") + '"></label>' +
          '<label class="yt-field"><span>Content niche</span><input id="yt-ch-niche" type="text" maxlength="300" value="' + esc(channel.content_niche || "") + '"></label>' +
        "</div>" +
        '<label class="yt-field"><span>Description</span><textarea id="yt-ch-description" rows="2" maxlength="2000">' + esc(channel.description || "") + "</textarea></label>" +
        '<label class="yt-field"><span>Target audience</span><textarea id="yt-ch-audience" rows="2" maxlength="500">' + esc(channel.target_audience || "") + "</textarea></label>" +
        (pillars ? '<p class="yt-hint">Content pillars: ' + esc(pillars) + "</p>" : "") +
        '<p id="yt-ch-error" class="yt-form-error is-hidden"></p>' +
        '<div class="yt-editor-actions">' +
          '<button type="submit" class="collart-btn-soft" id="yt-ch-save">Save profile</button>' +
          '<button type="button" class="collart-btn-soft" data-yt-channel-prompt="' + esc(channel.id) + '">Master prompt & versions</button>' +
          (S.activeChannel && S.activeChannel.id !== channel.id
            ? '<button type="button" class="collart-btn-soft" data-yt-channel-activate="' + esc(channel.id) + '">Activate</button>' : "") +
          '<button type="button" class="yt-danger-btn" data-yt-channel-delete="' + esc(channel.id) + '">Delete</button>' +
        "</div>" +
      "</form>" +
      '<div id="yt-channel-versions"></div>';
    var form = $("yt-channel-form");
    if (form) {
      form.addEventListener("submit", function (event) {
        event.preventDefault();
        saveChannel(channel.id);
      });
    }
  }

  function renderPromptVersions(channelId) {
    var host = $("yt-channel-versions");
    if (!host) return;
    var versions = S.channelPromptVersions;
    var current = null;
    var channel = null;
    for (var i = 0; i < S.channels.length; i += 1) {
      if (S.channels[i].id === channelId) { channel = S.channels[i]; break; }
    }
    for (var j = 0; j < versions.length; j += 1) {
      if (channel && versions[j].id === channel.master_prompt_version_id) { current = versions[j]; break; }
    }
    host.innerHTML =
      '<div class="yt-version-block">' +
        "<h4>Channel master prompt</h4>" +
        (current
          ? '<p class="yt-hint">Active: v' + esc(current.version) + " · " + esc(current.chars || (current.body || "").length) + " chars · " + esc(dateLabel(current.created_iso)) + "</p>" +
            '<details class="yt-editor-section"><summary>Preview current prompt</summary>' +
            '<pre class="yt-prompt-preview">' + esc((current.body || "").slice(0, 4000)) + ((current.body || "").length > 4000 ? "\n\n… (truncated preview)" : "") + "</pre></details>"
          : '<p class="yt-hint">No default version selected yet.</p>') +
        '<div class="yt-version-list">' +
          versions.map(function (version) {
            return '<div class="yt-version-row">' +
              "<b>v" + esc(version.version) + "</b>" +
              "<span>" + esc(version.note || "No note") + "</span>" +
              "<em>" + esc(dateLabel(version.created_iso)) + "</em>" +
              '<span class="yt-topic-actions">' +
                '<button type="button" class="yt-link" data-yt-prompt-default="' + esc(version.id) + '">Set default</button>' +
                '<button type="button" class="yt-link" data-yt-prompt-restore="' + esc(version.id) + '">Restore as new</button>' +
              "</span></div>";
          }).join("") +
        "</div>" +
        '<div class="yt-composer-foot">' +
          '<button type="button" class="collart-btn-soft" data-yt-channel-new-prompt="' + esc(channelId) + '">New prompt version</button>' +
        "</div>" +
      "</div>";
    icons();
  }

  function renderSettings() {
    renderConnectionCard();
    renderLLMSettings();
    renderChannelList();
    renderChannelEditor();
    icons();
  }

  /* ------------------------------------------------------------------ */
  /* Modal helpers                                                       */
  /* ------------------------------------------------------------------ */
  function openModal(title, bodyHtml, footHtml) {
    var modal = $("yt-modal");
    if (!modal) return;
    setText("yt-modal-title", title);
    var body = $("yt-modal-body");
    var foot = $("yt-modal-foot");
    if (body) body.innerHTML = bodyHtml || "";
    if (foot) foot.innerHTML = footHtml || "";
    modal.classList.remove("is-hidden");
    icons();
  }

  function closeModal() {
    var modal = $("yt-modal");
    if (modal) modal.classList.add("is-hidden");
    var body = $("yt-modal-body");
    var foot = $("yt-modal-foot");
    if (body) body.innerHTML = "";
    if (foot) foot.innerHTML = "";
  }

  function modalError(message) {
    var el = $("yt-modal-error");
    if (el) {
      el.textContent = message || "";
      el.classList.toggle("is-hidden", !message);
    }
  }

  function modalFoot(extra) {
    return '<button type="button" class="collart-btn-soft" data-yt-close>Cancel</button>' + (extra || "");
  }

  /* ------------------------------------------------------------------ */
  /* Create composer                                                     */
  /* ------------------------------------------------------------------ */
  function submitCreate() {
    var prompt = $("yt-create-prompt");
    var button = $("yt-create-submit");
    var text = prompt && prompt.value.trim();
    function setError(message) {
      var el = $("yt-create-error");
      if (!el) return;
      el.textContent = message || "";
      el.classList.toggle("is-hidden", !message);
    }
    setError("");
    if (!text) {
      setError("Describe the video you want first.");
      if (prompt) prompt.focus();
      return;
    }
    if (!S.channels.length) {
      setError("Create a channel profile in Settings first.");
      return;
    }
    var select = $("yt-create-channel");
    var channelId = (select && select.value) || (S.channels[0] && S.channels[0].id) || "";
    var title = text.split(/\n/)[0].trim().slice(0, 160);
    saveComposerPrefs();
    setBusy(button, true, "Creating…");
    yt("/topics?channel_id=" + encodeURIComponent(channelId), {
      method: "POST",
      body: { title: title, notes: text, priority: "P2" }
    }).then(function () {
      if (prompt) prompt.value = "";
      toast("Topic added to the backlog.");
      go("backlog");
    }).catch(function (error) {
      setError(error.code === "duplicate_topic"
        ? error.message + " Open the Topic Backlog to review it."
        : error.message);
    }).then(function () {
      setBusy(button, false);
    });
  }

  /* ------------------------------------------------------------------ */
  /* Project modals (existing behaviour)                                 */
  /* ------------------------------------------------------------------ */
  function openCreateProject() {
    openModal(
      "Create New Video",
      '<form id="yt-project-form" class="yt-form" autocomplete="off">' +
        '<label class="yt-field"><span>Title <em>required</em></span>' +
          '<input id="yt-form-title" type="text" maxlength="160" placeholder="e.g. 5 tax deductions small business owners miss" required></label>' +
        '<label class="yt-field"><span>Description</span>' +
          '<textarea id="yt-form-description" rows="3" maxlength="4000" placeholder="What is this video about?"></textarea></label>' +
        '<div class="yt-form-grid">' +
          '<label class="yt-field"><span>Content pillar</span>' +
            '<input id="yt-form-pillar" type="text" maxlength="80" placeholder="Tax Education"></label>' +
          '<label class="yt-field"><span>Status</span>' +
            '<select id="yt-form-status">' +
              '<option value="draft">Draft</option>' +
              '<option value="in_progress">In Progress</option>' +
              '<option value="review">Awaiting Approval</option>' +
            "</select></label>" +
        "</div>" +
        '<p class="yt-hint">The project starts in the Research stage. Packaging approval and production stages arrive in Phases 6–8.</p>' +
        '<p id="yt-modal-error" class="yt-form-error is-hidden"></p>' +
      "</form>",
      modalFoot('<button type="button" class="collart-btn" id="yt-form-create">Create Project</button>')
    );
    var form = $("yt-project-form");
    if (form) {
      form.addEventListener("submit", function (event) {
        event.preventDefault();
        createProject();
      });
    }
    var create = $("yt-form-create");
    if (create) create.addEventListener("click", createProject);
    var title = $("yt-form-title");
    if (title) title.focus();
  }

  function createProject() {
    var title = $("yt-form-title");
    var button = $("yt-form-create");
    if (!title || !title.value.trim()) {
      modalError("A title is required.");
      if (title) title.focus();
      return;
    }
    modalError("");
    setBusy(button, true, "Creating…");
    api("/api/studio/workspaces/youtube/projects", {
      method: "POST",
      body: {
        title: title.value.trim(),
        description: $("yt-form-description") ? $("yt-form-description").value : "",
        content_pillar: $("yt-form-pillar") ? $("yt-form-pillar").value : "",
        status: $("yt-form-status") ? $("yt-form-status").value : "draft"
      }
    }).then(function (payload) {
      closeModal();
      toast("Project created.");
      S.projectsLoaded = false;
      loadOverview();
      if (S.section === "projects") loadProjectsPage();
      return payload;
    }).catch(function (error) {
      modalError(error.message);
    }).then(function () {
      setBusy(button, false);
    });
  }

  function openProject(projectId) {
    var project = null;
    for (var i = 0; i < S.projects.length; i += 1) {
      if (S.projects[i].id === projectId) { project = S.projects[i]; break; }
    }
    if (!project) return;
    var archived = project.status === "archived";
    openModal(
      project.title || "Project",
      '<div class="yt-project-detail">' +
        '<div class="yt-project-badges">' + statusBadge(project.status) + stageBadge(project.stage) + "</div>" +
        '<p class="yt-project-description">' + esc(project.description || "No description yet.") + "</p>" +
        '<dl class="yt-meta">' +
          "<div><dt>Content pillar</dt><dd>" + esc(project.content_pillar || "—") + "</dd></div>" +
          "<div><dt>Created</dt><dd>" + esc(dateLabel(project.created_iso)) + "</dd></div>" +
          "<div><dt>Updated</dt><dd>" + esc(dateLabel(project.updated_iso)) + "</dd></div>" +
          "<div><dt>Project ID</dt><dd class=\"yt-mono\">" + esc(project.id) + "</dd></div>" +
        "</dl>" +
        '<p class="yt-hint">Packaging (Phase 6) transfers an approved package into this project; production stages arrive in Phase 8. This project is stored and isolated now.</p>' +
        '<p id="yt-modal-error" class="yt-form-error is-hidden"></p>' +
      "</div>",
      '<button type="button" class="collart-btn-soft" data-yt-close>Close</button>' +
      (archived
        ? '<button type="button" class="collart-btn" data-yt-restore="' + esc(project.id) + '">Restore</button>'
        : '<button type="button" class="collart-btn-soft" data-yt-archive="' + esc(project.id) + '">Archive</button>') +
      '<button type="button" class="yt-danger-btn" data-yt-delete="' + esc(project.id) + '">Delete</button>'
    );
  }

  function updateProject(projectId, patch, successMessage) {
    return api("/api/studio/workspaces/youtube/projects/" + encodeURIComponent(projectId), {
      method: "PATCH",
      body: patch
    }).then(function (payload) {
      closeModal();
      toast(successMessage || "Project updated.");
      S.projectsLoaded = false;
      loadOverview();
      if (S.section === "projects") loadProjectsPage();
      return payload;
    }).catch(function (error) {
      modalError(error.message);
    });
  }

  function confirmDelete(projectId) {
    var project = null;
    for (var i = 0; i < S.projects.length; i += 1) {
      if (S.projects[i].id === projectId) { project = S.projects[i]; break; }
    }
    openModal(
      "Delete project?",
      '<p class="yt-confirm-copy">Delete “' + esc(project ? project.title : "this project") +
        '”? It will be hidden from lists and kept recoverable. Media files are never deleted silently.</p>' +
        '<p id="yt-modal-error" class="yt-form-error is-hidden"></p>',
      modalFoot('<button type="button" class="yt-danger-btn" id="yt-confirm-delete">Delete Project</button>')
    );
    var button = $("yt-confirm-delete");
    if (button) {
      button.addEventListener("click", function () {
        setBusy(button, true, "Deleting…");
        api("/api/studio/workspaces/youtube/projects/" + encodeURIComponent(projectId), {
          method: "DELETE"
        }).then(function () {
          closeModal();
          toast("Project deleted.");
          S.projectsLoaded = false;
          loadOverview();
          if (S.section === "projects") loadProjectsPage();
        }).catch(function (error) {
          modalError(error.message);
          setBusy(button, false);
        });
      });
    }
  }

  /* ------------------------------------------------------------------ */
  /* Topic actions                                                       */
  /* ------------------------------------------------------------------ */
  function setTopicStatus(topicId, status) {
    var body = { status: status };
    if (status === "rejected") {
      var reason = window.prompt("Why is this topic rejected? (optional)") || "";
      if (reason) body.reason = reason;
    }
    yt("/topics/" + encodeURIComponent(topicId) + "/status", { method: "POST", body: body })
      .then(function () {
        toast("Topic updated.");
        return loadTopics();
      })
      .then(function () {
        renderTopicPipeline();
        renderTopics();
      })
      .catch(function (error) {
        panelError("yt-topics-error", error.message);
      });
  }

  function deleteTopic(topicId) {
    yt("/topics/" + encodeURIComponent(topicId), { method: "DELETE" })
      .then(function () {
        toast("Topic deleted.");
        return loadTopics();
      })
      .then(function () {
        renderTopicPipeline();
        renderTopics();
      })
      .catch(function (error) {
        panelError("yt-topics-error", error.message);
      });
  }

  function openTopicModal() {
    var pillars = (S.activeChannel && S.activeChannel.content_pillars) || [];
    var pillarOptions = '<option value="">No pillar</option>' + pillars.map(function (pillar) {
      return '<option value="' + esc(pillar.label) + '">' + esc(pillar.label) + "</option>";
    }).join("");
    openModal(
      "New Topic",
      '<form id="yt-topic-form" class="yt-form" autocomplete="off">' +
        '<label class="yt-field"><span>Title <em>required</em></span>' +
          '<input id="yt-topic-title" type="text" maxlength="160" placeholder="e.g. How the S-Corp salary rule actually works" required></label>' +
        '<label class="yt-field"><span>Angle</span><textarea id="yt-topic-angle" rows="2" maxlength="500" placeholder="The specific take or question"></textarea></label>' +
        '<div class="yt-form-grid">' +
          '<label class="yt-field"><span>Content pillar</span><select id="yt-topic-pillar">' + pillarOptions + "</select></label>" +
          '<label class="yt-field"><span>Priority</span><select id="yt-topic-priority"><option value="P1">P1</option><option value="P2" selected>P2</option><option value="P3">P3</option></select></label>' +
        "</div>" +
        '<p class="yt-hint">Topics can also be created from a saved validation in the Research tab.</p>' +
        '<p id="yt-modal-error" class="yt-form-error is-hidden"></p>' +
      "</form>",
      modalFoot('<button type="button" class="collart-btn" id="yt-topic-create-go">Create Topic</button>')
    );
    var button = $("yt-topic-create-go");
    var submit = function () {
      var title = $("yt-topic-title");
      if (!title || !title.value.trim()) { modalError("A title is required."); return; }
      modalError("");
      setBusy(button, true, "Creating…");
      yt("/topics", {
        method: "POST",
        body: {
          title: title.value.trim(),
          angle: ($("yt-topic-angle") || {}).value || "",
          pillar: ($("yt-topic-pillar") || {}).value || "",
          priority: ($("yt-topic-priority") || {}).value || "P2"
        }
      }).then(function () {
        closeModal();
        toast("Topic created.");
        return loadTopics();
      }).then(function () {
        renderTopicPipeline();
        renderTopics();
      }).catch(function (error) {
        modalError(error.message);
      }).then(function () {
        setBusy(button, false);
      });
    };
    if (button) button.addEventListener("click", submit);
    var form = $("yt-topic-form");
    if (form) form.addEventListener("submit", function (event) { event.preventDefault(); submit(); });
  }

  /* ------------------------------------------------------------------ */
  /* Validation + competitor actions                                     */
  /* ------------------------------------------------------------------ */
  function criteriaInputs() {
    var meta = [
      ["rpm_potential", "RPM potential"],
      ["proven_demand", "Proven demand"],
      ["geographic_audience", "Geographic audience"],
      ["content_sustainability", "Content sustainability"],
      ["content_gap", "Content gap"]
    ];
    return meta.map(function (item) {
      return '<div class="yt-criterion"><b>' + esc(item[1]) + "</b>" +
        '<select data-yt-criterion="' + esc(item[0]) + '">' +
          [1, 2, 3, 4, 5].map(function (score) {
            return '<option value="' + score + '"' + (score === 3 ? " selected" : "") + ">" + score + "</option>";
          }).join("") +
        "</select></div>";
    }).join("");
  }

  function openValidationModal() {
    var pillars = (S.activeChannel && S.activeChannel.content_pillars) || [];
    var pillarOptions = '<option value="">No pillar</option>' + pillars.map(function (pillar) {
      return '<option value="' + esc(pillar.label) + '">' + esc(pillar.label) + "</option>";
    }).join("");
    openModal(
      "New Niche Validation",
      '<form id="yt-validation-form" class="yt-form" autocomplete="off">' +
        '<label class="yt-field"><span>Subject <em>required</em></span>' +
          '<input id="yt-val-subject" type="text" maxlength="160" placeholder="e.g. Quarterly estimated taxes for new freelancers" required></label>' +
        '<label class="yt-field"><span>Content pillar</span><select id="yt-val-pillar">' + pillarOptions + "</select></label>" +
        '<div class="yt-criteria-grid">' + criteriaInputs() + "</div>" +
        '<label class="yt-field"><span>Summary</span><textarea id="yt-val-summary" rows="2" maxlength="4000" placeholder="Why this niche, who it serves, what the angle is"></textarea></label>' +
        '<div class="yt-form-grid">' +
          '<label class="yt-field"><span>Source label (optional)</span><input id="yt-val-source-label" type="text" maxlength="200" placeholder="e.g. IRS Publication 334"></label>' +
          '<label class="yt-field"><span>Source URL (optional)</span><input id="yt-val-source-url" type="url" maxlength="500" placeholder="https://…"></label>' +
        "</div>" +
        '<p class="yt-hint">Scores are analyst estimates. Measured figures must come from a recorded source — nothing is invented.</p>' +
        '<p id="yt-modal-error" class="yt-form-error is-hidden"></p>' +
      "</form>",
      modalFoot('<button type="button" class="collart-btn" id="yt-val-create-go">Save Validation</button>')
    );
    var button = $("yt-val-create-go");
    var submit = function () {
      var subject = $("yt-val-subject");
      if (!subject || !subject.value.trim()) { modalError("A subject is required."); return; }
      modalError("");
      var criteria = {};
      Array.prototype.forEach.call(document.querySelectorAll("[data-yt-criterion]"), function (select) {
        criteria[select.getAttribute("data-yt-criterion")] = { score: parseInt(select.value, 10) || 3 };
      });
      var sources = [];
      var label = ($("yt-val-source-label") || {}).value || "";
      var url = ($("yt-val-source-url") || {}).value || "";
      if (label.trim()) sources.push({ label: label.trim(), url: url.trim() });
      setBusy(button, true, "Saving…");
      yt("/research", {
        method: "POST",
        body: {
          subject: subject.value.trim(),
          pillar: ($("yt-val-pillar") || {}).value || "",
          criteria: criteria,
          summary: ($("yt-val-summary") || {}).value || "",
          sources: sources
        }
      }).then(function () {
        closeModal();
        toast("Validation saved.");
        return Promise.all([loadValidations(), yt("/overview")]);
      }).then(function (results) {
        S.overview = results[1];
        renderResearch();
      }).catch(function (error) {
        modalError(error.message);
      }).then(function () {
        setBusy(button, false);
      });
    };
    if (button) button.addEventListener("click", submit);
    var form = $("yt-validation-form");
    if (form) form.addEventListener("submit", function (event) { event.preventDefault(); submit(); });
  }

  function validationToTopic(validationId) {
    yt("/research/" + encodeURIComponent(validationId) + "/to-topic", { method: "POST", body: {} })
      .then(function () {
        toast("Topic created from validation.");
        return Promise.all([loadTopics(), loadValidations()]);
      })
      .then(function () {
        renderTopicPipeline();
        renderTopics();
        renderResearch();
      })
      .catch(function (error) {
        panelError("yt-research-error", error.message);
      });
  }

  function deleteValidation(validationId) {
    yt("/research/" + encodeURIComponent(validationId), { method: "DELETE" })
      .then(function () {
        toast("Validation deleted.");
        return loadValidations();
      })
      .then(renderResearch)
      .catch(function (error) {
        panelError("yt-research-error", error.message);
      });
  }

  function openCompetitorModal() {
    openModal(
      "Add Competitor",
      '<form id="yt-competitor-form" class="yt-form" autocomplete="off">' +
        '<label class="yt-field"><span>Name <em>required</em></span><input id="yt-comp-name" type="text" maxlength="120" required></label>' +
        '<label class="yt-field"><span>Channel URL</span><input id="yt-comp-url" type="url" maxlength="300" placeholder="https://youtube.com/@…"></label>' +
        '<label class="yt-field"><span>Positioning</span><textarea id="yt-comp-positioning" rows="2" maxlength="600"></textarea></label>' +
        '<p id="yt-modal-error" class="yt-form-error is-hidden"></p>' +
      "</form>",
      modalFoot('<button type="button" class="collart-btn" id="yt-comp-create-go">Add Competitor</button>')
    );
    var button = $("yt-comp-create-go");
    var submit = function () {
      var name = $("yt-comp-name");
      if (!name || !name.value.trim()) { modalError("A name is required."); return; }
      modalError("");
      setBusy(button, true, "Adding…");
      yt("/competitors", {
        method: "POST",
        body: {
          name: name.value.trim(),
          url: ($("yt-comp-url") || {}).value || "",
          positioning: ($("yt-comp-positioning") || {}).value || ""
        }
      }).then(function () {
        closeModal();
        toast("Competitor added.");
        return loadCompetitors();
      }).then(renderResearch)
        .catch(function (error) { modalError(error.message); })
        .then(function () { setBusy(button, false); });
    };
    if (button) button.addEventListener("click", submit);
    var form = $("yt-competitor-form");
    if (form) form.addEventListener("submit", function (event) { event.preventDefault(); submit(); });
  }

  function openObservationModal(competitorId) {
    openModal(
      "Add Observation",
      '<form id="yt-observation-form" class="yt-form" autocomplete="off">' +
        '<label class="yt-field"><span>Observation <em>required</em></span><textarea id="yt-obs-note" rows="2" maxlength="600" placeholder="What did you actually see?" required></textarea></label>' +
        '<label class="yt-field"><span>Basis</span><select id="yt-obs-basis"><option value="observed">Observed</option><option value="assumption">Assumption</option></select></label>' +
        '<p id="yt-modal-error" class="yt-form-error is-hidden"></p>' +
      "</form>",
      modalFoot('<button type="button" class="collart-btn" id="yt-obs-create-go">Add</button>')
    );
    var button = $("yt-obs-create-go");
    var submit = function () {
      var note = $("yt-obs-note");
      if (!note || !note.value.trim()) { modalError("An observation is required."); return; }
      modalError("");
      setBusy(button, true, "Adding…");
      yt("/competitors/" + encodeURIComponent(competitorId) + "/observations", {
        method: "POST",
        body: { note: note.value.trim(), basis: ($("yt-obs-basis") || {}).value || "observed" }
      }).then(function () {
        closeModal();
        toast("Observation added.");
        return loadCompetitors();
      }).then(renderResearch)
        .catch(function (error) { modalError(error.message); })
        .then(function () { setBusy(button, false); });
    };
    if (button) button.addEventListener("click", submit);
    var form = $("yt-observation-form");
    if (form) form.addEventListener("submit", function (event) { event.preventDefault(); submit(); });
  }

  /* ------------------------------------------------------------------ */
  /* Script actions                                                      */
  /* ------------------------------------------------------------------ */
  function approvedTopicOptions() {
    var approved = S.topics.filter(function (topic) {
      return topic.status === "approved" || topic.status === "scripted" || topic.status === "in_production";
    });
    return approved.map(function (topic) {
      return '<option value="' + esc(topic.id) + '">' + esc(topic.title) + "</option>";
    }).join("");
  }

  function refreshTopicsForScripts() {
    return loadTopics().then(function () { return approvedTopicOptions(); });
  }

  function openScriptModal() {
    refreshTopicsForScripts().then(function (options) {
      if (!options) {
        toast("No approved topics. Approve a validated topic (18/25+) first.");
        return;
      }
      openModal(
        "New Script",
        '<form id="yt-script-form" class="yt-form" autocomplete="off">' +
          '<label class="yt-field"><span>Approved topic <em>required</em></span>' +
            '<select id="yt-script-topic"><option value="">No topic (standalone)</option>' + options + "</select></label>" +
          '<label class="yt-field"><span>Title (defaults to topic title)</span><input id="yt-script-title" type="text" maxlength="160"></label>' +
          '<label class="yt-field"><span>Target duration (minutes)</span><input id="yt-script-duration" type="number" min="1" max="60" value="12"></label>' +
          '<p class="yt-hint">Scripts are written from approved topics; the backend enforces this.</p>' +
          '<p id="yt-modal-error" class="yt-form-error is-hidden"></p>' +
        "</form>",
        modalFoot('<button type="button" class="collart-btn" id="yt-script-create-go">Create Script</button>')
      );
      var button = $("yt-script-create-go");
      var submit = function () {
        modalError("");
        setBusy(button, true, "Creating…");
        var body = {};
        var topic = ($("yt-script-topic") || {}).value || "";
        var title = ($("yt-script-title") || {}).value || "";
        var duration = ($("yt-script-duration") || {}).value || "";
        if (topic) body.topic_id = topic;
        if (title.trim()) body.title = title.trim();
        if (duration) body.target_duration_min = parseInt(duration, 10);
        yt("/scripts", { method: "POST", body: body }).then(function () {
          closeModal();
          toast("Script created.");
          return loadScripts();
        }).then(function () {
          renderScriptList();
          renderScriptEditor();
        }).catch(function (error) {
          modalError(error.message);
        }).then(function () {
          setBusy(button, false);
        });
      };
      if (button) button.addEventListener("click", submit);
      var form = $("yt-script-form");
      if (form) form.addEventListener("submit", function (event) { event.preventDefault(); submit(); });
    }).catch(function (error) {
      panelError("yt-scripts-error", error.message);
    });
  }

  function openGenerateModal() {
    Promise.all([refreshTopicsForScripts(), loadLLM()]).then(function (results) {
      var options = results[0];
      var llm = results[1] || {};
      if (!llm.configured) {
        toast(llm.hint_setup || "The text-generation backend is not configured. Check Settings.");
        return;
      }
      if (!options) {
        toast("No approved topics. Approve a validated topic (18/25+) first.");
        return;
      }
      openModal(
        "Generate Script with AI",
        '<form id="yt-generate-form" class="yt-form" autocomplete="off">' +
          '<p class="yt-hint">Composes the channel master prompt + Scriptwriter extension + topic context, then calls <b>' +
            esc(llm.provider_label || llm.provider) + "</b> with model <b>" + esc(llm.model) + "</b>.</p>" +
          '<label class="yt-field"><span>Approved topic <em>required</em></span><select id="yt-gen-topic">' + options + "</select></label>" +
          '<label class="yt-field"><span>Target duration (minutes)</span><input id="yt-gen-duration" type="number" min="1" max="60" value="12"></label>' +
          '<label class="yt-field"><span>Extra instructions (optional)</span><textarea id="yt-gen-notes" rows="2" maxlength="2000" placeholder="Angle adjustments, examples to include…"></textarea></label>' +
          '<p class="yt-hint">Generation runs server-side and can take a few minutes. Malformed model output is reported, never saved.</p>' +
          '<p id="yt-modal-error" class="yt-form-error is-hidden"></p>' +
        "</form>",
        modalFoot('<button type="button" class="collart-btn" id="yt-gen-go">Generate</button>')
      );
      var button = $("yt-gen-go");
      var submit = function () {
        modalError("");
        setBusy(button, true, "Generating… this can take minutes");
        var topic = ($("yt-gen-topic") || {}).value || "";
        var duration = ($("yt-gen-duration") || {}).value || "";
        var notes = ($("yt-gen-notes") || {}).value || "";
        var body = { topic_id: topic };
        if (duration) body.target_duration_min = parseInt(duration, 10);
        if (notes.trim()) body.instructions = notes.trim();
        yt("/scripts/generate", { method: "POST", body: body }).then(function () {
          closeModal();
          toast("Script generated.");
          return loadScripts();
        }).then(function () {
          renderScriptList();
          renderScriptEditor();
        }).catch(function (error) {
          modalError(error.message);
        }).then(function () {
          setBusy(button, false);
        });
      };
      if (button) button.addEventListener("click", submit);
      var form = $("yt-generate-form");
      if (form) form.addEventListener("submit", function (event) { event.preventDefault(); submit(); });
    }).catch(function (error) {
      panelError("yt-scripts-error", error.message);
    });
  }

  function setScriptStatus(scriptId, status) {
    yt("/scripts/" + encodeURIComponent(scriptId) + "/status", { method: "POST", body: { status: status } })
      .then(function (payload) {
        S.currentScript = payload.script || S.currentScript;
        toast("Script updated.");
        return loadScripts();
      })
      .then(function () {
        renderScriptList();
        renderScriptEditor();
      })
      .catch(function (error) {
        panelError("yt-scripts-error", error.message);
      });
  }

  function deleteScript(scriptId) {
    yt("/scripts/" + encodeURIComponent(scriptId), { method: "DELETE" })
      .then(function () {
        S.currentScript = null;
        toast("Script deleted.");
        return loadScripts();
      })
      .then(function () {
        renderScriptList();
        renderScriptEditor();
      })
      .catch(function (error) {
        panelError("yt-scripts-error", error.message);
      });
  }

  function restoreScriptVersion(scriptId, versionId) {
    yt("/scripts/" + encodeURIComponent(scriptId) + "/versions/restore", { method: "POST", body: { version_id: versionId } })
      .then(function (payload) {
        S.currentScript = payload.script || null;
        closeModal();
        toast("Version restored as a new version.");
        return loadScripts();
      })
      .then(function () {
        renderScriptList();
        renderScriptEditor();
      })
      .catch(function (error) {
        toast(error.message);
      });
  }

  /* ------------------------------------------------------------------ */
  /* Channel actions                                                     */
  /* ------------------------------------------------------------------ */
  function openChannelModal() {
    openModal(
      "New Channel Profile",
      '<form id="yt-channel-modal" class="yt-form" autocomplete="off">' +
        '<label class="yt-field"><span>Name <em>required</em></span><input id="yt-chm-name" type="text" maxlength="120" placeholder="e.g. The Business Money Blueprint" required></label>' +
        '<label class="yt-field"><span>Tagline</span><input id="yt-chm-tagline" type="text" maxlength="200"></label>' +
        '<label class="yt-field"><span>Content niche</span><input id="yt-chm-niche" type="text" maxlength="300" placeholder="e.g. Small business finance and tax education"></label>' +
        '<p class="yt-hint">The first channel seeds automatically from the Business Money Blueprint catalog on first use.</p>' +
        '<p id="yt-modal-error" class="yt-form-error is-hidden"></p>' +
      "</form>",
      modalFoot('<button type="button" class="collart-btn" id="yt-chm-create-go">Create Channel</button>')
    );
    var button = $("yt-chm-create-go");
    var submit = function () {
      var name = $("yt-chm-name");
      if (!name || !name.value.trim()) { modalError("A name is required."); return; }
      modalError("");
      setBusy(button, true, "Creating…");
      yt("/channels", {
        method: "POST",
        body: {
          name: name.value.trim(),
          tagline: ($("yt-chm-tagline") || {}).value || "",
          content_niche: ($("yt-chm-niche") || {}).value || ""
        }
      }).then(function () {
        closeModal();
        toast("Channel created.");
        return loadChannels();
      }).then(function () {
        renderChannelList();
        renderChannelEditor();
      }).catch(function (error) {
        modalError(error.message);
      }).then(function () {
        setBusy(button, false);
      });
    };
    if (button) button.addEventListener("click", submit);
    var form = $("yt-channel-modal");
    if (form) form.addEventListener("submit", function (event) { event.preventDefault(); submit(); });
  }

  function selectChannel(channelId) {
    S.selectedChannelId = channelId;
    renderChannelList();
    renderChannelEditor();
  }

  function activateChannel(channelId) {
    yt("/channels/" + encodeURIComponent(channelId), { method: "POST", body: { action: "activate" } })
      .then(function (payload) {
        S.activeChannel = (payload && payload.channel) || S.activeChannel;
        toast("Channel activated.");
        return loadChannels();
      })
      .then(function () {
        renderChannelList();
        renderChannelEditor();
        renderCreate();
      })
      .catch(function (error) {
        panelError("yt-channels-error", error.message);
      });
  }

  function saveChannel(channelId) {
    var error = $("yt-ch-error");
    function setError(message) {
      if (error) {
        error.textContent = message || "";
        error.classList.toggle("is-hidden", !message);
      }
    }
    setError("");
    var name = ($("yt-ch-name") || {}).value || "";
    if (!name.trim()) { setError("A name is required."); return; }
    yt("/channels/" + encodeURIComponent(channelId), {
      method: "PATCH",
      body: {
        name: name.trim(),
        tagline: ($("yt-ch-tagline") || {}).value || "",
        handle: ($("yt-ch-handle") || {}).value || "",
        content_niche: ($("yt-ch-niche") || {}).value || "",
        description: ($("yt-ch-description") || {}).value || "",
        target_audience: ($("yt-ch-audience") || {}).value || ""
      }
    }).then(function (payload) {
      toast("Channel saved.");
      if (payload && payload.channel) S.activeChannel = payload.channel;
      return loadChannels();
    }).then(function () {
      renderChannelList();
      renderChannelEditor();
    }).catch(setError);
  }

  function deleteChannel(channelId) {
    yt("/channels/" + encodeURIComponent(channelId), { method: "DELETE" })
      .then(function () {
        toast("Channel deleted.");
        if (S.selectedChannelId === channelId) S.selectedChannelId = null;
        return loadChannels();
      })
      .then(function () {
        renderChannelList();
        renderChannelEditor();
      })
      .catch(function (error) {
        panelError("yt-channels-error", error.message);
      });
  }

  function openPromptVersions(channelId) {
    loadPromptVersions(channelId).then(function () {
      renderChannelEditor();
      renderPromptVersions(channelId);
    }).catch(function (error) {
      panelError("yt-channels-error", error.message);
    });
  }

  function openNewPromptModal(channelId) {
    openModal(
      "New Master Prompt Version",
      '<form id="yt-prompt-form" class="yt-form" autocomplete="off">' +
        '<label class="yt-field"><span>Prompt body <em>required</em></span>' +
          '<textarea id="yt-prompt-body" rows="10" maxlength="200000" placeholder="Paste or write the new master prompt. Editing always creates a new immutable version."></textarea></label>' +
        '<label class="yt-field"><span>Note</span><input id="yt-prompt-note" type="text" maxlength="200" placeholder="What changed and why"></label>' +
        '<p id="yt-modal-error" class="yt-form-error is-hidden"></p>' +
      "</form>",
      modalFoot('<button type="button" class="collart-btn" id="yt-prompt-create-go">Save Version</button>')
    );
    var button = $("yt-prompt-create-go");
    var submit = function () {
      var body = ($("yt-prompt-body") || {}).value || "";
      if (!body.trim()) { modalError("The prompt body is required."); return; }
      modalError("");
      setBusy(button, true, "Saving…");
      yt("/channels/" + encodeURIComponent(channelId) + "/prompt", {
        method: "POST",
        body: { body: body, note: ($("yt-prompt-note") || {}).value || "" }
      }).then(function () {
        closeModal();
        toast("Prompt version saved.");
        return loadChannels();
      }).then(function () {
        renderChannelList();
        renderChannelEditor();
        return loadPromptVersions(channelId);
      }).then(function () {
        renderPromptVersions(channelId);
      }).catch(function (error) {
        modalError(error.message);
      }).then(function () {
        setBusy(button, false);
      });
    };
    if (button) button.addEventListener("click", submit);
    var form = $("yt-prompt-form");
    if (form) form.addEventListener("submit", function (event) { event.preventDefault(); submit(); });
  }

  function setDefaultPrompt(channelId, versionId) {
    yt("/channels/" + encodeURIComponent(channelId) + "/prompt/set-default", { method: "POST", body: { version_id: versionId } })
      .then(function () {
        toast("Default prompt version set.");
        return loadChannels();
      })
      .then(function () {
        renderChannelList();
        renderChannelEditor();
      })
      .catch(function (error) {
        panelError("yt-channels-error", error.message);
      });
  }

  function restorePrompt(channelId, versionId) {
    yt("/channels/" + encodeURIComponent(channelId) + "/prompt/restore", { method: "POST", body: { version_id: versionId } })
      .then(function () {
        toast("Restored as a new version. History is immutable.");
        return Promise.all([loadChannels(), loadPromptVersions(channelId)]);
      })
      .then(function () {
        renderChannelList();
        renderChannelEditor();
        renderPromptVersions(channelId);
      })
      .catch(function (error) {
        panelError("yt-channels-error", error.message);
      });
  }

  /* ------------------------------------------------------------------ */
  /* LLM config                                                          */
  /* ------------------------------------------------------------------ */
  function saveLLM() {
    var select = $("yt-llm-provider");
    var modelInput = $("yt-llm-model");
    var body = { provider: (select && select.value) || "opencode" };
    var model = modelInput && modelInput.value.trim();
    if (model) body.model = model;
    yt("/llm/config", { method: "POST", body: body })
      .then(function (payload) {
        S.llm = payload.llm || S.llm;
        toast("Text generation profile saved.");
        renderLLMSettings();
        setText("yt-create-model", (S.llm && S.llm.model) || "GLM-5.3-Flash");
      })
      .catch(function (error) {
        toast(error.message);
      });
  }

  /* ------------------------------------------------------------------ */
  /* Navigation                                                          */
  /* ------------------------------------------------------------------ */
  function clearError() {
    S.lastError = null;
    var banner = $("yt-error-banner");
    if (banner) banner.classList.add("is-hidden");
  }

  function showError(message) {
    S.lastError = message;
    var banner = $("yt-error-banner");
    var text = $("yt-error-text");
    if (text) text.textContent = message || "Something went wrong.";
    if (banner) banner.classList.remove("is-hidden");
  }

  var SECTION_LOADERS = {
    create: loadOverview,
    projects: loadProjectsPage,
    backlog: loadBacklogPage,
    publishing: function () { return loadFeatures().then(renderPublishing); },
    analytics: function () { return loadFeatures().then(renderAnalytics); },
    intelligence: function () { icons(); },
    settings: loadSettingsPage
  };

  function setSection(section) {
    S.section = WS.normalizeSection("youtube", section);
    /* Exactly one panel is visible — this also fixes the old double-panel quirk. */
    Array.prototype.forEach.call(document.querySelectorAll("[data-yt-panel]"), function (panel) {
      var name = panel.getAttribute("data-yt-panel");
      panel.classList.toggle("is-hidden", name !== S.section);
    });
    Array.prototype.forEach.call(document.querySelectorAll("[data-yt-section]"), function (button) {
      var active = button.getAttribute("data-yt-section") === S.section;
      button.classList.toggle("is-active", active);
      button.setAttribute("aria-current", active ? "page" : "false");
    });
    setText("yt-section-title", WS.sectionLabel(S.section));
    setText("yt-section-crumb", WS.sectionLabel(S.section));
    var loader = SECTION_LOADERS[S.section];
    if (loader) loader();
    else renderPlaceholder(S.section);
  }

  function show(section) {
    setSection(section);
  }

  function go(section) {
    if (window.StudioWorkspaces && window.StudioWorkspaces.go) {
      window.StudioWorkspaces.go("youtube", section);
    } else {
      setSection(section);
    }
  }

  function setBacklogTab(tab) {
    S.backlogTab = WS.normalizeBacklogTab(tab);
    Array.prototype.forEach.call(document.querySelectorAll("[data-yt-btab]"), function (button) {
      button.classList.toggle("is-active", button.getAttribute("data-yt-btab") === S.backlogTab);
    });
    Array.prototype.forEach.call(document.querySelectorAll("[data-yt-btab-panel]"), function (panel) {
      panel.classList.toggle("is-hidden", panel.getAttribute("data-yt-btab-panel") !== S.backlogTab);
    });
    if (S.backlogTab === "research") {
      Promise.all([loadValidations(), loadCompetitors()]).then(renderResearch);
    }
  }

  function setProjectTab(tab) {
    S.projectTab = WS.normalizeProjectTab(tab);
    Array.prototype.forEach.call(document.querySelectorAll("[data-yt-ptab]"), function (button) {
      button.classList.toggle("is-active", button.getAttribute("data-yt-ptab") === S.projectTab);
    });
    Array.prototype.forEach.call(document.querySelectorAll("[data-yt-ptab-panel]"), function (panel) {
      panel.classList.toggle("is-hidden", panel.getAttribute("data-yt-ptab-panel") !== S.projectTab);
    });
    if (S.projectTab === "scripts") loadScriptsPage();
  }

  function sectionMeta(id) {
    var sections = (S.descriptor && S.descriptor.sections) || [];
    for (var i = 0; i < sections.length; i += 1) {
      if (sections[i] && sections[i].id === id) return sections[i];
    }
    return null;
  }

  function renderPlaceholder(section) {
    var meta = sectionMeta(section);
    var label = (meta && meta.label) || WS.sectionLabel(section);
    var phase = meta && meta.stage ? "Phase " + meta.stage : "a later phase";
    setText("yt-placeholder-title", label);
    setText("yt-placeholder-note",
      label + " is not available in this build yet. It is scheduled for " + phase +
      ", and no placeholder data is shown in the meantime.");
    setText("yt-placeholder-stage", meta && meta.stage ? "Phase " + meta.stage : "Planned");
    icons();
  }

  function refresh() {
    var loader = SECTION_LOADERS[S.section];
    if (loader) loader();
  }

  /* ------------------------------------------------------------------ */
  /* Wiring                                                              */
  /* ------------------------------------------------------------------ */
  function bind() {
    Array.prototype.forEach.call(document.querySelectorAll("[data-yt-section]"), function (button) {
      button.addEventListener("click", function () {
        go(button.getAttribute("data-yt-section"));
      });
    });

    Array.prototype.forEach.call(document.querySelectorAll("[data-yt-btab]"), function (button) {
      button.addEventListener("click", function () {
        setBacklogTab(button.getAttribute("data-yt-btab"));
      });
    });

    Array.prototype.forEach.call(document.querySelectorAll("[data-yt-ptab]"), function (button) {
      button.addEventListener("click", function () {
        setProjectTab(button.getAttribute("data-yt-ptab"));
      });
    });

    /* Prompt-first: the composer is the creation path on Create. Only the
       Projects section keeps a contextual "New Video Project" action until
       Phase 7 transfers approved packages into projects automatically. */
    var projectsCreate = $("yt-projects-create");
    if (projectsCreate) projectsCreate.addEventListener("click", openCreateProject);

    var composerSubmit = $("yt-create-submit");
    if (composerSubmit) composerSubmit.addEventListener("click", submitCreate);

    var composerPrompt = $("yt-create-prompt");
    if (composerPrompt) {
      composerPrompt.addEventListener("keydown", function (event) {
        if ((event.metaKey || event.ctrlKey) && event.key === "Enter") submitCreate();
      });
    }

    var refreshButton = $("yt-refresh");
    if (refreshButton) refreshButton.addEventListener("click", function () { refresh(); });

    var projectsSearch = $("yt-projects-search");
    if (projectsSearch) {
      projectsSearch.addEventListener("input", function () {
        S.query = projectsSearch.value.trim();
        if (S.section === "projects") loadProjectsPage();
      });
    }

    var projectsStatus = $("yt-projects-status");
    if (projectsStatus) {
      projectsStatus.addEventListener("change", function () {
        S.status = projectsStatus.value;
        if (S.section === "projects") loadProjectsPage();
      });
    }

    var projectsArchived = $("yt-projects-archived");
    if (projectsArchived) {
      projectsArchived.addEventListener("change", function () {
        S.includeArchived = !!projectsArchived.checked;
        if (S.section === "projects") loadProjectsPage();
      });
    }

    var topicsSearch = $("yt-topics-search");
    if (topicsSearch) {
      topicsSearch.addEventListener("input", function () {
        S.topicQuery.q = topicsSearch.value.trim();
        loadTopics().then(function () { renderTopicPipeline(); renderTopics(); });
      });
    }
    ["yt-topics-status", "yt-topics-pillar", "yt-topics-priority"].forEach(function (id) {
      var select = $(id);
      if (!select) return;
      var key = id.replace("yt-topics-", "");
      select.addEventListener("change", function () {
        S.topicQuery[key] = select.value;
        loadTopics().then(function () { renderTopicPipeline(); renderTopics(); });
      });
    });
    var minScore = $("yt-topics-min-score");
    if (minScore) {
      minScore.addEventListener("change", function () {
        S.topicQuery.min_score = minScore.value;
        loadTopics().then(function () { renderTopicPipeline(); renderTopics(); });
      });
    }

    var topicCreate = $("yt-topic-create");
    if (topicCreate) topicCreate.addEventListener("click", openTopicModal);

    var validationCreate = $("yt-validation-create");
    if (validationCreate) validationCreate.addEventListener("click", openValidationModal);

    var scriptCreate = $("yt-script-create");
    if (scriptCreate) scriptCreate.addEventListener("click", openScriptModal);

    var scriptGenerate = $("yt-script-generate");
    if (scriptGenerate) scriptGenerate.addEventListener("click", openGenerateModal);

    var scriptsSearch = $("yt-scripts-search");
    if (scriptsSearch) {
      scriptsSearch.addEventListener("input", function () {
        S.scriptQuery.q = scriptsSearch.value.trim();
        loadScriptsPage();
      });
    }

    var scriptsStatus = $("yt-scripts-status");
    if (scriptsStatus) {
      scriptsStatus.addEventListener("change", function () {
        S.scriptQuery.status = scriptsStatus.value;
        loadScriptsPage();
      });
    }

    var channelCreate = $("yt-channel-create");
    if (channelCreate) channelCreate.addEventListener("click", openChannelModal);

    var llmSave = $("yt-llm-save");
    if (llmSave) llmSave.addEventListener("click", saveLLM);

    var memoryGalaxy = $("yt-open-memory-galaxy");
    if (memoryGalaxy) {
      memoryGalaxy.addEventListener("click", function () {
        if (typeof window.switchMainView === "function") window.switchMainView("memory-galaxy");
      });
    }

    var adsSetup = $("yt-open-ads-setup");
    if (adsSetup) {
      adsSetup.addEventListener("click", function () {
        if (window.StudioWorkspaces && window.StudioWorkspaces.go) window.StudioWorkspaces.go("ads", "setup");
      });
    }

    document.addEventListener("click", function (event) {
      var target = event.target;

      var close = target.closest("[data-yt-close]");
      if (close) { closeModal(); return; }

      var open = target.closest("[data-yt-open]");
      if (open) { openProject(open.getAttribute("data-yt-open")); return; }

      var row = target.closest("[data-yt-project]");
      if (row && !target.closest("[data-yt-archive],[data-yt-restore],[data-yt-delete]")) {
        openProject(row.getAttribute("data-yt-project"));
        return;
      }

      var emptyAction = target.closest("[data-yt-empty-action]");
      if (emptyAction) {
        var action = emptyAction.getAttribute("data-yt-empty-action");
        if (action === "create") openCreateProject();
        else if (action === "topic") openTopicModal();
        else if (action === "validation") openValidationModal();
        return;
      }

      var archive = target.closest("[data-yt-archive]");
      if (archive) {
        updateProject(archive.getAttribute("data-yt-archive"), { status: "archived" }, "Project archived.");
        return;
      }

      var restore = target.closest("[data-yt-restore]");
      if (restore) {
        updateProject(restore.getAttribute("data-yt-restore"), { status: "draft" }, "Project restored.");
        return;
      }

      var remove = target.closest("[data-yt-delete]");
      if (remove) { confirmDelete(remove.getAttribute("data-yt-delete")); return; }

      var scriptRow = target.closest("[data-yt-script]");
      if (scriptRow) {
        var scriptId = scriptRow.getAttribute("data-yt-script");
        for (var i = 0; i < S.scripts.length; i += 1) {
          if (S.scripts[i].id === scriptId) { S.currentScript = S.scripts[i]; break; }
        }
        renderScriptList();
        renderScriptEditor();
        return;
      }

      var scriptStatus = target.closest("[data-yt-script-status]");
      if (scriptStatus && S.currentScript) {
        setScriptStatus(S.currentScript.id, scriptStatus.getAttribute("data-yt-script-status"));
        return;
      }

      var scriptVersions = target.closest("[data-yt-script-versions]");
      if (scriptVersions) { renderScriptVersions(scriptVersions.getAttribute("data-yt-script-versions")); return; }

      var versionRestore = target.closest("[data-yt-version-restore]");
      if (versionRestore && S.currentScript) {
        restoreScriptVersion(S.currentScript.id, versionRestore.getAttribute("data-yt-version-restore"));
        return;
      }

      var scriptDelete = target.closest("[data-yt-script-delete]");
      if (scriptDelete) { deleteScript(scriptDelete.getAttribute("data-yt-script-delete")); return; }

      var topicStatus = target.closest("[data-yt-topic-status]");
      if (topicStatus) {
        var next = topicStatus.getAttribute("data-yt-topic-status");
        if (next === "__script__") {
          openScriptModal();
        } else {
          setTopicStatus(topicStatus.getAttribute("data-yt-topic-id"), next);
        }
        return;
      }

      var topicDelete = target.closest("[data-yt-topic-delete]");
      if (topicDelete) { deleteTopic(topicDelete.getAttribute("data-yt-topic-delete")); return; }

      var validationTopic = target.closest("[data-yt-validation-topic]");
      if (validationTopic) { validationToTopic(validationTopic.getAttribute("data-yt-validation-topic")); return; }

      var validationDelete = target.closest("[data-yt-validation-delete]");
      if (validationDelete) { deleteValidation(validationDelete.getAttribute("data-yt-validation-delete")); return; }

      var addCompetitor = target.closest('[data-yt-action="add-competitor"]');
      if (addCompetitor) { openCompetitorModal(); return; }

      var observe = target.closest("[data-yt-competitor-observe]");
      if (observe) { openObservationModal(observe.getAttribute("data-yt-competitor-observe")); return; }

      var channelRow = target.closest("[data-yt-channel]");
      if (channelRow && !target.closest("[data-yt-channel-activate]")) {
        selectChannel(channelRow.getAttribute("data-yt-channel"));
        return;
      }

      var channelActivate = target.closest("[data-yt-channel-activate]");
      if (channelActivate) { activateChannel(channelActivate.getAttribute("data-yt-channel-activate")); return; }

      var channelPrompt = target.closest("[data-yt-channel-prompt]");
      if (channelPrompt) { openPromptVersions(channelPrompt.getAttribute("data-yt-channel-prompt")); return; }

      var channelNewPrompt = target.closest("[data-yt-channel-new-prompt]");
      if (channelNewPrompt) { openNewPromptModal(channelNewPrompt.getAttribute("data-yt-channel-new-prompt")); return; }

      var promptDefault = target.closest("[data-yt-prompt-default]");
      if (promptDefault && S.selectedChannelId) {
        setDefaultPrompt(S.selectedChannelId, promptDefault.getAttribute("data-yt-prompt-default"));
        return;
      }

      var promptRestore = target.closest("[data-yt-prompt-restore]");
      if (promptRestore && S.selectedChannelId) {
        restorePrompt(S.selectedChannelId, promptRestore.getAttribute("data-yt-prompt-restore"));
        return;
      }

      var channelDelete = target.closest("[data-yt-channel-delete]");
      if (channelDelete) { deleteChannel(channelDelete.getAttribute("data-yt-channel-delete")); return; }

      var modal = $("yt-modal");
      if (modal && event.target === modal) closeModal();
    });

    document.addEventListener("keydown", function (event) {
      if (event.key === "Escape") closeModal();
    });
  }

  function init() {
    if (S.initialized) return;
    S.initialized = true;
    bind();
    /* Populate topic filter selects once the channel context is known. */
    loadOverviewEditorial().then(function () {
      var statusSelect = $("yt-topics-status");
      if (statusSelect && !statusSelect.options.length) {
        statusSelect.innerHTML = '<option value="">All statuses</option>';
      }
      if (statusSelect && S.topicStatuses.length) {
        statusSelect.innerHTML = '<option value="">All statuses</option>' + S.topicStatuses.map(function (status) {
          return '<option value="' + esc(status) + '">' + esc(TOPIC_LABELS[status] || status) + "</option>";
        }).join("");
      }
      var pillarSelect = $("yt-topics-pillar");
      if (pillarSelect && S.activeChannel) {
        pillarSelect.innerHTML = '<option value="">All pillars</option>' + ((S.activeChannel.content_pillars || []).map(function (pillar) {
          return '<option value="' + esc(pillar.label) + '">' + esc(pillar.label) + "</option>";
        }).join("") || "");
      }
    });
  }

  window.YouTubeStudio = {
    init: init,
    show: show,
    go: go,
    refresh: refresh,
    openCreateProject: openCreateProject,
    setBacklogTab: setBacklogTab,
    setProjectTab: setProjectTab,
    section: function () { return S.section; },
    projects: function () { return S.projects.slice(); },
    state: S
  };

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
