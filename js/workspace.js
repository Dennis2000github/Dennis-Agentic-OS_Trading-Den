/*
 * Workspace agent screen (DENEVO / HERMES Agentic OS).
 *
 * One dashboard per project session: its own xterm instance, WebSocket and
 * agent process (see AgentDashboards). Selecting an agent shows that agent's
 * dashboard and hides the others without destroying them. The Code & Preview
 * panel is driven by the S0-S6 state machine in PanelState.
 *
 * Defensive by design: when its DOM contract is absent it does nothing.
 */
(function () {
  "use strict";

  var XTERM_VERSION = "5.3.0";
  var CDN = "https://cdn.jsdelivr.net/npm/";
  var XTERM_CSS = CDN + "xterm@" + XTERM_VERSION + "/css/xterm.css";
  var XTERM_JS = CDN + "xterm@" + XTERM_VERSION + "/lib/xterm.js";
  var FIT_JS = CDN + "xterm-addon-fit@0.8.0/lib/xterm-addon-fit.js";
  var UNICODE_JS = CDN + "xterm-addon-unicode11@0.6.0/lib/xterm-addon-unicode11.js";
  var WEBGL_JS = CDN + "xterm-addon-webgl@0.16.0/lib/xterm-addon-webgl.js";

  var MAX_AUTO_OPEN_MODELS = 12;   // show search above this many
  var HANDOFF_ANIM_MS = 220;

  var state = {
    initDone: false,
    token: null,
    agents: [],
    projects: [],
    sessions: [],
    sessionsLoadId: 0,
    agentId: null,
    modelId: null,
    sessionId: null,
    current: null,
    panel: null,
    models: { status: "idle", items: [], error: null, current: null, liveSwitch: false, forAgent: null },
    modelQuery: "",
    ready: null,
    readyNote: null,
    screen: { starting: false, exited: false, hasSession: false },
    handoff: {},          // sessionId -> {burst, suppressed}
    pendingFullPane: null,
    lastFocusButton: null,
    resizeTimer: null,
    runTimer: null,
    autoPreviewed: null,
  };

  var registry = null;
  var views = {};         // sessionId -> view facade (sockets, queues)

  function $(id) { return document.getElementById(id); }
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function ready(fn) {
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", fn);
    else fn();
  }
  function icons() {
    if (window.lucide && window.lucide.createIcons) window.lucide.createIcons();
  }
  function reducedMotion() {
    return window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
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

  // -- transport ---------------------------------------------------------
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

  function wsUrl(sessionId) {
    var proto = location.protocol === "https:" ? "wss" : "ws";
    return proto + "://" + location.host + "/api/ws/terminal?session_id=" +
      encodeURIComponent(sessionId) + "&token=" + encodeURIComponent(state.token);
  }

  // -- xterm bootstrap ---------------------------------------------------
  var xtermPromise = null;
  function loadScript(src) {
    return new Promise(function (resolve, reject) {
      var s = document.createElement("script");
      s.src = src;
      s.onload = resolve;
      s.onerror = function () { reject(new Error("failed to load " + src)); };
      document.head.appendChild(s);
    });
  }

  function ensureXterm() {
    if (xtermPromise) return xtermPromise;
    var css = document.createElement("link");
    css.rel = "stylesheet";
    css.href = XTERM_CSS;
    document.head.appendChild(css);
    xtermPromise = loadScript(XTERM_JS).then(function () {
      return loadScript(FIT_JS).catch(function () {});
    }).then(function () {
      return loadScript(UNICODE_JS).catch(function () {});
    }).then(function () {
      return loadScript(WEBGL_JS).catch(function () {});
    });
    return xtermPromise;
  }

  /* One view facade per session: terminal + socket + process wiring. */
  function createView(meta) {
    var sessionId = meta.sessionId;
    var host = meta.host;
    var view = {
      sessionId: sessionId,
      agentId: meta.agentId,
      element: null,
      term: null,
      fit: null,
      ws: null,
      mounted: false,
      firstOutput: false,
      startedAt: 0,
      onStatus: null,
      onExit: null,
      onHandoff: null,
    };

    view.element = document.createElement("div");
    view.element.className = "ws-dash absolute inset-0 hidden";
    view.element.setAttribute("data-session-id", sessionId);
    if (host) host.appendChild(view.element);

    view.show = function () {
      view.element.classList.remove("hidden");
      view.fitNow();
    };
    view.hide = function () {
      view.element.classList.add("hidden");
    };
    view.focus = function () {
      if (view.term) view.term.focus();
    };
    view.resize = function () { view.fitNow(); };
    view.sendInput = function (data) {
      if (view.ws && view.ws.readyState === 1) {
        view.ws.send(JSON.stringify({ type: "input", data: data }));
        return true;
      }
      return false;
    };
    view.write = function (chunk) { if (view.term) view.term.write(chunk); };
    view.dispose = function () {
      if (view.ws) { try { view.ws.close(); } catch (e) {} view.ws = null; }
      if (view.term) { try { view.term.dispose(); } catch (e) {} view.term = null; }
      if (view.element && view.element.parentNode) view.element.parentNode.removeChild(view.element);
      view.mounted = false;
    };

    view.fitNow = function () {
      if (!view.term || view.element.classList.contains("hidden")) return;
      if (view.element.clientWidth < 4 || view.element.clientHeight < 4) return;
      try { if (view.fit) view.fit.fit(); } catch (e) { /* host not measurable yet */ }
      if (view.ws && view.ws.readyState === 1 && view.term) {
        view.ws.send(JSON.stringify({ type: "resize", rows: view.term.rows, cols: view.term.cols }));
      }
    };

    view.mount = function () {
      if (view.mounted) return Promise.resolve(view);
      return ensureXterm().then(function () {
        if (!window.Terminal) throw new Error("terminal unavailable");
        view.term = new window.Terminal({
          fontFamily: "'JetBrains Mono', Menlo, Monaco, monospace",
          fontSize: 12.5,
          cursorBlink: true,
          convertEol: false,
          allowProposedApi: true,
          scrollback: 10000,
          theme: {
            background: "#050811",
            foreground: "#e2e8f0",
            cursor: "#00f0ff",
            selectionBackground: "rgba(0, 240, 255, 0.25)",
          },
        });
        if (window.FitAddon && window.FitAddon.FitAddon) {
          view.fit = new window.FitAddon.FitAddon();
          view.term.loadAddon(view.fit);
        }
        if (window.Unicode11Addon && window.Unicode11Addon.Unicode11Addon) {
          try {
            view.term.loadAddon(new window.Unicode11Addon.Unicode11Addon());
            if (view.term.unicode) view.term.unicode.activeVersion = "11";
          } catch (e) { /* keep the built-in unicode version */ }
        }
        if (window.WebglAddon && window.WebglAddon.WebglAddon) {
          try {
            view.term.loadAddon(new window.WebglAddon.WebglAddon());
          } catch (e) { /* falls back to the canvas/DOM renderer */ }
        }
        view.term.open(view.element);
        view.term.onData(function (data) {
          // Always this session's own process, never another dashboard's.
          if (registry) registry.inputTo(sessionId, data);
        });
        view.mounted = true;
        if (registry) registry.markMounted(sessionId);
        view.fitNow();
        return view;
      });
    };

    view.connect = function () {
      if (view.ws && (view.ws.readyState === 0 || view.ws.readyState === 1)) return;
      var ws;
      try { ws = new WebSocket(wsUrl(sessionId)); } catch (e) { return; }
      view.ws = ws;
      ws.onmessage = function (ev) {
        var msg;
        try { msg = JSON.parse(ev.data); } catch (e) { return; }
        if (msg.type === "data" || msg.type === "replay") {
          if (!view.firstOutput && msg.type === "data") {
            view.firstOutput = true;
            if (view.onFirstOutput) view.onFirstOutput();
          }
          if (registry) registry.write(sessionId, msg.data || "");
          else view.write(msg.data || "");
        } else if (msg.type === "status") {
          if (view.onStatus) view.onStatus(msg.status);
        } else if (msg.type === "exit") {
          if (view.onExit) view.onExit(msg.code);
        } else if (msg.type === "handoff") {
          if (view.onHandoff) view.onHandoff(msg);
        }
      };
      ws.onopen = function () { view.fitNow(); };
      ws.onclose = function () { if (view.ws === ws) view.ws = null; };
    };

    view.closeSocket = function () {
      if (view.ws) { try { view.ws.close(); } catch (e) {} view.ws = null; }
    };

    return view;
  }

  // -- agents ------------------------------------------------------------
  function loadAgents() {
    return api("/api/agents/availability").then(function (r) {
      state.agents = (r.data && r.data.agents) || [];
      if (r.data && r.data.backend_note) state.readyNote = r.data.backend_note;
      renderAgents();
      updateAgentLabel();
    });
  }

  function agentById(id) {
    return state.agents.filter(function (a) { return a.id === id; })[0] || null;
  }

  function renderAgents() {
    var host = $("ws-agent-options");
    if (!host) return;
    host.innerHTML = "";
    state.agents.forEach(function (a) {
      var btn = document.createElement("button");
      btn.className = "ws-menu-item" + (a.id === state.agentId ? " ws-menu-item-active" : "") +
        (a.available ? "" : " ws-menu-item-disabled");
      btn.innerHTML =
        '<span class="ws-agent-dot" style="background:' + (a.available ? "#10b981" : "#ef4444") + '"></span>' +
        '<span class="flex-1 text-left truncate">' + esc(a.name) + "</span>" +
        '<span class="text-[10px] font-mono text-slate-500 shrink-0">' +
        (a.session_count ? a.session_count + (a.session_count === 1 ? " session" : " sessions") : "") + "</span>";
      btn.title = a.available ? (a.version || "Available") : (a.reason || "Unavailable");
      if (a.available) {
        btn.addEventListener("click", function () { selectAgent(a.id); closeMenus(); });
      }
      host.appendChild(btn);
    });
    icons();
  }

  function updateAgentLabel() {
    var a = agentById(state.agentId);
    var el = $("ws-agent-label");
    if (el) el.textContent = "Agent: " + (a ? a.name : "\u2014");
  }

  function selectAgent(id) {
    var agent = agentById(id);
    if (!agent || !agent.available) return;
    if (state.agentId === id && state.sessionId) return;
    state.agentId = id;
    state.sessionId = null;
    state.current = null;
    state.modelId = null;
    state.models = { status: "idle", items: [], error: null, current: null, liveSwitch: false, forAgent: id };
    state.panel = PanelState.agentSelected(state.panel || PanelState.defaultPanel());
    updateAgentLabel();
    renderAgents();
    renderProjectMenu();
    resetScreen();
    renderPanel();
    loadModels(false);
    loadSessions().then(function () {
      // Start the agent's own dashboard using its last open project session.
      if (state.agentId !== id) return;
      var latest = state.sessions[0];
      if (latest) openSession(latest.id, { auto: true });
      else {
        state.screen.hasSession = false;
        renderScreen();
      }
    });
  }

  // -- screen state ------------------------------------------------------
  function dashboardsHost() { return $("ws-screens"); }

  function ensureRegistry() {
    if (registry) return registry;
    registry = window.AgentDashboards.createRegistry({ createView: createView });
    return registry;
  }

  function resetScreen() {
    state.screen = { starting: false, exited: false, hasSession: false };
    if (registry) registry.select(null);
    renderScreen();
  }

  function renderScreen() {
    var empty = $("ws-screen-empty");
    var starting = $("ws-starting");
    var exited = $("ws-exited");
    var hasAgent = !!state.agentId;
    var hasSession = state.screen.hasSession && !!state.sessionId;
    if (empty) {
      empty.classList.toggle("hidden", hasAgent && (hasSession || state.screen.starting || state.screen.exited));
      empty.innerHTML = hasAgent
        ? '<span class="text-[12px] text-slate-400">No project session yet for this agent.</span>' +
          '<span class="text-[11px] text-slate-500">Use <b>+ New</b> in the Sessions list to start one.</span>'
        : '<span class="text-[13px] font-semibold text-slate-300">No agent selected</span>' +
          '<span class="text-[11px] text-slate-500">Pick an agent to open its CLI dashboard.</span>';
    }
    if (starting) starting.classList.toggle("hidden", !state.screen.starting);
    if (exited) exited.classList.toggle("hidden", !state.screen.exited);
    var screen = $("ws-screen");
    if (screen) screen.setAttribute("data-state", hasAgent ? (state.screen.starting ? "starting" : state.screen.exited ? "exited" : hasSession ? "live" : "empty") : "no-agent");
  }

  function setStatus(s) {
    var el = $("ws-status");
    if (!el) return;
    var map = {
      running: ["Running", "#10b981"],
      busy: ["Busy", "#f59e0b"],
      idle: ["Idle", "#38bdf8"],
      stopped: ["Stopped", "#64748b"],
      starting: ["Starting", "#a78bfa"],
    };
    var v = map[s] || map.stopped;
    el.setAttribute("data-status", s);
    el.innerHTML = '<span class="w-2 h-2 rounded-full" style="background:' + v[1] + '"></span><span>' + v[0] + "</span>";
    updateModelAvailability();
  }

  function note(text) {
    var el = $("ws-note");
    if (!el) return;
    el.textContent = text || "";
    el.classList.toggle("hidden", !text);
  }

  // -- models (CLI-native) ----------------------------------------------
  function projectPathForModels() {
    if (state.current && state.current.project_path) return state.current.project_path;
    return state.projects[0] ? state.projects[0].path : "";
  }

  function loadModels(refresh) {
    if (!state.agentId) { renderModels(); return Promise.resolve(); }
    var forAgent = state.agentId;
    var forProject = projectPathForModels();
    state.models = { status: "loading", items: [], error: null, current: state.models.current, liveSwitch: false, forAgent: forAgent };
    renderModels();
    var q = "?refresh=" + (refresh ? "1" : "0") +
      (forProject ? "&project=" + encodeURIComponent(forProject) : "") +
      (state.sessionId ? "&session_id=" + encodeURIComponent(state.sessionId) : "");
    return api("/api/agents/" + forAgent + "/models" + q).then(function (r) {
      if (state.agentId !== forAgent) return;
      if (!r.ok) {
        state.models = { status: "error", items: [], error: (r.data && r.data.error) || "Couldn't load models for this agent.", current: state.models.current, liveSwitch: false, forAgent: forAgent };
        renderModels();
        return;
      }
      var items = (r.data && r.data.models) || [];
      state.models = {
        status: items.length ? "loaded" : "empty",
        items: items,
        error: null,
        current: (r.data && r.data.current_model) || null,
        liveSwitch: !!(r.data && r.data.supports_live_model_switch),
        forAgent: forAgent,
      };
      if (!state.modelId || !items.some(function (m) { return m.id === state.modelId; })) {
        state.modelId = state.models.current ||
          (function () {
            var def = items.filter(function (m) { return m.is_default; })[0] ||
              items.filter(function (m) { return m.available; })[0] || items[0];
            return def ? def.id : null;
          })();
      }
      renderModels();
      updateModelLabel();
    }).catch(function () {
      if (state.agentId !== forAgent) return;
      state.models = { status: "error", items: [], error: "Couldn't load models for this agent.", current: state.models.current, liveSwitch: false, forAgent: forAgent };
      renderModels();
    });
  }

  function modelName(id) {
    var m = state.models.items.filter(function (x) { return x.id === id; })[0];
    return m ? (m.name || m.id) : (id || null);
  }

  function renderModels() {
    var host = $("ws-model-options");
    var currentEl = $("ws-model-current");
    var searchWrap = $("ws-model-search-wrap");
    var searchInput = $("ws-model-search");
    if (!host) return;
    host.innerHTML = "";
    if (searchWrap) {
      searchWrap.classList.toggle("hidden", !(state.models.status === "loaded" && state.models.items.length > MAX_AUTO_OPEN_MODELS));
    }
    if (searchInput && searchInput.value !== state.modelQuery) searchInput.value = state.modelQuery;

    if (!state.agentId) {
      host.innerHTML = '<div class="px-3 py-2 text-[11px] text-slate-500">Select an agent first.</div>';
      if (currentEl) currentEl.textContent = "";
      return;
    }
    if (state.models.status === "loading") {
      host.innerHTML = '<div class="px-3 py-2 text-[11px] text-slate-500">Loading models\u2026</div>';
      if (currentEl) currentEl.textContent = "";
      return;
    }
    if (state.models.status === "error") {
      var err = document.createElement("div");
      err.className = "px-3 py-2 text-[11px] text-rose-300 space-y-2";
      err.innerHTML = "<div>" + esc(state.models.error || "Couldn't load models for this agent.") + "</div>";
      var retry = document.createElement("button");
      retry.className = "px-2 py-1 rounded-md bg-rose-950/60 border border-rose-700/50 text-rose-200 text-[11px]";
      retry.textContent = "Retry";
      retry.addEventListener("click", function (e) { e.stopPropagation(); loadModels(true); });
      err.appendChild(retry);
      host.appendChild(err);
      if (currentEl) currentEl.textContent = "";
      return;
    }
    if (currentEl) {
      var current = state.models.current || state.modelId;
      currentEl.textContent = current ? ("Current: " + (modelName(current) || current)) : "";
      currentEl.classList.toggle("hidden", !current);
    }
    if (state.models.status === "empty") {
      host.innerHTML = '<div class="px-3 py-2 text-[11px] text-slate-500">This CLI reported no models.</div>';
      return;
    }
    var query = state.modelQuery.toLowerCase();
    var shown = state.models.items.filter(function (m) {
      if (!query) return true;
      return String(m.name || m.id).toLowerCase().indexOf(query) !== -1 ||
        String(m.id).toLowerCase().indexOf(query) !== -1;
    });
    if (!shown.length) {
      host.innerHTML = '<div class="px-3 py-2 text-[11px] text-slate-500">No matching model.</div>';
      return;
    }
    shown.forEach(function (m) {
      var isCurrent = (state.models.current || state.modelId) === m.id;
      var btn = document.createElement("button");
      btn.className = "ws-menu-item" + (isCurrent ? " ws-menu-item-active" : "") +
        (m.available ? "" : " ws-menu-item-disabled");
      btn.innerHTML =
        '<span class="flex-1 text-left truncate">' + esc(m.name || m.id) + "</span>" +
        (m.is_default ? '<span class="text-[9px] font-mono text-cyan-400 shrink-0">default</span>' : "") +
        (m.available ? "" : '<span class="text-[10px] text-amber-400 shrink-0">' +
          esc(m.reason || "unavailable") + "</span>");
      if (m.description) btn.title = m.description;
      if (m.available) btn.addEventListener("click", function () { selectModel(m.id); closeMenus(); });
      host.appendChild(btn);
    });
  }

  function updateModelLabel() {
    var el = $("ws-model-label");
    if (!el) return;
    var current = state.models.current || state.modelId;
    el.textContent = "Model: " + (current ? (modelName(current) || current) : "\u2014");
  }

  /* Disabled only until an agent is chosen or while the session starts. Busy
   * does NOT disable it: the CLI status flips constantly (e.g. OpenCode redraws
   * its TUI), so disabling here made the control inconsistent and unreachable.
   * Picking a model while Busy still goes through the backend 409 confirmation
   * (selectModel), which restarts the session with the chosen model. */
  function modelDisabledReason() {
    if (!state.agentId) return "Select an agent first.";
    if (state.screen.starting) return "Agent is starting.";
    return "";
  }
  function updateModelAvailability() {
    var btn = $("ws-model-btn");
    if (!btn) return;
    var reason = modelDisabledReason();
    var disabled = !!reason;
    btn.disabled = disabled;
    btn.classList.toggle("ws-dropdown-disabled", disabled);
    btn.title = disabled ? reason : "Model for this CLI";
    if (disabled && $("ws-model-menu")) $("ws-model-menu").classList.add("hidden");
  }

  function selectModel(id) {
    var m = state.models.items.filter(function (x) { return x.id === id; })[0];
    if (!m || !m.available) return;
    if (!state.sessionId) {  // applied when a session starts
      state.modelId = id;
      updateModelLabel();
      renderModels();
      return;
    }
    var previous = state.models.current || state.current && state.current.model_id || null;
    api("/api/project-sessions/" + state.sessionId + "/model", { method: "POST", body: { model_id: id } })
      .then(function (r) {
        if (r.status === 409 && r.data && r.data.warning) {
          var go = window.confirm(r.data.message + "\n\nContinue?");
          if (!go) { revertModel(previous); return; }
          api("/api/project-sessions/" + state.sessionId + "/model", { method: "POST", body: { model_id: id, confirm: true } })
            .then(afterModelChange, function () { revertModel(previous); });
          return;
        }
        afterModelChange(r);
      }, function () { revertModel(previous); });
    function afterModelChange(r) {
      if (!r.ok) {
        revertModel(previous);
        note((r.data && r.data.error) || "Couldn't switch model. Keeping the previous one.");
        return;
      }
      var applied = r.data.current_model || r.data.model_id || id;
      state.models.current = applied;
      state.modelId = applied;
      if (state.current) state.current.model_id = applied;
      updateModelLabel();
      renderModels();
      setStatus(r.data.status || "running");
      if (!r.data.current_model) {
        note("Model saved. It applies when the session starts.");
      } else {
        // The CLI was relaunched with the new model. Resuming would make most
        // CLIs restore their recorded model, so this starts a fresh conversation.
        note("Model applied. The CLI restarted with the new model.");
      }
    }
    function revertModel(previous) {
      state.modelId = previous;
      updateModelLabel();
      renderModels();
    }
  }

  // -- projects ----------------------------------------------------------
  function loadProjects() {
    return api("/api/projects").then(function (r) {
      state.projects = (r.data && r.data.projects) || [];
      renderProjectMenu();
    });
  }

  function renderProjectMenu() {
    var host = $("ws-new-project-menu");
    if (!host || !host.classList) return;
    host.innerHTML = "";
    if (!state.agentId) {
      host.innerHTML = '<div class="px-3 py-2 text-[11px] text-slate-500">Select an agent first.</div>';
      return;
    }
    var title = document.createElement("div");
    title.className = "ws-menu-title";
    title.textContent = "New project session";
    host.appendChild(title);

    var input = document.createElement("input");
    input.id = "ws-new-session-name";
    input.type = "text";
    input.placeholder = "Project name (optional)";
    input.className = "w-full mx-0 mb-1 px-2.5 py-1.5 rounded-lg bg-slate-900/90 border border-slate-700 text-slate-200 text-[11px] outline-none focus:border-cyan-500";
    input.addEventListener("keydown", function (e) {
      if (e.key === "Enter" && state.projects.length) {
        e.preventDefault();
        createSession(state.projects[0].path, input.value);
      }
    });
    host.appendChild(input);

    var hint = document.createElement("div");
    hint.className = "px-2 pb-1.5 text-[9.5px] text-slate-500 leading-snug";
    hint.textContent = "Leave blank and the agent names it.";
    host.appendChild(hint);

    if (!state.projects.length) {
      var none = document.createElement("div");
      none.className = "px-3 py-2 text-[11px] text-slate-500";
      none.textContent = "No project folders allowed.";
      host.appendChild(none);
      return;
    }

    var label = document.createElement("div");
    label.className = "px-2 py-1 text-[9.5px] uppercase tracking-wider text-slate-500 font-mono";
    label.textContent = state.projects.length > 1 ? "Choose folder" : "Folder";
    host.appendChild(label);

    state.projects.forEach(function (p) {
      var btn = document.createElement("button");
      btn.className = "ws-menu-item";
      btn.innerHTML = '<span class="flex-1 text-left truncate">' + esc(p.name) + "</span>" +
        '<i data-lucide="plus" class="w-3 h-3 text-cyan-400"></i>';
      btn.title = p.path;
      btn.addEventListener("click", function (e) {
        e.stopPropagation();
        createSession(p.path, input.value);
      });
      host.appendChild(btn);
    });
    icons();
  }

  // -- sessions ----------------------------------------------------------
  function loadSessions() {
    var loadId = ++state.sessionsLoadId;
    var badge = $("ws-sessions-agent");
    var a = agentById(state.agentId);
    if (badge) {
      badge.textContent = a ? a.name : "";
      badge.classList.toggle("hidden", !a);
    }
    if (!state.agentId) {
      state.sessions = [];
      renderSessions();
      return Promise.resolve();
    }
    var forAgent = state.agentId;
    return api("/api/project-sessions?agent_id=" + encodeURIComponent(forAgent)).then(function (r) {
      if (state.agentId !== forAgent || loadId !== state.sessionsLoadId) return;
      if (!r.ok) return;
      state.sessions = (r.data && r.data.sessions) || [];
      renderSessions();
    });
  }

  function statusColor(s) {
    return { running: "#10b981", busy: "#f59e0b", idle: "#38bdf8", stopped: "#64748b" }[s] || "#64748b";
  }

  function renderSessions() {
    var host = $("ws-sessions-list");
    if (!host) return;
    host.innerHTML = "";
    if (!state.agentId) {
      host.innerHTML = '<div class="text-[10px] text-slate-600 px-2 py-1 font-mono">Select an agent to see its project sessions.</div>';
      return;
    }
    if (!state.sessions.length) {
      host.innerHTML = '<div class="text-[10px] text-slate-600 px-2 py-1 font-mono">No sessions yet. Use + New.</div>';
      return;
    }
    state.sessions.forEach(function (s) {
      var row = document.createElement("div");
      row.className = "ws-session-row group" + (s.id === state.sessionId ? " ws-session-active" : "");
      row.setAttribute("data-session-id", s.id);
      row.innerHTML =
        '<span class="w-1.5 h-1.5 rounded-full shrink-0" style="background:' + statusColor(s.status) + '"></span>' +
        '<span class="flex-1 min-w-0">' +
        '<span class="block truncate text-[11px] text-slate-200">' + esc(s.title || s.project) + "</span>" +
        '<span class="block truncate text-[9.5px] text-slate-500 font-mono">' + esc(timeAgo(s.last_active)) + "</span>" +
        "</span>";
      var del = document.createElement("button");
      del.className = "ws-session-del opacity-0 group-hover:opacity-100";
      del.title = "Delete session";
      del.innerHTML = '<i data-lucide="x" class="w-3 h-3"></i>';
      del.addEventListener("click", function (e) { e.stopPropagation(); deleteSession(s.id); });
      row.appendChild(del);
      row.addEventListener("click", function () { openSession(s.id); });
      host.appendChild(row);
    });
    icons();
  }

  function createSession(projectPath, title) {
    if (!state.agentId) return;
    closeMenus();
    var body = { agent_id: state.agentId, project: projectPath, model_id: state.modelId };
    var cleanTitle = (title || "").trim();
    if (cleanTitle) body.title = cleanTitle;
    note("Creating session…");
    api("/api/project-sessions", {
      method: "POST",
      body: body,
    }).then(function (r) {
      if (!r.ok) {
        note((r.data && r.data.error) || "Couldn't create the session.");
        return;
      }
      var s = r.data.session;
      state.sessions = [s].concat(state.sessions.filter(function (item) { return item.id !== s.id; }));
      renderSessions();
      note("");
      loadSessions().then(function () { openSession(s.id); }, function () { openSession(s.id); });
    }, function () {
      note("Backend not reachable. Start it with: python server.py 8080, then reload this page.");
    });
  }

  function deleteSession(id) {
    if (!window.confirm("Delete this session? Its process will be stopped.")) return;
    api("/api/project-sessions/" + id, { method: "DELETE" }).then(function () {
      if (state.sessionId === id) {
        if (registry) registry.dispose(id);
        delete views[id];
        state.sessionId = null;
        state.current = null;
        resetScreen();
      }
      state.sessions = state.sessions.filter(function (s) { return s.id !== id; });
      renderSessions();
      loadAgents();
    });
  }

  function openSession(id, opts) {
    opts = opts || {};
    return api("/api/project-sessions/" + id).then(function (r) {
      if (!r.ok) return;
      var s = r.data.session;
      state.sessionId = id;
      state.current = s;
      state.agentId = s.agent_id;
      state.modelId = s.current_model || s.model_id;
      state.models.current = s.current_model || null;
      state.panel = PanelState.normalize(s.panel);
      if (state.panel.overlay) state.panel = PanelState.exitFullscreen(state.panel);
      if (state.panel.noAgent) state.panel = PanelState.agentSelected(state.panel);
      state.handoff[id] = state.handoff[id] || { burst: 0, suppressed: 0 };
      updateAgentLabel();
      renderAgents();
      renderSessions();
      renderPanel();
      state.autoPreviewed = null;
      if (state.panel.code_file) loadCodeFile(state.panel.code_file);
      pollRun();
      ensureRegistrySelection();
      loadModels(false);
      if (s.status === "running" || s.status === "busy" || s.status === "idle") {
        state.screen = { starting: false, exited: false, hasSession: true };
        setStatus(s.status);
        attachView(id, s);
      } else {
        startSession(id, s);
      }
    });
  }

  function attachView(id, s) {
    var host = dashboardsHost();
    if (!host) return;
    ensureRegistry();
    var rec = registry.ensure({ sessionId: id, agentId: s.agent_id || state.agentId }, host);
    var view = rec.view;
    views[id] = view;
    view.onStatus = function (status) {
      if (state.sessionId !== id) return;
      if (state.current) state.current.status = status;
      setStatus(status);
      state.screen.starting = false;
      state.screen.exited = status === "stopped";
      state.screen.hasSession = status !== "stopped";
      renderScreen();
    };
    view.onExit = function () {
      if (state.sessionId !== id) return;
      state.screen.starting = false;
      state.screen.exited = true;
      state.screen.hasSession = false;
      setStatus("stopped");
      renderScreen();
      loadSessions();
    };
    view.onHandoff = function (msg) { onHandoff(id, msg); };
    view.onFirstOutput = function () {
      if (state.sessionId !== id) return;
      state.screen.starting = false;
      state.screen.hasSession = true;
      renderScreen();
      view.fitNow();
    };
    view.mount().then(function () {
      registry.select(id);
      view.connect();
      view.fitNow();
    }).catch(function () {
      if (state.sessionId === id) {
        note("Terminal failed to load. Check your connection and reload.");
        state.screen.starting = false;
        renderScreen();
      }
    });
  }

  function ensureRegistrySelection() {
    if (!state.sessionId) return;
    if (registry) registry.select(state.sessionId);
  }

  function startSession(id, session) {
    state.screen.starting = true;
    state.screen.exited = false;
    state.screen.hasSession = true;
    resetScreenDashboards();
    renderScreen();
    setStatus("starting");
    var dims = defaultDims();
    api("/api/project-sessions/" + id + "/start", {
      method: "POST",
      body: { rows: dims.rows, cols: dims.cols, model_id: state.modelId || undefined },
    }).then(function (r) {
      if (state.sessionId !== id) return;
      if (!r.ok) {
        state.screen.starting = false;
        state.screen.exited = true;
        state.screen.hasSession = false;
        setStatus("stopped");
        renderScreen();
        note((r.data && r.data.error) || "Couldn't start the agent. Check the Agent Registry.");
        return;
      }
      if (r.data.current_model) state.models.current = r.data.current_model;
      if (state.current) state.current.status = r.data.status || "running";
      if (r.data.message) note(r.data.message);
      attachView(id, session || state.current || { agent_id: state.agentId });
    }, function () {
      if (state.sessionId !== id) return;
      state.screen.starting = false;
      state.screen.exited = true;
      renderScreen();
      note("Backend not reachable. Start it with: python server.py 8080, then reload this page.");
    });
  }

  function resetScreenDashboards() {
    if (registry) registry.select(null);
  }

  function defaultDims() {
    var host = dashboardsHost();
    var rows = 30, cols = 100;
    if (host && host.clientHeight > 0 && host.clientWidth > 0) {
      rows = Math.max(8, Math.floor(host.clientHeight / 17));
      cols = Math.max(40, Math.floor(host.clientWidth / 8.2));
    }
    return { rows: rows, cols: cols };
  }

  function startCurrentSession() {
    if (!state.sessionId) return;
    startSession(state.sessionId, state.current);
  }

  // -- handoff (auto-expand on a real backend signal) --------------------
  function onHandoff(sessionId, msg) {
    var track = state.handoff[sessionId] || (state.handoff[sessionId] = { burst: 0, suppressed: 0 });
    var burst = msg.burst || 0;
    if (burst <= track.burst) return;           // already handled this burst
    track.burst = burst;
    if (burst <= track.suppressed) return;      // user closed/collapsed: stay shut
    if (state.sessionId !== sessionId) return;  // background dashboard: keep state
    var p = state.panel || PanelState.defaultPanel();
    if (msg.reason === "preview" && msg.preview_url) {
      p = PanelState.openPreview(p, msg.preview_url);
      loadPreview(msg.preview_url);
    } else if (msg.reason === "file_change" && msg.path) {
      p = PanelState.openCode(p, msg.path);
      loadCodeFile(msg.path);
    } else {
      p = PanelState.expand(p);
    }
    if (p.state === PanelState.S1) p = PanelState.expand(p);
    applyPanel(p, { fromHandoff: true });
  }

  // -- panel (S0-S6) -----------------------------------------------------
  function renderPanel() {
    var p = state.panel || PanelState.defaultPanel();
    var d = PanelState.describe(p);

    var section = $("agent-workspace");
    if (section) section.classList.toggle("ws-fs-active", d.overlay);
    var handle = $("ws-panel-handle");
    if (handle) handle.classList.toggle("hidden", !d.showHandle || d.showPanel);
    var panel = $("ws-panel");
    if (panel) panel.classList.toggle("ws-panel-open", d.showPanel);
    var codePane = $("ws-code-pane");
    if (codePane) {
      codePane.classList.toggle("hidden", !d.showCode && !d.codeStrip);
      codePane.classList.toggle("ws-pane-max", d.state === PanelState.S3);
      codePane.classList.toggle("ws-pane-strip", d.codeStrip);
    }
    var prevPane = $("ws-preview-pane");
    if (prevPane) {
      prevPane.classList.toggle("hidden", !d.showPreview && !d.previewStrip);
      prevPane.classList.toggle("ws-pane-max", d.state === PanelState.S4);
      prevPane.classList.toggle("ws-pane-strip", d.previewStrip);
    }
    var codeStrip = $("ws-code-strip");
    if (codeStrip) codeStrip.classList.toggle("hidden", !d.codeStrip);
    var prevStrip = $("ws-preview-strip");
    if (prevStrip) prevStrip.classList.toggle("hidden", !d.previewStrip);

    var fsBar = $("ws-fullscreen-bar");
    if (fsBar) fsBar.classList.toggle("hidden", !d.overlay);
    var fsProject = $("ws-fs-project");
    if (fsProject) fsProject.textContent = state.current ? state.current.project : "";
    var fsPane = $("ws-fs-pane");
    if (fsPane) fsPane.textContent = d.fullscreenPane === "code" ? "Code" : d.fullscreenPane === "preview" ? "Preview" : "";
    var fsAgent = $("ws-fs-agent");
    if (fsAgent) {
      var statusEl = $("ws-status");
      var label = statusEl ? statusEl.textContent : "";
      fsAgent.textContent = label ? "Agent \u00b7 " + label : "";
      fsAgent.classList.toggle("hidden", !state.sessionId);
    }
    var fsBrowser = $("ws-fs-browser");
    if (fsBrowser) {
      var browserOn = false;
      try { browserOn = window.localStorage.getItem("dennis.browserFullscreen") === "1"; } catch (e) {}
      fsBrowser.classList.toggle("ws-toggle-on", browserOn);
      fsBrowser.title = browserOn ? "Using the browser Fullscreen API (click to disable)" : "Use the browser Fullscreen API";
    }

    var pathEl = $("ws-code-path");
    if (pathEl && d.codeFile != null && document.activeElement !== pathEl) pathEl.value = d.codeFile || "";
    var urlEl = $("ws-preview-url");
    if (urlEl && d.previewUrl != null && document.activeElement !== urlEl) urlEl.value = d.previewUrl || "";

    fitActiveWhenSettled();
    icons();
  }

  function applyPanel(next, opts) {
    opts = opts || {};
    state.panel = next;
    renderPanel();
    persistPanel();
    if (!opts.silent) {
      var track = state.handoff[state.sessionId];
      if (!opts.fromHandoff && track) track.suppressed = track.burst;  // user acted
    }
  }

  function persistPanel() {
    if (!state.sessionId || !state.panel) return;
    var d = PanelState.describe(state.panel);
    var logical = d.state;
    if (logical === PanelState.S5 || logical === PanelState.S6) {
      logical = PanelState.normalize(state.panel).prev || PanelState.S2;
    }
    api("/api/project-sessions/" + state.sessionId + "/panel", {
      method: "POST",
      body: {
        panel: {
          state: logical,
          pane: state.panel.pane,
          code_file: state.panel.code_file,
          editor: state.panel.editor,
          preview_url: state.panel.preview_url,
        },
      },
    });
  }

  function fitActiveWhenSettled() {
    clearTimeout(state.resizeTimer);
    var settle = function () {
      if (registry) registry.resizeActive();
      refreshEditor();
    };
    // Two frames wait for the class change to be laid out; the transitionend
    // listener catches the end of the width animation; the timer is a fallback
    // for reduced motion / interrupted transitions.
    if (window.requestAnimationFrame) {
      window.requestAnimationFrame(function () {
        window.requestAnimationFrame(settle);
      });
    } else {
      settle();
    }
    state.resizeTimer = setTimeout(settle, reducedMotion() ? 60 : HANDOFF_ANIM_MS + 90);
  }

  function onPanelTransitionEnd(e) {
    if (e.target !== $("ws-panel") || e.propertyName !== "width") return;
    if (registry) registry.resizeActive();
  }

  // -- code pane (CodeMirror editor) -------------------------------------
  var editor = null;          // CodeMirror instance
  var editorPath = null;      // project-relative path currently loaded
  var editorOriginal = "";    // last-saved content, for dirty tracking

  function modeForPath(path) {
    var ext = String(path || "").split(".").pop().toLowerCase();
    var simple = {
      py: "python", pyw: "python", pyi: "python",
      js: "javascript", mjs: "javascript", cjs: "javascript",
      html: "htmlmixed", htm: "htmlmixed", vue: "htmlmixed",
      xml: "xml", svg: "xml",
      css: "css", scss: "text/x-scss", less: "text/x-less",
      md: "markdown", markdown: "markdown",
      sh: "shell", bash: "shell", zsh: "shell",
      yml: "yaml", yaml: "yaml",
    };
    if (simple[ext]) return simple[ext];
    if (ext === "jsx" || ext === "tsx") return { name: "javascript", jsx: true, typescript: true };
    if (ext === "ts") return { name: "javascript", typescript: true };
    if (ext === "json") return { name: "javascript", json: true };
    return "text/plain";
  }

  function ensureEditor() {
    if (editor || typeof window.CodeMirror === "undefined") return editor;
    var ta = $("ws-code-editor");
    if (!ta) return null;
    editor = window.CodeMirror.fromTextArea(ta, {
      lineNumbers: true,
      styleActiveLine: true,
      matchBrackets: true,
      autoCloseBrackets: true,
      lineWrapping: false,
      viewportMargin: Infinity,
      mode: "text/plain",
      extraKeys: {
        "Cmd-S": function () { saveCodeFile(); },
        "Ctrl-S": function () { saveCodeFile(); },
      },
    });
    editor.on("change", function () {
      setDirty(editor.getValue() !== editorOriginal);
      if (state.panel) state.panel.editor = { line: editor.getCursor().line };
    });
    editor.on("scroll", function () {
      if (state.panel) {
        state.panel.editor = { line: editor.getCursor().line, scrollTop: editor.getScrollInfo().top };
      }
    });
    return editor;
  }

  function refreshEditor() {
    if (editor) { try { editor.refresh(); } catch (e) { /* ignore */ } }
  }

  function setDirty(dirty) {
    var save = $("ws-code-save");
    var dot = $("ws-code-dirty");
    if (save) save.disabled = !dirty || !editorPath;
    if (dot) dot.classList.toggle("hidden", !dirty);
  }

  function loadCodeFile(rel) {
    var pathEl = $("ws-code-path");
    if (!state.sessionId) return;
    var path = rel != null ? rel : (pathEl ? pathEl.value.trim() : "");
    if (!path) return;
    api("/api/project-sessions/" + state.sessionId + "/file?path=" + encodeURIComponent(path)).then(function (r) {
      if (!r.ok) {
        note((r.data && r.data.error) || "Couldn't open that file.");
        return;
      }
      var ed = ensureEditor();
      state.panel = PanelState.openCode(state.panel, r.data.path,
        state.panel && state.panel.editor ? state.panel.editor : null);
      renderPanel();
      persistPanel();
      editorPath = r.data.path;
      editorOriginal = r.data.content;
      if (ed) {
        ed.setOption("mode", modeForPath(r.data.path));
        ed.setValue(r.data.content);
        ed.clearHistory();
        setDirty(false);
        var pos = state.panel && state.panel.editor;
        if (pos) {
          if (pos.line) ed.setCursor({ line: Math.min(pos.line, ed.lineCount() - 1), ch: 0 });
          if (pos.scrollTop) ed.scrollTo(null, pos.scrollTop);
        }
        refreshEditor();
      }
      if (pathEl) pathEl.value = r.data.path;
    });
  }

  function saveCodeFile() {
    if (!state.sessionId) { note("Start a session first."); return; }
    if (!editor || !editorPath) return;
    var content = editor.getValue();
    var path = editorPath;
    api("/api/project-sessions/" + state.sessionId + "/file", {
      method: "POST",
      body: { path: path, content: content },
    }).then(function (r) {
      if (!r.ok) { note((r.data && r.data.error) || "Couldn't save the file."); return; }
      editorOriginal = content;
      setDirty(false);
      note("Saved " + path + ".");
    });
  }

  function toggleFileTree() {
    var tree = $("ws-code-tree");
    if (!tree) return;
    tree.classList.toggle("hidden");
    if (!tree.classList.contains("hidden") && !tree.getAttribute("data-loaded")) {
      loadTree("");
    }
  }

  function loadTree(relPath) {
    var tree = $("ws-code-tree");
    if (!tree || !state.sessionId) return;
    api("/api/project-sessions/" + state.sessionId + "/tree?path=" + encodeURIComponent(relPath || ""))
      .then(function (r) {
        if (!r.ok) { tree.innerHTML = '<div class="p-2 text-[10px] text-rose-300">' + esc((r.data && r.data.error) || "Cannot list folder") + "</div>"; return; }
        tree.setAttribute("data-loaded", "1");
        tree.setAttribute("data-path", r.data.path || "");
        tree.innerHTML = "";
        if (r.data.path) {
          var up = document.createElement("button");
          up.className = "ws-tree-row text-slate-400";
          up.innerHTML = '<i data-lucide="arrow-up" class="w-3 h-3"></i><span>..</span>';
          up.addEventListener("click", function () {
            var parts = (r.data.path || "").split("/");
            parts.pop();
            loadTree(parts.join("/"));
          });
          tree.appendChild(up);
        }
        (r.data.entries || []).forEach(function (entry) {
          var btn = document.createElement("button");
          btn.className = "ws-tree-row";
          btn.innerHTML = entry.type === "dir"
            ? '<i data-lucide="folder" class="w-3 h-3 text-amber-400"></i><span>' + esc(entry.name) + "</span>"
            : '<i data-lucide="file-code" class="w-3 h-3 text-cyan-400"></i><span>' + esc(entry.name) + "</span>";
          btn.title = entry.name;
          btn.addEventListener("click", function () {
            var base = tree.getAttribute("data-path") || "";
            var full = base ? base + "/" + entry.name : entry.name;
            if (entry.type === "dir") loadTree(full);
            else loadCodeFile(full);
          });
          tree.appendChild(btn);
        });
        icons();
      });
  }

  // -- preview pane ------------------------------------------------------
  function loadPreview(url) {
    var urlEl = $("ws-preview-url");
    var frame = $("ws-preview-frame");
    var value = url != null ? url : (urlEl ? urlEl.value.trim() : "");
    if (!value) return;
    // Only real web URLs may reach the sandboxed iframe (never javascript:).
    if (!/^https?:\/\//i.test(value)) {
      note("Preview needs a full http:// or https:// URL.");
      return;
    }
    state.panel = PanelState.openPreview(state.panel, value);
    renderPanel();
    persistPanel();
    if (urlEl) urlEl.value = value;
    if (frame && frame.getAttribute("src") !== value) frame.setAttribute("src", value);
  }

  // -- run the open project ---------------------------------------------
  function absoluteUrl(url) {
    if (/^https?:\/\//i.test(url)) return url;
    var path = url.charAt(0) === "/" ? url : "/" + url;
    // Same-origin OS endpoints (e.g. the raw project-file route) are owner
    // scoped, so pass the session token in the query for the iframe request.
    if (path.indexOf("/api/") === 0 && state.token) {
      path += (path.indexOf("?") === -1 ? "?" : "&") + "token=" + encodeURIComponent(state.token);
    }
    return location.origin + path;
  }

  function autoPreview(url) {
    var frame = $("ws-preview-frame");
    if (!frame) return;
    var abs = absoluteUrl(url);
    if (frame.getAttribute("src") === abs) return;
    if (state.autoPreviewed === abs) return;
    state.autoPreviewed = abs;
    if (state.panel && (state.panel.state === PanelState.S0 || state.panel.state === PanelState.S1)) {
      state.panel = PanelState.expand(state.panel);
    }
    state.panel = PanelState.openPreview(state.panel, abs);
    renderPanel();
    persistPanel();
    frame.setAttribute("src", abs);
    note("Previewing " + abs);
  }

  function renderRun(st) {
    st = st || {};
    var runBtn = $("ws-code-run");
    var stopBtn = $("ws-code-stop");
    var pill = $("ws-run-status");
    var log = $("ws-code-runlog");
    var running = !!st.running;
    if (runBtn) runBtn.classList.toggle("hidden", running);
    if (stopBtn) stopBtn.classList.toggle("hidden", !running);
    if (pill) {
      if (running && st.url) {
        pill.textContent = "\u25cf " + st.url;
        pill.className = "ws-run-status";
      } else if (running) {
        pill.textContent = "\u25cf starting\u2026";
        pill.className = "ws-run-status ws-run-starting";
      } else if (st.error) {
        pill.textContent = "run failed";
        pill.className = "ws-run-status ws-run-error";
      } else {
        pill.className = "ws-run-status hidden";
      }
    }
    if (log) {
      var out = (st.output || []).join("\n");
      if (out) {
        log.textContent = out;
        log.classList.remove("hidden");
        log.scrollTop = log.scrollHeight;
      } else {
        log.textContent = "";
        log.classList.add("hidden");
      }
    }
    if (running && st.url) autoPreview(st.url);
  }

  function pollRun() {
    if (!state.sessionId) return;
    api("/api/project-sessions/" + state.sessionId + "/run").then(function (r) {
      if (r.ok && r.data) renderRun(r.data);
    }).catch(function () {});
  }

  function runProject() {
    if (!state.sessionId) { note("Start a session first."); return; }
    var pill = $("ws-run-status");
    if (pill) { pill.textContent = "starting\u2026"; pill.className = "ws-run-status ws-run-starting"; }
    var runBtn = $("ws-code-run");
    if (runBtn) runBtn.disabled = true;
    api("/api/project-sessions/" + state.sessionId + "/run/start", { method: "POST" }).then(function (r) {
      if (runBtn) runBtn.disabled = false;
      if (!r.ok) { note((r.data && r.data.error) || "Couldn't start the project."); }
      renderRun(r.data || {});
      if (state.runTimer) clearInterval(state.runTimer);
      state.runTimer = setInterval(pollRun, 2500);
    });
  }

  function stopProject() {
    if (!state.sessionId) return;
    api("/api/project-sessions/" + state.sessionId + "/run/stop", { method: "POST" }).then(function (r) {
      if (r.ok) renderRun(r.data || {});
    });
    if (state.runTimer) { clearInterval(state.runTimer); state.runTimer = null; }
    state.autoPreviewed = null;
  }

  // -- fullscreen (OS overlay) ------------------------------------------
  function fullscreenPane(pane) {
    state.lastFocusButton = document.activeElement;
    state.panel = PanelState.fullscreen(state.panel, pane);
    renderPanel();
    persistPanel();
    focusFullscreen();
    maybeBrowserFullscreen(true);
  }

  function exitFullscreen() {
    state.panel = PanelState.exitFullscreen(state.panel);
    renderPanel();
    persistPanel();
    maybeBrowserFullscreen(false);
    if (state.lastFocusButton && state.lastFocusButton.focus) {
      try { state.lastFocusButton.focus(); } catch (e) { /* ignore */ }
    }
  }

  function maybeBrowserFullscreen(enter) {
    var wantBrowser = false;
    try { wantBrowser = window.localStorage.getItem("dennis.browserFullscreen") === "1"; } catch (e) {}
    var section = $("agent-workspace");
    if (!wantBrowser) { state.browserFsRequested = false; return; }
    try {
      if (enter && section && section.requestFullscreen) {
        state.browserFsRequested = true;
        section.requestFullscreen().catch(function () {
          state.browserFsRequested = false;
          note("Browser fullscreen was denied; using the in-app full screen.");
        });
      } else if (!enter && document.fullscreenElement && document.exitFullscreen) {
        state.browserFsRequested = false;
        document.exitFullscreen().catch(function () {});
      }
    } catch (e) { /* denied: the CSS overlay is still active */ }
  }

  function focusFullscreen() {
    var btn = $("ws-fs-exit");
    if (btn && btn.focus) { try { btn.focus(); } catch (e) {} }
    var bar = $("ws-fullscreen-bar");
    if (bar && !bar.getAttribute("data-trap-bound")) {
      bar.setAttribute("data-trap-bound", "1");
      bar.addEventListener("keydown", trapFocus);
    }
  }

  function trapFocus(e) {
    if (e.key !== "Tab") return;
    var container = $("ws-fullscreen-bar");
    if (!container) return;
    var pane = state.panel && state.panel.state === PanelState.S5
      ? $("ws-code-pane") : $("ws-preview-pane");
    var nodes = [];
    [container, pane].forEach(function (root) {
      if (!root) return;
      root.querySelectorAll("button, [href], input, iframe, [tabindex]:not([tabindex='-1'])")
        .forEach(function (el) { nodes.push(el); });
    });
    if (!nodes.length) return;
    var first = nodes[0];
    var last = nodes[nodes.length - 1];
    if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
  }

  function onKeydown(e) {
    if (e.key !== "Escape") return;
    var result = PanelState.escape(state.panel || PanelState.defaultPanel());
    if (result.handled) {
      e.preventDefault();
      state.panel = result.panel;
      renderPanel();
      persistPanel();
      maybeBrowserFullscreen(false);
    }
  }

  function onBrowserFullscreenChange() {
    var p = state.panel;
    if (!p) return;
    if (!document.fullscreenElement && state.browserFsRequested &&
        (p.state === PanelState.S5 || p.state === PanelState.S6)) {
      state.browserFsRequested = false;
      exitFullscreen();
    }
  }

  // -- menus -------------------------------------------------------------
  function closeMenus() {
    ["ws-agent-menu", "ws-model-menu", "ws-new-project-menu"].forEach(function (id) {
      var el = $(id);
      if (el) el.classList.add("hidden");
    });
  }

  function toggleMenu(id) {
    var el = $(id);
    if (!el) return;
    var wasHidden = el.classList.contains("hidden");
    closeMenus();
    if (wasHidden) el.classList.remove("hidden");
  }

  function bind(id, ev, fn) {
    var el = $(id);
    if (el) el.addEventListener(ev, fn);
  }

  // -- init --------------------------------------------------------------
  function init() {
    if (state.initDone) return;                 // guard against double mounting
    if (!$("agent-workspace")) return;
    state.initDone = true;

    ensureRegistry();
    // Preload xterm so the first dashboard mounts without waiting on the CDN.
    ensureXterm().catch(function () {});
    // S0 until an agent is chosen (spec: panel hidden, handle hidden).
    state.panel = PanelState.agentCleared(PanelState.defaultPanel());

    bind("ws-agent-btn", "click", function (e) {
      e.stopPropagation();
      if (!e.currentTarget.disabled) toggleMenu("ws-agent-menu");
    });
    bind("ws-model-btn", "click", function (e) {
      e.stopPropagation();
      if (e.currentTarget.disabled) return;
      var wasHidden = $("ws-model-menu").classList.contains("hidden");
      toggleMenu("ws-model-menu");
      if (wasHidden) loadModels(false);
    });
    bind("ws-model-refresh", "click", function (e) { e.stopPropagation(); loadModels(true); });
    bind("ws-model-search", "input", function (e) { state.modelQuery = e.target.value || ""; renderModels(); });
    bind("ws-new-session", "click", function (e) {
      e.stopPropagation();
      if (!state.agentId) { note("Select an agent first."); return; }
      // Always offer an optional name first; leave it blank and the agent
      // supplies the session name.
      renderProjectMenu();
      toggleMenu("ws-new-project-menu");
      var nameInput = $("ws-new-session-name");
      if (nameInput) setTimeout(function () { nameInput.focus(); }, 0);
    });

    bind("ws-panel-handle", "click", function () {
      applyPanel(PanelState.expand(state.panel || PanelState.defaultPanel()));
    });
    bind("ws-panel-collapse", "click", function () {
      applyPanel(PanelState.collapse(state.panel || PanelState.defaultPanel()));
    });
    bind("ws-panel-close", "click", function () {
      resetPanel();
    });

    bind("ws-code-min", "click", function () { applyPanel(PanelState.minimizeCode(state.panel)); });
    bind("ws-code-max", "click", function () { applyPanel(PanelState.maximizeCode(state.panel)); });
    bind("ws-code-full", "click", function () { fullscreenPane("code"); });
    bind("ws-code-strip-restore", "click", function () { applyPanel(PanelState.maximizeCode(state.panel)); });
    bind("ws-preview-min", "click", function () { applyPanel(PanelState.minimizePreview(state.panel)); });
    bind("ws-preview-max", "click", function () { applyPanel(PanelState.maximizePreview(state.panel)); });
    bind("ws-preview-full", "click", function () { fullscreenPane("preview"); });
    bind("ws-preview-strip-restore", "click", function () { applyPanel(PanelState.maximizePreview(state.panel)); });
    bind("ws-fs-exit", "click", exitFullscreen);
    bind("ws-fs-browser", "click", function () {
      var next = "1";
      try {
        next = window.localStorage.getItem("dennis.browserFullscreen") === "1" ? "0" : "1";
        window.localStorage.setItem("dennis.browserFullscreen", next);
      } catch (e) {}
      maybeBrowserFullscreen(next === "1");
      renderPanel();
    });

    bind("ws-code-load", "click", function () { loadCodeFile(); });
    bind("ws-code-tree-toggle", "click", toggleFileTree);
    bind("ws-code-save", "click", saveCodeFile);
    bind("ws-code-run", "click", runProject);
    bind("ws-code-stop", "click", stopProject);
    bind("ws-preview-load", "click", function () { loadPreview(); });
    bind("ws-preview-url", "keydown", function (e) { if (e.key === "Enter") loadPreview(); });
    bind("ws-code-path", "keydown", function (e) { if (e.key === "Enter") loadCodeFile(); });
    bind("ws-start-session", "click", startCurrentSession);

    ensureEditor();

    // Close menus on an outside click only: clicks inside an open menu (e.g. the
    // model search box, refresh) must not dismiss it.
    document.addEventListener("click", function (e) {
      var open = ["ws-agent-menu", "ws-model-menu", "ws-new-project-menu"];
      for (var i = 0; i < open.length; i++) {
        var menu = $(open[i]);
        if (menu && !menu.classList.contains("hidden") && menu.contains(e.target)) return;
      }
      closeMenus();
    });
    document.addEventListener("keydown", onKeydown);
    document.addEventListener("fullscreenchange", onBrowserFullscreenChange);
    bind("ws-panel", "transitionend", onPanelTransitionEnd);
    window.addEventListener("resize", function () {
      if (registry) registry.resizeActive();
    });

    api("/api/auth/session").then(function (r) {
      state.token = r.data && r.data.token;
      if (!state.token) {
        note("Backend not reachable. Start it with: python server.py 8080, then reload this page.");
        renderPanel();
        setStatus("stopped");
        renderScreen();
        icons();
        return Promise.resolve();
      }
      return Promise.all([
        loadProjects(),
        loadAgents(),
        api("/api/system/readiness").then(function (rr) {
          if (rr.ok && rr.data) {
            state.ready = rr.data;
            state.readyNote = rr.data.backend_note || null;
            if (rr.data.docker && rr.data.docker.code !== "ok" && rr.data.backend === "docker") {
              state.readyNote = rr.data.docker.message;
            }
          }
        }).catch(function () {}),
      ]);
    }).then(function () {
      renderSessions();
      if (state.readyNote) note(state.readyNote);
      renderPanel();
      setStatus("stopped");
      renderScreen();
      icons();
    });
  }

  function resetPanel() {
    state.panel = PanelState.reset(state.panel);
    if (editor) { editor.setValue(""); editor.clearHistory(); }
    editorPath = null;
    editorOriginal = "";
    setDirty(false);
    var log = $("ws-code-runlog");
    if (log) { log.textContent = ""; log.classList.add("hidden"); }
    var pill = $("ws-run-status");
    if (pill) pill.className = "ws-run-status hidden";
    var runBtn = $("ws-code-run");
    if (runBtn) runBtn.classList.remove("hidden");
    var stopBtn = $("ws-code-stop");
    if (stopBtn) stopBtn.classList.add("hidden");
    state.autoPreviewed = null;
    var frame = $("ws-preview-frame");
    if (frame) { frame.removeAttribute("src"); }
    var tree = $("ws-code-tree");
    if (tree) { tree.innerHTML = ""; tree.removeAttribute("data-loaded"); tree.classList.add("hidden"); }
    renderPanel();
    persistPanel();
    if (registry) registry.resizeActive();
  }

  window.Workspace = {
    init: init,
    _state: state,
    _registry: function () { return registry; },
    _modelDisabledReason: modelDisabledReason,
  };
  ready(init);
})();
