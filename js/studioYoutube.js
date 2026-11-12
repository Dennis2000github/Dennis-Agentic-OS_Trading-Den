/* =====================================================================
   YOUTUBE STUDIO - workspace shell and project foundation.

   Stage 2 only. Real data:
     - workspace descriptor and counts come from /api/studio/workspaces;
     - projects come from the isolated YouTube project store;
     - settings come from the workspace settings store.

   Sections planned for later stages render an explicit placeholder. Nothing
   here fabricates research, analytics, assets, renders or publishing state.
   ===================================================================== */
(function () {
  "use strict";

  var WS = window.StudioWorkspaceState;
  if (!WS) return;

  var S = {
    initialized: false,
    workspace: "youtube",
    section: "overview",
    descriptor: null,
    counts: null,
    settings: null,
    projects: [],
    recentProjects: [],
    projectsLoaded: false,
    loading: false,
    busy: false,
    query: "",
    status: "",
    includeArchived: false,
    lastError: null
  };

  function $(id) { return document.getElementById(id); }

  function esc(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function setText(id, text) {
    var el = $(id);
    if (el) el.textContent = text == null ? "" : String(text);
  }

  function toggle(el, visible, hiddenClass) {
    if (!el) return;
    el.classList.toggle(hiddenClass || "is-hidden", !visible);
  }

  function icons() {
    if (window.lucide && window.lucide.createIcons) {
      try { window.lucide.createIcons(); } catch (err) { /* icons are optional */ }
    }
  }

  function toast(message) {
    if (window.Studio && window.Studio.toast) return window.Studio.toast(message);
    return null;
  }

  function api(path, options) {
    var opts = options || {};
    var init = {
      method: opts.method || "GET",
      cache: "no-store",
      headers: new Headers(opts.headers || {})
    };
    if (opts.body !== undefined) {
      init.headers.set("Content-Type", "application/json");
      init.body = JSON.stringify(opts.body);
    }
    return fetch(path, init).then(function (response) {
      return response.json().catch(function () { return {}; }).then(function (data) {
        if (!response.ok) {
          var message = (data && (data.error || data.message)) || ("Request failed (" + response.status + ")");
          var error = new Error(message);
          error.status = response.status;
          error.code = data && data.code;
          throw error;
        }
        return data || {};
      });
    });
  }

  function dateLabel(value) {
    if (!value) return "—";
    var date = new Date(value);
    if (isNaN(date.getTime())) return "—";
    try {
      return date.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
    } catch (err) {
      return date.toISOString().slice(0, 10);
    }
  }

  function statusBadge(status) {
    var tone = {
      draft: "muted",
      in_progress: "info",
      review: "warn",
      approved: "ok",
      scheduled: "info",
      published: "ok",
      archived: "muted"
    }[status] || "muted";
    return '<span class="yt-badge yt-badge-' + tone + '">' + esc(WS.projectStatusLabel(status)) + "</span>";
  }

  function stageBadge(stage) {
    return '<span class="yt-badge yt-badge-stage">' + esc(WS.stageLabel(stage)) + "</span>";
  }

  function emptyState(title, note, action) {
    return '<div class="yt-empty">' +
      '<div class="yt-empty-icon"><i data-lucide="' + (action && action.icon ? action.icon : "inbox") + '"></i></div>' +
      "<h3>" + esc(title) + "</h3>" +
      "<p>" + esc(note) + "</p>" +
      (action ? '<button type="button" class="collart-btn" data-yt-empty-action="' + esc(action.id) + '">' + esc(action.label) + "</button>" : "") +
      "</div>";
  }

  function skeletonRows(count) {
    var rows = "";
    for (var i = 0; i < count; i += 1) rows += '<div class="yt-skeleton"></div>';
    return rows;
  }

  /* ------------------------------------------------------------------ */
  /* Data                                                                */
  /* ------------------------------------------------------------------ */
  function loadDescriptor() {
    return api("/api/studio/workspaces").then(function (payload) {
      var list = payload.workspaces || [];
      var found = null;
      for (var i = 0; i < list.length; i += 1) {
        if (list[i] && list[i].id === "youtube") { found = list[i]; break; }
      }
      S.descriptor = found;
      S.counts = (found && found.counts) || null;
      return found;
    });
  }

  function loadSettings() {
    return api("/api/studio/workspaces/youtube/settings").then(function (payload) {
      S.settings = payload.settings || null;
      return S.settings;
    });
  }

  function loadProjects() {
    var params = [];
    if (S.status) params.push("status=" + encodeURIComponent(S.status));
    if (S.includeArchived) params.push("include_archived=1");
    if (S.query) params.push("q=" + encodeURIComponent(S.query));
    var suffix = params.length ? "?" + params.join("&") : "";
    return api("/api/studio/workspaces/youtube/projects" + suffix).then(function (payload) {
      S.projects = payload.projects || [];
      S.counts = payload.counts || S.counts;
      S.projectsLoaded = true;
      return S.projects;
    });
  }

  function loadRecentProjects() {
    return api("/api/studio/workspaces/youtube/projects").then(function (payload) {
      S.recentProjects = payload.projects || [];
      S.counts = payload.counts || S.counts;
      return S.recentProjects;
    });
  }

  function loadOverview() {
    if (S.loading) return;
    S.loading = true;
    clearError();
    setText("yt-overview-loading", "Loading workspace…");
    Promise.all([loadDescriptor(), loadSettings(), loadRecentProjects()])
      .then(function () {
        renderOverview();
        renderProjects();
      })
      .catch(function (error) {
        showError(error.message);
      })
      .then(function () {
        S.loading = false;
        setText("yt-overview-loading", "");
      });
  }

  function loadProjectsPage() {
    if (S.loading) return;
    S.loading = true;
    clearError();
    loadProjects().then(function () {
      renderProjects();
    }).catch(function (error) {
      showError(error.message);
    }).then(function () {
      S.loading = false;
    });
  }

  /* ------------------------------------------------------------------ */
  /* Overview                                                            */
  /* ------------------------------------------------------------------ */
  function countValue(key) {
    if (S.counts && S.counts[key] != null) return S.counts[key];
    return 0;
  }

  function statCard(id, label, icon, note) {
    return '<article class="yt-stat" id="' + id + '-card">' +
      '<span class="yt-stat-icon"><i data-lucide="' + icon + '"></i></span>' +
      '<div><span class="yt-stat-value" id="' + id + '">0</span>' +
      '<span class="yt-stat-label">' + esc(label) + "</span>" +
      (note ? '<span class="yt-stat-note">' + esc(note) + "</span>" : "") +
      "</div></article>";
  }

  function renderOverview() {
    var settings = S.settings || {};
    var channel = settings.active_channel_id ? "Channel configured" : "No channel configured";
    setText("yt-active-channel", channel);
    setText("yt-active-channel-note", settings.active_channel_id
      ? "Channel Manager will show the full profile in Stage 3."
      : "Create a channel profile in Channel Manager (Stage 3).");

    setText("yt-stat-total", countValue("total"));
    setText("yt-stat-scripts", countValue("scripts_in_progress"));
    setText("yt-stat-production", countValue("videos_in_production"));
    setText("yt-stat-approval", countValue("awaiting_approval"));
    setText("yt-stat-published", countValue("published"));
    setText("yt-stat-scheduled", countValue("scheduled"));

    var byStage = (S.counts && S.counts.by_stage) || {};
    setText("yt-queue-production", countValue("videos_in_production"));
    setText("yt-queue-scheduled", countValue("scheduled"));
    setText("yt-queue-research", byStage.research || 0);

    renderRecentProjects();
    renderPipeline();
    renderResearchQueue();
    renderPublishingSchedule();
    icons();
  }

  function renderRecentProjects() {
    var host = $("yt-recent-projects");
    var empty = $("yt-recent-empty");
    if (!host) return;
    if (!S.recentProjects.length) {
      host.innerHTML = "";
      toggle(empty, true);
      return;
    }
    toggle(empty, false);
    host.innerHTML = S.recentProjects.slice(0, 5).map(projectRow).join("");
    icons();
  }

  function projectRow(project) {
    return '<button type="button" class="yt-project-row" data-yt-project="' + esc(project.id) + '">' +
      '<span class="yt-project-mark"><i data-lucide="video"></i></span>' +
      '<span class="yt-project-copy">' +
        '<b>' + esc(project.title || "Untitled project") + "</b>" +
        '<em>' + esc(project.content_pillar || "No content pillar") + " · updated " + esc(dateLabel(project.updated_iso || project.created_iso)) + "</em>" +
      "</span>" +
      '<span class="yt-project-badges">' + statusBadge(project.status) + stageBadge(project.stage) + "</span>" +
      '<span class="yt-project-chevron"><i data-lucide="chevron-right"></i></span>' +
      "</button>";
  }

  function renderPipeline() {
    var host = $("yt-pipeline");
    if (!host) return;
    var stages = ["research", "script", "storyboard", "assets", "voice", "animation", "assembly", "review", "publishing", "analytics"];
    var counts = (S.counts && S.counts.by_stage) || {};
    var any = stages.some(function (stage) { return (counts[stage] || 0) > 0; });
    if (!any) {
      host.innerHTML = emptyState(
        "No videos in production",
        "Create a project and the pipeline will show its stage here.",
        { id: "create", label: "Create New Video", icon: "plus" }
      );
      icons();
      return;
    }
    host.innerHTML = '<div class="yt-pipeline-list">' + stages.map(function (stage) {
      var value = counts[stage] || 0;
      return '<div class="yt-pipeline-row">' +
        '<span class="yt-pipeline-name">' + esc(WS.stageLabel(stage)) + "</span>" +
        '<span class="yt-pipeline-bar"><i style="width:' + Math.min(100, value * 20) + '%"></i></span>' +
        '<span class="yt-pipeline-count">' + value + "</span></div>";
    }).join("") + "</div>";
    icons();
  }

  function renderResearchQueue() {
    var host = $("yt-research-queue");
    if (!host) return;
    var count = (S.counts && S.counts.by_stage && S.counts.by_stage.research) || 0;
    host.innerHTML = count
      ? '<p class="yt-panel-note">' + count + " project" + (count === 1 ? "" : "s") + " waiting in research.</p>"
      : '<p class="yt-panel-note">No research queued. Niche Research and Topic Studio arrive in Stage 3.</p>';
  }

  function renderPublishingSchedule() {
    var host = $("yt-upcoming-schedule");
    if (!host) return;
    var scheduled = S.recentProjects.filter(function (project) { return project.status === "scheduled"; });
    if (!scheduled.length) {
      host.innerHTML = '<p class="yt-panel-note">No publishing scheduled. The Publishing Calendar arrives in Stage 5.</p>';
      return;
    }
    host.innerHTML = scheduled.slice(0, 5).map(function (project) {
      return '<div class="yt-schedule-row"><span>' + esc(project.title) + "</span><b>Scheduled</b></div>";
    }).join("");
  }

  /* ------------------------------------------------------------------ */
  /* Projects                                                            */
  /* ------------------------------------------------------------------ */
  function renderProjects() {
    var host = $("yt-projects-list");
    if (!host) return;
    setText("yt-projects-count", (S.projects.length || 0) + " project" + (S.projects.length === 1 ? "" : "s"));
    if (!S.projects.length) {
      host.innerHTML = emptyState(
        S.query || S.status ? "No projects match" : "No YouTube projects yet",
        S.query || S.status
          ? "Clear the search or status filter to see all projects."
          : "Create your first video project. Research, scripts and production live inside it.",
        { id: "create", label: "Create New Video", icon: "plus" }
      );
      icons();
      return;
    }
    host.innerHTML = S.projects.map(projectCard).join("");
    icons();
  }

  function projectCard(project) {
    return '<article class="yt-project-card" data-yt-project="' + esc(project.id) + '">' +
      '<div class="yt-project-card-head">' +
        '<div><h3>' + esc(project.title || "Untitled project") + "</h3>" +
        '<p>' + esc(project.description || "No description yet.") + "</p></div>" +
        '<div class="yt-project-badges">' + statusBadge(project.status) + stageBadge(project.stage) + "</div>" +
      "</div>" +
      '<div class="yt-project-card-foot">' +
        '<span>' + esc(project.content_pillar || "No content pillar") + "</span>" +
        '<span>Created ' + esc(dateLabel(project.created_iso)) + "</span>" +
        '<span class="yt-project-actions">' +
          '<button type="button" class="yt-link" data-yt-open="' + esc(project.id) + '">Open</button>' +
        "</span>" +
      "</div></article>";
  }

  /* ------------------------------------------------------------------ */
  /* Placeholders                                                        */
  /* ------------------------------------------------------------------ */
  function sectionMeta(id) {
    var sections = (S.descriptor && S.descriptor.sections) || [];
    for (var i = 0; i < sections.length; i += 1) {
      if (sections[i] && sections[i].id === id) return sections[i];
    }
    return null;
  }

  function renderPlaceholder(section) {
    var meta = sectionMeta(section);
    var label = (meta && meta.label) || WS.sectionLabel(section);
    var stage = meta && meta.stage ? "Stage " + meta.stage : "a later stage";
    setText("yt-placeholder-title", label);
    setText("yt-placeholder-note",
      label + " is not available in this build yet. It is scheduled for " + stage +
      ", and no placeholder data is shown in the meantime.");
    setText("yt-placeholder-stage", meta && meta.stage ? "Stage " + meta.stage : "Planned");
    icons();
  }

  /* ------------------------------------------------------------------ */
  /* Modal                                                               */
  /* ------------------------------------------------------------------ */
  function openModal(title, bodyHtml, footHtml) {
    var modal = $("yt-modal");
    if (!modal) return;
    setText("yt-modal-title", title);
    var body = $("yt-modal-body");
    var foot = $("yt-modal-foot");
    if (body) body.innerHTML = bodyHtml || "";
    if (foot) foot.innerHTML = footHtml || "";
    modal.classList.remove("is-hidden");
    icons();
  }

  function closeModal() {
    var modal = $("yt-modal");
    if (modal) modal.classList.add("is-hidden");
    var body = $("yt-modal-body");
    var foot = $("yt-modal-foot");
    if (body) body.innerHTML = "";
    if (foot) foot.innerHTML = "";
  }

  function modalError(message) {
    var el = $("yt-modal-error");
    if (el) {
      el.textContent = message || "";
      el.classList.toggle("is-hidden", !message);
    }
  }

  function setBusy(button, on, label) {
    if (!button) return;
    if (on) {
      button.dataset.label = button.textContent;
      button.disabled = true;
      if (label) button.textContent = label;
    } else {
      button.disabled = false;
      if (button.dataset.label) button.textContent = button.dataset.label;
    }
  }

  function openCreateProject() {
    openModal(
      "Create New Video",
      '<form id="yt-project-form" class="yt-form" autocomplete="off">' +
        '<label class="yt-field"><span>Title <em>required</em></span>' +
          '<input id="yt-form-title" type="text" maxlength="160" placeholder="e.g. 5 tax deductions small business owners miss" required></label>' +
        '<label class="yt-field"><span>Description</span>' +
          '<textarea id="yt-form-description" rows="3" maxlength="4000" placeholder="What is this video about?"></textarea></label>' +
        '<div class="yt-form-grid">' +
          '<label class="yt-field"><span>Content pillar</span>' +
            '<input id="yt-form-pillar" type="text" maxlength="80" placeholder="Tax Education"></label>' +
          '<label class="yt-field"><span>Status</span>' +
            '<select id="yt-form-status">' +
              '<option value="draft">Draft</option>' +
              '<option value="in_progress">In Progress</option>' +
              '<option value="review">Awaiting Approval</option>' +
            "</select></label>" +
        "</div>" +
        '<p class="yt-hint">The project starts in the Research stage. Later stages are added in Stages 3–6.</p>' +
        '<p id="yt-modal-error" class="yt-form-error is-hidden"></p>' +
      "</form>",
      '<button type="button" class="collart-btn-soft" data-yt-close>Cancel</button>' +
      '<button type="button" class="collart-btn" id="yt-form-create">Create Project</button>'
    );
    var form = $("yt-project-form");
    if (form) {
      form.addEventListener("submit", function (event) {
        event.preventDefault();
        createProject();
      });
    }
    var create = $("yt-form-create");
    if (create) create.addEventListener("click", createProject);
    var title = $("yt-form-title");
    if (title) title.focus();
  }

  function createProject() {
    var title = $("yt-form-title");
    var button = $("yt-form-create");
    if (!title || !title.value.trim()) {
      modalError("A title is required.");
      if (title) title.focus();
      return;
    }
    modalError("");
    setBusy(button, true, "Creating…");
    api("/api/studio/workspaces/youtube/projects", {
      method: "POST",
      body: {
        title: title.value.trim(),
        description: $("yt-form-description") ? $("yt-form-description").value : "",
        content_pillar: $("yt-form-pillar") ? $("yt-form-pillar").value : "",
        status: $("yt-form-status") ? $("yt-form-status").value : "draft"
      }
    }).then(function (payload) {
      closeModal();
      toast("Project created.");
      S.projectsLoaded = false;
      loadOverview();
      if (S.section === "projects") loadProjectsPage();
      return payload;
    }).catch(function (error) {
      modalError(error.message);
    }).then(function () {
      setBusy(button, false);
    });
  }

  function openProject(projectId) {
    var project = null;
    for (var i = 0; i < S.projects.length; i += 1) {
      if (S.projects[i].id === projectId) { project = S.projects[i]; break; }
    }
    if (!project) return;
    var archived = project.status === "archived";
    openModal(
      project.title || "Project",
      '<div class="yt-project-detail">' +
        '<div class="yt-project-badges">' + statusBadge(project.status) + stageBadge(project.stage) + "</div>" +
        '<p class="yt-project-description">' + esc(project.description || "No description yet.") + "</p>" +
        '<dl class="yt-meta">' +
          "<div><dt>Content pillar</dt><dd>" + esc(project.content_pillar || "—") + "</dd></div>" +
          "<div><dt>Created</dt><dd>" + esc(dateLabel(project.created_iso)) + "</dd></div>" +
          "<div><dt>Updated</dt><dd>" + esc(dateLabel(project.updated_iso)) + "</dd></div>" +
          "<div><dt>Project ID</dt><dd class=\"yt-mono\">" + esc(project.id) + "</dd></div>" +
        "</dl>" +
        '<p class="yt-hint">Research, script, storyboard and production screens arrive in Stages 3–4. ' +
        "This project is stored and isolated now.</p>" +
        '<p id="yt-modal-error" class="yt-form-error is-hidden"></p>' +
      "</div>",
      '<button type="button" class="collart-btn-soft" data-yt-close>Close</button>' +
      (archived
        ? '<button type="button" class="collart-btn" data-yt-restore="' + esc(project.id) + '">Restore</button>'
        : '<button type="button" class="collart-btn-soft" data-yt-archive="' + esc(project.id) + '">Archive</button>') +
      '<button type="button" class="yt-danger-btn" data-yt-delete="' + esc(project.id) + '">Delete</button>'
    );
  }

  function updateProject(projectId, patch, successMessage) {
    return api("/api/studio/workspaces/youtube/projects/" + encodeURIComponent(projectId), {
      method: "PATCH",
      body: patch
    }).then(function (payload) {
      closeModal();
      toast(successMessage || "Project updated.");
      S.projectsLoaded = false;
      loadOverview();
      if (S.section === "projects") loadProjectsPage();
      return payload;
    }).catch(function (error) {
      modalError(error.message);
    });
  }

  function confirmDelete(projectId) {
    var project = null;
    for (var i = 0; i < S.projects.length; i += 1) {
      if (S.projects[i].id === projectId) { project = S.projects[i]; break; }
    }
    openModal(
      "Delete project?",
      '<p class="yt-confirm-copy">Delete “' + esc(project ? project.title : "this project") +
        '”? It will be hidden from lists and kept recoverable. Media files are never deleted silently.</p>' +
        '<p id="yt-modal-error" class="yt-form-error is-hidden"></p>',
      '<button type="button" class="collart-btn-soft" data-yt-close>Cancel</button>' +
      '<button type="button" class="yt-danger-btn" id="yt-confirm-delete">Delete Project</button>'
    );
    var button = $("yt-confirm-delete");
    if (button) {
      button.addEventListener("click", function () {
        setBusy(button, true, "Deleting…");
        api("/api/studio/workspaces/youtube/projects/" + encodeURIComponent(projectId), {
          method: "DELETE"
        }).then(function () {
          closeModal();
          toast("Project deleted.");
          S.projectsLoaded = false;
          loadOverview();
          if (S.section === "projects") loadProjectsPage();
        }).catch(function (error) {
          modalError(error.message);
          setBusy(button, false);
        });
      });
    }
  }

  /* ------------------------------------------------------------------ */
  /* Rendering / navigation                                              */
  /* ------------------------------------------------------------------ */
  function clearError() {
    S.lastError = null;
    var banner = $("yt-error-banner");
    if (banner) banner.classList.add("is-hidden");
  }

  function showError(message) {
    S.lastError = message;
    var banner = $("yt-error-banner");
    var text = $("yt-error-text");
    if (text) text.textContent = message || "Something went wrong.";
    if (banner) banner.classList.remove("is-hidden");
  }

  function setSection(section) {
    S.section = WS.normalizeSection("youtube", section);
    var overviewOrProjects = S.section === "overview" || S.section === "projects";
    var panels = document.querySelectorAll("[data-yt-panel]");
    Array.prototype.forEach.call(panels, function (panel) {
      var name = panel.getAttribute("data-yt-panel");
      var visible = name === S.section || (!overviewOrProjects && name === "placeholder");
      panel.classList.toggle("is-hidden", !visible);
    });
    Array.prototype.forEach.call(document.querySelectorAll("[data-yt-section]"), function (button) {
      var active = button.getAttribute("data-yt-section") === S.section;
      button.classList.toggle("is-active", active);
      button.setAttribute("aria-current", active ? "page" : "false");
    });
    setText("yt-section-title", WS.sectionLabel(S.section));
    setText("yt-section-crumb", WS.sectionLabel(S.section));
    if (S.section === "overview") loadOverview();
    else if (S.section === "projects") loadProjectsPage();
    else renderPlaceholder(S.section);
  }

  function show(section) {
    setSection(section);
  }

  function refresh() {
    if (S.section === "overview") loadOverview();
    else if (S.section === "projects") loadProjectsPage();
  }

  /* ------------------------------------------------------------------ */
  /* Wiring                                                              */
  /* ------------------------------------------------------------------ */
  function bind() {
    Array.prototype.forEach.call(document.querySelectorAll("[data-yt-section]"), function (button) {
      button.addEventListener("click", function () {
        var section = button.getAttribute("data-yt-section");
        if (window.StudioWorkspaces && window.StudioWorkspaces.go) {
          window.StudioWorkspaces.go("youtube", section);
        } else {
          show(section);
        }
      });
    });

    var createButtons = ["yt-create-video", "yt-new-project", "yt-overview-create", "yt-projects-create"];
    createButtons.forEach(function (id) {
      var button = $(id);
      if (button) button.addEventListener("click", openCreateProject);
    });

    var refreshButton = $("yt-refresh");
    if (refreshButton) refreshButton.addEventListener("click", function () { refresh(); });

    var search = $("yt-projects-search");
    if (search) {
      search.addEventListener("input", function () {
        S.query = search.value.trim();
        if (S.section === "projects") loadProjectsPage();
      });
    }

    var status = $("yt-projects-status");
    if (status) {
      status.addEventListener("change", function () {
        S.status = status.value;
        if (S.section === "projects") loadProjectsPage();
      });
    }

    var archived = $("yt-projects-archived");
    if (archived) {
      archived.addEventListener("change", function () {
        S.includeArchived = !!archived.checked;
        if (S.section === "projects") loadProjectsPage();
      });
    }

    document.addEventListener("click", function (event) {
      var close = event.target.closest("[data-yt-close]");
      if (close) { closeModal(); return; }

      var open = event.target.closest("[data-yt-open]");
      if (open) { openProject(open.getAttribute("data-yt-open")); return; }

      var row = event.target.closest("[data-yt-project]");
      if (row && !event.target.closest("[data-yt-archive],[data-yt-restore],[data-yt-delete]")) {
        openProject(row.getAttribute("data-yt-project"));
        return;
      }

      var emptyAction = event.target.closest("[data-yt-empty-action]");
      if (emptyAction && emptyAction.getAttribute("data-yt-empty-action") === "create") {
        openCreateProject();
        return;
      }

      var archive = event.target.closest("[data-yt-archive]");
      if (archive) {
        updateProject(archive.getAttribute("data-yt-archive"), { status: "archived" }, "Project archived.");
        return;
      }

      var restore = event.target.closest("[data-yt-restore]");
      if (restore) {
        updateProject(restore.getAttribute("data-yt-restore"), { status: "draft" }, "Project restored.");
        return;
      }

      var remove = event.target.closest("[data-yt-delete]");
      if (remove) {
        confirmDelete(remove.getAttribute("data-yt-delete"));
        return;
      }

      var modal = $("yt-modal");
      if (modal && event.target === modal) closeModal();
    });

    document.addEventListener("keydown", function (event) {
      if (event.key === "Escape") closeModal();
    });
  }

  function init() {
    if (S.initialized) return;
    S.initialized = true;
    bind();
  }

  window.YouTubeStudio = {
    init: init,
    show: show,
    refresh: refresh,
    openCreateProject: openCreateProject,
    section: function () { return S.section; },
    projects: function () { return S.projects.slice(); }
  };

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
