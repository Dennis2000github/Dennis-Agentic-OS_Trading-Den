// =========================================================================
// MEMORY GALAXY — the vault as a living star map (its own page).
// Notes are stars sized by their [[wikilink]] count; links are the gravity
// between them. The Preview bar above the workspace bar shows the note
// behind the selected star (title, path, excerpt) with one-tap open.
// =========================================================================

(function () {
  "use strict";

  var state = {
    initialized: false,
    nodes: [],           // {id, title, stem, links, x, y, vx, vy, r, color, dim}
    links: [],           // {s, t}
    byId: {},
    query: "",
    selected: null,      // node id
    hovered: null,
    raf: null,
    running: false,
    ox: 0, oy: 0,        // pan offset (world -> screen)
    scale: 1,
    dragging: false,
    dragged: false,
    dragStart: null,
    pointer: { x: 0, y: 0 },
    canvasW: 0,
    canvasH: 0,
    dpr: 1,
  };

  var PALETTE = [
    { match: /^Skills\//, color: "#c084fc" },
    { match: /^Hermes\//, color: "#22d3ee" },
    { match: /^Projects\//, color: "#34d399" },
    { match: /^Ideas/, color: "#fbbf24" },
  ];
  var BASE_COLOR = "#818cf8";

  function $(id) { return document.getElementById(id); }
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      if (c === _AMP) return _AMP + "amp;";
      if (c === "<") return _AMP + "lt;";
      if (c === ">") return _AMP + "gt;";
      if (c === '"') return _AMP + "quot;";
      return _AMP + "#39;";
    });
  }
  var _AMP = String.fromCharCode(38);
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
  // Layout: lightweight force simulation (repulsion + link springs).
  // ------------------------------------------------------------------
  function seedPositions() {
    var n = state.nodes.length;
    for (var i = 0; i < n; i++) {
      var node = state.nodes[i];
      var golden = Math.PI * (3 - Math.sqrt(5));
      var t = (i + 0.5) / Math.max(n, 1);
      var r = 360 * Math.sqrt(t);
      var a = i * golden;
      node.x = Math.cos(a) * r;
      node.y = Math.sin(a) * r * 0.72;
      node.vx = 0; node.vy = 0;
    }
  }

  function physicsStep() {
    var nodes = state.nodes;
    var links = state.links;
    if (!nodes.length) return false;
    var rep = 5200;               // repulsion constant
    var spring = 0.015;           // link spring
    var restLen = 150;            // rest length
    var damp = 0.86;
    var moved = 0;

    var i, j, a, b;
    for (i = 0; i < nodes.length; i++) {
      a = nodes[i];
      for (j = i + 1; j < nodes.length; j++) {
        b = nodes[j];
        var dx = b.x - a.x, dy = b.y - a.y;
        var d2 = dx * dx + dy * dy + 90;
        var f = rep / d2;
        var d = Math.sqrt(d2);
        var fx = (dx / d) * f, fy = (dy / d) * f;
        a.vx -= fx * 0.01; a.vy -= fy * 0.01;
        b.vx += fx * 0.01; b.vy += fy * 0.01;
      }
    }
    for (i = 0; i < links.length; i++) {
      a = state.byId[links[i].s]; b = state.byId[links[i].t];
      if (!a || !b) continue;
      var dx2 = b.x - a.x, dy2 = b.y - a.y;
      var dist = Math.sqrt(dx2 * dx2 + dy2 * dy2) || 1;
      var force = (dist - restLen) * spring;
      var ux = dx2 / dist, uy = dy2 / dist;
      a.vx += ux * force; a.vy += uy * force;
      b.vx -= ux * force; b.vy -= uy * force;
    }
    for (i = 0; i < nodes.length; i++) {
      a = nodes[i];
      // Gentle pull to center so the galaxy stays on screen.
      a.vx -= a.x * 0.0006; a.vy -= a.y * 0.0006;
      a.x += a.vx; a.y += a.vy;
      a.vx *= damp; a.vy *= damp;
      moved += Math.abs(a.vx) + Math.abs(a.vy);
    }
    return moved > nodes.length * 0.05;
  }

  // ------------------------------------------------------------------
  // Rendering
  // ------------------------------------------------------------------
  function nodeRadius(node) {
    return 3 + Math.min(9, Math.sqrt(node.links || 0) * 2.4);
  }

  function colorFor(node) {
    for (var i = 0; i < PALETTE.length; i++) {
      if (PALETTE[i].match.test(node.id)) return PALETTE[i].color;
    }
    return BASE_COLOR;
  }

  function applyFilter() {
    var q = state.query.trim().toLowerCase();
    for (var i = 0; i < state.nodes.length; i++) {
      var n = state.nodes[i];
      n.dim = !!q && (n.title || "").toLowerCase().indexOf(q) === -1 &&
        (n.id || "").toLowerCase().indexOf(q) === -1;
    }
  }

  function draw() {
    var canvas = $("mg-canvas");
    if (!canvas) { state.running = false; return; }
    var ctx = canvas.getContext("2d");
    var w = state.canvasW, h = state.canvasH;
    var dpr = state.dpr;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);

    var active = physicsStep();
    var ox = w / 2 + state.ox, oy = h / 2 + state.oy;
    var scale = state.scale;

    // Links first (under the stars).
    ctx.lineWidth = 1;
    for (var i = 0; i < state.links.length; i++) {
      var a = state.byId[state.links[i].s], b = state.byId[state.links[i].t];
      if (!a || !b) continue;
      var hot = state.selected && (a.id === state.selected || b.id === state.selected);
      var faded = (a.dim || b.dim);
      if (faded && !hot) continue;
      ctx.strokeStyle = hot ? "rgba(129, 140, 248, 0.85)" : "rgba(99, 102, 241, 0.18)";
      ctx.beginPath();
      ctx.moveTo(ox + a.x * scale, oy + a.y * scale);
      ctx.lineTo(ox + b.x * scale, oy + b.y * scale);
      ctx.stroke();
    }

    // Stars.
    for (var j = 0; j < state.nodes.length; j++) {
      var n = state.nodes[j];
      var x = ox + n.x * scale, y = oy + n.y * scale;
      var r = n.r * scale;
      var isSelected = state.selected === n.id;
      var isHover = state.hovered === n.id;
      if (n.dim && !isSelected) continue;

      var glow = ctx.createRadialGradient(x, y, 0, x, y, r * 3.2);
      var rgb = hexToRgb(n.color);
      var alpha = n.dim ? 0.12 : (isSelected || isHover ? 0.5 : 0.22);
      glow.addColorStop(0, "rgba(" + rgb + ", " + alpha + ")");
      glow.addColorStop(1, "rgba(" + rgb + ", 0)");
      ctx.fillStyle = glow;
      ctx.beginPath(); ctx.arc(x, y, r * 3.2, 0, Math.PI * 2); ctx.fill();

      ctx.fillStyle = n.dim ? "rgba(148, 163, 184, 0.25)" : n.color;
      ctx.beginPath(); ctx.arc(x, y, r, 0, Math.PI * 2); ctx.fill();

      if (isSelected) {
        ctx.strokeStyle = "rgba(255, 255, 255, 0.9)";
        ctx.lineWidth = 1.4;
        ctx.beginPath(); ctx.arc(x, y, r + 4.5, 0, Math.PI * 2); ctx.stroke();
        if (scale > 0.55 && state.canvasW > 560) {
          ctx.fillStyle = "rgba(226, 232, 240, 0.92)";
          ctx.font = "10px 'JetBrains Mono', monospace";
          ctx.textAlign = "center";
          ctx.fillText((n.title || "").slice(0, 34), x, y - r - 8);
        }
      }
    }

    state.running = active || state.dragging;
    if (state.running) state.raf = requestAnimationFrame(draw);
    else state.raf = null;
  }

  function hexToRgb(hex) {
    var h = hex.replace("#", "");
    if (h.length === 3) h = h[0] + h[0] + h[1] + h[1] + h[2] + h[2];
    var num = parseInt(h, 16);
    return ((num >> 16) & 255) + ", " + ((num >> 8) & 255) + ", " + (num & 255);
  }

  function wake() {
    if (!state.running && !state.raf) {
      state.running = true;
      state.raf = requestAnimationFrame(draw);
    }
  }

  function resize() {
    var canvas = $("mg-canvas");
    var wrap = $("mg-canvas-wrap");
    if (!canvas || !wrap) return;
    var rect = wrap.getBoundingClientRect();
    if (!rect.width || !rect.height) return;
    state.dpr = Math.min(2, window.devicePixelRatio || 1);
    state.canvasW = rect.width;
    state.canvasH = rect.height;
    canvas.width = Math.round(rect.width * state.dpr);
    canvas.height = Math.round(rect.height * state.dpr);
    wake();
  }

  // ------------------------------------------------------------------
  // Interaction
  // ------------------------------------------------------------------
  function toWorld(px, py) {
    var w = state.canvasW, h = state.canvasH;
    return {
      x: (px - w / 2 - state.ox) / state.scale,
      y: (py - h / 2 - state.oy) / state.scale,
    };
  }

  function nodeAt(px, py) {
    var p = toWorld(px, py);
    var best = null, bestD = 22 / state.scale;
    for (var i = 0; i < state.nodes.length; i++) {
      var n = state.nodes[i];
      if (n.dim) continue;
      var dx = n.x - p.x, dy = n.y - p.y;
      var d = Math.sqrt(dx * dx + dy * dy) - n.r;
      if (d < bestD) { bestD = d; best = n; }
    }
    return best;
  }

  function bindCanvas() {
    var canvas = $("mg-canvas");
    if (!canvas) return;

    canvas.addEventListener("mousedown", function (e) {
      state.dragging = true; state.dragged = false;
      var rect = canvas.getBoundingClientRect();
      state.dragStart = { x: e.clientX - rect.left, y: e.clientY - rect.top, ox: state.ox, oy: state.oy };
      canvas.style.cursor = "grabbing";
    });
    window.addEventListener("mousemove", function (e) {
      if (!state.dragging) return;
      var rect = canvas.getBoundingClientRect();
      var x = e.clientX - rect.left, y = e.clientY - rect.top;
      var dx = x - state.dragStart.x, dy = y - state.dragStart.y;
      if (Math.abs(dx) + Math.abs(dy) > 4) state.dragged = true;
      state.ox = state.dragStart.ox + dx;
      state.oy = state.dragStart.oy + dy;
      wake();
    });
    window.addEventListener("mouseup", function (e) {
      if (!state.dragging) return;
      state.dragging = false;
      canvas.style.cursor = "grab";
      if (!state.dragged) {
        var rect = canvas.getBoundingClientRect();
        var node = nodeAt(e.clientX - rect.left, e.clientY - rect.top);
        if (node) selectNode(node.id);
        else clearPreview();
      }
    });
    canvas.addEventListener("mousemove", function (e) {
      var rect = canvas.getBoundingClientRect();
      var node = nodeAt(e.clientX - rect.left, e.clientY - rect.top);
      state.hovered = node ? node.id : null;
      canvas.style.cursor = state.dragging ? "grabbing" : (node ? "pointer" : "grab");
    });
    canvas.addEventListener("wheel", function (e) {
      e.preventDefault();
      var factor = e.deltaY < 0 ? 1.12 : 0.89;
      state.scale = Math.min(3.2, Math.max(0.25, state.scale * factor));
      wake();
    }, { passive: false });
    window.addEventListener("resize", resize);
  }

  // ------------------------------------------------------------------
  // Preview bar (sits above the workspace bar)
  // ------------------------------------------------------------------
  function showPreview(node) {
    var empty = $("mg-preview-empty");
    var content = $("mg-preview-content");
    if (!empty || !content) return;
    empty.classList.add("hidden");
    content.classList.remove("hidden");
    $("mg-preview-title").textContent = node.title || node.stem || node.id;
    $("mg-preview-path").textContent = node.id;
    $("mg-preview-meta").textContent = (node.links || 0) + " links";
    $("mg-preview-excerpt").textContent = "Loading preview…";
    icons();

    api("/api/vault/note?path=" + encodeURIComponent(node.id)).then(function (r) {
      if (state.selected !== node.id) return;      // selection changed meanwhile
      var text = (r && r.content) || "";
      text = text.replace(/^---[\s\S]*?---/, "")   // strip front matter
                 .replace(/[#>*`_\[\]]/g, "")
                 .replace(/\s+/g, " ")
                 .trim();
      var el = $("mg-preview-excerpt");
      if (el) el.textContent = text ? text.slice(0, 240) + (text.length > 240 ? "…" : "")
                                    : "Empty note.";
    });
  }

  function clearPreview() {
    state.selected = null;
    var empty = $("mg-preview-empty");
    var content = $("mg-preview-content");
    if (empty) empty.classList.remove("hidden");
    if (content) content.classList.add("hidden");
    wake();
  }

  function selectNode(id) {
    state.selected = id;
    var node = state.byId[id];
    if (node) showPreview(node);
    wake();
  }

  // ------------------------------------------------------------------
  // Data
  // ------------------------------------------------------------------
  function refresh() {
    if (!state.initialized) return;
    api("/api/vault/graph").then(function (data) {
      var nodes = (data && data.nodes) || [];
      var links = (data && data.links) || [];
      var previous = state.byId || {};
      state.nodes = nodes.map(function (n) {
        var prev = previous[n.id];
        return {
          id: n.id, title: n.title || n.stem || n.id, stem: n.stem,
          links: n.links || 0,
          x: prev ? prev.x : 0, y: prev ? prev.y : 0,
          vx: 0, vy: 0,
          r: nodeRadius({ links: n.links || 0 }),
          color: colorFor(n),
          dim: false,
        };
      });
      state.byId = {};
      state.nodes.forEach(function (n) { state.byId[n.id] = n; });
      state.links = links
        .filter(function (l) { return state.byId[l.source] && state.byId[l.target]; })
        .map(function (l) { return { s: l.source, t: l.target }; });
      if (state.selected && !state.byId[state.selected]) clearPreview();
      applyFilter();

      var stats = $("mg-stats");
      if (stats) {
        stats.textContent = state.nodes.length + (state.nodes.length === 1 ? " star" : " stars") +
          " · " + state.links.length + (state.links.length === 1 ? " link" : " links");
      }
      var empty = $("mg-empty");
      if (empty) empty.classList.toggle("hidden", state.nodes.length > 0);
      if (state.nodes.length && !state.nodes.some(function (n) { return n.x || n.y; })) {
        seedPositions();
      }
      resize();
      wake();
    });
  }

  function recenter() {
    state.ox = 0; state.oy = 0; state.scale = 1;
    wake();
  }

  function reheat() {
    state.nodes.forEach(function (n) {
      n.vx += (Math.random() - 0.5) * 40;
      n.vy += (Math.random() - 0.5) * 40;
    });
    wake();
  }

  // ------------------------------------------------------------------
  // Init
  // ------------------------------------------------------------------
  function bind() {
    var refreshBtn = $("mg-refresh");
    if (refreshBtn) refreshBtn.addEventListener("click", refresh);
    var recenterBtn = $("mg-recenter");
    if (recenterBtn) recenterBtn.addEventListener("click", recenter);
    var reheatBtn = $("mg-reheat");
    if (reheatBtn) reheatBtn.addEventListener("click", reheat);
    var openVault = $("mg-open-vault");
    if (openVault) openVault.addEventListener("click", function () {
      if (state.selected && window.Vault && window.Vault.openPath) {
        window.Vault.openPath(state.selected);
      } else if (window.Vault && window.Vault.openGraph) {
        window.Vault.openGraph();
      }
    });
    var previewOpen = $("mg-preview-open");
    if (previewOpen) previewOpen.addEventListener("click", function () {
      if (state.selected && window.Vault && window.Vault.openPath) {
        window.Vault.openPath(state.selected);
      }
    });
    var previewClose = $("mg-preview-close");
    if (previewClose) previewClose.addEventListener("click", clearPreview);
    var search = $("mg-search");
    if (search) search.addEventListener("input", function () {
      state.query = search.value || "";
      applyFilter();
      wake();
    });
    bindCanvas();
  }

  var MemoryGalaxy = { refresh: refresh };
  window.MemoryGalaxy = MemoryGalaxy;
  /* Deterministic test surface (canvas coordinates differ per viewport). */
  window.__mg_debug = {
    get firstNodeId() { return state.nodes.length ? state.nodes[0].id : null; },
    get nodeCount() { return state.nodes.length; },
    select: function (id) {
      if (!state.byId[id]) return false;
      selectNode(id);
      return true;
    },
  };

  function ready(fn) {
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", fn);
    else fn();
  }

  ready(function () {
    if (!$("memory-galaxy-view")) return;
    state.initialized = true;
    bind();
    resize();
    refresh();
  });
})();
