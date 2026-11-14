/* =====================================================================
   COSMOS · agent dashboard UI.

   One independent dashboard per intelligence agent. Every agent shows the
   same two permanent divisions: Ads Intelligence (left) and YouTube
   Intelligence (right). The two divisions share services but never share
   data or history.

   Rendering is driven by js/cosmosState.js. This module owns the DOM and the
   HTTP calls only. It never executes a capability client-side: discovery,
   strategy, creative, research and performance runs all go through the
   backend engine and the configured model.
   ===================================================================== */
(function () {
  "use strict";

  var C = window.CosmosState;
  if (!C) return;

  var S = {
    state: C.emptyState(),
    backlogAds: [],
    backlogYoutube: null,
    modalSubmit: null,
    toastTimer: null
  };

  var STATUS_LABELS = {
    proposed: "Proposed",
    drafted: "Drafted",
    rejected: "Rejected",
    refined: "Refined",
    running: "Running",
    succeeded: "Succeeded",
    failed: "Failed",
    discovered: "Discovered",
    reviewing: "Reviewing",
    approved: "Approved",
    ready_for_creation: "Ready for creation",
    in_production: "In production",
    published: "Published",
    archived: "Archived"
  };

  var ADS_STATUS_ORDER = [
    "discovered", "drafted", "reviewing", "approved",
    "ready_for_creation", "in_production", "published", "archived"
  ];

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

  function toast(message, tone) {
    if (window.Studio && window.Studio.toast) {
      window.Studio.toast(message);
      return;
    }
    var el = $("cosmos-toast");
    if (!el) return;
    el.textContent = message;
    el.className = "cosmos-toast" + (tone ? " is-" + tone : "");
    if (S.toastTimer) clearTimeout(S.toastTimer);
    S.toastTimer = setTimeout(function () {
      el.className = "cosmos-toast hidden";
    }, 5200);
  }

  // -----------------------------------------------------------------------
  // Data loading
  // -----------------------------------------------------------------------
  function loadAgent(agentId) {
    var id = C.normalizeAgent(agentId);
    var base = "/api/cosmos/agents/" + encodeURIComponent(id);
    return Promise.all([
      api(base),
      api(base + "/recommendations?division=ads&limit=60"),
      api(base + "/recommendations?division=youtube&limit=60"),
      api(base + "/executions?division=ads&limit=12"),
      api(base + "/executions?division=youtube&limit=12")
    ]).then(function (results) {
      C.mergeAgentDetail(S.state, results[0].agent);
      var ads = C.divisionState(S.state, id, "ads");
      ads.recommendations = results[1].recommendations || [];
      ads.loaded = true;
      var youtube = C.divisionState(S.state, id, "youtube");
      youtube.recommendations = results[2].recommendations || [];
      youtube.loaded = true;
      C.divisionState(S.state, id, "ads").executions = results[3].executions || [];
      C.divisionState(S.state, id, "youtube").executions = results[4].executions || [];
    });
  }

  function loadBacklogs() {
    return Promise.all([
      api("/api/cosmos/backlog/ads?limit=12").catch(function () { return { products: [] }; }),
      api("/api/cosmos/backlog/youtube").catch(function () { return { counts: {}, topics: [] }; })
    ]).then(function (results) {
      S.backlogAds = results[0].products || [];
      S.backlogYoutube = results[1] || { counts: {}, topics: [] };
    });
  }

  function refresh(options) {
    var opts = options || {};
    S.state.error = null;
    S.state.notice = null;
    S.state.loading = !S.state.initialized;
    renderChrome();
    return api("/api/cosmos/overview").then(function (overview) {
      C.mergeOverview(S.state, overview);
      if (!opts.keepAgent) {
        var route = C.parseHash(window.location.hash);
        if (route.agent) C.setActiveAgent(S.state, route.agent);
      }
      return loadAgent(S.state.activeAgent);
    }).then(function () {
      return loadBacklogs();
    }).catch(function (err) {
      S.state.error = err.message || String(err);
    }).then(function () {
      S.state.loading = false;
      render();
    });
  }

  function selectAgent(agentId) {
    var id = C.setActiveAgent(S.state, agentId);
    try {
      window.history.replaceState(null, "", C.hashForAgent(id));
    } catch (err) { /* hash is cosmetic */ }
    S.state.error = null;
    S.state.notice = null;
    render();
    loadAgent(id).catch(function (err) {
      S.state.error = err.message || String(err);
    }).then(render);
  }

  function reloadCurrentAgent() {
    // Refresh the overview too: drafting and status changes move counts that
    // other agents' headers and the backlog blocks display.
    return api("/api/cosmos/overview").then(function (overview) {
      C.mergeOverview(S.state, overview);
      return loadAgent(S.state.activeAgent);
    }).then(function () {
      return loadBacklogs();
    }).then(render).catch(function (err) {
      S.state.error = err.message || String(err);
      render();
    });
  }

  // -----------------------------------------------------------------------
  // Render helpers
  // -----------------------------------------------------------------------
  function badge(status) {
    var tone = C.statusTone(status);
    return '<span class="cosmos-badge is-' + tone + '">' + esc(STATUS_LABELS[status] || status || "—") + "</span>";
  }

  function evidenceBadge(value) {
    return '<span class="cosmos-evidence">' + esc(C.evidenceLabel(value)) + "</span>";
  }

  function sourceList(sources) {
    var rows = Array.isArray(sources) ? sources.filter(function (source) {
      return source && (source.url || source.label);
    }) : [];
    if (!rows.length) return '<p class="cosmos-muted">No sources supplied — treated as an AI hypothesis.</p>';
    return '<ul class="cosmos-sources">' + rows.map(function (source) {
      var label = esc(source.label || source.url || "Source");
      if (source.url && /^https?:\/\//i.test(source.url)) {
        return '<li><a href="' + attr(source.url) + '" target="_blank" rel="noopener noreferrer">' + label + "</a></li>";
      }
      return "<li>" + label + "</li>";
    }).join("") + "</ul>";
  }

  function formatPrice(price, fallbackCurrency) {
    if (!price) return "—";
    if (typeof price === "object") {
      if (price.amount === null || price.amount === undefined) return "—";
      return String(price.currency || fallbackCurrency || "") + " " + esc(price.amount) + (price.verified ? " · verified" : " · unverified");
    }
    return esc(price);
  }

  function payloadRows(item, division) {
    var payload = item || {};
    var rows = [];
    function push(label, value) {
      if (value === null || value === undefined || value === "") return;
      rows.push('<div class="cosmos-kv"><span>' + esc(label) + "</span><b>" + esc(value) + "</b></div>");
    }
    if (division === "ads") {
      push("Merchant", payload.merchant);
      push("Category", payload.category);
      push("Price", formatPrice(payload.price, payload.currency));
      push("Target audience", payload.target_audience);
      push("Creative format", payload.creative_format);
      push("Affiliate", payload.affiliate_status || "unvalidated");
    } else {
      push("Pillar", payload.pillar);
      push("Audience question", payload.audience_question);
      push("Format", payload.suggested_format);
      push("Search intent", payload.search_intent);
      push("Commercial relevance", payload.commercial_relevance);
      push("Video style", payload.video_style);
    }
    if (!rows.length) return "";
    return '<div class="cosmos-kv-grid">' + rows.join("") + "</div>";
  }

  function recommendationCard(item, division) {
    var actions = C.recommendationActions(item.status);
    var payload = item.payload || {};
    var score = Number(item.relevance_score);
    var html = [
      '<article class="cosmos-card" data-status="' + attr(item.status) + '">',
      '  <header class="cosmos-card-head">',
      '    <div class="cosmos-card-title"><h4>' + esc(item.title) + "</h4>",
      '      <div class="cosmos-card-badges">' + badge(item.status) + evidenceBadge(item.evidence_status),
      '      <span class="cosmos-score">' + (isFinite(score) ? esc(score) : "—") + "/100</span></div>",
      "    </div>",
      '    <span class="cosmos-card-when">' + esc(C.formatWhen(item.created_iso)) + "</span>",
      "  </header>",
      '  <p class="cosmos-card-summary">' + esc(item.summary || payload.reason || payload.description || "") + "</p>",
      payloadRows(payload, division),
      item.opportunity ? '<p class="cosmos-note"><b>Opportunity:</b> ' + esc(item.opportunity) + "</p>" : "",
      item.competition ? '<p class="cosmos-note"><b>Competition:</b> ' + esc(item.competition) + "</p>" : "",
      item.affiliate_validation
        ? '<p class="cosmos-note"><b>Affiliate check:</b> ' + esc(item.affiliate_validation.status) +
          (item.affiliate_validation.program ? " · " + esc(item.affiliate_validation.program) : "") + "</p>"
        : "",
      item.destination
        ? '<p class="cosmos-note cosmos-destination">Drafted to ' + esc(item.destination.type === "youtube_topic" ? "Topic Backlog" : "Product Backlog") + " · " + esc(item.destination.id) + "</p>"
        : "",
      '<details class="cosmos-card-more"><summary>Sources &amp; provenance</summary>' +
        sourceList(item.sources) +
        '<p class="cosmos-muted">Execution ' + esc(item.execution_id || "—") + " · template " + esc(item.template_id || "—") + "</p>" +
      "</details>",
      '  <footer class="cosmos-card-actions">',
      actions.draft ? '<button type="button" class="cosmos-btn is-primary" data-cosmos-action="draft" data-cosmos-id="' + attr(item.id) + '">' + (division === "ads" ? "Draft to Product Backlog" : "Draft to Topic Backlog") + "</button>" : "",
      actions.refine ? '<button type="button" class="cosmos-btn" data-cosmos-action="refine" data-cosmos-id="' + attr(item.id) + '">Refine</button>' : "",
      division === "ads" && actions.validateAffiliate ? '<button type="button" class="cosmos-btn" data-cosmos-action="affiliate" data-cosmos-id="' + attr(item.id) + '">Affiliate check</button>' : "",
      actions.reject ? '<button type="button" class="cosmos-btn is-danger" data-cosmos-action="reject" data-cosmos-id="' + attr(item.id) + '">Reject</button>' : "",
      "  </footer>",
      "</article>"
    ];
    return html.filter(Boolean).join("\n");
  }

  function executionRow(item) {
    var tone = C.statusTone(item.status);
    var error = item.error ? '<p class="cosmos-run-error">' + esc(item.error.code) + ": " + esc(item.error.message) + "</p>" : "";
    var meta = [];
    if (item.duration_ms !== null && item.duration_ms !== undefined) meta.push(C.formatDuration(item.duration_ms));
    if (item.model) meta.push(item.model);
    if (item.duplicates_skipped) meta.push(item.duplicates_skipped + " duplicate(s) skipped");
    if (item.filtered_out) meta.push(item.filtered_out + " filtered");
    return [
      '<article class="cosmos-run">',
      '  <div class="cosmos-run-head"><span class="cosmos-badge is-' + tone + '">' + esc(STATUS_LABELS[item.status] || item.status) + "</span>",
      "    <b>" + esc(item.capability || "run") + "</b>",
      '    <span class="cosmos-card-when">' + esc(C.formatWhen(item.finished_iso || item.started_iso)) + "</span></div>",
      '  <p class="cosmos-run-task">' + esc(item.task) + "</p>",
      meta.length ? '<p class="cosmos-muted">' + esc(meta.join(" · ")) + "</p>" : "",
      error,
      "  <details><summary>Result</summary>",
      item.result && item.result.summary ? "<p>" + esc(item.result.summary) + "</p>" : '<p class="cosmos-muted">No result stored.</p>',
      item.result && item.result.insufficient_results ? '<p class="cosmos-muted">Fewer qualified items than required; no filler was added.</p>' : "",
      item.result && item.result.filtered && item.result.filtered.length
        ? '<ul class="cosmos-filtered">' + item.result.filtered.slice(0, 8).map(function (entry) {
            return "<li><b>" + esc(entry.title) + "</b> — " + esc(entry.reason) + "</li>";
          }).join("") + "</ul>"
        : "",
      "  </details>",
      "</article>"
    ].filter(Boolean).join("\n");
  }

  function emptyBlock(title, body) {
    return '<div class="cosmos-empty"><b>' + esc(title) + "</b><p>" + esc(body) + "</p></div>";
  }

  function configBlock(division, agent) {
    var state = S.state;
    var templates = C.templatesFor(state, division);
    var active = C.activeTemplateId(state, division);
    var rows = [];
    if (division === "youtube") {
      var channels = state.config.channels || [];
      if (channels.length) {
        rows.push(
          '<label class="cosmos-field"><span>Channel</span><select data-cosmos-channel>' +
          channels.map(function (channel) {
            var selected = channel.id === state.config.activeYoutubeChannelId ? " selected" : "";
            return '<option value="' + attr(channel.id) + '"' + selected + ">" + esc(channel.name) + "</option>";
          }).join("") + "</select></label>"
        );
      }
    }
    rows.push(
      '<label class="cosmos-field"><span>' + (division === "ads" ? "Ads template" : "Video template") + "</span><select data-cosmos-config=\"" + division + "\">" +
      templates.map(function (template) {
        var selected = template.id === active ? " selected" : "";
        return '<option value="' + attr(template.id) + '"' + selected + ">" + esc(template.name) + "</option>";
      }).join("") + "</select></label>"
    );
    rows.push('<button type="button" class="cosmos-btn is-ghost" data-cosmos-edit-template="' + division + '">Edit template</button>');
    var template = C.templateById(state, division, active);
    return [
      '<section class="cosmos-config">',
      '  <div class="cosmos-config-head"><span>Active configuration</span><em>' + esc(template ? template.name : "No template") + "</em></div>",
      '  <div class="cosmos-config-grid">' + rows.join("") + "</div>",
      template && template.description ? '<p class="cosmos-config-desc">' + esc(template.description) + "</p>" : "",
      "</section>"
    ].join("\n");
  }

  function composerBlock(division, agent) {
    var meta = (agent.divisions || {})[division] || {};
    var capability = (agent.capabilities || {})[division] || {};
    var local = C.divisionState(S.state, agent.id, division);
    var disabled = local.busy ? " disabled" : "";
    return [
      '<section class="cosmos-composer">',
      '  <div class="cosmos-composer-head">',
      "    <div><h3>" + esc(capability.label || "Intelligence run") + "</h3>",
      "    <p>Instruction overrides configuration. Configuration overrides template defaults.</p></div>",
      '    <span class="cosmos-capability">' + esc(capability.id || "") + "</span>",
      "  </div>",
      '  <label class="cosmos-field"><span>Task</span><textarea data-cosmos-field="task" rows="3" maxlength="8000" placeholder="' + attr(capability.task_placeholder || "What should this agent do?") + '"' + disabled + ">" + esc(local.task) + "</textarea></label>",
      '  <details class="cosmos-notes"><summary>Owner-supplied source notes (optional)</summary>',
      '    <textarea data-cosmos-field="notes" rows="3" maxlength="20000" placeholder="Paste only facts, links or measurements you can verify. These are treated as owner-supplied sources."' + disabled + ">" + esc(local.notes) + "</textarea>",
      "  </details>",
      local.error ? '<p class="cosmos-error">' + esc(local.error) + "</p>" : "",
      '  <div class="cosmos-composer-actions">',
      '    <button type="button" class="cosmos-btn is-primary" data-cosmos-run="' + division + '"' + disabled + ">" +
      (local.busy ? "Running…" : esc(capability.action_label || "Run")) + "</button>",
      '    <span class="cosmos-muted">' + esc(capability.kind === "recommendations" ? "Qualified cards only; no filler." : "Evidence-labelled output; nothing is published.") + "</span>",
      "  </div>",
      "</section>"
    ].join("\n");
  }

  function responsibilityList(agent, division) {
    var meta = (agent.divisions || {})[division] || {};
    var items = meta.responsibilities || [];
    if (!items.length) return "";
    return '<ul class="cosmos-responsibilities">' + items.map(function (item) {
      return "<li>" + esc(item) + "</li>";
    }).join("") + "</ul>";
  }

  function adsBacklogBlock() {
    var counts = (S.state.counts.ads_backlog || {});
    var rows = (S.backlogAds || []).slice(0, 8);
    var options = rows.length ? rows.map(function (product) {
      var currentIndex = ADS_STATUS_ORDER.indexOf(product.status);
      var allowed = ADS_STATUS_ORDER.slice(Math.max(0, currentIndex)).concat(["archived"]).filter(function (status, index, list) {
        return list.indexOf(status) === index;
      });
      return [
        '<div class="cosmos-backlog-row">',
        '  <div class="cosmos-backlog-main"><b>' + esc(product.name) + "</b>",
        '    <span class="cosmos-muted">' + esc(product.merchant || "No merchant") + " · " + esc(formatPrice(product.price, "PHP")) + "</span>",
        product.provenance && product.provenance.agent_id
          ? '<span class="cosmos-provenance">from ' + esc(product.provenance.agent_id.toUpperCase()) + "</span>"
          : "",
        "  </div>",
        '  <select data-cosmos-product="' + attr(product.id) + '" aria-label="Product status">' +
        allowed.map(function (status) {
          return '<option value="' + attr(status) + '"' + (status === product.status ? " selected" : "") + ">" + esc(STATUS_LABELS[status] || status) + "</option>";
        }).join("") + "</select>",
        "</div>"
      ].join("\n");
    }).join("") : "";
    return [
      '<section class="cosmos-backlog">',
      '  <div class="cosmos-section-head"><div><h3>Ads Product Backlog</h3>',
      '    <p>Drafts land here first. Publishing is never automatic.</p></div>',
      '    <span class="cosmos-count">' + esc(counts.total || 0) + " active</span></div>",
      rows.length ? '<div class="cosmos-backlog-list">' + options + "</div>" : emptyBlock("No products yet", "Draft an Ads recommendation, or open Ads Studio to add one manually."),
      '  <button type="button" class="cosmos-btn is-ghost" data-cosmos-open-studio="ads">Open Ads Studio backlog</button>',
      "</section>"
    ].join("\n");
  }

  function youtubeBacklogBlock() {
    var data = S.backlogYoutube || {};
    var counts = data.counts || (S.state.counts.youtube_backlog || {});
    var topics = (data.topics || []).slice(0, 8);
    var summary = Object.keys(counts).filter(function (key) {
      return key !== "total" && counts[key];
    }).map(function (key) {
      return esc((STATUS_LABELS[key] || key) + ": " + counts[key]);
    }).join(" · ");
    return [
      '<section class="cosmos-backlog">',
      '  <div class="cosmos-section-head"><div><h3>YouTube Topic Backlog</h3>',
      '    <p>The existing Topic Backlog remains the only topic destination.</p></div>',
      '    <span class="cosmos-count">' + esc(data.total || counts.total || 0) + " topics</span></div>",
      summary ? '<p class="cosmos-muted">' + summary + "</p>" : "",
      topics.length ? '<div class="cosmos-backlog-list">' + topics.map(function (topic) {
        return [
          '<div class="cosmos-backlog-row">',
          '  <div class="cosmos-backlog-main"><b>' + esc(topic.title) + "</b>",
          '    <span class="cosmos-muted">' + esc(topic.priority || "P2") + " · " + esc(C.formatWhen(topic.created_iso)) + "</span></div>",
          badge(topic.status),
          "</div>"
        ].join("\n");
      }).join("") + "</div>" : emptyBlock("No topics yet", "Draft a YouTube recommendation into the Topic Backlog."),
      '  <button type="button" class="cosmos-btn is-ghost" data-cosmos-open-studio="youtube">Open YouTube Studio backlog</button>',
      "</section>"
    ].join("\n");
  }

  function divisionPanel(division, agent) {
    var local = C.divisionState(S.state, agent.id, division);
    var meta = (agent.divisions || {})[division] || {};
    var title = meta.title || C.divisionTitle(division);
    var cards = C.filterDivision(local.recommendations, division);
    var executions = local.executions || [];
    var pending = cards.filter(function (item) { return item.status === "proposed"; }).length;
    return [
      '<section class="cosmos-division" data-division="' + division + '">',
      '  <header class="cosmos-division-head">',
      '    <div class="cosmos-division-title"><span class="cosmos-division-mark">' + (division === "ads" ? "A" : "Y") + "</span>",
      "      <div><h2>" + esc(title) + "</h2><p>" + esc(division === "ads" ? "Products, merchants, creative, evidence and Ads performance." : "Topics, channel strategy, creative, research and YouTube performance.") + "</p></div></div>",
      '    <div class="cosmos-division-stats"><span><b>' + pending + "</b> proposed</span><span><b>" + cards.length + "</b> cards</span><span><b>" + executions.length + "</b> runs</span></div>",
      "  </header>",
      responsibilityList(agent, division),
      configBlock(division, agent),
      composerBlock(division, agent),
      '<section class="cosmos-results">',
      '  <div class="cosmos-section-head"><div><h3>Recommendations</h3><p>Owner review required. Nothing is drafted automatically.</p></div>',
      '    <span class="cosmos-count">' + cards.length + "</span></div>",
      cards.length ? cards.map(function (item) { return recommendationCard(item, division); }).join("\n")
        : emptyBlock("No cards yet", "Run " + (((agent.capabilities || {})[division] || {}).action_label || "the capability") + " to produce reviewable cards."),
      "</section>",
      division === "ads" ? adsBacklogBlock() : youtubeBacklogBlock(),
      '<section class="cosmos-history">',
      '  <div class="cosmos-section-head"><div><h3>Execution history</h3><p>This agent\'s own runs in this division only.</p></div>',
      '    <span class="cosmos-count">' + executions.length + "</span></div>",
      executions.length ? executions.map(executionRow).join("\n")
        : emptyBlock("No runs yet", "Every run stores its model, configuration snapshot and honest result."),
      "</section>",
      "</section>"
    ].join("\n");
  }

  function agentHeader(agent) {
    var counts = (C.agentState(S.state, agent.id).recommendationCounts) || {};
    var name = agent.name || C.normalizeAgent(agent.id).toUpperCase();
    var stat = function (label, value) {
      return "<span><b>" + esc(value || 0) + "</b>" + esc(label) + "</span>";
    };
    return [
      '<header class="cosmos-agent-head">',
      '  <div class="cosmos-agent-identity">',
      '    <span class="cosmos-agent-mark is-' + attr(agent.accent || "cyan") + '">' + esc(name.charAt(0)) + "</span>",
      "    <div>",
      '      <div class="cosmos-agent-name">' + esc(name) + ' <span>' + esc(agent.role || "Intelligence agent") + "</span></div>",
      '      <p class="cosmos-agent-desc">' + esc(agent.description || "") + "</p>",
      '      <p class="cosmos-agent-tagline">' + esc(agent.tagline || "") + "</p>",
      "    </div>",
      "  </div>",
      '  <div class="cosmos-agent-stats">' + stat(" proposed", counts.proposed) + stat(" drafted", counts.drafted) + stat(" rejected", counts.rejected) + "</div>",
      "</header>"
    ].join("\n");
  }

  function renderChrome() {
    var subtitle = $("cosmos-subtitle");
    if (subtitle) {
      subtitle.textContent = S.state.initialized
        ? "Five independent agents. Two permanent divisions each: Ads Intelligence on the left, YouTube Intelligence on the right."
        : "Loading the COSMOS registry…";
    }
    var runtime = $("cosmos-runtime");
    if (runtime) {
      var llm = S.state.llm || {};
      var configured = !!llm.configured;
      runtime.className = "cosmos-runtime" + (configured ? " is-ready" : " is-warn");
      runtime.innerHTML = '<span class="cosmos-dot"></span><b>' + esc(configured ? (llm.model || "Model ready") : "Model not configured") + "</b><em>" +
        esc(configured ? (llm.provider || "shared Studio routing") : "Runs will report the error honestly") + "</em>";
    }
    var tabs = $("cosmos-agent-tabs");
    if (tabs) {
      var agents = S.state.agents.length ? S.state.agents : C.AGENT_ORDER.map(function (id) {
        return { id: id, name: id.toUpperCase(), role: "" };
      });
      tabs.innerHTML = agents.map(function (agent) {
        var id = C.normalizeAgent(agent.id);
        var active = id === S.state.activeAgent;
        var local = C.agentState(S.state, id);
        var pending = local.recommendationCounts && local.recommendationCounts.proposed;
        return '<button type="button" role="tab" aria-selected="' + (active ? "true" : "false") + '" class="cosmos-agent-tab' + (active ? " is-active" : "") + '" data-cosmos-agent="' + attr(id) + '">' +
          '<span class="cosmos-agent-tab-mark is-' + attr(agent.accent || "cyan") + '">' + esc(String(agent.name || id).charAt(0)) + "</span>" +
          '<span class="cosmos-agent-tab-copy"><b>' + esc(agent.name || id.toUpperCase()) + "</b><em>" + esc(agent.role || "") + "</em></span>" +
          (pending ? '<span class="cosmos-agent-tab-count">' + esc(pending) + "</span>" : "") +
          "</button>";
      }).join("");
    }
  }

  function alertBlock() {
    var el = $("cosmos-alert");
    if (!el) return;
    if (S.state.error) {
      el.className = "cosmos-alert is-error";
      el.innerHTML = '<i data-lucide="triangle-alert"></i><div><b>Something went wrong</b><p>' + esc(S.state.error) + "</p></div>";
      return;
    }
    if (S.state.notice) {
      el.className = "cosmos-alert is-" + (S.state.notice.tone || "ok");
      el.innerHTML = '<i data-lucide="' + (S.state.notice.tone === "warn" ? "info" : "check-circle-2") + '"></i><div><b>' + esc(S.state.notice.title) + "</b><p>" + esc(S.state.notice.body) + "</p></div>";
      return;
    }
    el.className = "cosmos-alert hidden";
    el.innerHTML = "";
  }

  function render() {
    renderChrome();
    alertBlock();
    var host = $("cosmos-agent");
    if (!host) return;
    if (S.state.loading && !S.state.initialized) {
      host.innerHTML = '<div class="cosmos-loading"><span class="cosmos-spinner"></span><b>Loading the five agents…</b><p>Reading local configuration, executions and backlogs.</p></div>';
      icons();
      return;
    }
    var agent = C.agentById(S.state, S.state.activeAgent);
    if (!agent) {
      host.innerHTML = '<div class="cosmos-empty"><b>Agent unavailable</b><p>Refresh to reload the COSMOS registry.</p></div>';
      icons();
      return;
    }
    host.innerHTML = agentHeader(agent) + '<div class="cosmos-divisions">' +
      divisionPanel("ads", agent) + divisionPanel("youtube", agent) + "</div>";
    icons();
  }

  // -----------------------------------------------------------------------
  // Modals
  // -----------------------------------------------------------------------
  function closeModal() {
    var modal = $("cosmos-modal");
    if (modal) modal.classList.add("hidden");
    S.modalSubmit = null;
  }

  function openModal(title, bodyHtml, submitLabel, onSubmit, tone) {
    var modal = $("cosmos-modal");
    if (!modal) return;
    $("cosmos-modal-title").textContent = title;
    $("cosmos-modal-body").innerHTML = bodyHtml;
    var submit = $("cosmos-modal-submit");
    submit.textContent = submitLabel || "Save";
    submit.className = "cosmos-btn " + (tone === "danger" ? "is-danger" : "is-primary");
    submit.disabled = false;
    S.modalSubmit = onSubmit;
    modal.classList.remove("hidden");
    icons();
    var first = modal.querySelector("input, textarea, select");
    if (first) first.focus();
  }

  function modalError(message) {
    var el = $("cosmos-modal-error");
    if (el) {
      el.textContent = message || "";
      el.classList.toggle("hidden", !message);
    }
  }

  function runModalSubmit() {
    if (!S.modalSubmit) return;
    var submit = $("cosmos-modal-submit");
    submit.disabled = true;
    modalError("");
    Promise.resolve(S.modalSubmit()).then(function () {
      submit.disabled = false;
      closeModal();
    }).catch(function (err) {
      submit.disabled = false;
      modalError(err.message || String(err));
    });
  }

  function openTemplateEditor(division) {
    var template = C.templateById(S.state, division, C.activeTemplateId(S.state, division));
    if (!template) {
      toast("No template selected.", "warn");
      return;
    }
    var fields = [];
    function textField(name, label, value) {
      fields.push('<label class="cosmos-field"><span>' + esc(label) + "</span><input data-template-field=\"" + attr(name) + '" value="' + attr(value || "") + '"></label>');
    }
    function listField(name, label, value) {
      fields.push('<label class="cosmos-field"><span>' + esc(label) + " (one per line)</span><textarea rows=\"3\" data-template-field=\"" + attr(name) + '">' + esc((value || []).join("\n")) + "</textarea></label>");
    }
    textField("name", "Name", template.name);
    textField("description", "Description", template.description);
    if (division === "ads") {
      textField("product_category", "Product category", template.product_category);
      textField("target_market", "Target market", template.target_market);
      textField("target_audience", "Target audience", template.target_audience);
      textField("affiliate_strategy", "Affiliate strategy", template.affiliate_strategy);
      textField("creative_style", "Creative style", template.creative_style);
      textField("video_format", "Video format", template.video_format);
      listField("product_interests", "Product interests", template.product_interests || []);
      listField("merchants", "Merchants", template.merchants || []);
      listField("marketplaces", "Marketplaces", template.marketplaces || []);
      listField("target_platforms", "Target platforms", template.target_platforms || []);
      listField("creative_directions", "Creative directions", template.creative_directions || []);
      listField("product_restrictions", "Product restrictions", template.product_restrictions || []);
      var range = template.price_range || {};
      fields.push('<div class="cosmos-field-row"><label class="cosmos-field"><span>Price min</span><input type="number" min="0" data-template-range="min" value="' + attr(range.min == null ? "" : range.min) + '"></label>' +
        '<label class="cosmos-field"><span>Price max</span><input type="number" min="0" data-template-range="max" value="' + attr(range.max == null ? "" : range.max) + '"></label>' +
        '<label class="cosmos-field"><span>Currency</span><input data-template-range="currency" value="' + attr(range.currency || "PHP") + '"></label></div>');
    } else {
      textField("video_style", "Video style", template.video_style);
      textField("visual_approach", "Visual approach", template.visual_approach);
      textField("script_direction", "Script direction", template.script_direction);
      listField("structure", "Structure", template.structure || []);
      listField("audience_interests", "Audience interests", template.audience_interests || []);
      listField("restrictions", "Restrictions", template.restrictions || []);
      fields.push('<label class="cosmos-field"><span>Format</span><select data-template-format><option value="longform"' + (template.format === "longform" ? " selected" : "") + '>Longform</option><option value="shorts"' + (template.format === "shorts" ? " selected" : "") + ">Shorts</option></select></label>");
      fields.push('<label class="cosmos-field"><span>Default duration (minutes)</span><input type="number" min="1" max="120" data-template-duration value="' + attr(template.default_duration_min || 12) + '"></label>');
    }
    var body = '<p class="cosmos-modal-note">Editing <b>' + esc(template.name) + "</b>. Past executions keep the snapshot they ran with.</p>" +
      '<div id="cosmos-modal-error" class="cosmos-error hidden"></div>' +
      '<div class="cosmos-template-form">' + fields.join("") + "</div>";

    openModal("Edit " + (division === "ads" ? "Ads" : "YouTube") + " template", body, "Save template", function () {
      var payload = {};
      var host = $("cosmos-modal-body");
      Array.prototype.forEach.call(host.querySelectorAll("[data-template-field]"), function (input) {
        var name = input.getAttribute("data-template-field");
        var value = input.value;
        if (input.tagName === "TEXTAREA") {
          payload[name] = value.split("\n").map(function (row) { return row.trim(); }).filter(Boolean);
        } else {
          payload[name] = value;
        }
      });
      if (division === "ads") {
        payload.price_range = {
          min: Number(host.querySelector('[data-template-range="min"]').value || 0),
          max: Number(host.querySelector('[data-template-range="max"]').value || 0),
          currency: host.querySelector('[data-template-range="currency"]').value.trim() || "PHP"
        };
      } else {
        payload.format = host.querySelector("[data-template-format]").value;
        payload.default_duration_min = Number(host.querySelector("[data-template-duration]").value || 12);
      }
      return api("/api/cosmos/templates/" + division + "/" + encodeURIComponent(template.id), {
        method: "PATCH", body: payload
      }).then(function (data) {
        var list = division === "ads" ? "adsTemplates" : "youtubeTemplates";
        var templates = (S.state.config[list] || []).map(function (row) {
          return row.id === data.template.id ? data.template : row;
        });
        S.state.config[list] = templates;
        S.state.notice = { tone: "ok", title: "Template saved", body: "Future runs use the new configuration. Historical runs keep their snapshots." };
        toast("Template saved.");
        render();
      });
    });
  }

  function openRefineModal(recommendationId) {
    var card = findRecommendation(recommendationId);
    var body = '<p class="cosmos-modal-note">Refine <b>' + esc(card ? card.title : "this recommendation") + "</b>. The original card is kept and linked.</p>" +
      '<div id="cosmos-modal-error" class="cosmos-error hidden"></div>' +
      '<label class="cosmos-field"><span>Refinement instruction</span><textarea id="cosmos-refine-note" rows="4" maxlength="2000" placeholder="What should change? Keep it inside the active configuration."></textarea></label>';
    openModal("Refine recommendation", body, "Run refinement", function () {
      var instruction = $("cosmos-refine-note").value.trim();
      return api("/api/cosmos/recommendations/" + encodeURIComponent(recommendationId) + "/refine", {
        method: "POST", body: { instruction: instruction }
      }).then(function (data) {
        S.state.notice = {
          tone: "ok", title: "Refinement complete",
          body: data.recommendation ? data.recommendation.title : "The refined card is ready for review."
        };
        toast("Refined.");
        return reloadCurrentAgent();
      });
    });
  }

  function openAffiliateModal(recommendationId) {
    var card = findRecommendation(recommendationId);
    var body = '<p class="cosmos-modal-note">Affiliate validation is a separate owner operation. Discovery never proves an affiliate relationship. Only record what you can confirm.</p>' +
      '<div id="cosmos-modal-error" class="cosmos-error hidden"></div>' +
      '<label class="cosmos-field"><span>Affiliate program</span><input id="cosmos-aff-program" maxlength="200" placeholder="e.g. Shopee Affiliate"></label>' +
      '<label class="cosmos-field"><span>Tracking URL</span><input id="cosmos-aff-url" maxlength="1000" placeholder="https://…"></label>' +
      '<label class="cosmos-field"><span>Evidence note</span><input id="cosmos-aff-evidence" maxlength="1000" placeholder="e.g. Approved program dashboard"></label>' +
      '<label class="cosmos-check"><input type="checkbox" id="cosmos-aff-confirm"> I confirm this is a validated affiliate link for <b>' + esc(card ? card.title : "this product") + "</b>.</label>";
    openModal("Affiliate check", body, "Record status", function () {
      return api("/api/cosmos/recommendations/" + encodeURIComponent(recommendationId) + "/validate-affiliate", {
        method: "POST",
        body: {
          confirmed: $("cosmos-aff-confirm").checked,
          program: $("cosmos-aff-program").value.trim(),
          url: $("cosmos-aff-url").value.trim(),
          evidence: $("cosmos-aff-evidence").value.trim()
        }
      }).then(function (data) {
        S.state.notice = {
          tone: data.status === "validated" ? "ok" : "warn",
          title: data.status === "validated" ? "Affiliate link recorded" : "Validation pending",
          body: data.note || "No external provider was contacted."
        };
        toast(data.status === "validated" ? "Affiliate link recorded." : "Validation requested.");
        return reloadCurrentAgent();
      });
    });
  }

  function openRejectModal(recommendationId) {
    var body = '<p class="cosmos-modal-note">Rejection keeps the card in history and never inserts it into a backlog.</p>' +
      '<div id="cosmos-modal-error" class="cosmos-error hidden"></div>' +
      '<label class="cosmos-field"><span>Reason (optional)</span><input id="cosmos-reject-note" maxlength="500" placeholder="Why is this not a fit?"></label>';
    openModal("Reject recommendation", body, "Reject", function () {
      return api("/api/cosmos/recommendations/" + encodeURIComponent(recommendationId) + "/reject", {
        method: "POST", body: { note: $("cosmos-reject-note").value.trim() }
      }).then(function () {
        S.state.notice = { tone: "warn", title: "Recommendation rejected", body: "It stays in history and was not added to a backlog." };
        toast("Rejected.");
        return reloadCurrentAgent();
      });
    }, "danger");
  }

  function findRecommendation(recommendationId) {
    var id = String(recommendationId || "");
    var local = C.agentState(S.state, S.state.activeAgent);
    var found = null;
    ["ads", "youtube"].forEach(function (division) {
      (local[division].recommendations || []).forEach(function (item) {
        if (item.id === id) found = item;
      });
    });
    return found;
  }

  // -----------------------------------------------------------------------
  // Actions
  // -----------------------------------------------------------------------
  function execute(division) {
    var agentId = S.state.activeAgent;
    var local = C.divisionState(S.state, agentId, division);
    if (local.busy) return;
    local.error = null;
    C.setBusy(S.state, agentId, division, true);
    render();
    var body = { division: division, task: local.task, source_notes: local.notes };
    if (division === "youtube" && S.state.config.activeYoutubeChannelId) {
      body.channel_id = S.state.config.activeYoutubeChannelId;
    }
    api("/api/cosmos/agents/" + encodeURIComponent(agentId) + "/execute", {
      method: "POST", body: body
    }).then(function (data) {
      var execution = data.execution || {};
      var result = execution.result || {};
      var count = (execution.recommendation_ids || []).length;
      S.state.notice = {
        tone: result.insufficient_results ? "warn" : "ok",
        title: result.insufficient_results ? "Completed with fewer qualified results" : "Run complete",
        body: (result.summary || "Execution finished.") + (count ? " " + count + " card(s) added." : " No cards were added.")
      };
      toast(result.insufficient_results ? "Fewer qualified results than required." : "Run complete.");
    }).catch(function (err) {
      C.setDivisionError(S.state, agentId, division, err.message || String(err));
    }).then(function () {
      C.setBusy(S.state, agentId, division, false);
      return reloadCurrentAgent();
    });
  }

  function draftRecommendation(recommendationId) {
    api("/api/cosmos/recommendations/" + encodeURIComponent(recommendationId) + "/draft", { method: "POST" })
      .then(function (data) {
        var destination = data.destination || {};
        S.state.notice = {
          tone: "ok", title: "Drafted",
          body: destination.type === "youtube_topic"
            ? "Added to the existing YouTube Topic Backlog as an idea."
            : "Added to the Ads Product Backlog as a drafted product."
        };
        toast("Drafted to the backlog.");
        return reloadCurrentAgent();
      }).catch(function (err) {
        S.state.notice = { tone: "warn", title: "Draft blocked", body: err.message || String(err) };
        return reloadCurrentAgent();
      });
  }

  function changeProductStatus(productId, status) {
    api("/api/cosmos/backlog/ads/" + encodeURIComponent(productId) + "/status", {
      method: "POST", body: { status: status }
    }).then(function () {
      toast("Backlog status updated.");
      return reloadCurrentAgent();
    }).catch(function (err) {
      S.state.notice = { tone: "warn", title: "Status not changed", body: err.message || String(err) };
      return reloadCurrentAgent();
    });
  }

  function setActiveTemplate(division, templateId) {
    api("/api/cosmos/config/active", {
      method: "POST", body: { domain: division, template_id: templateId }
    }).then(function () {
      if (division === "ads") S.state.config.activeAdsTemplateId = templateId;
      else S.state.config.activeYoutubeTemplateId = templateId;
      toast("Active template updated.");
      render();
    }).catch(function (err) {
      S.state.error = err.message || String(err);
      render();
    });
  }

  function setActiveChannel(channelId) {
    api("/api/cosmos/config/active", {
      method: "POST", body: { channel_id: channelId }
    }).then(function () {
      S.state.config.activeYoutubeChannelId = channelId;
      toast("Active channel updated.");
      render();
    }).catch(function (err) {
      S.state.error = err.message || String(err);
      render();
    });
  }

  function openStudio(workspace) {
    closeModal();
    if (window.StudioWorkspaces) {
      window.switchMainView("studio");
      setTimeout(function () {
        try { window.StudioWorkspaces.go(workspace, workspace === "ads" ? "backlog" : "backlog"); } catch (err) { /* fall back to default */ }
      }, 0);
      return;
    }
    window.location.hash = workspace === "ads" ? "#studio/ads/backlog" : "#studio/youtube/backlog";
    window.switchMainView("studio");
  }

  // -----------------------------------------------------------------------
  // Events
  // -----------------------------------------------------------------------
  function bind() {
    var tabs = $("cosmos-agent-tabs");
    if (tabs) {
      tabs.addEventListener("click", function (event) {
        var button = event.target.closest("[data-cosmos-agent]");
        if (button) selectAgent(button.getAttribute("data-cosmos-agent"));
      });
    }

    var refreshBtn = $("cosmos-refresh");
    if (refreshBtn) refreshBtn.addEventListener("click", function () { refresh({ keepAgent: true }); });

    var host = $("cosmos-agent");
    if (host) {
      host.addEventListener("input", function (event) {
        var field = event.target.getAttribute("data-cosmos-field");
        if (!field) return;
        var division = event.target.closest("[data-division]").getAttribute("data-division");
        if (field === "task") C.setTask(S.state, S.state.activeAgent, division, event.target.value);
        if (field === "notes") C.setNotes(S.state, S.state.activeAgent, division, event.target.value);
      });

      host.addEventListener("change", function (event) {
        var configSelect = event.target.getAttribute("data-cosmos-config");
        if (configSelect) return setActiveTemplate(configSelect, event.target.value);
        if (event.target.hasAttribute("data-cosmos-channel")) return setActiveChannel(event.target.value);
        var product = event.target.getAttribute("data-cosmos-product");
        if (product) return changeProductStatus(product, event.target.value);
      });

      host.addEventListener("click", function (event) {
        var button = event.target.closest("button");
        if (!button) return;
        var run = button.getAttribute("data-cosmos-run");
        if (run) return execute(run);
        var edit = button.getAttribute("data-cosmos-edit-template");
        if (edit) return openTemplateEditor(edit);
        var open = button.getAttribute("data-cosmos-open-studio");
        if (open) return openStudio(open);
        var action = button.getAttribute("data-cosmos-action");
        if (!action) return;
        var id = button.getAttribute("data-cosmos-id");
        if (action === "draft") return draftRecommendation(id);
        if (action === "refine") return openRefineModal(id);
        if (action === "affiliate") return openAffiliateModal(id);
        if (action === "reject") return openRejectModal(id);
      });
    }

    var modal = $("cosmos-modal");
    if (modal) {
      modal.addEventListener("click", function (event) {
        if (event.target === modal) closeModal();
      });
      var close = $("cosmos-modal-close");
      var cancel = $("cosmos-modal-cancel");
      var submit = $("cosmos-modal-submit");
      if (close) close.addEventListener("click", closeModal);
      if (cancel) cancel.addEventListener("click", closeModal);
      if (submit) submit.addEventListener("click", runModalSubmit);
    }
    document.addEventListener("keydown", function (event) {
      if (event.key === "Escape") closeModal();
    });

    window.addEventListener("hashchange", function () {
      var route = C.parseHash(window.location.hash);
      if (route.view !== "cosmos") return;
      var view = $("cosmos-view");
      if (view && view.classList.contains("hidden") && window.switchMainView) {
        // A pasted #cosmos/<agent> link should open COSMOS from any view.
        window.switchMainView("cosmos");
        return;
      }
      if (route.agent && route.agent !== S.state.activeAgent) selectAgent(route.agent);
    });
  }

  function init() {
    if (S.initialized) return;
    S.initialized = true;
    bind();
    refresh();
  }

  window.Cosmos = {
    init: init,
    // switchMainView may call refresh before this module's own DOMContentLoaded
    // listener runs; init() is idempotent, so bind once and load once.
    refresh: function () {
      if (!S.initialized) {
        init();
        return Promise.resolve();
      }
      return refresh();
    },
    selectAgent: selectAgent,
    state: function () { return S.state; }
  };

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
