// =========================================================================
// JARVIS DASHBOARD — the OS's default home view.
// Voice core (Hermes Apollo) stays alive across views; this dashboard is its
// face: core visual, live status cards, recent memory, quick commands.
// =========================================================================

(function () {
  "use strict";

  var state = {
    initialized: false,
    core: null,          // canvas render loop handle
    refreshTimer: null,
  };

  function $(id) { return document.getElementById(id); }
  var _AMP = String.fromCharCode(38);
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      if (c === _AMP) return _AMP + "amp;";
      if (c === "<") return _AMP + "lt;";
      if (c === ">") return _AMP + "gt;";
      if (c === '"') return _AMP + "quot;";
      return _AMP + "#39;";
    });
  }

  function icons() {
    if (window.lucide && window.lucide.createIcons) {
      try { window.lucide.createIcons(); } catch (e) { /* no-op */ }
    }
  }
  function api(path) {
    return fetch(path, { cache: "no-store" })
      .then(function (r) { return r.json().catch(function () { return {}; }); })
      .catch(function () { return {}; });
  }

  // ------------------------------------------------------------------
  // Core visual: a calm arc-reactor galaxy, tap to wake the voice core.
  // (Deliberately separate from HermesGalaxy, which owns the sidebar
  //  widget and the voice pipeline — this is display-only.)
  // ------------------------------------------------------------------
  function initCore() {
    var canvas = $("jarvis-core-canvas");
    if (!canvas) return;
    var ctx = canvas.getContext("2d");
    var angle = 0;
    var level = 0, targetLevel = 0.25;
    var raf = null;

    function size() {
      var rect = canvas.getBoundingClientRect();
      var w = Math.round(rect.width) || 168;
      var h = Math.round(rect.height) || 168;
      if (canvas.width !== w || canvas.height !== h) {
        canvas.width = w; canvas.height = h;
      }
    }

    function draw() {
      var w = canvas.width, h = canvas.height;
      var cx = w / 2, cy = h / 2;
      var r = Math.min(w, h) * 0.42;
      ctx.clearRect(0, 0, w, h);

      // Outer halo
      var halo = ctx.createRadialGradient(cx, cy, r * 0.2, cx, cy, r * 1.35);
      halo.addColorStop(0, "rgba(34, 211, 238, 0.16)");
      halo.addColorStop(1, "rgba(34, 211, 238, 0)");
      ctx.fillStyle = halo;
      ctx.fillRect(0, 0, w, h);

      // Rings
      ctx.lineWidth = 1;
      ctx.strokeStyle = "rgba(8, 145, 178, 0.45)";
      ctx.beginPath(); ctx.arc(cx, cy, r, 0, Math.PI * 2); ctx.stroke();
      ctx.strokeStyle = "rgba(59, 130, 246, 0.25)";
      ctx.beginPath(); ctx.arc(cx, cy, r * 0.72, 0, Math.PI * 2); ctx.stroke();

      // Orbiting star nodes on two arms
      var stars = 44;
      for (var i = 0; i < stars; i++) {
        var arm = (i % 2) * Math.PI;
        var t = i / stars;
        var dist = r * (0.28 + 0.68 * Math.pow(t, 0.85));
        var a = angle * (1.2 - t * 0.55) + arm + t * 4.2;
        var x = cx + Math.cos(a) * dist;
        var y = cy + Math.sin(a) * dist * 0.62;   // slight ellipse = galaxy feel
        var size = 1.1 + (1 - t) * 1.6 + Math.sin(i * 12.9) * 0.35;
        var alpha = 0.35 + (1 - t) * 0.55;
        ctx.fillStyle = t < 0.22 ? "rgba(255, 255, 255, " + alpha + ")"
          : (i % 3 === 0 ? "rgba(103, 232, 249, " + alpha + ")"
                         : "rgba(96, 165, 250, " + alpha + ")");
        ctx.beginPath(); ctx.arc(x, y, size, 0, Math.PI * 2); ctx.fill();
      }

      // Reactive nucleus
      var pulse = r * 0.16 * (1 + level * 1.4);
      var core = ctx.createRadialGradient(cx, cy, 0, cx, cy, pulse * 2.1);
      core.addColorStop(0, "rgba(255, 255, 255, 0.95)");
      core.addColorStop(0.45, "rgba(103, 232, 249, 0.75)");
      core.addColorStop(1, "rgba(37, 99, 235, 0)");
      ctx.fillStyle = core;
      ctx.beginPath(); ctx.arc(cx, cy, pulse * 2.1, 0, Math.PI * 2); ctx.fill();

      angle += 0.016;
      level += (targetLevel - level) * 0.08;
      targetLevel = 0.22 + 0.5 * Math.abs(Math.sin(angle * 0.35));

      raf = requestAnimationFrame(draw);
    }

    canvas.addEventListener("click", function () {
      wakeCore();
    });

    size();
    if (window.ResizeObserver) new ResizeObserver(size).observe(canvas);
    raf = requestAnimationFrame(draw);

    state.core = { stop: function () { if (raf) cancelAnimationFrame(raf); } };
  }

  function wakeCore() {
    var el = $("jarvis-core-state");
    function flash(label) {
      if (!el) return;
      el.textContent = label || "listening";
      el.classList.add("jarvis-state-live");
      window.setTimeout(function () {
        el.textContent = "standby";
        el.classList.remove("jarvis-state-live");
      }, 4200);
    }
    // Wake the real voice core (the sidebar Apollo widget owns the pipeline).
    var apollo = window.hermesApollo;
    if (apollo && typeof apollo.wake === "function") {
      apollo.wake(true);
      flash("listening");
      return;
    }
    var wrap = $("apollo-canvas-wrap");
    if (wrap) { wrap.click(); flash("listening"); return; }
    var widget = $("hermes-apollo-widget");
    if (widget) {
      widget.scrollIntoView({ behavior: "smooth", block: "center" });
      widget.classList.add("apollo-awake");
      window.setTimeout(function () { widget.classList.remove("apollo-awake"); }, 4200);
    }
    flash("voice core warming up");
  }

  // ------------------------------------------------------------------
  // Status cards
  // ------------------------------------------------------------------
  function setText(id, value) {
    var el = $(id);
    if (el) el.textContent = value;
  }

  function refreshStatus() {
    api("/api/tts/status").then(function (d) {
      var el = $("jarvis-stat-voice");
      if (!el) return;
      var k = (d && d.kokoro) || {};
      if (k.healthy && k.loaded) {
        el.textContent = "Online";
        setText("jarvis-stat-voice-sub", "Kokoro · JARVIS preset");
      } else if (k.reachable) {
        el.textContent = "Warming up";
        setText("jarvis-stat-voice-sub", "Kokoro is loading its model");
      } else {
        el.textContent = "Standby";
        setText("jarvis-stat-voice-sub", "Browser voice fallback active");
      }
    });
    api("/api/agents/availability").then(function (d) {
      var agents = (d && d.agents) || [];
      var ready = agents.filter(function (a) { return a.available || a.detected; }).length;
      if ($("jarvis-stat-agents")) {
        setText("jarvis-stat-agents", ready ? ready + " / " + agents.length : "—");
        setText("jarvis-stat-agents-sub",
          ready ? "Host CLIs discovered" : "Open the Agent Registry to install");
      }
      var sys = $("jarvis-system");
      if (sys && agents.length) {
        sys.innerHTML = agents.slice(0, 6).map(function (a) {
          var okk = a.available || a.detected;
          return '<div class="flex items-center gap-2">' +
            '<span class="w-1.5 h-1.5 rounded-full shrink-0" style="background:' +
            (okk ? "#34d399" : "#64748b") + '"></span>' +
            '<span class="flex-1 min-w-0 truncate">' + esc(a.name || a.id) + "</span>" +
            '<span class="text-[9.5px] font-mono text-slate-500">' + (okk ? "ready" : "not installed") + "</span></div>";
        }).join("");
      }
    });
    api("/api/vault/graph").then(function (d) {
      var nodes = (d && d.nodes) || [];
      if ($("jarvis-stat-notes")) setText("jarvis-stat-notes", nodes.length ? String(nodes.length) : "—");
      var recent = nodes.slice(-5).reverse();
      var host = $("jarvis-recent-notes");
      if (host) {
        if (!recent.length) {
          host.innerHTML = '<p class="text-[11px] text-slate-500">No notes yet. Create one in the Vault to grow your memory.</p>';
        } else {
          host.innerHTML = recent.map(function (n) {
            return '<button class="jarvis-note-row" data-note-path="' + esc(n.id) + '">' +
              '<i data-lucide="file-text" class="w-3.5 h-3.5 text-purple-400 shrink-0"></i>' +
              '<span class="flex-1 min-w-0 truncate text-left">' + esc(n.title || n.stem || n.id) + "</span>" +
              '<span class="text-[9px] font-mono text-slate-500 shrink-0">' + (n.links || 0) + " links</span></button>";
          }).join("");
          icons();
          host.querySelectorAll("[data-note-path]").forEach(function (btn) {
            btn.addEventListener("click", function () {
              var p = btn.getAttribute("data-note-path");
              if (window.Vault && window.Vault.openPath) window.Vault.openPath(p);
              else if (window.Vault) window.Vault.open();
            });
          });
        }
      }
    });
    api("/api/project-sessions").then(function (d) {
      var sessions = (d && d.sessions) || [];
      var running = sessions.filter(function (s) {
        return s && (s.status === "running" || s.live_status === "running" || s.live_status === "busy");
      }).length;
      if ($("jarvis-stat-sessions")) {
        setText("jarvis-stat-sessions", sessions.length ? running + " / " + sessions.length : "—");
        setText("jarvis-stat-sessions-sub", running ? "Project sessions live" : "Open Workspace to start one");
      }
    });
  }

  // ------------------------------------------------------------------
  // Quick commands
  // ------------------------------------------------------------------
  function bindQuickCommands() {
    function go(btnId, view) {
      var btn = $(btnId);
      if (btn) btn.addEventListener("click", function () {
        if (window.switchMainView) window.switchMainView(view);
      });
    }
    go("jarvis-qc-workspace", "workspace");
    go("jarvis-qc-galaxy", "memory-galaxy");
    go("jarvis-qc-vault", "vault");
    go("jarvis-qc-studio", "studio");
    go("jarvis-qc-agents", "workspace");
    var openGalaxy = $("jarvis-open-galaxy");
    if (openGalaxy) openGalaxy.addEventListener("click", function () {
      if (window.switchMainView) window.switchMainView("memory-galaxy");
    });
  }

  function greeting() {
    var el = $("jarvis-greeting");
    if (!el) return;
    var h = new Date().getHours();
    var part = h < 12 ? "Good morning" : (h < 18 ? "Good afternoon" : "Good evening");
    el.textContent = part + ", Dennis. All systems are online.";
  }

  // ------------------------------------------------------------------
  // Public API
  // ------------------------------------------------------------------
  var JarvisDashboard = {
    refresh: function () {
      if (!state.initialized) return;
      greeting();
      refreshStatus();
    },
  };
  window.JarvisDashboard = JarvisDashboard;

  function ready(fn) {
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", fn);
    else fn();
  }

  ready(function () {
    if (!$("jarvis-view")) return;
    state.initialized = true;
    initCore();
    bindQuickCommands();
    greeting();
    refreshStatus();
    // Keep the cards honest without hammering the backend.
    if (state.refreshTimer) clearInterval(state.refreshTimer);
    state.refreshTimer = setInterval(refreshStatus, 30000);
  });
})();
