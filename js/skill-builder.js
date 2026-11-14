/* =====================================================================
   AGENT SKILL BUILDER

   Two modes, one lifecycle:
     Source -> Skill          (YouTube tutorials, GitHub repositories)
     Video Reverse Engineering (tutorial or reference video)

   Every mode converges on: extract -> generate -> editable review ->
   reject / Add to Vault -> verified Obsidian write. Nothing is written to
   the Vault (or installed) before approval.

   Loaded with `defer`; guards on the view existing.
   ===================================================================== */
(function () {
  "use strict";

  var AGENT_FALLBACK = [
    { id: "antigravity", name: "Antigravity", provider: "Google DeepMind", available: true },
    { id: "opencode", name: "OpenCode", provider: "OpenCode", available: true },
    { id: "claude-code", name: "Claude Code", provider: "Anthropic", available: true },
    { id: "codex", name: "Codex CLI", provider: "OpenAI", available: true },
    { id: "hermes-cli", name: "Hermes Agent", provider: "Nous Research", available: true },
    { id: "cursor", name: "Cursor", provider: "Cursor", available: true },
    { id: "openclaw", name: "OpenClaw", provider: "OpenClaw", available: true }
  ];

  var STATE_STAGES = [
    "Validating source",
    "Reference Analysis",
    "Video Reverse Engineering",
    "Video Style",
    "Video Format",
    "Production Technology",
    "Dependencies",
    "Skill Generation",
    "Validation and Replication",
    "Review and Approval",
    "Obsidian Publication",
    "Completed"
  ];

  var state = {
    view: null,
    initialized: false,
    busy: false,
    tab: "transcript",
    panes: { transcript: "", visual: "", skill: "", log: "" },
    agents: [],
    models: [],
    modelsFor: null,
    modelQuery: "",
    providerId: null,
    providerLocked: false,
    providers: [],
    providerSetup: null,
    providerSetupSig: null,
    modelsLoading: false,
    agentId: null,
    modelId: null,
    mode: "source",
    objective: "tutorial",
    reviewTab: "skill",
    jobs: [],
    jobsLoading: false,
    packagePreview: null,
    templates: [],
    templateQuery: "",
    templateCategory: "all",
    jobId: null,
    pollTimer: null,
    elapsedTimer: null,
    startedAt: 0,
    pollErrors: 0,
    draft: null,
    saveTimer: null,
    dirty: false,
    saving: false,
    publishing: false,
    lastSaved: ""
  };

  function $(id) { return document.getElementById(id); }
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function icons() { if (window.lucide && window.lucide.createIcons) { try { window.lucide.createIcons(); } catch (e) { /* no-op */ } } }

  /* ------------------------------------------------------------------ */
  /* transport                                                           */
  /* ------------------------------------------------------------------ */
  function api(path, opts) {
    opts = opts || {};
    var init = { method: opts.method || "GET", cache: "no-store" };
    if (opts.body !== undefined) {
      init.headers = { "Content-Type": "application/json" };
      init.body = JSON.stringify(opts.body);
    }
    return fetch(path, init).then(function (res) {
      return res.json().catch(function () { return {}; }).then(function (data) {
        return { ok: res.ok, status: res.status, data: data || {} };
      });
    });
  }

  /* ------------------------------------------------------------------ */
  /* panes + tabs                                                        */
  /* ------------------------------------------------------------------ */
  function renderPane() {
    var pane = $("sb-pane");
    if (!pane) return;
    var text = state.panes[state.tab];
    if (!text) {
      var empty = {
        transcript: "No source read yet. Paste a YouTube tutorial or GitHub repository URL and press Generate skill.",
        visual: "No visual analysis yet. Enable Visual analysis, or use Video Reverse Engineering for scene-level analysis.",
        skill: "No skill generated yet. Generate a skill, then review it before it is saved.",
        log: "Activity will appear here."
      };
      text = empty[state.tab] || "";
    }
    pane.textContent = text;
  }

  function setTab(tab) {
    state.tab = tab;
    var tabs = state.view ? state.view.querySelectorAll(".sb-tab") : [];
    Array.prototype.forEach.call(tabs, function (t) {
      t.classList.toggle("sb-tab-active", t.getAttribute("data-sb-tab") === tab);
    });
    renderPane();
  }

  function setPane(tab, text) { state.panes[tab] = text || ""; if (state.tab === tab) renderPane(); }

  function log(line) {
    var stamp = new Date().toLocaleTimeString();
    state.panes.log += "[" + stamp + "] " + line + "\n";
    if (state.tab === "log") renderPane();
  }

  function setBusy(on, label) {
    state.busy = on;
    var busy = $("sb-busy");
    var labelEl = busy ? busy.querySelector("span") : null;
    if (labelEl && label) labelEl.textContent = label;
    if (busy) busy.classList.toggle("hidden", !on);
    ["sb-analyze", "sb-build", "sb-setup"].forEach(function (id) {
      var b = $(id);
      if (b) b.disabled = on;
    });
    if (!on) stopElapsed();
    icons();
  }

  /* ------------------------------------------------------------------ */
  /* mode + progress                                                     */
  /* ------------------------------------------------------------------ */
  function setMode(mode) {
    state.mode = mode === "templates" ? "templates" : (mode === "video" ? "video" : "source");
    var buttons = state.view ? state.view.querySelectorAll(".sb-mode") : [];
    Array.prototype.forEach.call(buttons, function (b) {
      var active = b.getAttribute("data-sb-mode") === state.mode;
      b.classList.toggle("sb-mode-active", active);
      b.setAttribute("aria-selected", active ? "true" : "false");
    });

    var srcFields = $("sb-source-fields");
    var srcActions = $("sb-source-actions");
    var tmplWrap = $("sb-templates-wrap");
    var frameOptions = $("sb-frame-options");
    var label = $("sb-source-label");
    var eyes = $("sb-eyes");
    var isTemplates = state.mode === "templates";
    var isVideo = state.mode === "video";

    if (isTemplates) {
      if (srcFields) srcFields.classList.add("hidden");
      if (srcActions) srcActions.classList.add("hidden");
      if (frameOptions) frameOptions.classList.add("hidden");
      if (tmplWrap) tmplWrap.classList.remove("hidden");
      loadTemplates();
    } else {
      if (srcFields) srcFields.classList.remove("hidden");
      if (srcActions) srcActions.classList.remove("hidden");
      if (tmplWrap) tmplWrap.classList.add("hidden");
      if (frameOptions) frameOptions.classList.toggle("hidden", !isVideo);
      if (label) {
        label.textContent = isVideo
          ? "Reference video URL(s)"
          : "YouTube, video or GitHub URL(s)";
      }
    }
    if (eyes) {
      eyes.disabled = false;
      if (isVideo) eyes.checked = true;
    }
    syncCharacterField();
    syncObjectiveNote();
    syncReplicationField();
  }

  function getObjective() {
    var input = state.view
      ? state.view.querySelector('input[name="sb-objective"]:checked')
      : null;
    return input && input.value === "reference" ? "reference" : "tutorial";
  }

  function syncObjectiveNote() {
    var note = $("sb-objective-note");
    if (!note) return;
    var objective = getObjective();
    note.textContent = objective === "reference"
      ? "reconstruct the method, never the content"
      : "extract the method from a tutorial";
  }

  function syncCharacterField() {
    var field = $("sb-character-field");
    if (!field) return;
    var show = state.mode === "video" && getObjective() === "reference";
    field.classList.toggle("hidden", !show);
  }

  function framesOn() { var el = $("sb-frames"); return !!(el && el.checked); }
  function replicateOn() { var el = $("sb-replicate"); return !!(el && el.checked); }
  function replicationTopic() {
    var el = $("sb-replication-topic");
    return (el && el.value || "").trim();
  }
  function syncReplicationField() {
    var input = $("sb-replication-topic");
    if (!input) return;
    input.classList.toggle("hidden", !replicateOn());
  }

  /* ------------------------------------------------------------------ */
  /* Claude Code Templates (Skill Templates tab)                         */
  /* ------------------------------------------------------------------ */
  function loadTemplates() {
    api("/api/skill-builder/templates").then(function (r) {
      if (r.ok && r.data && Array.isArray(r.data.templates)) {
        state.templates = r.data.templates;
        renderTemplates();
      }
    }).catch(function (e) {
      log("templates error: " + e);
    });
  }

  function renderTemplates() {
    var host = $("sb-template-list");
    var countEl = $("sb-template-count");
    if (!host) return;

    var cat = state.templateCategory || "all";
    var query = (state.templateQuery || "").toLowerCase().trim();

    var list = (state.templates || []).filter(function (t) {
      if (cat !== "all" && (t.category || "").toLowerCase() !== cat) return false;
      if (query) {
        var hay = [t.title || "", t.name || "", t.description || "", (t.tags || []).join(" ")].join(" ").toLowerCase();
        if (hay.indexOf(query) === -1) return false;
      }
      return true;
    });

    if (countEl) countEl.textContent = list.length + " template" + (list.length === 1 ? "" : "s");

    if (!list.length) {
      host.innerHTML = '<div class="sb-rail-empty py-6 text-center text-slate-500">No matching templates found.</div>';
      return;
    }

    host.innerHTML = list.map(function (t) {
      var catClass = "sb-template-cat-" + (t.category || "skills");
      var tagsHtml = (t.tags || []).slice(0, 3).map(function (tag) {
        return '<span class="sb-template-tag">' + esc(tag) + '</span>';
      }).join("");

      return '<div class="sb-template-card">' +
        '<div class="sb-template-card-head">' +
          '<div class="flex-1 min-w-0">' +
            '<div class="sb-template-title truncate" title="' + esc(t.title || t.name) + '">' + esc(t.title || t.name) + '</div>' +
            '<div class="text-[9px] font-mono text-emerald-400/80">' + esc(t.name) + '</div>' +
          '</div>' +
          '<span class="sb-template-category-badge ' + catClass + '">' + esc(t.category) + '</span>' +
        '</div>' +
        '<div class="sb-template-desc" title="' + esc(t.description || "") + '">' + esc(t.description || "") + '</div>' +
        (tagsHtml ? '<div class="sb-template-tags">' + tagsHtml + '</div>' : '') +
        '<div class="sb-template-actions">' +
          '<button type="button" class="sb-mini" data-tmpl-preview="' + esc(t.id) + '" title="Preview template in Skill tab">' +
            '<i data-lucide="eye" class="w-3 h-3"></i><span>Preview</span>' +
          '</button>' +
          '<button type="button" class="sb-mini sb-mini-primary ml-auto" data-tmpl-adopt="' + esc(t.id) + '" title="Review and add this skill to the Vault">' +
            '<i data-lucide="folder-plus" class="w-3 h-3"></i><span>Adopt Skill</span>' +
          '</button>' +
        '</div>' +
      '</div>';
    }).join("");
    icons();
  }

  function previewTemplate(tmplId) {
    api("/api/skill-builder/templates/" + encodeURIComponent(tmplId)).then(function (r) {
      if (!r.ok || !r.data || !r.data.content) {
        setPane("skill", "Error loading template: " + ((r.data && r.data.error) || r.status));
        setTab("skill");
        return;
      }
      setPane("skill", r.data.content);
      setTab("skill");
    });
  }

  function adoptTemplate(tmplId) {
    setBusy(true, "Adopting template…");
    api("/api/skill-builder/templates/adopt", { method: "POST", body: { template_id: tmplId } }).then(function (r) {
      if (!r.ok || !r.data || !r.data.draft) {
        throw new Error((r.data && r.data.error) || ("HTTP " + r.status));
      }
      openReview(r.data.draft);
    }).catch(function (e) {
      log("Failed to adopt template: " + (e.message || e));
    }).then(function () {
      setBusy(false);
    });
  }

  function setProgressVisible(on) {
    var el = $("sb-progress");
    if (el) el.classList.toggle("hidden", !on);
  }

  function renderProgress(job) {
    setProgressVisible(true);
    var stageEl = $("sb-progress-stage");
    var detailEl = $("sb-progress-detail");
    var bar = $("sb-progress-bar");
    var stagesEl = $("sb-progress-stages");
    var stages = (job && job.stages && job.stages.length) ? job.stages : STATE_STAGES;
    var current = job ? job.stage_index : 0;
    var status = job ? job.status : "running";
    if (stageEl) stageEl.textContent = (job && job.stage) ? job.stage : (status === "done" ? "Completed" : "Starting…");
    if (detailEl) detailEl.textContent = (job && job.detail) ? job.detail : "";
    if (bar) bar.style.width = Math.max(2, Math.min(100, (job && job.percent) || 3)) + "%";

    if (stagesEl) {
      stagesEl.innerHTML = stages.map(function (name, i) {
        var cls = "sb-progress-stage";
        if (status === "error" && i === current) cls += " failed";
        else if (i < current || status === "done") cls += " done";
        else if (i === current) cls += " active";
        return '<span class="' + cls + '">' + (i < current || status === "done" ? "✓ " : (i === current ? "● " : "○ ")) + esc(name) + "</span>";
      }).join("");
    }
    var elapsed = $("sb-progress-elapsed");
    if (elapsed) elapsed.textContent = ((job && job.elapsed_seconds) || elapsedSeconds()) + "s";
    icons();
  }

  function elapsedSeconds() {
    if (!state.startedAt) return "0.0";
    return ((Date.now() - state.startedAt) / 1000).toFixed(1);
  }
  function startElapsed() {
    state.startedAt = Date.now();
    stopElapsed();
    state.elapsedTimer = window.setInterval(function () {
      var el = $("sb-progress-elapsed");
      if (el) el.textContent = elapsedSeconds() + "s";
    }, 200);
  }
  function stopElapsed() {
    if (state.elapsedTimer) window.clearInterval(state.elapsedTimer);
    state.elapsedTimer = null;
  }

  /* ------------------------------------------------------------------ */
  /* agent + model dropdowns (mirrors the Studio toolbar)                */
  /* ------------------------------------------------------------------ */
  function closeMenus(except) {
    ["sb-agent-menu", "sb-provider-menu", "sb-model-menu"].forEach(function (id) {
      if (id === except) return;
      var m = $(id);
      if (m) m.classList.add("hidden");
    });
  }
  function toggleMenu(menuId, btnId) {
    var menu = $(menuId), btn = $(btnId);
    if (!menu || !btn || btn.disabled) return;
    var willOpen = menu.classList.contains("hidden");
    closeMenus(menuId);
    menu.classList.toggle("hidden", !willOpen);
  }
  function setAgentLabel() {
    var el = $("sb-agent-label");
    var a = state.agents.filter(function (x) { return x.id === state.agentId; })[0];
    if (el) el.textContent = "Agent: " + (a ? a.name : "—");
  }
  function setModelLabel() {
    var el = $("sb-model-label");
    if (!state.modelId) { if (el) el.textContent = "Model: CLI default"; return; }
    var m = state.models.filter(function (x) { return x.id === state.modelId; })[0];
    var name = m ? modelDisplay(m) : state.modelId;
    if (el) el.textContent = "Model: " + name;
  }

  /* OpenCode names models ``provider/model``; the provider has its own
   * dropdown, so the model label shows just the model part. */
  function modelDisplay(m) {
    if (!m) return "";
    var name = m.name || m.id || "";
    var prefix = (m.provider || "") + "/";
    if (m.provider && name.indexOf(prefix) === 0) return name.slice(prefix.length);
    return name;
  }

  function setProviderLabel() {
    var el = $("sb-provider-label");
    if (!el) return;
    if (state.modelsLoading) { el.textContent = "Provider: …"; return; }
    var p = state.providers.filter(function (x) { return x.id === state.providerId; })[0];
    if (p) { el.textContent = "Provider: " + (p.label || p.id); return; }
    var count = state.providers.length;
    el.textContent = count > 1 ? "Provider: All (" + count + ")" : "Provider: —";
  }

  /* Provider list comes from the same /models payload as the model list: only
   * providers the CLI actually reported are shown (never a hardcoded list). */
  function renderProviders() {
    var host = $("sb-provider-options");
    var btn = $("sb-provider-btn");
    if (btn) btn.disabled = !state.agentId;
    if (!host) return;
    renderProviderSetup();
    if (!state.agentId) {
      host.innerHTML = '<div class="sb-rail-empty">Select an agent first.</div>';
      return;
    }
    if (state.modelsLoading) {
      host.innerHTML = '<div class="sb-rail-empty">Loading providers…</div>';
      return;
    }
    if (!state.providers.length) {
      host.innerHTML = '<div class="sb-rail-empty">This agent did not report any provider. Configure it below, then press Refresh.</div>';
      return;
    }
    var rows = [];
    if (state.providers.length > 1) {
      var total = state.providers.reduce(function (n, p) { return n + (p.model_count || 0); }, 0);
      rows.push(providerRow("All providers", total + (total === 1 ? " model" : " models"),
        !state.providerId, "__all__", true,
        "Show models from every provider this agent reports."));
    }
    state.providers.forEach(function (p) {
      var count = p.model_count || 0;
      rows.push(providerRow(p.label || p.id, count + (count === 1 ? " model" : " models"),
        state.providerId === p.id, p.id, p.available !== false, ""));
    });
    host.innerHTML = rows.join("");
    icons();
  }

  function providerRow(label, countText, active, id, available, title) {
    return '<button type="button" class="ws-menu-item' + (active ? " ws-menu-item-active" : "") +
      (available ? "" : " ws-menu-item-disabled") + '" data-provider="' + esc(id) + '"' +
      (available ? "" : ' disabled') + (title ? ' title="' + esc(title) + '"' : "") + '>' +
      '<span class="ws-agent-dot" style="background:' + (available ? "#f59e0b" : "#ef4444") + '"></span>' +
      '<span class="flex-1 min-w-0 text-left truncate">' + esc(label) + '</span>' +
      '<span class="text-[10px] font-mono text-slate-500 shrink-0">' + esc(countText) + '</span>' +
      '</button>';
  }

  function renderProviderSetup() {
    var host = $("sb-provider-setup");
    if (!host) return;
    var setup = state.providerSetup;
    var sig = setup ? [setup.label || "", setup.command || "", setup.hint || "", setup.form || ""].join("\u0001") : "";
    if (sig === state.providerSetupSig) return;   // already rendered (search keystroke)
    state.providerSetupSig = sig;
    host.innerHTML = "";
    if (!setup || (!setup.command && !setup.hint && !setup.form)) {
      host.classList.add("hidden");
      return;
    }
    host.classList.remove("hidden");
    var html = '<div class="text-[10px] font-semibold text-slate-300">' +
      esc(setup.label || "Configure provider") + '</div>';
    if (setup.command) {
      html += '<div class="flex items-center gap-1.5">' +
        '<code class="flex-1 min-w-0 truncate px-2 py-1 rounded-md bg-[#0b1226] border border-[#22304f] text-[10.5px] font-mono text-cyan-300">' +
        esc(setup.command) + '</code>' +
        '<button type="button" id="sb-provider-copy" class="ws-icon-btn" title="Copy command">' +
        '<i data-lucide="copy" class="w-3 h-3"></i></button></div>';
    }
    if (setup.hint) {
      html += '<div class="text-[10px] text-slate-500 leading-snug">' + esc(setup.hint) + '</div>';
    }
    if (setup.form === "freellmapi") {
      html += '<div class="ws-setup-auth"><div class="ws-setup-state" id="sb-free-state">Checking FreeLLMAPI…</div>' +
        '<div class="space-y-1.5"><input class="ws-setup-input" id="sb-free-url" type="url" placeholder="https://api.groq.com/openai/v1" aria-label="Free API base URL">' +
        '<input class="ws-setup-input" id="sb-free-model" type="text" placeholder="Model ID from the FreeLLMAPI repo" aria-label="Free API model ID">' +
        '<input class="ws-setup-input" id="sb-free-key" type="password" autocomplete="new-password" placeholder="Provider API key" aria-label="Free API key">' +
        '<div class="ws-setup-row"><button type="button" class="ws-setup-btn ws-setup-btn-primary" id="sb-free-connect">Connect</button>' +
        '<button type="button" class="ws-setup-btn ws-setup-btn-danger hidden" id="sb-free-disconnect">Disconnect</button></div></div>' +
        '<div class="ws-setup-msg" id="sb-free-msg" role="status"></div></div>';
    }
    host.innerHTML = html;
    if (setup.form === "freellmapi") bindFreeLLMAPISetup();
    icons();
  }

  function bindFreeLLMAPISetup() {
    var stateEl = $("sb-free-state"), url = $("sb-free-url"), model = $("sb-free-model");
    var key = $("sb-free-key"), connect = $("sb-free-connect"), disconnect = $("sb-free-disconnect");
    var msg = $("sb-free-msg");
    function message(text) { if (msg) msg.textContent = text || ""; }
    function paint(s) {
      if (!stateEl) return;
      if (s && s.configured) {
        stateEl.textContent = "Connected to " + s.base_url + " · key " + (s.key_hint || "saved");
        stateEl.className = "ws-setup-state ws-setup-state-ok";
        url.value = s.base_url || ""; model.value = s.model || "";
        key.placeholder = "Saved key (leave blank to keep)";
        disconnect.classList.remove("hidden");
      } else {
        stateEl.textContent = "Connect one provider and model from the FreeLLMAPI catalog.";
        stateEl.className = "ws-setup-state ws-setup-state-missing";
        key.placeholder = "Provider API key"; disconnect.classList.add("hidden");
      }
    }
    connect.addEventListener("click", function (e) {
      e.stopPropagation();
      if (!url.value.trim() || !model.value.trim() || (!key.value.trim() && !key.placeholder.startsWith("Saved key"))) {
        message("Enter the provider base URL, model ID, and API key."); return;
      }
      connect.disabled = true; message("Saving connection…");
      api("/api/freellmapi", { method: "POST", body: { agent_id: state.agentId, base_url: url.value.trim(), model: model.value.trim(), api_key: key.value.trim() } })
        .then(function (r) {
          if (!r.ok) { message((r.data && r.data.error) || "Could not save the provider."); return; }
          key.value = ""; paint((r.data && r.data.settings) || {}); message("Connected. Refreshing models…");
          return loadModels(true).then(function () { message("Connected. Choose the FreeLLMAPI model from the model menu."); });
        }).catch(function () { message("Backend unreachable."); }).then(function () { connect.disabled = false; });
    });
    disconnect.addEventListener("click", function (e) {
      e.stopPropagation(); disconnect.disabled = true; message("Disconnecting…");
      api("/api/freellmapi?agent_id=" + encodeURIComponent(state.agentId), { method: "DELETE" }).then(function (r) {
        if (!r.ok) { message((r.data && r.data.error) || "Could not disconnect."); return; }
        key.value = ""; paint((r.data && r.data.settings) || {}); message("Disconnected.");
        return loadModels(true);
      }).catch(function () { message("Backend unreachable."); }).then(function () { disconnect.disabled = false; });
    });
    api("/api/freellmapi?agent_id=" + encodeURIComponent(state.agentId)).then(function (r) {
      if (r.ok) paint((r.data && r.data.settings) || {});
      else if (stateEl) stateEl.textContent = "Could not check FreeLLMAPI settings.";
    }).catch(function () { if (stateEl) stateEl.textContent = "Could not check FreeLLMAPI settings."; });
  }

  function resolveProviderId() {
    var known = function (id) {
      return state.providers.some(function (p) { return p.id === id; });
    };
    // An explicit pick wins, including an explicit "All providers" (null).
    if (state.providerLocked) {
      if (!state.providerId) return null;
      if (known(state.providerId)) return state.providerId;
    }
    if (state.providerId && known(state.providerId)) return state.providerId;
    var current = state.models.filter(function (m) { return m.id === state.modelId; })[0];
    if (current && current.provider && known(current.provider)) return current.provider;
    if (state.providers.length === 1) return state.providers[0].id;
    return null;
  }

  function selectProvider(id) {
    if (!state.agentId) return;
    state.providerId = id || null;
    state.providerLocked = true;
    state.modelQuery = "";
    setProviderLabel();
    renderProviders();
    renderModels();
    // The provider is a filter; the model is the actual change. Open the
    // filtered model list so the switch is completed in one flow.
    closeMenus();
    toggleMenu("sb-model-menu", "sb-model-btn");
    focusModelSearch();
  }
  function setModelNote(text) {
    var el = $("sb-model-note");
    if (!el) return;
    if (text) { el.textContent = text; el.classList.remove("hidden"); }
    else { el.textContent = ""; el.classList.add("hidden"); }
  }
  function renderAgents() {
    var host = $("sb-agent-options");
    if (!host) return;
    if (!state.agents.length) { host.innerHTML = '<div class="sb-rail-empty">No agents found.</div>'; return; }
    host.innerHTML = state.agents.map(function (a) {
      var off = a.available === false;
      var active = a.id === state.agentId;
      var detected = !off && a.registered === false;
      var meta = off ? (a.reason || "unavailable")
        : detected ? "detected · setup"
        : (a.provider || "");
      var hint = off ? (a.reason || "unavailable")
        : detected ? (a.name || a.id) + " detected on host — select to integrate"
        : (a.provider || "");
      return '<button class="ws-menu-item' + (active ? " ws-menu-item-active" : "") + (off ? " ws-menu-item-disabled" : "") + '"' +
        ' data-agent="' + esc(a.id) + '"' + (off ? " disabled" : "") + ' title="' + esc(hint) + '">' +
        '<span class="ws-agent-dot" style="background:' + (off ? "#475569" : detected ? "#f59e0b" : "#22c55e") + '"></span>' +
        '<span class="flex-1 min-w-0 text-left truncate">' + esc(a.name || a.id) + '</span>' +
        '<span class="text-[9.5px] text-slate-500 shrink-0">' + esc(meta) + '</span>' +
        '</button>';
    }).join("");
  }
  function renderModels() {
    renderProviders();
    setProviderLabel();
    var host = $("sb-model-options");
    var searchWrap = $("sb-model-search-wrap");
    var searchInput = $("sb-model-search");
    if (!host) return;
    if (searchWrap) searchWrap.classList.toggle("hidden", !state.models.length);
    if (searchInput && searchInput.value !== state.modelQuery) searchInput.value = state.modelQuery;
    if (!state.models.length) {
      host.innerHTML = '<div class="sb-rail-empty">This CLI uses its own default model.</div>';
      return;
    }
    var terms = (state.modelQuery || "").toLowerCase().trim().split(/\s+/).filter(Boolean);
    var shown = state.models.filter(function (m) {
      if (state.providerId && m.provider !== state.providerId) return false;
      if (!terms.length) return true;
      var hay = [m.name || m.id, m.id, m.description || "", m.provider || ""].join(" ").toLowerCase();
      return terms.every(function (t) { return hay.indexOf(t) !== -1; });
    });
    if (!shown.length) {
      host.innerHTML = '<div class="sb-rail-empty">No matching model.</div>';
      return;
    }
    host.innerHTML = shown.map(function (m) {
      var off = m.available === false;
      var active = m.id === state.modelId;
      return '<button class="ws-menu-item' + (active ? " ws-menu-item-active" : "") + (off ? " ws-menu-item-disabled" : "") + '"' +
        ' data-model="' + esc(m.id) + '"' + (off ? " disabled" : "") + '>' +
        '<span class="flex-1 min-w-0 text-left truncate">' + esc(modelDisplay(m)) + '</span>' +
        (m.description && m.description !== m.provider
          ? '<span class="text-[9.5px] text-slate-500 shrink-0 truncate max-w-[40%]">' + esc(m.description) + '</span>'
          : '') +
        '</button>';
    }).join("");
  }

  function focusModelSearch() {
    var wrap = $("sb-model-search-wrap");
    var input = $("sb-model-search");
    if (!wrap || !input || wrap.classList.contains("hidden")) return;
    window.setTimeout(function () { input.focus(); }, 0);
  }

  function loadAgents() {
    if (window.location.protocol === "file:") { applyAgents(AGENT_FALLBACK); return; }
    api("/api/agents/availability").then(function (r) {
      var agents = r.ok && r.data && Array.isArray(r.data.agents) ? r.data.agents : null;
      applyAgents(withRequiredAgents(agents && agents.length ? agents : AGENT_FALLBACK));
    }).catch(function () { applyAgents(withRequiredAgents(AGENT_FALLBACK)); });
  }
  function withRequiredAgents(agents) {
    var list = Array.isArray(agents) ? agents.slice() : [];
    [
      { id: "cursor", name: "Cursor", provider: "Cursor" },
      { id: "openclaw", name: "OpenClaw", provider: "OpenClaw" }
    ].forEach(function (required) {
      if (!list.some(function (agent) { return agent && agent.id === required.id; })) {
        list.splice(Math.min(1, list.length), 0, {
          id: required.id,
          name: required.name,
          provider: required.provider,
          available: false,
          reason: "Backend restart required to detect this CLI"
        });
      }
    });
    return list;
  }
  function applyAgents(agents) {
    var previousAgent = state.agentId;
    state.agents = agents;
    if (!state.agentId || !agents.some(function (a) { return a.id === state.agentId; })) {
      var first = agents.filter(function (a) { return a.available !== false; })[0] || agents[0];
      state.agentId = first ? first.id : null;
    }
    var changed = state.agentId !== previousAgent;
    if (changed) {
      state.modelId = null;
      state.providerId = null;
      state.providerLocked = false;
      state.providers = [];
      state.providerSetup = null;
    }
    setAgentLabel();
    renderAgents();
    if (state.agentId && (changed || state.modelsFor !== state.agentId)) {
      loadModels(false);
    }
  }
  function loadModels(refresh) {
    var btn = $("sb-model-btn");
    if (!state.agentId) {
      state.models = [];
      state.modelsFor = null;
      state.modelsLoading = false;
      state.providers = [];
      state.providerId = null;
      state.providerLocked = false;
      state.providerSetup = null;
      renderModels();
      if (btn) btn.disabled = true;
      return;
    }
    state.modelsFor = state.agentId;
    state.modelsLoading = true;
    if (btn) btn.disabled = false;
    setModelNote("Loading models…");
    var host = $("sb-model-options");
    if (host) host.innerHTML = '<div class="sb-rail-empty">Loading…</div>';
    var url = "/api/agents/" + encodeURIComponent(state.agentId) + "/models" + (refresh ? "?refresh=1" : "");
    return api(url).then(function (r) {
      state.modelsLoading = false;
      if (!r.ok || !r.data || !Array.isArray(r.data.models) || !r.data.models.length) {
        state.models = [];
        state.modelId = null;
        state.providers = [];
        // Keep an explicit provider pick across the error so Refresh restores
        // it once the CLI can list models again.
        state.providerSetup = (r.data && r.data.provider_setup) || null;
        setModelLabel(); renderModels();
        setModelNote(r.ok ? "" : "Model list unavailable — using CLI default");
        return;
      }
      state.models = r.data.models;
      var current = r.data.current_model ||
        ((state.models.filter(function (m) { return m.is_default; })[0] || state.models[0]).id);
      var keep = state.modelId && state.models.some(function (m) { return m.id === state.modelId; });
      state.modelId = keep ? state.modelId : current;
      state.providers = (r.data && r.data.providers) || [];
      state.providerSetup = (r.data && r.data.provider_setup) || null;
      state.providerId = resolveProviderId();
      setModelLabel(); renderModels(); setModelNote("");
    }).catch(function () {
      state.modelsLoading = false;
      state.models = [];
      state.modelId = null;
      state.providers = [];
      // Keep the provider pick; a later successful load re-derives the label.
      state.providerSetup = null;
      setModelLabel(); renderModels(); setModelNote("Couldn't load models — using CLI default");
    });
  }

  function integrateAgent(id, name) {
    log("integrating detected agent " + (name || id) + " into the registry…");
    api("/api/agents/integrate", { method: "POST", body: { agent_id: id } }).then(function (r) {
      if (!r.ok || !r.data || r.data.success === false) {
        log("integration failed: " + ((r.data && r.data.error) || r.status));
        return;
      }
      log("integrated '" + (name || id) + "' — it is now a registered agent.");
      loadAgents();
    }).catch(function (e) { log("integration error: " + e); });
  }

  /* ------------------------------------------------------------------ */
  /* status + config                                                     */
  /* ------------------------------------------------------------------ */
  function renderStatus(s) {
    var pill = $("sb-status-pill");
    var text = $("sb-status-text");
    var deps = s.deps || {};
    var ready = s.exists && deps.yt_dlp && deps.gemini;
    var label = s.error ? s.error
      : ready ? "Ready"
      : s.exists ? "Missing dependencies — check Configuration"
      : "Workspace not installed — press Setup";
    if (text) text.textContent = label;
    if (pill) {
      pill.classList.toggle("sb-pill-ok", !!ready && !s.error);
      pill.classList.toggle("sb-pill-warn", !ready || !!s.error);
    }
    var cfg = $("sb-config");
    if (cfg) {
      var chain = (s.models || []).join(" → ") || "—";
      var cache = s.cache || {};
      cfg.innerHTML =
        row("workspace", s.exists ? "found" : "missing") +
        row("yt-dlp", deps.yt_dlp ? "ok" : "missing") +
        row("ffmpeg", deps.ffmpeg ? "ok" : "missing") +
        row("Gemini key", deps.gemini ? "ok" : "missing") +
        row("Vault skills", String(s.published_count || 0)) +
        row("Cache", (cache.entries || 0) + " entries") +
        '<div class="pt-1 text-slate-500">models</div><div class="text-emerald-300/90 break-words">' + esc(chain) + "</div>" +
        '<div class="pt-1 text-slate-500">vault</div><div class="text-slate-400 break-all">' + esc(s.vault_skills_dir || "") + "</div>" +
        '<div class="pt-1 text-slate-500">drafts</div><div class="text-slate-400 break-all">' + esc(s.drafts_dir || "") + "</div>" +
        (s.error ? '<div class="pt-1 text-rose-300 break-words">' + esc(s.error) + "</div>" : "");
    }
  }
  function row(k, v) {
    return '<div class="flex items-center justify-between gap-2"><span class="text-slate-500">' + esc(k) +
      '</span><span class="text-slate-300">' + esc(v) + "</span></div>";
  }
  function loadStatus() {
    api("/api/skill-builder/status").then(function (r) {
      if (r.ok) { renderStatus(r.data); return; }
      var msg = r.status === 404 ? "Backend route missing — restart the server"
        : r.status === 401 ? "Unauthorized — reload the page"
        : ((r.data && r.data.error) || ("HTTP " + r.status));
      renderStatus({ exists: false, deps: {}, error: msg });
    }).catch(function (e) { renderStatus({ exists: false, deps: {}, error: "Backend unreachable: " + e }); });
  }

  function runSetup() {
    if (state.busy) return;
    setBusy(true, "Setting up…");
    log("running workspace setup…");
    setTab("log");
    api("/api/skill-builder/setup", { method: "POST", body: {} }).then(function (r) {
      if (!r.ok) { log("setup failed: " + ((r.data && r.data.error) || r.status)); return; }
      log("setup finished (exit " + r.data.code + ")\n" + (r.data.output || ""));
      loadStatus();
    }).catch(function (e) { log("setup error: " + e); }).then(function () { setBusy(false); });
  }

  /* ------------------------------------------------------------------ */
  /* skills rail                                                         */
  /* ------------------------------------------------------------------ */
  function renderSkills(list) {
    var host = $("sb-skills");
    if (!host) return;
    if (!list || !list.length) { host.innerHTML = '<div class="sb-rail-empty">No skills yet. Approve your first one.</div>'; return; }
    host.innerHTML = list.map(function (s) {
      var badge = s.source === "vault"
        ? '<span class="sb-chip sb-chip-ok">vault</span>'
        : s.installed ? '<span class="sb-chip sb-chip-ok">installed</span>'
        : '<span class="sb-chip">legacy</span>';
      var installBtn = s.installed ? "" : '<button class="sb-mini" data-install="' + esc(s.name) + '">Install</button>';
      return '<div class="sb-skill">' +
        '<div class="flex items-center gap-2">' +
          '<i data-lucide="puzzle" class="w-3.5 h-3.5 text-emerald-300 shrink-0"></i>' +
          '<span class="flex-1 min-w-0 truncate text-[11.5px] font-semibold text-slate-200">' + esc(s.title || s.name) + '</span>' +
          badge +
        '</div>' +
        '<div class="text-[10px] text-slate-500 line-clamp-2 mt-0.5">' + esc(s.description || "") + '</div>' +
        '<div class="flex items-center gap-1.5 mt-1.5">' +
          '<button class="sb-mini" data-view="' + esc(s.name) + '">View</button>' + installBtn +
          '<span class="ml-auto text-[9px] font-mono text-slate-600">' + esc(s.source) + '</span>' +
        '</div></div>';
    }).join("");
    icons();
  }
  function loadSkills() {
    return api("/api/skill-builder/skills").then(function (r) {
      if (r.ok) renderSkills(r.data.skills || []);
    }).catch(function () { /* no-op */ });
  }
  function viewSkill(name) {
    api("/api/skill-builder/skill?name=" + encodeURIComponent(name)).then(function (r) {
      if (!r.ok) { setPane("skill", "Error: " + ((r.data && r.data.error) || r.status)); setTab("skill"); return; }
      setPane("skill", r.data.content || "(empty)");
      setTab("skill");
    });
  }
  function installSkill(name) {
    api("/api/skill-builder/install", { method: "POST", body: { name: name } }).then(function (r) {
      if (!r.ok) { log("install failed: " + ((r.data && r.data.error) || r.status)); setTab("log"); return; }
      log("installed skill '" + name + "' → " + (r.data.target || ""));
      loadSkills();
    });
  }

  /* ------------------------------------------------------------------ */
  /* job runner + progress polling                                       */
  /* ------------------------------------------------------------------ */
  function urls() {
    var el = $("sb-urls");
    if (!el) return [];
    return el.value.split(/\s+/).map(function (u) { return u.trim(); }).filter(Boolean);
  }
  function eyesOn() { var el = $("sb-eyes"); return !!(el && el.checked); }

  function submitJob(action) {
    if (state.busy || state.jobId) return;
    var list = urls();
    if (!list.length) { setPane("log", "Paste at least one source URL."); setTab("log"); return; }
    if (state.mode === "video") {
      state.objective = getObjective();
    } else {
      state.objective = "tutorial";
    }
    var body = {
      action: action,
      urls: list,
      mode: state.mode,
      objective: state.objective,
      eyes: eyesOn(),
      extract_frames: state.mode === "video" && framesOn(),
      replicate: state.mode === "video" && replicateOn(),
      replication_topic: state.mode === "video" ? replicationTopic() : "",
      agent_id: state.agentId || undefined,
      model_id: state.modelId || undefined,
      character_image_url: (state.mode === "video" && state.objective === "reference")
        ? ((($("sb-character-url") || {}).value || "").trim() || undefined)
        : undefined
    };
    setBusy(true, action === "analyze" ? "Analyzing…" : "Generating…");
    setProgressVisible(true);
    renderProgress({ stages: STATE_STAGES, stage_index: 0, stage: "Validating source", detail: "Submitting job", percent: 3, status: "queued" });
    startElapsed();
    log((action === "analyze" ? "analyzing " : "generating from ") + list.length + " source(s), mode=" + state.mode);
    setTab("log");

    api("/api/skill-builder/jobs", { method: "POST", body: body }).then(function (r) {
      if (!r.ok || !r.data || !r.data.job) {
        throw new Error((r.data && r.data.error) || ("HTTP " + r.status));
      }
      state.jobId = r.data.job.id;
      state.pollErrors = 0;
      if (r.data.deduplicated) log("reusing the already-running job " + state.jobId);
      else log("job " + state.jobId + " started");
      pollJob();
    }).catch(function (e) {
      finishJob();
      showProgressError(String(e.message || e));
    });
  }

  function pollJob() {
    if (!state.jobId) return;
    if (state.pollTimer) window.clearTimeout(state.pollTimer);
    api("/api/skill-builder/jobs/" + encodeURIComponent(state.jobId)).then(function (r) {
      if (!r.ok || !r.data || !r.data.id) throw new Error((r.data && r.data.error) || ("HTTP " + r.status));
      state.pollErrors = 0;
      var job = r.data;
      renderProgress(job);
      if (job.detail) log(job.stage + ": " + job.detail);
      if (job.status === "done") {
        finishJob();
        onJobDone(job);
        return;
      }
      if (job.status === "error" || job.status === "cancelled") {
        finishJob();
        showProgressError(job.status === "cancelled" ? "Cancelled." : (job.error || "The job failed."));
        return;
      }
      state.pollTimer = window.setTimeout(pollJob, 900);
    }).catch(function (e) {
      state.pollErrors += 1;
      if (state.pollErrors >= 4) {
        finishJob();
        showProgressError("Lost contact with the job: " + (e.message || e));
        return;
      }
      state.pollTimer = window.setTimeout(pollJob, 1200);
    });
  }

  function finishJob() {
    if (state.pollTimer) window.clearTimeout(state.pollTimer);
    state.pollTimer = null;
    state.jobId = null;
    setBusy(false);
    stopElapsed();
    loadJobs();
  }

  function showProgressError(message) {
    setProgressVisible(true);
    renderProgress({
      stages: STATE_STAGES, stage_index: 0, stage: "Failed", detail: message,
      percent: 100, status: "error", elapsed_seconds: elapsedSeconds()
    });
    log("failed: " + message);
    setPane("log", state.panes.log);
    setTab("log");
  }

  function sourceSummary(sources) {
    return (sources || []).map(function (s) {
      var meta = s.metadata || {};
      var lines = [
        "[" + (s.kind || "source") + "] " + (s.title || s.id || s.url),
        "URL: " + (s.url || ""),
        "id: " + (s.id || "") + (s.cached ? "  (cached)" : ""),
        "fetch: " + (s.fetch_seconds != null ? s.fetch_seconds + "s" : "?") +
          "  transcript: " + (s.transcript_chars || 0) + " chars" +
          "  text: " + (s.text_chars || 0) + " chars" +
          (s.visual_chars ? "  visual: " + s.visual_chars + " chars" : "")
      ];
      if (meta.channel) lines.push("channel: " + meta.channel);
      if (meta.duration_seconds) lines.push("duration: " + meta.duration_seconds + "s");
      if (s.documents && s.documents.length) {
        lines.push("documents: " + s.documents.map(function (d) { return d.path; }).join(", "));
      }
      if (s.warnings && s.warnings.length) lines.push("warnings: " + s.warnings.join(" | "));
      return lines.join("\n");
    }).join("\n\n");
  }

  function sourcePreviews(sources) {
    return (sources || []).map(function (s) {
      return "### " + (s.title || s.id || s.url) + "\n" + (s.preview || "(no text preview)");
    }).join("\n\n---\n\n");
  }

  function visualPreviews(sources) {
    return (sources || []).filter(function (s) { return s.visual_preview; })
      .map(function (s) {
        return "### " + (s.title || s.id || s.url) + "\n" + s.visual_preview;
      }).join("\n\n---\n\n");
  }

  function evidenceSummary(evidenceList) {
    return (evidenceList || []).map(function (ev) {
      var source = ev.source || {};
      var technical = ev.technical || {};
      var timeline = ev.timeline || {};
      var tech = [
        technical.width && technical.height ? technical.width + "x" + technical.height : "",
        technical.fps ? technical.fps + " fps" : "",
        technical.duration_seconds ? technical.duration_seconds + "s" : "",
        technical.container || ""
      ].filter(Boolean).join(" · ");
      var lines = ["EVIDENCE · " + (source.title || source.id || source.url || "reference")];
      if (tech) lines.push("technical: " + tech);
      lines.push("scenes: " + (ev.scene_count || 0) + " · frames: " + (ev.frame_count || 0) +
        (timeline.cuts != null ? " · cuts: " + timeline.cuts +
          " (" + (timeline.cuts_per_minute || 0) + "/min)" : ""));
      if (timeline.mean_scene_seconds) {
        lines.push("mean scene: " + timeline.mean_scene_seconds + "s");
      }
      if ((ev.limitations || []).length) {
        lines.push("limitations: " + ev.limitations.join(" · "));
      }
      return lines.join("\n");
    }).filter(Boolean).join("\n\n---\n\n");
  }

  function onJobDone(job) {
    var result = job.result || {};
    if (result.draft) {
      setPane("skill", result.draft.content || "");
      setPane("transcript", sourcePreviews(result.sources) || sourceSummary(result.sources));
      var vis = (result.sources || []).filter(function (s) { return s.visual_chars; });
      var evidenceText = evidenceSummary(result.draft.evidence);
      var visualText = vis.length
        ? visualPreviews(result.sources)
        : "No visual analysis was run for this job.";
      setPane("visual", [evidenceText, visualText].filter(Boolean).join("\n\n---\n\n"));
      var t = result.timings || {};
      log("draft ready: " + result.draft.name + " (fetch " + (t.fetch_seconds || "?") +
          "s, generation " + (t.generation_seconds || "?") + "s, total " + (t.total_seconds || "?") + "s)");
      if (result.warnings && result.warnings.length) {
        result.warnings.forEach(function (w) { log("warning: " + w); });
      }
      openReview(result.draft, result);
      loadJobs();
      return;
    }
    if (result.sources) {
      setPane("transcript", sourcePreviews(result.sources) || sourceSummary(result.sources));
      setPane("visual", visualPreviews(result.sources) || "No visual analysis was requested.");
      var timings = result.timings || {};
      log("source read complete in " + (timings.total_seconds || "?") + "s" +
          (timings.fetch_seconds ? " (fetch " + timings.fetch_seconds + "s)" : ""));
      setTab("transcript");
      loadSkills();
      return;
    }
    log("job completed with no result");
  }

  /* ------------------------------------------------------------------ */
  /* recent jobs rail                                                    */
  /* ------------------------------------------------------------------ */
  function loadJobs() {
    state.jobsLoading = true;
    return api("/api/skill-builder/jobs").then(function (r) {
      state.jobsLoading = false;
      if (r.ok && r.data && Array.isArray(r.data.jobs)) {
        state.jobs = r.data.jobs;
        renderJobs();
      }
    }).catch(function () {
      state.jobsLoading = false;
      renderJobs();
    });
  }

  function jobModeLabel(job) {
    var request = (job && job.request) || {};
    var mode = request.mode === "video" ? "video" : "source";
    var objective = request.objective === "reference" ? "reference" : "tutorial";
    return mode + " · " + objective;
  }

  function renderJobs() {
    var host = $("sb-jobs");
    if (!host) return;
    var list = state.jobs || [];
    if (!list.length) {
      host.innerHTML = '<div class="sb-rail-empty">' +
        (state.jobsLoading ? "Loading jobs…" : "No jobs yet.") + "</div>";
      return;
    }
    host.innerHTML = list.slice(0, 8).map(function (job) {
      var status = job.status || "queued";
      var running = status === "running" || status === "queued";
      var action = job.kind === "generate" ? "Generate" : "Analyze";
      var detail = running
        ? (job.stage || "Working…")
        : (job.error || (status === "interrupted"
            ? "Interrupted — resubmit to continue"
            : status));
      var buttons = [];
      if (running) {
        buttons.push('<button type="button" class="sb-mini" data-job-attach="' + esc(job.id) + '">Attach</button>');
      } else if (status === "interrupted") {
        buttons.push('<button type="button" class="sb-mini sb-mini-primary" data-job-resume="' + esc(job.id) + '">Resubmit</button>');
      } else if (status === "done" && job.result && job.result.draft) {
        buttons.push('<button type="button" class="sb-mini sb-mini-primary" data-job-open="' + esc(job.id) + '">Review</button>');
      }
      if (running) {
        buttons.push('<button type="button" class="sb-mini ml-auto" data-job-cancel="' + esc(job.id) + '">Cancel</button>');
      }
      return '<div class="sb-job-row">' +
        '<div class="flex items-center gap-1.5">' +
          '<span class="sb-job-dot sb-job-' + esc(status) + '"></span>' +
          '<span class="flex-1 min-w-0 truncate text-[10.5px] text-slate-200">' + esc(action) + " · " + esc(jobModeLabel(job)) + "</span>" +
          '<span class="text-[9px] font-mono text-slate-500">' + esc((job.elapsed_seconds || 0) + "s") + "</span>" +
        "</div>" +
        '<div class="text-[9px] text-slate-500 truncate mt-0.5" title="' + esc(detail) + '">' + esc(detail) + "</div>" +
        (buttons.length ? '<div class="flex items-center gap-1.5 mt-1">' + buttons.join("") + "</div>" : "") +
        "</div>";
    }).join("");
  }

  function attachJob(id) {
    if (state.busy || state.jobId) return;
    state.jobId = id;
    state.pollErrors = 0;
    setBusy(true, "Attaching…");
    setProgressVisible(true);
    log("attached to running job " + id);
    setTab("log");
    pollJob();
  }

  function resumeJob(id) {
    if (state.busy || state.jobId) return;
    api("/api/skill-builder/jobs/" + encodeURIComponent(id)).then(function (r) {
      if (!r.ok || !r.data || !r.data.request) {
        throw new Error((r.data && r.data.error) || "Job not found");
      }
      var request = r.data.request;
      setBusy(true, "Resubmitting…");
      setProgressVisible(true);
      renderProgress({ stages: STATE_STAGES, stage_index: 0, stage: "Validating source",
                      detail: "Resubmitting interrupted job", percent: 3, status: "queued" });
      startElapsed();
      log("resubmitting interrupted job " + id + " (" + jobModeLabel(r.data) + ")");
      setTab("log");
      return api("/api/skill-builder/jobs", { method: "POST", body: request }).then(function (res) {
        if (!res.ok || !res.data || !res.data.job) {
          throw new Error((res.data && res.data.error) || ("HTTP " + res.status));
        }
        state.jobId = res.data.job.id;
        state.pollErrors = 0;
        log("job " + state.jobId + " started");
        pollJob();
      });
    }).catch(function (e) {
      finishJob();
      showProgressError(String(e.message || e));
    });
  }

  function openJobDraft(id) {
    var job = (state.jobs || []).filter(function (j) { return j.id === id; })[0];
    if (!job || !job.result || !job.result.draft) return;
    onJobDone(job);
  }

  function cancelJob(id) {
    var target = id || state.jobId;
    if (!target) return;
    api("/api/skill-builder/jobs/" + encodeURIComponent(target) + "/cancel", { method: "POST", body: {} })
      .then(function (r) {
        if (!r.ok) log("cancel failed: " + ((r.data && r.data.error) || r.status));
        else log("cancel requested");
        loadJobs();
      }).catch(function (e) { log("cancel error: " + e); });
  }

  /* ------------------------------------------------------------------ */
  /* Review Generated Skill                                              */
  /* ------------------------------------------------------------------ */
  function reviewName() { return (($("sb-review-name") || {}).value || "").trim(); }
  function reviewDesc() { return (($("sb-review-description") || {}).value || "").trim(); }
  function reviewContent() { return (($("sb-review-content") || {}).value || ""); }

  function setReviewStatus(text, isError) {
    var el = $("sb-review-status");
    if (!el) return;
    el.classList.toggle("hidden", !text);
    el.classList.toggle("error", !!isError);
    el.textContent = text || "";
  }
  function setReviewConflict(text) {
    var el = $("sb-review-conflict");
    if (!el) return;
    el.classList.toggle("hidden", !text);
    el.textContent = text || "";
  }

  function reviewJson(value, emptyText) {
    if (!value || (typeof value === "object" && !Object.keys(value).length)) {
      return emptyText;
    }
    try { return JSON.stringify(value, null, 2); }
    catch (e) { return emptyText; }
  }

  function setReviewTab(tab) {
    state.reviewTab = tab || "skill";
    var tabs = state.view ? state.view.querySelectorAll("[data-sb-rtab]") : [];
    Array.prototype.forEach.call(tabs, function (b) {
      var active = b.getAttribute("data-sb-rtab") === state.reviewTab;
      b.classList.toggle("sb-review-tab-active", active);
      b.setAttribute("aria-selected", active ? "true" : "false");
    });
    var panels = state.view ? state.view.querySelectorAll("[data-sb-rpanel]") : [];
    Array.prototype.forEach.call(panels, function (p) {
      p.classList.toggle("active", p.getAttribute("data-sb-rpanel") === state.reviewTab);
    });
    if (state.reviewTab === "package") loadPackagePreview();
  }

  function renderReviewPanels(draft) {
    draft = draft || {};
    var panels = {
      analysis: [draft.analysis, "No structured analysis — this is a source-mode draft."],
      style: [draft.style_profile, "No separate style profile was produced."],
      format: [draft.format_profile, "No separate format profile was produced."],
      production: [draft.production, "No production plan was produced."],
      dependencies: [draft.dependencies, "No dependency manifest was produced."]
    };
    Object.keys(panels).forEach(function (key) {
      var el = $("sb-review-" + key);
      if (el) el.textContent = reviewJson(panels[key][0], panels[key][1]);
    });
    renderReviewTests(draft);
    renderPackageList(((draft.package || {}).files) || []);
  }

  function renderReviewTests(draft) {
    var host = $("sb-review-tests");
    if (!host) return;
    var validation = draft.validation || {};
    var checks = validation.checks || [];
    var replication = validation.replication || null;
    var errors = validation.package_errors || [];
    var html = "";

    html += '<div class="sb-test-head">Static checks <span class="sb-test-verdict ' +
      (validation.passed ? "ok" : "bad") + '">' +
      (validation.passed ? "passed" : (checks.length ? "not passed" : "not run")) +
      "</span></div>";
    if (!checks.length) {
      html += '<div class="sb-rail-empty">No package checks for this draft.</div>';
    } else {
      html += '<ul class="sb-test-checks">' + checks.map(function (c) {
        var fail = !c.passed;
        var soft = fail && c.required === false;
        return '<li class="' + (fail ? (soft ? "warn" : "fail") : "pass") + '">' +
          '<span class="sb-test-mark">' + (fail ? (soft ? "!" : "×") : "✓") + "</span>" +
          '<span class="flex-1 min-w-0">' + esc(c.name) +
          (c.detail ? ' <span class="sb-test-detail">' + esc(c.detail) + "</span>" : "") +
          (c.required === false ? ' <span class="sb-test-soft">recommended</span>' : "") +
          "</span></li>";
      }).join("") + "</ul>";
    }
    if (errors.length) {
      html += '<div class="sb-test-head">Package errors</div><ul class="sb-test-checks">' +
        errors.map(function (e) {
          return '<li class="fail"><span class="sb-test-mark">×</span><span>' + esc(e) + "</span></li>";
        }).join("") + "</ul>";
    }
    html += '<div class="sb-test-head">Replication test <span class="sb-test-verdict ' +
      (replication ? (replication.passed ? "ok" : "bad") : "") + '">' +
      (replication ? (replication.passed ? "passed" : "did not pass") : "not requested") +
      "</span></div>";
    if (!replication) {
      html += '<div class="sb-rail-empty">Enable “Run replication test” in Video Reverse Engineering mode to test the method on a new topic.</div>';
    } else if (replication.error) {
      html += '<div class="sb-review-warn">' + esc(replication.error) + "</div>";
    } else {
      html += '<ul class="sb-test-checks">' + (replication.criteria || []).map(function (c) {
        return '<li class="' + (c.passed ? "pass" : "fail") + '">' +
          '<span class="sb-test-mark">' + (c.passed ? "✓" : "×") + "</span>" +
          '<span class="flex-1 min-w-0">' + esc(c.id || c.name || "criterion") +
          '<span class="sb-test-detail">' + esc(c.detail || "") + "</span></span></li>";
      }).join("") + "</ul>";
      html += '<div class="sb-test-foot">Content overlap with the reference: ' +
        esc(((replication.content_overlap || 0) * 100).toFixed(1)) + "%" +
        (replication.topic ? " · topic: " + esc(replication.topic) : "") +
        (replication.model ? " · model: " + esc(replication.model) : "") + "</div>";
    }
    if (draft.warnings && draft.warnings.length) {
      html += '<div class="sb-test-head">Warnings</div><ul class="sb-test-checks">' +
        draft.warnings.map(function (w) {
          return '<li class="warn"><span class="sb-test-mark">!</span><span>' + esc(w) + "</span></li>";
        }).join("") + "</ul>";
    }
    host.innerHTML = html;
  }

  function renderPackageList(files) {
    var host = $("sb-package-list");
    if (!host) return;
    var list = files || [];
    if (!list.length) {
      host.innerHTML = '<div class="sb-rail-empty">No compiled package.</div>';
      return;
    }
    host.innerHTML = list.map(function (f) {
      return '<button type="button" class="sb-package-file" data-pkg-path="' + esc(f.path) + '">' +
        '<span class="sb-package-file-name">' + esc(f.path) + "</span>" +
        '<span class="sb-package-file-size">' + esc(f.bytes || 0) + " B</span>" +
        "</button>";
    }).join("");
  }

  function showPackageFile(path) {
    var pre = $("sb-package-preview");
    if (!pre) return;
    var files = (state.packagePreview && state.packagePreview.files) || [];
    var file = files.filter(function (f) { return f.path === path; })[0];
    if (!file) { pre.textContent = "Select a package file to preview it."; return; }
    pre.textContent = file.preview != null && file.preview !== ""
      ? file.preview
      : "(binary or empty file — " + (file.bytes || 0) + " bytes)";
    var buttons = state.view ? state.view.querySelectorAll("[data-pkg-path]") : [];
    Array.prototype.forEach.call(buttons, function (b) {
      b.classList.toggle("sb-package-file-active", b.getAttribute("data-pkg-path") === path);
    });
  }

  function loadPackagePreview() {
    if (!state.draft) return Promise.resolve();
    var id = state.draft.id;
    if (state.packagePreview && state.packagePreview.id === id) return Promise.resolve();
    var pre = $("sb-package-preview");
    return api("/api/skill-builder/package?draft=" + encodeURIComponent(id)).then(function (r) {
      if (!r.ok) {
        state.packagePreview = null;
        renderPackageList([]);
        if (pre) pre.textContent = (r.data && r.data.error) || "This draft has no compiled package.";
        return;
      }
      state.packagePreview = {
        id: id,
        root: r.data.root,
        total_bytes: r.data.total_bytes || 0,
        files: r.data.files || []
      };
      renderPackageList(state.packagePreview.files);
      if (state.packagePreview.files.length) {
        showPackageFile(state.packagePreview.files[0].path);
      } else if (pre) {
        pre.textContent = "The package is empty.";
      }
    }).catch(function (e) {
      state.packagePreview = null;
      if (pre) pre.textContent = "Could not load the package: " + (e.message || e);
    });
  }

  function updateReviewTarget() {
    var card = $("sb-review");
    if (!card || !state.draft) return;
    var vault = state.draft.vault_dir || "";
    var name = reviewName() || state.draft.name || "…";
    var target = $("sb-review-target");
    if (target) target.textContent = (vault ? vault.replace(/\/$/, "") + "/" : "") + "Skills/" + name + "/SKILL.md";
  }

  function openReview(draft, result) {
    state.draft = draft;
    state.dirty = false;
    var overlay = $("sb-review");
    if (!overlay) return;
    ($("sb-review-name") || {}).value = draft.name || "";
    ($("sb-review-description") || {}).value = draft.description || "";
    ($("sb-review-content") || {}).value = draft.content || "";
    var meta = $("sb-review-meta");
    if (meta) {
      var pkg = draft.package || {};
      meta.textContent = (draft.mode === "video" ? "Video Reverse Engineering" : "Source → Skill") +
        " · " + ((draft.sources || []).length) + " source(s) · generated by " + (draft.author || "unknown") +
        ((pkg.files || []).length ? " · package " + pkg.files.length + " file(s)" : "") +
        " · nothing saved yet";
    }
    var src = $("sb-review-source");
    if (src) src.textContent = (draft.sources || []).map(function (s) {
      return (s.title || s.url || s.id) + " [" + (s.kind || "source") + "]";
    }).join(" · ") || "—";
    var did = $("sb-review-draft");
    if (did) did.textContent = draft.id || "—";
    state.packagePreview = null;
    renderReviewPanels(draft);
    setReviewTab("skill");
    setReviewConflict(draft.conflict
      ? "A skill named '" + draft.name + "' already exists. Rename it, or Add to Vault to confirm replacement."
      : "");
    setReviewStatus("", false);
    var saveState = $("sb-review-save-state");
    if (saveState) saveState.textContent = "Not saved yet — changes auto-save as a draft";
    updateReviewTarget();
    var publish = $("sb-review-publish");
    if (publish) { publish.disabled = false; publish.innerHTML = '<i data-lucide="vault" class="w-3.5 h-3.5"></i><span>Add to Vault</span>'; }
    var reject = $("sb-review-reject");
    if (reject) { reject.disabled = false; reject.innerHTML = '<i data-lucide="trash-2" class="w-3.5 h-3.5"></i><span>Reject</span>'; }
    overlay.classList.remove("hidden");
    icons();
  }

  function closeReview(force) {
    if (!force && state.dirty) {
      if (!window.confirm("Close the review? The draft is kept on the server, but unsaved field edits will be lost.")) return;
    }
    if (state.saveTimer) window.clearTimeout(state.saveTimer);
    state.saveTimer = null;
    state.dirty = false;
    var overlay = $("sb-review");
    if (overlay) overlay.classList.add("hidden");
  }

  function validateReview() {
    var name = reviewName();
    if (!/^[a-z0-9][a-z0-9._-]{0,63}$/.test(name)) {
      return "Skill name must start with a lowercase letter or number and contain only letters, numbers, dots, dashes or underscores (max 64).";
    }
    if (reviewDesc().length < 10) return "Write a description of at least 10 characters.";
    if (reviewDesc().length > 500) return "Keep the description under 500 characters.";
    if (reviewContent().trim().length < 40) return "The generated skill content is too short to publish.";
    if (reviewContent().length > 512 * 1024) return "That skill is larger than the 512 KB limit.";
    return "";
  }

  function scheduleSave() {
    state.dirty = true;
    updateReviewTarget();
    var saveState = $("sb-review-save-state");
    if (saveState) saveState.textContent = "Unsaved edits…";
    if (state.saveTimer) window.clearTimeout(state.saveTimer);
    state.saveTimer = window.setTimeout(saveDraft, 900);
  }

  function saveDraft() {
    if (!state.draft || state.saving || state.publishing) return Promise.resolve(false);
    state.saveTimer = null;
    var id = state.draft.id;
    var payload = { name: reviewName(), description: reviewDesc(), content: reviewContent() };
    state.saving = true;
    return api("/api/skill-builder/drafts/" + encodeURIComponent(id), { method: "POST", body: payload })
      .then(function (r) {
        state.saving = false;
        if (!r.ok) {
          setReviewStatus("Draft not saved: " + ((r.data && r.data.error) || r.status), true);
          return false;
        }
        state.draft = r.data;
        state.dirty = false;
        state.lastSaved = new Date().toLocaleTimeString();
        var saveState = $("sb-review-save-state");
        if (saveState) saveState.textContent = "Draft saved " + state.lastSaved;
        setReviewConflict(r.data.conflict
          ? "A skill named '" + r.data.name + "' already exists. Rename it, or Add to Vault to confirm replacement."
          : "");
        setReviewStatus("", false);
        updateReviewTarget();
        return true;
      })
      .catch(function (e) {
        state.saving = false;
        setReviewStatus("Draft not saved: " + e, true);
        return false;
      });
  }

  function rejectDraft() {
    if (!state.draft || state.publishing) return;
    var msg = "Reject this generated draft? It will be deleted from staging. " +
      "Approved skills in the Vault are not touched.";
    if (!window.confirm(msg)) return;
    var id = state.draft.id;
    var reject = $("sb-review-reject");
    if (reject) reject.disabled = true;
    api("/api/skill-builder/drafts/" + encodeURIComponent(id) + "/reject", { method: "POST", body: {} })
      .then(function (r) {
        if (!r.ok) {
          setReviewStatus("Could not reject: " + ((r.data && r.data.error) || r.status), true);
          if (reject) reject.disabled = false;
          return;
        }
        log("rejected draft " + id + " — nothing was written to the Vault");
        state.draft = null;
        state.dirty = false;
        closeReview(true);
        setPane("skill", "Draft rejected. Nothing was published.");
        setTab("skill");
        setReviewStatus("", false);
        loadSkills();
      })
      .catch(function (e) {
        setReviewStatus("Could not reject: " + e, true);
        if (reject) reject.disabled = false;
      });
  }

  function publishDraft(overwrite) {
    if (!state.draft || state.publishing) return;
    var problem = validateReview();
    if (problem) { setReviewStatus(problem, true); return; }
    var id = state.draft.id;
    var name = reviewName();
    var body = {
      name: name,
      description: reviewDesc(),
      content: reviewContent(),
      overwrite: !!overwrite
    };
    state.publishing = true;
    var publish = $("sb-review-publish");
    var reject = $("sb-review-reject");
    if (publish) { publish.disabled = true; publish.innerHTML = '<i data-lucide="loader-circle" class="w-3.5 h-3.5 animate-spin"></i><span>Saving…</span>'; }
    if (reject) reject.disabled = true;
    setReviewStatus("Validating and writing to the Vault…", false);
    icons();

    api("/api/skill-builder/drafts/" + encodeURIComponent(id) + "/publish", { method: "POST", body: body })
      .then(function (r) {
        if (!r.ok) {
          state.publishing = false;
          if (publish) { publish.disabled = false; publish.innerHTML = '<i data-lucide="vault" class="w-3.5 h-3.5"></i><span>Add to Vault</span>'; }
          if (reject) reject.disabled = false;
          var err = (r.data && r.data.error) || ("HTTP " + r.status);
          if (r.status === 409 || (r.data && r.data.conflict)) {
            setReviewConflict("A skill named '" + name + "' already exists. Change the name, or confirm replacement to overwrite it.");
            if (window.confirm("A skill named '" + name + "' already exists. Replace it? This overwrites only that approved skill.")) {
              publishDraft(true);
            } else {
              setReviewStatus("Not published. Rename the skill or confirm replacement.", true);
            }
            return;
          }
          setReviewStatus("Publishing failed: " + err + " — your edits are preserved. You can retry without re-extracting.", true);
          return;
        }
        state.publishing = false;
        var saved = r.data;
        state.draft = null;
        state.dirty = false;
        if (publish) { publish.disabled = true; publish.innerHTML = '<i data-lucide="check" class="w-3.5 h-3.5"></i><span>Saved</span>'; }
        if (reject) { reject.disabled = false; reject.innerHTML = '<i data-lucide="x" class="w-3.5 h-3.5"></i><span>Close</span>'; }
        var saveState = $("sb-review-save-state");
        if (saveState) saveState.textContent = "Verified write";
        setReviewConflict("");
        setReviewStatus("Saved and verified: " + (saved.absolute_path || saved.path) +
          " (" + (saved.bytes || 0) + " bytes)" + (saved.replaced ? " — replaced an existing skill" : ""), false);
        log("published '" + saved.name + "' → " + (saved.path || saved.absolute_path) + " (verified)");
        setPane("skill", r.data.content || reviewContent());
        loadSkills();
        loadStatus();
      })
      .catch(function (e) {
        state.publishing = false;
        if (publish) { publish.disabled = false; publish.innerHTML = '<i data-lucide="vault" class="w-3.5 h-3.5"></i><span>Add to Vault</span>'; }
        if (reject) reject.disabled = false;
        setReviewStatus("Publishing failed: " + e + " — your edits are preserved.", true);
      });
  }

  /* ------------------------------------------------------------------ */
  /* wiring                                                              */
  /* ------------------------------------------------------------------ */
  function init() {
    state.view = $("skill-builder-view");
    if (!state.view || state.initialized) return;
    state.initialized = true;

    var analyzeBtn = $("sb-analyze");
    if (analyzeBtn) analyzeBtn.addEventListener("click", function () { submitJob("analyze"); });
    var buildBtn = $("sb-build");
    if (buildBtn) buildBtn.addEventListener("click", function () { submitJob("generate"); });
    var setupBtn = $("sb-setup");
    if (setupBtn) setupBtn.addEventListener("click", runSetup);
    var refreshBtn = $("sb-skills-refresh");
    if (refreshBtn) refreshBtn.addEventListener("click", function () { loadStatus(); loadSkills(); loadJobs(); });
    var cancelBtn = $("sb-progress-cancel");
    if (cancelBtn) cancelBtn.addEventListener("click", function () { cancelJob(); });
    var jobsRefresh = $("sb-jobs-refresh");
    if (jobsRefresh) jobsRefresh.addEventListener("click", function () { loadJobs(); });
    var jobsHost = $("sb-jobs");
    if (jobsHost) jobsHost.addEventListener("click", function (e) {
      var attach = e.target.closest("[data-job-attach]");
      if (attach) { attachJob(attach.getAttribute("data-job-attach")); return; }
      var resume = e.target.closest("[data-job-resume]");
      if (resume) { resumeJob(resume.getAttribute("data-job-resume")); return; }
      var open = e.target.closest("[data-job-open]");
      if (open) { openJobDraft(open.getAttribute("data-job-open")); return; }
      var cancel = e.target.closest("[data-job-cancel]");
      if (cancel) cancelJob(cancel.getAttribute("data-job-cancel"));
    });

    var modeButtons = state.view.querySelectorAll(".sb-mode");
    Array.prototype.forEach.call(modeButtons, function (b) {
      b.addEventListener("click", function () { setMode(b.getAttribute("data-sb-mode")); });
    });
    var objectiveInputs = state.view.querySelectorAll('input[name="sb-objective"]');
    Array.prototype.forEach.call(objectiveInputs, function (input) {
      input.addEventListener("change", function () {
        state.objective = getObjective();
        syncCharacterField();
        syncObjectiveNote();
      });
    });
    var replicateInput = $("sb-replicate");
    if (replicateInput) replicateInput.addEventListener("change", syncReplicationField);
    syncCharacterField();
    syncObjectiveNote();
    syncReplicationField();

    // Agent dropdown
    var agentBtn = $("sb-agent-btn");
    if (agentBtn) agentBtn.addEventListener("click", function (e) { e.stopPropagation(); toggleMenu("sb-agent-menu", "sb-agent-btn"); });
    var agentOpts = $("sb-agent-options");
    if (agentOpts) agentOpts.addEventListener("click", function (e) {
      var item = e.target.closest("[data-agent]");
      if (!item || item.disabled) return;
      var id = item.getAttribute("data-agent");
      var agent = state.agents.filter(function (a) { return a.id === id; })[0];
      state.agentId = id;
      state.modelId = null;
      state.modelQuery = "";
      state.providerId = null;
      state.providerLocked = false;
      state.providers = [];
      state.providerSetup = null;
      setAgentLabel(); renderAgents(); closeMenus(); loadModels(false);
      if (agent && agent.registered === false) integrateAgent(id, agent.name);
    });

    // Provider dropdown
    var providerBtn = $("sb-provider-btn");
    if (providerBtn) providerBtn.addEventListener("click", function (e) {
      e.stopPropagation();
      var wasHidden = $("sb-provider-menu").classList.contains("hidden");
      toggleMenu("sb-provider-menu", "sb-provider-btn");
      if (wasHidden) { renderProviders(); setProviderLabel(); }
    });
    var providerOpts = $("sb-provider-options");
    if (providerOpts) providerOpts.addEventListener("click", function (e) {
      var item = e.target.closest("[data-provider]");
      if (!item || item.disabled) return;
      // selectProvider opens the model menu; keep this click away from the
      // document handler that would immediately close it again.
      e.stopPropagation();
      var id = item.getAttribute("data-provider");
      selectProvider(id === "__all__" ? null : id);
    });
    var providerRefresh = $("sb-provider-refresh");
    if (providerRefresh) providerRefresh.addEventListener("click", function (e) {
      e.stopPropagation();
      loadModels(true);
    });
    var providerSetup = $("sb-provider-setup");
    if (providerSetup) providerSetup.addEventListener("click", function (e) {
      if (!e.target.closest("#sb-provider-copy")) return;
      e.stopPropagation();
      var cmd = (state.providerSetup && state.providerSetup.command) || "";
      if (!cmd) return;
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(cmd).then(function () {
          setModelNote("Copied: " + cmd);
        }, function () {});
      }
    });

    // Model dropdown
    var modelBtn = $("sb-model-btn");
    if (modelBtn) modelBtn.addEventListener("click", function (e) {
      e.stopPropagation();
      var wasHidden = $("sb-model-menu").classList.contains("hidden");
      toggleMenu("sb-model-menu", "sb-model-btn");
      if (wasHidden) focusModelSearch();
    });
    var modelOpts = $("sb-model-options");
    if (modelOpts) modelOpts.addEventListener("click", function (e) {
      var item = e.target.closest("[data-model]");
      if (!item || item.disabled) return;
      state.modelId = item.getAttribute("data-model");
      state.modelQuery = "";
      setModelLabel(); renderModels(); closeMenus();
    });
    var modelRefresh = $("sb-model-refresh");
    if (modelRefresh) modelRefresh.addEventListener("click", function (e) { e.stopPropagation(); loadModels(true); });
    var modelSearch = $("sb-model-search");
    if (modelSearch) {
      modelSearch.addEventListener("input", function (e) {
        state.modelQuery = e.target.value || "";
        renderModels();
      });
      modelSearch.addEventListener("click", function (e) { e.stopPropagation(); });
    }

    var tabs = state.view.querySelectorAll(".sb-tab");
    Array.prototype.forEach.call(tabs, function (t) {
      t.addEventListener("click", function () { setTab(t.getAttribute("data-sb-tab")); });
    });

    var reviewTabs = state.view.querySelectorAll("[data-sb-rtab]");
    Array.prototype.forEach.call(reviewTabs, function (t) {
      t.addEventListener("click", function () { setReviewTab(t.getAttribute("data-sb-rtab")); });
    });
    var packageList = $("sb-package-list");
    if (packageList) packageList.addEventListener("click", function (e) {
      var file = e.target.closest("[data-pkg-path]");
      if (file) showPackageFile(file.getAttribute("data-pkg-path"));
    });

    var skillsHost = $("sb-skills");
    if (skillsHost) skillsHost.addEventListener("click", function (e) {
      var v = e.target.closest("[data-view]");
      if (v) { viewSkill(v.getAttribute("data-view")); return; }
      var i = e.target.closest("[data-install]");
      if (i) installSkill(i.getAttribute("data-install"));
    });

    // Review fields: autosave the draft after edits settle.
    ["sb-review-name", "sb-review-description", "sb-review-content"].forEach(function (id) {
      var el = $(id);
      if (!el) return;
      el.addEventListener("input", scheduleSave);
    });
    var closeBtn = $("sb-review-close");
    if (closeBtn) closeBtn.addEventListener("click", function () { closeReview(false); });
    var rejectBtn = $("sb-review-reject");
    if (rejectBtn) rejectBtn.addEventListener("click", function () {
      if (!state.draft) { closeReview(true); return; }
      rejectDraft();
    });
    var publishBtn = $("sb-review-publish");
    if (publishBtn) publishBtn.addEventListener("click", function () { publishDraft(false); });
    var overlay = $("sb-review");
    if (overlay) overlay.addEventListener("keydown", function (e) {
      if (e.key === "Escape") closeReview(false);
    });

    document.addEventListener("click", function (e) {
      var modelMenu = $("sb-model-menu");
      if (modelMenu && !modelMenu.classList.contains("hidden") && modelMenu.contains(e.target)) return;
      var providerMenu = $("sb-provider-menu");
      if (providerMenu && !providerMenu.classList.contains("hidden") && providerMenu.contains(e.target)) return;
      var agentMenu = $("sb-agent-menu");
      if (agentMenu && !agentMenu.classList.contains("hidden") && agentMenu.contains(e.target)) return;
      closeMenus();
    });

    setMode("source");
    setTab("transcript");
    loadStatus();
    loadSkills();
    loadJobs();
    loadAgents();
    icons();
  }

  function refresh() {
    if (!state.initialized) init();
    if (!state.view) return;
    loadStatus();
    loadSkills();
    loadJobs();
    loadAgents();
    icons();
  }

  function open() {
    if (window.switchMainView) window.switchMainView("skill-builder");
    else refresh();
  }

  window.SkillBuilder = {
    init: init,
    refresh: refresh,
    open: open,
    setMode: setMode,
    state: state
  };

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
