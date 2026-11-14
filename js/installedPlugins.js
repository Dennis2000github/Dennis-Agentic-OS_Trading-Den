/* =====================================================================
   INSTALLED PLUGINS — one rail for apps, skill packs and GitHub repos.

   The backend (`GET /api/plugins/installed`) merges the live plugin folders
   under ~/.agents/plugins with the bundled catalog and the apps shipped with
   the OS. Every item carries a short `tag` (UX, MCP, API, LLM …) and a
   lifecycle status: installed | disabled | core | bundled | available.
   ===================================================================== */
(function () {
  "use strict";

  var state = {
    view: null,
    initialized: false,
    data: null,
    query: "",
    filter: "all",
    busy: false,
    modalItem: null
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

  function toast(message, isError) {
    var el = $("ip-toast");
    if (!el) return;
    el.textContent = message || "";
    el.classList.toggle("is-err", !!isError);
    el.classList.add("is-on");
    clearTimeout(toast._timer);
    toast._timer = setTimeout(function () {
      el.classList.remove("is-on");
    }, 3200);
  }

  function setStatus(text, warning) {
    var el = $("ip-status-text");
    var pill = $("ip-status-pill");
    if (el) el.textContent = text || "Ready";
    if (pill) {
      pill.classList.toggle("sb-pill-ok", !warning);
      pill.classList.toggle("sb-pill-warn", !!warning);
    }
  }

  function statusLabel(item) {
    return ({
      installed: "Installed",
      disabled: "Disabled",
      core: "Built-in",
      bundled: "Bundled",
      available: "Available"
    })[item.status] || item.status || "Plugin";
  }

  function itemClass(item) {
    return "ip-card is-" + esc(item.status || "available") + (item.status === "disabled" ? " is-disabled" : "");
  }

  function shortVersion(value) {
    return value ? "v" + String(value).replace(/^v/i, "") : "";
  }

  /* ------------------------------------------------------------------ */
  /* Sidebar + header                                                    */
  /* ------------------------------------------------------------------ */
  function syncSidebar(data) {
    var counts = (data && data.counts) || {};
    var badge = $("sidebar-installed-count");
    if (badge) {
      badge.textContent = String(counts.installed || 0);
      badge.classList.toggle("is-on", (counts.installed || 0) > 0);
    }
  }

  function renderSummary(data) {
    var counts = (data && data.counts) || {};
    var items = (data && data.items) || [];

    if ($("ip-total-badge")) {
      $("ip-total-badge").textContent = (counts.installed || 0) + " installed";
    }
    if ($("ip-count-installed")) $("ip-count-installed").textContent = String(counts.installed || 0);
    if ($("ip-count-disabled")) $("ip-count-disabled").textContent = (counts.disabled || 0) + " disabled";
    if ($("ip-count-bundled")) $("ip-count-bundled").textContent = String((counts.core || 0) + (counts.bundled || 0) + (counts.available || 0));
    if ($("ip-count-core")) $("ip-count-core").textContent = (counts.core || 0) + " built-in apps";
    if ($("ip-count-skills")) $("ip-count-skills").textContent = String(counts.skills || 0);
    if ($("ip-count-agents")) $("ip-count-agents").textContent = String(counts.agents || 0);
    if ($("ip-root-meta")) $("ip-root-meta").textContent = data && data.root ? " · " + data.root : "";
    if ($("ip-root-path")) $("ip-root-path").textContent = (data && data.root) || "~/.agents/plugins";

    var tags = [];
    var seen = {};
    items.forEach(function (item) {
      if (!item.tag || seen[item.tag]) return;
      seen[item.tag] = true;
      tags.push(item.tag);
    });
    var cloud = $("ip-tag-cloud");
    if (cloud) {
      cloud.innerHTML = tags.slice(0, 12).map(function (tag) {
        var count = items.filter(function (i) { return i.tag === tag; }).length;
        return '<button type="button" class="ip-cloud-tag" data-ip-tag="' + esc(tag) + '" title="Filter by ' + esc(tag) + '">' +
          "<b>" + esc(tag) + "</b><span>" + count + "</span></button>";
      }).join("");
    }

    setStatus((counts.installed || 0) + " installed · " + (counts.bundled || 0) + " bundled", false);
  }

  /* ------------------------------------------------------------------ */
  /* Cards                                                               */
  /* ------------------------------------------------------------------ */
  function skillChips(item) {
    var skills = (item && item.skills) || [];
    if (!skills.length) return "";
    var chips = skills.slice(0, 3).map(function (skill) {
      return '<span class="ip-chip ip-chip-skill" title="' + esc(skill.title || skill.name) + '">' +
        esc(skill.name || skill.title) + "</span>";
    });
    if (skills.length > 3) {
      chips.push('<span class="ip-chip ip-chip-more">+' + (skills.length - 3) + " skills</span>");
    }
    return chips.join("");
  }

  function agentChips(item) {
    var agents = (item && item.agents) || [];
    if (!agents.length) return "";
    var labels = {
      "generic": "generic",
      "claude-code": "Claude",
      "cursor": "Cursor",
      "codex": "Codex",
      "gemini-cli": "Gemini",
      "opencode": "OpenCode",
      "hermes": "Hermes"
    };
    var chips = agents.slice(0, 3).map(function (agent) {
      return '<span class="ip-chip ip-chip-agent" title="Skills linked into ' + esc(agent) + '">' +
        esc(labels[agent] || agent) + "</span>";
    });
    if (agents.length > 3) {
      chips.push('<span class="ip-chip ip-chip-more">+' + (agents.length - 3) + "</span>");
    }
    return chips.join("");
  }

  function actions(item) {
    var out = [];
    var id = esc(item.id || item.name);
    if (!item.installed && item.installable) {
      out.push('<button type="button" class="ip-act ip-act-primary" data-ip-action="install" data-ip-id="' + id + '">' +
        '<i data-lucide="download" class="w-3 h-3"></i><span>Install</span></button>');
    }
    if (item.installed) {
      if (item.installable) {
        out.push('<button type="button" class="ip-act" data-ip-action="toggle" data-ip-id="' + id + '" data-ip-enabled="' + (item.enabled ? "1" : "0") + '">' +
          '<i data-lucide="' + (item.enabled ? "power-off" : "power") + '" class="w-3 h-3"></i><span>' + (item.enabled ? "Disable" : "Enable") + "</span></button>");
      }
      if ((item.skill_count || (item.skills || []).length) > 0) {
        out.push('<button type="button" class="ip-act" data-ip-action="link" data-ip-id="' + id + '">' +
          '<i data-lucide="link" class="w-3 h-3"></i><span>Link skills</span></button>');
      }
    }
    if (item.view) {
      out.push('<button type="button" class="ip-act" data-ip-action="open" data-ip-view="' + esc(item.view) + '">' +
        '<i data-lucide="arrow-up-right" class="w-3 h-3"></i><span>Open app</span></button>');
    }
    if (item.repository) {
      out.push('<a class="ip-act" href="' + esc(item.repository) + '" target="_blank" rel="noopener noreferrer" title="' + esc(item.repository) + '">' +
        '<i data-lucide="github" class="w-3 h-3"></i><span>Repo</span></a>');
    }
    if (item.installed && item.installable) {
      out.push('<button type="button" class="ip-act ip-act-danger ip-foot-spacer" data-ip-action="remove" data-ip-id="' + id + '" title="Uninstall ' + esc(item.title) + '">' +
        '<i data-lucide="trash-2" class="w-3 h-3"></i></button>');
    }
    return out.join("");
  }

  function card(item) {
    var version = shortVersion(item.version);
    var sub = [version, item.category, item.author].filter(Boolean).join(" · ");
    var chips = skillChips(item) + agentChips(item);
    return '<article class="' + itemClass(item) + '" data-ip-card="' + esc(item.id || item.name) + '">' +
      '<div class="ip-card-head">' +
        '<div class="ip-card-icon"><i data-lucide="' + esc(item.icon || "puzzle") + '" class="w-4 h-4"></i></div>' +
        '<div class="ip-card-title-wrap">' +
          '<div class="ip-card-title-row">' +
            '<span class="ip-card-title" title="' + esc(item.title || item.name) + '">' + esc(item.title || item.name) + "</span>" +
            '<span class="ip-tag" title="Short tag">' + esc(item.tag || "PLG") + "</span>" +
          "</div>" +
          '<div class="ip-card-sub" title="' + esc(sub) + '">' + esc(sub || "Plugin") + "</div>" +
        "</div>" +
        '<span class="ip-status ip-status-' + esc(item.status || "available") + '">' + esc(statusLabel(item)) + "</span>" +
      "</div>" +
      '<p class="ip-card-desc">' + esc(item.description || "No description provided.") + "</p>" +
      '<div class="ip-chip-row">' + chips + "</div>" +
      '<div class="ip-card-foot">' + actions(item) + "</div>" +
    "</article>";
  }

  function matches(item) {
    if (state.filter === "installed" && item.status !== "installed" && item.status !== "disabled") return false;
    if (state.filter === "bundled" && !(item.status === "bundled" || item.status === "core" || item.status === "available")) return false;
    var query = state.query.trim().toLowerCase();
    if (!query) return true;
    var haystack = [
      item.id, item.name, item.title, item.tag, item.category, item.description,
      item.repository, item.author
    ].concat((item.skills || []).map(function (s) { return s.name + " " + (s.title || ""); }))
      .join(" ").toLowerCase();
    return haystack.indexOf(query) >= 0;
  }

  function renderGrid() {
    var host = $("ip-grid");
    if (!host) return;
    var items = ((state.data && state.data.items) || []).filter(matches);
    var count = $("ip-result-count");
    if (count) count.textContent = items.length + (items.length === 1 ? " item" : " items");
    if (!items.length) {
      host.innerHTML = '<div class="sb-rail-empty">No plugins match this search. Try another tag, or install a GitHub repository.</div>';
      icons();
      return;
    }
    host.innerHTML = items.map(card).join("");
    icons();
  }

  /* ------------------------------------------------------------------ */
  /* Load                                                                */
  /* ------------------------------------------------------------------ */
  function load() {
    if (state.busy) return;
    state.busy = true;
    setStatus("Reading installed plugins…", false);
    return api("/api/plugins/installed").then(function (result) {
      if (!result.ok) {
        setStatus((result.data && result.data.error) || "Cannot read the plugin registry", true);
        var host = $("ip-grid");
        if (host) host.innerHTML = '<div class="sb-rail-empty">' + esc((result.data && result.data.error) || "Cannot read the plugin registry") + "</div>";
        return;
      }
      state.data = result.data;
      renderSummary(result.data);
      syncSidebar(result.data);
      renderGrid();
    }).catch(function (error) {
      setStatus("Cannot reach the plugin backend", true);
      console.error("installed plugins", error);
    }).then(function () {
      state.busy = false;
    });
  }

  /* ------------------------------------------------------------------ */
  /* Actions                                                             */
  /* ------------------------------------------------------------------ */
  function findItem(id) {
    var items = (state.data && state.data.items) || [];
    for (var i = 0; i < items.length; i += 1) {
      if ((items[i].id || items[i].name) === id) return items[i];
    }
    return null;
  }

  function runAction(action, id, el) {
    var item = findItem(id);
    if (!item) return;

    if (action === "install") {
      if (el) el.disabled = true;
      toast("Installing " + item.title + "…");
      api("/api/plugins/install", { method: "POST", body: { catalog_id: item.id } }).then(function (result) {
        if (!result.ok) {
          toast((result.data && result.data.error) || "Install failed", true);
          if (el) el.disabled = false;
          return;
        }
        toast("Installed " + item.title + ".");
        return load();
      }).catch(function (error) {
        toast("Install failed: " + error.message, true);
        if (el) el.disabled = false;
      });
      return;
    }

    if (action === "toggle") {
      if (el) el.disabled = true;
      api("/api/plugins/toggle", {
        method: "POST",
        body: { name: item.name || item.id, enabled: !item.enabled, agents: item.agents || undefined }
      }).then(function (result) {
        if (!result.ok) {
          toast((result.data && result.data.error) || "Could not update the plugin", true);
          if (el) el.disabled = false;
          return;
        }
        toast(item.enabled ? "Disabled " + item.title + "." : "Enabled " + item.title + ".");
        return load();
      }).catch(function (error) {
        toast("Update failed: " + error.message, true);
        if (el) el.disabled = false;
      });
      return;
    }

    if (action === "remove") {
      if (!window.confirm("Uninstall " + item.title + "?\n\nSkills linked into agent folders are removed too.")) return;
      api("/api/plugins/uninstall", { method: "POST", body: { name: item.name || item.id } }).then(function (result) {
        if (!result.ok) {
          toast((result.data && result.data.error) || "Uninstall failed", true);
          return;
        }
        toast("Removed " + item.title + ".");
        return load();
      }).catch(function (error) {
        toast("Uninstall failed: " + error.message, true);
      });
      return;
    }

    if (action === "open") {
      var view = el && el.getAttribute("data-ip-view");
      if (view && window.switchMainView) window.switchMainView(view);
      return;
    }

    if (action === "link") openLinkModal(item);
  }

  /* ------------------------------------------------------------------ */
  /* Link-skills modal                                                   */
  /* ------------------------------------------------------------------ */
  function openLinkModal(item) {
    state.modalItem = item;
    var modal = $("ip-modal");
    var host = $("ip-modal-agents");
    if (!modal || !host) return;
    var agents = (state.data && state.data.agents) || [];
    var selected = item.agents || [];
    host.innerHTML = agents.map(function (agent) {
      var checked = selected.length ? selected.indexOf(agent.id) >= 0 : !!agent.present;
      return '<label class="ip-agent-option"><input type="checkbox" name="ip-agent" value="' + esc(agent.id) + '"' +
        (checked ? " checked" : "") + '><span>' + esc(agent.label || agent.id) + "</span>" +
        "<small>" + esc(agent.dir || "") + "</small></label>";
    }).join("");
    if ($("ip-modal-title")) $("ip-modal-title").textContent = "Link skills · " + (item.title || item.name);
    if ($("ip-modal-subtitle")) $("ip-modal-subtitle").textContent = (item.skill_count || (item.skills || []).length) + " skills will be linked into the selected agent skill folders.";
    if ($("ip-modal-status")) $("ip-modal-status").textContent = "";
    modal.classList.remove("hidden");
    icons();
  }

  function closeLinkModal() {
    var modal = $("ip-modal");
    if (modal) modal.classList.add("hidden");
    state.modalItem = null;
  }

  function linkSkills() {
    var item = state.modalItem;
    if (!item) return;
    var selected = [];
    Array.prototype.forEach.call(document.querySelectorAll('input[name="ip-agent"]:checked'), function (input) {
      selected.push(input.value);
    });
    if (!selected.length) {
      if ($("ip-modal-status")) $("ip-modal-status").textContent = "Select at least one agent.";
      return;
    }
    var button = $("ip-modal-link");
    if (button) button.disabled = true;
    if ($("ip-modal-status")) $("ip-modal-status").textContent = "Linking…";
    api("/api/plugins/install-skills", {
      method: "POST",
      body: { name: item.name || item.id, agents: selected }
    }).then(function (result) {
      if (!result.ok) {
        if ($("ip-modal-status")) $("ip-modal-status").textContent = (result.data && result.data.error) || "Could not link skills.";
        return;
      }
      var failed = ((result.data && result.data.results) || []).filter(function (r) { return !r.ok; });
      closeLinkModal();
      toast(failed.length ? "Linked with " + failed.length + " conflict(s)." : "Skills linked into " + selected.length + " agent(s).", failed.length > 0);
      return load();
    }).catch(function (error) {
      if ($("ip-modal-status")) $("ip-modal-status").textContent = "Link failed: " + error.message;
    }).then(function () {
      if (button) button.disabled = false;
    });
  }

  /* ------------------------------------------------------------------ */
  /* Install from GitHub                                                 */
  /* ------------------------------------------------------------------ */
  function toggleInstallPanel(show) {
    var panel = $("ip-install-panel");
    if (!panel) return;
    var next = show == null ? panel.classList.contains("hidden") : !!show;
    panel.classList.toggle("hidden", !next);
    if (next) {
      var input = $("ip-source");
      if (input) {
        input.focus();
        input.select();
      }
    }
  }

  function submitInstall(event) {
    if (event) event.preventDefault();
    var source = ($("ip-source") && $("ip-source").value || "").trim();
    var name = ($("ip-name") && $("ip-name").value || "").trim();
    if (!source) {
      toast("A Git URL or local folder is required.", true);
      return;
    }
    var body = { source: source };
    if (name) body.name = name;
    var button = $("ip-install-submit");
    if (button) button.disabled = true;
    toast("Cloning " + source + "…");
    api("/api/plugins/install", { method: "POST", body: body }).then(function (result) {
      if (!result.ok) {
        toast((result.data && result.data.error) || "Install failed", true);
        return;
      }
      var installed = result.data || {};
      toast("Installed " + (installed.title || installed.name || name || "plugin") + ".");
      if ($("ip-source")) $("ip-source").value = "";
      if ($("ip-name")) $("ip-name").value = "";
      toggleInstallPanel(false);
      return load();
    }).catch(function (error) {
      toast("Install failed: " + error.message, true);
    }).then(function () {
      if (button) button.disabled = false;
    });
  }

  /* ------------------------------------------------------------------ */
  /* Wiring                                                              */
  /* ------------------------------------------------------------------ */
  function init() {
    state.view = $("installed-plugins-view");
    if (!state.view || state.initialized) return;
    state.initialized = true;

    var grid = $("ip-grid");
    if (grid) grid.addEventListener("click", function (event) {
      var actionEl = event.target.closest("[data-ip-action]");
      if (!actionEl) return;
      event.preventDefault();
      runAction(actionEl.getAttribute("data-ip-action"), actionEl.getAttribute("data-ip-id"), actionEl);
    });

    var cloud = $("ip-tag-cloud");
    if (cloud) cloud.addEventListener("click", function (event) {
      var tag = event.target.closest("[data-ip-tag]");
      if (!tag) return;
      state.query = tag.getAttribute("data-ip-tag") || "";
      state.filter = "all";
      if ($("ip-search")) $("ip-search").value = state.query;
      Array.prototype.forEach.call(document.querySelectorAll("[data-ip-filter]"), function (btn) {
        btn.classList.toggle("ip-seg-btn-active", btn.getAttribute("data-ip-filter") === "all");
      });
      renderGrid();
    });

    var search = $("ip-search");
    if (search) search.addEventListener("input", function () {
      state.query = search.value || "";
      renderGrid();
    });

    var filter = $("ip-filter");
    if (filter) filter.addEventListener("click", function (event) {
      var button = event.target.closest("[data-ip-filter]");
      if (!button) return;
      state.filter = button.getAttribute("data-ip-filter") || "all";
      Array.prototype.forEach.call(filter.querySelectorAll("[data-ip-filter]"), function (btn) {
        btn.classList.toggle("ip-seg-btn-active", btn === button);
      });
      renderGrid();
    });

    var refresh = $("ip-refresh");
    if (refresh) refresh.addEventListener("click", load);

    var installToggle = $("ip-install-toggle");
    if (installToggle) installToggle.addEventListener("click", function () { toggleInstallPanel(); });

    var installForm = $("ip-install-form");
    if (installForm) installForm.addEventListener("submit", submitInstall);

    var cancel = $("ip-install-cancel");
    if (cancel) cancel.addEventListener("click", function () { toggleInstallPanel(false); });

    var modalClose = $("ip-modal-close");
    if (modalClose) modalClose.addEventListener("click", closeLinkModal);
    var modal = $("ip-modal");
    if (modal) modal.addEventListener("click", function (event) {
      if (event.target === modal) closeLinkModal();
    });
    var modalLink = $("ip-modal-link");
    if (modalLink) modalLink.addEventListener("click", linkSkills);
    document.addEventListener("keydown", function (event) {
      if (event.key === "Escape" && modal && !modal.classList.contains("hidden")) closeLinkModal();
    });

    load();
  }

  function refresh() {
    if (!state.initialized) { init(); return; }
    if (!state.view) return;
    load();
    icons();
  }

  function open() {
    if (window.switchMainView) window.switchMainView("installed-plugins");
    else refresh();
  }

  window.InstalledPlugins = { init: init, refresh: refresh, open: open };

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
