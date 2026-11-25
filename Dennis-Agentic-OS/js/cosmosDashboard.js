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
    research: null,
    competitors: null,
    modalSubmit: null,
    toastTimer: null,
    // Shared router session link, disclosed only on an explicit owner click.
    youtubeLink: null,
    // Owner preset choices for the open Refine/Reject modal (learning loop).
    presetChoice: null,
    // Poll timer that follows an autonomous top-up after a draft.
    topupTimer: null,
    topupTries: 0,
    // Per-division Agent/Model dropdowns (same Workspace pattern as Studio).
    llmAgents: [],
    llmAgentsLoading: null,
    llmModels: {},
    llmProviders: {},
    llmModelsLoading: {},
    llmExpanded: {},
    llmOpenMenu: null,
    llmBusy: false
  };

  // Preset feedback templates: selecting one records why the card missed and
  // is injected into future discovery prompts (the agent learns the owner).
  var REJECT_PRESETS = [
    { id: "not_relevant", label: "Not relevant to my template" },
    { id: "price_out_of_range", label: "Price outside my range" },
    { id: "weak_demand", label: "Weak demand evidence" },
    { id: "wrong_audience", label: "Wrong audience" },
    { id: "already_covered", label: "Already covered / duplicated" },
    { id: "low_viral_risk", label: "Little viral potential" }
  ];
  var REFINE_PRESETS = [
    { id: "seasonal_angle", label: "Seasonal angle (Christmas, birthdays, festivals)" },
    { id: "cheaper_alternative", label: "Find a cheaper alternative" },
    { id: "better_angle", label: "Better product/topic angle" },
    { id: "different_audience", label: "Aim at a different audience" },
    { id: "stronger_hook", label: "Stronger viral hook" },
    { id: "tighter_niche", label: "Tighten to a narrower niche" }
  ];
  var BOARD_SIZE = 5;

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
  // QUASAR research workflows (its YouTube domain hosts the migrated screens)
  // -----------------------------------------------------------------------
  function fetchResearch() {
    return Promise.all([
      api("/api/studio/youtube/research").catch(function () { return { validations: [] }; }),
      api("/api/studio/youtube/competitors").catch(function () { return { competitors: [] }; })
    ]).then(function (results) {
      S.research = results[0] || { validations: [] };
      S.competitors = results[1] || { competitors: [] };
    });
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

  function refresh(options) {
    var opts = options || {};
    S.state.error = null;
    S.state.notice = null;
    S.state.loading = !S.state.initialized;
    renderChrome();
    loadLLMAgents(false); // Workspace agent registry for the division dropdowns
    return api("/api/cosmos/overview").then(function (overview) {
      C.mergeOverview(S.state, overview);
      if (!opts.keepAgent) {
        var route = C.parseHash(window.location.hash);
        if (route.agent) C.setActiveAgent(S.state, route.agent);
      }
      if (S.state.activeAgent === C.GENESIS_ID) {
        // GENESIS loads its own production data through its own module.
        return null;
      }
      return loadAgent(S.state.activeAgent);
    }).then(function () {
      // QUASAR's YouTube domain hosts the migrated research workflows.
      if (S.state.activeAgent === "quasar") return fetchResearch();
      return null;
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
    if (id === C.GENESIS_ID) {
      if (window.CosmosGenesis) window.CosmosGenesis.refresh();
      return;
    }
    loadAgent(id).catch(function (err) {
      S.state.error = err.message || String(err);
    }).then(function () {
      if (id === "quasar") {
        // QUASAR's YouTube domain hosts the migrated research workflows.
        return fetchResearch().catch(function () { /* shown as empty state */ });
      }
      return null;
    }).then(render);
  }

  function reloadCurrentAgent() {
    // Refresh the overview too: drafting and status changes move counts that
    // other agents' headers and the backlog blocks display.
    if (S.state.activeAgent === C.GENESIS_ID) {
      if (window.CosmosGenesis) window.CosmosGenesis.refresh();
      return Promise.resolve();
    }
    return api("/api/cosmos/overview").then(function (overview) {
      C.mergeOverview(S.state, overview);
      return loadAgent(S.state.activeAgent);
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

  // Product/topic candidate criteria — why the agent selected this card,
  // what it could earn, how viral it could be, the demand behind it and the
  // local occasion it fits (Christmas, birthdays, festivals, evergreen…).
  function criteriaRows(payload) {
    var rows = [];
    function push(label, value) {
      if (value === null || value === undefined || value === "") return;
      rows.push('<div class="cosmos-kv"><span>' + esc(label) + "</span><b>" + esc(value) + "</b></div>");
    }
    push("Why selected", payload.why_selected);
    push("Potential sales", payload.sales_potential);
    push("Virality potential", payload.virality_potential);
    push("Market demand", payload.market_demand);
    push("Local occasion", payload.local_occasion);
    if (!rows.length) return "";
    return '<div class="cosmos-kv-grid cosmos-criteria">' + rows.join("") + "</div>";
  }

  // The verifiable link below a card: a recorded affiliate URL first, then
  // the marketplace listing URL. Opens in a new tab for the owner to check.
  function cardVerifyUrl(item, payload) {
    if (item.affiliate_validation && item.affiliate_validation.url) {
      return item.affiliate_validation.url;
    }
    return payload.product_url || payload.url || null;
  }

  function thumbFallback(division, payload) {
    var initial = ((payload && (payload.category || payload.pillar)) || "").trim().charAt(0);
    return '<span class="cosmos-thumb-fallback is-' + division + '">' +
      esc(initial || (division === "ads" ? "P" : "T")) + "</span>";
  }

  function recommendationCard(item, division) {
    var actions = C.recommendationActions(item.status);
    var payload = item.payload || {};
    var score = Number(item.relevance_score);
    var verifyUrl = cardVerifyUrl(item, payload);
    // The fallback letter sits underneath the image; if the image fails to
    // load it removes itself and the fallback shows through.
    var thumb = '<div class="cosmos-card-thumb">' + thumbFallback(division, payload) +
      (payload.image_url
        ? '<img src="' + attr(payload.image_url) + '" alt="" loading="lazy" onerror="this.remove()">'
        : "") +
      "</div>";
    var quickInfo = item.summary || payload.why_selected || payload.description || payload.reason || "";
    var html = [
      '<article class="cosmos-card is-thumb" data-status="' + attr(item.status) + '" data-cosmos-id="' + attr(item.id) + '">',
      '  <div class="cosmos-card-hover"><b>' + esc(item.title) + "</b>" +
        (quickInfo ? "<p>" + esc(String(quickInfo).slice(0, 220)) + "</p>" : "") + "</div>",
      '  <div class="cosmos-card-row">',
      thumb,
      '    <div class="cosmos-card-body">',
      '      <div class="cosmos-card-line"><h4 title="' + attr(item.title) + '">' + esc(item.title) + "</h4>" +
        '<span class="cosmos-score" title="Relevance score">' + (isFinite(score) ? esc(score) : "—") + "/100</span></div>",
      verifyUrl
        ? '<a class="cosmos-card-link" href="' + attr(verifyUrl) + '" target="_blank" rel="noopener noreferrer" title="Open and verify">' +
          '<i data-lucide="external-link" class="w-3 h-3"></i> ' + esc(item.affiliate_validation && item.affiliate_validation.url ? "Affiliate link" : "Verify on " + (payload.merchant || "merchant")) + "</a>"
        : (division === "ads" ? '<span class="cosmos-card-link is-none">No link yet — validate affiliate</span>' : ""),
      '    </div>',
      "  </div>",
      '  <footer class="cosmos-card-actions">',
      actions.draft ? '<button type="button" class="cosmos-btn is-primary" data-cosmos-action="draft" data-cosmos-id="' + attr(item.id) + '">Draft</button>' : "",
      '<button type="button" class="cosmos-btn" data-cosmos-action="info" data-cosmos-id="' + attr(item.id) + '">Info</button>',
      actions.refine ? '<button type="button" class="cosmos-btn" data-cosmos-action="refine" data-cosmos-id="' + attr(item.id) + '">Refine</button>' : "",
      division === "ads" && actions.validateAffiliate ? '<button type="button" class="cosmos-btn" data-cosmos-action="affiliate" data-cosmos-id="' + attr(item.id) + '">Affiliate</button>' : "",
      actions.reject ? '<button type="button" class="cosmos-btn is-danger is-ghost" data-cosmos-action="reject" data-cosmos-id="' + attr(item.id) + '">Reject</button>' : "",
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

  function platformChip(division) {
    // Ads division: registered commerce platform (Shopee) the agent sources
    // product candidates from. Registration captures the owner's affiliate
    // program context; the chip reflects its status.
    var platform = C.adsPlatform(S.state, "shopee");
    var connected = !!(platform && platform.connected);
    var detail = connected
      ? (platform.handle || platform.region || "registered")
      : "not registered";
    return [
      '<div class="cosmos-platform-chip' + (connected ? " is-on" : "") + '" title="' +
        attr(connected ? ("Shopee registered — " + detail) : "Register the Shopee affiliate program this division sources from") + '">',
      '  <span class="cosmos-platform-dot"></span>',
      "  <span class=\"cosmos-platform-copy\"><b>Shopee</b><em>" + esc(detail) + "</em></span>",
      '  <button type="button" class="cosmos-btn is-ghost" data-cosmos-platform-shopee>' + (connected ? "Manage" : "Connect") + "</button>",
      "</div>"
    ].join("\n");
  }

  function channelChip(agent) {
    // YouTube division: editorial channel profile + live connected identity.
    // Connection state comes only from the shared integration router; the
    // seeded Studio profile is never presented as proof of a live connection.
    var live = C.youtubeChannel(S.state);
    var connected = live.status === "connected";
    var detail = connected
      ? (live.handle || "connected via Composio")
      : C.youtubeChannelStatusLabel(S.state);
    var channels = S.state.config.channels || [];
    var activeId = S.state.config.activeYoutubeChannelId;
    var rows = [];
    if (channels.length) {
      rows.push(
        '<label class="cosmos-field"><span>Editorial profile</span><select data-cosmos-channel>' +
        channels.map(function (channel) {
          var selected = channel.id === activeId ? " selected" : "";
          return '<option value="' + attr(channel.id) + '"' + selected + ">" + esc(channel.name) + "</option>";
        }).join("") + "</select></label>"
      );
    }
    var title = connected
      ? ("Live channel: " + live.name)
      : (live.error || "The connected YouTube channel could not be read.");
    return [
      '<div class="cosmos-platform-chip' + (connected ? " is-on" : "") + '" title="' + attr(title) + '">',
      '  <span class="cosmos-platform-dot"></span>',
      '  <span class="cosmos-platform-copy"><b>' + esc(connected ? live.name : "Connected YouTube channel") + "</b><em>" +
        esc(detail) + "</em></span>",
      '  <button type="button" class="cosmos-btn is-ghost" data-cosmos-channel-register>Register profile</button>',
      "</div>"
    ].concat(rows).join("\n");
  }

  function youtubeStateChip(live) {
    var status = live.status;
    var tone = status === "connected" ? "is-on"
      : (status === "unavailable" ? "is-warn" : "");
    var label = C.youtubeChannelStatusLabel(S.state);
    return [
      '<span class="cosmos-youtube-state-chip ' + tone + '" title="' +
        attr(live.error || "Live channel identity from the shared YouTube integration") + '">',
      '  <span class="cosmos-platform-dot"></span>',
      "  " + esc(label),
      "</span>"
    ].join("");
  }

  function youtubeActionChip(agent, live) {
    // ANDROMEDA owns the compact connect/refresh action; every other agent
    // only reads the shared identity. The flow never auto-opens the link.
    if (C.normalizeAgent(agent.id) !== "andromeda") return youtubeStateChip(live);
    var connected = live.status === "connected";
    return [
      '<button type="button" class="cosmos-youtube-link-chip' + (connected ? " is-on" : "") +
        '" data-cosmos-youtube-link title="' +
        attr(connected ? "Refresh the connected YouTube channel" : "Disclose the shared YouTube session connect link") + '">',
      '  <span class="cosmos-platform-dot"></span>',
      "  " + (connected ? "Refresh" : "Connect"),
      "</button>"
    ].join("");
  }

  function youtubeAvatarMarkup(live) {
    if (!live.avatar_url) {
      return '<span class="cosmos-division-mark">' +
        esc((live.name || "Y").trim().charAt(0).toUpperCase()) + "</span>";
    }
    return [
      '<span class="cosmos-division-mark cosmos-division-avatar">',
      '  <span class="cosmos-avatar-fallback">Y</span>',
      '  <img src="' + attr(live.avatar_url) + '" alt="">',
      "</span>"
    ].join("");
  }

  function configBlock(division, agent) {
    var state = S.state;
    var templates = C.templatesFor(state, division);
    var active = C.activeTemplateId(state, division);
    var rows = [];
    if (division === "ads") {
      rows.push(
        '<label class="cosmos-field"><span>Ads template</span><select data-cosmos-config="ads">' +
        templates.map(function (template) {
          var selected = template.id === active ? " selected" : "";
          return '<option value="' + attr(template.id) + '"' + selected + ">" + esc(template.name) + "</option>";
        }).join("") + "</select></label>"
      );
      rows.push('<button type="button" class="cosmos-btn is-ghost" data-cosmos-edit-template="ads">Edit template</button>');
    } else {
      rows.push(
        '<label class="cosmos-field"><span>Video template</span><select data-cosmos-config="youtube">' +
        templates.map(function (template) {
          var selected = template.id === active ? " selected" : "";
          return '<option value="' + attr(template.id) + '"' + selected + ">" + esc(template.name) + "</option>";
        }).join("") + "</select></label>"
      );
      rows.push('<button type="button" class="cosmos-btn is-ghost" data-cosmos-edit-template="youtube">Edit template</button>');
    }
    var template = C.templateById(state, division, active);
    return [
      '<section class="cosmos-config">',
      '  <div class="cosmos-config-head"><span>Active configuration</span><em>' + esc(template ? template.name : "No template") + "</em></div>",
      '  <div class="cosmos-config-grid">' + rows.join("") + "</div>",
      '  <div class="cosmos-config-registration">' + (division === "ads" ? platformChip("ads") : channelChip(agent)) + "</div>",
      "</section>"
    ].join("\n");
  }

  /* -----------------------------------------------------------------------
     Per-division Agent + Model dropdowns (Workspace pattern, one row per
     division composer). A division with no stored choice inherits the shared
     Studio selection; choosing here overrides it for this division only.
     ----------------------------------------------------------------------- */
  function loadLLMAgents(force) {
    if (S.llmAgentsLoading && !force) return S.llmAgentsLoading;
    S.llmAgentsLoading = api("/api/agents/availability").then(function (data) {
      S.llmAgents = (data && Array.isArray(data.agents)) ? data.agents : [];
      S.llmAgentsLoading = null;
      refreshLLMRowLabels();
      return S.llmAgents;
    }).catch(function () {
      S.llmAgents = [];
      S.llmAgentsLoading = null;
      refreshLLMRowLabels();
      return [];
    });
    return S.llmAgentsLoading;
  }

  function loadLLMModels(agentId, force) {
    if (!agentId) return Promise.resolve([]);
    if (!force && S.llmModels[agentId]) return Promise.resolve(S.llmModels[agentId]);
    if (S.llmModelsLoading[agentId]) return S.llmModelsLoading[agentId];
    S.llmModelsLoading[agentId] = api("/api/agents/" + encodeURIComponent(agentId) + "/models" + (force ? "?refresh=1" : ""))
      .then(function (data) {
        S.llmModels[agentId] = (data && Array.isArray(data.models)) ? data.models : [];
        S.llmProviders[agentId] = (data && Array.isArray(data.providers)) ? data.providers : [];
        delete S.llmModelsLoading[agentId];
        renderOpenModelMenu(agentId);
        return S.llmModels[agentId];
      })
      .catch(function () {
        S.llmModels[agentId] = [];
        S.llmProviders[agentId] = [];
        delete S.llmModelsLoading[agentId];
        renderOpenModelMenu(agentId);
        return [];
      });
    return S.llmModelsLoading[agentId];
  }

  function llmAgentById(agentId) {
    return S.llmAgents.filter(function (agent) { return agent.id === agentId; })[0] || null;
  }

  function llmAgentLabel(agentId) {
    var agent = llmAgentById(agentId);
    return (agent && (agent.name || agent.id)) || agentId || "—";
  }

  function llmRowEl(division, selector) {
    var host = $("cosmos-agent");
    if (!host) return null;
    var row = host.querySelector('[data-cosmos-llm-row="' + division + '"]');
    return row ? row.querySelector(selector) : null;
  }

  function llmSelectionFor(division) {
    return C.llmSelection(S.state, S.state.activeAgent, division);
  }

  function closeLLMMenus() {
    S.llmOpenMenu = null;
    var host = $("cosmos-agent");
    if (!host) return;
    host.querySelectorAll(".cosmos-llm-menu").forEach(function (menu) {
      menu.classList.add("hidden");
    });
  }

  function refreshLLMRowLabels() {
    ["ads", "youtube"].forEach(function (division) {
      var selection = llmSelectionFor(division);
      var agentLabel = llmRowEl(division, '[data-cosmos-llm-label="agent"]');
      if (agentLabel) agentLabel.textContent = llmAgentLabel(selection.agent_id);
      var modelLabel = llmRowEl(division, '[data-cosmos-llm-label="model"]');
      if (modelLabel) modelLabel.textContent = selection.model || "Agent default";
      var inherit = llmRowEl(division, "[data-cosmos-llm-inherit]");
      if (inherit) inherit.classList.toggle("hidden", !selection.inherited);
      var dot = llmRowEl(division, "[data-cosmos-llm-dot]");
      if (dot) dot.style.background = selection.agent_id ? "#22c55e" : "#475569";
    });
  }

  function renderLLMAgentOptions(division) {
    var host = llmRowEl(division, '[data-cosmos-llm-options="agent"]');
    if (!host) return;
    var selection = llmSelectionFor(division);
    if (!S.llmAgents.length) {
      host.innerHTML = '<div class="cosmos-llm-empty">No agents registered — refresh the Workspace agent list.</div>';
      return;
    }
    host.innerHTML = S.llmAgents.map(function (agent) {
      var isActive = agent.id === selection.agent_id;
      var available = agent.available !== false;
      var dot = available ? (isActive ? "#22c55e" : "#94a3b8") : "#475569";
      var meta = [agent.provider, available ? "ready" : (agent.reason || "unavailable")].filter(Boolean).join(" · ");
      return '<button type="button" class="ws-menu-item' + (isActive ? " ws-menu-item-active" : "") +
        (available ? "" : " ws-menu-item-disabled") + '" data-cosmos-llm-agent="' + esc(agent.id) + '"' +
        (available ? "" : " disabled") + ">" +
        '<span class="ws-agent-dot" style="background:' + dot + '"></span>' +
        '<span class="cosmos-llm-item-copy">' +
          '<span class="cosmos-llm-item-name">' + esc(agent.name || agent.id) + "</span>" +
          '<span class="cosmos-llm-item-meta">' + esc(meta) + "</span>" +
        "</span>" +
        "</button>";
    }).join("");
    icons();
  }

  function llmModelDisplay(model) {
    if (!model) return "";
    var name = model.name || model.id || "";
    var prefix = (model.provider || "") + "/";
    if (model.provider && name.indexOf(prefix) === 0) return name.slice(prefix.length);
    return name;
  }

  /* Default expansion: the group holding the current model (or the first
     group) is open; everything the user toggled stays as they left it. */
  function llmGroupOpen(division, groupId, models, currentModel) {
    var remembered = S.llmExpanded[division + ":" + groupId];
    if (remembered !== undefined) return remembered;
    var currentProvider = null;
    (models || []).forEach(function (model) {
      if (model.id === currentModel) currentProvider = model.provider || "__default__";
    });
    if (!currentProvider && models && models.length) currentProvider = models[0].provider || "__default__";
    return groupId === currentProvider;
  }

  function renderLLMModelOptions(division, agentId) {
    var host = llmRowEl(division, '[data-cosmos-llm-options="model"]');
    if (!host) return;
    var selection = llmSelectionFor(division);
    if (!agentId) {
      host.innerHTML = '<div class="cosmos-llm-empty">Pick an agent first.</div>';
      return;
    }
    if (!S.llmModels[agentId] && S.llmModelsLoading[agentId]) {
      host.innerHTML = '<div class="cosmos-llm-empty">Loading models…</div>';
      return;
    }
    var models = S.llmModels[agentId] || [];
    if (!models.length) {
      host.innerHTML = '<div class="cosmos-llm-empty">No models reported for this agent — use Refresh.</div>';
      return;
    }
    var search = llmRowEl(division, "[data-cosmos-llm-search]");
    var query = ((search && search.value) || "").trim().toLowerCase();
    if (query) {
      models = models.filter(function (model) {
        return ((model.name || model.id || "") + " " + (model.provider || "")).toLowerCase().indexOf(query) !== -1;
      });
    }
    if (!models.length) {
      host.innerHTML = '<div class="cosmos-llm-empty">No models match that search.</div>';
      return;
    }
    /* Group by the provider each model reports, like the Workspace menu. */
    var order = [];
    models.forEach(function (model) {
      var id = model.provider || "__default__";
      if (order.indexOf(id) === -1) order.push(id);
    });
    var providers = S.llmProviders[agentId] || [];
    var groups = order.map(function (id) {
      var groupModels = models.filter(function (model) { return (model.provider || "__default__") === id; });
      var provider = providers.filter(function (p) { return p.id === id; })[0];
      return {
        id: id,
        label: id === "__default__" ? "Default" : ((provider && provider.label) || id),
        models: groupModels,
        available: groupModels.some(function (model) { return model.available !== false; })
      };
    });
    host.innerHTML = groups.map(function (group) {
      var open = query ? true : llmGroupOpen(division, group.id, S.llmModels[agentId] || [], selection.model);
      var body = group.models.map(function (model) {
        var active = model.id === selection.model;
        var off = model.available === false;
        return '<button type="button" class="ws-menu-item' + (active ? " ws-menu-item-active" : "") +
          (off ? " ws-menu-item-disabled" : "") + '" data-cosmos-llm-model="' + esc(model.id) + '"' +
          (off ? " disabled" : "") + ">" + esc(llmModelDisplay(model)) + "</button>";
      }).join("");
      return '<div class="model-provider-group" data-open="' + (open ? "true" : "false") + '">' +
        '<button type="button" class="model-provider-group-head" data-cosmos-llm-provider="' + esc(group.id) + '"' +
          ' aria-expanded="' + (open ? "true" : "false") + '">' +
          '<span class="ws-agent-dot" style="background:' + (group.available ? "#22c55e" : "#475569") + '"></span>' +
          '<span class="cosmos-llm-group-label">' + esc(group.label) + "</span>" +
          '<span class="model-provider-group-count">' + esc(group.models.length + (group.models.length === 1 ? " model" : " models")) + "</span>" +
          '<i data-lucide="chevron-down" class="model-provider-group-caret w-3 h-3"></i>' +
        "</button>" +
        '<div class="model-provider-group-body">' + body + "</div>" +
      "</div>";
    }).join("");
    icons();
  }

  function renderOpenModelMenu(agentId) {
    ["ads", "youtube"].forEach(function (division) {
      var selection = llmSelectionFor(division);
      if (selection.agent_id !== agentId) return;
      var menu = llmRowEl(division, '[data-cosmos-llm-menu="model"]');
      if (menu && !menu.classList.contains("hidden")) {
        renderLLMModelOptions(division, agentId);
      }
    });
  }

  function toggleLLMMenu(division, kind) {
    var menu = llmRowEl(division, '[data-cosmos-llm-menu="' + kind + '"]');
    if (!menu) return;
    var willOpen = menu.classList.contains("hidden");
    closeLLMMenus();
    if (!willOpen) return;
    menu.classList.remove("hidden");
    S.llmOpenMenu = S.state.activeAgent + ":" + division + ":" + kind;
    var selection = llmSelectionFor(division);
    if (kind === "agent") {
      renderLLMAgentOptions(division);
    } else if (S.llmModels[selection.agent_id]) {
      renderLLMModelOptions(division, selection.agent_id);
    } else {
      // First open: show the loading placeholder; the fetch re-renders the
      // open menu through renderOpenModelMenu when it finishes.
      var options = llmRowEl(division, '[data-cosmos-llm-options="model"]');
      if (options) options.innerHTML = '<div class="cosmos-llm-empty">Loading models…</div>';
      loadLLMModels(selection.agent_id, false);
    }
  }

  function configureDivisionLLM(division, body) {
    var agentId = S.state.activeAgent;
    S.llmBusy = true;
    return api("/api/cosmos/llm/" + encodeURIComponent(agentId) + "/" + division, { method: "POST", body: body })
      .then(function (payload) {
        C.setLLMSelection(S.state, agentId, division, payload.selection || body);
        toast("Agent + model saved for this division.");
      })
      .catch(function (error) {
        toast(error.message);
      })
      .then(function () {
        S.llmBusy = false;
        S.llmOpenMenu = null;
        render();
      });
  }

  function llmRowBlock(division) {
    var selection = llmSelectionFor(division);
    var agentName = llmAgentLabel(selection.agent_id);
    return [
      '<div class="cosmos-llm-row" data-cosmos-llm-row="' + division + '" data-cosmos-llm-division="' + division + '">',
      '  <div class="cosmos-llm-field">',
      '    <span>Agent</span>',
      '    <button type="button" class="cosmos-llm-btn" data-cosmos-llm-toggle="agent" title="Agent that runs this division">',
      '      <span class="ws-agent-dot" data-cosmos-llm-dot style="background:' + (selection.agent_id ? "#22c55e" : "#475569") + '"></span>',
      '      <span class="cosmos-llm-btn-label" data-cosmos-llm-label="agent">' + esc(agentName) + "</span>",
      '      <i data-lucide="chevron-down" class="cosmos-llm-caret w-3 h-3"></i>',
      "    </button>",
      '    <div class="ws-menu cosmos-llm-menu hidden" data-cosmos-llm-menu="agent">',
      '      <div class="cosmos-llm-menu-list" data-cosmos-llm-options="agent"></div>',
      "    </div>",
      "  </div>",
      '  <div class="cosmos-llm-field">',
      "    <span>Model</span>",
      '    <button type="button" class="cosmos-llm-btn" data-cosmos-llm-toggle="model" title="Generation model for this division">',
      '      <span class="cosmos-llm-btn-label" data-cosmos-llm-label="model">' + esc(selection.model || "Agent default") + "</span>",
      '      <i data-lucide="chevron-down" class="cosmos-llm-caret w-3 h-3"></i>',
      "    </button>",
      '    <div class="ws-menu cosmos-llm-menu hidden" data-cosmos-llm-menu="model">',
      '      <div class="cosmos-llm-menu-tools">',
      '        <input type="text" class="cosmos-llm-search" data-cosmos-llm-search placeholder="Search models…" autocomplete="off" spellcheck="false">',
      '        <button type="button" class="cosmos-llm-refresh" data-cosmos-llm-refresh title="Refresh models"><i data-lucide="refresh-cw" class="w-3 h-3"></i></button>',
      "      </div>",
      '      <div class="cosmos-llm-menu-list" data-cosmos-llm-options="model"></div>',
      "    </div>",
      "  </div>",
      '  <span class="cosmos-llm-inherit' + (selection.inherited ? "" : " hidden") + '" data-cosmos-llm-inherit>Studio default</span>',
      "</div>"
    ].join("\n");
  }

  function autonomyBlock(division, agent) {
    // Autonomous (default) runs on the six-hour scheduler and tops up after
    // each draft. Override hands control to the owner: selecting it expands
    // the task prompt box and the Discover button.
    var capability = (agent.capabilities || {})[division] || {};
    var local = C.divisionState(S.state, agent.id, division);
    var mode = C.autonomyMode(S.state, agent.id, division);
    var info = C.autonomyInfo(S.state, agent.id, division);
    var disabled = local.busy ? " disabled" : "";
    var isAutonomous = mode === "autonomous";
    var statusLine = "";
    if (isAutonomous) {
      var next = info.next_run_iso ? C.formatWhen(info.next_run_iso) : "within 6h";
      var last = info.last_run_iso ? C.formatWhen(info.last_run_iso) : "not yet";
      statusLine = '<p class="cosmos-autonomy-status">Next autonomous run ' + esc(next) +
        " · last " + esc(last) + " · keeps " + BOARD_SIZE + " cards</p>";
    }
    return [
      '<section class="cosmos-autonomy" data-cosmos-autonomy="' + division + '">',
      '  <div class="cosmos-mode-toggle" role="tablist" aria-label="' + esc(capability.label || "Discovery") + ' mode">',
      '    <button type="button" class="cosmos-mode' + (isAutonomous ? " is-active" : "") + '" data-cosmos-mode="autonomous" data-cosmos-division="' + division + '"' + (local.busy ? " disabled" : "") + ">Autonomous</button>",
      '    <button type="button" class="cosmos-mode' + (!isAutonomous ? " is-active" : "") + '" data-cosmos-mode="override" data-cosmos-division="' + division + '"' + (local.busy ? " disabled" : "") + ">Override</button>",
      "  </div>",
      isAutonomous ? statusLine : "",
      !isAutonomous ? [
        '  <div class="cosmos-override" data-cosmos-override="' + division + '">',
        '    <label class="cosmos-field"><span>Task</span><textarea data-cosmos-field="task" rows="3" maxlength="8000" placeholder="' + attr(capability.task_placeholder || "What should this agent do?") + '"' + disabled + ">" + esc(local.task) + "</textarea></label>",
        '    <details class="cosmos-notes"><summary>Owner-supplied source notes (optional)</summary>',
        '      <textarea data-cosmos-field="notes" rows="3" maxlength="20000" placeholder="Paste only facts, links or measurements you can verify. These are treated as owner-supplied sources."' + disabled + ">" + esc(local.notes) + "</textarea>",
        "    </details>",
        local.error ? '<p class="cosmos-error">' + esc(local.error) + "</p>" : "",
        '    <div class="cosmos-composer-actions">',
        '      <button type="button" class="cosmos-btn is-primary" data-cosmos-run="' + division + '"' + disabled + ">" +
          (local.busy ? "Running…" : esc(capability.action_label || "Discover")) + "</button>",
        '      <span class="cosmos-muted">Manual run only — the scheduler leaves this division alone.</span>',
        "    </div>",
        "  </div>"
      ].join("\n") : "",
      "</section>"
    ].filter(Boolean).join("\n");
  }

  function divisionInfoButton(agent, division, title) {
    // "?" beside the division title. The bubble carries the agent summary
    // (moved out of the removed header panel) plus this division's
    // responsibilities. It only opens while hovering/focusing the button, so
    // it can never cover or block the Agent/Model dropdowns below.
    var meta = (agent.divisions || {})[division] || {};
    var items = meta.responsibilities || [];
    var name = agent.name || C.normalizeAgent(agent.id).toUpperCase();
    var label = title || meta.title || C.divisionTitle(division);
    return [
      '<span class="cosmos-division-info">',
      '  <button type="button" class="cosmos-info-btn" aria-label="About ' + esc(label) + '" title="What this division does">?</button>',
      '  <span class="cosmos-division-info-bubble" role="tooltip">',
      '    <span class="cosmos-division-info-name">' + esc(name) + "<span>" + esc(agent.role || "Intelligence agent") + "</span></span>",
      agent.tagline ? '<p class="cosmos-division-info-tagline">' + esc(agent.tagline) + "</p>" : "",
      agent.description ? "<p>" + esc(agent.description) + "</p>" : "",
      items.length ? '<span class="cosmos-division-info-label">' + esc(label) + " responsibilities</span>" +
        '<ul class="cosmos-responsibilities">' + items.map(function (item) {
          return "<li>" + esc(item) + "</li>";
        }).join("") + "</ul>" : "",
      "  </span>",
      "</span>"
    ].filter(Boolean).join("\n");
  }

  function cosmosTextField(name, label, value, required) {
    return '<label class="cosmos-field"><span>' + esc(label) + (required ? " *" : "") + '</span><input data-product-field="' + attr(name) + '" value="' + attr(value || "") + '"></label>';
  }

  function cosmosNumberField(name, label, value) {
    return '<label class="cosmos-field"><span>' + esc(label) + '</span><input type="number" min="0" step="0.01" data-product-field="' + attr(name) + '" value="' + attr(value == null ? "" : value) + '"></label>';
  }

  var YOUTUBE_STATUS_ORDER = ["idea", "researching", "validated", "approved", "scripted", "in_production", "published", "rejected"];

  function quasarResearchBlock() {
    // Research & evidence workflows migrated from YouTube Studio. QUASAR's
    // YouTube Intelligence domain is the intelligence-side home for analyst
    // validations and competitor observations (evidence rules unchanged).
    var data = S.research || {};
    var rules = data.rules || {};
    var validations = data.validations || [];
    var competitors = (S.competitors && S.competitors.competitors) || [];
    return [
      '<section class="cosmos-backlog">',
      '  <div class="cosmos-section-head"><div><h3>Research &amp; Evidence — YouTube</h3>',
      '    <p>Analyst validations and competitor observations, migrated from YouTube Studio. Scores remain analyst estimates, never measured platform data.</p></div>',
      '    <span class="cosmos-count">' + esc(validations.length) + " validations</span></div>",
      rules && rules.validation ? '<p class="cosmos-muted">Approval threshold: ' + esc(rules.validation.approval_threshold || 18) + "/" + esc(rules.validation.max_total || 25) + " — sourced criteria pass, flags trigger review.</p>" : "",
      validations.length ? '<div class="cosmos-backlog-list">' + validations.slice(0, 10).map(function (validation) {
        return [
          '<div class="cosmos-backlog-row">',
          '  <div class="cosmos-backlog-main"><b>' + esc(validation.subject) + "</b>",
          '    <span class="cosmos-muted">' + esc(validation.total_score) + "/" + esc(validation.max_total) + " · " + esc(validation.verdict) +
          (validation.topic_id ? " · topic " + esc(validation.topic_id) : "") + " · " + esc(C.formatWhen(validation.created_iso)) + "</span>",
          (validation.flags || []).length ? '<span class="cosmos-provenance">flags: ' + esc(validation.flags.join(", ")) + "</span>" : "",
          "  </div>",
          '  <div class="cosmos-backlog-actions">',
          validation.topic_id ? "" : '<button type="button" class="cosmos-btn is-ghost" data-cosmos-validation-topic="' + attr(validation.id) + '">To topic</button>',
          '  <button type="button" class="cosmos-btn is-danger is-ghost" data-cosmos-validation-delete="' + attr(validation.id) + '">Delete</button>',
          "  </div>",
          "</div>"
        ].join("\n");
      }).join("") + "</div>" : emptyBlock("No saved validations", "Validations created in research workflows are kept here with their sources."),
      '  <button type="button" class="cosmos-btn is-ghost" data-cosmos-add-validation>Record a validation</button>',
      "</section>",
      '<section class="cosmos-backlog">',
      '  <div class="cosmos-section-head"><div><h3>Competitor Intelligence</h3>',
      '    <p>Observations are tagged observed or assumed — assumptions never become evidence.</p></div>',
      '    <span class="cosmos-count">' + esc(competitors.length) + " competitors</span></div>",
      competitors.length ? '<div class="cosmos-backlog-list">' + competitors.slice(0, 10).map(function (competitor) {
        return [
          '<details class="cosmos-card-more cosmos-competitor"><summary><b>' + esc(competitor.name) + "</b>" +
          (competitor.reference ? ' <span class="cosmos-provenance">reference</span>' : "") + " — " +
          esc((competitor.observations || []).length + " observation(s)") + "</summary>",
          competitor.positioning ? '<p class="cosmos-muted">' + esc(competitor.positioning) + "</p>" : "",
          '<ul class="cosmos-sources">' + (competitor.observations || []).slice(-6).map(function (observation) {
            return "<li><b>" + esc(observation.basis || "observed") + "</b> — " + esc(observation.note) + "</li>";
          }).join("") + "</ul>",
          '<div class="cosmos-backlog-actions">',
          '  <input type="text" placeholder="New observation…" maxlength="600" data-cosmos-observation-input="' + attr(competitor.id) + '" aria-label="New observation for ' + attr(competitor.name) + '">',
          '  <button type="button" class="cosmos-btn is-ghost" data-cosmos-competitor-observe="' + attr(competitor.id) + '">Add observation</button>',
          "</div>",
          "</details>"
        ].join("");
      }).join("") + "</div>" : emptyBlock("No competitors tracked", "Add a competitor to start recording observations."),
      '  <button type="button" class="cosmos-btn is-ghost" data-cosmos-add-competitor>Add competitor</button>',
      "</section>"
    ].join("\n");
  }


  function divisionPanel(division, agent) {
    // One division, top to bottom: Agent/Model row, active configuration,
    // Autonomous/Override, the five-card board, collapsed execution history.
    var local = C.divisionState(S.state, agent.id, division);
    var meta = (agent.divisions || {})[division] || {};
    var live = C.youtubeChannel(S.state);
    var connected = division === "youtube" && C.youtubeChannelConnected(S.state);
    var title = C.divisionDisplayTitle(S.state, division, agent);
    var infoTitle = meta.title || C.divisionTitle(division);
    var mark = connected ? youtubeAvatarMarkup(live)
      : '<span class="cosmos-division-mark">' + (division === "ads" ? "A" : "Y") + "</span>";
    var actionChip = division === "youtube" ? youtubeActionChip(agent, live) : "";
    var cards = C.boardCards(S.state, agent.id, division);
    var executions = local.executions || [];
    return [
      '<section class="cosmos-division" data-division="' + division + '">',
      '  <header class="cosmos-division-head">',
      '    <div class="cosmos-division-title">' + mark,
      '      <div class="cosmos-division-heading"><h2>' + esc(title) + "</h2>" + actionChip +
        divisionInfoButton(agent, division, infoTitle) + "</div></div>",
      '    <div class="cosmos-division-stats"><span><b>' + cards.length + "</b>/" + BOARD_SIZE + " cards</span></div>",
      "  </header>",
      llmRowBlock(division),
      configBlock(division, agent),
      autonomyBlock(division, agent),
      '<section class="cosmos-results">',
      '  <div class="cosmos-section-head"><div><h3>Recommendations</h3><p>' +
        esc(division === "ads" ? "Product candidates sourced for you." : "Topic candidates sourced for you.") + "</p></div>",
      '    <span class="cosmos-count">' + cards.length + "</span></div>",
      cards.length ? cards.map(function (item) { return recommendationCard(item, division); }).join("\n")
        : emptyBlock("No cards yet", C.autonomyMode(S.state, agent.id, division) === "autonomous"
          ? "The agent fills this board automatically (every 6 hours) or you can switch to Override and run it now."
          : "Switch to Autonomous, or supply a task and press " + (((agent.capabilities || {})[division] || {}).action_label || "Discover") + "."),
      "</section>",
      (division === "youtube" && agent.id === "quasar") ? quasarResearchBlock() : "",
      '<details class="cosmos-history-details">',
      '  <summary><i data-lucide="chevron-right" class="cosmos-history-caret w-3 h-3"></i> Execution history' +
        '<span class="cosmos-count">' + executions.length + "</span></summary>",
      '  <div class="cosmos-history-body">' +
        (executions.length ? executions.map(executionRow).join("\n")
          : emptyBlock("No runs yet", "Every run stores its model, configuration snapshot and honest result.")) + "</div>",
      "</details>",
      "</section>"
    ].join("\n");
  }

  // Profile emblems: one per COSMOS agent + GENESIS, sliced from the owner's
  // six-panel emblems artwork into img/cosmos/<id>.png. Falls back to the
  // original letter mark for any unknown id.
  var EMBLEM_IDS = ["andromeda", "polaris", "pleiades", "quasar", "helios", "genesis"];
  function emblemSrc(id) {
    var n = C.normalizeAgent(id);
    return EMBLEM_IDS.indexOf(n) >= 0 ? "img/cosmos/" + n + ".png" : "";
  }
  function emblemMark(id, fallback, cls) {
    var src = emblemSrc(id);
    if (!src) return '<span class="cosmos-agent-mark ' + attr(cls || "") + '">' + esc(fallback || "?") + "</span>";
    return '<span class="cosmos-avatar ' + attr(cls || "") + '">' +
      '<img src="' + attr(src) + '" alt="' + attr(fallback || id) + ' emblem" loading="lazy" />' +
      '<span class="cosmos-avatar-fallback">' + esc(fallback || "?") + "</span></span>";
  }

  function renderChrome() {
    // The old COSMOS Intelligence Layer banner is gone; the chrome is just
    // the agent tab rail (with GENESIS) — kept element-safe if the markup
    // ever reintroduces a runtime badge.
    var runtime = $("cosmos-runtime");
    if (runtime) {
      var llm = S.state.llm || {};
      var configured = !!llm.configured;
      runtime.className = "cosmos-runtime" + (configured ? " is-ready" : " is-warn");
      runtime.innerHTML = '<span class="cosmos-dot"></span><b>' + esc(configured ? (llm.model || "Agent default") : "Model not configured") + "</b><em>" +
        esc(configured ? (llm.agent_name || llm.agent_id || "Studio routing") : "Runs will report the error honestly") + "</em>";
    }
    var tabs = $("cosmos-agent-tabs");
    if (tabs) {
      var agents = S.state.agents.length ? S.state.agents : C.AGENT_ORDER.map(function (id) {
        return { id: id, name: id.toUpperCase(), role: "" };
      });
      var agentTabs = agents.map(function (agent) {
        var id = C.normalizeAgent(agent.id);
        var active = id === S.state.activeAgent;
        var local = C.agentState(S.state, id);
        var pending = local.recommendationCounts && local.recommendationCounts.proposed;
        return '<button type="button" role="tab" aria-selected="' + (active ? "true" : "false") + '" class="cosmos-agent-tab' + (active ? " is-active" : "") + '" data-cosmos-agent="' + attr(id) + '">' +
          emblemMark(id, String(agent.name || id).charAt(0), "is-" + (agent.accent || "cyan")) +
          '<span class="cosmos-agent-tab-copy"><b>' + esc(agent.name || id.toUpperCase()) + "</b><em>" + esc(agent.role || "") + "</em></span>" +
          (pending ? '<span class="cosmos-agent-tab-count">' + esc(pending) + "</span>" : "") +
          "</button>";
      });
      // GENESIS: the shared production destination, visually distinguished
      // from the five intelligence agents. A system dashboard, not an agent.
      var genesisActive = S.state.activeAgent === C.GENESIS_ID;
      var genesisCounts = (S.state.counts && S.state.counts.genesis) || {};
      var genesisBusy = ((genesisCounts.renders || {}).active || 0) + ((genesisCounts.publications || {}).scheduled || 0);
      agentTabs.push('<button type="button" role="tab" aria-selected="' + (genesisActive ? "true" : "false") +
        '" class="cosmos-agent-tab cosmos-tab-genesis' + (genesisActive ? " is-active" : "") + '" data-cosmos-agent="' + attr(C.GENESIS_ID) + '">' +
        emblemMark(C.GENESIS_ID, "G", "is-genesis") +
        '<span class="cosmos-agent-tab-copy"><b>GENESIS</b><em>Production · Render · Publish</em></span>' +
        (genesisBusy ? '<span class="cosmos-agent-tab-count">' + esc(genesisBusy) + "</span>" : "") +
        "</button>");
      tabs.innerHTML = agentTabs.join("");
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
    // Menus are rebuilt closed; any open dropdown state is stale after this.
    S.llmOpenMenu = null;
    var host = $("cosmos-agent");
    if (!host) return;
    if (S.state.activeAgent === C.GENESIS_ID) {
      // GENESIS owns the host while its tab is active.
      if (window.CosmosGenesis) window.CosmosGenesis.render(host);
      return;
    }
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
    host.innerHTML = '<div class="cosmos-divisions">' +
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

  function openModal(title, bodyHtml, submitLabel, onSubmit, tone, opts) {
    var modal = $("cosmos-modal");
    if (!modal) return;
    $("cosmos-modal-title").textContent = title;
    $("cosmos-modal-body").innerHTML = bodyHtml;
    var submit = $("cosmos-modal-submit");
    submit.textContent = submitLabel || "Save";
    submit.className = "cosmos-btn " + (tone === "danger" ? "is-danger" : "is-primary");
    submit.disabled = false;
    // Info-style read-only modals hide the primary action entirely.
    submit.style.display = (opts && opts.hideSubmit) ? "none" : "";
    S.modalSubmit = (opts && opts.hideSubmit) ? null : onSubmit;
    var cancel = $("cosmos-modal-cancel");
    if (cancel) cancel.textContent = (opts && opts.hideSubmit) ? "Close" : "Cancel";
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

  function presetChipsHtml(kind) {
    // One selectable row of preset templates; the choice is recorded as
    // feedback so the intelligence learns what the owner wants.
    var presets = kind === "reject" ? REJECT_PRESETS : REFINE_PRESETS;
    return '<div class="cosmos-preset-chips" data-preset-kind="' + kind + '">' +
      presets.map(function (preset) {
        return '<button type="button" class="cosmos-preset' +
          (S.presetChoice === preset.id ? " is-active" : "") +
          '" data-cosmos-preset="' + attr(preset.id) + '">' + esc(preset.label) + "</button>";
      }).join("") + "</div>";
  }

  function openRefineModal(recommendationId) {
    var card = findRecommendation(recommendationId);
    S.presetChoice = null;
    var body = '<p class="cosmos-modal-note">Refine <b>' + esc(card ? card.title : "this recommendation") + "</b>. The original card is kept and linked. Your preset choice is fed back to the agent.</p>" +
      '<div id="cosmos-modal-error" class="cosmos-error hidden"></div>' +
      '<div class="cosmos-field"><span>What should change? (preset)</span>' + presetChipsHtml("refine") + "</div>" +
      '<label class="cosmos-field"><span>Refinement instruction (optional)</span><textarea id="cosmos-refine-note" rows="4" maxlength="2000" placeholder="What should change? Keep it inside the active configuration."></textarea></label>';
    openModal("Refine recommendation", body, "Run refinement", function () {
      var instruction = $("cosmos-refine-note").value.trim();
      if (!S.presetChoice && !instruction) {
        modalError("Pick a preset or write an instruction.");
        return;
      }
      return api("/api/cosmos/recommendations/" + encodeURIComponent(recommendationId) + "/refine", {
        method: "POST", body: { instruction: instruction, preset: S.presetChoice }
      }).then(function (data) {
        S.state.notice = {
          tone: "ok", title: "Refinement complete",
          body: data.recommendation ? data.recommendation.title : "The refined card is ready for review."
        };
        toast("Refined — the agent learned from this.");
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
    S.presetChoice = null;
    var body = '<p class="cosmos-modal-note">Rejection removes the card from the board and records why — the agent learns from the preset you pick.</p>' +
      '<div id="cosmos-modal-error" class="cosmos-error hidden"></div>' +
      '<div class="cosmos-field"><span>Reason (preset)</span>' + presetChipsHtml("reject") + "</div>" +
      '<label class="cosmos-field"><span>Note (optional)</span><input id="cosmos-reject-note" maxlength="500" placeholder="Why is this not a fit?"></label>';
    openModal("Reject recommendation", body, "Reject", function () {
      var note = $("cosmos-reject-note").value.trim();
      if (!S.presetChoice && !note) {
        modalError("Pick a preset or write a note.");
        return;
      }
      return api("/api/cosmos/recommendations/" + encodeURIComponent(recommendationId) + "/reject", {
        method: "POST", body: { note: note, preset: S.presetChoice }
      }).then(function () {
        S.state.notice = { tone: "warn", title: "Recommendation rejected", body: "It stays in history and was never drafted. The agent will avoid this pattern." };
        toast("Rejected — feedback recorded.");
        return reloadCurrentAgent();
      });
    }, "danger");
  }

  function openInfoModal(recommendationId) {
    var card = findRecommendation(recommendationId);
    if (!card) return;
    var division = card.division === "youtube" ? "youtube" : "ads";
    var payload = card.payload || {};
    var isAds = division === "ads";
    var name = isAds ? (payload.name || card.title) : card.title;
    var description = payload.description || card.summary || payload.reason || "";
    var body = '<div class="cosmos-info-head">' +
        badge(card.status) + evidenceBadge(card.evidence_status) +
        '<span class="cosmos-score">' + (isFinite(Number(card.relevance_score)) ? esc(card.relevance_score) : "—") + "/100</span>" +
        '<span class="cosmos-card-when">' + esc(C.formatWhen(card.created_iso)) + "</span></div>" +
      "<h4 class='cosmos-info-name'>" + esc(name) + "</h4>" +
      (description ? "<p class='cosmos-info-desc'>" + esc(description) + "</p>" : "") +
      "<div class='cosmos-info-section'><b>Why the agent selected this</b>" + (criteriaRows(payload) || '<p class="cosmos-muted">No candidate criteria recorded for this card.</p>') + "</div>" +
      "<div class='cosmos-info-section'><b>Configuration fit</b>" + (payloadRows(payload, division) || '<p class="cosmos-muted">No configuration rows recorded.</p>') + "</div>" +
      (isAds && card.affiliate_validation
        ? "<div class='cosmos-info-section'><b>Affiliate check</b><p class='cosmos-muted'>" +
          esc(card.affiliate_validation.status) + (card.affiliate_validation.program ? " · " + esc(card.affiliate_validation.program) : "") + "</p></div>"
        : "") +
      (!isAds && card.opportunity ? "<div class='cosmos-info-section'><b>Opportunity</b><p class='cosmos-muted'>" + esc(card.opportunity) + "</p></div>" : "") +
      (!isAds && card.competition ? "<div class='cosmos-info-section'><b>Competition</b><p class='cosmos-muted'>" + esc(card.competition) + "</p></div>" : "") +
      "<div class='cosmos-info-section'><b>Sources &amp; provenance</b>" + sourceList(card.sources) +
        "<p class='cosmos-muted'>Execution " + esc(card.execution_id || "—") + " · template " + esc(card.template_id || "—") + "</p></div>";
    var verifyUrl = cardVerifyUrl(card, payload);
    if (verifyUrl) {
      body = '<a class="cosmos-card-link" href="' + attr(verifyUrl) + '" target="_blank" rel="noopener noreferrer">' +
        '<i data-lucide="external-link" class="w-3 h-3"></i> Open and verify</a>' + body;
    }
    openModal(isAds ? "Product info" : "Topic info", body, "Close", null, null, { hideSubmit: true });
  }

  function openShopeeModal() {
    var platform = C.adsPlatform(S.state, "shopee") || {};
    var body = '<p class="cosmos-modal-note">Register the Shopee affiliate program this Ads division sources product candidates from. Discovery uses the handle, region and program link as sourcing context; every card still shows its listing link for you to verify.</p>' +
      '<div id="cosmos-modal-error" class="cosmos-error hidden"></div>' +
      '<div class="cosmos-template-form">' +
      '<label class="cosmos-field"><span>Affiliate handle / ID</span><input data-shopee-field="handle" value="' + attr(platform.handle || "") + '" placeholder="e.g. yourname-ff0aa1"></label>' +
      '<label class="cosmos-field"><span>Program URL</span><input data-shopee-field="program_url" value="' + attr(platform.program_url || "") + '" placeholder="https://affiliate.shopee.com.br/…"></label>' +
      '<label class="cosmos-field"><span>Region</span><input data-shopee-field="region" value="' + attr(platform.region || "PH") + '" placeholder="PH"></label>' +
      '<label class="cosmos-field"><span>Notes</span><textarea data-shopee-field="notes" rows="2" placeholder="Commission rates, exclusions, anything the agent should respect.">' + esc(platform.notes || "") + "</textarea></label>" +
      '<label class="cosmos-check"><input type="checkbox" data-shopee-field="connected"' + (platform.connected === false ? "" : " checked") + "> Registered — the Ads division may source from Shopee</label>" +
      "</div>";
    openModal("Shopee platform registration", body, "Save registration", function () {
      var host = $("cosmos-modal-body");
      var read = function (name) {
        var el = host.querySelector('[data-shopee-field="' + name + '"]');
        return el ? (el.type === "checkbox" ? el.checked : el.value.trim()) : "";
      };
      return api("/api/cosmos/config/ads/platform", {
        method: "POST",
        body: {
          platform: "shopee",
          connected: read("connected"),
          handle: read("handle"),
          program_url: read("program_url"),
          region: read("region"),
          notes: read("notes")
        }
      }).then(function () {
        toast("Shopee registration saved.");
        S.state.notice = { tone: "ok", title: "Platform registered", body: "The Ads division now sources with your Shopee affiliate context." };
        return refresh({ keepAgent: true });
      });
    });
  }

  function openChannelRegisterModal() {
    var body = '<p class="cosmos-modal-note">Register a YouTube channel profile for the Topic Discovery division. For full upload/publish connection use OAuth after registering.</p>' +
      '<div id="cosmos-modal-error" class="cosmos-error hidden"></div>' +
      '<div class="cosmos-template-form">' +
      '<label class="cosmos-field"><span>Channel name *</span><input data-channel-field="name" placeholder="e.g. My Tech Channel"></label>' +
      '<label class="cosmos-field"><span>Handle</span><input data-channel-field="handle" placeholder="@mytechchannel"></label>' +
      '<label class="cosmos-field"><span>Content niche</span><input data-channel-field="content_niche" placeholder="e.g. Small business finance"></label>' +
      '<label class="cosmos-field"><span>Target audience</span><input data-channel-field="target_audience" placeholder="Who the videos are for"></label>' +
      '<label class="cosmos-field"><span>Description</span><textarea data-channel-field="description" rows="2"></textarea></label>' +
      "</div>";
    openModal("Register YouTube channel", body, "Register channel", function () {
      var host = $("cosmos-modal-body");
      var read = function (name) {
        var el = host.querySelector('[data-channel-field="' + name + '"]');
        return el ? el.value.trim() : "";
      };
      if (!read("name")) {
        modalError("Channel name is required.");
        return;
      }
      return api("/api/cosmos/config/youtube/channel", {
        method: "POST",
        body: {
          name: read("name"),
          handle: read("handle"),
          content_niche: read("content_niche"),
          target_audience: read("target_audience"),
          description: read("description")
        }
      }).then(function () {
        toast("Channel registered and set active.");
        return refresh({ keepAgent: true });
      });
    });
  }

  function refreshYouTubeChannel() {
    return api("/api/integrations/youtube/channel?refresh=1").then(function (data) {
      C.mergeYoutubeChannel(S.state, data);
      render();
      if (data && data.status === "connected") {
        toast("Live channel loaded: " + data.name);
      } else {
        toast(C.youtubeChannelStatusLabel(S.state) + " — the title stays on YouTube Intelligence until a live read succeeds.", "warn");
      }
      return data || {};
    }).catch(function (err) {
      toast(err.message || String(err), "warn");
      throw err;
    });
  }

  function openExplicitYouTubeLink() {
    // Explicit second step only: nothing opens when the link is disclosed.
    var link = S.youtubeLink;
    if (!link || !/^https:\/\/connect\.composio\.dev\/link\//.test(link)) {
      toast("Refusing to open an unexpected connect link.", "warn");
      return;
    }
    window.open(link, "_blank", "noopener,noreferrer");
    toast("Connect link opened. Return here and refresh after completing it.");
  }

  function openYouTubeSessionModal() {
    return api("/api/integrations/youtube/session/link", { method: "POST", body: {} })
      .then(function (data) {
        var link = data && (data.redirect_url || (data.session && data.session.redirect_url));
        if (!link) {
          toast("The shared router returned no connect link.", "warn");
          return null;
        }
        S.youtubeLink = link;
        var body = [
          '<p class="cosmos-muted">ANDROMEDA uses the same shared YouTube connection as Settings. Open the link explicitly, finish the session binding, then refresh the channel.</p>',
          '<div class="cosmos-youtube-link">' + esc(link) + "</div>",
          '<button type="button" class="cosmos-btn is-ghost" data-cosmos-youtube-open>Open connect link</button>'
        ].join("\n");
        openModal("Connect YouTube", body, "Refresh channel", function () {
          return refreshYouTubeChannel().then(function (live) {
            if (!live || live.status !== "connected") {
              throw new Error("Still not connected — complete the link before refreshing.");
            }
            return live;
          });
        });
        return data;
      })
      .catch(function (err) {
        toast(err.message || String(err), "warn");
        throw err;
      });
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

  function setAutonomyMode(division, mode) {
    var agentId = S.state.activeAgent;
    var current = C.autonomyMode(S.state, agentId, division);
    if (!mode || mode === current) return;
    // Optimistic flip, then persist; on failure the refresh restores truth.
    C.setAutonomy(S.state, agentId, division, {
      mode: mode,
      updated_iso: new Date().toISOString()
    });
    render();
    api("/api/cosmos/config/autonomy", {
      method: "POST",
      body: { agent_id: agentId, division: division, mode: mode }
    }).then(function (data) {
      C.setAutonomy(S.state, agentId, division, data.selection || data);
      S.state.notice = {
        tone: "ok",
        title: mode === "autonomous" ? "Autonomous discovery on" : "Override on",
        body: mode === "autonomous"
          ? "This division now runs by itself every 6 hours and keeps five cards on the board."
          : "This division runs only when you supply a task and press Discover."
      };
      toast(mode === "autonomous" ? "Autonomous mode on." : "Override mode on.");
      return reloadCurrentAgent();
    }).catch(function (err) {
      toast(err.message || String(err), "warn");
      return refresh({ keepAgent: true });
    });
  }

  function draftRecommendation(recommendationId) {
    api("/api/cosmos/recommendations/" + encodeURIComponent(recommendationId) + "/draft", { method: "POST" })
      .then(function () {
        S.state.notice = {
          tone: "ok", title: "Shipped to GENESIS",
          body: "The card left this board and entered the GENESIS production queue. An autonomous division refills it immediately."
        };
        toast("Drafted — shipped to GENESIS. Refilling the board…");
        return reloadCurrentAgent();
      })
      .then(function () {
        // Follow the immediate top-up: poll briefly so the owner sees the
        // replacement card land without a manual refresh.
        var agentId = S.state.activeAgent;
        var division = null;
        ["ads", "youtube"].forEach(function (part) {
          if (C.divisionState(S.state, agentId, part).recommendations.some(function (item) {
            return item.id === recommendationId;
          })) division = part;
        });
        if (division && C.autonomyMode(S.state, agentId, division) === "autonomous") {
          startTopupPoll();
        }
      })
      .catch(function (err) {
        S.state.notice = { tone: "warn", title: "Draft blocked", body: err.message || String(err) };
        return reloadCurrentAgent();
      });
  }

  function startTopupPoll() {
    if (S.topupTimer) clearTimeout(S.topupTimer);
    S.topupTries = 0;
    var tick = function () {
      S.topupTries += 1;
      if (S.topupTries > 30) return; // ~5 minutes, then stop quietly
      reloadCurrentAgent().then(function () {
        var agentId = S.state.activeAgent;
        var full = ["ads", "youtube"].every(function (division) {
          return C.autonomyMode(S.state, agentId, division) !== "autonomous" ||
            C.boardCards(S.state, agentId, division).length >= BOARD_SIZE;
        });
        if (!full) S.topupTimer = setTimeout(tick, 10000);
      });
    };
    S.topupTimer = setTimeout(tick, 10000);
  }



  function validationToTopic(validationId) {
    api("/api/studio/youtube/research/" + encodeURIComponent(validationId) + "/to-topic", { method: "POST" })
      .then(function (data) {
        toast("Validation converted to a topic.");
        S.state.notice = { tone: "ok", title: "Topic created", body: (data.topic && data.topic.title) || "The validation became a backlog topic." };
        return reloadCurrentAgent();
      }).catch(function (err) {
        S.state.notice = { tone: "warn", title: "Conversion failed", body: err.message || String(err) };
        render();
      });
  }

  function deleteValidation(validationId) {
    api("/api/studio/youtube/research/" + encodeURIComponent(validationId), { method: "DELETE" })
      .then(function () {
        toast("Validation deleted.");
        return fetchResearch().then(render);
      }).catch(function (err) {
        S.state.notice = { tone: "warn", title: "Delete failed", body: err.message || String(err) };
        render();
      });
  }

  function openValidationModal() {
    var body = '<p class="cosmos-modal-note">Record an analyst validation. Score each criterion 1–5 with a basis (estimate or sourced); sourced criteria carry a label. Nothing invents platform data.</p>' +
      '<div id="cosmos-modal-error" class="cosmos-error hidden"></div>' +
      '<div class="cosmos-template-form">' +
      cosmosTextField("val_subject", "Subject", "", true) +
      cosmosTextField("val_pillar", "Pillar", "") +
      '<div class="cosmos-field-row">' +
      cosmosNumberField("val_rpm", "RPM potential (1-5)", 3) +
      cosmosNumberField("val_demand", "Proven demand (1-5)", 3) +
      "</div>" +
      '<div class="cosmos-field-row">' +
      cosmosNumberField("val_geo", "Geographic audience (1-5)", 3) +
      cosmosNumberField("val_sustain", "Sustainability (1-5)", 3) +
      cosmosNumberField("val_gap", "Content gap (1-5)", 3) +
      "</div>" +
      '<label class="cosmos-field"><span>Summary</span><textarea rows="2" data-product-field="val_summary"></textarea></label>' +
      "</div>";
    openModal("Record niche validation", body, "Save validation", function () {
      var host = $("cosmos-modal-body");
      var read = function (name) {
        var el = host.querySelector('[data-product-field="' + name + '"]');
        return el ? el.value.trim() : "";
      };
      var num = function (name) {
        var el = host.querySelector('[data-product-field="' + name + '"]');
        return Math.max(1, Math.min(5, Number(el && el.value) || 3));
      };
      var payload = {
        subject: read("val_subject"),
        pillar: read("val_pillar"),
        summary: read("val_summary"),
        criteria: [
          { id: "rpm_potential", score: num("val_rpm") },
          { id: "proven_demand", score: num("val_demand") },
          { id: "geographic_audience", score: num("val_geo") },
          { id: "content_sustainability", score: num("val_sustain") },
          { id: "content_gap", score: num("val_gap") }
        ]
      };
      return api("/api/studio/youtube/research", { method: "POST", body: payload }).then(function () {
        toast("Validation saved.");
        S.state.notice = { tone: "ok", title: "Validation saved", body: "Scores are analyst estimates — sources stay attached to the record." };
        return reloadCurrentAgent();
      });
    });
  }

  function openCompetitorModal() {
    var body = '<p class="cosmos-modal-note">Track a competitor channel. Observations you record are tagged per-entry as observed or assumed.</p>' +
      '<div id="cosmos-modal-error" class="cosmos-error hidden"></div>' +
      '<div class="cosmos-template-form">' +
      cosmosTextField("comp_name", "Name", "", true) +
      cosmosTextField("comp_url", "URL", "") +
      cosmosTextField("comp_positioning", "Positioning", "") +
      "</div>";
    openModal("Add competitor", body, "Add competitor", function () {
      var host = $("cosmos-modal-body");
      var read = function (name) {
        var el = host.querySelector('[data-product-field="' + name + '"]');
        return el ? el.value.trim() : "";
      };
      return api("/api/studio/youtube/competitors", {
        method: "POST",
        body: { name: read("comp_name"), url: read("comp_url"), positioning: read("comp_positioning") }
      }).then(function () {
        toast("Competitor added.");
        return fetchResearch().then(render);
      });
    });
  }

  function addObservation(competitorId, note) {
    if (!note) {
      toast("Write the observation first.", "warn");
      return;
    }
    api("/api/studio/youtube/competitors/" + encodeURIComponent(competitorId) + "/observations", {
      method: "POST", body: { note: note, basis: "observed" }
    }).then(function () {
      toast("Observation recorded.");
      return fetchResearch().then(render);
    }).catch(function (err) {
      S.state.notice = { tone: "warn", title: "Observation failed", body: err.message || String(err) };
      render();
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
      });

      host.addEventListener("input", function (event) {
        if (event.target.hasAttribute("data-cosmos-llm-search")) {
          // Re-filter the open model menu in place so the input keeps focus.
          var division = event.target.closest("[data-cosmos-llm-row]");
          if (!division) return;
          var div = division.getAttribute("data-cosmos-llm-row");
          var selection = llmSelectionFor(div);
          renderLLMModelOptions(div, selection.agent_id);
          return;
        }
      });

      host.addEventListener("click", function (event) {
        var button = event.target.closest("button");
        if (!button) return;
        var llmToggle = button.getAttribute("data-cosmos-llm-toggle");
        if (llmToggle) {
          var llmRow = button.closest("[data-cosmos-llm-row]");
          return toggleLLMMenu(llmRow ? llmRow.getAttribute("data-cosmos-llm-row") : null, llmToggle);
        }
        var llmAgent = button.getAttribute("data-cosmos-llm-agent");
        if (llmAgent) {
          var agentRow = button.closest("[data-cosmos-llm-row]");
          var agentDiv = agentRow ? agentRow.getAttribute("data-cosmos-llm-row") : null;
          var current = llmSelectionFor(agentDiv);
          if (!agentDiv || llmAgent === current.agent_id) { closeLLMMenus(); return; }
          return configureDivisionLLM(agentDiv, { agent_id: llmAgent, model: "" });
        }
        var llmModel = button.getAttribute("data-cosmos-llm-model");
        if (llmModel) {
          var modelRow = button.closest("[data-cosmos-llm-row]");
          var modelDiv = modelRow ? modelRow.getAttribute("data-cosmos-llm-row") : null;
          var modelSel = llmSelectionFor(modelDiv);
          if (!modelDiv || llmModel === modelSel.model) { closeLLMMenus(); return; }
          return configureDivisionLLM(modelDiv, { agent_id: modelSel.agent_id, model: llmModel });
        }
        var llmProvider = button.getAttribute("data-cosmos-llm-provider");
        if (llmProvider) {
          var providerRow = button.closest("[data-cosmos-llm-row]");
          var providerDiv = providerRow ? providerRow.getAttribute("data-cosmos-llm-row") : null;
          var key = providerDiv + ":" + llmProvider;
          S.llmExpanded[key] = !(S.llmExpanded[key] !== undefined ? S.llmExpanded[key] : true);
          var providerSel = llmSelectionFor(providerDiv);
          renderLLMModelOptions(providerDiv, providerSel.agent_id);
          return;
        }
        if (button.hasAttribute("data-cosmos-llm-refresh")) {
          var refreshRow = button.closest("[data-cosmos-llm-row]");
          var refreshDiv = refreshRow ? refreshRow.getAttribute("data-cosmos-llm-row") : null;
          var refreshSel = llmSelectionFor(refreshDiv);
          if (!refreshSel.agent_id) return;
          var options = llmRowEl(refreshDiv, '[data-cosmos-llm-options="model"]');
          if (options) options.innerHTML = '<div class="cosmos-llm-empty">Loading models…</div>';
          return loadLLMModels(refreshSel.agent_id, true);
        }
        var run = button.getAttribute("data-cosmos-run");
        if (run) return execute(run);
        var edit = button.getAttribute("data-cosmos-edit-template");
        if (edit) return openTemplateEditor(edit);
        // Autonomy mode toggle (Autonomous default / Override expands prompt).
        var modeButton = button.getAttribute("data-cosmos-mode");
        if (modeButton) return setAutonomyMode(button.getAttribute("data-cosmos-division"), modeButton);
        if (button.hasAttribute("data-cosmos-platform-shopee")) return openShopeeModal();
        if (button.hasAttribute("data-cosmos-channel-register")) return openChannelRegisterModal();
        if (button.hasAttribute("data-cosmos-youtube-link")) {
          var liveChannel = C.youtubeChannel(S.state);
          return liveChannel.status === "connected"
            ? refreshYouTubeChannel()
            : openYouTubeSessionModal();
        }
        var preset = button.getAttribute("data-cosmos-preset");
        if (preset) {
          S.presetChoice = S.presetChoice === preset ? null : preset;
          var chips = button.closest(".cosmos-preset-chips");
          if (chips) {
            Array.prototype.forEach.call(chips.querySelectorAll(".cosmos-preset"), function (chip) {
              chip.classList.toggle("is-active", chip.getAttribute("data-cosmos-preset") === S.presetChoice);
            });
          }
          return;
        }
        var validationTopic = button.getAttribute("data-cosmos-validation-topic");
        if (validationTopic) return validationToTopic(validationTopic);
        var validationDelete = button.getAttribute("data-cosmos-validation-delete");
        if (validationDelete) return deleteValidation(validationDelete);
        if (button.hasAttribute("data-cosmos-add-validation")) return openValidationModal();
        var observe = button.getAttribute("data-cosmos-competitor-observe");
        if (observe) {
          var input = host.querySelector('[data-cosmos-observation-input="' + observe + '"]');
          return addObservation(observe, input ? input.value.trim() : "");
        }
        if (button.hasAttribute("data-cosmos-add-competitor")) return openCompetitorModal();
        var action = button.getAttribute("data-cosmos-action");
        if (!action) return;
        var id = button.getAttribute("data-cosmos-id");
        if (action === "draft") return draftRecommendation(id);
        if (action === "info") return openInfoModal(id);
        if (action === "refine") return openRefineModal(id);
        if (action === "affiliate") return openAffiliateModal(id);
        if (action === "reject") return openRejectModal(id);
      });
    }

    var modal = $("cosmos-modal");
    if (modal) {
      modal.addEventListener("click", function (event) {
        if (event.target === modal) closeModal();
        var openLink = event.target && event.target.closest
          ? event.target.closest("[data-cosmos-youtube-open]") : null;
        if (openLink) openExplicitYouTubeLink();
      });
      var close = $("cosmos-modal-close");
      var cancel = $("cosmos-modal-cancel");
      var submit = $("cosmos-modal-submit");
      if (close) close.addEventListener("click", closeModal);
      if (cancel) cancel.addEventListener("click", closeModal);
      if (submit) submit.addEventListener("click", runModalSubmit);
    }
    document.addEventListener("keydown", function (event) {
      if (event.key === "Escape") {
        if (S.llmOpenMenu) closeLLMMenus();
        closeModal();
      }
    });

    // Click outside an open division dropdown closes it.
    document.addEventListener("click", function (event) {
      if (!S.llmOpenMenu) return;
      if (event.target.closest(".cosmos-llm-field")) return;
      closeLLMMenus();
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
    state: function () { return S.state; },
    // Shared modal + toast surface (used by the GENESIS module too).
    toast: toast,
    openModal: openModal,
    closeModal: closeModal
  };

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
