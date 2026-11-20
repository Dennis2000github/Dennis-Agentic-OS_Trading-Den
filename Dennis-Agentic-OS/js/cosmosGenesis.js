/* =====================================================================
   GENESIS · the COSMOS production dashboard.

   GENESIS is a system-level production workflow, not a sixth intelligence
   agent. It owns production intake, project workspaces, final rendering,
   quality review, owner approval and publishing for BOTH divisions
   (Ads + YouTube). Intelligence stays with the five agent dashboards; this
   module renders into the same COSMOS host when the GENESIS tab is active.

   Every count, state and preview shown here comes from the persisted
   backend records (data/cosmos/genesis/* + the Studio media library).
   Nothing is simulated: a render is only "complete" when ffmpeg produced a
   file, and a publication is only "succeeded" when the destination API
   confirmed a remote id.
   ===================================================================== */
(function () {
  "use strict";

  var C = window.CosmosState;
  if (!C) return;

  var S = {
    bound: false,
    token: null,
    activeTab: "overview",
    loading: false,
    error: null,
    busy: {},
    overview: null,
    engine: null,
    queue: [],
    queueTotal: 0,
    queueFilter: "all",
    queueQuery: "",
    projects: [],
    projectFilter: "all",
    projectQuery: "",
    activeProjectId: null,
    project: null,
    renders: [],
    publications: [],
    destinations: [],
    media: [],
    mediaById: {},
    mediaKind: "video",
    mediaQuery: "",
    attach: null,
    timer: null
  };

  var TABS = [
    ["overview", "Overview", "gauge"],
    ["queue", "Production Queue", "list-checks"],
    ["projects", "Projects", "clapperboard"],
    ["renders", "Render Center", "film"],
    ["publishing", "Publishing", "upload-cloud"]
  ];

  var PROJECT_STATUS_LABELS = {
    in_production: "In production",
    rendering: "Rendering",
    quality_review: "Quality review",
    awaiting_approval: "Awaiting approval",
    approved: "Approved",
    published: "Published",
    failed: "Failed",
    archived: "Archived"
  };

  var RENDER_STATUS_LABELS = {
    queued: "Queued",
    preparing: "Preparing",
    waiting_media: "Waiting for media",
    assembling: "Assembling",
    complete: "Complete",
    failed: "Failed",
    cancelled: "Cancelled"
  };

  var PUB_STATUS_LABELS = {
    awaiting_approval: "Awaiting approval",
    scheduled: "Scheduled",
    publishing: "Publishing",
    succeeded: "Published",
    failed: "Failed",
    cancelled: "Cancelled"
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

  function attr(value) { return esc(value); }

  function icons() {
    if (window.lucide && window.lucide.createIcons) {
      try { window.lucide.createIcons(); } catch (err) { /* icons are optional */ }
    }
  }

  function toast(message) {
    if (window.Cosmos && window.Cosmos.toast) return window.Cosmos.toast(message);
    if (window.Studio && window.Studio.toast) return window.Studio.toast(message);
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

  function fmtWhen(iso) { return C.formatWhen(iso); }

  function badge(status, labels) {
    var tone = C.statusToneExtended(status);
    var text = (labels && labels[status]) || status || "—";
    return '<span class="cosmos-badge is-' + tone + '">' + esc(text) + "</span>";
  }

  function mediaUrl(mediaId) {
    if (!mediaId) return "";
    var base = "/api/studio/media/" + encodeURIComponent(mediaId);
    return S.token ? base + "?token=" + encodeURIComponent(S.token) : base;
  }

  function mediaPreview(record) {
    if (!record) return "";
    if (record.kind === "video") {
      return '<video class="genesis-media" src="' + attr(mediaUrl(record.id)) + '" muted preload="metadata" controls></video>';
    }
    if (record.kind === "image") {
      return '<img class="genesis-media" src="' + attr(mediaUrl(record.id)) + '" alt="' + esc(record.title || "media") + '">';
    }
    if (record.kind === "audio") {
      return '<audio class="genesis-audio" controls src="' + attr(mediaUrl(record.id)) + '"></audio>';
    }
    return "";
  }

  function ensureToken() {
    if (S.token) return Promise.resolve(S.token);
    return api("/api/auth/session").then(function (data) {
      S.token = (data && data.token) || null;
      return S.token;
    }).catch(function () { return null; });
  }

  // -----------------------------------------------------------------------
  // Data loading
  // -----------------------------------------------------------------------
  function loadOverview() {
    return api("/api/cosmos/genesis/overview").then(function (data) {
      S.overview = data;
      S.engine = data.engine || null;
      S.destinations = data.destinations || [];
      S.projects = data.projects || [];
      S.renders = data.renders || [];
      S.publications = data.publications || [];
    });
  }

  function loadQueue() {
    var params = new URLSearchParams();
    if (S.queueFilter === "ads" || S.queueFilter === "youtube") params.set("division", S.queueFilter);
    if (S.queueQuery) params.set("q", S.queueQuery);
    var suffix = params.toString() ? "?" + params.toString() : "";
    return api("/api/cosmos/genesis/queue" + suffix).then(function (data) {
      S.queue = data.queue || [];
      S.queueTotal = data.total || 0;
    });
  }

  function loadProjects() {
    var params = new URLSearchParams();
    if (S.projectFilter === "ads" || S.projectFilter === "youtube") params.set("division", S.projectFilter);
    if (S.projectQuery) params.set("q", S.projectQuery);
    var suffix = params.toString() ? "?" + params.toString() : "";
    return api("/api/cosmos/genesis/projects" + suffix).then(function (data) {
      S.projects = data.projects || [];
    });
  }

  function loadProject(projectId) {
    return api("/api/cosmos/genesis/projects/" + encodeURIComponent(projectId)).then(function (data) {
      S.project = data.project || null;
      S.activeProjectId = S.project ? S.project.id : null;
      if (Array.isArray(data.renders)) S.renders = data.renders;
      if (Array.isArray(data.publications)) S.publications = data.publications;
      cacheMedia((data.media && data.media.media) || []);
    });
  }

  function cacheMedia(records) {
    (records || []).forEach(function (record) {
      if (!record || !record.id) return;
      S.mediaById[record.id] = record;
    });
  }

  function loadRenders() {
    return api("/api/cosmos/genesis/renders?limit=60").then(function (data) {
      S.renders = data.renders || [];
    });
  }

  function loadPublications() {
    return api("/api/cosmos/genesis/publications?limit=60").then(function (data) {
      S.publications = data.publications || [];
    });
  }

  function loadDestinations() {
    return api("/api/cosmos/genesis/publishing/destinations").then(function (data) {
      S.destinations = data.destinations || [];
    });
  }

  function loadMedia() {
    var params = new URLSearchParams();
    if (S.mediaKind && S.mediaKind !== "all") params.set("kind", S.mediaKind);
    if (S.mediaQuery) params.set("query", S.mediaQuery);
    params.set("role", "all");
    params.set("limit", "48");
    return api("/api/studio/library?" + params.toString()).then(function (data) {
      S.media = data.items || [];
      cacheMedia(S.media);
    });
  }

  function loadEngine() {
    return api("/api/cosmos/genesis/render-engine").then(function (data) {
      S.engine = data.engine || null;
    });
  }

  function refreshActiveTab() {
    // Media previews embed the session token in their URLs; once it arrives
    // (first paint may race the session call) repaint so <video>/<img>
    // elements point at authorized URLs.
    if (!S.token) {
      ensureToken().then(function (token) {
        if (token && (S.project || S.renders.length)) repaint();
      });
    }
    S.error = null;
    var jobs = [loadEngine()];
    if (S.activeTab === "overview") jobs.push(loadOverview());
    if (S.activeTab === "queue") jobs.push(loadQueue());
    if (S.activeTab === "projects") {
      jobs.push(loadProjects(), loadDestinations());
      if (S.activeProjectId) jobs.push(loadProject(S.activeProjectId).catch(function () { S.project = null; }));
    }
    if (S.activeTab === "renders") jobs.push(loadRenders());
    if (S.activeTab === "publishing") jobs.push(loadPublications(), loadDestinations());
    return Promise.all(jobs).catch(function (err) {
      S.error = err.message || String(err);
    }).then(function () {
      repaint();
    });
  }

  // -----------------------------------------------------------------------
  // Rendering
  // -----------------------------------------------------------------------
  function render(host) {
    host.innerHTML = shell();
    icons();
    if (!S.bound) bind(host);
    if (!S.overview && !S.loading) {
      S.loading = true;
      refreshActiveTab().then(function () { S.loading = false; });
    }
  }

  function repaint() {
    var host = $("cosmos-agent");
    if (!host) return;
    host.innerHTML = shell();
    icons();
  }

  function shell() {
    var counts = (S.overview && S.overview.counts) || {};
    return [
      '<header class="genesis-head">',
      '  <div class="genesis-identity">',
      '    <span class="genesis-mark"><i data-lucide="orbit"></i></span>',
      '    <div>',
      '      <div class="genesis-name">GENESIS <span>Production Dashboard</span></div>',
      '      <p class="genesis-desc">One production line for Ads and YouTube: intake, rendering, quality review, approval and publishing. The five intelligence agents stay upstream.</p>',
      "    </div>",
      "  </div>",
      '  <div class="genesis-head-stats">',
      '    <span><b>' + esc((counts.projects && counts.projects.in_production) || 0) + "</b> in production</span>",
      '    <span><b>' + esc((counts.renders && counts.renders.active) || 0) + "</b> rendering</span>",
      '    <span><b>' + esc((counts.projects && counts.projects.awaiting_approval) || 0) + "</b> awaiting approval</span>",
      '    <span><b>' + esc((counts.publications && counts.publications.succeeded) || 0) + "</b> published</span>",
      "  </div>",
      "</header>",
      '<nav class="genesis-tabs" role="tablist">' + TABS.map(function (tab) {
        var active = S.activeTab === tab[0] ? " is-active" : "";
        return '<button type="button" role="tab" aria-selected="' + (active ? "true" : "false") +
          '" class="genesis-tab' + active + '" data-cg-tab="' + attr(tab[0]) + '"><i data-lucide="' + attr(tab[2]) + '"></i>' + esc(tab[1]) + "</button>";
      }).join("") + "</nav>",
      S.error ? '<div class="cosmos-alert is-error"><i data-lucide="triangle-alert"></i><div><p>' + esc(S.error) + "</p></div></div>" : "",
      '<div class="genesis-body">' + tabContent() + "</div>"
    ].join("\n");
  }

  function tabContent() {
    if (S.loading && !S.overview) {
      return '<div class="cosmos-loading"><span class="cosmos-spinner"></span><b>Loading production state…</b></div>';
    }
    if (S.activeTab === "overview") return overviewTab();
    if (S.activeTab === "queue") return queueTab();
    if (S.activeTab === "projects") return projectsTab();
    if (S.activeTab === "renders") return rendersTab();
    if (S.activeTab === "publishing") return publishingTab();
    return "";
  }

  function emptyBlock(title, body) {
    return '<div class="cosmos-empty"><b>' + esc(title) + "</b><p>" + esc(body) + "</p></div>";
  }

  function divisionChip(division) {
    return '<span class="genesis-division is-' + attr(division) + '">' + (division === "ads" ? "Ads" : "YouTube") + "</span>";
  }

  // -- Overview -----------------------------------------------------------
  function overviewTab() {
    var data = S.overview || {};
    var counts = data.counts || {};
    var projects = data.projects || [];
    var renders = data.renders || [];
    var publications = data.publications || [];
    var pc = counts.projects || {};
    var rc = counts.renders || {};
    var bc = counts.publications || {};
    return [
      engineBanner(),
      '<div class="genesis-stat-grid">',
      statCard("Ready for production", counts.ready_for_production || 0, "list-checks"),
      statCard("Assets being prepared", counts.assets_preparing || 0, "package"),
      statCard("Active render jobs", rc.active || 0, "film"),
      statCard("Completed renders", rc.complete || 0, "check-circle-2"),
      statCard("Awaiting approval", pc.awaiting_approval || 0, "shield-check"),
      statCard("Scheduled publications", bc.scheduled || 0, "calendar-clock"),
      statCard("Published videos", bc.succeeded || 0, "upload-cloud"),
      statCard("Failed jobs", counts.failed_total || 0, "triangle-alert"),
      "</div>",
      '<div class="genesis-columns">',
      panel("Recent production projects", projects.length ? projects.map(projectRow).join("") : emptyBlock("No projects yet", "Start production from the queue.")),
      panel("Recent render jobs", renders.length ? renders.map(renderRow).join("") : emptyBlock("No renders yet", "Completed renders appear here with their real state.")),
      panel("Recent publications", publications.length ? publications.map(publicationRow).join("") : emptyBlock("Nothing published yet", "Publishing requires an approved quality review and explicit owner approval.")),
      "</div>"
    ].join("\n");
  }

  function statCard(label, value, icon) {
    return [
      '<div class="genesis-stat">',
      '  <i data-lucide="' + attr(icon) + '"></i>',
      "  <b>" + esc(value || 0) + "</b>",
      "  <span>" + esc(label) + "</span>",
      "</div>"
    ].join("");
  }

  function panel(title, body) {
    return [
      '<section class="genesis-panel">',
      '  <h3>' + esc(title) + "</h3>",
      body,
      "</section>"
    ].join("\n");
  }

  function engineBanner() {
    var engine = S.engine || {};
    if (engine.available) {
      return '<div class="genesis-engine is-ok"><i data-lucide="film"></i><div><b>FFmpeg ' + esc(engine.version || "available") + "</b>" +
        "<p>Final assembly renders locally with the real encoder. " +
        (engine.runner ? "Provider-backed scene generation routes through the Studio job runner." : "Scene generation uses existing media library assets.") + "</p></div></div>";
    }
    return '<div class="genesis-engine is-warn"><i data-lucide="triangle-alert"></i><div><b>FFmpeg is not installed</b>' +
      "<p>Final video rendering is unavailable. Install ffmpeg (brew install ffmpeg) — nothing will pretend to render.</p></div></div>";
  }

  // -- Queue --------------------------------------------------------------
  function queueTab() {
    var filters = [["all", "All"], ["ads", "Ads"], ["youtube", "YouTube"]];
    return [
      '<div class="genesis-toolbar">',
      '  <div class="genesis-chips">' + filters.map(function (pair) {
        return '<button type="button" class="genesis-chip' + (S.queueFilter === pair[0] ? " is-active" : "") +
          '" data-cg-queue-filter="' + attr(pair[0]) + '">' + esc(pair[1]) + "</button>";
      }).join("") + "</div>",
      '  <input type="search" class="genesis-search" placeholder="Search the queue…" value="' + attr(S.queueQuery) + '" data-cg-queue-search>',
      "</div>",
      '<p class="cosmos-muted">' + esc(S.queueTotal) + " eligible item(s). Intake covers approved Ads products, handed-off Ads briefs, approved Ads/YouTube Studio projects, and scripted YouTube topics. Sources already in an active production project are hidden, and every lifecycle gate is enforced by the backend.</p>",
      S.queue.length ? '<div class="genesis-queue">' + S.queue.map(queueItem).join("") + "</div>"
        : emptyBlock("Queue is empty", "Approved Ads products and briefs, approved Studio projects, and scripted YouTube topics will appear here for production intake.")
    ].join("\n");
  }

  function queueItem(item) {
    var provenance = item.provenance || {};
    var assets = item.assets || {};
    var origin = [];
    if (item.origin) origin.push(item.origin);
    if (provenance.agent_id) origin.push("agent " + String(provenance.agent_id).toUpperCase());
    if (provenance.recommendation_id) origin.push("recommendation " + provenance.recommendation_id);
    var assetNote = [];
    if (item.division === "ads") {
      if (assets.image_url) assetNote.push("product image available");
      if (assets.affiliate_status) assetNote.push("affiliate: " + assets.affiliate_status);
      if (assets.brief_id) assetNote.push("brief " + assets.brief_id);
    } else {
      if (assets.script_id) assetNote.push("script: " + (assets.script_title || assets.script_id) + " (" + (assets.script_sections || 0) + " sections)");
      else if (item.source_kind === "youtube_topic") assetNote.push("no script linked");
    }
    return [
      '<article class="genesis-queue-item">',
      '  <div class="genesis-queue-main">',
      '    <div class="genesis-queue-title">' + divisionChip(item.division) + "<b>" + esc(item.title) + "</b>" + badge(item.status) + "</div>",
      '    <p class="cosmos-muted">' + esc(item.subtitle || "") + (assetNote.length ? " · " + esc(assetNote.join(" · ")) : "") + "</p>",
      origin.length ? '<p class="genesis-provenance">Origin: ' + esc(origin.join(" · ")) + "</p>" : "",
      item.destination ? '<p class="genesis-provenance">Destination: ' + esc(item.destination) + "</p>" : "",
      "  </div>",
      '  <button type="button" class="cosmos-btn is-primary" data-cg-start="' + attr(item.source_kind) + "|" + attr(item.source_id) + "|" + attr(provenance.channel_id || "") + '">Start production</button>',
      "</article>"
    ].join("\n");
  }

  // -- Projects -----------------------------------------------------------
  function projectsTab() {
    if (S.project) return projectDetail(S.project);
    var filters = [["all", "All"], ["ads", "Ads"], ["youtube", "YouTube"]];
    return [
      '<div class="genesis-toolbar">',
      '  <div class="genesis-chips">' + filters.map(function (pair) {
        return '<button type="button" class="genesis-chip' + (S.projectFilter === pair[0] ? " is-active" : "") +
          '" data-cg-project-filter="' + attr(pair[0]) + '">' + esc(pair[1]) + "</button>";
      }).join("") + "</div>",
      '  <input type="search" class="genesis-search" placeholder="Search projects…" value="' + attr(S.projectQuery) + '" data-cg-project-search>',
      "</div>",
      S.projects.length ? '<div class="genesis-project-list">' + S.projects.map(projectRow).join("") + "</div>"
        : emptyBlock("No production projects", "Approved Ads products and scripted YouTube topics enter production from the queue.")
    ].join("\n");
  }

  function projectRow(project) {
    return [
      '<article class="genesis-project-row" data-cg-open-project="' + attr(project.id) + '" role="button" tabindex="0">',
      '  <div class="genesis-queue-title">' + divisionChip(project.division) + "<b>" + esc(project.title) + "</b>" + badge(project.status, PROJECT_STATUS_LABELS) + "</div>",
      '  <p class="cosmos-muted">' + esc(project.source && project.source.kind === "youtube_topic" ? "Topic" : "Product") + " " +
      esc((project.source && project.source.id) || "") + " · updated " + esc(fmtWhen(project.updated_iso)) + "</p>",
      "</article>"
    ].join("\n");
  }

  function fieldRow(label, control) {
    return '<label class="cosmos-field"><span>' + esc(label) + "</span>" + control + "</label>";
  }

  function projectDetail(project) {
    var settings = project.settings || {};
    var audio = project.audio || {};
    var metadata = project.publishing_metadata || {};
    var refs = project.references || {};
    var review = project.quality_review;
    var approval = project.approval;
    var editable = project.status === "in_production" || project.status === "failed";
    var reopenable = ["quality_review", "awaiting_approval", "approved", "failed"].indexOf(project.status) !== -1;
    var scenes = project.scenes || [];
    var platformOptions = ["youtube", "youtube_shorts", "meta_ads", "tiktok"];

    return [
      '<div class="genesis-detail-head">',
      '  <button type="button" class="cosmos-btn is-ghost" data-cg-back>← All projects</button>',
      '  <div class="genesis-queue-title">' + divisionChip(project.division) + "<b>" + esc(project.title) + "</b>" + badge(project.status, PROJECT_STATUS_LABELS) + "</div>",
      reopenable ? '<button type="button" class="cosmos-btn is-ghost" data-cg-reopen="' + attr(project.id) + '">Return to production</button>' : "",
      '  <button type="button" class="cosmos-btn is-ghost" data-cg-archive="' + attr(project.id) + '">Archive</button>',
      "</div>",
      provenanceBlock(project),
      '<div class="genesis-workspace">',

      '<section class="genesis-panel"><h3>Brief &amp; creative direction</h3>',
      fieldRow("Title", '<input data-cg-field="title" value="' + attr(project.title) + '"' + (editable ? "" : " disabled") + ">"),
      fieldRow("Description", '<textarea data-cg-field="description" rows="3"' + (editable ? "" : " disabled") + ">" + esc(project.description) + "</textarea>"),
      fieldRow("Creative direction", '<textarea data-cg-field="creative_direction" rows="3" placeholder="Visual style, pacing, do/don\'t…"' + (editable ? "" : " disabled") + ">" + esc(project.creative_direction) + "</textarea>"),
      "</section>",

      '<section class="genesis-panel"><h3>Timeline &amp; scenes</h3>',
      '<p class="cosmos-muted">Each scene needs real media from the library (or a provider generation spec) before the render can run.</p>',
      scenes.length ? scenes.map(function (scene, index) { return sceneRow(scene, index, editable); }).join("") : emptyBlock("No scenes", "Add scenes to build the timeline."),
      editable ? '<button type="button" class="cosmos-btn" data-cg-add-scene>+ Add scene</button>' : "",
      "</section>",

      '<section class="genesis-panel"><h3>References</h3>',
      refGroup("Model / character", refs.model_media_ids, "model_refs", editable),
      refGroup("Product &amp; props", refs.product_media_ids, "product_refs", editable),
      refGroup("Props (extra)", refs.props_media_ids, "props_refs", editable),
      "</section>",

      '<section class="genesis-panel"><h3>Voiceover, audio &amp; captions</h3>',
      refSingle("Voiceover", audio.voiceover_media_id, "voiceover", editable),
      refSingle("Music", audio.music_media_id, "music", editable),
      fieldRow("Captions (SRT, burned into the render)", '<textarea data-cg-field="captions_srt" rows="3" placeholder="1&#10;00:00:00,000 --> 00:00:04,000&#10;Caption text"' + (editable ? "" : " disabled") + ">" + esc(audio.captions_srt || "") + "</textarea>"),
      "</section>",

      '<section class="genesis-panel"><h3>Export settings</h3>',
      '<div class="genesis-field-grid">',
      fieldRow("Aspect ratio", '<select data-cg-field="aspect_ratio"' + (editable ? "" : " disabled") + ">" +
        ["16:9", "9:16", "1:1", "4:5"].map(function (a) {
          return '<option value="' + a + '"' + (settings.aspect_ratio === a ? " selected" : "") + ">" + a + "</option>";
        }).join("") + "</select>"),
      fieldRow("Frame rate", '<select data-cg-field="fps"' + (editable ? "" : " disabled") + ">" +
        [24, 25, 30, 50, 60].map(function (f) {
          return '<option value="' + f + '"' + (Number(settings.fps) === f ? " selected" : "") + ">" + f + " fps</option>";
        }).join("") + "</select>"),
      fieldRow("Export format", '<input value="MP4 (H.264 + AAC)" disabled>'),
      "</div>",
      fieldRow("Target platforms", '<div class="genesis-checks">' + platformOptions.map(function (p) {
        var checked = (settings.target_platforms || []).indexOf(p) !== -1 ? " checked" : "";
        return '<label class="cosmos-check"><input type="checkbox" data-cg-platform="' + attr(p) + '"' + checked + (editable ? "" : " disabled") + "> " + esc(p) + "</label>";
      }).join("") + "</div>"),
      "</section>",

      '<section class="genesis-panel"><h3>Destination metadata</h3>',
      fieldRow("Title", '<input data-cg-field="pub_title" maxlength="100" value="' + attr(metadata.title) + '"' + (editable ? "" : " disabled") + ">"),
      fieldRow("Description", '<textarea data-cg-field="pub_description" rows="3"' + (editable ? "" : " disabled") + ">" + esc(metadata.description) + "</textarea>"),
      fieldRow("Tags (comma separated)", '<input data-cg-field="pub_tags" value="' + esc((metadata.tags || []).join(", ")) + '"' + (editable ? "" : " disabled") + ">"),
      thumbnailRow(metadata, editable),
      fieldRow("Sponsored / affiliate disclosure", '<input data-cg-field="pub_disclosure" placeholder="e.g. Includes a paid affiliate link." value="' + attr(metadata.disclosure) + '"' + (editable ? "" : " disabled") + ">"),
      '<div class="genesis-field-grid">',
      fieldRow("Sponsored", '<label class="cosmos-check"><input type="checkbox" data-cg-field="pub_sponsored"' + (metadata.sponsored ? " checked" : "") + (editable ? "" : " disabled") + "> This content is sponsored / uses affiliate links</label>"),
      fieldRow("Privacy", '<select data-cg-field="pub_privacy"' + (editable ? "" : " disabled") + ">" +
        ["private", "unlisted", "public"].map(function (p) {
          return '<option value="' + p + '"' + (metadata.privacy_status === p ? " selected" : "") + ">" + p + "</option>";
        }).join("") + "</select>"),
      "</div>",
      "</section>",

      editable ? '<div class="genesis-actions"><button type="button" class="cosmos-btn is-primary" data-cg-save="' + attr(project.id) + '">Save project</button>' +
        '<button type="button" class="cosmos-btn" data-cg-render="' + attr(project.id) + '">Render final video</button></div>' : "",

      '<section class="genesis-panel"><h3>Renders</h3>' + (rendersFor(project).length
        ? rendersFor(project).map(renderRow).join("") : emptyBlock("No renders yet", "Render the final video once the timeline has media.")) + "</section>",

      '<section class="genesis-panel"><h3>Quality review</h3>' + qualityBlock(project, review) + "</section>",

      '<section class="genesis-panel"><h3>Owner approval</h3>' + approvalBlock(project, approval) + "</section>",

      '<section class="genesis-panel"><h3>Publishing</h3>' + publishBlock(project) + "</section>",

      '<section class="genesis-panel"><h3>History</h3><ul class="genesis-history">' +
        (project.history || []).slice().reverse().slice(0, 20).map(function (entry) {
          return "<li><b>" + esc(entry.event) + "</b> <span>" + esc(fmtWhen(entry.iso)) + "</span>" +
            (entry.note ? " — " + esc(entry.note) : "") + "</li>";
        }).join("") + "</ul></section>",

      "</div>"
    ].join("\n");
  }

  function provenanceBlock(project) {
    var p = project.provenance || {};
    var rows = [];
    function push(label, value) { if (value) rows.push('<div class="cosmos-kv"><span>' + esc(label) + "</span><b>" + esc(value) + "</b></div>"); }
    push("Division", project.division);
    push("Backlog record", p.backlog_kind && p.backlog_item_id ? p.backlog_kind + " · " + p.backlog_item_id : null);
    push("Agent", p.agent_id);
    push("Recommendation", p.recommendation_id);
    push("Execution", p.execution_id);
    push("Script", p.script_id);
    push("Channel", p.channel_id);
    push("Studio project", p.studio_project_id);
    return '<details class="genesis-provenance-box"><summary>Provenance — where this production came from</summary><div class="cosmos-kv-grid">' +
      rows.join("") + "</div></details>";
  }

  function sceneRow(scene, index, editable) {
    var record = scene.media_id ? findMediaRecord(scene.media_id) : null;
    return [
      '<div class="genesis-scene" data-scene-id="' + attr(scene.id) + '">',
      '  <div class="genesis-scene-index">' + (index + 1) + "</div>",
      '  <div class="genesis-scene-media">' + (record ? mediaPreview(record) : '<div class="genesis-media-empty">no media</div>') + "</div>",
      '  <div class="genesis-scene-body">',
      '    <b>' + esc(scene.title || "Untitled scene") + "</b>" + badge(scene.status === "ready" ? "approved" : "unvalidated"),
      scene.description ? '<p class="cosmos-muted">' + esc(scene.description) + "</p>" : "",
      scene.generation ? '<p class="genesis-provenance">Generation spec: ' + esc(scene.generation.provider) + " · " + esc(scene.generation.model_id) + "</p>" : "",
      "  </div>",
      editable ? '<div class="genesis-scene-actions">' +
        '<button type="button" class="cosmos-btn is-ghost" data-cg-attach-media="scene|' + attr(scene.id) + '">Attach media</button>' +
        '<button type="button" class="cosmos-btn is-danger is-ghost" data-cg-remove-scene="' + attr(scene.id) + '">Remove</button></div>' : "",
      "</div>"
    ].join("\n");
  }

  function findMediaRecord(mediaId) {
    var cached = S.mediaById[mediaId] || (S.media || []).find(function (item) { return item && item.id === mediaId; });
    if (cached) {
      // Some upload paths store a video under kind "image"; the content type
      // is the truth — previews must not render a <video> as a broken <img>.
      var type = String(cached.content_type || "").toLowerCase();
      if (type.indexOf("video/") === 0) cached.kind = "video";
      else if (type.indexOf("audio/") === 0) cached.kind = "audio";
      else if (type.indexOf("image/") === 0) cached.kind = "image";
      return cached;
    }
    // The library list is partial; the id alone is still enough for the media
    // endpoint to serve a preview, but the kind is unknown, so no fake preview.
    return { id: mediaId, kind: null, title: mediaId };
  }

  function refGroup(label, ids, target, editable) {
    var list = (ids || []).map(function (mediaId) {
      var record = findMediaRecord(mediaId);
      return '<div class="genesis-ref">' + (record && record.kind === "image" ? mediaPreview(record) : '<span class="cosmos-kv"><b>' + esc(mediaId) + "</b></span>") +
        (editable ? '<button type="button" class="cosmos-btn is-danger is-ghost" data-cg-ref-remove="' + target + "|" + attr(mediaId) + '">×</button>' : "") + "</div>";
    }).join("");
    return '<div class="genesis-ref-group"><span>' + label + "</span>" +
      '<div class="genesis-ref-list">' + (list || '<span class="cosmos-muted">none</span>') + "</div>" +
      (editable ? '<button type="button" class="cosmos-btn is-ghost" data-cg-attach-media="' + attr(target) + '|">+ Add</button>' : "") + "</div>";
  }

  function refSingle(label, mediaId, target, editable) {
    return '<div class="genesis-ref-group"><span>' + label + "</span>" +
      '<div class="genesis-ref-list">' + (mediaId ? '<span class="cosmos-kv"><b>' + esc(mediaId) + "</b></span>" : '<span class="cosmos-muted">none</span>') + "</div>" +
      (editable ? '<button type="button" class="cosmos-btn is-ghost" data-cg-attach-media="' + attr(target) + '|">' + (mediaId ? "Replace" : "+ Add") + "</button>" : "") + "</div>";
  }

  function thumbnailRow(metadata, editable) {
    var mediaId = metadata.thumbnail_media_id;
    var record = mediaId ? findMediaRecord(mediaId) : null;
    var preview = record && record.kind === "image"
      ? mediaPreview(record)
      : (mediaId ? '<span class="cosmos-kv"><b>' + esc(mediaId) + "</b></span>" : '<span class="cosmos-muted">none</span>');
    return '<div class="genesis-ref-group"><span>Thumbnail</span>' +
      '<div class="genesis-ref-list">' + preview + "</div>" +
      (editable ? '<button type="button" class="cosmos-btn is-ghost" data-cg-attach-media="thumbnail|">' + (mediaId ? "Replace" : "+ Choose") + "</button>" : "") + "</div>";
  }

  function rendersFor(project) {
    return (S.renders || []).filter(function (render) { return render.project_id === project.id; });
  }

  function renderRow(render) {
    var active = ["queued", "preparing", "waiting_media", "assembling"].indexOf(render.status) !== -1;
    var out = [
      '<article class="genesis-render">',
      '  <div class="genesis-queue-title"><b>' + esc(render.id) + "</b>" + badge(render.status, RENDER_STATUS_LABELS) + "</div>",
      '  <p class="cosmos-muted">' + esc(render.detail || "") + "</p>",
      render.error ? '<p class="cosmos-run-error">' + esc(render.error.code) + ": " + esc(render.error.message) + "</p>" : "",
      (render.warnings || []).length ? '<p class="cosmos-muted">⚠ ' + esc(render.warnings.join(" · ")) + "</p>" : "",
      '<p class="genesis-provenance">' + esc(render.engine || "ffmpeg") + (render.engine_version ? " " + esc(render.engine_version) : "") +
      (render.duration_seconds ? " · " + esc(render.duration_seconds) + "s" : "") +
      (render.media_id ? " · media " + esc(render.media_id) : "") + "</p>",
      render.media_id ? '<video class="genesis-media" src="' + attr(mediaUrl(render.media_id)) + '" controls preload="metadata"></video>' : "",
      active ? '<button type="button" class="cosmos-btn is-ghost" data-cg-cancel-render="' + attr(render.id) + '">Cancel render</button>' : "",
      "</article>"
    ];
    return out.filter(Boolean).join("\n");
  }

  function qualityBlock(project, review) {
    if (!review) {
      return emptyBlock("Not reviewed yet", "Run the quality review after a completed render — the checks probe the real output file.");
    }
    var counts = { pass: 0, warn: 0, fail: 0 };
    (review.checks || []).forEach(function (check) { counts[check.status] = (counts[check.status] || 0) + 1; });
    return [
      '<div class="genesis-queue-title">' + badge(review.verdict === "pass" ? "approved" : review.verdict === "warn" ? "unvalidated" : "failed") +
      '<span class="cosmos-muted">' + esc(counts.pass) + " pass · " + esc(counts.warn) + " warn · " + esc(counts.fail) + " fail · " + esc(fmtWhen(review.ran_iso)) + "</span></div>",
      '<ul class="genesis-checks">' + (review.checks || []).map(function (check) {
        return '<li class="is-' + esc(check.status) + '"><b>' + esc(check.label) + "</b> — " + esc(check.detail) + "</li>";
      }).join("") + "</ul>",
      '<button type="button" class="cosmos-btn" data-cg-qa="' + attr(project.id) + '">Re-run quality review</button>'
    ].join("\n");
  }

  function approvalBlock(project, approval) {
    if (approval && approval.approved) {
      return [
        '<p class="genesis-approved">✔ Approved by ' + esc(approval.by || "owner") + " · " + esc(fmtWhen(approval.at_iso)) +
        (approval.note ? " — " + esc(approval.note) : "") + "</p>",
        '<button type="button" class="cosmos-btn is-ghost" data-cg-withdraw="' + attr(project.id) + '">Withdraw approval</button>'
      ].join("\n");
    }
    return [
      approval && approval.note ? '<p class="cosmos-muted">Last decision: ' + esc(approval.note) + " · " + esc(fmtWhen(approval.at_iso)) + "</p>" : "",
      '<p class="cosmos-muted">Approval requires a quality review without failing checks. Publishing additionally requires the explicit approval flag on the publish request.</p>',
      '<button type="button" class="cosmos-btn is-primary" data-cg-approve="' + attr(project.id) + '">Approve for publishing</button>'
    ].filter(Boolean).join("\n");
  }

  function publishBlock(project) {
    var pubs = (S.publications || []).filter(function (pub) { return pub.project_id === project.id; });
    var usable = (S.destinations || []).filter(function (d) { return d.available && d.authorized; });
    var anyAvailable = (S.destinations || []).some(function (d) { return d.available; });
    var form;
    if (usable.length) {
      form = [
        '<div class="genesis-publish-form">',
        fieldRow("Destination", '<select data-cg-field="pub_destination">' +
          (S.destinations || []).map(function (d) {
            var disabled = !(d.available && d.authorized);
            var label = d.name + (!d.available ? " — unavailable" : (!d.authorized ? " — not authorized" : ""));
            return '<option value="' + attr(d.id) + '"' + (disabled ? " disabled" : "") + ">" + esc(label) + "</option>";
          }).join("") + "</select>"),
        fieldRow("Schedule (optional, ISO 8601 UTC)", '<input data-cg-field="pub_schedule" placeholder="2026-11-25T15:00:00Z">'),
        fieldRow("Approval note", '<input data-cg-field="pub_note" placeholder="Why this is approved">'),
        '<label class="cosmos-check"><input type="checkbox" data-cg-field="pub_approve"> I approve publishing this project now/as scheduled</label>',
        '<button type="button" class="cosmos-btn is-primary" data-cg-publish="' + attr(project.id) + '">Publish</button>',
        "</div>"
      ].join("\n");
    } else {
      form = emptyBlock(
        anyAvailable ? "Destination not connected" : "No destination available",
        anyAvailable
          ? "Connect YouTube in the Publishing tab before publishing this project."
          : "No publishing adapter is available. Unsupported destinations are shown as unavailable, never as working."
      );
    }
    return [
      (project.approval || {}).approved ? form
        : emptyBlock("Approval required", "Publishing unlocks after the owner approves the project in Quality Review."),
      '<h4>Publication history</h4>',
      pubs.length ? pubs.map(publicationRow).join("") : '<p class="cosmos-muted">No publication attempts for this project.</p>'
    ].join("\n");
  }

  // -- Render Center ------------------------------------------------------
  function rendersTab() {
    return [
      engineBanner(),
      S.renders.length ? S.renders.map(function (render) {
        var project = (S.projects || []).find(function (p) { return p.id === render.project_id; });
        return '<div class="genesis-render-wrap">' +
          '<div class="genesis-queue-title">' + divisionChip(render.division) + "<b>" + esc(project ? project.title : render.project_id) + "</b>" + badge(render.status, RENDER_STATUS_LABELS) + "</div>" +
          renderRow(render) + "</div>";
      }).join("") : emptyBlock("No render jobs", "Renders start from a project workspace once the timeline has media.")
    ].join("\n");
  }

  // -- Publishing ---------------------------------------------------------
  function publishingTab() {
    return [
      '<div class="genesis-columns">',
      panel("Destination accounts", destinationCards()),
      panel("Publishing rules", '<ul class="genesis-checks">' +
        "<li class='is-pass'><b>Explicit approval</b> — nothing publishes without the owner's approval flag on the request.</li>" +
        "<li class='is-pass'><b>Confirmed remote ids</b> — a publication is only 'Published' when the destination API returned a real content id.</li>" +
        "<li class='is-warn'><b>Duplicates blocked</b> — a second publish for an already-published destination is rejected.</li>" +
        "<li class='is-warn'><b>Failures are recorded</b> — failed uploads keep the destination error and can be retried.</li>" +
        "</ul>"),
      "</div>",
      '<section class="genesis-panel"><h3>Publishing history</h3>' +
      (S.publications.length ? S.publications.map(publicationRow).join("") : emptyBlock("Nothing published yet", "Publications appear here with their real status and remote ids.")) + "</section>"
    ].join("\n");
  }

  function destinationCards() {
    if (!S.destinations.length) return emptyBlock("No destinations", "Destination adapters appear here.");
    return S.destinations.map(function (d) {
      var rows = [];
      if (d.available) {
        rows.push('<div class="cosmos-kv"><span>OAuth client</span><b>' + (d.configured ? "configured (" + esc(d.configured_source || "") + ")" : "not configured") + "</b></div>");
        rows.push('<div class="cosmos-kv"><span>Authorization</span><b>' + (d.authorized ? "authorized" : "not authorized") + "</b></div>");
        rows.push('<div class="cosmos-kv"><span>Scope</span><b>' + esc((d.scopes || []).join(" ")) + "</b></div>");
        rows.push('<div class="cosmos-kv"><span>Redirect URI</span><b class="genesis-mono">' + esc(d.redirect_uri || "") + "</b></div>");
        if (d.channel_title) rows.push('<div class="cosmos-kv"><span>Channel</span><b>' + esc(d.channel_title) + "</b></div>");
        return [
          '<article class="genesis-destination">',
          '  <div class="genesis-queue-title"><b>' + esc(d.name) + "</b>" + (d.authorized ? badge("published") : badge("unvalidated")) + "</div>",
          d.reason ? '<p class="cosmos-muted">' + esc(d.reason) + "</p>" : "",
          '  <div class="cosmos-kv-grid">' + rows.join("") + "</div>",
          d.available && d.configured && !d.authorized ? '<button type="button" class="cosmos-btn is-primary" data-cg-authorize>Connect YouTube</button>' : "",
          d.available && d.authorized ? '<button type="button" class="cosmos-btn is-ghost" data-cg-disconnect>Disconnect</button>' : "",
          "</article>"
        ].join("\n");
      }
      return [
        '<article class="genesis-destination is-off">',
        '  <div class="genesis-queue-title"><b>' + esc(d.name) + "</b>" + badge("rejected") + "</div>",
        '  <p class="cosmos-muted">' + esc(d.reason || "Not implemented.") + "</p>",
        "</article>"
      ].join("\n");
    }).join("");
  }

  function publicationRow(pub) {
    return [
      '<article class="genesis-publication">',
      '  <div class="genesis-queue-title"><b>' + esc(pub.destination) + "</b>" + badge(pub.status, PUB_STATUS_LABELS) +
      '  <span class="cosmos-card-when">' + esc(fmtWhen(pub.created_iso)) + "</span></div>",
      pub.metadata && pub.metadata.title ? '<p class="cosmos-muted">' + esc(pub.metadata.title) + "</p>" : "",
      pub.remote_url ? '<p><a href="' + attr(pub.remote_url) + '" target="_blank" rel="noopener noreferrer">' + esc(pub.remote_url) + "</a>" +
        (pub.remote_id ? " · id " + esc(pub.remote_id) : "") + "</p>" : "",
      pub.scheduled_iso ? '<p class="cosmos-muted">Scheduled for ' + esc(pub.scheduled_iso) + "</p>" : "",
      pub.error ? '<p class="cosmos-run-error">' + esc(pub.error.code) + ": " + esc(pub.error.message) + "</p>" : "",
      (pub.warnings || []).length ? '<p class="cosmos-muted">⚠ ' + esc(pub.warnings.join(" · ")) + "</p>" : "",
      '<p class="genesis-provenance">attempts: ' + esc(pub.attempts || 0) + " · " + esc(fmtWhen(pub.updated_iso)) + "</p>",
      '<div class="genesis-scene-actions">',
      pub.status === "failed" ? '<button type="button" class="cosmos-btn is-ghost" data-cg-retry-pub="' + attr(pub.id) + '">Retry</button>' : "",
      pub.status === "scheduled" ? '<button type="button" class="cosmos-btn is-ghost" data-cg-cancel-pub="' + attr(pub.id) + '">Cancel schedule</button>' : "",
      "</div>",
      "</article>"
    ].filter(Boolean).join("\n");
  }

  // -----------------------------------------------------------------------
  // Actions
  // -----------------------------------------------------------------------
  function collectProjectPatch(projectId) {
    var host = $("cosmos-agent");
    if (!host) return null;
    var patch = {};
    var value = function (name) {
      var el = host.querySelector('[data-cg-field="' + name + '"]');
      return el ? el.value : null;
    };
    var title = value("title");
    if (title !== null) patch.title = title;
    var description = value("description");
    if (description !== null) patch.description = description;
    var direction = value("creative_direction");
    if (direction !== null) patch.creative_direction = direction;
    var captions = value("captions_srt");
    if (captions !== null) patch.audio = { captions_srt: captions };

    var aspect = value("aspect_ratio");
    var fps = value("fps");
    var platforms = [];
    Array.prototype.forEach.call(host.querySelectorAll("[data-cg-platform]"), function (input) {
      if (input.checked) platforms.push(input.getAttribute("data-cg-platform"));
    });
    var settings = {};
    if (aspect !== null) settings.aspect_ratio = aspect;
    if (fps !== null) settings.fps = Number(fps);
    settings.target_platforms = platforms;
    patch.settings = settings;

    var tags = value("pub_tags");
    patch.publishing_metadata = {
      title: value("pub_title") || undefined,
      description: value("pub_description"),
      tags: tags ? tags.split(",").map(function (t) { return t.trim(); }).filter(Boolean) : [],
      disclosure: value("pub_disclosure"),
      sponsored: host.querySelector('[data-cg-field="pub_sponsored"]').checked,
      privacy_status: value("pub_privacy")
    };
    return patch;
  }

  function saveProject(projectId) {
    var patch = collectProjectPatch(projectId);
    if (!patch) return Promise.resolve();
    return api("/api/cosmos/genesis/projects/" + encodeURIComponent(projectId), { method: "PATCH", body: patch })
      .then(function (data) {
        S.project = data.project;
        toast("Project saved.");
        return refreshActiveTab();
      });
  }

  function startProduction(sourceKind, sourceId, channelId) {
    var key = "start|" + sourceKind + "|" + sourceId;
    if (S.busy[key]) return;
    S.busy[key] = true;
    var body = { source_kind: sourceKind, source_id: sourceId };
    if (channelId) body.channel_id = channelId;
    api("/api/cosmos/genesis/projects", {
      method: "POST",
      body: body
    }).then(function (data) {
      S.busy[key] = false;
      S.project = data.project;
      S.activeProjectId = data.project.id;
      S.activeTab = "projects";
      toast("Production project created.");
      return refreshActiveTab();
    }).catch(function (err) {
      S.busy[key] = false;
      S.error = err.message || String(err);
      repaint();
    });
  }

  function openMediaPicker(ctx) {
    S.attach = ctx;
    var modal = window.Cosmos;
    var kindDefault = ctx.target === "thumbnail" ? "image"
      : (ctx.target === "voiceover" || ctx.target === "music") ? "audio"
      : ctx.target === "captions_media" ? "file" : "video";
    S.mediaKind = kindDefault;
    var load = function () {
      return loadMedia().then(function () {
        var list = S.media.map(function (item) {
          return [
            '<div class="genesis-pick" data-cg-pick="' + attr(item.id) + '" role="button" tabindex="0">',
            '  <div class="genesis-pick-preview">' + (item.kind === "video" || item.kind === "image" ? mediaPreview(item) : "") + "</div>",
            '  <div><b>' + esc(item.title || item.filename || item.id) + "</b>",
            '  <p class="cosmos-muted">' + esc(item.kind) + " · " + esc(item.id) + (item.duration ? " · " + esc(Math.round(item.duration)) + "s" : "") + "</p></div>",
            "</div>"
          ].join("");
        }).join("");
        var body = '<div class="genesis-picker">' +
          '<div class="genesis-toolbar"><div class="genesis-chips">' +
          ["video", "image", "audio", "file", "all"].map(function (kind) {
            return '<button type="button" class="genesis-chip' + (S.mediaKind === kind ? " is-active" : "") + '" data-cg-media-kind="' + kind + '">' + kind + "</button>";
          }).join("") + "</div>" +
          '<input type="search" class="genesis-search" placeholder="Search the media library…" data-cg-media-search value="' + attr(S.mediaQuery) + '"></div>' +
          '<div id="cosmos-modal-error" class="cosmos-error hidden"></div>' +
          (list || emptyBlock("No media found", "Generate or upload media in the Studio first — GENESIS only references real assets.")) + "</div>";
        modal.openModal("Attach media", body, "", null);
        var submit = $("cosmos-modal-submit");
        if (submit) submit.classList.add("hidden");
      });
    };
    load();
  }

  function attachMedia(mediaId) {
    var ctx = S.attach;
    if (!ctx || !ctx.projectId) return Promise.resolve();
    var project = S.project;
    if (!project || project.id !== ctx.projectId) {
      return loadProject(ctx.projectId).then(function () { return attachMedia(mediaId); });
    }
    var patch = {};
    if (ctx.target === "scene") {
      patch.scenes = (project.scenes || []).map(function (scene) {
        if (scene.id !== ctx.sceneId) return scene;
        var next = Object.assign({}, scene, { media_id: mediaId, status: "ready" });
        return next;
      });
    } else if (ctx.target === "voiceover" || ctx.target === "music" || ctx.target === "captions_media") {
      var audio = Object.assign({}, project.audio || {});
      audio[ctx.target === "voiceover" ? "voiceover_media_id" : ctx.target === "music" ? "music_media_id" : "captions_media_id"] = mediaId;
      patch.audio = audio;
    } else if (ctx.target === "thumbnail") {
      patch.publishing_metadata = Object.assign({}, project.publishing_metadata || {}, { thumbnail_media_id: mediaId });
    } else if (ctx.target === "model_refs" || ctx.target === "product_refs" || ctx.target === "props_refs") {
      var refs = Object.assign({}, project.references || {});
      var key = ctx.target === "model_refs" ? "model_media_ids" : ctx.target === "product_refs" ? "product_media_ids" : "props_media_ids";
      var list = (refs[key] || []).slice();
      if (list.indexOf(mediaId) === -1) list.push(mediaId);
      refs[key] = list;
      patch.references = refs;
    }
    return api("/api/cosmos/genesis/projects/" + encodeURIComponent(ctx.projectId), { method: "PATCH", body: patch })
      .then(function (data) {
        S.project = data.project;
        if (window.Cosmos && window.Cosmos.closeModal) window.Cosmos.closeModal();
        S.attach = null;
        toast("Media attached.");
        return refreshActiveTab();
      });
  }

  function addScene() {
    var project = S.project;
    if (!project) return;
    var scenes = (project.scenes || []).slice();
    scenes.push({ title: "Scene " + (scenes.length + 1), description: "", kind: "video" });
    return api("/api/cosmos/genesis/projects/" + encodeURIComponent(project.id), { method: "PATCH", body: { scenes: scenes } })
      .then(function (data) { S.project = data.project; return refreshActiveTab(); });
  }

  function removeScene(sceneId) {
    var project = S.project;
    if (!project) return;
    var scenes = (project.scenes || []).filter(function (scene) { return scene.id !== sceneId; });
    return api("/api/cosmos/genesis/projects/" + encodeURIComponent(project.id), { method: "PATCH", body: { scenes: scenes } })
      .then(function (data) { S.project = data.project; return refreshActiveTab(); });
  }

  function removeRef(target, mediaId) {
    var project = S.project;
    if (!project) return;
    var refs = JSON.parse(JSON.stringify(project.references || {}));
    var key = target === "model_refs" ? "model_media_ids" : target === "product_refs" ? "product_media_ids" : "props_media_ids";
    refs[key] = (refs[key] || []).filter(function (id) { return id !== mediaId; });
    return api("/api/cosmos/genesis/projects/" + encodeURIComponent(project.id), { method: "PATCH", body: { references: refs } })
      .then(function (data) { S.project = data.project; return refreshActiveTab(); });
  }

  function startRender(projectId) {
    return api("/api/cosmos/genesis/projects/" + encodeURIComponent(projectId) + "/render", { method: "POST" })
      .then(function (data) {
        toast("Render started — progress is real, watch the Render Center.");
        S.activeTab = "renders";
        return refreshActiveTab();
      }).catch(function (err) {
        S.error = err.message || String(err);
        repaint();
      });
  }

  function cancelRender(renderId) {
    return api("/api/cosmos/genesis/renders/" + encodeURIComponent(renderId) + "/cancel", { method: "POST" })
      .then(function () { return refreshActiveTab(); });
  }

  function runQA(projectId) {
    return api("/api/cosmos/genesis/projects/" + encodeURIComponent(projectId) + "/quality-review", { method: "POST" })
      .then(function (data) {
        S.project = data.project;
        toast("Quality review: " + data.review.verdict + ".");
        return refreshActiveTab();
      }).catch(function (err) {
        S.error = err.message || String(err);
        repaint();
      });
  }

  function approve(projectId) {
    return api("/api/cosmos/genesis/projects/" + encodeURIComponent(projectId) + "/approval", {
      method: "POST", body: { approved: true, note: "Approved from the GENESIS dashboard." }
    }).then(function (data) {
      S.project = data.project;
      toast("Project approved for publishing.");
      return refreshActiveTab();
    }).catch(function (err) {
      S.error = err.message || String(err);
      repaint();
    });
  }

  function withdraw(projectId) {
    return api("/api/cosmos/genesis/projects/" + encodeURIComponent(projectId) + "/approval", {
      method: "POST", body: { approved: false }
    }).then(function (data) {
      S.project = data.project;
      toast("Approval withdrawn.");
      return refreshActiveTab();
    });
  }

  function reopen(projectId) {
    return api("/api/cosmos/genesis/projects/" + encodeURIComponent(projectId) + "/reopen", { method: "POST" })
      .then(function (data) {
        S.project = data.project;
        toast("Project returned to production.");
        return refreshActiveTab();
      }).catch(function (err) {
        S.error = err.message || String(err);
        repaint();
      });
  }

  function publish(projectId) {
    var host = $("cosmos-agent");
    var approved = host && host.querySelector('[data-cg-field="pub_approve"]');
    if (!approved || !approved.checked) {
      S.error = "Publishing requires the explicit approval checkbox — nothing publishes automatically.";
      repaint();
      return Promise.resolve();
    }
    var value = function (name) {
      var el = host.querySelector('[data-cg-field="' + name + '"]');
      return el ? el.value.trim() : "";
    };
    var destination = value("pub_destination");
    if (!destination) {
      S.error = "Choose an available, authorized destination before publishing.";
      repaint();
      return Promise.resolve();
    }
    var metadata = {};
    if (value("pub_title")) metadata.title = value("pub_title");
    var body = {
      destination: destination,
      approved: true,
      approval_note: value("pub_note")
    };
    if (value("pub_schedule")) body.schedule_at = value("pub_schedule");
    if (Object.keys(metadata).length) body.metadata = metadata;
    return api("/api/cosmos/genesis/projects/" + encodeURIComponent(projectId) + "/publish", { method: "POST", body: body })
      .then(function (data) {
        toast("Publication started — status reflects the real destination response.");
        return refreshActiveTab();
      }).catch(function (err) {
        S.error = err.message || String(err);
        repaint();
      });
  }

  function retryPub(pubId) {
    return api("/api/cosmos/genesis/publications/" + encodeURIComponent(pubId) + "/retry", { method: "POST" })
      .then(function () { return refreshActiveTab(); });
  }

  function cancelPub(pubId) {
    return api("/api/cosmos/genesis/publications/" + encodeURIComponent(pubId) + "/cancel", { method: "POST" })
      .then(function () { return refreshActiveTab(); });
  }

  function authorizeYouTube() {
    return api("/api/cosmos/genesis/publishing/youtube/authorize", { method: "POST" })
      .then(function (data) {
        if (data && data.authorization_url) {
          window.open(data.authorization_url, "genesis-youtube-oauth", "width=560,height=720");
          toast("Complete the Google consent screen — this tab updates once authorized.");
          setTimeout(refreshActiveTab, 8000);
        }
      }).catch(function (err) {
        S.error = err.message || String(err);
        repaint();
      });
  }

  function disconnectYouTube() {
    return api("/api/cosmos/genesis/publishing/youtube/disconnect", { method: "POST" })
      .then(function () { toast("YouTube disconnected."); return refreshActiveTab(); });
  }

  // -----------------------------------------------------------------------
  // Events (delegated, bound once)
  // -----------------------------------------------------------------------
  function bind(host) {
    S.bound = true;
    ensureToken();

    host.addEventListener("click", function (event) {
      var target = event.target;
      var button = target.closest("button");
      var pick = target.closest("[data-cg-pick]");
      if (pick) { event.preventDefault(); attachMedia(pick.getAttribute("data-cg-pick")); return; }
      var openRow = target.closest("[data-cg-open-project]");
      if (openRow) {
        S.activeProjectId = openRow.getAttribute("data-cg-open-project");
        loadProject(S.activeProjectId).then(repaint);
        return;
      }
      if (!button) return;
      var tab = button.getAttribute("data-cg-tab");
      if (tab) { S.activeTab = tab; refreshActiveTab(); return; }
      var qf = button.getAttribute("data-cg-queue-filter");
      if (qf) { S.queueFilter = qf; refreshActiveTab(); return; }
      var pf = button.getAttribute("data-cg-project-filter");
      if (pf) { S.projectFilter = pf; refreshActiveTab(); return; }
      var mk = button.getAttribute("data-cg-media-kind");
      if (mk) { S.mediaKind = mk; loadMedia().then(function () { openMediaPicker(S.attach); }); return; }
      var start = button.getAttribute("data-cg-start");
      if (start) {
        var bits = start.split("|");
        return startProduction(bits[0], bits[1], bits[2] || null);
      }
      if (button.hasAttribute("data-cg-back")) { S.project = null; S.activeProjectId = null; repaint(); return; }
      var save = button.getAttribute("data-cg-save");
      if (save) { event.preventDefault(); return saveProject(save); }
      var renderId = button.getAttribute("data-cg-render");
      if (renderId) { event.preventDefault(); return startRender(renderId); }
      var cancelRenderId = button.getAttribute("data-cg-cancel-render");
      if (cancelRenderId) return cancelRender(cancelRenderId);
      var qa = button.getAttribute("data-cg-qa");
      if (qa) { event.preventDefault(); return runQA(qa); }
      var approveId = button.getAttribute("data-cg-approve");
      if (approveId) { event.preventDefault(); return approve(approveId); }
      var withdrawId = button.getAttribute("data-cg-withdraw");
      if (withdrawId) { event.preventDefault(); return withdraw(withdrawId); }
      var reopenId = button.getAttribute("data-cg-reopen");
      if (reopenId) { event.preventDefault(); return reopen(reopenId); }
      var pubId = button.getAttribute("data-cg-publish");
      if (pubId) { event.preventDefault(); return publish(pubId); }
      var retryId = button.getAttribute("data-cg-retry-pub");
      if (retryId) return retryPub(retryId);
      var cancelPubId = button.getAttribute("data-cg-cancel-pub");
      if (cancelPubId) return cancelPub(cancelPubId);
      if (button.hasAttribute("data-cg-authorize")) return authorizeYouTube();
      if (button.hasAttribute("data-cg-disconnect")) return disconnectYouTube();
      var archiveId = button.getAttribute("data-cg-archive");
      if (archiveId) {
        api("/api/cosmos/genesis/projects/" + encodeURIComponent(archiveId) + "/archive", { method: "POST" })
          .then(function () { S.project = null; toast("Project archived."); return refreshActiveTab(); })
          .catch(function (err) { S.error = err.message || String(err); repaint(); });
        return;
      }
      var attach = button.getAttribute("data-cg-attach-media");
      if (attach) {
        var parts = attach.split("|");
        openMediaPicker({ projectId: S.activeProjectId, target: parts[0], sceneId: parts[1] || null });
        return;
      }
      var removeSceneId = button.getAttribute("data-cg-remove-scene");
      if (removeSceneId) { event.preventDefault(); return removeScene(removeSceneId); }
      var refRemove = button.getAttribute("data-cg-ref-remove");
      if (refRemove) {
        var refBits = refRemove.split("|");
        return removeRef(refBits[0], refBits[1]);
      }
      if (button.hasAttribute("data-cg-add-scene")) { event.preventDefault(); return addScene(); }
    });

    host.addEventListener("keydown", function (event) {
      if (event.key !== "Enter") return;
      var openRow = event.target.closest("[data-cg-open-project]");
      if (openRow) {
        S.activeProjectId = openRow.getAttribute("data-cg-open-project");
        loadProject(S.activeProjectId).then(repaint);
      }
    });

    host.addEventListener("change", function (event) {
      if (event.target.matches("[data-cg-queue-search], [data-cg-project-search], [data-cg-media-search]")) {
        return; // handled on input below
      }
    });

    host.addEventListener("input", function (event) {
      if (event.target.matches("[data-cg-queue-search]")) {
        S.queueQuery = event.target.value;
        debounce("queue", function () { refreshActiveTab(); });
      }
      if (event.target.matches("[data-cg-project-search]")) {
        S.projectQuery = event.target.value;
        debounce("projects", function () { refreshActiveTab(); });
      }
      if (event.target.matches("[data-cg-media-search]")) {
        S.mediaQuery = event.target.value;
        debounce("media", function () {
          loadMedia().then(function () { openMediaPicker(S.attach); });
        });
      }
    });

    // Poll while renders or publications are in flight so the states shown
    // are the real backend states.
    S.timer = window.setInterval(function () {
      if (document.hidden) return;
      if (S.activeTab !== "renders" && S.activeTab !== "overview" && S.activeTab !== "projects") return;
      var active = (S.renders || []).some(function (render) {
        return ["queued", "preparing", "waiting_media", "assembling"].indexOf(render.status) !== -1;
      }) || (S.publications || []).some(function (pub) {
        return pub.status === "publishing";
      });
      if (active) refreshActiveTab();
    }, 4000);
  }

  var debounces = {};
  function debounce(key, fn) {
    if (debounces[key]) clearTimeout(debounces[key]);
    debounces[key] = setTimeout(fn, 350);
  }

  // -----------------------------------------------------------------------
  // Public surface
  // -----------------------------------------------------------------------
  window.CosmosGenesis = {
    render: render,
    refresh: refreshActiveTab,
    state: function () { return S; }
  };
})();
