/* =====================================================================
   FREELLMAPI — free LLM API catalog + per-agent connection dashboard.

   The catalog comes from backend/freellm_catalog.py (a parsed snapshot of
   open-free-llm-api/awesome-freellm-apis). Saving a connection reuses the
   existing owner-gated /api/freellmapi settings API, so the OS keeps the
   same per-agent endpoint model it already uses for Claude Code / Codex.
   ===================================================================== */
(function () {
  "use strict";

  var state = {
    initialized: false,
    loading: false,
    catalog: null,
    agents: {},
    query: "",
    tier: "all",
    card: "all",
    modality: "all",
    selected: null,
    agent: "claude-code",
    toastTimer: null
  };

  function $(id) { return document.getElementById(id); }

  function esc(value) {
    return String(value == null ? "" : value).replace(/[&<>"']/g, function (ch) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch];
    });
  }

  function icons() {
    if (window.lucide && window.lucide.createIcons) {
      try { window.lucide.createIcons(); } catch (e) { /* no-op */ }
    }
  }

  function fmt(number) {
    return Number(number || 0).toLocaleString();
  }

  function api(path, opts) {
    opts = opts || {};
    var init = { method: opts.method || "GET", cache: "no-store" };
    if (opts.body !== undefined) {
      init.headers = { "Content-Type": "application/json" };
      init.body = JSON.stringify(opts.body);
    }
    return fetch(path, init).then(function (response) {
      return response.json().catch(function () { return {}; }).then(function (data) {
        return { ok: response.ok, status: response.status, data: data };
      });
    });
  }

  function toast(text, warning) {
    var el = $("fl-toast");
    if (!el) return;
    el.textContent = text || "";
    el.classList.toggle("fl-toast-warn", !!warning);
    el.classList.add("fl-toast-visible");
    clearTimeout(state.toastTimer);
    state.toastTimer = setTimeout(function () {
      el.classList.remove("fl-toast-visible");
    }, 3200);
  }

  function copyText(value, label) {
    var done = function () { toast((label || "Value") + " copied"); };
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(value || "").then(done).catch(function () { fallbackCopy(value, done); });
    } else {
      fallbackCopy(value, done);
    }
  }

  function fallbackCopy(value, done) {
    var area = document.createElement("textarea");
    area.value = value || "";
    area.setAttribute("readonly", "readonly");
    area.style.position = "fixed";
    area.style.opacity = "0";
    document.body.appendChild(area);
    area.select();
    try { document.execCommand("copy"); done(); } catch (e) { toast("Could not copy", true); }
    document.body.removeChild(area);
  }

  function initials(name) {
    var parts = String(name || "").replace(/[^A-Za-z0-9 ]+/g, " ").trim().split(/\s+/);
    var out = (parts[0] || "?").charAt(0);
    if (parts.length > 1) out += parts[1].charAt(0);
    return out.toUpperCase();
  }

  function cardKey(value) {
    var raw = String(value || "").toLowerCase();
    if (raw === "no" || raw === "") return "no";
    if (raw.indexOf("phone") >= 0) return "phone";
    if (raw.indexOf("registration") >= 0) return "registration";
    return "other";
  }

  function cardLabel(value) {
    var key = cardKey(value);
    return { no: "No card", phone: "Phone verification", registration: "Registration", other: value || "See provider" }[key];
  }

  /* ------------------------------------------------------------------ */
  /* Catalog lookups + filtering                                         */
  /* ------------------------------------------------------------------ */
  function providers() {
    return (state.catalog && state.catalog.providers) || [];
  }

  function providerById(id) {
    for (var i = 0; i < providers().length; i += 1) {
      if (providers()[i].id === id) return providers()[i];
    }
    return null;
  }

  function providerByName(name) {
    var target = String(name || "").toLowerCase();
    for (var i = 0; i < providers().length; i += 1) {
      if (String(providers()[i].name || "").toLowerCase() === target) return providers()[i];
    }
    return null;
  }

  function matches(provider) {
    if (state.tier !== "all" && provider.tier !== state.tier) return false;
    if (state.card !== "all" && cardKey(provider.credit_card) !== state.card) return false;
    if (state.modality !== "all" && (provider.modalities || []).indexOf(state.modality) < 0) return false;
    var query = state.query.trim().toLowerCase();
    if (!query) return true;
    if (String(provider.name || "").toLowerCase().indexOf(query) >= 0) return true;
    if ((provider.modalities || []).join(" ").toLowerCase().indexOf(query) >= 0) return true;
    for (var i = 0; i < (provider.models || []).length; i += 1) {
      var model = provider.models[i];
      if ((model.name + " " + model.id).toLowerCase().indexOf(query) >= 0) return true;
    }
    return false;
  }

  function filteredProviders() {
    return providers().filter(matches);
  }

  /* ------------------------------------------------------------------ */
  /* Header + summary                                                    */
  /* ------------------------------------------------------------------ */
  function renderHeader() {
    var catalog = state.catalog || {};
    var counts = catalog.counts || {};
    var ready = !!catalog.ok;
    var pill = $("fl-status-pill");
    var text = $("fl-status-text");
    if (pill) {
      pill.classList.toggle("sb-pill-ok", ready);
      pill.classList.toggle("sb-pill-warn", !ready);
    }
    if (text) {
      text.textContent = ready
        ? fmt(counts.providers) + " providers · " + fmt(counts.models) + " free models"
        : "Catalog unavailable";
    }
    var meta = $("fl-source-meta");
    if (meta) {
      var bits = [];
      if (catalog.updated) bits.push("data " + catalog.updated);
      var snap = catalog.snapshot || {};
      if (snap.commit) bits.push("snapshot " + String(snap.commit).slice(0, 8));
      else if (catalog.repo_kind === "checkout") bits.push("local checkout");
      meta.textContent = bits.length ? " · " + bits.join(" · ") : "";
    }
    var stats = $("fl-stats");
    if (stats) {
      var chips = [];
      if (counts.listed_models) chips.push('<span class="mg-stat-chip">' + fmt(counts.listed_models) + " featured models</span>");
      if (counts.no_card_providers) chips.push('<span class="mg-stat-chip">' + fmt(counts.no_card_providers) + " no-card providers</span>");
      if (counts.modalities) chips.push('<span class="mg-stat-chip">' + fmt(counts.modalities) + " modalities</span>");
      if (catalog.repo_kind) chips.push('<span class="mg-stat-chip">' + esc(catalog.repo_kind) + "</span>");
      stats.innerHTML = chips.join("");
    }
    var badge = $("fl-total-badge");
    if (badge) badge.textContent = ready ? fmt(counts.models) + " models" : "Catalog";
  }

  function renderSummary() {
    var catalog = state.catalog || {};
    var counts = catalog.counts || {};
    var configured = 0;
    Object.keys(state.agents || {}).forEach(function (id) {
      if (state.agents[id] && state.agents[id].configured) configured += 1;
    });
    setText("fl-count-providers", fmt(counts.providers));
    setText("fl-count-models", fmt(counts.models));
    setText("fl-count-nocard", fmt(counts.no_card_providers));
    setText("fl-count-agents", configured + " / " + Object.keys(state.agents || {}).length);
  }

  function setText(id, value) {
    var el = $(id);
    if (el) el.textContent = value;
  }

  /* ------------------------------------------------------------------ */
  /* Provider list                                                       */
  /* ------------------------------------------------------------------ */
  function renderFilters() {
    var select = $("fl-modality");
    if (!select) return;
    var current = state.modality;
    var options = ['<option value="all">All modalities</option>'];
    ((state.catalog && state.catalog.modalities) || []).forEach(function (value) {
      options.push('<option value="' + esc(value) + '">' + esc(value) + "</option>");
    });
    select.innerHTML = options.join("");
    select.value = current;
    if (select.value !== current) select.value = "all";
    Array.prototype.forEach.call(document.querySelectorAll("[data-fl-tier]"), function (button) {
      button.classList.toggle("fl-seg-btn-active", button.getAttribute("data-fl-tier") === state.tier);
    });
  }

  function providerCard(provider) {
    var active = state.selected === provider.id;
    var chips = (provider.modalities || []).slice(0, 4).map(function (value) {
      return '<span class="fl-chip">' + esc(value) + "</span>";
    }).join("");
    if ((provider.modalities || []).length > 4) {
      chips += '<span class="fl-chip fl-chip-more">+' + (provider.modalities.length - 4) + "</span>";
    }
    var meta = [];
    if (provider.free_models) meta.push(fmt(provider.free_models) + " models");
    if (provider.max_context) meta.push(provider.max_context + " ctx");
    meta.push(cardLabel(provider.credit_card));
    return '<button type="button" class="fl-provider' + (active ? " fl-provider-active" : "") +
      '" data-fl-provider="' + esc(provider.id) + '" title="' + esc(provider.name) + '">' +
      '<div class="fl-provider-top">' +
      '<span class="fl-provider-avatar">' + esc(initials(provider.name)) + "</span>" +
      '<div style="min-width:0;flex:1">' +
      '<div class="fl-provider-name">' + esc(provider.name) + "</div>" +
      '<div class="fl-provider-meta">' + esc(meta.join(" · ")) + "</div>" +
      "</div>" +
      '<span class="fl-tier fl-tier-' + esc(provider.tier) + '">' + (provider.tier === "renewable" ? "Credits" : "Free") + "</span>" +
      "</div>" +
      '<div class="fl-chips">' + chips + "</div>" +
      "</button>";
  }

  function renderProviders() {
    var host = $("fl-providers");
    if (!host) return;
    var catalog = state.catalog;
    if (!catalog || !catalog.ok) {
      host.innerHTML = '<div class="fl-detail-empty"><i data-lucide="database-zap" class="w-6 h-6 text-emerald-500/60"></i>' +
        "<h4>Catalog not installed</h4><p>Run <code>python3 scripts/sync_free_llm_apis.py</code> to snapshot the awesome-free-llm-apis repository.</p></div>";
      icons();
      return;
    }
    var list = filteredProviders();
    var count = $("fl-results");
    if (count) count.textContent = fmt(list.length) + " / " + fmt(providers().length) + " providers";
    if (!list.length) {
      host.innerHTML = '<div class="fl-detail-empty"><i data-lucide="search-x" class="w-6 h-6 text-slate-600"></i>' +
        "<h4>No providers match</h4><p>Clear the search or widen the filters.</p></div>";
      icons();
      return;
    }
    host.innerHTML = '<div class="fl-provider-list">' + list.map(providerCard).join("") + "</div>";
    icons();
  }

  /* ------------------------------------------------------------------ */
  /* Provider detail + popular models                                    */
  /* ------------------------------------------------------------------ */
  function modelRow(model, providerId, showUse) {
    var use = showUse && model.id
      ? '<button type="button" class="fl-use" data-fl-use="' + esc(model.id) + '">Use</button>'
      : "";
    var link = model.url
      ? '<a class="fl-model-meta" href="' + esc(model.url) + '" target="_blank" rel="noopener noreferrer">docs</a>'
      : "";
    var meta = [model.context, model.rate_limit].filter(Boolean).join(" · ");
    return '<div class="fl-model">' +
      '<div class="fl-model-main">' +
      '<div class="fl-model-name">' + esc(model.name) + "</div>" +
      '<div class="fl-model-id">' + esc(model.id || "") + "</div>" +
      "</div>" +
      (meta ? '<span class="fl-model-meta">' + esc(meta) + "</span>" : "") +
      link + use +
      "</div>";
  }

  function topModelRow(model) {
    var provider = providerByName(model.provider);
    var match = null;
    if (provider) {
      for (var i = 0; i < (provider.models || []).length; i += 1) {
        if (String(provider.models[i].name).toLowerCase() === String(model.name).toLowerCase()) {
          match = provider.models[i];
          break;
        }
      }
    }
    var use = match
      ? '<button type="button" class="fl-use" data-fl-use="' + esc(match.id) + '" data-fl-provider="' + esc(provider.id) + '">Use</button>'
      : "";
    var link = model.url
      ? '<a class="fl-model-meta" href="' + esc(model.url) + '" target="_blank" rel="noopener noreferrer">docs</a>'
      : "";
    return '<div class="fl-model">' +
      '<div class="fl-model-main">' +
      '<div class="fl-model-name">' + esc(model.name) + "</div>" +
      '<div class="fl-model-id">' + esc(model.provider) + "</div>" +
      "</div>" +
      '<span class="fl-model-meta">' + esc([model.context, model.usage].filter(Boolean).join(" · ")) + "</span>" +
      link + use +
      "</div>";
  }

  function renderDetail() {
    var host = $("fl-detail");
    if (!host) return;
    var catalog = state.catalog;
    if (!catalog || !catalog.ok) {
      host.innerHTML = '<div class="fl-detail-empty"><i data-lucide="unplug" class="w-6 h-6 text-slate-600"></i>' +
        "<h4>No catalog to browse</h4><p>The dashboard reads the snapshot in public-plugins/free-llm-apis.</p></div>";
      icons();
      return;
    }
    var provider = state.selected ? providerById(state.selected) : null;
    if (!provider) {
      var top = (catalog.top_models || []).slice(0, 10);
      host.innerHTML = '<div class="fl-detail-empty" style="height:auto;padding:18px 18px 8px">' +
        '<i data-lucide="flame" class="w-6 h-6 text-emerald-400/70"></i>' +
        "<h4>Popular free models</h4>" +
        "<p>Pick a provider on the left to see its featured models and connect one.</p></div>" +
        '<div style="padding:0 14px 14px">' +
        '<div class="fl-section-title"><i data-lucide="trending-up" class="w-3 h-3"></i>Top free models by weekly usage</div>' +
        '<div class="fl-model-list">' + top.map(topModelRow).join("") + "</div></div>";
      icons();
      return;
    }

    var stats = [
      ["Free models", fmt(provider.free_models)],
      ["Max context", provider.max_context || "—"],
      ["Credit card", cardLabel(provider.credit_card)],
      ["Tier", provider.tier === "renewable" ? "Renewable credits" : "Permanent free"]
    ].map(function (pair) {
      return '<div class="fl-stat"><span>' + esc(pair[0]) + "</span><strong>" + esc(pair[1]) + "</strong></div>";
    }).join("");

    var chips = (provider.modalities || []).map(function (value) {
      return '<span class="fl-chip">' + esc(value) + "</span>";
    }).join("");

    var key = provider.key_url
      ? '<a class="sb-btn sb-btn-primary" href="' + esc(provider.key_url) + '" target="_blank" rel="noopener noreferrer"><i data-lucide="key-round" class="w-3.5 h-3.5"></i><span>Get API key</span></a>'
      : "";
    var base = provider.base_url
      ? '<div class="fl-base"><i data-lucide="link" class="w-3.5 h-3.5 shrink-0"></i><span style="flex:1">' + esc(provider.base_url) + "</span>" +
        '<button type="button" class="fl-use" data-fl-copy="' + esc(provider.base_url) + '">Copy</button></div>'
      : '<div class="fl-base" style="color:#94a3b8"><i data-lucide="link-2-off" class="w-3.5 h-3.5"></i><span>This provider does not publish a single OpenAI-compatible base URL.</span></div>';

    var models = (provider.models || []).map(function (model) {
      return modelRow(model, provider.id, true);
    }).join("");
    if (!models) {
      models = '<div class="fl-detail-empty" style="height:auto;padding:16px"><p>No featured model rows for this provider. Open its model page on freellm.net to copy a model ID.</p></div>';
    }

    host.innerHTML =
      '<div class="fl-detail-head">' +
      '<div style="min-width:0">' +
      '<div class="fl-detail-title">' + esc(provider.name) + "</div>" +
      '<div class="fl-detail-sub">' + esc(provider.free_models) + " free models · " + esc(provider.max_context || "context varies") + "</div>" +
      "</div>" +
      '<div class="fl-detail-actions">' +
      '<a class="sb-btn sb-btn-ghost" href="' + esc(catalog.site || "https://freellm.net") + '/models/" target="_blank" rel="noopener noreferrer"><i data-lucide="external-link" class="w-3.5 h-3.5"></i><span>Browse models</span></a>' +
      key +
      "</div></div>" +
      '<div class="fl-detail-stats">' + stats + "</div>" +
      base +
      '<div class="fl-section-title"><i data-lucide="layers" class="w-3 h-3"></i>Modalities</div>' +
      '<div class="fl-chips">' + (chips || '<span class="fl-chip">text</span>') + "</div>" +
      '<div class="fl-section-title"><i data-lucide="sparkles" class="w-3 h-3"></i>Featured models</div>' +
      '<div class="fl-model-list">' + models + "</div>";
    icons();
  }

  /* ------------------------------------------------------------------ */
  /* Connection form                                                     */
  /* ------------------------------------------------------------------ */
  function agentStatus(agentId) {
    return (state.agents && state.agents[agentId]) || {};
  }

  function renderConfig() {
    Array.prototype.forEach.call(document.querySelectorAll("[data-fl-agent]"), function (button) {
      button.classList.toggle("fl-agent-tab-active", button.getAttribute("data-fl-agent") === state.agent);
    });
    var status = agentStatus(state.agent);
    var base = $("fl-base");
    var model = $("fl-model");
    var key = $("fl-key");
    if (base) base.value = status.base_url || "";
    if (model) model.value = status.model || "";
    if (key) {
      key.value = "";
      key.placeholder = status.configured
        ? "Saved key " + (status.key_hint || "") + " — leave blank to keep"
        : "Paste the provider API key";
    }
    var line = $("fl-config-status");
    if (line) {
      line.classList.remove("fl-ok", "fl-warn");
      if (status.configured) {
        line.classList.add("fl-ok");
        line.textContent = "Connected · " + (status.model || "model") + " · key " + (status.key_hint || "saved");
      } else {
        line.textContent = "Not connected. Pick a provider and paste its key.";
      }
    }
    var states = $("fl-agent-states");
    if (states) {
      states.innerHTML = ["claude-code", "codex"].map(function (id) {
        var value = agentStatus(id);
        var label = id === "claude-code" ? "Claude Code" : "Codex CLI";
        return '<div class="fl-agent-state"><span class="fl-dot' + (value.configured ? " fl-dot-on" : "") + '"></span>' +
          "<strong>" + label + "</strong>" +
          "<span style='margin-left:auto'>" + esc(value.configured ? (value.model || "configured") : "not connected") + "</span></div>";
      }).join("");
    }
    icons();
  }

  function saveConfig(event) {
    if (event) event.preventDefault();
    var body = {
      agent_id: state.agent,
      base_url: ($("fl-base") || {}).value || "",
      model: ($("fl-model") || {}).value || "",
      api_key: ($("fl-key") || {}).value || ""
    };
    var line = $("fl-config-status");
    if (!body.base_url.trim() || !body.model.trim()) {
      if (line) { line.classList.remove("fl-ok"); line.classList.add("fl-warn"); line.textContent = "Base URL and model ID are required."; }
      return;
    }
    var button = $("fl-save");
    if (button) button.disabled = true;
    api("/api/freellmapi", { method: "POST", body: body }).then(function (result) {
      if (!result.ok) {
        if (line) { line.classList.remove("fl-ok"); line.classList.add("fl-warn"); line.textContent = (result.data && result.data.error) || "Could not save the connection."; }
        toast((result.data && result.data.error) || "Could not save the connection.", true);
        return;
      }
      state.agents[state.agent] = result.data.settings || {};
      renderConfig();
      renderSummary();
      toast("Connected. Choose " + (result.data.settings && result.data.settings.model || "the model") + " in the agent's model menu.");
    }).catch(function (error) {
      toast("Could not save: " + error.message, true);
    }).then(function () {
      if (button) button.disabled = false;
    });
  }

  function clearConfig() {
    var button = $("fl-clear");
    if (button) button.disabled = true;
    api("/api/freellmapi?agent_id=" + encodeURIComponent(state.agent), { method: "DELETE" }).then(function (result) {
      if (!result.ok) {
        toast((result.data && result.data.error) || "Could not clear the connection.", true);
        return;
      }
      state.agents[state.agent] = result.data.settings || {};
      renderConfig();
      renderSummary();
      toast("FreeLLMAPI connection cleared for this agent.");
    }).catch(function (error) {
      toast("Could not clear: " + error.message, true);
    }).then(function () {
      if (button) button.disabled = false;
    });
  }

  function useModel(providerId, modelId) {
    var provider = providerById(providerId);
    if (!provider) return;
    state.selected = providerId;
    renderProviders();
    renderDetail();
    renderConfig();
    var base = $("fl-base");
    var model = $("fl-model");
    var key = $("fl-key");
    if (base) base.value = provider.base_url || "";
    if (model) model.value = modelId || "";
    if (key) {
      key.value = "";
      if ((agentStatus(state.agent).base_url || "") !== (provider.base_url || "")) {
        key.placeholder = "Paste this provider's API key";
      }
    }
    var pane = $("fl-config-pane");
    if (pane && pane.scrollIntoView) {
      try { pane.scrollIntoView({ behavior: "smooth", block: "nearest" }); } catch (e) { /* ignore */ }
    }
    if (key) { try { key.focus(); } catch (e) { /* ignore */ } }
    toast("Model loaded into the connection form. Paste the provider key to finish.");
  }

  /* ------------------------------------------------------------------ */
  /* Load + wiring                                                       */
  /* ------------------------------------------------------------------ */
  function load(silent) {
    if (state.loading) return;
    state.loading = true;
    var button = $("fl-refresh");
    if (button) button.disabled = true;
    api("/api/freellmapi/catalog").then(function (result) {
      if (!result.ok) {
        toast((result.data && result.data.error) || "Could not load the catalog", true);
        return;
      }
      state.catalog = result.data.catalog || null;
      state.agents = result.data.agents || {};
      renderHeader();
      renderSummary();
      renderFilters();
      renderProviders();
      renderDetail();
      renderConfig();
      if (state.catalog && state.catalog.ok && !silent) {
        toast(fmt(state.catalog.counts.models) + " free models from " + fmt(state.catalog.counts.providers) + " providers loaded.");
      }
    }).catch(function (error) {
      toast("Could not load the catalog: " + error.message, true);
    }).then(function () {
      state.loading = false;
      if (button) button.disabled = false;
    });
  }

  function init() {
    if (!$("freellm-view") || state.initialized) return;
    state.initialized = true;

    var search = $("fl-search");
    if (search) search.addEventListener("input", function () {
      state.query = search.value || "";
      renderProviders();
    });

    Array.prototype.forEach.call(document.querySelectorAll("[data-fl-tier]"), function (button) {
      button.addEventListener("click", function () {
        state.tier = button.getAttribute("data-fl-tier") || "all";
        renderFilters();
        renderProviders();
      });
    });

    var card = $("fl-card");
    if (card) card.addEventListener("change", function () {
      state.card = card.value || "all";
      renderProviders();
    });

    var modality = $("fl-modality");
    if (modality) modality.addEventListener("change", function () {
      state.modality = modality.value || "all";
      renderProviders();
    });

    var refresh = $("fl-refresh");
    if (refresh) refresh.addEventListener("click", load);

    var list = $("fl-providers");
    if (list) list.addEventListener("click", function (event) {
      var item = event.target.closest("[data-fl-provider]");
      if (!item) return;
      state.selected = item.getAttribute("data-fl-provider");
      renderProviders();
      renderDetail();
    });

    var detail = $("fl-detail");
    if (detail) detail.addEventListener("click", function (event) {
      var copy = event.target.closest("[data-fl-copy]");
      if (copy) {
        copyText(copy.getAttribute("data-fl-copy"), "Base URL");
        return;
      }
      var use = event.target.closest("[data-fl-use]");
      if (!use) return;
      var providerId = use.getAttribute("data-fl-provider") || state.selected;
      useModel(providerId, use.getAttribute("data-fl-use"));
    });

    Array.prototype.forEach.call(document.querySelectorAll("[data-fl-agent]"), function (button) {
      button.addEventListener("click", function () {
        state.agent = button.getAttribute("data-fl-agent") || "claude-code";
        renderConfig();
      });
    });

    var form = $("fl-config-form");
    if (form) form.addEventListener("submit", saveConfig);
    var clear = $("fl-clear");
    if (clear) clear.addEventListener("click", clearConfig);

    renderConfig();
    load();
  }

  function refresh() {
    if (!state.initialized) { init(); return; }
    if (state.catalog) {
      renderHeader();
      renderSummary();
      renderProviders();
      renderDetail();
      renderConfig();
    }
    load(true);
  }

  function open() {
    if (window.switchMainView) window.switchMainView("freellm");
    else refresh();
  }

  window.FreeLLMAPI = { init: init, refresh: refresh, open: open, load: load };

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
