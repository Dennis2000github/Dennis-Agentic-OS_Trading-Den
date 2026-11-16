/* =====================================================================
   CONNECTED SKILLS — every connected or generated skill, one list.

   Sources (merged and deduped by name, with a source badge each):
     · Vault            — notes under vault/Skills/  (/api/vault/notes)
     · Skill Builder    — published skill packages   (/api/skill-builder/registry)
     · Plugin packs     — skills shipped by plugins  (/api/plugins/installed)
     · Agent-linked     — SKILL.md linked into agent dirs (/api/plugins/skills)

   Rows deep-link to their owner: Vault rows open the Vault, Skill
   Builder rows open the Skill Builder, plugin/agent rows open the
   Tools → Installed Plugins panel.
   ===================================================================== */
(function () {
  "use strict";

  var SOURCES = ["vault", "builder", "plugin", "agent"];

  var state = {
    initialized: false,
    loading: false,
    items: [],
    query: "",
    filter: "all"
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
    var el = $("cs-toast");
    if (!el) return;
    el.textContent = message;
    el.dataset.kind = isError ? "error" : "info";
    el.classList.add("show");
    clearTimeout(toast._t);
    toast._t = setTimeout(function () { el.classList.remove("show"); }, 3200);
  }

  function setStatus(text, isError) {
    var el = $("cs-status-text");
    if (el) el.textContent = text;
    var pill = $("cs-status-pill");
    if (pill) pill.classList.toggle("sb-pill-warn", !!isError);
  }

  function api(path) {
    return fetch(path, { method: "GET", cache: "no-store" }).then(function (response) {
      return response.json().catch(function () { return {}; }).then(function (data) {
        return { ok: response.ok, status: response.status, data: data };
      });
    });
  }

  var SOURCE_META = {
    vault: { label: "Vault", icon: "book-marked", action: "vault", actionLabel: "Vault" },
    builder: { label: "Skill Builder", icon: "wrench", action: "skillbuilder", actionLabel: "Builder" },
    plugin: { label: "Plugin pack", icon: "package-check", action: "plugins", actionLabel: "Plugins" },
    agent: { label: "Agent-linked", icon: "bot", action: "plugins", actionLabel: "Plugins" }
  };

  function sourceIcon(source) {
    var meta = SOURCE_META[source] || {};
    return meta.icon || "sparkles";
  }

  function addSource(item, source) {
    if (item.sources.indexOf(source) < 0) item.sources.push(source);
  }

  function upsert(map, order, item) {
    var key = String(item.name || "").toLowerCase();
    if (!key) return;
    if (!map[key]) { map[key] = item; order.push(key); return; }
    var existing = map[key];
    item.sources.forEach(function (source) { addSource(existing, source); });
    if (!existing.description && item.description) existing.description = item.description;
    if (!existing.path && item.path) existing.path = item.path;
    if (!existing.agents.length && item.agents.length) existing.agents = item.agents;
    if (item.version && (!existing.version || existing.version < item.version)) existing.version = item.version;
  }

  function collectVault(tree, out) {
    (tree || []).forEach(function (node) {
      if (node.type === "dir") {
        collectVault(node.children || [], out);
        return;
      }
      var path = String(node.path || "");
      if (!/^skills\//i.test(path)) return;
      out.push({
        name: String(node.name || "").replace(/\.md$/i, ""),
        title: node.title || node.name,
        description: "",
        path: path,
        sources: ["vault"]
      });
    });
  }

  function merge(results) {
    var map = {}, order = [];

    // Vault: every note under Skills/.
    var vaultSkills = [];
    collectVault((results[0].data && results[0].data.tree) || [], vaultSkills);
    vaultSkills.forEach(function (item) { upsert(map, order, {
      name: item.name, title: item.title, description: item.description,
      path: item.path, sources: ["vault"], agents: [], version: ""
    }); });

    // Skill Builder: published packages with version history.
    ((results[1].data && results[1].data.skills) || []).forEach(function (record) {
      upsert(map, order, {
        name: record.name || "",
        title: record.name || "",
        description: "",
        path: record.path || "",
        sources: ["builder"],
        agents: [],
        version: record.current_version || "",
        publishedAt: record.published_at || "",
        versions: record.versions || 0
      });
    });

    // Plugin packs: skills shipped inside installed plugins.
    ((results[2].data && results[2].data.items) || []).forEach(function (plugin) {
      (plugin.skills || []).forEach(function (skill) {
        var name = typeof skill === "string" ? skill : (skill && (skill.name || skill.title));
        if (!name) return;
        upsert(map, order, {
          name: name,
          title: (typeof skill === "object" && (skill.title || skill.name)) || name,
          description: (plugin.title || plugin.name || "") + " plugin pack" +
            (plugin.tag ? " · " + plugin.tag : ""),
          path: "",
          sources: ["plugin"],
          agents: plugin.agents || []
        });
      });
    });

    // Agent-linked: SKILL.md discovered in agent skill directories.
    ((results[3].data && results[3].data.skills) || []).forEach(function (record) {
      upsert(map, order, {
        name: record.name || record.title || "",
        title: record.title || record.name || "",
        description: record.description || "",
        path: record.path || "",
        sources: ["agent"],
        agents: record.agents || []
      });
    });

    return order.map(function (key) { return map[key]; });
  }

  function row(item) {
    var badges = item.sources.map(function (source) {
      return '<span class="cs-badge cs-badge-' + esc(source) + '">' +
        '<i data-lucide="' + sourceIcon(source) + '" class="w-3 h-3"></i>' +
        esc((SOURCE_META[source] || {}).label || source) + '</span>';
    }).join("");
    var meta = [];
    if (item.version) meta.push("v" + esc(item.version));
    if (item.versions) meta.push(item.versions + " versions");
    if (item.agents && item.agents.length) meta.push(esc(item.agents.join(" · ")));
    var action = (SOURCE_META[item.sources[0]] || {});
    var actionButton = action.action
      ? '<button type="button" class="sb-btn sb-btn-ghost" data-cs-open="' + esc(action.action) + '">' +
        '<i data-lucide="' + sourceIcon(item.sources[0]) + '" class="w-3.5 h-3.5"></i><span>' +
        esc(action.actionLabel || "Open") + '</span></button>'
      : "";
    return '<div class="cs-row">' +
      '<span class="ca-logo ca-logo-flat cs-logo"><i data-lucide="graduation-cap" class="w-4 h-4"></i></span>' +
      '<span class="ca-row-main"><strong>' + esc(item.title || item.name) + '</strong>' +
      '<small>' + (esc(item.description) || esc(item.path || "")) + '</small>' +
      '<span class="cs-badges">' + badges + '</span></span>' +
      '<span class="ca-row-meta">' + meta.join(" · ") + '</span>' +
      actionButton +
      '</div>';
  }

  function visibleItems() {
    var query = state.query.trim().toLowerCase();
    return state.items.filter(function (item) {
      if (state.filter !== "all" && item.sources.indexOf(state.filter) < 0) return false;
      if (!query) return true;
      var haystack = [item.name, item.title, item.description, item.path]
        .concat(item.sources, item.agents || []).join(" ").toLowerCase();
      return haystack.indexOf(query) >= 0;
    });
  }

  function render() {
    var counts = { vault: 0, builder: 0, plugin: 0, agent: 0 };
    state.items.forEach(function (item) {
      item.sources.forEach(function (source) { if (counts[source] != null) counts[source] += 1; });
    });

    if ($("cs-count-total")) $("cs-count-total").textContent = String(state.items.length);
    if ($("cs-count-vault")) $("cs-count-vault").textContent = String(counts.vault);
    if ($("cs-count-builder")) $("cs-count-builder").textContent = String(counts.builder);
    if ($("cs-count-agents")) {
      // Agent-linked counts standalone agent rows; plugin rows are separate.
      $("cs-count-agents").textContent = String(counts.agent + counts.plugin);
    }

    var items = visibleItems();
    var host = $("cs-list");
    if (host) host.innerHTML = items.length
      ? items.map(row).join("")
      : '<div class="sb-rail-empty">No skills match. Try another search or source.</div>';
    if ($("cs-result-count")) {
      $("cs-result-count").textContent = items.length + " of " + state.items.length + " skills";
    }

    document.dispatchEvent(new CustomEvent("dennis:hub-count", { detail: { key: "skills", value: state.items.length } }));
    icons();
  }

  function openTarget(target) {
    if (target === "vault") {
      if (window.switchMainView) window.switchMainView("vault");
      return;
    }
    if (target === "skillbuilder") {
      if (window.switchMainView) window.switchMainView("skill-builder");
      return;
    }
    if (target === "plugins" && window.PluginsHub) {
      window.PluginsHub.show("tools", "plugins");
      return;
    }
  }

  function bind() {
    var host = $("cs-list");
    if (host && !host._csBound) {
      host._csBound = true;
      host.addEventListener("click", function (event) {
        var button = event.target.closest("[data-cs-open]");
        if (button) openTarget(button.getAttribute("data-cs-open"));
      });
    }
    var search = $("cs-search");
    if (search && !search._csBound) {
      search._csBound = true;
      search.addEventListener("input", function () {
        state.query = search.value || "";
        render();
      });
    }
    var filter = $("cs-source-filter");
    if (filter && !filter._csBound) {
      filter._csBound = true;
      filter.addEventListener("click", function (event) {
        var button = event.target.closest("[data-cs-source]");
        if (!button) return;
        state.filter = button.getAttribute("data-cs-source") || "all";
        Array.prototype.forEach.call(filter.querySelectorAll(".cs-seg-btn"), function (el) {
          el.classList.toggle("cs-seg-btn-active", el === button);
        });
        render();
      });
    }
    if ($("cs-refresh") && !$("cs-refresh")._csBound) {
      $("cs-refresh")._csBound = true;
      $("cs-refresh").addEventListener("click", function () { load(); });
    }
    var regRefresh = $("cs-reg-refresh");
    if (regRefresh && !regRefresh._regBound) {
      regRefresh._regBound = true;
      regRefresh.addEventListener("click", function () { regLoad(); });
    }
  }

  function load() {
    if (state.loading) return;
    state.loading = true;
    setStatus("Loading…");
    return Promise.all([
      api("/api/vault/notes"),
      api("/api/skill-builder/registry"),
      api("/api/plugins/installed"),
      api("/api/plugins/skills")
    ]).then(function (results) {
      state.items = merge(results);
      var failed = results.some(function (result) { return !result.ok; });
      setStatus(failed ? "Partial data (a source failed)" : "Up to date", failed);
      render();
    }).catch(function (error) {
      setStatus("Load failed", true);
      toast("Connected Skills failed to load: " + error.message, true);
    }).then(function () {
      state.loading = false;
    });
  }

  /* =====================================================================
     UNIVERSAL SHARED SKILL REGISTRY — one canonical source, every
     compatible agent, on-demand load. Backed by /api/skills (see
     backend/skill_access.py). The list above stays the legacy 4-source
     view; this section adds the registry operations.
     ===================================================================== */

  var REG = {
    tab: "all",
    search: "",
    agent: "",
    items: [],
    matrix: [],
    loading: false,
    selected: null,
    detail: null,
    executing: false
  };

  function regApi(path, options) {
    return fetch(path, options || { method: "GET", cache: "no-store" })
      .then(function (response) {
        return response.json().catch(function () { return {}; }).then(function (data) {
          return { ok: response.ok, status: response.status, data: data };
        });
      });
  }

  function regPost(path, payload) {
    return regApi(path, {
      method: "POST", cache: "no-store",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload || {})
    });
  }

  function regEnabled(item) {
    return item.enabled !== false;
  }

  function regStatus(item) {
    if (!item.enabled) return ['cs-reg-disabled', 'disabled'];
    if (item.validation_status === "validated") return ['cs-reg-valid', 'validated'];
    return ['cs-reg-warn', item.validation_status || 'unvalidated'];
  }

  function regRow(item) {
    var status = regStatus(item);
    var agents = (item.compatible_agents || []).slice(0, 4).join(", ") ||
      "unassigned";
    var tags = (item.capability_tags || []).slice(0, 3).map(function (tag) {
      return '<span class="cs-reg-tag">' + esc(tag) + '</span>';
    }).join("");
    var actions = [];
    actions.push('<button type="button" class="sb-btn sb-btn-ghost" data-reg-action="open" data-reg-id="' + esc(item.id) + '"><i data-lucide="eye" class="w-3 h-3"></i><span>View</span></button>');
    actions.push('<button type="button" class="sb-btn sb-btn-ghost" data-reg-action="load" data-reg-id="' + esc(item.id) + '" data-reg-agent="' + esc(REG.agent || "claude-code") + '" ' + (regEnabled(item) ? "" : "disabled") + ' title="Inject instructions into the agent context"><i data-lucide="download-cloud" class="w-3 h-3"></i><span>Load</span></button>');
    if (item.enabled) {
      actions.push('<button type="button" class="sb-btn sb-btn-ghost" data-reg-action="disable" data-reg-id="' + esc(item.id) + '" title="Block new invocations"><i data-lucide="power" class="w-3 h-3"></i><span>Disable</span></button>');
    } else {
      actions.push('<button type="button" class="sb-btn sb-btn-ghost" data-reg-action="enable" data-reg-id="' + esc(item.id) + '" title="Allow new invocations"><i data-lucide="power" class="w-3 h-3"></i><span>Enable</span></button>');
    }
    actions.push('<button type="button" class="sb-btn sb-btn-ghost" data-reg-action="validate" data-reg-id="' + esc(item.id) + '" title="Re-run integrity + secret checks"><i data-lucide="shield-check" class="w-3 h-3"></i><span>Validate</span></button>');
    actions.push('<button type="button" class="sb-btn sb-btn-ghost" data-reg-action="remove" data-reg-id="' + esc(item.id) + '" title="Remove registration (keeps the Vault source)"><i data-lucide="unlink" class="w-3 h-3"></i><span>Remove</span></button>');
    return '<div class="cs-row">' +
      '<span class="ca-logo ca-logo-flat cs-logo"><i data-lucide="package-open" class="w-4 h-4"></i></span>' +
      '<span class="ca-row-main"><strong>' + esc(item.title || item.name) + '</strong>' +
      '<small>' + (esc(item.description) || esc(item.path || "")) + '</small>' +
      '<span class="cs-badges">' + tags +
      '<span class="cs-badge cs-badge-reg">registry</span></span></span>' +
      '<span class="ca-row-meta">v' + esc(item.version || "?") + ' · ' +
      esc(item.installation_status || "unknown") + ' · <b class="' + status[0] + '">' + status[1] + '</b>' +
      (item.updated_at ? ' · ' + esc(item.updated_at) : "") + '</span>' +
      '<span class="ca-row-meta" title="' + esc(agents) + '">' + esc(agents) + '</span>' +
      '<span class="cs-row-actions">' + actions.join("") + '</span>' +
      '</div>';
  }

  function regRender() {
    if (!$("cs-reg-list")) return;
    var q = REG.search.trim().toLowerCase();
    var items = REG.items.filter(function (item) {
      if (REG.tab === "enabled" && !regEnabled(item)) return false;
      if (REG.tab === "disabled" && regEnabled(item)) return false;
      if (REG.agent && (item.compatible_agents || []).indexOf(REG.agent) < 0) return false;
      if (!q) return true;
      var hay = [item.name, item.title, item.description,
        (item.capability_tags || []).join(" "), (item.categories || []).join(" ")]
        .join(" ").toLowerCase();
      return hay.indexOf(q) >= 0;
    });
    $("cs-reg-list").innerHTML = items.length
      ? items.map(regRow).join("")
      : '<div class="sb-rail-empty">No registry skills match. Adjust search or filters.</div>';
    if ($("cs-reg-count")) $("cs-reg-count").textContent = items.length + " of " + REG.items.length + " registered";
    icons();
  }

  function regLoad(options) {
    REG.loading = true;
    var qs = "?enabled_only=0&limit=100";
    if (REG.agent) qs += "&agent=" + encodeURIComponent(REG.agent);
    return Promise.all([
      regApi("/api/skills" + qs),
      regApi("/api/skills/capabilities")
    ]).then(function (results) {
      REG.items = (results[0].data && results[0].data.skills) || [];
      REG.matrix = (results[1].data && results[1].data.agents) || [];
      var failed = results.some(function (r) { return !r.ok; });
      if (failed) toast("Registry loaded partially", true);
      regRender();
      regRenderMatrix();
      REG.loading = false;
    }).catch(function (error) {
      REG.loading = false;
      toast("Skill Registry failed to load: " + error.message, true);
    });
  }

  function regRenderMatrix() {
    var host = $("cs-reg-matrix");
    if (!host) return;
    if (!REG.matrix.length) {
      host.innerHTML = '<div class="sb-rail-empty">No agent capability rows.</div>';
      return;
    }
    host.innerHTML = REG.matrix.map(function (row) {
      var cls = row.level === "executable" ? "cs-reg-valid" :
        row.level === "instructions" ? "cs-reg-warn" : "cs-reg-bad";
      var methods = (row.access_methods || []).join(", ") || "none";
      return '<div class="cs-reg-matrix-row">' +
        '<span class="cs-reg-agent">' + esc(row.agent_id) + '</span>' +
        '<span class="cs-reg-level ' + cls + '">' + esc(row.level) + '</span>' +
        '<span class="cs-reg-methods" title="' + esc(methods) + '">' + esc(methods) + '</span>' +
        '</div>';
    }).join("");
  }

  function regDetail(id) {
    return Promise.all([
      regApi("/api/skills/" + encodeURIComponent(id)),
      regApi("/api/skills/" + encodeURIComponent(id) + "/dependencies")
    ]).then(function (results) {
      REG.detail = {
        skill: results[0].data || {},
        deps: results[1].data || {}
      };
      regRenderDetail();
    });
  }

  function regRenderDetail() {
    var overlay = $("cs-reg-modal");
    var body = $("cs-reg-modal-body");
    if (!overlay || !body || !REG.detail) return;
    var s = REG.detail.skill || {};
    var d = REG.detail.deps || {};
    var tags = (s.capability_tags || []).map(function (t) {
      return '<span class="cs-reg-tag">' + esc(t) + '</span>';
    }).join("");
    var reqs = (s.execution_requirements || []).join(", ") || "none";
    var deps = (d.mandatory || []).map(function (m) {
      return '<li class="' + (m.present ? "cs-reg-valid" : "cs-reg-bad") + '">' +
        esc(m.name) + " — " + (m.present ? "present" : "missing") + "</li>";
    }).join("");
    var ext = (d.external || []).map(function (m) {
      return '<li class="cs-reg-warn">' + esc(m.name) + " — external, never auto-installed</li>";
    }).join("");
    body.innerHTML =
      '<h3 class="cs-reg-title">' + esc(s.name || "") + ' <span class="cs-reg-version">v' + esc(s.version || "?") + '</span></h3>' +
      '<p class="cs-reg-desc">' + esc(s.description || "") + '</p>' +
      '<div class="cs-reg-grid">' +
      '<div><span class="hud-label">Status</span><b>' + esc(s.installation_status || "") + ' · ' + esc(s.validation_status || "") + '</b></div>' +
      '<div><span class="hud-label">Enabled</span><b>' + (s.enabled === false ? "disabled" : "enabled") + '</b></div>' +
      '<div><span class="hud-label">Categories</span><b>' + esc((s.categories || []).join(", ") || "—") + '</b></div>' +
      '<div><span class="hud-label">Compatible agents</span><b>' + esc((s.compatible_agents || []).join(", ") || "unassigned") + '</b></div>' +
      '<div><span class="hud-label">Execution requirements</span><b>' + esc(reqs) + '</b></div>' +
      '<div><span class="hud-label">Canonical path</span><b>' + esc(s.path || "") + '</b></div>' +
      '</div>' +
      '<div class="cs-reg-block"><span class="hud-label">Capability tags</span><div class="cs-badges">' + (tags || '<i>none</i>') + '</div></div>' +
      '<div class="cs-reg-block"><span class="hud-label">Runtime dependencies</span><ul class="cs-reg-deps">' + (deps || "<li>none mandatory</li>") + (ext || "") + '</ul></div>' +
      '<div class="cs-reg-block"><span class="hud-label">Source</span><b>' + esc((s.source && s.source.url) || (typeof s.source === "string" ? s.source : "") || "vault") + '</b></div>' +
      '<div class="cs-reg-block"><span class="hud-label">Instructions (SKILL.md)</span>' +
      '<pre class="cs-reg-skillmd">' + esc(s.skill_md ? String(s.skill_md).slice(0, 4000) : "(load the skill to read its instructions)") + '</pre></div>' +
      '<div class="cs-reg-block"><span class="hud-label">Canonical install</span>' +
      '<p class="cs-reg-install-hint">One canonical source in the Vault; agents reach it by symlink or instruction injection. External skills (e.g. img2threejs) are fetched once by the operator, never copied per agent.</p></div>';
    overlay.classList.add("show");
    icons();
  }

  function regExecute(id, agentId) {
    // Load = instruction injection into the agent's context (the only
    // consumption method every agent supports). Script execution is an
    // owner-gated operator action (backend/skill_execution.py); the UI
    // never claims it happened.
    REG.executing = true;
    regPost("/api/skills/" + encodeURIComponent(id) + "/load", { agent_id: agentId })
      .then(function (loaded) {
        if (!loaded.ok) throw new Error((loaded.data && loaded.data.error) || "load failed");
        var level = (loaded.data.consumption || []).indexOf("native") >= 0 ? "native skills" :
          "instruction injection";
        toast("Loaded into %s via %s — script execution is owner-gated"
          .replace("%s", agentId).replace("%s", level));
        return regPost("/api/skills/" + loaded.data.context_id + "/release");
      })
      .then(function () {
        REG.executing = false;
        regLoad();
      })
      .catch(function (error) {
        REG.executing = false;
        toast("Load failed: " + error.message, true);
      });
  }

  function regBind() {
    var refresh = $("cs-reg-refresh");
    if (refresh && !refresh._regBound) {
      refresh._regBound = true;
      refresh.addEventListener("click", function () { regLoad(); });
    }
    var host = $("cs-reg-list");
    if (host && !host._regBound) {
      host._regBound = true;
      host.addEventListener("click", function (event) {
        var button = event.target.closest("[data-reg-action]");
        if (!button) return;
        var id = button.getAttribute("data-reg-id");
        var action = button.getAttribute("data-reg-action");
        var agent = button.getAttribute("data-reg-agent") || REG.agent || "claude-code";
        if (action === "open") {
          regDetail(id);
        } else if (action === "load") {
          regExecute(id, agent);
        } else if (action === "disable" || action === "enable") {
          regPost("/api/skills/" + encodeURIComponent(id) + "/enable",
            { enabled: action === "enable" }).then(function () { regLoad(); });
        } else if (action === "validate") {
          regPost("/api/skills/" + encodeURIComponent(id) + "/validate")
            .then(function (res) {
              if (res.ok && res.data && res.data.ok) toast("Skill validated");
              else toast("Validation found problems", true);
              regLoad();
              if (REG.detail && REG.detail.skill && REG.detail.skill.name) {
                regDetail("/api/skills/" + encodeURIComponent(id));
              }
            });
        } else if (action === "remove") {
          if (!window.confirm("Remove this skill's registration? The canonical Vault source is kept.")) return;
          regPost("/api/skills/" + encodeURIComponent(id) + "/remove")
            .then(function () { regLoad(); });
        }
      });
    }
    var search = $("cs-reg-search");
    if (search && !search._regBound) {
      search._regBound = true;
      search.addEventListener("input", function () {
        REG.search = search.value || "";
        regRender();
      });
    }
    var seg = $("cs-reg-filter");
    if (seg && !seg._regBound) {
      seg._regBound = true;
      seg.addEventListener("click", function (event) {
        var button = event.target.closest("[data-reg-tab]");
        if (!button) return;
        REG.tab = button.getAttribute("data-reg-tab") || "all";
        Array.prototype.forEach.call(seg.querySelectorAll(".cs-seg-btn"), function (el) {
          el.classList.toggle("cs-seg-btn-active", el === button);
        });
        regRender();
      });
    }
    var agentSel = $("cs-reg-agent");
    if (agentSel && !agentSel._regBound) {
      agentSel._regBound = true;
      agentSel.addEventListener("change", function () {
        REG.agent = agentSel.value;
        regLoad();
      });
    }
    var matrixToggle = $("cs-reg-matrix-toggle");
    if (matrixToggle && !matrixToggle._regBound) {
      matrixToggle._regBound = true;
      matrixToggle.addEventListener("click", function () {
        var panel = $("cs-reg-matrix-panel");
        if (panel) panel.classList.toggle("hidden");
      });
    }
    var modalClose = $("cs-reg-modal-close");
    if (modalClose && !modalClose._regBound) {
      modalClose._regBound = true;
      modalClose.addEventListener("click", function () {
        var overlay = $("cs-reg-modal");
        if (overlay) overlay.classList.remove("show");
      });
    }
  }

  function regInit() {
    if (!$("cs-reg-list") || REG.initDone) return;
    REG.initDone = true;
    regBind();
    regLoad();
  }

  function init() {
    if (!$("tools-panel-skills") || state.initialized) return;
    state.initialized = true;
    bind();
    regInit();
  }

  function refresh() {
    init();
    if (state.initialized) load();
    regInit();
  }

  window.ConnectedSkills = { init: init, refresh: refresh };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
