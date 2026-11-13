/* =====================================================================
   PLUGINS — API Mega List upstream repository workspace.

   This is deliberately not a second API catalog. The repository's own
   README.md, category READMEs, pipeline scripts, and supporting files are
   the workspace. The only backend writes are an explicit upstream sync.
   ===================================================================== */
(function () {
  "use strict";

  var PREVIEW_LINES = 320;
  var IMAGE_EXT = /\.(png|jpe?g|gif|webp|svg|ico|woff2?|ttf)$/i;

  var state = {
    view: null,
    initialized: false,
    busy: false,
    status: null,
    tree: null,
    current: "README.md",
    currentData: null,
    raw: false,
    full: false,
    loading: false
  };

  function $(id) { return document.getElementById(id); }

  function esc(value) {
    return String(value == null ? "" : value).replace(/[&<>"']/g, function (ch) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch];
    });
  }

  function icons() {
    if (window.lucide && window.lucide.createIcons) {
      try { window.lucide.createIcons(); } catch (e) { /* no-op */ }
    }
  }

  function fmt(number) {
    return Number(number || 0).toLocaleString();
  }

  function bytes(number) {
    number = Number(number || 0);
    if (number >= 1024 * 1024) return (number / (1024 * 1024)).toFixed(1) + " MB";
    if (number >= 1024) return Math.round(number / 1024) + " KB";
    return number + " B";
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

  function setStatus(text, warning) {
    var el = $("mg-status-text");
    var pill = $("mg-status-pill");
    if (el) el.textContent = text || "Ready";
    if (pill) {
      pill.classList.toggle("sb-pill-ok", !warning);
      pill.classList.toggle("sb-pill-warn", !!warning);
    }
  }

  function setBusy(on, label) {
    state.busy = !!on;
    ["mg-sync", "mg-raw", "mg-render-full"].forEach(function (id) {
      var el = $(id);
      if (el) el.disabled = !!on;
    });
    if (on && label) setStatus(label, false);
  }

  function compactDate(value) {
    return String(value || "").replace("T", " ").slice(0, 16).replace("+0800", "");
  }

  /* ------------------------------------------------------------------ */
  /* Status + tree                                                       */
  /* ------------------------------------------------------------------ */
  function loadStatus() {
    return api("/api/megalist/status").then(function (result) {
      if (!result.ok) {
        setStatus((result.data && result.data.error) || "Repository unavailable", true);
        return;
      }
      state.status = result.data;
      renderStatus();
    }).catch(function (error) {
      setStatus("Cannot reach the repository backend", true);
      console.error("megalist status", error);
    });
  }

  function renderStatus() {
    var s = state.status || {};
    var badge = $("mg-total-badge");
    if (badge) badge.textContent = s.api_count ? fmt(s.api_count) + " APIs" : "Repository";

    var source = $("mg-source");
    var github = $("mg-github");
    if (source && s.source) source.href = s.source;
    if (github && s.source) github.href = s.source;

    var meta = $("mg-source-meta");
    if (meta) {
      var bits = [];
      if (s.commit) bits.push((s.branch || "main") + " @ " + String(s.commit).slice(0, 8));
      if (s.synced_at) bits.push("synced " + compactDate(s.synced_at));
      meta.textContent = bits.length ? " · " + bits.join(" · ") : "";
    }

    var stats = [];
    if (s.core_category_count) stats.push('<span class="mg-stat-chip">' + fmt(s.core_category_count) + " core categories</span>");
    if (s.category_count) stats.push('<span class="mg-stat-chip">' + fmt(s.category_count) + " total categories</span>");
    if (s.featured_count) stats.push('<span class="mg-stat-chip">' + fmt(s.featured_count) + " featured collection</span>");
    if (s.sponsored_count) stats.push('<span class="mg-stat-chip">' + fmt(s.sponsored_count) + " sponsored</span>");
    if (s.last_updated) stats.push('<span class="mg-stat-chip">Updated ' + esc(s.last_updated) + "</span>");
    if (s.files) stats.push('<span class="mg-stat-chip">' + fmt(s.files) + " files · " + bytes(s.bytes) + "</span>");
    if (s.commit) stats.push('<span class="mg-stat-chip">commit ' + esc(String(s.commit).slice(0, 8)) + "</span>");
    var host = $("mg-stats");
    if (host) host.innerHTML = stats.join("");

    if (s.repo_ready) {
      setStatus(s.api_count ? fmt(s.api_count) + " APIs · upstream snapshot" : "Upstream snapshot ready", false);
    } else {
      setStatus("Repository snapshot missing", true);
    }
  }

  function loadTree() {
    return api("/api/megalist/tree").then(function (result) {
      if (!result.ok) {
        setStatus((result.data && result.data.error) || "Cannot read repository tree", true);
        return;
      }
      state.tree = result.data;
      renderTree();
      renderPipeline();
    }).catch(function (error) {
      setStatus("Cannot read repository tree", true);
      console.error("megalist tree", error);
    });
  }

  function treePath(node) {
    if (!node) return "";
    if (node.type === "dir") return node.readme || node.path;
    return node.path;
  }

  function treeButton(node) {
    var path = treePath(node);
    var active = state.current === path || state.current === node.path;
    var icon = node.type === "dir" ? "folder" : (/\.(js|cjs|mjs)$/i.test(path) ? "file-code" : "file-text");
    var count = node.count ? '<span class="mg-tree-count">' + fmt(node.count) + "</span>" : "";
    var title = node.description || node.title || path;
    return '<button type="button" class="mg-tree-item' + (active ? " mg-tree-item-active" : "") +
      '" data-mg-path="' + esc(path) + '" title="' + esc(title) + '">' +
      '<i data-lucide="' + icon + '" class="w-3 h-3 shrink-0"></i>' +
      '<span class="mg-tree-label">' + esc(node.label || node.name || path) + "</span>" + count +
      "</button>";
  }

  function renderTree() {
    var host = $("mg-tree");
    var count = $("mg-tree-count");
    if (!host) return;
    var sections = (state.tree && state.tree.sections) || [];
    if (!sections.length) {
      host.innerHTML = '<div class="sb-rail-empty">The repository tree is empty.</div>';
      return;
    }
    var total = 0;
    host.innerHTML = sections.map(function (section) {
      var items = section.items || [];
      total += items.length;
      return '<div class="mg-tree-section">' + esc(section.label || section.id) + "</div>" +
        items.map(treeButton).join("");
    }).join("");
    if (count) count.textContent = fmt(total) + " entries";
    icons();
  }

  function renderPipeline() {
    var host = $("mg-pipeline");
    if (!host) return;
    var steps = (state.tree && state.tree.pipeline) || [];
    if (!steps.length) {
      host.innerHTML = '<span class="text-[10px] text-slate-500">Pipeline scripts are not present in this snapshot.</span>';
      return;
    }
    host.innerHTML = steps.map(function (step, index) {
      return '<button type="button" class="mg-pipeline-step" data-mg-path="' + esc(step.path) +
        '" title="' + esc(step.description || step.path) + '">' +
        '<span class="mg-step-num">' + (index + 1) + "</span>" +
        '<span>' + esc(step.name) + "</span></button>";
    }).join("");
    icons();
  }

  /* ------------------------------------------------------------------ */
  /* Document loading + markdown                                        */
  /* ------------------------------------------------------------------ */
  function openFile(path, opts) {
    opts = opts || {};
    path = String(path || "README.md").trim() || "README.md";
    if (state.loading) return Promise.resolve();
    state.loading = true;
    state.current = path;
    if (!opts.keep) {
      state.raw = false;
      state.full = false;
    }
    renderTree();
    setBusy(true, "Reading " + path + "…");

    return api("/api/megalist/file?path=" + encodeURIComponent(path)).then(function (result) {
      if (!result.ok) {
        state.currentData = null;
        state.loading = false;
        setBusy(false);
        setStatus((result.data && result.data.error) || "Cannot read file", true);
        var content = $("mg-content");
        if (content) content.innerHTML = '<div class="sb-rail-empty">' + esc((result.data && result.data.error) || "Cannot read file") + "</div>";
        return;
      }
      state.currentData = result.data;
      state.current = result.data.path || path;
      renderDocument();
      renderTree();
      setStatus((state.status && state.status.api_count ? fmt(state.status.api_count) + " APIs · " : "") +
        "upstream snapshot", false);
      if (opts.hash) scrollToHash(opts.hash);
    }).catch(function (error) {
      setStatus("Cannot read " + path, true);
      console.error("megalist file", error);
    }).then(function () {
      state.loading = false;
      setBusy(false);
    });
  }

  function renderDocument() {
    var data = state.currentData;
    var host = $("mg-content");
    var foot = $("mg-doc-foot");
    var footText = $("mg-doc-foot-text");
    var fullButton = $("mg-render-full");
    if (!host || !data) return;

    var title = $("mg-doc-title");
    if (title) title.textContent = data.title || data.name || data.path;
    var meta = $("mg-doc-meta");
    if (meta) meta.textContent = data.path + " · " + bytes(data.size) + " · " + fmt(data.lines) + " lines";
    var github = $("mg-open-github");
    if (github) github.href = data.github_url;
    var rawButton = $("mg-raw");
    if (rawButton) {
      rawButton.classList.toggle("hidden", data.kind !== "markdown");
      var rawLabel = rawButton.querySelector("span");
      if (rawLabel) rawLabel.textContent = state.raw ? "Preview" : "Raw";
    }

    host.innerHTML = "";
    var isMarkdown = data.kind === "markdown" && !state.raw;
    if (!isMarkdown) {
      var pre = document.createElement("pre");
      pre.className = "mg-raw";
      pre.textContent = data.text || "";
      host.appendChild(pre);
      if (foot) foot.classList.add("hidden");
      icons();
      return;
    }

    var lines = String(data.text || "").split(/\r?\n/);
    var truncated = !state.full && lines.length > PREVIEW_LINES;
    var source = truncated ? lines.slice(0, PREVIEW_LINES).join("\n") : (data.text || "");
    var html = "";
    try {
      if (window.marked && window.marked.parse) {
        html = window.marked.parse(source, { gfm: true, breaks: false });
      } else {
        html = "";
      }
    } catch (error) {
      console.error("marked", error);
      html = "";
    }
    if (!html) {
      var fallback = document.createElement("pre");
      fallback.className = "mg-raw";
      fallback.textContent = source;
      host.appendChild(fallback);
    } else {
      var wrapper = document.createElement("div");
      wrapper.className = "mg-markdown";
      wrapper.appendChild(sanitize(html, data.path));
      host.appendChild(wrapper);
    }

    if (foot) {
      var showFoot = truncated || state.full || lines.length > PREVIEW_LINES;
      foot.classList.toggle("hidden", !showFoot);
      if (footText) {
        footText.textContent = state.full
          ? "Showing all " + fmt(lines.length) + " lines."
          : "Previewing " + Math.min(PREVIEW_LINES, lines.length) + " of " + fmt(lines.length) + " lines.";
      }
      if (fullButton) {
        var label = fullButton.querySelector("span");
        if (label) label.textContent = state.full ? "Show preview" : "Render full file";
      }
    }
    icons();
  }

  function scrollToHash(hash) {
    if (!hash) return;
    var host = $("mg-content");
    if (!host) return;
    var target = null;
    try { target = host.querySelector(hash); } catch (e) { target = null; }
    if (target && target.scrollIntoView) {
      target.scrollIntoView({ block: "start" });
    }
  }

  /* ------------------------------------------------------------------ */
  /* Safe HTML rendering for the repository's GFM + inline HTML         */
  /* ------------------------------------------------------------------ */
  function resolveRelative(basePath, href) {
    var out = { path: basePath || "README.md", hash: "" };
    var raw = String(href || "").trim();
    if (!raw) return out;
    var hashAt = raw.indexOf("#");
    if (hashAt >= 0) {
      out.hash = raw.slice(hashAt);
      raw = raw.slice(0, hashAt);
    }
    if (!raw) return out;
    var base = "https://repo.local/" + encodeURI(basePath || "README.md").replace(/#/g, "%23");
    var url;
    try { url = new URL(raw, base); } catch (e) { return out; }
    var path = url.pathname.replace(/^\/+/, "");
    try { path = decodeURIComponent(path); } catch (e) { /* keep encoded */ }
    out.path = path || "README.md";
    if (url.hash) out.hash = url.hash;
    return out;
  }

  function headingSlug(text) {
    return String(text || "")
      .toLowerCase()
      .replace(/[^\w\- ]+/g, "")
      .replace(/\s+/g, "-");
  }

  function sanitize(html, basePath) {
    var template = document.createElement("template");
    template.innerHTML = html;

    template.content.querySelectorAll(
      "script,iframe,object,embed,link,style,form,input,button,textarea,select,option,meta,base,frame,frameset,applet,audio,video,source,track,canvas"
    ).forEach(function (el) { el.remove(); });

    // Marked v15 does not emit GitHub-style heading ids. The upstream README
    // links to those anchors, so restore them for in-document navigation.
    var usedHeadings = {};
    template.content.querySelectorAll("h1,h2,h3,h4,h5,h6").forEach(function (heading) {
      if (heading.id) return;
      var base = headingSlug(heading.textContent || "");
      if (!base) return;
      var slug = base;
      var suffix = 1;
      while (usedHeadings[slug]) {
        slug = base + "-" + suffix;
        suffix += 1;
      }
      usedHeadings[slug] = true;
      heading.id = slug;
    });

    Array.prototype.forEach.call(template.content.querySelectorAll("*"), function (el) {
      Array.prototype.slice.call(el.attributes).forEach(function (attr) {
        var name = String(attr.name || "").toLowerCase();
        var value = String(attr.value || "");
        if (name.indexOf("on") === 0 || name === "style" || name === "srcset" ||
            name === "srcdoc" || name === "formaction" || name === "xlink:href") {
          el.removeAttribute(attr.name);
          return;
        }
        if ((name === "href" || name === "src") && /^\s*(javascript|vbscript|data:text\/html)/i.test(value)) {
          el.removeAttribute(attr.name);
        } else if (name === "href" && /^\s*data:/i.test(value)) {
          el.removeAttribute(attr.name);
        }
      });

      if (el.tagName === "IMG") {
        var src = el.getAttribute("src") || "";
        if (!src) { el.remove(); return; }
        if (/^https?:\/\//i.test(src) || /^data:image\/(png|jpe?g|gif|webp)[;,]/i.test(src)) {
          // Leave remote images and safe raster data images alone.
        } else if (/^data:/i.test(src)) {
          el.remove();
          return;
        } else if (/^\/\//.test(src)) {
          el.setAttribute("src", "https:" + src);
        } else {
          var image = resolveRelative(basePath, src);
          el.setAttribute("src", "/api/megalist/asset?path=" + encodeURIComponent(image.path));
        }
        el.setAttribute("loading", "lazy");
        el.setAttribute("referrerpolicy", "no-referrer");
        return;
      }

      if (el.tagName !== "A") return;
      var href = el.getAttribute("href") || "";
      if (!href) return;
      if (href.charAt(0) === "#") return;
      if (/^\/\//.test(href)) {
        el.setAttribute("href", "https:" + href);
        el.setAttribute("target", "_blank");
        el.setAttribute("rel", "noopener noreferrer");
        return;
      }
      if (/^(https?:|mailto:|tel:)/i.test(href)) {
        el.setAttribute("target", "_blank");
        el.setAttribute("rel", "noopener noreferrer");
        return;
      }
      var target = resolveRelative(basePath, href);
      if (IMAGE_EXT.test(target.path)) {
        el.setAttribute("href", "/api/megalist/asset?path=" + encodeURIComponent(target.path));
        el.setAttribute("target", "_blank");
        el.setAttribute("rel", "noopener noreferrer");
        return;
      }
      el.setAttribute("href", "#");
      el.setAttribute("data-mg-path", target.path);
      if (target.hash) el.setAttribute("data-mg-hash", target.hash);
      el.setAttribute("title", "Open " + target.path + " in the repository workspace");
    });

    return template.content;
  }

  /* ------------------------------------------------------------------ */
  /* Sync                                                               */
  /* ------------------------------------------------------------------ */
  function sync() {
    if (state.busy) return;
    setBusy(true, "Syncing upstream repository…");
    api("/api/megalist/sync", { method: "POST" }).then(function (result) {
      if (!result.ok) {
        setStatus((result.data && result.data.error) || "Sync failed", true);
        return;
      }
      state.status = result.data.status || state.status;
      state.raw = false;
      state.full = false;
      renderStatus();
      return loadTree().then(function () {
        return openFile("README.md");
      });
    }).catch(function (error) {
      setStatus("Sync failed: " + error.message, true);
    }).then(function () {
      setBusy(false);
    });
  }

  /* ------------------------------------------------------------------ */
  /* Wiring                                                             */
  /* ------------------------------------------------------------------ */
  function init() {
    state.view = $("plugins-view");
    if (!state.view || state.initialized) return;
    state.initialized = true;

    var tree = $("mg-tree");
    if (tree) tree.addEventListener("click", function (event) {
      var item = event.target.closest("[data-mg-path]");
      if (!item) return;
      openFile(item.getAttribute("data-mg-path"));
    });

    var content = $("mg-content");
    if (content) content.addEventListener("click", function (event) {
      var link = event.target.closest("a[data-mg-path]");
      if (!link) return;
      event.preventDefault();
      openFile(link.getAttribute("data-mg-path"), { hash: link.getAttribute("data-mg-hash") });
    });

    var pipeline = $("mg-pipeline");
    if (pipeline) pipeline.addEventListener("click", function (event) {
      var step = event.target.closest("[data-mg-path]");
      if (!step) return;
      openFile(step.getAttribute("data-mg-path"));
    });

    var syncButton = $("mg-sync");
    if (syncButton) syncButton.addEventListener("click", sync);

    var raw = $("mg-raw");
    if (raw) raw.addEventListener("click", function () {
      if (!state.currentData) return;
      state.raw = !state.raw;
      renderDocument();
    });

    var full = $("mg-render-full");
    if (full) full.addEventListener("click", function () {
      if (!state.currentData) return;
      state.full = !state.full;
      renderDocument();
      var host = $("mg-content");
      if (host) host.scrollTop = 0;
    });

    refresh();
  }

  function refresh() {
    if (!state.initialized) { init(); return; }
    if (!state.view) return;
    loadStatus().then(loadTree).then(function () {
      return openFile(state.current || "README.md", { keep: true });
    });
    icons();
  }

  function open() {
    if (window.switchMainView) window.switchMainView("plugins");
    else refresh();
  }

  window.Plugins = { init: init, refresh: refresh, open: open };

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
