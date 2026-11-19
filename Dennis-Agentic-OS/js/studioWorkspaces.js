/* =====================================================================
   STUDIO WORKSPACES - shared shell for Ads Studio and YouTube Studio.

   Responsibilities:
     - render the workspace selector state;
     - show/hide the two workspace shells without reloading the page;
     - keep each workspace's last section in memory and in the URL hash;
     - support deep links such as #studio/youtube/overview.

   It never touches Ads generation state or YouTube project data. Those stay
   owned by js/studio.js and js/studioYoutube.js.
   ===================================================================== */
(function () {
  "use strict";

  var WSS = window.StudioWorkspaceState;
  if (!WSS) return;

  var STORAGE_KEY = "dennis.studio.workspace";
  var S = {
    workspace: null,
    adsSection: "fashion",
    youtubeSection: "overview",
    initialized: false,
    applying: false
  };

  function $(id) { return document.getElementById(id); }

  function view() { return $("studio-view"); }

  function readSaved() {
    try {
      var saved = window.localStorage.getItem(STORAGE_KEY);
      if (saved) return WSS.normalizeWorkspace(saved, "ads");
    } catch (err) { /* private mode */ }
    return "ads";
  }

  function saveWorkspace(workspace) {
    try { window.localStorage.setItem(STORAGE_KEY, workspace); } catch (err) { /* ignore */ }
    try {
      fetch("/api/studio/workspaces/active", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ workspace: workspace })
      }).catch(function () { /* last active is a convenience, not a gate */ });
    } catch (err) { /* ignore */ }
  }

  function setSwitchState(workspace) {
    Array.prototype.forEach.call(document.querySelectorAll("[data-studio-workspace]"), function (button) {
      var active = button.getAttribute("data-studio-workspace") === workspace;
      button.classList.toggle("is-active", active);
      button.setAttribute("aria-selected", active ? "true" : "false");
    });
  }

  function showAds(show) {
    var sidebar = $("studio-ads-sidebar");
    var main = $("studio-ads-main");
    if (sidebar) sidebar.classList.toggle("is-hidden", !show);
    if (main) main.classList.toggle("is-hidden", !show);
  }

  function showYoutube(show) {
    var sidebar = $("studio-youtube-sidebar");
    var main = $("studio-youtube-main");
    if (sidebar) sidebar.classList.toggle("is-hidden", !show);
    if (main) main.classList.toggle("is-hidden", !show);
  }

  function apply(workspace, section, options) {
    var opts = options || {};
    var ws = WSS.normalizeWorkspace(workspace, "ads");
    var sec = WSS.normalizeSection(ws, section || (ws === "ads" ? S.adsSection : S.youtubeSection));
    var changed = S.workspace !== ws;
    S.workspace = ws;
    if (ws === "ads") S.adsSection = sec;
    else S.youtubeSection = sec;

    var root = view();
    if (root) root.setAttribute("data-workspace", ws);
    setSwitchState(ws);
    showAds(ws === "ads");
    showYoutube(ws === "youtube");

    if (ws === "ads") {
      if (window.Studio && window.Studio.setTab) window.Studio.setTab(sec);
      if (window.Studio && window.Studio.refresh && opts.refreshAds) window.Studio.refresh();
    } else if (window.YouTubeStudio && window.YouTubeStudio.show) {
      window.YouTubeStudio.show(sec);
    }

    if (changed) saveWorkspace(ws);
    if (!opts.skipHash) setHash(ws, sec, opts.replace);
  }

  function setHash(workspace, section, replace) {
    var next = WSS.buildHash(workspace, section);
    if (window.location.hash === next) return;
    try {
      if (replace) window.history.replaceState(null, "", next);
      else window.history.pushState(null, "", next);
    } catch (err) {
      window.location.hash = next;
    }
  }

  function go(workspace, section) {
    var ws = WSS.normalizeWorkspace(workspace, "ads");
    var sec = WSS.normalizeSection(ws, section);
    if (S.workspace === ws && (ws === "ads" ? S.adsSection : S.youtubeSection) === sec) {
      setHash(ws, sec, false);
      return;
    }
    apply(ws, sec, { replace: false });
  }

  function applyHash(options) {
    var route = WSS.parseHash(window.location.hash);
    if (!route) return false;
    if (document.getElementById("studio-view") &&
        document.getElementById("studio-view").classList.contains("hidden") &&
        window.switchMainView) {
      window.switchMainView("studio");
    }
    var workspace = route.explicitWorkspace ? route.workspace : (S.workspace || readSaved());
    apply(workspace, route.section || null, {
      replace: !options || options.replace !== false,
      skipHash: true
    });
    return true;
  }

  function bind() {
    Array.prototype.forEach.call(document.querySelectorAll("[data-studio-workspace]"), function (button) {
      button.addEventListener("click", function () {
        var workspace = button.getAttribute("data-studio-workspace");
        if (workspace === "youtube") go("youtube", S.youtubeSection || "overview");
        else go("ads", S.adsSection || "fashion");
      });
    });

    // Ads tab clicks keep the URL in sync. The Studio module still owns the
    // actual panel switch; this only records the section for deep links.
    document.addEventListener("click", function (event) {
      if (S.workspace !== "ads") return;
      var button = event.target.closest("[data-studio-tab],[data-studio-subtab]");
      if (!button) return;
      var section = button.getAttribute("data-studio-subtab") || button.getAttribute("data-studio-tab");
      if (section) {
        S.adsSection = WSS.normalizeSection("ads", section);
        setHash("ads", S.adsSection, false);
      }
    }, true);

    // The brand button in the shared header switches back to Ads.
    var brand = document.querySelector("#studio-view .collart-brand");
    if (brand) {
      brand.addEventListener("click", function () {
        setTimeout(function () { go("ads", "fashion"); }, 0);
      });
    }

    window.addEventListener("popstate", function () { applyHash({ replace: true }); });
    window.addEventListener("hashchange", function () { applyHash({ replace: true }); });
  }

  function init() {
    if (S.initialized) return;
    S.initialized = true;
    bind();

    var route = WSS.parseHash(window.location.hash);
    if (route) {
      applyHash({ replace: true });
      return;
    }

    var saved = readSaved();
    S.workspace = saved;
    // Prepare the shell without writing a hash until the user interacts.
    apply(saved, saved === "ads" ? S.adsSection : S.youtubeSection, { skipHash: true });
  }

  window.StudioWorkspaces = {
    init: init,
    go: go,
    workspace: function () { return S.workspace; },
    section: function () { return S.workspace === "ads" ? S.adsSection : S.youtubeSection; },
    applyHash: applyHash
  };

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
