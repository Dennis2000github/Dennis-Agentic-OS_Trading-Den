/*
 * AI Connections manager: providers, gateways/proxies, routing policy.
 *
 * One manager for every AI connection route (official providers and
 * alternative gateways) plus the global Free/Paid access preference.
 * The backend owns all secrets: this view only ever sees key hints,
 * health from real endpoint tests and discovered model lists.
 *
 * Defensive by design: when its DOM contract is absent it does nothing.
 */
(function () {
  "use strict";

  var state = {
    token: null,
    connections: [],
    presets: { providers: {}, gateways: {} },
    matrix: [],
    preferences: null,
    models: {},      // connectionId -> {models, error, stale}
    expanded: {},    // connectionId -> bool
    editing: null,   // connection id being edited (null = adding)
    typeFilter: "gateway", // modal type segment
    presetId: "",
  };

  function $(id) { return document.getElementById(id); }
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function icons() {
    if (window.lucide && window.lucide.createIcons) window.lucide.createIcons();
  }
  function ready(fn) {
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", fn);
    else fn();
  }
  function timeAgo(iso) {
    if (!iso) return "";
    var then = Date.parse(iso);
    if (isNaN(then)) return "";
    var s = Math.max(0, Math.floor((Date.now() - then) / 1000));
    if (s < 60) return "just now";
    if (s < 3600) return Math.floor(s / 60) + "m ago";
    if (s < 86400) return Math.floor(s / 3600) + "h ago";
    return Math.floor(s / 86400) + "d ago";
  }
  function toast(msg, kind) {
    var el = $("cn-toast");
    if (!el) return;
    el.textContent = msg;
    el.dataset.kind = kind || "info";
    el.classList.add("show");
    clearTimeout(toast._t);
    toast._t = setTimeout(function () { el.classList.remove("show"); }, 3200);
  }

  function api(path, opts) {
    opts = opts || {};
    var headers = opts.headers || {};
    if (state.token) headers["X-Dennis-Token"] = state.token;
    if (opts.body !== undefined) headers["Content-Type"] = "application/json";
    return fetch(path, {
      method: opts.method || "GET",
      headers: headers,
      body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
    }).then(function (res) {
      return res.json().catch(function () { return {}; }).then(function (data) {
        return { ok: res.ok, status: res.status, data: data };
      });
    });
  }

  // -- status visuals ----------------------------------------------------
  var STATUS_CLASS = {
    connected: "ok", configured: "mid", auth_required: "warn",
    not_configured: "warn", auth_failed: "bad", unavailable: "bad",
    rate_limited: "warn", configuration_error: "bad", testing: "mid",
    unknown: "mid",
  };
  var STATUS_LABEL = {
    connected: "Connected", configured: "Configured", auth_required: "Auth required",
    not_configured: "Not configured", auth_failed: "Auth failed",
    unavailable: "Unavailable", rate_limited: "Rate limited",
    configuration_error: "Config error", testing: "Testing", unknown: "Unknown",
  };
  var PRICING_LABEL = {
    free: "Free", free_tier: "Free tier", paid: "Paid",
    subscription: "Subscription", local: "Local", unknown: "Pricing ?",
  };

  function statusOf(conn) {
    var h = conn.health || {};
    var fresh = h.checked_at && (Date.now() - Date.parse(h.checked_at)) < 300000;
    var status = h.status || "unknown";
    if (!fresh && status === "connected") status = "configured";
    if (!fresh && status === "unknown") {
      status = (conn.base_url && (conn.auth || {}).configured) ? "configured" : "not_configured";
    }
    if (!(conn.auth || {}).configured && (status === "unknown" || status === "configured")) {
      var preset = (state.presets[conn.type === "provider" ? "providers" : "gateways"] || {})[conn.provider_id] || {};
      if (preset.key_env) status = "auth_required";
    }
    return status;
  }

  // -- rendering ---------------------------------------------------------
  function render() {
    var badge = $("cn-count-badge");
    if (badge) badge.textContent = String(state.connections.length);
    renderPane("cn-providers", "provider");
    renderPane("cn-gateways", "gateway");
    renderPreferences();
    icons();
  }

  function renderPane(hostId, type) {
    var host = $(hostId);
    if (!host) return;
    var rows = state.connections.filter(function (c) { return c.type === type; });
    if (!rows.length) {
      host.innerHTML = '<div class="sb-rail-empty">No ' + (type === "provider" ? "provider" : "gateway") +
        ' connections yet. Use <strong>Add connection</strong> to add one.</div>';
      return;
    }
    host.innerHTML = rows.map(function (c) { return cardHtml(c); }).join("");
    rows.forEach(function (c) { bindCard(host, c); });
  }

  function cardHtml(c) {
    var status = statusOf(c);
    var cls = STATUS_CLASS[status] || "mid";
    var auth = c.auth || {};
    var protocolLabel = c.protocol === "anthropic" ? "Anthropic-compatible" : "OpenAI-compatible";
    var typeLabel = c.type === "provider" ? "Provider" : "Gateway";
    var derived = c.derived ? '<span class="cn-chip" title="Managed by the FreeLLMAPI per-agent settings">FreeLLMAPI</span>' : "";
    var compatChip = "";
    if (c.compatibility && c.compatibility !== "compatible") {
      compatChip = '<span class="cn-chip cn-chip-warn" title="' + esc(c.compatibility_reason || "") + '">' +
        esc(c.compatibility) + "</span>";
    }
    var modelCount = (state.models[c.id] || {}).count;
    var countText = modelCount != null ? (modelCount + " model" + (modelCount === 1 ? "" : "s")) : "";
    return '' +
      '<div class="cn-card" data-conn="' + esc(c.id) + '">' +
        '<div class="cn-card-head">' +
          '<span class="cn-dot cn-dot-' + cls + '" title="' + esc(STATUS_LABEL[status] || status) + '"></span>' +
          '<div class="cn-card-title">' +
            '<strong>' + esc(c.name) + '</strong>' +
            '<span class="cn-chip">' + esc(typeLabel) + '</span>' +
            '<span class="cn-chip">' + esc(protocolLabel) + '</span>' +
            '<span class="cn-chip">' + esc(PRICING_LABEL[c.pricing] || c.pricing) + '</span>' +
            derived + compatChip +
          '</div>' +
        '</div>' +
        '<div class="cn-card-meta">' +
          '<span class="cn-url" title="' + esc(c.base_url) + '">' + esc(c.base_url || "no base URL") + '</span>' +
          '<span>' + esc(STATUS_LABEL[status] || status) +
            (c.health && c.health.latency_ms != null ? ' · ' + c.health.latency_ms + 'ms' : '') +
            (c.health && c.health.checked_at ? ' · ' + timeAgo(c.health.checked_at) : '') + '</span>' +
          (auth.configured ? '<span>Key ' + esc(auth.hint || "saved") + ' (' + esc(auth.source) + ')</span>'
                           : (c.key_env ? '<span>Key via ' + esc(c.key_env) + '</span>' : '<span class="cn-warn">No key</span>')) +
          (countText ? '<span>' + esc(countText) + '</span>' : '') +
        '</div>' +
        (c.notes ? '<p class="cn-card-notes">' + esc(c.notes) + '</p>' : "") +
        '<div class="cn-card-actions">' +
          '<button data-act="test" class="sb-btn sb-btn-ghost" title="Run a real endpoint test"><i data-lucide="activity" class="w-3.5 h-3.5"></i><span>Test</span></button>' +
          '<button data-act="models" class="sb-btn sb-btn-ghost" title="Discover or view models"><i data-lucide="boxes" class="w-3.5 h-3.5"></i><span>Models</span></button>' +
          (c.derived ? "" : '<button data-act="edit" class="sb-btn sb-btn-ghost"><i data-lucide="pencil" class="w-3.5 h-3.5"></i><span>Edit</span></button>' +
                            '<button data-act="delete" class="sb-btn sb-btn-ghost cn-danger" title="Remove connection"><i data-lucide="trash-2" class="w-3.5 h-3.5"></i><span>Remove</span></button>') +
        '</div>' +
        '<div class="cn-models hidden" data-models></div>' +
      '</div>';
  }

  function bindCard(host, c) {
    var card = host.querySelector('[data-conn="' + c.id + '"]');
    if (!card) return;
    card.querySelectorAll("[data-act]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var act = btn.dataset.act;
        if (act === "test") testConnection(c);
        else if (act === "models") toggleModels(c, card);
        else if (act === "edit") openModal(c);
        else if (act === "delete") removeConnection(c);
      });
    });
  }

  function renderModels(c, host) {
    var data = state.models[c.id] || {};
    var models = data.models || [];
    var html = "";
    if (data.error) {
      html += '<div class="cn-model-error">' + esc(data.error) +
        (data.stale ? " — showing the last known list (stale)." : "") + '</div>';
    }
    if (!models.length) {
      html += '<div class="cn-model-empty">No models discovered. Try Discover, or add a model id manually below.</div>';
    } else {
      html += '<div class="cn-model-list">' + models.map(function (m) {
        var caps = m.capabilities || {};
        var capChips = ["image", "audio", "video", "tools"].filter(function (k) {
          return caps[k] && caps[k] !== "unknown";
        }).map(function (k) {
          return '<span class="cn-chip' + (caps[k] === "supported" ? "" : " cn-chip-dim") + '">' + k + ": " + esc(caps[k]) + '</span>';
        }).join("");
        return '<div class="cn-model-row">' +
          '<code>' + esc(m.native_id) + '</code>' +
          (m.name && m.name !== m.native_id ? '<span>' + esc(m.name) + '</span>' : "") +
          (m.context_window ? '<span class="cn-chip">ctx ' + esc(shortNum(m.context_window)) + '</span>' : "") +
          '<span class="cn-chip">' + esc(PRICING_LABEL[m.pricing] || m.pricing) + '</span>' +
          capChips +
          (m.source === "manual" ? '<span class="cn-chip">manual</span>' : "") +
          '<button class="cn-mini" data-remove-model="' + esc(m.native_id) + '" title="Remove from connection"><i data-lucide="x" class="w-3 h-3"></i></button>' +
        '</div>';
      }).join("") + '</div>';
    }
    html += '<div class="cn-model-add">' +
      '<input type="text" data-add-model placeholder="Add model id manually (e.g. gpt-4o-mini)" aria-label="Model id">' +
      '<button class="sb-btn sb-btn-ghost" data-add-model-btn><i data-lucide="plus" class="w-3.5 h-3.5"></i><span>Add</span></button>' +
      '<button class="sb-btn sb-btn-ghost" data-discover><i data-lucide="download-cloud" class="w-3.5 h-3.5"></i><span>Discover</span></button>' +
    '</div>';
    host.innerHTML = html;
    host.classList.remove("hidden");
    var addBtn = host.querySelector("[data-add-model-btn]");
    var input = host.querySelector("[data-add-model]");
    if (addBtn && input) {
      addBtn.addEventListener("click", function () {
        var id = (input.value || "").trim();
        if (!id) return;
        api("/api/connections/" + encodeURIComponent(c.id) + "/models", {
          method: "POST", body: { native_id: id }
        }).then(function (r) {
          if (!r.ok) { toast(r.data.error || "Could not add the model.", "bad"); return; }
          input.value = "";
          toast("Model added to " + c.name + ".");
          loadModels(c, true).then(function () { render(); });
        });
      });
    }
    var discoverBtn = host.querySelector("[data-discover]");
    if (discoverBtn) discoverBtn.addEventListener("click", function () { discoverModels(c); });
    host.querySelectorAll("[data-remove-model]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        api("/api/connections/" + encodeURIComponent(c.id) + "/models?native_id=" +
            encodeURIComponent(btn.dataset.removeModel), { method: "DELETE" })
          .then(function (r) {
            if (!r.ok) { toast(r.data.error || "Could not remove the model.", "bad"); return; }
            loadModels(c, true).then(function () { render(); });
          });
      });
    });
    icons();
  }

  function shortNum(n) {
    if (n >= 1000000) return (n / 1000000).toFixed(n % 1000000 ? 1 : 0) + "M";
    if (n >= 1000) return Math.round(n / 1000) + "K";
    return String(n);
  }

  function toggleModels(c, card) {
    var host = card.querySelector("[data-models]");
    if (!host) return;
    if (!host.classList.contains("hidden")) {
      host.classList.add("hidden");
      return;
    }
    host.classList.remove("hidden");
    host.innerHTML = '<div class="cn-model-empty">Loading models…</div>';
    loadModels(c, false).then(function () {
      if (host.isConnected) renderModels(c, host);
    });
  }

  function loadModels(c, refresh) {
    return api("/api/connections/" + encodeURIComponent(c.id) + "/models" + (refresh ? "?refresh=1" : ""))
      .then(function (r) {
        var data = r.data || {};
        state.models[c.id] = {
          models: data.models || [], error: data.error || null,
          stale: !!data.stale, count: (data.models || []).length,
        };
        return state.models[c.id];
      });
  }

  function discoverModels(c) {
    toast("Discovering models on " + c.name + "…");
    api("/api/connections/" + encodeURIComponent(c.id) + "/discover", { method: "POST", body: {} })
      .then(function (r) {
        if (!r.ok) { toast(r.data.error || "Discovery failed.", "bad"); }
        else { toast(((r.data.models || []).length) + " models discovered."); }
        state.models[c.id] = {
          models: r.data.models || [], error: r.data.error || null,
          stale: !!r.data.stale, count: (r.data.models || []).length,
        };
        refresh();
      });
  }

  function testConnection(c) {
    toast("Testing " + c.name + "…");
    api("/api/connections/" + encodeURIComponent(c.id) + "/test", { method: "POST", body: {} })
      .then(function (r) {
        var health = (r.data || {}).health || {};
        if (health.status === "connected") toast("Connected" + (health.model_count != null ? " · " + health.model_count + " models" : "") + " · " + (health.latency_ms || 0) + "ms.", "ok");
        else toast(health.detail || STATUS_LABEL[health.status] || "Test finished.", health.status === "configured" ? "info" : "bad");
        refresh();
      });
  }

  function removeConnection(c) {
    var label = c.derived ? ("This removes the FreeLLMAPI settings for " + c.id.replace("freellmapi:", "") + ".") : "Remove this connection?";
    if (!window.confirm(label + "\n\n" + c.name)) return;
    api("/api/connections/" + encodeURIComponent(c.id), { method: "DELETE" })
      .then(function (r) {
        if (!r.ok) { toast(r.data.error || "Could not remove the connection.", "bad"); return; }
        toast("Connection removed.");
        refresh();
      });
  }

  // -- routing preferences -------------------------------------------------
  function renderPreferences() {
    var prefs = state.preferences;
    if (!prefs) return;
    var mode = (prefs.global || {}).mode || "free_first";
    document.querySelectorAll("[data-cn-mode]").forEach(function (btn) {
      btn.classList.toggle("cn-seg-btn-active", btn.dataset.cnMode === mode);
    });
    var allow = $("cn-allow-paid");
    if (allow) allow.checked = !!(prefs.global || {}).allow_paid_fallback;
    var note = $("cn-override-note");
    var agentKeys = Object.keys(prefs.agents || {});
    if (note) {
      if (agentKeys.length) {
        note.textContent = "Per-agent overrides: " + agentKeys.map(function (k) {
          return k + " → " + prefs.agents[k].mode;
        }).join(", ");
        note.classList.remove("hidden");
      } else {
        note.classList.add("hidden");
      }
    }
  }

  function setMode(mode) {
    api("/api/routing/preferences", { method: "POST", body: { mode: mode } })
      .then(function (r) {
        if (!r.ok) { toast(r.data.error || "Could not save the preference.", "bad"); return; }
        state.preferences = r.data.preferences;
        renderPreferences();
        toast("AI access preference: " + mode.replace("_", " ") + ".");
      });
  }

  function setAllowPaid(allowed) {
    api("/api/routing/preferences", {
      method: "POST", body: { mode: (state.preferences.global || {}).mode || "free_first",
                              allow_paid_fallback: allowed }
    }).then(function (r) {
      if (!r.ok) { toast(r.data.error || "Could not save.", "bad"); return; }
      state.preferences = r.data.preferences;
      toast(allowed ? "Paid fallback allowed." : "Paid fallback requires approval again.");
    });
  }

  // -- add / edit modal ----------------------------------------------------
  function currentPreset() {
    var bucket = state.presets[state.typeFilter === "provider" ? "providers" : "gateways"] || {};
    return bucket[state.presetId] || null;
  }

  function fillPresets() {
    var sel = $("cn-preset");
    if (!sel) return;
    var bucket = state.presets[state.typeFilter === "provider" ? "providers" : "gateways"] || {};
    var keys = Object.keys(bucket);
    sel.innerHTML = '<option value="">Custom (manual base URL)…</option>' + keys.map(function (k) {
      return '<option value="' + esc(k) + '"' + (state.presetId === k ? " selected" : "") + '>' +
        esc(bucket[k].label || k) + '</option>';
    }).join("");
    var note = $("cn-preset-note");
    var preset = currentPreset();
    if (note) note.textContent = preset ? (preset.notes || "") : "";
    if (preset) {
      if ($("cn-base-url")) $("cn-base-url").value = preset.base_url || "";
      if ($("cn-protocol")) $("cn-protocol").value = preset.protocol || "openai";
      if ($("cn-pricing") && preset.pricing) $("cn-pricing").value = preset.pricing;
      if ($("cn-name") && !$("cn-name").value && preset.label) $("cn-name").value = preset.label;
    }
    var protoField = $("cn-protocol-field");
    if (protoField) protoField.classList.toggle("hidden", state.typeFilter === "provider" && !!state.presetId);
  }

  function openModal(existing) {
    var modal = $("cn-modal");
    if (!modal) return;
    state.editing = existing ? existing.id : null;
    state.typeFilter = existing ? existing.type : state.typeFilter;
    state.presetId = existing ? "" : state.presetId;
    document.querySelectorAll("[data-cn-type]").forEach(function (btn) {
      btn.classList.toggle("cn-seg-btn-active", btn.dataset.cnType === state.typeFilter);
    });
    var title = $("cn-modal-title");
    if (title) title.textContent = existing ? "Edit connection" : "Add connection";
    var set = function (id, v) { var el = $(id); if (el) el.value = v == null ? "" : v; };
    set("cn-name", existing ? existing.name : "");
    set("cn-base-url", existing ? existing.base_url : "");
    set("cn-key", "");
    set("cn-notes", existing ? existing.notes : "");
    var pricing = $("cn-pricing");
    if (pricing) pricing.value = existing ? (existing.pricing || "unknown") : "unknown";
    var protocol = $("cn-protocol");
    if (protocol) protocol.value = existing ? (existing.protocol || "openai") : "openai";
    var msg = $("cn-modal-msg");
    if (msg) { msg.textContent = ""; msg.classList.remove("cn-error"); }
    fillPresets();
    modal.classList.remove("hidden");
    icons();
  }

  function closeModal() {
    var modal = $("cn-modal");
    if (modal) modal.classList.add("hidden");
    state.editing = null;
  }

  function saveModal() {
    var payload = {
      name: ($("cn-name").value || "").trim(),
      base_url: ($("cn-base-url").value || "").trim(),
      api_key: ($("cn-key").value || "").trim(),
      pricing: $("cn-pricing").value || "unknown",
      notes: ($("cn-notes").value || "").trim(),
      protocol: $("cn-protocol").value || "openai",
    };
    var msg = $("cn-modal-msg");
    function fail(text) { if (msg) { msg.textContent = text; msg.classList.add("cn-error"); } }
    if (!payload.base_url) { fail("A base URL is required."); return; }
    function saveError(r) {
      // A 404 on a new route means the page was reloaded but the backend
      // process still runs older code: say so instead of a generic error.
      if (r.status === 404) {
        fail("This DENNIS backend does not know the connections API yet. " +
             "Restart app.py (close the terminal window and start it again), " +
             "then reload the page.");
        return;
      }
      fail((r.data && r.data.error) || "Could not save the connection (HTTP " + r.status + ").");
    }
    if (state.editing) {
      api("/api/connections/" + encodeURIComponent(state.editing), { method: "PATCH", body: payload })
        .then(function (r) {
          if (!r.ok) { saveError(r); return; }
          closeModal(); toast("Connection saved."); refresh();
        });
      return;
    }
    payload.type = state.typeFilter;
    payload.preset_id = state.presetId || "";
    payload.provider_id = state.presetId || (state.typeFilter === "provider" ? "custom_provider" : "custom_gateway");
    payload.name = payload.name || (currentPreset() || {}).label || (state.typeFilter === "provider" ? "Custom provider" : "Custom gateway");
    api("/api/connections", { method: "POST", body: payload })
      .then(function (r) {
        if (!r.ok) { saveError(r); return; }
        closeModal();
        toast("Connection added. Run Test to check it.");
        refresh();
      });
  }

  // -- bootstrap -----------------------------------------------------------
  function load() {
    return Promise.all([
      api("/api/auth/session"),
      api("/api/connections"),
      api("/api/routing/preferences"),
    ]).then(function (results) {
      var auth = results[0].data || {};
      state.token = auth.token || state.token;
      var data = results[1].data || {};
      state.connections = data.connections || [];
      state.presets = data.presets || { providers: {}, gateways: {} };
      state.matrix = data.matrix || [];
      state.preferences = (results[2].data || {}).preferences || null;
      render();
    }).catch(function () {
      toast("Backend not reachable. Start app.py, then reload.", "bad");
    });
  }

  function refresh() { return load(); }

  function bind() {
    var refreshBtn = $("cn-refresh");
    if (refreshBtn) refreshBtn.addEventListener("click", function () { refresh(); });
    var addBtn = $("cn-add");
    if (addBtn) addBtn.addEventListener("click", function () { openModal(null); });
    var closeBtn = $("cn-modal-close");
    if (closeBtn) closeBtn.addEventListener("click", closeModal);
    var cancelBtn = $("cn-cancel");
    if (cancelBtn) cancelBtn.addEventListener("click", closeModal);
    var saveBtn = $("cn-save");
    if (saveBtn) saveBtn.addEventListener("click", saveModal);
    var modal = $("cn-modal");
    if (modal) modal.addEventListener("click", function (ev) {
      if (ev.target === modal) closeModal();
    });
    document.addEventListener("keydown", function (ev) {
      if (ev.key === "Escape" && modal && !modal.classList.contains("hidden")) closeModal();
    });
    document.querySelectorAll("[data-cn-type]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        state.typeFilter = btn.dataset.cnType;
        state.presetId = "";
        document.querySelectorAll("[data-cn-type]").forEach(function (b) {
          b.classList.toggle("cn-seg-btn-active", b === btn);
        });
        fillPresets();
      });
    });
    var presetSel = $("cn-preset");
    if (presetSel) presetSel.addEventListener("change", function () {
      state.presetId = presetSel.value;
      fillPresets();
    });
    document.querySelectorAll("[data-cn-mode]").forEach(function (btn) {
      btn.addEventListener("click", function () { setMode(btn.dataset.cnMode); });
    });
    var allowPaid = $("cn-allow-paid");
    if (allowPaid) allowPaid.addEventListener("change", function () {
      setAllowPaid(allowPaid.checked);
    });
  }

  ready(function () {
    if (!$("connections-view")) return;  // DOM contract absent: do nothing
    bind();
    load();
  });

  window.AIConnections = { refresh: refresh };
})();
