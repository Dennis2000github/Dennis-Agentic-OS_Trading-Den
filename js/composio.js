/* =====================================================================
   COMPOSIO DASHBOARD — local dashboard shell backed by the existing adapter.

   The UI deliberately calls the OS's owner-gated Composio routes rather than
   handling provider OAuth or exposing credentials in the browser. Composio's
   hosted dashboard remains available for project features not exposed here.
   ===================================================================== */
(function () {
  "use strict";

  var state = {
    tab: "megalist",
    view: "overview",
    initialized: false,
    loading: false,
    busy: false,
    overview: null,
    toolkits: [],
    query: "",
    selected: "",
    detail: null,
    detailLoading: false,
    client: "generic"
  };

  function $(id) { return document.getElementById(id); }

  function esc(value) {
    return String(value == null ? "" : value).replace(/[&<>"']/g, function (ch) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch];
    });
  }

  function icons() {
    if (window.lucide && window.lucide.createIcons) {
      try { window.lucide.createIcons(); } catch (error) { /* best effort */ }
    }
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
    var el = $("cp-toast");
    if (!el) return;
    el.textContent = text || "";
    el.classList.toggle("text-amber-300", !!warning);
    el.classList.toggle("text-slate-400", !warning);
    window.clearTimeout(toast.timer);
    if (text) toast.timer = window.setTimeout(function () { el.textContent = ""; }, 9000);
  }

  function setBusy(on) {
    state.busy = !!on;
    ["cp-refresh", "cp-save-keys", "cp-clear-keys", "cp-install", "cp-wire", "cp-unwire"].forEach(function (id) {
      var el = $(id);
      if (el) el.disabled = !!on;
    });
  }

  function copyText(value, label) {
    var done = function () { toast((label || "Copied") + " to clipboard"); };
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(value).then(done).catch(function () { fallbackCopy(value, done); });
      return;
    }
    fallbackCopy(value, done);
  }

  function fallbackCopy(value, done) {
    var area = document.createElement("textarea");
    area.value = value || "";
    area.setAttribute("readonly", "readonly");
    area.style.position = "fixed";
    area.style.opacity = "0";
    document.body.appendChild(area);
    area.select();
    try { document.execCommand("copy"); done(); } catch (error) { toast("Could not copy", true); }
    document.body.removeChild(area);
  }

  function showPluginTab(name) {
    state.tab = name === "composio" ? "composio" : "megalist";
    var mg = $("mg-workspace");
    var cp = $("composio-workspace");
    if (mg) mg.classList.toggle("hidden", state.tab !== "megalist");
    if (cp) cp.classList.toggle("hidden", state.tab !== "composio");
    var mega = $("plugins-tab-megalist");
    var composio = $("plugins-tab-composio");
    if (mega) mega.classList.toggle("plugins-tab-active", state.tab === "megalist");
    if (composio) composio.classList.toggle("plugins-tab-active", state.tab === "composio");
    if (state.tab === "composio") load();
    icons();
  }

  function showView(name) {
    var allowed = { overview: true, toolkits: true, connections: true, mcp: true, setup: true };
    state.view = allowed[name] ? name : "overview";
    Array.prototype.forEach.call(document.querySelectorAll("[data-composio-panel]"), function (panel) {
      panel.classList.toggle("hidden", panel.getAttribute("data-composio-panel") !== state.view);
    });
    Array.prototype.forEach.call(document.querySelectorAll("[data-composio-view]"), function (button) {
      button.classList.toggle("composio-nav-active", button.getAttribute("data-composio-view") === state.view);
    });
    if (state.view === "mcp") renderMcp();
    icons();
  }

  function load() {
    if (state.loading) return;
    state.loading = true;
    api("/api/composio/overview").then(function (result) {
      if (!result.ok) {
        toast((result.data && result.data.error) || "Could not load Composio", true);
        return;
      }
      applyOverview(result.data);
    }).catch(function (error) {
      toast("Could not load Composio: " + error.message, true);
    }).then(function () {
      state.loading = false;
    });
  }

  function applyOverview(data) {
    state.overview = data || {};
    state.toolkits = (state.overview.catalog && state.overview.catalog.toolkits) || [];
    renderHeader();
    renderOverview();
    renderToolkits();
    renderConnections();
    renderClients();
    renderRuntime();
    renderMcp();
    if (state.selected) {
      var exists = state.toolkits.some(function (item) { return item.slug === state.selected; });
      if (exists) loadToolkit(state.selected, true);
      else { state.selected = ""; state.detail = null; renderToolkitDetail(); }
    }
    icons();
  }

  function renderHeader() {
    var data = state.overview || {};
    var status = data.status || {};
    var catalog = data.catalog || {};
    var clients = data.clients || [];
    var connections = data.connections || [];
    var plugin = data.plugin || {};
    var runtime = data.runtime || {};
    var hasKey = !!(status.consumer_key_set || status.api_key_set);
    var wired = clients.filter(function (item) { return item.wired; }).length;
    var runtimeReady = runtime.cli && runtime.cli.installed ? "CLI ready" : "CLI not installed";

    if ($("cp-key-text")) $("cp-key-text").textContent = hasKey ? "Credentials configured" : "No project key";
    if ($("cp-key-pill")) {
      $("cp-key-pill").classList.toggle("sb-pill-ok", hasKey);
      $("cp-key-pill").classList.toggle("sb-pill-warn", !hasKey);
    }
    if ($("cp-catalog-text")) $("cp-catalog-text").textContent = (catalog.source || "seed") + " catalog · " + Number(catalog.count || 0).toLocaleString() + " toolkits";
    if ($("cp-catalog-pill")) {
      $("cp-catalog-pill").classList.toggle("sb-pill-ok", catalog.source === "live");
      $("cp-catalog-pill").classList.toggle("sb-pill-warn", catalog.source !== "live");
    }
    if ($("cp-checked-at")) $("cp-checked-at").textContent = data.checked_at ? "checked " + data.checked_at.replace("T", " ") : "";
    if ($("cp-summary-toolkits")) $("cp-summary-toolkits").textContent = Number(catalog.count || 0).toLocaleString();
    if ($("cp-summary-source")) $("cp-summary-source").textContent = (catalog.source || "seed") + " catalog";
    if ($("cp-summary-connections")) $("cp-summary-connections").textContent = connections.length;
    if ($("cp-summary-clients")) $("cp-summary-clients").textContent = wired + " / " + clients.length;
    if ($("cp-summary-client-detail")) $("cp-summary-client-detail").textContent = wired ? wired + " wired" : "Detected locally";
    if ($("cp-summary-plugin")) $("cp-summary-plugin").textContent = plugin.installed ? "Installed" : "Not installed";
    if ($("cp-summary-runtime")) $("cp-summary-runtime").textContent = runtimeReady;
    if ($("cp-count")) $("cp-count").textContent = Number(catalog.count || 0).toLocaleString();
    if ($("cp-nav-toolkit-count")) $("cp-nav-toolkit-count").textContent = Number(catalog.count || 0).toLocaleString();
    if ($("cp-nav-connection-count")) $("cp-nav-connection-count").textContent = connections.length;
    if ($("cp-connections-count")) $("cp-connections-count").textContent = connections.length;
    if ($("cp-wire-state")) $("cp-wire-state").textContent = wired ? wired + " wired" : "not wired";
    if ($("cp-cred-state")) $("cp-cred-state").textContent = status.updated_at ? "saved " + status.updated_at.replace("T", " ") : "";
    var pluginState = $("cp-plugin-state");
    if (pluginState) pluginState.textContent = plugin.installed ? (plugin.name || "composio") + " v" + (plugin.version || "?") + (plugin.enabled ? " · enabled" : " · disabled") : "Plugin pack is not installed.";
  }

  function renderOverview() {
    var warning = $("cp-overview-warning");
    var text = state.overview && state.overview.warning;
    if (warning) {
      warning.textContent = text || "";
      warning.classList.toggle("hidden", !text);
    }
    var host = $("cp-meta-tools");
    if (!host) return;
    var tools = (state.overview && state.overview.meta_tools) || [];
    host.innerHTML = tools.length ? tools.map(function (tool) {
      return '<span class="composio-chip" title="' + esc(tool.description) + '">' + esc(tool.slug) + '</span>';
    }).join("") : '<span class="sb-rail-empty">No meta tools returned.</span>';
  }

  function filteredToolkits() {
    var query = state.query.trim().toLowerCase();
    if (!query) return state.toolkits;
    return state.toolkits.filter(function (item) {
      return [item.name, item.slug, item.category, item.description].join(" ").toLowerCase().indexOf(query) >= 0;
    });
  }

  function renderToolkits() {
    var host = $("cp-list");
    if (!host) return;
    var items = filteredToolkits();
    if (!items.length) {
      host.innerHTML = '<div class="sb-rail-empty">No toolkit matches that search.</div>';
      return;
    }
    host.innerHTML = items.slice(0, 500).map(function (item) {
      var active = item.slug === state.selected ? " composio-item-active" : "";
      var count = item.tools_count ? Number(item.tools_count).toLocaleString() + " tools" : (item.category || "Toolkit");
      return '<button type="button" class="composio-item' + active + '" data-composio-slug="' + esc(item.slug) + '"><span>' + esc(item.name || item.slug) + '</span><small>' + esc(item.slug) + ' · ' + esc(count) + '</small></button>';
    }).join("");
  }

  function renderToolkitDetail() {
    var host = $("cp-detail");
    if (!host) return;
    if (!state.selected) {
      host.innerHTML = '<div class="sb-rail-empty">Select a toolkit to inspect its tools.</div>';
      return;
    }
    var item = state.toolkits.filter(function (entry) { return entry.slug === state.selected; })[0] || { slug: state.selected, name: state.selected };
    var detail = state.detail;
    var loading = state.detailLoading || !detail || (detail.toolkit && detail.toolkit.slug !== state.selected);
    var html = '<div class="composio-detail-head"><div><h4>' + esc(item.name || item.slug) + '</h4><small><code>' + esc(item.slug) + '</code>' + (item.category ? ' · ' + esc(item.category) : '') + (item.tools_count ? ' · ' + Number(item.tools_count).toLocaleString() + ' tools' : '') + '</small></div><a href="' + esc(item.docs || "https://docs.composio.dev/toolkits/" + state.selected) + '" target="_blank" rel="noopener noreferrer" class="sb-btn sb-btn-ghost"><i data-lucide="external-link" class="w-3.5 h-3.5"></i><span>Docs</span></a></div><p class="composio-muted">' + esc(item.description || "No description in this catalog source.") + '</p>';
    if (loading) {
      host.innerHTML = html + '<div class="sb-rail-empty">Loading tools…</div>';
      return;
    }
    if (detail.warning) html += '<div class="composio-warning">' + esc(detail.warning) + '</div>';
    if (detail.connections && detail.connections.length) html += '<div class="composio-section-title"><span>Connections</span></div><div class="composio-chip-list">' + detail.connections.map(function (connection) { return '<span class="composio-chip composio-chip-ok">' + esc(connection.status || "connected") + '</span>'; }).join("") + '</div>';
    var tools = detail.tools || [];
    html += '<div class="composio-section-title"><span>Tools</span><small>' + tools.length + '</small></div>';
    html += tools.length ? '<div class="composio-tool-list">' + tools.slice(0, 120).map(function (tool) { return '<div class="composio-tool"><code>' + esc(tool.slug) + '</code><p>' + esc(tool.description || tool.name || "") + '</p></div>'; }).join("") + '</div>' : '<div class="sb-rail-empty">No tools returned for this toolkit.</div>';
    host.innerHTML = html;
    icons();
  }

  function loadToolkit(slug, quiet) {
    state.selected = slug;
    state.detailLoading = true;
    if (!quiet) { state.detail = null; renderToolkits(); }
    renderToolkitDetail();
    api("/api/composio/toolkit?slug=" + encodeURIComponent(slug)).then(function (result) {
      if (!result.ok) {
        state.detail = { toolkit: { slug: slug }, tools: [], warning: (result.data && result.data.error) || "Could not load this toolkit." };
        toast((result.data && result.data.error) || "Could not load that toolkit", true);
        return;
      }
      if (state.selected === slug) state.detail = result.data;
    }).catch(function (error) {
      state.detail = { toolkit: { slug: slug }, tools: [], warning: error.message };
    }).then(function () {
      state.detailLoading = false;
      if (state.selected === slug) renderToolkitDetail();
    });
  }

  function renderConnections() {
    var host = $("cp-connections");
    if (!host) return;
    var connections = (state.overview && state.overview.connections) || [];
    if (!connections.length) {
      host.innerHTML = '<div class="sb-rail-empty">No connected accounts returned. Add a project key or connect an account in the native dashboard.</div>';
      return;
    }
    host.innerHTML = connections.map(function (connection) {
      return '<div class="composio-connection"><div class="composio-connection-icon"><i data-lucide="link-2" class="w-4 h-4"></i></div><div class="min-w-0"><strong>' + esc(connection.toolkit || "Unknown toolkit") + '</strong><small>' + esc(connection.id || "No connection ID") + '</small></div><span class="composio-status">' + esc(connection.status || "connected") + '</span><time>' + esc(connection.created_at || "") + '</time></div>';
    }).join("");
    icons();
  }

  function selectedClients() {
    return Array.prototype.slice.call(document.querySelectorAll(".composio-client-check:checked")).map(function (input) { return input.value; });
  }

  function renderClients() {
    var host = $("cp-clients");
    var select = $("cp-client-select");
    if (!host) return;
    var clients = (state.overview && state.overview.clients) || [];
    host.innerHTML = clients.length ? clients.map(function (client) {
      var checked = client.present || client.wired ? " checked" : "";
      var badge = client.wired ? '<span class="composio-status composio-status-ok">wired</span>' : (client.present ? '<span class="composio-status">detected</span>' : '<span class="composio-status">absent</span>');
      return '<label class="composio-client"><input type="checkbox" class="composio-client-check" value="' + esc(client.id) + '"' + checked + '><span><strong>' + esc(client.label) + '</strong><small>' + esc(client.path) + '</small></span>' + badge + '</label>';
    }).join("") : '<div class="sb-rail-empty">No supported MCP clients detected.</div>';
    if (select) {
      var current = state.client;
      select.innerHTML = '<option value="generic">Generic JSON</option>' + clients.map(function (client) { return '<option value="' + esc(client.id) + '">' + esc(client.label) + '</option>'; }).join("");
      select.value = clients.some(function (client) { return client.id === current; }) ? current : "generic";
    }
  }

  function renderMcp() {
    var select = $("cp-client-select");
    if (!select) return;
    state.client = select.value || state.client || "generic";
    api("/api/composio/mcp?client=" + encodeURIComponent(state.client)).then(function (result) {
      var code = result.ok && result.data ? result.data.snippet : ((result.data && result.data.error) || "Could not load MCP configuration.");
      if ($("cp-mcp-snippet")) $("cp-mcp-snippet").textContent = code || "";
    }).catch(function (error) {
      if ($("cp-mcp-snippet")) $("cp-mcp-snippet").textContent = error.message;
    });
  }

  function runtimeLine(label, item, command) {
    var installed = item && item.installed;
    return '<div class="composio-runtime-row"><span>' + esc(label) + '</span><b class="' + (installed ? 'composio-status-ok' : '') + '">' + (installed ? 'installed' : 'not installed') + (item && item.version ? ' · ' + esc(item.version) : '') + '</b><button type="button" class="composio-copy" data-copy="' + esc(command) + '" title="Copy command"><i data-lucide="copy" class="w-3 h-3"></i></button></div><code class="composio-runtime-cmd">' + esc(command) + '</code>';
  }

  function renderRuntime() {
    var host = $("cp-runtime");
    if (!host) return;
    var runtime = (state.overview && state.overview.runtime) || {};
    var install = runtime.install || {};
    host.innerHTML = runtimeLine("CLI", runtime.cli, install.cli || "curl -fsSL https://composio.dev/install | sh") + runtimeLine("Python SDK", runtime.sdk, install.sdk || "python3 -m pip install composio");
    icons();
  }

  function refreshCatalog() {
    if (state.busy) return;
    setBusy(true);
    toast("Refreshing the Composio catalog…");
    api("/api/composio/refresh", { method: "POST" }).then(function (result) {
      if (!result.ok) { toast((result.data && result.data.error) || "Refresh failed", true); return; }
      return api("/api/composio/overview").then(function (overview) { if (overview.ok) applyOverview(overview.data); });
    }).catch(function (error) { toast("Refresh failed: " + error.message, true); }).then(function () { setBusy(false); });
  }

  function saveKeys() {
    if (state.busy) return;
    var consumer = ($("cp-consumer") || {}).value || "";
    var apiKey = ($("cp-api") || {}).value || "";
    if (!consumer.trim() && !apiKey.trim()) { toast("Paste a consumer key or project API key first.", true); return; }
    setBusy(true);
    api("/api/composio/keys", { method: "POST", body: { consumer_key: consumer, api_key: apiKey } }).then(function (result) {
      if (!result.ok) { toast((result.data && result.data.error) || "Could not save the keys", true); return; }
      if ($("cp-consumer")) $("cp-consumer").value = "";
      if ($("cp-api")) $("cp-api").value = "";
      toast("Keys saved locally. Reloading the catalog…");
      return api("/api/composio/refresh", { method: "POST" }).then(function () { return api("/api/composio/overview"); }).then(function (overview) { if (overview.ok) applyOverview(overview.data); });
    }).catch(function (error) { toast("Could not save the keys: " + error.message, true); }).then(function () { setBusy(false); });
  }

  function clearKeys() {
    if (state.busy) return;
    setBusy(true);
    api("/api/composio/clear-keys", { method: "POST" }).then(function (result) {
      if (!result.ok) { toast((result.data && result.data.error) || "Could not clear the keys", true); return; }
      toast("Stored keys cleared.");
      return api("/api/composio/overview").then(function (overview) { if (overview.ok) applyOverview(overview.data); });
    }).catch(function (error) { toast("Could not clear the keys: " + error.message, true); }).then(function () { setBusy(false); });
  }

  function installPlugin() {
    if (state.busy) return;
    setBusy(true);
    toast("Installing the Composio plugin pack…");
    api("/api/composio/install", { method: "POST", body: {} }).then(function (result) {
      if (!result.ok) { toast((result.data && result.data.error) || "Install failed", true); return; }
      var plugin = result.data && result.data.plugin || {};
      toast("Installed " + (plugin.name || "composio") + " v" + (plugin.version || "?") + ".");
      return api("/api/composio/overview").then(function (overview) { if (overview.ok) applyOverview(overview.data); });
    }).catch(function (error) { toast("Install failed: " + error.message, true); }).then(function () { setBusy(false); });
  }

  function wire(remove) {
    if (state.busy) return;
    var clients = selectedClients();
    if (!clients.length) { toast("Select at least one MCP client.", true); return; }
    setBusy(true);
    toast(remove ? "Removing Composio from selected clients…" : "Wiring Composio MCP…");
    api("/api/composio/wire", { method: "POST", body: { clients: clients, remove: !!remove } }).then(function (result) {
      if (!result.ok) { toast((result.data && result.data.error) || "MCP wiring failed", true); return; }
      var items = result.data && result.data.results || [];
      var failed = items.some(function (item) { return !item.ok; });
      toast(items.map(function (item) { return item.client + ": " + (item.action || item.error || "done"); }).join(" · ") || "Done", failed);
      return api("/api/composio/overview").then(function (overview) { if (overview.ok) applyOverview(overview.data); });
    }).catch(function (error) { toast("MCP wiring failed: " + error.message, true); }).then(function () { setBusy(false); });
  }

  function init() {
    if (!$("plugins-view") || $("composio-workspace") == null || state.initialized) return;
    state.initialized = true;
    var megaTab = $("plugins-tab-megalist");
    var composioTab = $("plugins-tab-composio");
    if (megaTab) megaTab.addEventListener("click", function () { showPluginTab("megalist"); });
    if (composioTab) composioTab.addEventListener("click", function () { showPluginTab("composio"); });
    Array.prototype.forEach.call(document.querySelectorAll("[data-composio-view]"), function (button) {
      button.addEventListener("click", function () { showView(button.getAttribute("data-composio-view")); });
    });
    if ($("cp-search")) $("cp-search").addEventListener("input", function () { state.query = $("cp-search").value || ""; renderToolkits(); });
    if ($("cp-list")) $("cp-list").addEventListener("click", function (event) { var item = event.target.closest("[data-composio-slug]"); if (item) loadToolkit(item.getAttribute("data-composio-slug")); });
    if ($("cp-client-select")) $("cp-client-select").addEventListener("change", function () { state.client = this.value; renderMcp(); });
    if ($("cp-copy-mcp")) $("cp-copy-mcp").addEventListener("click", function () { copyText(($('cp-mcp-snippet') || {}).textContent || "", "MCP configuration"); });
    if ($("cp-refresh")) $("cp-refresh").addEventListener("click", refreshCatalog);
    if ($("cp-save-keys")) $("cp-save-keys").addEventListener("click", saveKeys);
    if ($("cp-clear-keys")) $("cp-clear-keys").addEventListener("click", clearKeys);
    if ($("cp-install")) $("cp-install").addEventListener("click", installPlugin);
    if ($("cp-wire")) $("cp-wire").addEventListener("click", function () { wire(false); });
    if ($("cp-unwire")) $("cp-unwire").addEventListener("click", function () { wire(true); });
    ["cp-consumer", "cp-api"].forEach(function (id) { if ($(id)) $(id).addEventListener("keydown", function (event) { if (event.key === "Enter") saveKeys(); }); });
    ["cp-runtime"].forEach(function (id) { if ($(id)) $(id).addEventListener("click", function (event) { var button = event.target.closest("[data-copy]"); if (button) copyText(button.getAttribute("data-copy"), "Command"); }); });
    showView("overview");
    icons();
  }

  function refresh() {
    if (!state.initialized) { init(); return; }
    if (state.tab === "composio") load();
  }

  function open() {
    if (window.switchMainView) window.switchMainView("plugins");
    showPluginTab("composio");
  }

  window.Composio = { init: init, refresh: refresh, open: open, showView: showView, showPluginTab: showPluginTab };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
