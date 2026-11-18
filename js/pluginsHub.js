/* =====================================================================
   PLUGINS HUB — one sidebar view, four tabs, everything API-related.

   Tabs: API Mega List · Composio · FreeLLMAPI (Catalog + AI Connections)
         · Tools (Installed Plugins · Connected Apps · Connected Skills)

   The hub owns tab + sub-tab switching and dispatches refresh calls to
   the module that owns the visible surface. Legacy deep links
   (switchMainView('freellm' | 'connections' | 'installed-plugins'))
   are mapped onto the matching tab by js/app.js.
   ===================================================================== */
(function () {
  "use strict";

  var TABS = ["megalist", "composio", "freellm", "tools"];
  var FREELLM_SUBS = ["catalog", "connections"];
  var TOOLS_SUBS = ["plugins", "apps", "skills"];

  var state = {
    initialized: false,
    tab: "megalist",
    freellmSub: "catalog",
    toolsSub: "plugins"
  };

  function $(id) { return document.getElementById(id); }

  function icons() {
    if (window.lucide && window.lucide.createIcons) {
      try { window.lucide.createIcons(); } catch (error) { /* no-op */ }
    }
  }

  function toggleVisible(el, on) {
    if (el) el.classList.toggle("hidden", !on);
  }

  function setSubTabs(selector, attr, value) {
    Array.prototype.forEach.call(document.querySelectorAll(selector), function (button) {
      button.classList.toggle("plugins-tab-active", button.getAttribute(attr) === value);
    });
  }

  /* ------------------------------------------------------------------ */
  /* Refresh dispatch — the visible surface's module refreshes itself.   */
  /* ------------------------------------------------------------------ */
  function refresh() {
    if (state.tab === "megalist") {
      if (window.Plugins) window.Plugins.refresh();
    } else if (state.tab === "composio") {
      if (window.Composio) window.Composio.refresh();
    } else if (state.tab === "freellm") {
      if (state.freellmSub === "connections") {
        if (window.AIConnections) window.AIConnections.refresh();
      } else if (window.FreeLLMAPI) {
        window.FreeLLMAPI.refresh();
      }
    } else if (state.tab === "tools") {
      if (state.toolsSub === "apps") {
        if (window.ConnectedApps) window.ConnectedApps.refresh();
      } else if (state.toolsSub === "skills") {
        if (window.ConnectedSkills) window.ConnectedSkills.refresh();
      } else if (window.InstalledPlugins) {
        window.InstalledPlugins.refresh();
      }
    }
    icons();
  }

  /* ------------------------------------------------------------------ */
  /* Pipeline strip (FreeLLMAPI tab): Connect → Test → Discover → Assign */
  /* ------------------------------------------------------------------ */
  function runPipelineStep(step) {
    if (step === "connect") {
      show("freellm", "connections");
      if ($("cn-add")) $("cn-add").click();
      return;
    }
    if (step === "test") {
      show("freellm", "connections");
      // Health checks run against real endpoints on every load/refresh.
      if (window.AIConnections) window.AIConnections.refresh();
      return;
    }
    if (step === "discover") {
      show("freellm", "connections");
      if (window.AIConnections) window.AIConnections.refresh();
      return;
    }
    if (step === "assign") {
      // Assigning a model to an agent happens in the Catalog connect pane.
      show("freellm", "catalog");
      return;
    }
  }

  /* ------------------------------------------------------------------ */
  /* Rendering the current tab/sub-tab state onto the DOM.               */
  /* ------------------------------------------------------------------ */
  function apply() {
    toggleVisible($("mg-workspace"), state.tab === "megalist");
    toggleVisible($("composio-workspace"), state.tab === "composio");
    toggleVisible($("freellm-workspace"), state.tab === "freellm");
    toggleVisible($("tools-workspace"), state.tab === "tools");

    setSubTabs("[data-plugins-tab]", "data-plugins-tab", state.tab);

    // Header actions + stats belong to the Mega List tab only.
    toggleVisible($("plugins-actions-megalist"), state.tab === "megalist");
    toggleVisible($("mg-stats"), state.tab === "megalist");

    toggleVisible($("freellm-panel-catalog"), state.tab === "freellm" && state.freellmSub === "catalog");
    toggleVisible($("freellm-panel-connections"), state.tab === "freellm" && state.freellmSub === "connections");
    setSubTabs("[data-freellm-subtab]", "data-freellm-subtab", state.freellmSub);

    toggleVisible($("tools-panel-plugins"), state.tab === "tools" && state.toolsSub === "plugins");
    toggleVisible($("tools-panel-apps"), state.tab === "tools" && state.toolsSub === "apps");
    toggleVisible($("tools-panel-skills"), state.tab === "tools" && state.toolsSub === "skills");
    setSubTabs("[data-tools-subtab]", "data-tools-subtab", state.toolsSub);

    icons();
  }

  /* ------------------------------------------------------------------ */
  /* Public: jump straight to a tab (and sub-tab).                       */
  /* ------------------------------------------------------------------ */
  function show(tab, subtab) {
    if (!state.initialized) init();
    if (TABS.indexOf(tab) >= 0) state.tab = tab;
    if (state.tab === "freellm" && FREELLM_SUBS.indexOf(subtab) >= 0) state.freellmSub = subtab;
    if (state.tab === "tools" && TOOLS_SUBS.indexOf(subtab) >= 0) state.toolsSub = subtab;
    // Keep the Composio module's internal tab state in sync so its own
    // refresh/showView logic keeps working inside the hub.
    if (state.tab === "composio" && window.Composio && window.Composio.showPluginTab) {
      window.Composio.showPluginTab("composio", true);
    }
    apply();
    refresh();
  }

  function init() {
    if (!$("plugins-view") || state.initialized) return;
    state.initialized = true;

    Array.prototype.forEach.call(document.querySelectorAll("[data-plugins-tab]"), function (button) {
      button.addEventListener("click", function () { show(button.getAttribute("data-plugins-tab")); });
    });
    Array.prototype.forEach.call(document.querySelectorAll("[data-freellm-subtab]"), function (button) {
      button.addEventListener("click", function () { show("freellm", button.getAttribute("data-freellm-subtab")); });
    });
    Array.prototype.forEach.call(document.querySelectorAll("[data-tools-subtab]"), function (button) {
      button.addEventListener("click", function () { show("tools", button.getAttribute("data-tools-subtab")); });
    });
    Array.prototype.forEach.call(document.querySelectorAll("[data-fl-pipe]"), function (button) {
      button.addEventListener("click", function () { runPipelineStep(button.getAttribute("data-fl-pipe")); });
    });

    // Deep links from other surfaces: e.g. Connected Apps → Composio tab.
    ["ca-open-composio"].forEach(function (id) {
      if ($(id)) $(id).addEventListener("click", function () { show("composio"); });
    });
    if ($("cs-open-skillbuilder")) {
      $("cs-open-skillbuilder").addEventListener("click", function () {
        if (window.switchMainView) window.switchMainView("skill-builder");
      });
    }
    if ($("ca-refresh")) $("ca-refresh").addEventListener("click", refresh);
    if ($("cs-refresh")) $("cs-refresh").addEventListener("click", refresh);

    // Count badges in tab labels (connections, plugins, apps, skills).
    document.addEventListener("dennis:hub-count", function (event) {
      var detail = event.detail || {};
      var targets = {
        connections: "cn-nav-count",
        plugins: "tools-nav-plugin-count",
        apps: "tools-nav-app-count",
        skills: "tools-nav-skill-count"
      };
      var el = $(targets[detail.key]);
      if (el && typeof detail.value === "number") el.textContent = String(detail.value);
    });

    apply();
  }

  window.PluginsHub = { init: init, show: show, refresh: refresh };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
