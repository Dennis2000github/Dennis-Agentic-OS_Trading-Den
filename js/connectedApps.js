/* =====================================================================
   CONNECTED APPS — one dashboard, two sources, no duplication.

   Source 1: Composio connected accounts (from /api/composio/overview).
   Source 2: API Mega List entries the user saved/configured
             (from /api/megalist/saved).

   Every row deep-links into the surface that owns it: Composio rows
   open the Composio tab; Mega List rows open the API Mega List tab or
   the API's documentation.
   ===================================================================== */
(function () {
  "use strict";

  var state = {
    initialized: false,
    loading: false,
    composio: [],
    toolkits: {},
    megalist: [],
    collections: 0
  };

  function $(id) { return document.getElementById(id); }

  function esc(value) {
    return String(value == null ? "" : value).replace(/[&<>"']/g, function (ch) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch];
    });
  }

  function icons() {
    if (window.lucide && window.lucide.createIcons) {
      try { window.lucide.createIcons(); } catch (error) { /* no-op */ }
    }
  }

  function toast(message, isError) {
    var el = $("ca-toast");
    if (!el) return;
    el.textContent = message;
    el.dataset.kind = isError ? "error" : "info";
    el.classList.add("show");
    clearTimeout(toast._t);
    toast._t = setTimeout(function () { el.classList.remove("show"); }, 3200);
  }

  function setStatus(text, isError) {
    var el = $("ca-status-text");
    if (el) el.textContent = text;
    var pill = $("ca-status-pill");
    if (pill) pill.classList.toggle("sb-pill-warn", !!isError);
  }

  function api(path) {
    return fetch(path, { method: "GET", cache: "no-store" }).then(function (response) {
      return response.json().catch(function () { return {}; }).then(function (data) {
        return { ok: response.ok, status: response.status, data: data };
      });
    });
  }

  var MEGALIST_SAVED_URL = "/api/megalist/saved?page_size=100";

  function goTab(tab, subtab) {
    if (window.PluginsHub) window.PluginsHub.show(tab, subtab);
    else if (window.switchMainView) window.switchMainView("plugins");
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

  /* ------------------------------------------------------------------ */
  /* Composio accounts                                                    */
  /* ------------------------------------------------------------------ */
  function statusClass(status) {
    var value = String(status || "").toUpperCase();
    if (value === "ACTIVE" || value === "connected") return "ok";
    if (value === "INITIATED" || value === "FAILED" || value === "EXPIRED") return "warn";
    return "muted";
  }

  function composioRow(connection) {
    var toolkit = state.toolkits[String(connection.toolkit || "").toLowerCase()] || {};
    var letter = esc(String(toolkit.name || connection.toolkit || "?").charAt(0).toUpperCase());
    var img = toolkit.logo
      ? '<img src="' + esc(toolkit.logo) + '" alt="" loading="lazy" onerror="this.remove()">'
      : "";
    var status = connection.status || "connected";
    return '<div class="ca-row">' +
      '<span class="ca-logo"><span class="ca-logo-letter">' + letter + '</span>' + img + '</span>' +
      '<span class="ca-row-main"><strong>' + esc(toolkit.name || connection.toolkit || "App") + '</strong>' +
      '<small>' + esc(connection.toolkit || "") + '</small></span>' +
      '<span class="ca-row-meta">' + esc(timeAgo(connection.created_at || connection.updated_at)) + '</span>' +
      '<span class="ca-status ca-status-' + statusClass(status) + '">' + esc(String(status).toLowerCase()) + '</span>' +
      '<button type="button" class="sb-btn sb-btn-ghost" data-ca-composio><i data-lucide="external-link" class="w-3.5 h-3.5"></i><span>Composio</span></button>' +
      '</div>';
  }

  /* ------------------------------------------------------------------ */
  /* Mega List saved APIs                                                 */
  /* ------------------------------------------------------------------ */
  function megalistRow(entry) {
    var doc = entry.documentation_url || entry.source_url_clean || entry.source_url || "";
    var collection = entry.collection_name || entry.collection || "";
    return '<div class="ca-row">' +
      '<span class="ca-logo ca-logo-flat"><i data-lucide="github" class="w-4 h-4"></i></span>' +
      '<span class="ca-row-main"><strong>' + esc(entry.name || entry.id) + '</strong>' +
      '<small>' + esc(entry.category || "") + (entry.pricing_model ? " · " + esc(entry.pricing_model) : "") +
      (collection ? " · " + esc(collection) : "") + '</small></span>' +
      '<span class="ca-row-meta">' + esc(timeAgo(entry.saved_at)) + '</span>' +
      '<span class="ca-status ca-status-ok">saved</span>' +
      (doc ? '<a class="sb-btn sb-btn-ghost" href="' + esc(doc) + '" target="_blank" rel="noopener"><i data-lucide="book-open" class="w-3.5 h-3.5"></i><span>Docs</span></a>' : "") +
      '<button type="button" class="sb-btn sb-btn-ghost" data-ca-megalist><i data-lucide="github" class="w-3.5 h-3.5"></i><span>Mega List</span></button>' +
      '</div>';
  }

  function emptyRow(message) {
    return '<div class="sb-rail-empty">' + esc(message) + '</div>';
  }

  function render() {
    var total = state.composio.length + state.megalist.length;

    if ($("ca-count-composio")) $("ca-count-composio").textContent = String(state.composio.length);
    if ($("ca-count-megalist")) $("ca-count-megalist").textContent = String(state.megalist.length);
    if ($("ca-count-total")) $("ca-count-total").textContent = String(total);
    if ($("ca-count-collections")) $("ca-count-collections").textContent = String(state.collections);
    if ($("ca-composio-sub")) $("ca-composio-sub").textContent = state.composio.length + " accounts";
    if ($("ca-megalist-sub")) {
      var megaLabel = state.megalist.length + " saved APIs";
      if (state.megalist.totalHidden) megaLabel += " (+" + state.megalist.totalHidden + " more)";
      $("ca-megalist-sub").textContent = megaLabel;
    }

    var host = $("ca-composio-list");
    if (host) host.innerHTML = state.composio.length
      ? state.composio.map(composioRow).join("")
      : emptyRow("No Composio accounts yet. Open the Composio tab to connect apps.");

    var mega = $("ca-megalist-list");
    if (mega) mega.innerHTML = state.megalist.length
      ? state.megalist.map(megalistRow).join("")
      : emptyRow("No configured API Mega List entries yet. Save APIs from the API Mega List tab.");

    document.dispatchEvent(new CustomEvent("dennis:hub-count", { detail: { key: "apps", value: total } }));
    icons();
  }

  function bindList(hostId) {
    var host = $(hostId);
    if (!host || host._caBound) return;
    host._caBound = true;
    host.addEventListener("click", function (event) {
      if (event.target.closest("[data-ca-composio]")) { goTab("composio"); return; }
      if (event.target.closest("[data-ca-megalist]")) { goTab("megalist"); return; }
    });
  }

  function load() {
    if (state.loading) return;
    state.loading = true;
    setStatus("Loading…");
    return Promise.all([
      api("/api/composio/overview"),
      api(MEGALIST_SAVED_URL)
    ]).then(function (results) {
      var overview = (results[0] && results[0].data) || {};
      var saved = (results[1] && results[1].data) || {};

      state.composio = (overview.connections || []).filter(function (connection) {
        var status = String(connection.status || "").toUpperCase();
        return status !== "INITIATED" && status !== "FAILED" && status !== "EXPIRED" && status !== "DELETED";
      });
      var toolkits = {};
      (overview.catalog && overview.catalog.toolkits || []).forEach(function (item) {
        if (item && item.slug) toolkits[String(item.slug).toLowerCase()] = item;
      });
      state.toolkits = toolkits;

      var entries = saved.items || saved.entries || [];
      state.megalist = entries.filter(function (entry) { return entry && entry.saved !== false; });
      // The endpoint pages; total tells us if more saved APIs exist.
      if (saved.total && saved.total > state.megalist.length) {
        state.megalist.totalHidden = saved.total - state.megalist.length;
      } else {
        state.megalist.totalHidden = 0;
      }
      state.collections = (saved.collections || []).length || 0;

      var failed = results.some(function (result) { return !result.ok; });
      setStatus(failed ? "Partial data (a source failed)" : "Up to date", failed);
      render();
    }).catch(function (error) {
      setStatus("Load failed", true);
      toast("Connected Apps failed to load: " + error.message, true);
    }).then(function () {
      state.loading = false;
    });
  }

  function init() {
    if (!$("tools-panel-apps") || state.initialized) return;
    state.initialized = true;
    bindList("ca-composio-list");
    bindList("ca-megalist-list");
    if ($("ca-refresh")) $("ca-refresh").addEventListener("click", function () { load(); });
  }

  function refresh() {
    init();
    if (state.initialized) load();
  }

  window.ConnectedApps = { init: init, refresh: refresh };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
