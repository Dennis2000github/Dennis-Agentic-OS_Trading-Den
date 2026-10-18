// DENNIS Agentic OS - MCP Connector Hub (discovery + custom + policy/OAuth workflow)
(function () {
  "use strict";

  var api = {
    token: null,
    servers: [],
    presets: {},
    registry: "https://registry.modelcontextprotocol.io",
    current: null,
    step: 1,
    results: []
  };

  function $(id) { return document.getElementById(id); }

  function esc(text) {
    return String(text == null ? "" : text).replace(/[&<>"']/g, function (m) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;" }[m];
    });
  }

  function icons() { if (window.lucide) window.lucide.createIcons(); }

  function toast(msg, kind) {
    if (window.showToast) { window.showToast(msg); return; }
    console.log("[mcp]", msg);
  }

  /* ------------------------------------------------------------------ */
  /* Transport                                                          */
  /* ------------------------------------------------------------------ */

  function ensureToken() {
    if (api.token) return Promise.resolve(api.token);
    return fetch("/api/auth/session")
      .then(function (r) { return r.ok ? r.json() : {}; })
      .then(function (d) { api.token = d.token || null; return api.token; })
      .catch(function () { return null; });
  }

  function rest(path, method, body) {
    return ensureToken().then(function (token) {
      var headers = {};
      if (token) headers["X-Dennis-Token"] = token;
      var opts = { method: method || "GET", headers: headers };
      if (body !== undefined) {
        headers["Content-Type"] = "application/json";
        opts.body = JSON.stringify(body);
      }
      return fetch(path, opts).then(function (res) {
        return res.json().catch(function () { return {}; }).then(function (data) {
          if (!res.ok) throw new Error(data.error || ("Request failed (" + res.status + ")"));
          return data;
        });
      });
    });
  }

  /* ------------------------------------------------------------------ */
  /* Server state                                                       */
  /* ------------------------------------------------------------------ */

  function loadServers() {
    return rest("/api/mcp/servers").then(function (data) {
      api.servers = data.servers || [];
      api.presets = data.presets || {};
      api.registry = data.registry || api.registry;
      renderPanel();
      renderConnected();
      return api.servers;
    }).catch(function (err) {
      var list = $("mcp-panel-list");
      if (list) {
        list.innerHTML = '<div class="text-[10.5px] text-rose-300 px-1 py-2">' + esc(err.message) + "</div>";
      }
      throw err;
    });
  }

  function statusMeta(status) {
    switch (status) {
      case "connected": return { cls: "mcp-badge-connected", label: "Connected" };
      case "authorizing": return { cls: "mcp-badge-authorizing", label: "Awaiting auth" };
      case "error": return { cls: "mcp-badge-error", label: "Error" };
      default: return { cls: "mcp-badge-review", label: "Policy review" };
    }
  }

  function initial(name) {
    return esc((name || "?").trim().charAt(0).toUpperCase() || "?");
  }

  function renderPanel() {
    var list = $("mcp-panel-list");
    var count = $("mcp-panel-count");
    var connected = api.servers.filter(function (s) { return s.status === "connected"; }).length;
    if (count) count.textContent = connected + " active";
    if (!list) return;
    if (!api.servers.length) {
      list.innerHTML = '<div class="text-[10.5px] text-slate-500 px-1 py-2 leading-relaxed">No connectors yet. Search the MCP registry or add a custom server to begin.</div>';
      return;
    }
    list.innerHTML = api.servers.slice(0, 6).map(function (s) {
      var meta = statusMeta(s.status);
      return '<button class="mcp-panel-row w-full flex items-center justify-between py-1 border-b border-slate-800/50 text-left hover:bg-slate-800/30 rounded px-0.5 transition" data-mcp-open="' + esc(s.id) + '">' +
        '<div class="flex items-center gap-2 min-w-0">' +
          '<div class="w-5 h-5 rounded bg-fuchsia-500/20 flex items-center justify-center text-fuchsia-300 text-[10px] font-bold shrink-0">' + initial(s.name) + "</div>" +
          '<div class="min-w-0"><div class="font-medium text-slate-200 text-[11px] truncate">' + esc(s.title || s.name) + "</div>" +
          '<div class="text-[9px] text-slate-500 truncate">' + esc(s.transport || "mcp") + " · " + esc(s.auth_type || "none") + "</div></div>" +
        "</div>" +
        '<span class="mcp-badge ' + meta.cls + '">' + meta.label + "</span>" +
      "</button>";
    }).join("");
  }

  function renderConnected() {
    var host = $("mcp-connected-list");
    var badge = $("mcp-connected-badge");
    if (badge) badge.textContent = api.servers.length;
    if (!host) return;
    if (!api.servers.length) {
      host.innerHTML = '<div class="flex flex-col items-center justify-center py-12 text-slate-500 text-xs gap-2">' +
        '<i data-lucide="plug-zap" class="w-6 h-6 text-slate-600"></i>' +
        "<span>No MCP connectors registered yet.</span>" +
        '<span class="text-[10.5px] text-slate-600">Use "Search Registry" or "Custom Connector".</span></div>';
      icons();
      return;
    }
    host.innerHTML = api.servers.map(function (s) {
      var meta = statusMeta(s.status);
      var policy = s.policy || {};
      var endpoint = s.transport === "stdio"
        ? (s.command || "stdio") + " " + ((s.scopes || []).length ? "" : "")
        : (s.url || "-");
      var actions = "";
      if (s.status === "connected") {
        if (s.has_token) actions += '<button data-mcp-action="refresh" data-mcp-id="' + esc(s.id) + '" class="px-2.5 py-1 rounded-lg bg-slate-800 hover:bg-slate-700 text-[10.5px] text-slate-200">Refresh token</button> ';
      } else if (s.auth_type === "oauth") {
        actions += '<button data-mcp-action="authorize" data-mcp-id="' + esc(s.id) + '" class="px-2.5 py-1 rounded-lg bg-fuchsia-700 hover:bg-fuchsia-600 text-[10.5px] text-white">Authorize</button> ';
      } else {
        actions += '<button data-mcp-action="connect" data-mcp-id="' + esc(s.id) + '" class="px-2.5 py-1 rounded-lg bg-emerald-700 hover:bg-emerald-600 text-[10.5px] text-white">Connect</button> ';
      }
      actions += '<button data-mcp-action="policy" data-mcp-id="' + esc(s.id) + '" class="px-2.5 py-1 rounded-lg bg-slate-800 hover:bg-slate-700 text-[10.5px] text-slate-200">Policy</button> ';
      actions += '<button data-mcp-action="remove" data-mcp-id="' + esc(s.id) + '" class="px-2.5 py-1 rounded-lg bg-rose-950/60 hover:bg-rose-900 text-[10.5px] text-rose-300">Remove</button>';
      return '<div class="mcp-card">' +
        '<div class="flex items-start justify-between gap-3">' +
          '<div class="flex items-start gap-2.5 min-w-0">' +
            '<div class="w-8 h-8 rounded-lg bg-fuchsia-500/20 flex items-center justify-center text-fuchsia-300 text-xs font-bold shrink-0">' + initial(s.name) + "</div>" +
            '<div class="min-w-0">' +
              '<div class="flex items-center gap-2"><span class="font-semibold text-white text-xs truncate">' + esc(s.title || s.name) + "</span>" +
              '<span class="mcp-badge ' + meta.cls + '">' + meta.label + "</span></div>" +
              '<div class="text-[10.5px] text-slate-400 truncate">' + esc(s.description || s.id) + "</div>" +
              '<div class="text-[10px] text-slate-500 font-mono truncate mt-0.5">' + esc(s.transport) + " · " + esc(endpoint) + "</div>" +
            "</div>" +
          "</div>" +
        "</div>" +
        '<div class="flex flex-wrap items-center gap-2 mt-2.5 pt-2.5 border-t border-slate-800/60">' +
          '<span class="text-[10px] text-slate-500 font-mono">risk: ' + esc(policy.risk_level || "medium") + " · " + esc(policy.preset || "restricted") +
          (policy.allow_write ? ' · <span class="text-amber-400">write</span>' : "") + "</span>" +
          '<span class="ml-auto flex gap-1.5">' + actions + "</span>" +
        "</div>" +
        (s.last_error ? '<div class="text-[10px] text-amber-300/80 mt-1.5">' + esc(s.last_error) + "</div>" : "") +
      "</div>";
    }).join("");
    if (window.lucide) window.lucide.createIcons();
  }

  /* ------------------------------------------------------------------ */
  /* Modal + tabs                                                       */
  /* ------------------------------------------------------------------ */

  function openModal(tab) {
    var modal = $("mcp-modal");
    if (!modal) return;
    modal.classList.remove("hidden");
    showHub();
    switchTab(tab || "registry");
    loadServers().catch(function () {});
    icons();
  }

  function closeModal() {
    var modal = $("mcp-modal");
    if (modal) modal.classList.add("hidden");
  }

  function showHub() {
    var hub = $("mcp-hub-view");
    var wf = $("mcp-workflow-view");
    if (hub) hub.classList.remove("hidden");
    if (wf) wf.classList.add("hidden");
  }

  function switchTab(tab) {
    var tabs = { registry: "mcp-tab-registry", custom: "mcp-tab-custom", connected: "mcp-tab-connected" };
    Object.keys(tabs).forEach(function (key) {
      var btn = $(tabs[key]);
      if (!btn) return;
      var cls = ["bg-fuchsia-600", "text-white"];
      var off = ["text-slate-400"];
      if (key === tab) {
        btn.classList.remove.apply(btn.classList, off);
        btn.classList.add.apply(btn.classList, cls);
      } else {
        btn.classList.remove.apply(btn.classList, cls);
        btn.classList.add.apply(btn.classList, off);
      }
    });
    ["mcp-view-registry", "mcp-view-custom", "mcp-view-connected"].forEach(function (id) {
      var el = $(id);
      if (el) el.classList.add("hidden");
    });
    var view = $("mcp-view-" + tab);
    if (view) view.classList.remove("hidden");
    if (tab === "connected") loadServers().catch(function () {});
    icons();
  }

  /* ------------------------------------------------------------------ */
  /* Registry discovery                                                 */
  /* ------------------------------------------------------------------ */

  function runRegistrySearch() {
    var input = $("mcp-registry-search");
    var results = $("mcp-registry-results");
    var source = $("mcp-registry-source");
    var q = input ? input.value.trim() : "";
    if (results) {
      results.innerHTML = '<div class="col-span-2 flex items-center justify-center py-12 text-slate-500 text-xs"><i data-lucide="loader" class="w-4 h-4 animate-spin mr-2"></i> Searching the MCP registry...</div>';
      icons();
    }
    if (source) source.textContent = "";
    rest("/api/mcp/discover?q=" + encodeURIComponent(q) + "&limit=24").then(function (data) {
      api.results = data.results || [];
      renderRegistryResults(data);
    }).catch(function (err) {
      if (results) results.innerHTML = '<div class="col-span-2 text-rose-300 text-xs p-3">' + esc(err.message) + "</div>";
    });
  }

  function renderRegistryResults(data) {
    var results = $("mcp-registry-results");
    var source = $("mcp-registry-source");
    if (!results) return;
    if (source) {
      var label = data.source === "registry" ? "Live results from " + api.registry : "Offline curated catalog";
      source.textContent = label + (data.error ? "  •  " + data.error : "");
    }
    if (!api.results.length) {
      results.innerHTML = '<div class="col-span-2 text-slate-500 text-xs p-3">No servers matched. Try another term or add a custom connector.</div>';
      return;
    }
    results.innerHTML = api.results.map(function (r, i) {
      var added = api.servers.some(function (s) { return s.id === r.id || (s.name && s.name === r.title); });
      return '<div class="mcp-card flex flex-col">' +
        '<div class="flex items-start justify-between gap-2">' +
          '<div class="min-w-0">' +
            '<div class="font-semibold text-white text-xs truncate">' + esc(r.title || r.id) + "</div>" +
            '<div class="text-[9.5px] text-slate-500 font-mono truncate">' + esc(r.id) + (r.version ? " · v" + esc(r.version) : "") + "</div>" +
          "</div>" +
          '<span class="mcp-badge mcp-badge-review shrink-0">' + esc(r.transport || "mcp") + "</span>" +
        "</div>" +
        '<div class="text-[10.5px] text-slate-400 mt-2 flex-1">' + esc(r.description || "") + "</div>" +
        '<div class="flex items-center justify-between gap-2 mt-2.5 pt-2.5 border-t border-slate-800/60">' +
          '<span class="text-[9.5px] text-slate-500 font-mono">auth: ' + esc(r.auth_type || "none") + "</span>" +
          (added
            ? '<span class="text-[10px] text-emerald-400 flex items-center gap-1"><i data-lucide="check" class="w-3 h-3"></i> Added</span>'
            : '<button data-mcp-add="' + i + '" class="px-3 py-1 rounded-lg bg-fuchsia-600 hover:bg-fuchsia-500 text-[10.5px] text-white font-semibold">Connect</button>') +
        "</div>" +
      "</div>";
    }).join("");
    if (window.lucide) window.lucide.createIcons();
  }

  function addFromRegistry(index) {
    var r = api.results[index];
    if (!r) return;
    var payload = {
      id: r.id,
      name: r.title || r.id,
      title: r.title || r.id,
      description: r.description || "",
      transport: r.transport || "streamable-http",
      url: r.url || "",
      command: r.command || "",
      args: r.args || [],
      auth_type: r.auth_type || (r.url ? "oauth" : "none"),
      source: "registry",
      repository: r.repository || "",
      preset: "restricted"
    };
    rest("/api/mcp/servers", "POST", payload).then(function (data) {
      toast("Added " + (data.server.title || data.server.name) + " - review its policy.");
      return loadServers().then(function () { startWorkflow(data.server); });
    }).catch(function (err) { toast("Could not add connector: " + err.message); });
  }

  /* ------------------------------------------------------------------ */
  /* Custom connector                                                   */
  /* ------------------------------------------------------------------ */

  function syncCustomFields() {
    var transport = $("mcp-c-transport");
    var urlField = $("mcp-c-url-field");
    var cmdField = $("mcp-c-command-field");
    var auth = $("mcp-c-auth");
    if (!transport) return;
    var isStdio = transport.value === "stdio";
    if (urlField) urlField.classList.toggle("hidden", isStdio);
    if (cmdField) cmdField.classList.toggle("hidden", !isStdio);
    if (auth && isStdio) auth.value = "api_key";
    if (auth && !isStdio && auth.value === "api_key") auth.value = "oauth";
  }

  function createCustom() {
    var payload = {
      name: ($("mcp-c-name") || {}).value ? $("mcp-c-name").value.trim() : "",
      description: ($("mcp-c-desc") || {}).value ? $("mcp-c-desc").value.trim() : "",
      transport: ($("mcp-c-transport") || {}).value || "streamable-http",
      url: ($("mcp-c-url") || {}).value ? $("mcp-c-url").value.trim() : "",
      command: ($("mcp-c-command") || {}).value ? $("mcp-c-command").value.trim() : "",
      args: ($("mcp-c-args") || {}).value ? $("mcp-c-args").value.trim() : "",
      auth_type: ($("mcp-c-auth") || {}).value || "oauth",
      source: "custom",
      preset: "restricted"
    };
    if (!payload.name) { toast("A connector name is required"); return; }
    rest("/api/mcp/servers", "POST", payload).then(function (data) {
      toast("Connector created - review its policy.");
      return loadServers().then(function () { startWorkflow(data.server); });
    }).catch(function (err) { toast("Could not create connector: " + err.message); });
  }

  /* ------------------------------------------------------------------ */
  /* Policy + OAuth workflow                                            */
  /* ------------------------------------------------------------------ */

  function startWorkflow(server) {
    api.current = server;
    api.step = 1;
    var hub = $("mcp-hub-view");
    var wf = $("mcp-workflow-view");
    if (hub) hub.classList.add("hidden");
    if (wf) wf.classList.remove("hidden");
    fillPolicy(server.policy || {});
    var label = $("mcp-wf-server-label");
    if (label) {
      label.textContent = (server.title || server.name) + "  •  " + server.transport + "  •  " +
        (server.url || server.command || server.id);
    }
    var err = $("mcp-wf-error");
    if (err) err.classList.add("hidden");
    renderStep(1);
    icons();
  }

  function fillPolicy(policy) {
    var preset = policy.preset || "restricted";
    var radios = document.querySelectorAll('input[name="mcp-wf-preset"]');
    Array.prototype.forEach.call(radios, function (r) { r.checked = r.value === preset; });
    if ($("mcp-wf-scopes")) $("mcp-wf-scopes").value = (policy.scopes || ["mcp:tools"]).join(" ");
    if ($("mcp-wf-tools")) $("mcp-wf-tools").value = (policy.tools || []).join(" ");
    if ($("mcp-wf-risk")) $("mcp-wf-risk").value = policy.risk_level || "medium";
    if ($("mcp-wf-data")) $("mcp-wf-data").value = policy.data_classification || "internal";
    if ($("mcp-wf-write")) $("mcp-wf-write").checked = !!policy.allow_write;
    if ($("mcp-wf-network")) $("mcp-wf-network").checked = policy.allow_network !== false;
    if ($("mcp-wf-approval")) $("mcp-wf-approval").checked = policy.require_approval !== false;
    if ($("mcp-wf-accept")) $("mcp-wf-accept").checked = !!policy.accepted;
  }

  function applyPresetDefaults(preset) {
    var map = api.presets[preset];
    if (!map) return;
    if ($("mcp-wf-write")) $("mcp-wf-write").checked = !!map.allow_write;
    if ($("mcp-wf-network")) $("mcp-wf-network").checked = map.allow_network !== false;
    if ($("mcp-wf-approval")) $("mcp-wf-approval").checked = map.require_approval !== false;
    if ($("mcp-wf-risk")) $("mcp-wf-risk").value = map.risk_level || "medium";
    if ($("mcp-wf-data")) $("mcp-wf-data").value = map.data_classification || "internal";
  }

  function collectPolicy() {
    var preset = "restricted";
    var radios = document.querySelectorAll('input[name="mcp-wf-preset"]');
    Array.prototype.forEach.call(radios, function (r) { if (r.checked) preset = r.value; });
    return {
      preset: preset,
      scopes: (($("mcp-wf-scopes") || {}).value || "").split(/\s+/).filter(Boolean),
      tools: (($("mcp-wf-tools") || {}).value || "").split(/[\s,]+/).filter(Boolean),
      allow_write: !!($("mcp-wf-write") || {}).checked,
      allow_network: !!($("mcp-wf-network") || {}).checked,
      require_approval: !!($("mcp-wf-approval") || {}).checked,
      risk_level: ($("mcp-wf-risk") || {}).value || "medium",
      data_classification: ($("mcp-wf-data") || {}).value || "internal",
      accepted: !!($("mcp-wf-accept") || {}).checked
    };
  }

  function renderStep(n) {
    api.step = n;
    [1, 2, 3].forEach(function (i) {
      var step = $("mcp-wf-step-" + i);
      if (step) {
        step.classList.toggle("active", i === n);
        if (i !== n) step.classList.add("hidden");
        else step.classList.remove("hidden");
      }
      var badge = $("mcp-wf-badge-" + i);
      if (badge) {
        badge.classList.remove("active", "completed");
        if (i < n) badge.classList.add("completed");
        else if (i === n) badge.classList.add("active");
      }
    });
    var back = $("mcp-wf-back");
    if (back) back.classList.toggle("hidden", n === 1);
    var next = $("mcp-wf-next");
    if (next) {
      if (n === 1) {
        next.classList.remove("hidden");
        next.innerHTML = '<span>Accept Policy &amp; Continue</span><i data-lucide="arrow-right" class="w-3.5 h-3.5"></i>';
      } else {
        next.classList.add("hidden");
      }
    }
    var finish = $("mcp-wf-finish");
    if (finish) finish.classList.toggle("hidden", n !== 3);
    icons();
  }

  function showWfError(msg) {
    var el = $("mcp-wf-error");
    if (!el) return;
    el.textContent = msg;
    el.classList.remove("hidden");
  }

  function hideWfError() {
    var el = $("mcp-wf-error");
    if (el) el.classList.add("hidden");
  }

  function submitPolicy() {
    var policy = collectPolicy();
    if (!policy.accepted) { showWfError("You must accept the policy to continue."); return; }
    if (!api.current || !api.current.id) { showWfError("No connector selected."); return; }
    hideWfError();
    rest("/api/mcp/servers/" + encodeURIComponent(api.current.id) + "/policy", "POST", { policy: policy })
      .then(function (data) {
        api.current = data.server;
        configureStep2();
        renderStep(2);
      })
      .catch(function (err) { showWfError(err.message); });
  }

  function configureStep2() {
    var server = api.current || {};
    var oauth = $("mcp-wf-oauth");
    var direct = $("mcp-wf-direct");
    var isOauth = server.auth_type === "oauth" && server.transport !== "stdio";
    if (oauth) oauth.classList.toggle("hidden", !isOauth);
    if (direct) direct.classList.toggle("hidden", isOauth);
    var urlbox = $("mcp-wf-urlbox");
    var codebox = $("mcp-wf-codebox");
    var clientBox = $("mcp-wf-clientid-wrap");
    if (urlbox) urlbox.classList.add("hidden");
    if (codebox) codebox.classList.add("hidden");
    if (clientBox) clientBox.classList.add("hidden");
    var status = $("mcp-wf-auth-status");
    if (status) status.textContent = "";
    if (isOauth) {
      runAuthorize(true);
    } else {
      var desc = $("mcp-wf-direct-desc");
      if (desc) {
        desc.textContent = server.transport === "stdio"
          ? "stdio connector: provide its credentials as environment variables on the host (setenv before launch), then connect."
          : "This connector uses a static credential. Store it on the host / gateway, then connect.";
      }
    }
  }

  function runAuthorize(silent) {
    var server = api.current || {};
    if (!server.id) return;
    var status = $("mcp-wf-auth-status");
    if (status) status.textContent = "Discovering authorization servers...";
    if (!silent) hideWfError();
    rest("/api/mcp/servers/" + encodeURIComponent(server.id) + "/authorize", "POST", {})
      .then(function (data) {
        api.current = data.server;
        var urlbox = $("mcp-wf-urlbox");
        var codebox = $("mcp-wf-codebox");
        var clientBox = $("mcp-wf-clientid-wrap");
        var urlInput = $("mcp-wf-url");
        if (urlInput) urlInput.value = data.authorization_url || "";
        if (clientBox) clientBox.classList.toggle("hidden", !data.needs_client_id);
        if (data.needs_client_id) {
          if (status) status.textContent = data.note || "Provide a client id to continue.";
          if (urlbox) urlbox.classList.add("hidden");
          if (codebox) codebox.classList.add("hidden");
          return;
        }
        if (status) status.textContent = data.note || ("Authorizing via " + (data.issuer || "provider"));
        if (urlbox) urlbox.classList.remove("hidden");
        if (codebox) codebox.classList.remove("hidden");
        if (data.authorization_url && !silent) openUrl(data.authorization_url);
        api.pendingState = data.state || "";
      })
      .catch(function (err) {
        if (status) status.textContent = "";
        showWfError(err.message);
      });
  }

  function openUrl(url) {
    try { window.open(url, "_blank", "noopener,noreferrer"); }
    catch (e) { /* popup blocked */ }
  }

  function completeOAuth() {
    var server = api.current || {};
    var code = ($("mcp-wf-code") || {}).value || "";
    if (!code.trim()) { showWfError("Paste the authorization code or the redirected URL."); return; }
    hideWfError();
    rest("/api/mcp/servers/" + encodeURIComponent(server.id) + "/callback", "POST", { code: code.trim() })
      .then(function (data) {
        api.current = data.server;
        toast("Connector authorized.");
        finishStep(data.server);
      })
      .catch(function (err) { showWfError(err.message); });
  }

  function connectDirect() {
    var server = api.current || {};
    rest("/api/mcp/servers/" + encodeURIComponent(server.id) + "/connect", "POST", {})
      .then(function (data) {
        api.current = data.server;
        toast("Connector connected.");
        finishStep(data.server);
      })
      .catch(function (err) { showWfError(err.message); });
  }

  function finishStep(server) {
    if ($("mcp-wf-done-name")) $("mcp-wf-done-name").textContent = server.title || server.name;
    if ($("mcp-wf-done-transport")) $("mcp-wf-done-transport").textContent = server.transport;
    if ($("mcp-wf-done-status")) $("mcp-wf-done-status").textContent = server.status;
    if ($("mcp-wf-done-endpoint")) $("mcp-wf-done-endpoint").textContent = server.url || server.command || server.id;
    renderStep(3);
    loadServers().catch(function () {});
  }

  function handleServerAction(action, id) {
    var server = api.servers.filter(function (s) { return s.id === id; })[0];
    if (!server) return;
    if (action === "remove") {
      if (!window.confirm("Remove connector '" + (server.title || server.name) + "' and revoke its tokens?")) return;
      rest("/api/mcp/servers/" + encodeURIComponent(id), "DELETE")
        .then(function () { toast("Connector removed."); return loadServers(); })
        .catch(function (err) { toast(err.message); });
      return;
    }
    if (action === "refresh") {
      rest("/api/mcp/servers/" + encodeURIComponent(id) + "/refresh", "POST", {})
        .then(function () { toast("Token refreshed."); return loadServers(); })
        .catch(function (err) { toast(err.message); });
      return;
    }
    if (action === "policy") { startWorkflow(server); return; }
    if (action === "authorize" || action === "connect") {
      startWorkflow(server);
    }
  }

  /* ------------------------------------------------------------------ */
  /* Wiring                                                             */
  /* ------------------------------------------------------------------ */

  function init() {
    var panelList = $("mcp-panel-list");
    if (panelList) {
      panelList.addEventListener("click", function (e) {
        var row = e.target.closest("[data-mcp-open]");
        if (!row) return;
        var server = api.servers.filter(function (s) { return s.id === row.getAttribute("data-mcp-open"); })[0];
        if (server) { openModal("connected"); startWorkflow(server); }
      });
    }

    if ($("btn-mcp-discover")) $("btn-mcp-discover").addEventListener("click", function () { openModal("registry"); });
    if ($("btn-mcp-custom")) $("btn-mcp-custom").addEventListener("click", function () { openModal("custom"); });
    if ($("mcp-panel-search")) {
      $("mcp-panel-search").addEventListener("keydown", function (e) {
        if (e.key !== "Enter") return;
        e.preventDefault();
        openModal("registry");
        var box = $("mcp-registry-search");
        if (box) box.value = $("mcp-panel-search").value;
        runRegistrySearch();
      });
    }
    if ($("close-mcp-modal")) $("close-mcp-modal").addEventListener("click", closeModal);
    if ($("mcp-modal")) {
      $("mcp-modal").addEventListener("click", function (e) {
        if (e.target === $("mcp-modal")) closeModal();
      });
    }

    if ($("mcp-tab-registry")) $("mcp-tab-registry").addEventListener("click", function () { switchTab("registry"); });
    if ($("mcp-tab-custom")) $("mcp-tab-custom").addEventListener("click", function () { switchTab("custom"); });
    if ($("mcp-tab-connected")) $("mcp-tab-connected").addEventListener("click", function () { switchTab("connected"); });

    if ($("mcp-registry-btn")) $("mcp-registry-btn").addEventListener("click", runRegistrySearch);
    if ($("mcp-registry-search")) {
      $("mcp-registry-search").addEventListener("keydown", function (e) {
        if (e.key === "Enter") { e.preventDefault(); runRegistrySearch(); }
      });
    }
    if ($("mcp-registry-results")) {
      $("mcp-registry-results").addEventListener("click", function (e) {
        var btn = e.target.closest("[data-mcp-add]");
        if (!btn) return;
        addFromRegistry(parseInt(btn.getAttribute("data-mcp-add"), 10));
      });
    }

    if ($("mcp-c-transport")) $("mcp-c-transport").addEventListener("change", syncCustomFields);
    if ($("mcp-c-create")) $("mcp-c-create").addEventListener("click", createCustom);

    if ($("mcp-connected-list")) {
      $("mcp-connected-list").addEventListener("click", function (e) {
        var btn = e.target.closest("[data-mcp-action]");
        if (!btn) return;
        handleServerAction(btn.getAttribute("data-mcp-action"), btn.getAttribute("data-mcp-id"));
      });
    }

    var presetRadios = document.querySelectorAll('input[name="mcp-wf-preset"]');
    Array.prototype.forEach.call(presetRadios, function (r) {
      r.addEventListener("change", function () { applyPresetDefaults(r.value); });
    });

    if ($("mcp-wf-next")) $("mcp-wf-next").addEventListener("click", submitPolicy);
    if ($("mcp-wf-back")) {
      $("mcp-wf-back").addEventListener("click", function () {
        if (api.step > 1) { renderStep(api.step - 1); } else { showHub(); }
      });
    }
    if ($("mcp-wf-cancel")) $("mcp-wf-cancel").addEventListener("click", function () { showHub(); loadServers().catch(function () {}); });
    if ($("mcp-wf-finish")) {
      $("mcp-wf-finish").addEventListener("click", function () {
        showHub();
        switchTab("connected");
        loadServers().catch(function () {});
      });
    }
    if ($("mcp-wf-authorize")) $("mcp-wf-authorize").addEventListener("click", function () { runAuthorize(false); });
    if ($("mcp-wf-open")) {
      $("mcp-wf-open").addEventListener("click", function () {
        var url = ($("mcp-wf-url") || {}).value;
        if (url) openUrl(url);
      });
    }
    if ($("mcp-wf-complete")) $("mcp-wf-complete").addEventListener("click", completeOAuth);
    if ($("mcp-wf-connect")) $("mcp-wf-connect").addEventListener("click", connectDirect);
    if ($("mcp-wf-clientid-save")) {
      $("mcp-wf-clientid-save").addEventListener("click", function () {
        var cid = ($("mcp-wf-clientid") || {}).value || "";
        if (!cid.trim()) { showWfError("Enter a client id."); return; }
        rest("/api/mcp/servers/" + encodeURIComponent(api.current.id) + "/client-id", "POST", { client_id: cid.trim() })
          .then(function (data) {
            api.current = data.server;
            var clientBox = $("mcp-wf-clientid-wrap");
            if (clientBox) clientBox.classList.add("hidden");
            var urlbox = $("mcp-wf-urlbox");
            var codebox = $("mcp-wf-codebox");
            var urlInput = $("mcp-wf-url");
            if (urlInput) urlInput.value = data.authorization_url || "";
            if (urlbox) urlbox.classList.remove("hidden");
            if (codebox) codebox.classList.remove("hidden");
            var status = $("mcp-wf-auth-status");
            if (status) status.textContent = data.note || "";
            api.pendingState = data.state || "";
          })
          .catch(function (err) { showWfError(err.message); });
      });
    }

    window.addEventListener("message", function (event) {
      var data = event.data;
      if (!data || data.type !== "dennis-mcp-oauth") return;
      if (data.error) { showWfError("Provider returned: " + data.error); return; }
      var codeEl = $("mcp-wf-code");
      if (codeEl && data.code) {
        codeEl.value = data.code;
        completeOAuth();
      }
    });

    syncCustomFields();
    loadServers().catch(function () {});
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }

  window.DennisMCP = { open: openModal, refresh: loadServers };
})();
