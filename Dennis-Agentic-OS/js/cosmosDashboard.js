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
    backlogAdsQuery: "",
    backlogAdsStatus: "",
    backlogYoutubeQuery: "",
    backlogYoutubeStatus: "",
    research: null,
    competitors: null,
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
  // Backlog management state (the authoritative Ads + YouTube interfaces)
  // -----------------------------------------------------------------------
  function fetchBacklogAds() {
    var params = new URLSearchParams();
    params.set("limit", "60");
    if (S.backlogAdsStatus) params.set("status", S.backlogAdsStatus);
    if (S.backlogAdsQuery) params.set("q", S.backlogAdsQuery);
    return api("/api/cosmos/backlog/ads?" + params.toString()).then(function (data) {
      S.backlogAds = data.products || [];
    });
  }

  function fetchBacklogYoutube() {
    var params = new URLSearchParams();
    if (S.backlogYoutubeStatus) params.set("status", S.backlogYoutubeStatus);
    if (S.backlogYoutubeQuery) params.set("q", S.backlogYoutubeQuery);
    if (S.state.config.activeYoutubeChannelId) {
      params.set("channel_id", S.state.config.activeYoutubeChannelId);
    }
    var suffix = params.toString() ? "?" + params.toString() : "";
    return api("/api/cosmos/backlog/youtube" + suffix).then(function (data) {
      S.backlogYoutube = {
        channel_id: data.channel_id,
        channel_name: data.channel_name,
        counts: data.counts || {},
        total: data.total || 0,
        topics: data.topics || [],
        statuses: data.statuses || []
      };
    });
  }

  function fetchResearch() {
    return Promise.all([
      api("/api/studio/youtube/research").catch(function () { return { validations: [] }; }),
      api("/api/studio/youtube/competitors").catch(function () { return { competitors: [] }; })
    ]).then(function (results) {
      S.research = results[0] || { validations: [] };
      S.competitors = results[1] || { competitors: [] };
    });
  }

  function loadBacklogs() {
    var jobs = [fetchBacklogAds(), fetchBacklogYoutube()];
    if (S.state.activeAgent === "quasar") jobs.push(fetchResearch());
    return Promise.all(jobs).catch(function () { /* blocks render with empty state */ });
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
    // The single authoritative Ads Product Backlog management interface.
    // Ads Studio links here instead of hosting its own duplicate screen.
    var counts = (S.state.counts.ads_backlog || {});
    var rows = S.backlogAds || [];
    var statusOptions = ["", "discovered", "drafted", "reviewing", "approved",
      "ready_for_creation", "in_production", "published", "archived"];
    var list = rows.length ? rows.map(function (product) {
      var currentIndex = ADS_STATUS_ORDER.indexOf(product.status);
      var allowed = ADS_STATUS_ORDER.slice(Math.max(0, currentIndex)).concat(["archived"]).filter(function (status, index, list) {
        return list.indexOf(status) === index;
      });
      var provenance = product.provenance || {};
      return [
        '<div class="cosmos-backlog-row">',
        '  <div class="cosmos-backlog-main"><b>' + esc(product.name) + "</b>",
        '    <span class="cosmos-muted">' + esc(product.merchant || "No merchant") + " · " + esc(formatPrice(product.price, "PHP")) +
        (product.category ? " · " + esc(product.category) : "") + "</span>",
        product.provenance && product.provenance.agent_id
          ? '<span class="cosmos-provenance">from ' + esc(product.provenance.agent_id.toUpperCase()) +
            (provenance.recommendation_id ? " · rec " + esc(provenance.recommendation_id) : "") + "</span>"
          : "",
        "  </div>",
        '  <div class="cosmos-backlog-actions">',
        '  <select data-cosmos-product="' + attr(product.id) + '" aria-label="Product status">' +
        allowed.map(function (status) {
          return '<option value="' + attr(status) + '"' + (status === product.status ? " selected" : "") + ">" + esc(STATUS_LABELS[status] || status) + "</option>";
        }).join("") + "</select>",
        '  <button type="button" class="cosmos-btn is-ghost" data-cosmos-edit-product="' + attr(product.id) + '">Edit</button>',
        product.archived
          ? ""
          : '<button type="button" class="cosmos-btn is-danger is-ghost" data-cosmos-delete-product="' + attr(product.id) + '" data-cosmos-delete-name="' + attr(product.name) + '">Delete</button>',
        "  </div>",
        "</div>"
      ].join("\n");
    }).join("") : "";
    return [
      '<section class="cosmos-backlog">',
      '  <div class="cosmos-section-head"><div><h3>Ads Product Backlog</h3>',
      '    <p>The authoritative product pipeline. Ads Studio uses this same data; publishing stays in GENESIS.</p></div>',
      '    <span class="cosmos-count">' + esc(counts.total || 0) + " active</span></div>",
      '  <div class="cosmos-backlog-toolbar">',
      '    <input type="search" placeholder="Search products…" value="' + attr(S.backlogAdsQuery) + '" data-cosmos-ads-search aria-label="Search products">',
      '    <select data-cosmos-ads-status aria-label="Filter by status">' +
      statusOptions.map(function (status) {
        return '<option value="' + attr(status) + '"' + (S.backlogAdsStatus === status ? " selected" : "") + ">" +
          (status ? esc(STATUS_LABELS[status] || status) : "All statuses") + "</option>";
      }).join("") + "</select>",
      '    <button type="button" class="cosmos-btn" data-cosmos-add-product>Add product</button>',
      "  </div>",
      rows.length ? '<div class="cosmos-backlog-list">' + list + "</div>" : emptyBlock("No products match", "Adjust the search or draft an Ads recommendation into the backlog."),
      "</section>"
    ].join("\n");
  }

  function openProductModal(product) {
    var isNew = !product;
    var data = product || {};
    var body = '<p class="cosmos-modal-note">' + (isNew
      ? "Add a product manually. It enters the pipeline as <b>drafted</b>."
      : "Editing <b>" + esc(data.name) + "</b>. Lifecycle history is preserved.") + "</p>" +
      '<div id="cosmos-modal-error" class="cosmos-error hidden"></div>' +
      '<div class="cosmos-template-form">' +
      cosmosTextField("name", "Name", data.name, true) +
      cosmosTextField("merchant", "Merchant", data.merchant) +
      cosmosTextField("category", "Category", data.category) +
      cosmosTextField("product_url", "Product URL", data.product_url) +
      cosmosTextField("image_url", "Image URL", data.image_url) +
      '<div class="cosmos-field-row">' +
      cosmosNumberField("price_amount", "Price", data.price && data.price.amount) +
      cosmosTextField("price_currency", "Currency", (data.price && data.price.currency) || "PHP") +
      "</div>" +
      cosmosTextField("target_audience", "Target audience", data.target_audience) +
      cosmosTextField("reason", "Reason / note", data.reason) +
      "</div>";
    openModal(isNew ? "Add product" : "Edit product", body, isNew ? "Add product" : "Save product", function () {
      var host = $("cosmos-modal-body");
      var read = function (name) {
        var el = host.querySelector('[data-product-field="' + name + '"]');
        return el ? el.value.trim() : "";
      };
      var payload = {
        name: read("name"),
        merchant: read("merchant"),
        category: read("category"),
        product_url: read("product_url"),
        image_url: read("image_url"),
        target_audience: read("target_audience"),
        reason: read("reason"),
        price: {
          amount: read("price_amount") === "" ? null : Number(read("price_amount")),
          currency: read("price_currency") || "PHP"
        }
      };
      if (isNew) {
        return api("/api/cosmos/backlog/ads", { method: "POST", body: payload }).then(function () {
          toast("Product added.");
          S.state.notice = { tone: "ok", title: "Product added", body: "It is now in the backlog as drafted." };
          return reloadCurrentAgent();
        });
      }
      return api("/api/cosmos/backlog/ads/" + encodeURIComponent(data.id), { method: "PATCH", body: payload }).then(function () {
        toast("Product saved.");
        return reloadCurrentAgent();
      });
    });
  }

  function cosmosTextField(name, label, value, required) {
    return '<label class="cosmos-field"><span>' + esc(label) + (required ? " *" : "") + '</span><input data-product-field="' + attr(name) + '" value="' + attr(value || "") + '"></label>';
  }

  function cosmosNumberField(name, label, value) {
    return '<label class="cosmos-field"><span>' + esc(label) + '</span><input type="number" min="0" step="0.01" data-product-field="' + attr(name) + '" value="' + attr(value == null ? "" : value) + '"></label>';
  }

  function deleteProduct(productId, name) {
    var body = '<p class="cosmos-modal-note">Soft-delete <b>' + esc(name || productId) + "</b>? The record is kept for provenance but removed from the active backlog. This cannot be undone from the UI.</p>" +
      '<div id="cosmos-modal-error" class="cosmos-error hidden"></div>';
    openModal("Delete product", body, "Delete", function () {
      return api("/api/cosmos/backlog/ads/" + encodeURIComponent(productId), { method: "DELETE" }).then(function () {
        toast("Product deleted.");
        S.state.notice = { tone: "warn", title: "Product deleted", body: "The record was soft-deleted; history is preserved." };
        return reloadCurrentAgent();
      });
    }, "danger");
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

  function youtubeBacklogBlock() {
    // The single authoritative YouTube Topic Backlog management interface,
    // backed by the COSMOS backlog API over the existing youtube_topics store
    // and its transition rules. Studio no longer exposes this screen.
    var data = S.backlogYoutube || {};
    var counts = data.counts || (S.state.counts.youtube_backlog || {});
    var topics = data.topics || [];
    var summary = Object.keys(counts).filter(function (key) {
      return key !== "total" && counts[key];
    }).map(function (key) {
      return esc((STATUS_LABELS[key] || key) + ": " + counts[key]);
    }).join(" · ");
    var list = topics.length ? topics.map(function (topic) {
      var script = topic.script || null;
      var studioProject = topic.studio_project || null;
      var links = [];
      if (script && !script.missing) {
        links.push("script: " + (script.title || script.id) + (script.status ? " (" + script.status + ")" : ""));
      } else if (topic.script_id) {
        links.push("script missing: " + topic.script_id);
      }
      if (studioProject) {
        links.push("Studio project: " + (studioProject.title || studioProject.id) +
          (studioProject.status ? " (" + studioProject.status + ")" : ""));
      } else if (topic.project_id) {
        links.push("Studio project missing: " + topic.project_id);
      }
      return [
        '<div class="cosmos-backlog-row">',
        '  <div class="cosmos-backlog-main"><b>' + esc(topic.title) + "</b>",
        topic.has_active_production
          ? ' <span class="cosmos-badge is-info">In GENESIS production</span>'
          : "",
        '    <span class="cosmos-muted">' + esc(topic.pillar || "—") + " · " + esc(topic.priority || "P2") +
        (topic.validation_id ? " · validated" : "") + " · " + esc(C.formatWhen(topic.created_iso)) + "</span>",
        links.length ? '    <span class="cosmos-muted">' + esc(links.join(" · ")) + "</span>" : "",
        topic.notes ? '<details class="cosmos-card-more"><summary>Provenance &amp; notes</summary><p>' + esc(topic.notes) + "</p></details>" : "",
        "  </div>",
        '  <div class="cosmos-backlog-actions">',
        '  <select data-cosmos-topic="' + attr(topic.id) + '" aria-label="Topic status">' +
        YOUTUBE_STATUS_ORDER.concat([]).map(function (status) {
          return '<option value="' + attr(status) + '"' + (status === topic.status ? " selected" : "") + ">" + esc(STATUS_LABELS[status] || status) + "</option>";
        }).join("") + "</select>",
        '  <button type="button" class="cosmos-btn is-ghost" data-cosmos-topic-detail="' + attr(topic.id) + '">Details</button>',
        "  </div>",
        "</div>"
      ].join("\n");
    }).join("") : "";
    return [
      '<section class="cosmos-backlog">',
      '  <div class="cosmos-section-head"><div><h3>YouTube Topic Backlog</h3>',
      '    <p>The authoritative topic pipeline. GENESIS owns production from scripted topics onward; status changes here follow the same forward-only rules.</p></div>',
      '    <span class="cosmos-count">' + esc(data.total || counts.total || 0) + " topics</span></div>",
      data.channel_name ? '<p class="cosmos-muted">Channel: ' + esc(data.channel_name) + "</p>" : "",
      summary ? '<p class="cosmos-muted">' + summary + "</p>" : "",
      '  <div class="cosmos-backlog-toolbar">',
      '    <input type="search" placeholder="Search topics…" value="' + attr(S.backlogYoutubeQuery) + '" data-cosmos-yt-search aria-label="Search topics">',
      '    <select data-cosmos-yt-status aria-label="Filter by status">' +
      [""].concat(YOUTUBE_STATUS_ORDER).map(function (status) {
        return '<option value="' + attr(status) + '"' + (S.backlogYoutubeStatus === status ? " selected" : "") + ">" +
          (status ? esc(STATUS_LABELS[status] || status) : "All statuses") + "</option>";
      }).join("") + "</select>",
      "  </div>",
      topics.length ? '<div class="cosmos-backlog-list">' + list + "</div>" : emptyBlock("No topics match", "Draft a YouTube recommendation into the backlog, or add a topic manually."),
      '  <button type="button" class="cosmos-btn" data-cosmos-add-topic>Add topic</button>',
      "</section>"
    ].join("\n");
  }

  function openTopicDetail(topicId) {
    var topic = (S.backlogYoutube && (S.backlogYoutube.topics || [])).find(function (item) { return item.id === topicId; });
    if (!topic) return;
    var channel = S.state.config.activeYoutubeChannelId;
    var query = channel ? "?channel_id=" + encodeURIComponent(channel) : "";
    openModal("Topic — " + (topic.title || topicId),
      '<div class="cosmos-kv-grid">' +
      '<div class="cosmos-kv"><span>Loading</span><b>Reading linked records…</b></div>' +
      "</div>", "Close", null);
    api("/api/cosmos/backlog/youtube/" + encodeURIComponent(topicId) + query).then(function (data) {
      var full = data.topic || topic;
      var script = data.script;
      var studioProject = data.studio_project;
      var projects = data.genesis_projects || [];
      var rows = [];
      function push(label, value) {
        if (value !== null && value !== undefined && value !== "") {
          rows.push('<div class="cosmos-kv"><span>' + esc(label) + "</span><b>" + esc(value) + "</b></div>");
        }
      }
      push("Status", full.status);
      push("Priority", full.priority);
      push("Pillar", full.pillar);
      push("Angle", full.angle);
      push("Audience question", full.audience_question);
      push("Search intent", full.search_intent);
      push("Commercial relevance", full.commercial_relevance);
      push("Score", full.score);
      push("Validation", full.validation_id);
      push("Published video", full.published_video_id);
      push("Channel", full.channel_id);
      push("Rejected reason", full.rejected_reason);
      var scriptBlock = "";
      if (script) {
        scriptBlock = '<p class="cosmos-modal-note"><b>Script:</b> ' + esc(script.title || script.id) +
          (script.status ? " · " + esc(script.status) : "") +
          (script.sections ? " · " + esc(script.sections) + " section(s)" : "") +
          (script.word_count ? " · " + esc(script.word_count) + " words" : "") + "</p>";
      } else if (full.script_id) {
        scriptBlock = '<p class="cosmos-modal-note"><b>Script:</b> ' + esc(full.script_id) + " (record missing)</p>";
      }
      var studioBlock = "";
      if (studioProject) {
        studioBlock = '<p class="cosmos-modal-note"><b>Studio project:</b> ' + esc(studioProject.title || studioProject.id) +
          (studioProject.status ? " · " + esc(studioProject.status) : "") +
          (studioProject.stage ? " · " + esc(studioProject.stage) : "") + "</p>";
      } else if (full.project_id) {
        studioBlock = '<p class="cosmos-modal-note"><b>Studio project:</b> ' + esc(full.project_id) + " (record missing)</p>";
      }
      var productionBlock = "";
      if (projects.length) {
        productionBlock = '<div class="cosmos-modal-note"><b>GENESIS production</b><ul class="cosmos-sources">' +
          projects.map(function (project) {
            return "<li>" + esc(project.title || project.id) + " — " + esc(project.status || "unknown") +
              (project.stage ? " · " + esc(project.stage) : "") + "</li>";
          }).join("") + "</ul>" +
          '<p><a class="cosmos-link" href="#cosmos/genesis">Open GENESIS production</a></p></div>';
      } else {
        productionBlock = '<p class="cosmos-modal-note"><b>GENESIS production:</b> not started' +
          (data.queue_eligible ? " — this topic is eligible for the production queue." : ".") + "</p>";
      }
      var publications = data.publications || [];
      var publicationBlock = publications.length
        ? '<div class="cosmos-modal-note"><b>Publications</b><ul class="cosmos-sources">' +
          publications.map(function (pub) {
            return "<li>" + esc(pub.status || "unknown") +
              (pub.remote_url ? ' — <a class="cosmos-link" href="' + attr(pub.remote_url) + '" target="_blank" rel="noopener">view</a>' : "") +
              (pub.scheduled_for ? " · scheduled " + esc(C.formatWhen(pub.scheduled_for)) : "") + "</li>";
          }).join("") + "</ul></div>"
        : "";
      var duplicates = data.duplicates || [];
      var duplicateBlock = duplicates.length
        ? '<p class="cosmos-modal-note"><b>Possible duplicates:</b> ' + duplicates.map(function (item) {
            return esc(item.title || item.id);
          }).join(" · ") + "</p>"
        : "";
      var body = '<div class="cosmos-kv-grid">' + rows.join("") + "</div>" +
        scriptBlock + studioBlock + productionBlock + publicationBlock + duplicateBlock +
        (full.notes ? '<p class="cosmos-modal-note"><b>Provenance:</b> ' + esc(full.notes) + "</p>" : "") +
        (full.competitor_coverage && full.competitor_coverage.length
          ? '<p class="cosmos-modal-note"><b>Competitor coverage:</b> ' + full.competitor_coverage.map(function (c) {
              return esc(c.competitor + (c.basis ? " (" + c.basis + ")" : ""));
            }).join(" · ") + "</p>"
          : "");
      openModal("Topic — " + (full.title || topicId), body, "Close", null);
    }).catch(function (err) {
      openModal("Topic — " + (topic.title || topicId),
        '<div class="cosmos-error">' + esc(err.message || String(err)) + "</div>", "Close", null);
    });
  }

  function openAddTopicModal() {
    var body = '<p class="cosmos-modal-note">Add a topic manually. It enters the pipeline as an <b>idea</b>.</p>' +
      '<div id="cosmos-modal-error" class="cosmos-error hidden"></div>' +
      '<div class="cosmos-template-form">' +
      cosmosTextField("topic_title", "Title", "", true) +
      cosmosTextField("topic_angle", "Angle", "") +
      cosmosTextField("topic_pillar", "Pillar", "") +
      '<label class="cosmos-field"><span>Priority</span><select data-product-field="topic_priority"><option>P1</option><option selected>P2</option><option>P3</option></select></label>' +
      "</div>";
    openModal("Add topic", body, "Add topic", function () {
      var host = $("cosmos-modal-body");
      var read = function (name) {
        var el = host.querySelector('[data-product-field="' + name + '"]');
        return el ? el.value.trim() : "";
      };
      var payload = {
        title: read("topic_title"),
        angle: read("topic_angle"),
        pillar: read("topic_pillar"),
        priority: read("topic_priority") || "P2"
      };
      var channel = S.state.config.activeYoutubeChannelId;
      if (channel) payload.channel_id = channel;
      return api("/api/cosmos/backlog/youtube", { method: "POST", body: payload }).then(function () {
        toast("Topic added.");
        S.state.notice = { tone: "ok", title: "Topic added", body: "It entered the backlog as an idea." };
        return reloadCurrentAgent();
      });
    });
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
      (division === "youtube" && agent.id === "quasar") ? quasarResearchBlock() : "",
      '<section class="cosmos-history">',
      '  <div class="cosmos-section-head"><div><h3>Execution history</h3><p>This agent\'s own runs in this division only.</p></div>',
      '    <span class="cosmos-count">' + executions.length + "</span></div>",
      executions.length ? executions.map(executionRow).join("\n")
        : emptyBlock("No runs yet", "Every run stores its model, configuration snapshot and honest result."),
      "</section>",
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

  function agentHeader(agent) {
    var counts = (C.agentState(S.state, agent.id).recommendationCounts) || {};
    var name = agent.name || C.normalizeAgent(agent.id).toUpperCase();
    var stat = function (label, value) {
      return "<span><b>" + esc(value || 0) + "</b>" + esc(label) + "</span>";
    };
    return [
      '<header class="cosmos-agent-head">',
      '  <div class="cosmos-agent-identity">',
      '    ' + emblemMark(agent.id, name.charAt(0), "is-" + (agent.accent || "cyan")),
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

  function changeTopicStatus(topicId, status) {
    var channel = S.state.config.activeYoutubeChannelId;
    var query = channel ? "?channel_id=" + encodeURIComponent(channel) : "";
    api("/api/cosmos/backlog/youtube/" + encodeURIComponent(topicId) + "/status" + query, {
      method: "POST", body: { status: status }
    }).then(function () {
      toast("Topic status updated.");
      return reloadCurrentAgent();
    }).catch(function (err) {
      S.state.notice = { tone: "warn", title: "Status not changed", body: err.message || String(err) };
      return reloadCurrentAgent();
    });
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

  var backlogDebounceTimers = {};
  function backlogDebounce(key, fn) {
    if (backlogDebounceTimers[key]) clearTimeout(backlogDebounceTimers[key]);
    backlogDebounceTimers[key] = setTimeout(fn, 320);
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
        var product = event.target.getAttribute("data-cosmos-product");
        if (product) return changeProductStatus(product, event.target.value);
        var topic = event.target.getAttribute("data-cosmos-topic");
        if (topic) return changeTopicStatus(topic, event.target.value);
        if (event.target.hasAttribute("data-cosmos-ads-status")) {
          S.backlogAdsStatus = event.target.value;
          return fetchBacklogAds().then(render).catch(function () { render(); });
        }
        if (event.target.hasAttribute("data-cosmos-yt-status")) {
          S.backlogYoutubeStatus = event.target.value;
          return fetchBacklogYoutube().then(render).catch(function () { render(); });
        }
      });

      host.addEventListener("input", function (event) {
        if (event.target.hasAttribute("data-cosmos-ads-search")) {
          S.backlogAdsQuery = event.target.value;
          backlogDebounce("ads", function () {
            fetchBacklogAds().then(render).catch(function () { /* keep view */ });
          });
        }
        if (event.target.hasAttribute("data-cosmos-yt-search")) {
          S.backlogYoutubeQuery = event.target.value;
          backlogDebounce("yt", function () {
            fetchBacklogYoutube().then(render).catch(function () { /* keep view */ });
          });
        }
      });

      host.addEventListener("click", function (event) {
        var button = event.target.closest("button");
        if (!button) return;
        var run = button.getAttribute("data-cosmos-run");
        if (run) return execute(run);
        var edit = button.getAttribute("data-cosmos-edit-template");
        if (edit) return openTemplateEditor(edit);
        if (button.hasAttribute("data-cosmos-add-product")) return openProductModal(null);
        var editProduct = button.getAttribute("data-cosmos-edit-product");
        if (editProduct) {
          var found = (S.backlogAds || []).find(function (item) { return item.id === editProduct; });
          return openProductModal(found);
        }
        var deleteProduct = button.getAttribute("data-cosmos-delete-product");
        if (deleteProduct) return deleteProduct(deleteProduct, button.getAttribute("data-cosmos-delete-name"));
        if (button.hasAttribute("data-cosmos-add-topic")) return openAddTopicModal();
        var topicDetail = button.getAttribute("data-cosmos-topic-detail");
        if (topicDetail) return openTopicDetail(topicDetail);
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
    state: function () { return S.state; },
    // Shared modal + toast surface (used by the GENESIS module too).
    toast: toast,
    openModal: openModal,
    closeModal: closeModal
  };

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
