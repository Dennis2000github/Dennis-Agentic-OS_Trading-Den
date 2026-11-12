/*
 * Vault — Obsidian-compatible Markdown knowledge base for DENNIS Agentic OS.
 *
 * A single-page notes browser: tree, CodeMirror Markdown editor, live preview
 * with [[wikilinks]], backlinks panel, search and a link graph. All storage is
 * plain `.md` files served by backend/vault.py under the OS origin.
 *
 * Defensive by design: when its DOM contract is absent it does nothing.
 */
(function () {
  "use strict";

  var state = {
    token: null,
    vault: "",
    tree: [],
    index: {},          // lowercased title/stem -> path
    current: null,      // { path, title, content }
    dirty: false,
    mode: "edit",       // edit | preview | graph
    cm: null,
    graph: { nodes: [], links: [], raf: null, scale: 1, ox: 0, oy: 0,
             dragging: null, panning: false, px: 0, py: 0, inited: false },
    searchTimer: null,
  };

  function $(id) { return document.getElementById(id); }
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function icons() { if (window.lucide && window.lucide.createIcons) window.lucide.createIcons(); }
  function ready(fn) {
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", fn);
    else fn();
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

  function toast(msg, kind) {
    if (window.showToast) { window.showToast(msg); return; }
    // Fallback only if app.js did not load.
    var t = document.createElement("div");
    t.textContent = msg;
    t.style.cssText = "position:fixed;bottom:3rem;right:1.5rem;z-index:60;background:#0f172a;color:#e2e8f0;padding:8px 14px;border-radius:8px;font-size:12px";
    document.body.appendChild(t);
    setTimeout(function () { t.remove(); }, 2200);
  }

  // ======================================================================
  // TREE
  // ======================================================================
  function indexTree(nodes, acc) {
    acc = acc || {};
    (nodes || []).forEach(function (n) {
      if (n.type === "dir") { indexTree(n.children, acc); }
      else {
        acc[n.name.replace(/\.(md|markdown)$/i, "").toLowerCase()] = n.path;
        if (n.title) acc[n.title.toLowerCase()] = n.path;
        acc[n.path.toLowerCase()] = n.path;
      }
    });
    return acc;
  }

  function renderTree() {
    var host = $("vault-tree");
    if (!host) return;
    host.innerHTML = "";
    if (!state.tree.length) {
      host.innerHTML = '<p class="text-slate-500 text-[11px] p-2">No notes yet. Create one.</p>';
      return;
    }
    host.appendChild(renderNodes(state.tree));
    markActive();
  }

  function renderNodes(nodes) {
    var frag = document.createDocumentFragment();
    (nodes || []).forEach(function (node) {
      if (node.type === "dir") {
        var col = document.createElement("div");
        var head = document.createElement("div");
        head.className = "vault-tree-folder";
        head.innerHTML = '<i data-lucide="folder" class="w-3.5 h-3.5"></i><span>' + esc(node.name) + "</span>";
        var kids = document.createElement("div");
        kids.className = "vault-tree-children";
        kids.appendChild(renderNodes(node.children));
        head.addEventListener("click", function () { kids.classList.toggle("hidden"); });
        col.appendChild(head); col.appendChild(kids);
        frag.appendChild(col);
      } else {
        var item = document.createElement("div");
        item.className = "vault-tree-item";
        item.dataset.path = node.path;
        item.innerHTML = '<i data-lucide="file-text" class="w-3.5 h-3.5 text-purple-400/80 shrink-0"></i>' +
          '<span class="vault-tree-name">' + esc(node.title || node.name) + "</span>";
        item.addEventListener("click", function () { openNote(node.path); });
        frag.appendChild(item);
      }
    });
    return frag;
  }

  function markActive() {
    var host = $("vault-tree");
    if (!host) return;
    host.querySelectorAll(".vault-tree-item").forEach(function (el) {
      el.classList.toggle("active", !!state.current && el.dataset.path === state.current.path);
    });
  }

  // ======================================================================
  // NOTES
  // ======================================================================
  function loadTree() {
    return api("/api/vault/notes").then(function (r) {
      if (!r.ok) { toast((r.data && r.data.error) || "Vault unavailable"); return; }
      state.vault = r.data.vault || "";
      state.tree = r.data.tree || [];
      state.index = indexTree(state.tree);
      var p = $("vault-path"); if (p) p.textContent = state.vault;
      renderTree();
    });
  }

  function ensureEditor() {
    if (state.cm || !window.CodeMirror) return;
    var ta = $("vault-editor");
    if (!ta) return;
    state.cm = CodeMirror.fromTextArea(ta, {
      mode: "markdown",
      lineNumbers: true,
      lineWrapping: true,
      theme: "default",
      styleActiveLine: true,
      matchBrackets: true,
      autoCloseBrackets: true,
    });
    state.cm.on("change", function () { setDirty(true); });
    state.cm.setOption("extraKeys", {
      "Cmd-S": function () { saveNote(); },
      "Ctrl-S": function () { saveNote(); },
    });
  }

  function setDirty(v) {
    state.dirty = v;
    var btn = $("vault-save-note");
    if (btn) btn.classList.toggle("opacity-60", !v);
  }

  function showEditor(on) {
    ["vault-note-toolbar", "vault-editor-wrap"].forEach(function (id) {
      var el = $(id); if (el) el.classList.toggle("hidden", !on);
    });
    var g = $("vault-graph-wrap"); if (g) g.classList.toggle("hidden", on || state.mode !== "graph");
    var e = $("vault-empty"); if (e) e.classList.toggle("hidden", on || state.mode === "graph");
  }

  function openNote(path) {
    if (state.dirty && !confirm("Discard unsaved changes?")) return;
    return api("/api/vault/note?path=" + encodeURIComponent(path)).then(function (r) {
      if (!r.ok) { toast((r.data && r.data.error) || "Cannot open note"); return; }
      state.current = { path: r.data.path, title: r.data.title, content: r.data.content };
      state.mode = "edit";
      setMode("edit");
      ensureEditor();
      if (state.cm) { state.cm.setValue(r.data.content); state.cm.clearHistory(); }
      var t = $("vault-note-title"); if (t) t.value = r.data.title;
      setDirty(false);
      markActive();
      closeSearch();
      loadBacklinks(r.data.path);
    });
  }

  function saveNote() {
    if (!state.current || !state.cm) return Promise.resolve();
    var content = state.cm.getValue();
    return api("/api/vault/note", { method: "POST", body: { path: state.current.path, content: content } })
      .then(function (r) {
        if (!r.ok) { toast((r.data && r.data.error) || "Save failed"); return; }
        state.current.content = content;
        state.current.title = r.data.title || state.current.title;
        setDirty(false);
        toast("Saved " + (r.data.title || state.current.path));
        loadTree().then(function () { markActive(); });
      });
  }

  function newNote() {
    var name = prompt("New note name:", "Untitled");
    if (name === null) return;
    var path = (name.trim() || "Untitled").replace(/[\\/]+/g, "-") + ".md";
    api("/api/vault/note", { method: "POST", body: { path: path, create: true } })
      .then(function (r) {
        if (!r.ok) { toast((r.data && r.data.error) || "Could not create note"); return; }
        return loadTree().then(function () { return openNote(r.data.path); });
      });
  }

  function renameNote() {
    if (!state.current) return;
    var proposed = state.current.title || state.current.path;
    var name = prompt("Rename note to:", proposed);
    if (name === null) return;
    var folder = state.current.path.indexOf("/") >= 0
      ? state.current.path.slice(0, state.current.path.lastIndexOf("/") + 1) : "";
    var dst = folder + (name.trim() || "Untitled").replace(/[\\/]+/g, "-") + ".md";
    api("/api/vault/rename", { method: "POST", body: { from: state.current.path, to: dst } })
      .then(function (r) {
        if (!r.ok) { toast((r.data && r.data.error) || "Rename failed"); return; }
        return loadTree().then(function () { return openNote(r.data.path); });
      });
  }

  function deleteNote() {
    if (!state.current) return;
    if (!confirm("Move \"" + state.current.title + "\" to .trash?")) return;
    api("/api/vault/delete", { method: "POST", body: { path: state.current.path } })
      .then(function (r) {
        if (!r.ok) { toast((r.data && r.data.error) || "Delete failed"); return; }
        state.current = null; setDirty(false);
        if (state.cm) state.cm.setValue("");
        showEditor(false); state.mode = "edit";
        var bl = $("vault-backlinks"); if (bl) bl.innerHTML = '<p class="text-slate-500 text-[11px]">No note selected.</p>';
        toast("Moved to .trash");
        loadTree();
      });
  }

  function loadBacklinks(path) {
    var host = $("vault-backlinks");
    if (!host) return;
    host.innerHTML = '<p class="text-slate-500 text-[11px]">Loading…</p>';
    api("/api/vault/backlinks?path=" + encodeURIComponent(path)).then(function (r) {
      var items = (r.ok && r.data.backlinks) || [];
      if (!items.length) { host.innerHTML = '<p class="text-slate-500 text-[11px]">No backlinks.</p>'; return; }
      host.innerHTML = "";
      items.forEach(function (b) {
        var el = document.createElement("div");
        el.className = "vault-tree-item";
        el.innerHTML = '<i data-lucide="corner-down-right" class="w-3.5 h-3.5 text-cyan-400/80 shrink-0"></i>' +
          '<span class="vault-tree-name">' + esc(b.title) + "</span>";
        el.addEventListener("click", function () { openNote(b.path); });
        host.appendChild(el);
      });
    });
  }

  // ======================================================================
  // MODES / PREVIEW
  // ======================================================================
  function setMode(mode) {
    state.mode = mode;
    var editBtn = $("vault-tab-edit"), prevBtn = $("vault-tab-preview");
    function style(btn, on) {
      if (!btn) return;
      btn.classList.toggle("bg-purple-600/30", on);
      btn.classList.toggle("border-purple-500/50", on);
      btn.classList.toggle("text-purple-200", on);
      btn.classList.toggle("text-slate-400", !on);
      btn.classList.toggle("border-transparent", !on);
    }
    style(editBtn, mode === "edit");
    style(prevBtn, mode === "preview");

    var editorWrap = $("vault-editor-wrap");
    var graphWrap = $("vault-graph-wrap");
    var empty = $("vault-empty");
    var toolbar = $("vault-note-toolbar");

    if (mode === "graph") {
      if (toolbar) toolbar.classList.add("hidden");
      if (editorWrap) editorWrap.classList.add("hidden");
      if (empty) empty.classList.add("hidden");
      if (graphWrap) graphWrap.classList.remove("hidden");
      showGraph();
      return;
    }
    var hasNote = !!state.current;
    if (toolbar) toolbar.classList.toggle("hidden", !hasNote);
    if (empty) empty.classList.toggle("hidden", hasNote);
    if (graphWrap) graphWrap.classList.add("hidden");
    if (!hasNote) { if (editorWrap) editorWrap.classList.add("hidden"); return; }
    if (editorWrap) editorWrap.classList.remove("hidden");

    var ta = $("vault-editor"), preview = $("vault-preview");
    if (mode === "preview") {
      if (state.cm) {
        state.cm.getWrapperElement().style.display = "none";
      } else if (ta) ta.classList.add("hidden");
      if (preview) {
        preview.classList.remove("hidden");
        preview.innerHTML = renderMarkdown(state.cm ? state.cm.getValue() : (state.current.content || ""));
        if (state.cm) state.cm.refresh();
      }
    } else {
      if (preview) preview.classList.add("hidden");
      if (state.cm) { state.cm.getWrapperElement().style.display = ""; state.cm.refresh(); }
      else if (ta) ta.classList.remove("hidden");
    }
  }

  // ---- minimal, safe Markdown -> HTML renderer --------------------------
  function renderMarkdown(md) {
    md = String(md || "");
    var blocks = [];
    md = md.replace(/```([\s\S]*?)```/g, function (_, code) {
      blocks.push(code.replace(/^\n/, ""));
      return "\u0000BLOCK" + (blocks.length - 1) + "\u0000";
    });

    var lines = md.split("\n");
    var html = [];
    var listType = null;
    function closeList() { if (listType) { html.push(listType === "ul" ? "</ul>" : "</ol>"); listType = null; } }

    lines.forEach(function (line) {
      var m;
      if ((m = /^(#{1,6})\s+(.*)$/.exec(line))) {
        closeList();
        var lvl = m[1].length;
        html.push("<h" + lvl + ">" + inline(m[2]) + "</h" + lvl + ">");
      } else if (/^\s*([-*_])\1{2,}\s*$/.test(line)) {
        closeList(); html.push("<hr>");
      } else if ((m = /^>\s?(.*)$/.exec(line))) {
        closeList(); html.push("<blockquote>" + inline(m[1]) + "</blockquote>");
      } else if ((m = /^\s*[-*+]\s+(.*)$/.exec(line))) {
        if (listType !== "ul") { closeList(); html.push("<ul>"); listType = "ul"; }
        html.push("<li>" + inline(m[1]) + "</li>");
      } else if ((m = /^\s*\d+\.\s+(.*)$/.exec(line))) {
        if (listType !== "ol") { closeList(); html.push("<ol>"); listType = "ol"; }
        html.push("<li>" + inline(m[1]) + "</li>");
      } else if (/^\s*$/.test(line)) {
        closeList();
      } else {
        closeList();
        html.push("<p>" + inline(line) + "</p>");
      }
    });
    closeList();

    var out = html.join("\n");
    out = out.replace(/\u0000BLOCK(\d+)\u0000/g, function (_, i) {
      return "<pre><code>" + esc(blocks[Number(i)]) + "</code></pre>";
    });
    return out;
  }

  function inline(text) {
    var s = esc(text);
    // Protect inline code spans first so [[..]] and *..* inside them stay literal.
    var codes = [];
    s = s.replace(/`([^`]+)`/g, function (_, c) {
      codes.push(c);
      return "\u0001C" + (codes.length - 1) + "\u0001";
    });
    s = s.replace(/!\[([^\]]*)\]\(([^)\s]+)\)/g, function (_, alt, src) {
      return '<img alt="' + alt + '" src="' + src + '">';
    });
    s = s.replace(/\[\[([^\]\|#]+)(?:#[^\]\|]+)?(?:\|([^\]]+))?\]\]/g, function (_, target, label) {
      var t = target.trim();
      var path = state.index[t.toLowerCase()];
      var text = (label || t);
      if (path) return '<a href="#" class="wikilink" data-wikilink="' + esc(path) + '">' + esc(text) + "</a>";
      return '<a href="#" class="wikilink missing" data-wikilink-new="' + esc(t) + '">' + esc(text) + "</a>";
    });
    s = s.replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, function (_, label, href) {
      var external = /^https?:\/\//i.test(href);
      var attrs = external ? ' target="_blank" rel="noopener"' : "";
      return '<a href="' + esc(href) + '"' + attrs + ">" + label + "</a>";
    });
    s = s.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
    s = s.replace(/__([^_]+)__/g, "<strong>$1</strong>");
    s = s.replace(/\*([^*]+)\*/g, "<em>$1</em>");
    s = s.replace(/_([^_]+)_/g, "<em>$1</em>");
    s = s.replace(/~~([^~]+)~~/g, "<del>$1</del>");
    s = s.replace(/\u0001C(\d+)\u0001/g, function (_, i) { return "<code>" + codes[Number(i)] + "</code>"; });
    return s;
  }

  // ======================================================================
  // GRAPH
  // ======================================================================
  function showGraph() {
    var wrap = $("vault-graph-wrap");
    var canvas = $("vault-graph-canvas");
    if (!wrap || !canvas) return;
    api("/api/vault/graph").then(function (r) {
      if (!r.ok) { toast((r.data && r.data.error) || "Graph unavailable"); return; }
      var nodes = (r.data.nodes || []).map(function (n, i) { return Object.assign({}, n, { x: 0, y: 0, vx: 0, vy: 0, i: i }); });
      state.graph.nodes = nodes;
      state.graph.links = (r.data.links || []).map(function (l) { return { s: l.source, t: l.target }; });
      state.graph.scale = 1; state.graph.ox = 0; state.graph.oy = 0;
      initGraphPositions();
      bindGraph(canvas);
      layoutAndDraw();
    });
  }

  function initGraphPositions() {
    var n = state.graph.nodes.length;
    state.graph.nodes.forEach(function (node, i) {
      var a = (i / Math.max(1, n)) * Math.PI * 2;
      node.x = Math.cos(a) * 120; node.y = Math.sin(a) * 120;
    });
  }

  function layoutAndDraw() {
    var g = state.graph;
    var canvas = $("vault-graph-canvas");
    if (!canvas || canvas.closest("#vault-graph-wrap").classList.contains("hidden")) { g.raf = null; return; }
    var dpr = window.devicePixelRatio || 1;
    var w = canvas.clientWidth, h = canvas.clientHeight;
    if (canvas.width !== w * dpr || canvas.height !== h * dpr) {
      canvas.width = w * dpr; canvas.height = h * dpr;
    }
    var ctx = canvas.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

    // tiny force step
    var nodes = g.nodes, byId = {};
    nodes.forEach(function (n) { byId[n.id] = n; });
    for (var step = 0; step < 2; step++) {
      nodes.forEach(function (a) {
        nodes.forEach(function (b) {
          if (a === b) return;
          var dx = a.x - b.x, dy = a.y - b.y;
          var d2 = dx * dx + dy * dy + 0.01;
          var f = 900 / d2;
          var d = Math.sqrt(d2);
          a.vx += (dx / d) * f; a.vy += (dy / d) * f;
        });
      });
      g.links.forEach(function (l) {
        var a = byId[l.s], b = byId[l.t];
        if (!a || !b) return;
        var dx = b.x - a.x, dy = b.y - a.y, d = Math.sqrt(dx * dx + dy * dy) + 0.01;
        var f = (d - 80) * 0.02;
        a.vx += (dx / d) * f; a.vy += (dy / d) * f;
        b.vx -= (dx / d) * f; b.vy -= (dy / d) * f;
      });
      nodes.forEach(function (n) {
        n.vx -= n.x * 0.002; n.vy -= n.y * 0.002;
        n.vx *= 0.86; n.vy *= 0.86;
        n.x += n.vx; n.y += n.vy;
      });
    }

    ctx.clearRect(0, 0, w, h);
    ctx.save();
    ctx.translate(w / 2 + g.ox, h / 2 + g.oy);
    ctx.scale(g.scale, g.scale);

    ctx.strokeStyle = "rgba(124,58,237,0.35)";
    ctx.lineWidth = 1 / g.scale;
    g.links.forEach(function (l) {
      var a = byId[l.s], b = byId[l.t];
      if (!a || !b) return;
      ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); ctx.stroke();
    });
    nodes.forEach(function (n) {
      var r = 4 + Math.min(6, (n.links || 0)) ;
      ctx.beginPath(); ctx.arc(n.x, n.y, r, 0, Math.PI * 2);
      ctx.fillStyle = state.current && state.current.path === n.id ? "#22d3ee" : "#a855f7";
      ctx.fill();
      ctx.font = (11 / g.scale) + "px ui-monospace, monospace";
      ctx.fillStyle = "rgba(226,232,240,0.85)";
      ctx.fillText(n.title || n.stem || n.id, n.x + r + 3, n.y + 3);
    });
    ctx.restore();
    g.raf = requestAnimationFrame(layoutAndDraw);
  }

  function bindGraph(canvas) {
    if (state.graph.inited) return;
    state.graph.inited = true;
    canvas.addEventListener("wheel", function (e) {
      e.preventDefault();
      var g = state.graph;
      g.scale = Math.max(0.3, Math.min(3, g.scale * (e.deltaY < 0 ? 1.1 : 0.9)));
    }, { passive: false });
    canvas.addEventListener("mousedown", function (e) {
      var g = state.graph;
      g.dragging = hitNode(e); g.panning = !g.dragging;
      g.px = e.offsetX; g.py = e.offsetY;
    });
    canvas.addEventListener("mousemove", function (e) {
      var g = state.graph;
      if (g.dragging) {
        var n = g.dragging;
        n.x += (e.offsetX - g.px) / g.scale; n.y += (e.offsetY - g.py) / g.scale;
        n.vx = 0; n.vy = 0; g.px = e.offsetX; g.py = e.offsetY;
      } else if (g.panning) {
        g.ox += e.offsetX - g.px; g.oy += e.offsetY - g.py;
        g.px = e.offsetX; g.py = e.offsetY;
      }
    });
    window.addEventListener("mouseup", function (e) {
      var g = state.graph;
      if (g.dragging) {
        var same = Math.abs(e.offsetX - g.px) < 3 && Math.abs(e.offsetY - g.py) < 3;
        var node = g.dragging; g.dragging = null;
        if (same) { var path = node.id; state.mode = "edit"; setMode("edit"); openNote(path); }
      }
      g.panning = false;
    });
  }

  function hitNode(e) {
    var g = state.graph, canvas = e.target;
    var w = canvas.clientWidth, h = canvas.clientHeight;
    var x = (e.offsetX - (w / 2 + g.ox)) / g.scale;
    var y = (e.offsetY - (h / 2 + g.oy)) / g.scale;
    for (var i = 0; i < g.nodes.length; i++) {
      var n = g.nodes[i], r = 8;
      if ((n.x - x) * (n.x - x) + (n.y - y) * (n.y - y) <= r * r) return n;
    }
    return null;
  }

  function stopGraph() {
    if (state.graph.raf) { cancelAnimationFrame(state.graph.raf); state.graph.raf = null; }
  }

  // ======================================================================
  // SEARCH
  // ======================================================================
  function onSearch(value) {
    clearTimeout(state.searchTimer);
    var q = (value || "").trim();
    if (!q) { closeSearch(); return; }
    state.searchTimer = setTimeout(function () {
      api("/api/vault/search?q=" + encodeURIComponent(q)).then(function (r) {
        var host = $("vault-search-results");
        var tree = $("vault-tree");
        if (!host) return;
        host.classList.remove("hidden");
        if (tree) tree.classList.add("hidden");
        var items = (r.ok && r.data.results) || [];
        if (!items.length) { host.innerHTML = '<p class="text-slate-500 text-[11px] p-2">No matches.</p>'; return; }
        host.innerHTML = "";
        items.forEach(function (it) {
          var el = document.createElement("div");
          el.className = "vault-tree-item flex-col !items-start gap-0.5";
          el.innerHTML = '<span class="font-semibold text-purple-200">' + esc(it.title) + "</span>" +
            '<span class="text-[10px] text-slate-500 truncate w-full">' + esc(it.snippet || it.path) + "</span>";
          el.addEventListener("click", function () { openNote(it.path); });
          host.appendChild(el);
        });
      });
    }, 180);
  }

  function closeSearch() {
    var host = $("vault-search-results"), tree = $("vault-tree");
    if (host) { host.classList.add("hidden"); host.innerHTML = ""; }
    if (tree) tree.classList.remove("hidden");
  }

  // ======================================================================
  // WIRING
  // ======================================================================
  function bind() {
    var search = $("vault-search");
    if (search) search.addEventListener("input", function () { onSearch(search.value); });
    var newBtn = $("vault-new-note"); if (newBtn) newBtn.addEventListener("click", newNote);
    var saveBtn = $("vault-save-note"); if (saveBtn) saveBtn.addEventListener("click", saveNote);
    var renBtn = $("vault-rename-note"); if (renBtn) renBtn.addEventListener("click", renameNote);
    var delBtn = $("vault-delete-note"); if (delBtn) delBtn.addEventListener("click", deleteNote);
    var gBtn = $("vault-graph-toggle");
    if (gBtn) gBtn.addEventListener("click", function () {
      if (state.mode === "graph") { setMode("edit"); }
      else { if (state.dirty) saveNote(); setMode("graph"); }
    });
    var eBtn = $("vault-tab-edit"); if (eBtn) eBtn.addEventListener("click", function () { setMode("edit"); });
    var pBtn = $("vault-tab-preview"); if (pBtn) pBtn.addEventListener("click", function () { setMode("preview"); });

    var title = $("vault-note-title");
    if (title) title.readOnly = true;

    var preview = $("vault-preview");
    if (preview) preview.addEventListener("click", function (e) {
      var a = e.target.closest("a.wikilink, a[data-wikilink]");
      if (!a) return;
      e.preventDefault();
      if (a.dataset.wikilink) { openNote(a.dataset.wikilink); return; }
      if (a.dataset.wikilinkNew) {
        var name = a.dataset.wikilinkNew;
        if (confirm('Create note "' + name + '"?')) {
          api("/api/vault/note", { method: "POST", body: { path: name + ".md", create: true } })
            .then(function (r) { if (r.ok) loadTree().then(function () { openNote(r.data.path); }); });
        }
      }
    });

    // token then first load
    api("/api/auth/session").then(function (r) {
      state.token = r.data && r.data.token;
      return loadTree();
    });
    icons();
  }

  // ======================================================================
  // PUBLIC API (used by sidebar, Quick Tools and the Apollo voice)
  // ======================================================================
  var Vault = {
    open: function () {
      if (window.switchMainView) window.switchMainView("vault");
      ensureEditor();
      setTimeout(function () { if (state.cm) state.cm.refresh(); }, 60);
    },
    openPath: function (path) {
      Vault.open();
      return openNote(path);
    },
    openGraph: function () {
      Vault.open();
      setTimeout(function () { if (state.dirty) saveNote(); setMode("graph"); }, 80);
    },
    reload: loadTree,
  };
  window.Vault = Vault;

  ready(function () {
    if (!$("vault-view")) return;
    bind();
    window.addEventListener("resize", function () {
      if (state.cm) state.cm.refresh();
      if (state.mode !== "graph") stopGraph();
    });
  });
})();
