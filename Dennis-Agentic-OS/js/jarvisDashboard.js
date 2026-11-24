// =========================================================================
// HERMES–JARVIS NEURAL COMMAND CENTER — the OS's default home view.
// Voice core (Hermes Apollo) stays alive across views; this dashboard is its
// face: neural core canvas, system telemetry, waveform, briefing history.
// All numbers shown are real measurements — none are decorative.
// =========================================================================

(function () {
  "use strict";

  var state = {
    initialized: false,
    core: null,          // { stop } for the shared render loop
    refreshTimer: null,
    clockTimer: null,
    teleTimer: null,
    quoteTimer: null,
    briefTimer: null,
    toastTimer: null,
    briefBusy: false,
    briefOnline: false,
    briefKind: "",
    quoteIdx: 0,
    briefCount: 0,
    latencyMs: null,
    signalPct: null,
    coreSize: { w: 0, h: 0 },
    reducedMotion: false,
  };

  var QUOTES = [
    "At your service, sir.",
    "All systems nominal, sir.",
    "Standing by for your command.",
    "The den is quiet. Markets monitored, sir.",
    "Memory synchronized. Awaiting your word.",
  ];

  var CORE_STATE_TEXT = {
    standby: "SYSTEM STANDBY",
    listening: "SYSTEM LISTENING",
    awake: "SYSTEM AWAKE",
    speaking: "TRANSMITTING",
    muted: "SYSTEM MUTED",
  };

  function $(id) { return document.getElementById(id); }
  var _AMP = String.fromCharCode(38);
  function esc(s) {
    return String(s == null ? "" : String(s)).replace(/[&<>"']/g, function (c) {
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
  function apollo() { return window.hermesApollo || null; }

  // Owner-token bootstrap (same pattern as js/tts.js) — every OS API write
  // is authenticated with X-Dennis-Token.
  var _tokenPromise = null;
  function authToken() {
    if (!_tokenPromise) {
      _tokenPromise = fetch("/api/auth/session", { cache: "no-store" })
        .then(function (r) { return r.json(); })
        .then(function (d) { return (d && d.token) || ""; })
        .catch(function () { return ""; });
    }
    return _tokenPromise;
  }
  function postJSON(path, body) {
    return authToken().then(function (token) {
      return fetch(path, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Dennis-Token": token },
        body: JSON.stringify(body || {}),
      }).then(function (r) { return r.json().catch(function () { return {}; }); });
    });
  }

  function hudToast(msg) {
    var toast = $("apollo-hud-toast");
    var text = $("apollo-hud-toast-msg");
    if (!toast || !text) return;
    text.textContent = msg;
    toast.classList.add("visible");
    if (state.toastTimer) clearTimeout(state.toastTimer);
    state.toastTimer = window.setTimeout(function () {
      toast.classList.remove("visible");
    }, 3600);
  }

  // "Mon 15 Jun, 08:07" — matches the Neural Command Center readout style.
  function fmtWhen(iso) {
    var d = new Date(iso);
    if (!iso || isNaN(d.getTime())) return String(iso || "");
    var days = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
    var mons = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
    function p2(n) { return (n < 10 ? "0" : "") + n; }
    return days[d.getDay()] + " " + d.getDate() + " " + mons[d.getMonth()] +
      ", " + p2(d.getHours()) + ":" + p2(d.getMinutes());
  }

  function setBar(id, pct) {
    var bar = $(id);
    if (bar) bar.style.width = Math.max(0, Math.min(100, pct)) + "%";
  }

  // ------------------------------------------------------------------
  // Live clock (top-right readout)
  // ------------------------------------------------------------------
  function startClock() {
    var clock = $("jarvis-clock");
    if (!clock) return;
    var dateEl = $("jarvis-date");
    function p2(n) { return (n < 10 ? "0" : "") + n; }
    function tick() {
      var now = new Date();
      clock.textContent = p2(now.getHours()) + ":" + p2(now.getMinutes()) + ":" + p2(now.getSeconds());
      if (dateEl) {
        dateEl.textContent = now.toLocaleDateString(undefined,
          { weekday: "long", day: "numeric", month: "long", year: "numeric" });
      }
    }
    tick();
    if (state.clockTimer) clearInterval(state.clockTimer);
    state.clockTimer = setInterval(tick, 1000);
  }

  // ------------------------------------------------------------------
  // Telemetry readouts
  // ------------------------------------------------------------------
  function updateWakeControls() {
    var ap = apollo();
    var on = !!(ap && ap.isVoiceWakeActive);
    var st = ap ? (ap.state || "standby") : "offline";

    var teleStatus = $("jarvis-tele-status");
    if (teleStatus) {
      var dot = teleStatus.querySelector(".hud-dot");
      var label = st.charAt(0).toUpperCase() + st.slice(1);
      teleStatus.innerHTML = "";
      if (dot) teleStatus.appendChild(dot);
      teleStatus.appendChild(document.createTextNode(label));
      teleStatus.className = "hud-value " + (
        st === "muted" ? "text-rose-400"
        : st === "standby" ? "text-cyan-300"
        : "text-emerald-400");
      if (dot) dot.className = "hud-dot " + (
        st === "muted" ? "bg-rose-400"
        : st === "standby" ? "bg-cyan-400"
        : "bg-emerald-400");
    }
    var teleMode = $("jarvis-tele-mode");
    if (teleMode) teleMode.innerHTML = (on ? "&raquo;&nbsp;Auto" : "&raquo;&nbsp;Manual");
    var teleWake = $("jarvis-tele-wake");
    if (teleWake) {
      teleWake.textContent = on ? "On" : "Off";
      teleWake.className = "hud-value " + (on ? "text-emerald-400" : "text-rose-400");
    }

    var coreState = $("jarvis-core-state");
    if (coreState) {
      coreState.textContent = CORE_STATE_TEXT[st] || ("SYSTEM " + String(st).toUpperCase());
    }

    var btn = $("jarvis-bar-wake");
    if (btn) {
      var label = btn.querySelector("span");
      if (label) label.textContent = "Wake word " + (on ? "ON" : "OFF");
      btn.classList.toggle("jarvis-bar-btn-off", !on);
    }
  }

  // Voice label: effective provider + voice from the TTS config (honest
  // value — never a hardcoded preset name). The bottom-bar picker owns its
  // own label; this feeds the telemetry readout only.
  function voiceLabel(presetId, preset) {
    if (preset && preset.name) return preset.name;
    if (!presetId) return "JARVIS";
    return presetId.replace(/[-_]+/g, " ").replace(/\b\w/g, function (c) { return c.toUpperCase(); });
  }

  function effectiveVoiceLabel(cfg) {
    cfg = cfg || {};
    var apollo = (cfg.studios || {}).apollo || {};
    var provider = apollo.provider || cfg.global_default_provider || "kokoro";
    var model = apollo.model_id || cfg.global_default_model_id || "";
    var preset = (cfg.presets || {})[apollo.voice_preset || apollo.preset] || {};
    var voice = apollo.voice_id || preset.voice_id || "";
    var providerName = ({ kokoro: "Kokoro", fish: "Fish Audio", elevenlabs: "ElevenLabs" })[provider] || provider;
    return {
      text: providerName + (voice ? " \u00b7 " + voice : ""),
      title: "Apollo voice \u2014 " + providerName +
        (model ? " \u00b7 " + model : "") +
        (voice ? " \u00b7 " + voice : "") +
        (preset.name ? " (preset: " + preset.name + ")" : "")
    };
  }

  function loadTelemetry() {
    var t0 = (window.performance && performance.now) ? performance.now() : Date.now();
    var kokoroOk = null, jarvisOk = null;

    // Latency: measured on every cycle, shown as a rolling 3-sample average
    // so a single cold sample doesn't misrepresent the steady state.
    api("/api/tts/status").then(function (d) {
      state.latencyMs = ((window.performance && performance.now) ? performance.now() : Date.now()) - t0;
      state.latencySamples = (state.latencySamples || []).concat(state.latencyMs).slice(-3);
      var avg = state.latencySamples.reduce(function (a, b) { return a + b; }, 0) / state.latencySamples.length;
      var lat = $("jarvis-tele-latency");
      if (lat) lat.textContent = (avg / 1000).toFixed(2) + "s";
      var k = (d && d.kokoro) || {};
      kokoroOk = !!(k.reachable);
      updateSignal();
      api("/api/tts/config").then(function (c) {
        var cfg = (c && c.config) || c || {};
        var label = effectiveVoiceLabel(cfg);
        var voice = $("jarvis-tele-voice");
        if (voice) { voice.textContent = label.text; voice.title = label.title; }
      });
    });

    // Core load: real backend load average normalized by core count.
    api("/api/system/telemetry").then(function (t) {
      var pct = (t && typeof t.load_pct === "number") ? t.load_pct : null;
      setBar("jarvis-tele-load-bar", pct || 0);
      var v = $("jarvis-tele-load");
      if (v) {
        v.textContent = pct == null ? "\u2014" : pct.toFixed(1) + "%";
        v.title = (t && t.rss_mb) ? ("OS backend: " + t.rss_mb + " MB RSS \u00b7 up " +
          Math.round((t.uptime_s || 0) / 60) + " min") : "";
      }
    });

    // Neural throughput + connected nodes: real agent registry readiness.
    api("/api/agents/availability").then(function (d) {
      var agents = (d && d.agents) || [];
      var ready = agents.filter(function (a) { return a.available || a.detected; }).length;
      var pct = agents.length ? Math.round((ready / agents.length) * 100) : 0;
      setBar("jarvis-tele-throughput-bar", pct);
      var v = $("jarvis-tele-throughput");
      if (v) v.textContent = agents.length ? pct + "%" : "\u2014";
      var nodes = $("jarvis-net-nodes");
      if (nodes) {
        nodes.textContent = agents.length ? ready + " / " + agents.length : "\u2014";
        nodes.className = "hud-value " + (ready ? "text-emerald-400" : "text-slate-400");
      }
    });

    // Memory status: the Vault second brain.
    api("/api/vault/graph").then(function (d) {
      var nodes = (d && d.nodes) || [];
      var mem = $("jarvis-net-memory");
      if (mem) {
        mem.textContent = nodes.length ? "SYNCHRONIZED \u00b7 " + nodes.length + " notes" : "\u2014";
        mem.className = "hud-value " + (nodes.length ? "text-emerald-400" : "text-slate-400");
      }
    });

    // Signal: voice engine + briefing service availability (real composite).
    api("/api/digest/service").then(function (d) {
      jarvisOk = !!(d && d.available === true);
      updateSignal();
    });

    function updateSignal() {
      var parts = [];
      if (kokoroOk !== null) parts.push(kokoroOk ? 1 : 0);
      if (jarvisOk !== null) parts.push(jarvisOk ? 1 : 0);
      if (!parts.length) return;
      var pct = Math.round((parts.reduce(function (a, b) { return a + b; }, 0) / parts.length) * 100);
      state.signalPct = pct;
      setBar("jarvis-tele-signal-bar", pct);
      var v = $("jarvis-tele-signal");
      if (v) {
        v.textContent = pct + "%";
        v.className = "hud-value " + (pct >= 100 ? "text-emerald-400"
          : pct > 0 ? "text-cyan-300" : "text-rose-400");
      }
    }
  }

  // ------------------------------------------------------------------
  // The neural core — the centerpiece renderer.
  // Reactive rings + orbiting ellipses + triangle array + nucleus.
  // ------------------------------------------------------------------
  function initCore() {
    var canvas = $("jarvis-core-canvas");
    if (!canvas) return;
    var ctx = canvas.getContext("2d");
    var dpr = Math.min(2, window.devicePixelRatio || 1);
    var visible = true;
    var raf = null;

    state.reducedMotion = window.matchMedia &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches;

    function size() {
      var rect = canvas.getBoundingClientRect();
      var w = Math.round(rect.width), h = Math.round(rect.height);
      if (!w || !h) return false;
      if (canvas.width !== Math.round(w * dpr) || canvas.height !== Math.round(h * dpr)) {
        canvas.width = Math.round(w * dpr);
        canvas.height = Math.round(h * dpr);
      }
      state.coreSize = { w: w, h: h };
      return true;
    }
    if (window.ResizeObserver) new ResizeObserver(function () { size(); }).observe(canvas);

    function apolloState() {
      var ap = apollo();
      return ap ? (ap.state || "standby") : "standby";
    }
    function apolloLevel() {
      var ap = apollo();
      var lvl = ap ? (ap.audioLevel || ap.targetAudioLevel || 0) : 0;
      return Math.max(0, Math.min(1, lvl));
    }

    function drawCore(w, h, t) {
      var cx = w / 2;
      var cy = h * 0.44;
      var R = Math.min(w * 0.30, h * 0.30, 260);
      if (R < 24) return;
      var st = apolloState();
      var active = (st === "listening" || st === "awake" || st === "speaking");
      var level = apolloLevel();
      var speed = state.reducedMotion ? 0 : (st === "speaking" ? 1.8 : active ? 1.25 : 0.55);
      var glow = active ? 1 : st === "muted" ? 0.45 : 0.7;

      ctx.clearRect(0, 0, w, h);

      // --- deep glow behind the core ---
      var bg = ctx.createRadialGradient(cx, cy, 0, cx, cy, R * 2.8);
      bg.addColorStop(0, "rgba(14, 165, 233, " + 0.16 * glow + ")");
      bg.addColorStop(0.55, "rgba(8, 90, 140, " + 0.08 * glow + ")");
      bg.addColorStop(1, "rgba(2, 8, 20, 0)");
      ctx.fillStyle = bg;
      ctx.fillRect(0, 0, w, h);

      // --- projector platform below ---
      var py = h * 0.88;
      var plat = ctx.createRadialGradient(cx, py, 0, cx, py, R * 1.9);
      plat.addColorStop(0, "rgba(34, 211, 238, " + 0.20 * glow + ")");
      plat.addColorStop(1, "rgba(2, 8, 20, 0)");
      ctx.fillStyle = plat;
      ctx.fillRect(cx - R * 2, py - R * 0.8, R * 4, R * 1.6);
      for (var pr = 0; pr < 4; pr++) {
        var rx = R * (1.55 - pr * 0.3);
        ctx.beginPath();
        ctx.strokeStyle = "rgba(34, 211, 238, " + (0.4 - pr * 0.08) * glow + ")";
        ctx.lineWidth = 1.5 - pr * 0.2;
        ctx.ellipse(cx, py, rx, rx * 0.18, 0, 0, Math.PI * 2);
        ctx.stroke();
      }

      // --- light beam from platform to nucleus ---
      var beamW = R * 0.10 * (1 + level * 0.8);
      var beam = ctx.createLinearGradient(0, py, 0, cy);
      beam.addColorStop(0, "rgba(103, 232, 249, " + 0.30 * glow + ")");
      beam.addColorStop(1, "rgba(103, 232, 249, 0)");
      ctx.fillStyle = beam;
      ctx.fillRect(cx - beamW / 2, cy, beamW, py - cy);

      ctx.save();
      ctx.translate(cx, cy);

      // --- outer dashed ring (slow rotation) ---
      ctx.save();
      ctx.rotate(t * 0.05 * speed);
      ctx.beginPath();
      ctx.setLineDash([3, 9]);
      ctx.strokeStyle = "rgba(34, 211, 238, " + 0.5 * glow + ")";
      ctx.lineWidth = 1.2;
      ctx.arc(0, 0, R * 1.52, 0, Math.PI * 2);
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.restore();

      // --- tick ring ---
      ctx.save();
      ctx.rotate(-t * 0.02 * speed);
      for (var i = 0; i < 72; i++) {
        var a = (i / 72) * Math.PI * 2;
        var long = i % 6 === 0;
        var r1 = R * 1.30, r2 = r1 + (long ? 10 : 5);
        ctx.beginPath();
        ctx.strokeStyle = "rgba(103, 232, 249, " + (long ? 0.65 : 0.32) * glow + ")";
        ctx.lineWidth = long ? 1.6 : 1;
        ctx.moveTo(Math.cos(a) * r1, Math.sin(a) * r1);
        ctx.lineTo(Math.cos(a) * r2, Math.sin(a) * r2);
        ctx.stroke();
      }
      ctx.restore();

      // --- solid ring + inner ring ---
      ctx.beginPath();
      ctx.strokeStyle = "rgba(34, 211, 238, " + 0.75 * glow + ")";
      ctx.lineWidth = 2;
      ctx.arc(0, 0, R * 1.16, 0, Math.PI * 2);
      ctx.stroke();
      ctx.beginPath();
      ctx.strokeStyle = "rgba(96, 165, 250, " + 0.4 * glow + ")";
      ctx.lineWidth = 1;
      ctx.arc(0, 0, R * 1.05, 0, Math.PI * 2);
      ctx.stroke();

      // --- triangle array (rotating, alternating up/down) ---
      ctx.save();
      ctx.rotate(t * 0.12 * speed);
      for (var k = 0; k < 8; k++) {
        var ta = (k / 8) * Math.PI * 2;
        var tx = Math.cos(ta) * R * 0.88;
        var ty = Math.sin(ta) * R * 0.88;
        var ts = R * 0.13;
        ctx.save();
        ctx.translate(tx, ty);
        ctx.rotate(ta + Math.PI / 2);
        ctx.beginPath();
        if (k % 2 === 0) { ctx.moveTo(0, -ts); ctx.lineTo(ts, ts); ctx.lineTo(-ts, ts); }
        else { ctx.moveTo(0, ts); ctx.lineTo(ts, -ts); ctx.lineTo(-ts, -ts); }
        ctx.closePath();
        ctx.strokeStyle = "rgba(165, 243, 252, " + 0.85 * glow + ")";
        ctx.lineWidth = 1.6;
        ctx.stroke();
        ctx.restore();
      }
      ctx.restore();

      // --- two tilted orbit ellipses with traveling nodes ---
      for (var e = 0; e < 2; e++) {
        var tilt = (e === 0 ? -0.32 : 0.26);
        var phase = t * (e === 0 ? 0.5 : -0.38) * speed;
        ctx.save();
        ctx.rotate(tilt);
        ctx.beginPath();
        ctx.setLineDash([14, 10]);
        ctx.strokeStyle = "rgba(56, 189, 248, " + 0.55 * glow + ")";
        ctx.lineWidth = 1.4;
        ctx.ellipse(0, 0, R * 1.78, R * 0.52, 0, 0, Math.PI * 2);
        ctx.stroke();
        ctx.setLineDash([]);
        // traveling node
        var nx = Math.cos(phase * Math.PI * 2) * R * 1.78;
        var ny = Math.sin(phase * Math.PI * 2) * R * 0.52;
        ctx.beginPath();
        ctx.fillStyle = "rgba(165, 243, 252, " + 0.95 * glow + ")";
        ctx.shadowColor = "rgba(34, 211, 238, 0.9)";
        ctx.shadowBlur = 12;
        ctx.arc(nx, ny, 2.6, 0, Math.PI * 2);
        ctx.fill();
        ctx.shadowBlur = 0;
        ctx.restore();
      }

      // --- nucleus: pulsing reactor ---
      var pulse = 0.5 + 0.5 * Math.sin(t * (active ? 5 : 2) * (state.reducedMotion ? 0 : 1));
      var nr = R * (0.34 + level * 0.14 + pulse * 0.035);
      var nucleus = ctx.createRadialGradient(0, 0, 0, 0, 0, nr * 2.6);
      nucleus.addColorStop(0, "rgba(240, 253, 255, 0.9)");
      nucleus.addColorStop(0.22, "rgba(103, 232, 249, " + 0.75 * glow + ")");
      nucleus.addColorStop(0.45, "rgba(34, 211, 238, " + 0.4 * glow + ")");
      nucleus.addColorStop(1, "rgba(14, 116, 200, 0)");
      ctx.fillStyle = nucleus;
      ctx.beginPath();
      ctx.arc(0, 0, nr * 2.6, 0, Math.PI * 2);
      ctx.fill();
      // crisp energy core (compact, hot center)
      ctx.beginPath();
      ctx.fillStyle = "rgba(240, 253, 255, " + 0.95 * glow + ")";
      ctx.arc(0, 0, nr * 0.30, 0, Math.PI * 2);
      ctx.fill();
      // lens flare: horizontal + vertical spikes
      var flare = ctx.createLinearGradient(-nr * 3, 0, nr * 3, 0);
      flare.addColorStop(0, "rgba(34, 211, 238, 0)");
      flare.addColorStop(0.5, "rgba(165, 243, 252, " + 0.5 * glow + ")");
      flare.addColorStop(1, "rgba(34, 211, 238, 0)");
      ctx.fillStyle = flare;
      ctx.fillRect(-nr * 3, -1.1, nr * 6, 2.2);
      ctx.fillStyle = flare;
      ctx.fillRect(-1.1, -nr * 2.1, 2.2, nr * 4.2);

      ctx.restore();
    }

    function loop(now) {
      raf = null;
      if (!size()) return;
      if (document.hidden || !visible) return;
      var w = state.coreSize.w, h = state.coreSize.h;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      drawCore(w, h, now / 1000);
      drawWave(now / 1000);
      raf = requestAnimationFrame(loop);
    }
    function kick() {
      if (raf === null && !document.hidden && visible) raf = requestAnimationFrame(loop);
    }

    document.addEventListener("visibilitychange", kick);
    if ("IntersectionObserver" in window) {
      new IntersectionObserver(function (entries) {
        visible = entries.some(function (en) { return en.isIntersecting; });
        kick();
      }, { threshold: 0 }).observe(canvas);
    }
    canvas.addEventListener("click", function () { wakeCore(); });
    size();
    kick();
    state.core = { stop: function () { if (raf) cancelAnimationFrame(raf); raf = null; } };
  }

  function wakeCore() {
    var ap = apollo();
    if (ap && typeof ap.wake === "function") {
      ap.wake(true);
    }
  }

  // ------------------------------------------------------------------
  // Waveform — full-width audio visualizer above the command bar.
  // Idle = gentle organic noise; listening/speaking follows the real
  // microphone/TTS level from the Apollo voice core.
  // ------------------------------------------------------------------
  function drawWave(t) {
    var canvas = $("jarvis-wave-canvas");
    if (!canvas) return;
    var ctx = canvas.getContext("2d");
    var dpr = Math.min(2, window.devicePixelRatio || 1);
    var rect = canvas.getBoundingClientRect();
    var w = Math.round(rect.width), h = Math.round(rect.height);
    if (!w || !h) return;
    if (canvas.width !== Math.round(w * dpr)) {
      canvas.width = Math.round(w * dpr);
      canvas.height = Math.round(h * dpr);
    }
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);

    var ap = apollo();
    var st = ap ? (ap.state || "standby") : "standby";
    var level = ap ? Math.max(0, Math.min(1, ap.audioLevel || ap.targetAudioLevel || 0)) : 0;
    var energy = st === "speaking" || st === "listening" || st === "awake"
      ? 0.35 + level * 1.4 : 0;
    if (st === "muted") energy = 0;

    var bars = 96;
    var gap = w / bars;
    var mid = h * 0.52;
    for (var i = 0; i < bars; i++) {
      var x = i * gap + gap * 0.22;
      var bw = Math.max(1.4, gap * 0.5);
      // layered sines = organic idle motion; center-weighted envelope
      var env = 0.30 + 0.70 * Math.sin((i / bars) * Math.PI);
      var idle = (0.16 + 0.10 * Math.sin(t * 1.6 + i * 0.35)
                       + 0.08 * Math.sin(t * 0.9 + i * 0.11)) * env;
      var amp = (idle + energy * env * (0.25 + 0.45 * Math.abs(Math.sin(i * 12.9 + t * 6))))
        * h * 0.42;
      amp = Math.max(1.5, Math.min(amp, h * 0.46));
      var hue = 187 + 20 * Math.sin(i * 0.09 + t * 0.4);
      ctx.fillStyle = "hsla(" + hue + ", 95%, 62%, " + (0.5 + amp / (h * 0.5) * 0.5) + ")";
      ctx.fillRect(x, mid - amp, bw, amp);
      // faint reflection below
      ctx.fillStyle = "hsla(" + hue + ", 95%, 62%, 0.16)";
      ctx.fillRect(x, mid + 2, bw, amp * 0.35);
    }
  }

  // ------------------------------------------------------------------
  // Command bar
  // ------------------------------------------------------------------
  function rotateQuote() {
    var el = $("jarvis-bar-quote");
    if (!el) return;
    state.quoteIdx = (state.quoteIdx + 1) % QUOTES.length;
    el.textContent = "\u201C" + QUOTES[state.quoteIdx] + "\u201D";
  }

  function bindCommandBar() {
    var wake = $("jarvis-bar-wake");
    if (wake) wake.addEventListener("click", function () {
      var ap = apollo();
      if (ap && typeof ap.toggleVoiceWake === "function") ap.toggleVoiceWake();
      updateWakeControls();
    });
    var brief = $("jarvis-bar-briefing");
    if (brief) brief.addEventListener("click", function () {
      var panel = $("jarvis-brief-list");
      if (panel && panel.scrollIntoView) {
        panel.scrollIntoView({ behavior: "smooth", block: "nearest" });
      }
      generateBriefing("daily");
    });
    var refresh = $("jarvis-brief-refresh");
    if (refresh) refresh.addEventListener("click", loadBriefings);
    var close = $("jarvis-brief-close");
    if (close) close.addEventListener("click", function () {
      var panel = $("jarvis-brief-panel");
      if (!panel) return;
      var collapsed = panel.classList.toggle("collapsed");
      close.title = collapsed ? "Expand panel" : "Collapse panel";
    });
    var gen = $("jarvis-brief-generate");
    if (gen) gen.addEventListener("click", function () { generateBriefing("daily"); });
    var weekly = $("jarvis-brief-weekly");
    if (weekly) weekly.addEventListener("click", function () { generateBriefing("weekly"); });
  }

  // ------------------------------------------------------------------
  // Briefing history (native Step 5 briefing module). Offline => honest
  // reason; busy => honest "Building…".
  // ------------------------------------------------------------------
  function briefingRow(kind, when, text, opts) {
    var full = String(text || "");
    var row = document.createElement("div");
    row.className = "jarvis-brief-row" + (opts && opts.latest ? " latest" : "");
    var head = '<div class="flex items-center gap-2">' +
      '<span class="jarvis-brief-badge">' + esc(kind) + "</span>" +
      '<span class="text-[9.5px] font-mono text-slate-500">' + esc(when) + "</span>";
    if (opts && opts.latest) {
      head += '<button class="jarvis-brief-speak ml-auto" title="Speak in the JARVIS voice">' +
        '<i data-lucide="volume-2" class="w-3.5 h-3.5"></i></button>';
    }
    head += "</div>";
    row.innerHTML = head +
      '<p class="jarvis-brief-text mt-1 text-slate-300">' + esc(full) + "</p>";
    if (!opts || !opts.latest) {
      // "Tap to reopen": expand the clamped preview.
      row.addEventListener("click", function (e) {
        if (e.target.closest(".jarvis-brief-speak")) return;
        row.classList.toggle("expanded");
      });
    }
    var speakBtn = row.querySelector(".jarvis-brief-speak");
    if (speakBtn) {
      speakBtn.addEventListener("click", function (e) {
        e.stopPropagation();
        if (window.DennisTTS && typeof window.DennisTTS.speak === "function") {
          // No explicit preset: it would pin the provider server-side and
          // override the voice picker's saved provider for Apollo.
          window.DennisTTS.speak(full, { studio: "apollo" });
        } else if (apollo() && typeof apollo().speak === "function") {
          apollo().speak(full, null);
        }
      });
    }
    icons();
    return row;
  }

  function updateBriefCount(extra) {
    var el = $("jarvis-brief-count");
    if (el) el.textContent = (state.briefCount + (extra || 0)) + " saved";
  }

  function updateGenerateButton() {
    var gen = $("jarvis-brief-generate");
    var weekly = $("jarvis-brief-weekly");
    var busyLabel = state.briefKind === "weekly" ? "Synthesizing\u2026"
      : state.briefBusy ? "Synthesizing\u2026" : null;
    if (gen) {
      var span = gen.querySelector("span");
      var label = busyLabel && state.briefKind !== "weekly" ? "Synthesizing\u2026"
        : (!state.briefOnline) ? "Start service"
        : state.briefBusy ? "Busy\u2026" : "Generate daily";
      if (span) span.textContent = label;
      gen.disabled = state.briefBusy;
      gen.classList.toggle("busy", state.briefBusy && state.briefKind !== "weekly");
    }
    if (weekly) {
      var wspan = weekly.querySelector("span");
      var wlabel = busyLabel && state.briefKind === "weekly" ? "Synthesizing\u2026"
        : (!state.briefOnline) ? "Start service"
        : state.briefBusy ? "Busy\u2026" : "Weekly";
      if (wspan) wspan.textContent = wlabel;
      weekly.disabled = state.briefBusy;
      weekly.classList.toggle("busy", state.briefBusy && state.briefKind === "weekly");
    }
  }

  function loadBriefings() {
    if (state.briefBusy) return;
    var list = $("jarvis-brief-list");
    if (!list) return;
    api("/api/digest").then(function (d) {
      if (!list.isConnected) return;
      list.innerHTML = "";
      state.briefCount = 0;
      var online = !!(d && d.available === true);
      state.briefOnline = online;
      if (!online) {
        list.innerHTML = '<p class="text-[11px] text-slate-500">Briefing module offline. ' +
          esc((d && d.reason) ? d.reason : "Native briefing module unavailable.") + "</p>";
        updateGenerateButton();
        updateBriefCount();
        return;
      }
      // Latest weekly briefing first (WEEKLY badge, full text).
      api("/api/digest/weekly").then(function (w) {
        if (!list.isConnected) return;
        if (w && w.available === true && w.weekly) {
          var wrow = briefingRow("WEEKLY",
            fmtWhen(w.weekly.generated_at), w.weekly.text, { latest: true });
          list.insertBefore(wrow, list.firstChild);
          state.briefCount += 1;
        }
        if (!d.digest && !(w && w.weekly)) {
          list.innerHTML = '<p class="text-[11px] text-slate-500">No briefings yet. Generate one below &mdash; or say &ldquo;Hey Apollo, daily briefing&rdquo;.</p>';
        }
        updateBriefCount();
        icons();
      });
      if (d.digest) {
        list.appendChild(briefingRow("DAILY",
          fmtWhen(d.digest.generated_at), d.digest.text, { latest: true }));
        state.briefCount += 1;
      }
      api("/api/digest/history").then(function (h) {
        if (!list.isConnected) return;
        var items = (h && h.available === true) ? (h.items || []) : [];
        var latestAt = (d.digest && d.digest.generated_at) || "";
        items.forEach(function (it) {
          if (latestAt && it.generated_at === latestAt) return; // already shown in full
          list.appendChild(briefingRow("DAILY", fmtWhen(it.generated_at), it.text || "", {}));
          state.briefCount += 1;
        });
        updateBriefCount();
        icons();
      });
      updateGenerateButton();
    });
  }

  function generateBriefing(kind) {
    if (state.briefBusy) return;
    kind = kind === "weekly" ? "weekly" : "daily";
    var title = kind === "weekly" ? "Generate weekly briefing" : "Generate daily briefing";
    var message = "This reads live local OS state (connections, COSMOS, Genesis, Trading Den, audit, voice) and writes a briefing plus cached audio under ~/.jarvis/briefings. Continue?";
    var ask = (window.JarvisOs && typeof window.JarvisOs.confirm === 'function')
      ? window.JarvisOs.confirm(title, message, "Sources: backend/jarvis_os.py + backend/tts.py")
      : Promise.resolve(window.confirm(message));
    ask.then(function (yes) {
      if (!yes) return;
      state.briefKind = kind;
      state.briefBusy = true;
      updateGenerateButton();
      hudToast(kind === "weekly"
        ? "Building your weekly briefing from local OS state, sir\u2026"
        : "Building your daily briefing from local OS state, sir\u2026");
      return postJSON('/api/digest/generate', { kind: kind, confirmed: true })
        .then(function (d) {
          state.briefBusy = false;
          state.briefKind = "";
          updateGenerateButton();
          if (d && d.available) {
            hudToast(kind === "weekly"
              ? "Your weekly briefing is ready, sir."
              : "Your daily briefing is ready, sir.");
          } else {
            hudToast("Briefing failed. " + ((d && (d.reason || d.error)) || "Check the native briefing module."));
          }
          loadBriefings();
        });
    }).catch(function () {
      state.briefBusy = false;
      state.briefKind = "";
      updateGenerateButton();
      hudToast("The request failed \u2014 the OS backend may be restarting.");
    });
  }

  // ------------------------------------------------------------------
  // Public API
  // ------------------------------------------------------------------
  var JarvisDashboard = {
    refresh: function () {
      if (!state.initialized) return;
      updateWakeControls();
      loadTelemetry();
      loadBriefings();
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
    // Shared Provider → Model → Voice picker on the bottom-right voice control.
    // Apollo (JARVIS voice) is the default scope; Global default is also here.
    if (window.DennisVoicePicker && $("jarvis-bar-voice")) {
      window.DennisVoicePicker.mount("jarvis-bar-voice", {
        scopes: ["apollo", "global"],
        registry: true,
        placement: "up"
      });
    }
    bindCommandBar();
    startClock();
    updateWakeControls();
    loadTelemetry();
    loadBriefings();
    // Voice-core state reacts in ~2s (DOM-only); network cards every 30s.
    if (state.teleTimer) clearInterval(state.teleTimer);
    state.teleTimer = setInterval(updateWakeControls, 2000);
    if (state.quoteTimer) clearInterval(state.quoteTimer);
    state.quoteTimer = setInterval(rotateQuote, 15000);
    if (state.briefTimer) clearInterval(state.briefTimer);
    state.briefTimer = setInterval(loadBriefings, 30000);
    if (state.refreshTimer) clearInterval(state.refreshTimer);
    state.refreshTimer = setInterval(loadTelemetry, 30000);
  });
})();
