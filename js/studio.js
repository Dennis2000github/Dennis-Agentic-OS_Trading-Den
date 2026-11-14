/* =====================================================================
   STUDIO - generation console for #studio-view.

   Real provider-backed jobs only:
     - providers and models come from /api/studio/*;
     - parameters are rendered from the pinned model schema;
     - active jobs show state + elapsed time (the API reports no progress);
     - finished outputs are previewed from the local media library.

   No fake progress, no simulated completion, no secrets in the page.
   ===================================================================== */
(function () {
  "use strict";

  var SS = window.StudioState;
  if (!SS) return;

  var MAX_ATTACH = 6;
  var MAX_UPLOAD_BYTES = 25 * 1024 * 1024;
  var POLL_MS = 2500;
  var TICK_MS = 1000;
  var JOB_LIMIT = 200;

  var PRESETS = {
    cinematic: "cinematic lighting, film grain, shallow depth of field, dramatic composition",
    realistic: "photorealistic, natural lighting, realistic textures, lifelike detail",
    anime: "anime style, vibrant colors, clean line art, detailed background",
    product: "clean product photography, studio lighting, sharp focus, neutral background",
    social: "bold social media composition, high contrast, eye-catching framing"
  };

  var S = {
    initialized: false,
    token: null,
    view: null,
    providers: [],
    catalog: null,
    mcpConfig: null,
    providerId: null,
    provider: null,
    agents: [],
    agentId: null,
    agent: null,
    avatars: [],
    avatarsLoaded: false,
    models: [],
    modes: [],
    modelId: null,
    detail: null,
    fields: [],
    values: {},
    inputMode: "",
    modelQuery: "",
    kindFilter: "",
    preset: "",
    jobs: [],
    jobId: null,
    selectedJob: null,
    selectedAt: 0,
    jobSyncedAt: 0,
    selectedMedia: null,
    selectedVariant: 0,
    attachments: [],
    videoKit: {
      ref: { url: "", name: "", file: null, data_base64: "" },
      avatar: { url: "", name: "", file: null, data_base64: "" },
      product: { url: "", name: "", file: null, data_base64: "" },
      analyzing: false,
      analysis: null,
      prompt: "",
      chat: "",
      templateId: "",
      templates: [],
      templatesLoaded: false,
      templatesLoading: false
    },
    library: [],
    libraryQuery: "",
    libraryKind: "",
    libraryStats: { count: 0, bytes: 0 },
    playlists: [],
    templates: [],
    searchQuery: "",
    tab: "higgsfield",
    higgsfieldTab: "activity",
    stageKey: "",
    stageOutputs: [],
    currentOutput: null,
    generateBusy: false,
    setupBusy: false,
    uploading: 0,
    pendingField: null,
    pendingPlaylistMedia: null,
    finishedSeen: {},
    pollTimer: null,
    tickTimer: null
  };

  function $(id) { return document.getElementById(id); }

  function esc(value) {
    return String(value == null ? "" : value).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function icons() {
    if (window.lucide && window.lucide.createIcons) {
      try { window.lucide.createIcons(); } catch (err) { /* no-op */ }
    }
  }

  function setText(id, text) {
    var el = $(id);
    if (el) el.textContent = text == null ? "" : String(text);
  }

  function show(el, visible) {
    if (el) el.classList.toggle("hidden", !visible);
  }

  function find(list, id) {
    return SS.findById(list || [], id);
  }

  function fieldByName(name) {
    for (var i = 0; i < S.fields.length; i += 1) {
      if (S.fields[i] && S.fields[i].name === name) return S.fields[i];
    }
    return null;
  }

  function api(path, options) {
    var opts = options ? Object.assign({}, options) : {};
    if (opts.body && typeof opts.body !== "string") {
      opts.body = JSON.stringify(opts.body);
      opts.headers = Object.assign({ "Content-Type": "application/json" }, opts.headers || {});
    }
    return fetch(path, opts).then(function (response) {
      return response.text().then(function (text) {
        var data = {};
        if (text) {
          try { data = JSON.parse(text); } catch (err) { data = { error: text }; }
        }
        if (!response.ok) {
          var problem = new Error(data.message || data.error || ("HTTP " + response.status));
          problem.code = data.code || "http_error";
          problem.status = response.status;
          throw problem;
        }
        return data;
      });
    });
  }

  var toastTimer = null;
  function toast(message) {
    var el = $("studio-toast");
    if (!el) {
      el = document.createElement("div");
      el.id = "studio-toast";
      el.className = "studio-toast";
      document.body.appendChild(el);
    }
    el.innerHTML = '<i data-lucide="sparkles" class="w-3.5 h-3.5 text-fuchsia-300"></i><span>' + esc(message) + "</span>";
    icons();
    el.classList.add("visible");
    if (toastTimer) window.clearTimeout(toastTimer);
    toastTimer = window.setTimeout(function () { el.classList.remove("visible"); }, 3200);
  }

  function busy(button, on, label) {
    if (!button) return;
    button.disabled = !!on;
    if (label) {
      var span = button.querySelector("span");
      if (span) span.textContent = label;
    }
  }

  /* ------------------------------------------------------------------ */
  /* Session token (only used for <video>/<img> URLs and downloads)      */
  /* ------------------------------------------------------------------ */
  function loadToken() {
    return api("/api/auth/session")
      .then(function (data) {
        S.token = (data && data.token) || null;
        return S.token;
      })
      .catch(function () { S.token = null; return null; });
  }

  /* ------------------------------------------------------------------ */
  /* Media URLs                                                          */
  /* ------------------------------------------------------------------ */
  function mediaLink(mediaId, remoteUrl) {
    if (mediaId) return SS.mediaUrl(mediaId, S.token);
    return remoteUrl || "";
  }

  /* ------------------------------------------------------------------ */
  /* Providers, models and schema-driven parameters                      */
  /* ------------------------------------------------------------------ */
  function loadProviders() {
    return api("/api/studio/providers").then(function (data) {
      S.providers = SS.normalizeList(data, "providers");
      S.catalog = data.catalog || null;
      S.mcpConfig = data.mcp || null;
      var wanted = S.providerId;
      var next = find(S.providers, wanted);
      if (!next) {
        next = S.providers.filter(function (p) { return p.configured; })[0] || S.providers[0] || null;
      }
      S.providerId = next ? next.id : null;
      S.provider = next;
      renderProviders();
      renderSetup();
      updateHeader();
      return loadModels();
    }).catch(function (err) {
      setText("studio-status-text", "Backend unreachable");
      setMessage("studio-provider-note", err.message);
      toast(err.message);
    });
  }

  function loadModels() {
    var provider = S.provider;
    if (!provider) {
      S.models = [];
      S.modelId = null;
      S.detail = null;
      S.fields = [];
      renderModels();
      renderParams();
      updateGenerateState();
      return Promise.resolve();
    }
    setModelNote("Loading models…");
    return api("/api/studio/models?provider=" + encodeURIComponent(provider.id))
      .then(function (data) {
        S.models = SS.normalizeList(data, "models");
        S.modes = Array.isArray(data.modes) ? data.modes : [];
        if (data.catalog) S.catalog = data.catalog;
        var candidates = SS.filterModelsByInput(S.models, S.inputMode, "", "");
        var keep = find(candidates, S.modelId) || find(S.models, S.modelId);
        if (!keep || (S.inputMode && SS.inputModeForModel(keep) !== S.inputMode)) {
          var preferred = candidates.filter(function (model) { return model.kind === "video"; })[0] || candidates[0];
          S.modelId = preferred ? preferred.id : (S.models.length ? S.models[0].id : null);
        }
        renderModels();
        renderInputModes();
        updateHeader();
        if (S.modelId) {
          if (S.detail && S.detail.id === S.modelId && S.detail.provider === provider.id) {
            updateGenerateState();
            renderModelNote();
            renderModelCard();
            renderUsage();
            return null;
          }
          return loadModelDetail(S.modelId);
        }
        S.detail = null;
        S.fields = [];
        S.values = {};
        renderParams();
        setModelNote(S.models.length ? "" : "No models available.");
        renderModelCard();
        renderUsage();
        updateGenerateState();
        return null;
      })
      .catch(function (err) {
        S.models = [];
        S.modelId = null;
        S.detail = null;
        S.fields = [];
        renderModels();
        renderParams();
        setModelNote(err.message);
        updateGenerateState();
      });
  }

  function loadModelDetail(modelId) {
    var provider = S.provider;
    if (!provider || !modelId) return Promise.resolve();
    return api("/api/studio/models?provider=" + encodeURIComponent(provider.id) +
        "&id=" + encodeURIComponent(modelId))
      .then(function (detail) {
        if (S.modelId !== modelId) return null;
        S.detail = detail;
        S.fields = detail.fields || [];
        S.values = SS.defaultValues(S.fields);
        var prompt = SS.promptField(S.fields);
        if (prompt && prompt.default != null) S.values.prompt = prompt.default;
        if (!S.inputMode) S.inputMode = SS.inputModeForModel(detail);
        renderInputModes();
        renderPrompt();
        renderParams();
        setModelNote("");
        updateGenerateState();
        renderModelNote();
        renderModelCard();
        renderUsage();
        renderStage();
        return null;
      })
      .catch(function (err) {
        S.detail = null;
        S.fields = [];
        S.values = {};
        renderParams();
        renderModelCard();
        setModelNote(err.message);
        updateGenerateState();
      });
  }

  function chooseProvider(providerId) {
    if (providerId === S.providerId) return Promise.resolve();
    S.providerId = providerId;
    S.provider = find(S.providers, providerId);
    S.models = [];
    S.modelId = null;
    S.detail = null;
    S.fields = [];
    S.values = {};
    S.inputMode = "";
    S.stageKey = "";
    renderProviders();
    renderSetup();
    updateHeader();
    renderParams();
    renderInputModes();
    renderModelCard();
    return loadModels();
  }

  function chooseModel(modelId) {
    if (!modelId || modelId === S.modelId) return Promise.resolve();
    S.modelId = modelId;
    var model = find(S.models, modelId);
    if (model) S.inputMode = SS.inputModeForModel(model);
    S.detail = null;
    S.fields = [];
    S.values = {};
    S.selectedVariant = 0;
    S.stageKey = "";
    renderInputModes();
    renderModels();
    renderModelCard();
    updateGenerateState();
    return loadModelDetail(modelId);
  }

  function renderProviders() {
    var host = $("studio-provider-options");
    if (host) {
      if (!S.providers.length) {
        host.innerHTML = '<div class="studio-rail-empty">No providers found.</div>';
      } else {
        host.innerHTML = S.providers.map(function (provider) {
          var active = provider.id === S.providerId;
          return '' +
            '<button class="ws-menu-item' + (active ? " ws-menu-item-active" : "") + '" data-provider="' + esc(provider.id) + '">' +
              '<span class="studio-dot ' + (provider.configured ? "on" : "off") + '"></span>' +
              '<span class="flex-1 min-w-0 text-left">' +
                '<span class="block truncate">' + esc(provider.label || provider.id) +
                  (provider.experimental ? ' <span class="studio-badge">experimental</span>' : "") + '</span>' +
                '<span class="block text-[9.5px] text-slate-500 truncate">' +
                  esc((provider.model_count || 0) + " models · " + (provider.configured ? "configured" : "setup required")) +
                '</span>' +
              '</span>' +
            '</button>';
        }).join("");
      }
    }
    setText("studio-provider-label", S.provider ? (S.provider.label || S.provider.id) : "Choose provider");
    setText("studio-provider-chip", S.provider ? (S.provider.label || S.provider.id) : "provider: —");
    if (S.catalog) {
      setText("studio-catalog-chip", (S.catalog.model_count || 0) + " models · " +
        (S.catalog.catalog_version || "catalog"));
    }
    icons();
  }

  function loadAgents() {
    return api("/api/agents/availability").then(function (data) {
      S.agents = withRequiredAgents(SS.normalizeList(data, "agents"));
      var stored = null;
      try { stored = window.localStorage.getItem("dennis.studio.agent"); } catch (err) { stored = null; }
      var next = find(S.agents, S.agentId) || find(S.agents, stored) ||
        S.agents.filter(function (agent) { return agent.available; })[0] || S.agents[0] || null;
      S.agentId = next ? next.id : null;
      S.agent = next;
      renderAgents();
      return null;
    }).catch(function (err) {
      setText("studio-agent-label", "Unavailable");
      toast("Could not load agents: " + err.message);
      return null;
    });
  }

  // Keep the integrations visible while a backend process with an older
  // registry is being restarted. Availability still comes from the backend.
  function withRequiredAgents(agents) {
    var list = Array.isArray(agents) ? agents.slice() : [];
    [
      { id: "cursor", name: "Cursor", provider: "Cursor" },
      { id: "openclaw", name: "OpenClaw", provider: "OpenClaw" }
    ].forEach(function (required) {
      if (!list.some(function (agent) { return agent && agent.id === required.id; })) {
        list.splice(Math.min(1, list.length), 0, {
          id: required.id,
          name: required.name,
          provider: required.provider,
          available: false,
          reason: "Backend restart required to detect this CLI"
        });
      }
    });
    return list;
  }

  function renderAgents() {
    /* One agent list, several hosts: the Activity console plus the Build
       dropdowns in the Ads and YouTube headers. Agents keep their own
       provider + model — they build; generation uses Provider/Model. */
    var hosts = ["studio-agent-options", "ads-agent-options", "yt-agent-options"];
    hosts.forEach(function (hostId) {
      var host = $(hostId);
      if (!host) return;
      if (!S.agents.length) {
        host.innerHTML = '<div class="studio-rail-empty">No agents registered.</div>';
        return;
      }
      host.innerHTML = S.agents.map(function (agent) {
        var active = agent.id === S.agentId;
        var available = agent.available !== false;
        var providerModel = [agent.provider, agent.model].filter(Boolean).join(" · ") || "agent";
        return '' +
          '<button class="ws-menu-item' + (active ? " ws-menu-item-active" : "") + (available ? "" : " ws-menu-item-disabled") + '" data-agent="' + esc(agent.id) + '"' + (available ? "" : " disabled") + '>' +
            '<span class="studio-dot ' + (available ? "on" : "off") + '"></span>' +
            '<span class="flex-1 min-w-0 text-left">' +
              '<span class="block truncate">' + esc(agent.name || agent.id) + '</span>' +
              '<span class="block text-[9.5px] text-slate-500 truncate">' +
                esc(providerModel + " · " + (available ? "ready" : (agent.reason || "not installed"))) +
              '</span>' +
            '</span>' +
          '</button>';
      }).join("");
    });
    ["studio-agent-label", "ads-agent-label", "yt-agent-label"].forEach(function (labelId) {
      var el = $(labelId);
      if (el) el.textContent = S.agent ? (S.agent.name || S.agent.id) : "—";
    });
    var btn = $("studio-agent-btn");
    if (btn) btn.disabled = !S.agents.length;
    icons();
  }

  function chooseAgent(id) {
    var agent = find(S.agents, id);
    if (!agent || agent.available === false) return;
    S.agentId = agent.id;
    S.agent = agent;
    try { window.localStorage.setItem("dennis.studio.agent", agent.id); } catch (err) { /* no-op */ }
    renderAgents();
    updateVideoKitModelHint();
  }

  function renderInputModes() {
    var host = $("studio-input-modes");
    if (!host) return;
    var counts = { text: 0, image: 0, video: 0 };
    S.models.forEach(function (model) {
      var mode = SS.inputModeForModel(model);
      counts[mode] = (counts[mode] || 0) + 1;
    });
    Array.prototype.forEach.call(host.querySelectorAll("[data-input-mode]"), function (button) {
      var mode = button.getAttribute("data-input-mode");
      button.classList.toggle("active", mode === S.inputMode);
      button.setAttribute("aria-selected", mode === S.inputMode ? "true" : "false");
      button.title = (counts[mode] || 0) + " documented " + mode + " model(s)";
      button.classList.toggle("is-empty", !counts[mode]);
    });
  }

  function renderModels() {
    var host = $("studio-model-options");
    var search = $("studio-model-search");
    var btn = $("studio-model-btn");
    if (btn) btn.disabled = !S.provider;
    if (search && search.value !== S.modelQuery) search.value = S.modelQuery;
    var selected = find(S.models, S.modelId);
    setText("studio-model-label", selected ? (selected.label || selected.id) : "Choose a model");
    if (!host) return;
    var shown = SS.filterModelsByInput(S.models, S.inputMode, S.kindFilter, S.modelQuery);
    if (!shown.length) {
      host.innerHTML = '<div class="studio-rail-empty">' +
        (S.models.length ? "No model matches this input mode and filter." : "No models loaded.") + '</div>';
      return;
    }
    host.innerHTML = shown.map(function (model) {
      var active = model.id === S.modelId;
      var tags = (model.tags || []).slice(0, 3).join(" · ");
      return '' +
        '<button class="ws-menu-item' + (active ? " ws-menu-item-active" : "") + '" data-model="' + esc(model.id) + '">' +
          '<span class="flex-1 min-w-0 text-left">' +
            '<span class="block truncate">' + esc(model.label || model.id) + '</span>' +
            '<span class="block text-[9.5px] text-slate-500 truncate">' +
              esc((model.mode_label || model.mode || model.kind || "") + (tags ? " · " + tags : "")) +
            '</span>' +
          '</span>' +
        '</button>';
    }).join("");
    icons();
  }

  function setModelNote(text) {
    var el = $("studio-model-note");
    if (!el) return;
    if (text) { el.textContent = text; el.classList.remove("hidden"); }
    else { el.textContent = ""; el.classList.add("hidden"); }
  }

  function renderModelNote() {
    if (!S.provider) return;
    if (!S.provider.configured) {
      setModelNote(S.provider.id === "higgsfield_mcp"
        ? "Connect the MCP server in Setup before generating."
        : "Add API credentials in Setup before generating.");
      return;
    }
    setModelNote("");
    if (S.detail) {
      var hint = SS.acceptHint(S.detail);
      setMessage("studio-provider-note", hint || (S.provider.experimental ? "Experimental MCP provider." : "Official API"));
    }
  }

  function setMessage(id, text) {
    var el = $(id);
    if (!el) return;
    el.textContent = text || "";
  }

  function promptField() {
    return SS.promptField(S.fields);
  }

  function renderPrompt() {
    var el = $("studio-prompt");
    if (!el) return;
    var field = promptField();
    el.disabled = !field;
    el.placeholder = field
      ? (field.description || "Describe the shot, subject, camera and mood…")
      : "This model has no prompt input. Use its documented parameters below.";
    if (field && field.maxLength) el.maxLength = Number(field.maxLength);
    else el.removeAttribute("maxlength");
    if (document.activeElement !== el) {
      el.value = String(S.values.prompt == null ? "" : S.values.prompt);
    }
    updatePromptHint();
  }

  function updatePromptHint() {
    var el = $("studio-prompt");
    var hint = $("studio-prompt-hint");
    var count = $("studio-prompt-count");
    if (!el) return;
    var field = promptField();
    var max = field && field.maxLength ? Number(field.maxLength) : 2000;
    var label = el.value.length + " / " + max;
    if (hint) hint.textContent = field ? (field.description ? "Documented input" : "") : "No prompt field";
    if (count) count.textContent = field ? label : "not used";
  }

  function fieldControlHtml(field) {
    var name = esc(field.name);
    var value = S.values[field.name];
    if (field.control === "toggle") {
      return '<label class="studio-toggle"><input type="checkbox" data-field="' + name + '"' +
        (value ? " checked" : "") + '><span>Enabled</span></label>';
    }
    if (field.control === "select") {
      var choices = field.enum || [];
      var forceChoices = field.name === "aspect_ratio";
      var forceSelect = field.name === "resolution";
      if (choices.length && !forceSelect && (choices.length <= 6 || forceChoices)) {
        return '<div class="studio-choice-group" data-choice-field="' + name + '">' +
          choices.map(function (option) {
            var text = String(option);
            return '<button type="button" class="studio-choice' +
              (String(value) === text ? " active" : "") + '" data-choice-field="' + name +
              '" data-choice-value="' + esc(text) + '">' + esc(text) + "</button>";
          }).join("") + '</div>';
      }
      var options = choices.map(function (option) {
        var text = String(option);
        return '<option value="' + esc(text) + '"' + (String(value) === text ? " selected" : "") + '>' +
          esc(text) + '</option>';
      }).join("");
      return '<select data-field="' + name + '" class="studio-input">' + options + '</select>';
    }
    if (field.control === "number") {
      if (field.name === "duration" && field.minimum != null && field.maximum != null) {
        var minimum = Number(field.minimum);
        var maximum = Number(field.maximum);
        var durationChoices = [5, 10, 15, 20, 30, 45, 60].filter(function (option) {
          return option >= minimum && option <= maximum;
        });
        var current = Number(value);
        if (isFinite(current) && durationChoices.indexOf(current) === -1) durationChoices.push(current);
        durationChoices.sort(function (a, b) { return a - b; });
        if (durationChoices.length) {
          return '<div class="studio-choice-group" data-choice-field="' + name + '">' +
            durationChoices.map(function (option) {
              return '<button type="button" class="studio-choice' +
                (String(value) === String(option) ? " active" : "") + '" data-choice-field="' + name +
                '" data-choice-value="' + esc(option) + '">' + esc(option) + 's</button>';
            }).join("") + '</div>';
        }
      }
      var attrs = ' type="number"';
      if (field.minimum != null) attrs += ' min="' + esc(field.minimum) + '"';
      if (field.maximum != null) attrs += ' max="' + esc(field.maximum) + '"';
      attrs += ' step="' + (field.type === "integer" ? "1" : "any") + '"';
      return '<input data-field="' + name + '"' + attrs + ' value="' + esc(value) + '" class="studio-input">';
    }
    if (field.control === "json") {
      var json = "";
      try { json = value ? JSON.stringify(value, null, 2) : ""; } catch (err) { json = ""; }
      return '<textarea data-field="' + name + '" rows="3" spellcheck="false" class="studio-input studio-textarea">' + esc(json) + '</textarea>';
    }
    if (field.control === "url_list") {
      var urls = Array.isArray(value) ? value.join("\n") : (value || "");
      return '<textarea data-field="' + name + '" rows="2" spellcheck="false" placeholder="One URL per line" class="studio-input studio-textarea">' + esc(urls) + '</textarea>';
    }
    if (field.control === "url") {
      return '<div class="studio-url-row">' +
        '<input data-field="' + name + '" type="url" value="' + esc(value) + '" placeholder="https://…" class="studio-input">' +
        '<button class="studio-attach-btn" data-upload-field="' + name + '" title="Upload a file for this field"><i data-lucide="upload" class="w-3.5 h-3.5"></i></button>' +
        '</div>';
    }
    return '<input data-field="' + name + '" type="text" value="' + esc(value) + '" class="studio-input">';
  }

  function fieldHtml(field) {
    var hint = field.description ? '<p class="studio-param-hint">' + esc(field.description) + '</p>' : "";
    return '' +
      '<div class="studio-param" data-param="' + esc(field.name) + '">' +
        '<div class="studio-param-head">' +
          '<label>' + esc(field.title || field.name) + (field.required ? ' <span class="studio-required">*</span>' : '') + '</label>' +
          '<span class="studio-param-type">' + esc(field.type || field.control) + '</span>' +
        '</div>' +
        fieldControlHtml(field) +
        hint +
      '</div>';
  }

  function renderParams() {
    var host = $("studio-params");
    if (!host) return;
    var fields = S.fields.filter(function (field) { return field.name !== "prompt"; });
    if (!fields.length) {
      host.innerHTML = S.detail
        ? '<div class="studio-params-empty">This model documents no additional parameters.</div>'
        : "";
      icons();
      return;
    }
    var priority = ["duration", "aspect_ratio", "resolution"];
    var basic = fields.filter(function (field) {
      return priority.indexOf(field.name) !== -1;
    }).sort(function (a, b) {
      return priority.indexOf(a.name) - priority.indexOf(b.name);
    });
    var advanced = fields.filter(function (field) {
      return basic.indexOf(field) === -1;
    });
    var html = basic.map(fieldHtml).join("");
    if (advanced.length) {
      var open = advanced.some(function (field) { return !!field.required; }) ? " open" : "";
      html += '<details class="studio-advanced"' + open + '><summary>Advanced Settings <span class="studio-mono">' +
        advanced.length + '</span></summary>' +
        '<div class="studio-params-grid">' + advanced.map(fieldHtml).join("") + '</div></details>';
    }
    host.innerHTML = html;
    icons();
  }

  function updateParamFromEvent(target) {
    var choice = target.closest("[data-choice-value]");
    if (choice) {
      var choiceName = choice.getAttribute("data-choice-field");
      S.values[choiceName] = choice.getAttribute("data-choice-value");
      var group = choice.closest("[data-choice-field]");
      if (group) {
        Array.prototype.forEach.call(group.querySelectorAll("[data-choice-value]"), function (button) {
          button.classList.toggle("active", button === choice);
        });
      }
      updateGenerateState();
      return;
    }
    var fieldEl = target.closest("[data-field]");
    if (!fieldEl) return;
    var name = fieldEl.getAttribute("data-field");
    var field = fieldByName(name);
    if (!field) return;
    if (field.control === "toggle") S.values[name] = !!fieldEl.checked;
    else S.values[name] = fieldEl.value;
    if (name === "prompt") updatePromptHint();
    updateGenerateState();
  }

  function syncParamInputs() {
    Array.prototype.forEach.call(document.querySelectorAll("#studio-params [data-field]"), function (el) {
      var name = el.getAttribute("data-field");
      var field = fieldByName(name);
      if (!field) return;
      var value = S.values[name];
      if (field.control === "toggle") el.checked = !!value;
      else if (field.control === "json") {
        try { el.value = value ? JSON.stringify(value, null, 2) : ""; } catch (err) { el.value = ""; }
      } else if (field.control === "url_list") {
        el.value = Array.isArray(value) ? value.join("\n") : (value || "");
      } else {
        el.value = value == null ? "" : String(value);
      }
    });
    Array.prototype.forEach.call(document.querySelectorAll("#studio-params [data-choice-field]"), function (group) {
      var name = group.getAttribute("data-choice-field");
      var value = String(S.values[name] == null ? "" : S.values[name]);
      Array.prototype.forEach.call(group.querySelectorAll("[data-choice-value]"), function (button) {
        button.classList.toggle("active", button.getAttribute("data-choice-value") === value);
      });
    });
  }

  function updateGenerateState() {
    var button = $("studio-generate");
    var providerReady = S.provider && S.provider.configured;
    var disabled = S.generateBusy || S.uploading > 0 || !providerReady || !S.modelId || !S.detail;
    if (button) {
      button.disabled = !!disabled;
      button.title = disabled
        ? (!providerReady ? "Configure the provider in Setup first" : "Choose a model first")
        : "Generate";
    }
    setText("studio-provider-note", !S.provider
      ? "Choose a provider."
      : (!providerReady
        ? (S.provider.id === "higgsfield_mcp" ? "MCP is not connected. Open Setup." : "API credentials are not configured. Open Setup.")
        : (S.detail ? (SS.acceptHint(S.detail) || "Ready") : "")));
    var label = "Generate";
    if (S.detail) {
      if (S.detail.kind === "image") label = "Generate image";
      else if (S.detail.kind === "audio") label = "Generate audio";
      else label = "Generate video";
    }
    setText("studio-generate-label", label);
    if (S.uploading) setText("studio-prompt-count", "Uploading " + S.uploading + " file(s)…");
    else updatePromptHint();
  }

  /* ------------------------------------------------------------------ */
  /* Attachments / uploads                                               */
  /* ------------------------------------------------------------------ */
  function fileToBase64(file) {
    return new Promise(function (resolve, reject) {
      var reader = new FileReader();
      reader.onload = function () {
        var text = String(reader.result || "");
        resolve(text.indexOf(",") === -1 ? text : text.slice(text.indexOf(",") + 1));
      };
      reader.onerror = function () { reject(new Error("Could not read " + file.name)); };
      reader.readAsDataURL(file);
    });
  }

  function renderAttachments() {
    var host = $("studio-attachments");
    if (!host) return;
    if (!S.attachments.length) {
      show(host, false);
      host.innerHTML = "";
      return;
    }
    show(host, true);
    host.innerHTML = S.attachments.map(function (item, index) {
      return '<span class="studio-chip" title="' + esc(item.name) + '">' +
        '<i data-lucide="paperclip" class="w-3 h-3"></i>' +
        '<span class="studio-chip-name">' + esc(item.name) + '</span>' +
        '<button class="studio-chip-x" data-remove-attachment="' + index + '" title="Remove"><i data-lucide="x" class="w-3 h-3"></i></button>' +
        '</span>';
    }).join("");
    icons();
  }

  function urlFieldForUpload() {
    if (S.pendingField) return fieldByName(S.pendingField);
    var summary = (S.detail && S.detail.summary) || {};
    var preferred = (summary.image_fields || []).concat(["image_url", "image_urls", "video_url", "video_urls"]);
    for (var i = 0; i < preferred.length; i += 1) {
      var field = fieldByName(preferred[i]);
      if (field && field.control === "url" && !S.values[field.name]) return field;
      if (field && field.control === "url_list" && !S.values[field.name]) return field;
    }
    var urlFields = S.fields.filter(function (field) {
      return field.control === "url" || field.control === "url_list";
    });
    return urlFields[0] || null;
  }

  function applyUploadUrl(fieldName, url) {
    var field = fieldName ? fieldByName(fieldName) : urlFieldForUpload();
    if (!field) return null;
    if (field.control === "url") {
      S.values[field.name] = url;
    } else if (field.control === "url_list") {
      var current = S.values[field.name];
      var list = Array.isArray(current) ? current.slice() : (current ? String(current).split(/[\n,]+/).filter(Boolean) : []);
      list.push(url);
      S.values[field.name] = list;
    } else {
      return null;
    }
    syncParamInputs();
    return field.name;
  }

  function uploadFiles(files) {
    var list = Array.prototype.slice.call(files || []);
    if (!list.length) return;
    if (!S.provider) { toast("Choose a provider first"); return; }
    if (!S.provider.configured) {
      toast("Configure the provider in Setup first");
      setTab("setup");
      return;
    }
    list.forEach(function (file) {
      if (S.attachments.length >= MAX_ATTACH) { toast("Up to " + MAX_ATTACH + " attachments"); return; }
      if (file.size > MAX_UPLOAD_BYTES) { toast(file.name + " is larger than 25 MB"); return; }
      S.uploading += 1;
      updateGenerateState();
      fileToBase64(file)
        .then(function (data) {
          return api("/api/studio/uploads", {
            method: "POST",
            body: {
              provider: S.provider.id,
              filename: file.name,
              content_type: file.type || "",
              data_base64: data
            }
          });
        })
        .then(function (result) {
          var item = {
            name: file.name,
            url: result.public_url || "",
            media_id: (result.media && result.media.id) || null,
            field: S.pendingField || null
          };
          S.attachments.push(item);
          if (item.url) {
            var applied = applyUploadUrl(item.field, item.url);
            if (applied) item.field = applied;
          }
          renderAttachments();
          toast("Uploaded " + file.name);
        })
        .catch(function (err) { toast(err.message); })
        .then(function () {
          S.uploading = Math.max(0, S.uploading - 1);
          S.pendingField = null;
          updateGenerateState();
        });
    });
  }

  function removeAttachment(index) {
    var item = S.attachments[index];
    if (!item) return;
    S.attachments.splice(index, 1);
    if (item.field) {
      var field = fieldByName(item.field);
      if (field && field.control === "url" && S.values[item.field] === item.url) {
        S.values[item.field] = "";
      }
      if (field && field.control === "url_list") {
        var current = S.values[item.field];
        var list = Array.isArray(current) ? current : [];
        S.values[item.field] = list.filter(function (url) { return url !== item.url; });
      }
      syncParamInputs();
    }
    renderAttachments();
  }

  /* ------------------------------------------------------------------ */
  /* Jobs and stage                                                      */
  /* ------------------------------------------------------------------ */
  function jobElapsed(job) {
    if (!job) return 0;
    var base = Number(job.elapsed_seconds) || 0;
    if (SS.isActive(job) && S.jobSyncedAt) base += (Date.now() - S.jobSyncedAt) / 1000;
    return base;
  }

  function loadJobs(silent) {
    return api("/api/studio/jobs?limit=" + JOB_LIMIT).then(function (data) {
      S.jobs = SS.normalizeList(data, "jobs");
      var finished = false;
      S.jobs.forEach(function (job) {
        if (job.terminal && SS.jobOutput(job) && !S.finishedSeen[job.id]) {
          S.finishedSeen[job.id] = true;
          finished = true;
        }
      });
      if (!S.jobId && !S.selectedMedia && S.jobs.length) {
        S.jobId = S.jobs[0].id;
      }
      if (S.jobId) {
        var updated = find(S.jobs, S.jobId);
        if (updated) {
          S.selectedJob = updated;
          S.jobSyncedAt = Date.now();
        } else if (S.selectedJob) {
          S.jobId = null;
          S.selectedJob = null;
        }
      }
      renderJobs();
      renderStage();
      renderUsage();
      updateHeader();
      if (finished) loadLibrary(true);
    }).catch(function (err) {
      if (!silent) toast(err.message);
    });
  }

  function renderJobs() {
    renderHistoryPage();
    var host = $("studio-jobs-list");
    if (!host) return;
    var jobs = S.jobs.slice(0, 6);
    if (!jobs.length) {
      host.innerHTML = '<div class="studio-rail-empty">No jobs yet. Submit a prompt to start.</div>';
      return;
    }
    host.innerHTML = jobs.map(function (job) {
      var active = job.id === S.jobId;
      var tone = SS.stateTone(job.state);
      var output = SS.jobOutput(job);
      var kind = (output && output.kind) || job.kind || "video";
      var thumb = "";
      if (output && (output.media_id || output.url)) {
        var src = mediaLink(output.media_id, output.url);
        thumb = kind === "image"
          ? '<img src="' + esc(src) + '" alt="" loading="lazy">'
          : '<video src="' + esc(src) + '" muted preload="metadata" playsinline></video>';
      } else {
        thumb = '<i data-lucide="' + (kind === "image" ? "image" : "film") + '" class="w-4 h-4"></i>';
      }
      var title = job.prompt ? String(job.prompt).slice(0, 80) : SS.jobTitle(job);
      return '' +
        '<div class="studio-recent' + (active ? " active" : "") + '" data-job-id="' + esc(job.id) + '" role="button" tabindex="0">' +
          '<div class="studio-recent-thumb">' + thumb + '</div>' +
          '<div class="studio-recent-body">' +
            '<div class="studio-recent-title">' + esc(title) + '</div>' +
            '<div class="studio-recent-meta">' +
              '<span class="studio-state studio-state-' + esc(tone) + '">' + esc(SS.stateLabel(job.state)) + '</span>' +
              '<span>' + esc(SS.relativeTime(job.age_seconds)) + '</span>' +
            '</div>' +
          '</div>' +
          (job.can_cancel
            ? '<button class="studio-mini-btn" data-job-action="cancel" data-job-id="' + esc(job.id) + '" title="Cancel queued job"><i data-lucide="x" class="w-3 h-3"></i></button>'
            : (job.terminal
              ? '<button class="studio-mini-btn studio-mini-danger" data-job-action="delete" data-job-id="' + esc(job.id) + '" title="Delete job"><i data-lucide="trash-2" class="w-3 h-3"></i></button>'
              : "")) +
        '</div>';
    }).join("");
    icons();
  }

  function historyRow(job) {
    var output = SS.jobOutput(job);
    var kind = (output && output.kind) || job.kind || "video";
    var src = output ? mediaLink(output.media_id, output.url) : "";
    var thumb = src
      ? (kind === "image"
        ? '<img src="' + esc(src) + '" alt="" loading="lazy">'
        : '<video src="' + esc(src) + '" muted preload="metadata" playsinline></video>')
      : '<i data-lucide="film" class="w-4 h-4"></i>';
    var tone = SS.stateTone(job.state);
    return '' +
      '<div class="studio-history-row' + (job.id === S.jobId ? " active" : "") + '" data-history-job="' + esc(job.id) + '" role="button" tabindex="0">' +
        '<div class="studio-history-thumb">' + thumb + '</div>' +
        '<div class="studio-history-body">' +
          '<div class="studio-history-prompt">' + esc(job.prompt || SS.jobTitle(job)) + '</div>' +
          '<div class="studio-history-meta">' +
            '<span class="studio-state studio-state-' + esc(tone) + '">' + esc(SS.stateLabel(job.state)) + '</span>' +
            '<span>' + esc(job.model_label || job.model_id || "model") + '</span>' +
            '<span>' + esc(SS.relativeTime(job.age_seconds)) + '</span>' +
            (SS.isActive(job) ? '<span>' + esc(SS.elapsedLabel(jobElapsed(job))) + '</span>' : '') +
          '</div>' +
        '</div>' +
        '<div class="studio-history-actions">' +
          (job.can_cancel
            ? '<button type="button" class="studio-mini-btn" data-history-action="cancel" data-job-id="' + esc(job.id) + '" title="Cancel queued job"><i data-lucide="x" class="w-3 h-3"></i></button>'
            : (job.terminal
              ? '<button type="button" class="studio-mini-btn studio-mini-danger" data-history-action="delete" data-job-id="' + esc(job.id) + '" title="Delete job"><i data-lucide="trash-2" class="w-3 h-3"></i></button>'
              : "")) +
        '</div>' +
      '</div>';
  }

  function renderHistoryPage() {
    var host = $("studio-history-page");
    if (!host) return;
    var completed = S.jobs.filter(function (job) { return job.state === "succeeded"; }).length;
    setText("studio-history-stats", S.jobs.length +
      (S.jobs.length === 1 ? " job" : " jobs") + " · " + completed + " completed · newest first");
    if (!S.jobs.length) {
      host.innerHTML = '<div class="studio-empty-state"><i data-lucide="history" class="w-6 h-6"></i>' +
        '<span>No generations yet. Submit a prompt to start a provider job.</span></div>';
      icons();
      return;
    }
    host.innerHTML = S.jobs.map(historyRow).join("");
    icons();
  }

  function selectJob(jobId) {
    var job = find(S.jobs, jobId);
    S.selectedMedia = null;
    S.jobId = jobId;
    S.selectedJob = job || null;
    S.selectedVariant = 0;
    S.jobSyncedAt = Date.now();
    S.stageKey = "";
    renderJobs();
    if (job) renderStage();
    else {
      api("/api/studio/jobs/" + encodeURIComponent(jobId)).then(function (data) {
        S.selectedJob = data;
        S.jobSyncedAt = Date.now();
        renderStage();
      }).catch(function (err) { toast(err.message); });
    }
  }

  function selectMedia(item) {
    S.selectedMedia = item;
    S.jobId = null;
    S.selectedJob = null;
    S.selectedVariant = 0;
    S.stageKey = "";
    renderJobs();
    renderStage();
    renderUsage();
  }

  function renderStage() {
    var empty = $("studio-stage-empty");
    var busyPanel = $("studio-stage-busy");
    var mediaHost = $("studio-stage-media");
    var errorPanel = $("studio-stage-error");
    [empty, busyPanel, mediaHost, errorPanel].forEach(function (el) { show(el, false); });
    show($("studio-variations"), false);

    var job = S.selectedJob;
    var item = S.selectedMedia;

    if (item) {
      setText("studio-stage-kind", item.kind || "media");
      setText("studio-stage-state", "library");
      setText("studio-job-timer", "");
      setText("studio-stage-model", item.title || item.filename || "Library media");
      setText("studio-result-elapsed", SS.humanBytes(item.size));
      renderMedia(mediaFromItem(item), item.title || item.filename || "Library media");
      show(mediaHost, true);
      updateStageControls(mediaFromItem(item));
      return;
    }

    if (!job) {
      setText("studio-stage-kind", S.detail && S.detail.kind ? S.detail.kind : "—");
      setText("studio-stage-state", S.detail ? "ready" : "idle");
      setText("studio-job-timer", "");
      setText("studio-stage-model", S.detail ? (S.detail.label || S.detail.id) : "No model selected");
      setText("studio-result-elapsed", "");
      show(empty, true);
      updateStageControls(null);
      return;
    }

    setText("studio-stage-kind", job.kind || "job");
    setText("studio-stage-state", SS.stateLabel(job.state));
    setText("studio-stage-model", (job.model_label || job.model_id || "Job") + (job.prompt ? " · " + String(job.prompt).slice(0, 60) : ""));
    setText("studio-job-timer", SS.isActive(job) ? SS.elapsedLabel(jobElapsed(job)) : "");
    setText("studio-result-elapsed", SS.elapsedLabel(jobElapsed(job)));

    if (job.state === "failed") {
      setText("studio-error-text", SS.jobErrorText(job));
      show(errorPanel, true);
      updateStageControls(null);
      return;
    }
    if (job.state === "cancelled") {
      setText("studio-error-text", "This job was cancelled before it produced an output.");
      show(errorPanel, true);
      updateStageControls(null);
      return;
    }
    if (job.state === "succeeded") {
      var outputs = (job.outputs || []).filter(function (output) {
        return output && (output.media_id || output.url);
      });
      if (!outputs.length) {
        setText("studio-error-text", "The job finished but returned no output URL.");
        show(errorPanel, true);
        updateStageControls(null);
        return;
      }
      if (S.selectedVariant >= outputs.length) S.selectedVariant = 0;
      var selected = outputs[S.selectedVariant] || outputs[0];
      setText("studio-stage-state", "ready");
      renderMedia(selected, SS.jobTitle(job));
      renderVariations(outputs, job);
      show(mediaHost, true);
      updateStageControls(selected);
      return;
    }

    setText("studio-busy-state", SS.isActive(job)
      ? (job.state === "queued" ? "Queued with the provider…" : "Provider is generating…")
      : SS.stateLabel(job.state));
    setText("studio-busy-elapsed", SS.elapsedLabel(jobElapsed(job)) + " elapsed");
    var cancel = $("studio-cancel-job");
    if (cancel) {
      cancel.classList.toggle("hidden", !job.can_cancel);
      cancel.disabled = false;
    }
    show(busyPanel, true);
    updateStageControls(null);
  }

  function renderMedia(output, label) {
    var host = $("studio-stage-media");
    if (!host) return;
    var key = (S.selectedMedia ? "media:" + S.selectedMedia.id : "job:" + (S.jobId || "")) +
      ":" + (output && (output.media_id || output.url) || "");
    if (S.stageKey === key) return;
    S.stageKey = key;
    host.innerHTML = "";
    if (!output) return;
    var src = mediaLink(output.media_id, output.url);
    if (!src) return;
    var kind = output.kind || (S.detail && S.detail.kind) || "video";
    var frame = document.createElement("div");
    frame.className = "studio-media-frame";
    var media;
    if (kind === "image") {
      media = document.createElement("img");
      media.src = src;
      media.alt = label || "Generated image";
      media.loading = "lazy";
    } else if (kind === "audio") {
      media = document.createElement("audio");
      media.src = src;
      media.controls = true;
    } else {
      media = document.createElement("video");
      media.src = src;
      media.controls = true;
      media.playsInline = true;
      media.loop = true;
      media.muted = true;
      media.autoplay = true;
      media.preload = "metadata";
    }
    frame.appendChild(media);
    host.appendChild(frame);
  }

  function renderVariations(outputs, job) {
    var host = $("studio-variations-list");
    var panel = $("studio-variations");
    if (!host || !panel) return;
    if (!outputs || outputs.length <= 1) {
      show(panel, false);
      host.innerHTML = "";
      return;
    }
    setText("studio-variations-count", outputs.length + " outputs");
    host.innerHTML = outputs.map(function (output, index) {
      var src = mediaLink(output.media_id, output.url);
      var kind = output.kind || (job && job.kind) || "video";
      var body = kind === "image"
        ? '<img src="' + esc(src) + '" alt="Variation ' + (index + 1) + '" loading="lazy">'
        : '<video src="' + esc(src) + '" muted preload="metadata" playsinline></video>';
      var play = kind === "image" ? "" : '<span class="studio-variation-play"><i data-lucide="play" class="w-3.5 h-3.5"></i></span>';
      return '<button type="button" class="studio-variation' + (index === S.selectedVariant ? " active" : "") +
        '" data-variant="' + index + '" title="Show variation ' + (index + 1) + '">' + body + play +
        '<span>' + (index + 1) + '</span></button>';
    }).join("");
    show(panel, true);
    icons();
  }

  function mediaFromItem(item) {
    if (!item) return null;
    return { kind: item.kind, media_id: item.id, url: null, title: item.title, filename: item.filename };
  }

  function updateStageControls(output) {
    var open = $("studio-open-media");
    var download = $("studio-download");
    var share = $("studio-share");
    var save = $("studio-save-library");
    var saveSkill = $("studio-save-skill");
    var available = !!(output && (output.media_id || output.url));
    [open, download, share].forEach(function (button) { if (button) button.disabled = !available; });
    if (save) save.disabled = !(output && output.url && !output.media_id);
    var jobPrompt = S.selectedJob && S.selectedJob.params && S.selectedJob.params.prompt;
    if (saveSkill) saveSkill.disabled = !(available && jobPrompt);
    S.currentOutput = output || null;
  }

  function outputUrl(output) {
    if (!output) return "";
    return mediaLink(output.media_id, output.url);
  }

  function openOutput() {
    var url = outputUrl(S.currentOutput);
    if (url) window.open(url, "_blank", "noopener");
  }

  function downloadOutput() {
    var output = S.currentOutput;
    var url = outputUrl(output);
    if (!url) return;
    var item = output && output.media_id ? mediaItem(output.media_id) : null;
    var a = document.createElement("a");
    a.href = url;
    var ext = (output && output.kind === "image") ? "png" : (output && output.kind === "audio" ? "mp3" : "mp4");
    var filename = (item && item.filename) || (output && output.filename) ||
      ((SS.jobTitle(S.selectedJob) || "studio-output") + "." + ext);
    a.download = filename.replace(/[^A-Za-z0-9._ -]/g, "_");
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
  }

  function shareOutput() {
    var output = S.currentOutput;
    var url = outputUrl(output);
    if (!url) return;
    if (!navigator.share || !window.File) {
      toast("This browser has no file share sheet; downloading instead.");
      downloadOutput();
      return;
    }
    fetch(url, { credentials: "same-origin" })
      .then(function (response) {
        if (!response.ok) throw new Error("Could not read the media file.");
        return response.blob();
      })
      .then(function (blob) {
        var ext = output.kind === "image" ? "png" : (output.kind === "audio" ? "mp3" : "mp4");
        var file = new File([blob], "studio-output." + ext, { type: blob.type || "application/octet-stream" });
        if (navigator.canShare && !navigator.canShare({ files: [file] })) {
          throw new Error("File sharing is not supported here.");
        }
        return navigator.share({ files: [file], title: SS.jobTitle(S.selectedJob) || "Studio output" });
      })
      .catch(function (err) {
        if (err && err.name === "AbortError") return;
        toast(err.message || "Sharing failed; downloading instead.");
        downloadOutput();
      });
  }

  function saveOutputToLibrary() {
    var output = S.currentOutput;
    if (!output) return;
    if (output.media_id) { toast("This output is already in your library."); return; }
    if (!output.url) return;
    var button = $("studio-save-library");
    busy(button, true, "Saving…");
    api("/api/studio/library/import", {
      method: "POST",
      body: {
        url: output.url,
        kind: output.kind || (S.selectedJob && S.selectedJob.kind) || "video",
        title: SS.jobTitle(S.selectedJob) || "Studio output",
        provider: S.provider ? S.provider.id : null,
        model_id: S.selectedJob ? S.selectedJob.model_id : S.modelId
      }
    }).then(function (data) {
      output.media_id = data.item.id;
      output.kind = data.item.kind;
      S.stageKey = "";
      renderStage();
      toast("Saved to the local library");
      return loadLibrary(true);
    }).catch(function (err) {
      toast(err.message);
    }).then(function () { busy(button, false, "Add to library"); });
  }

  function saveRenderedSkill() {
    var job = S.selectedJob;
    if (!job) return Promise.resolve(null);
    var prompt = (job.params && job.params.prompt) || job.prompt || "";
    if (!String(prompt).trim()) {
      toast("This job has no saved prompt to reuse.");
      return Promise.resolve(null);
    }
    var name = "Recreate " + (SS.jobTitle(job) || "Studio output");
    if (name.length > 90) name = name.slice(0, 90);
    return saveVideoSkill({
      name: name,
      prompt: prompt,
      analysis: S.videoKit.analysis || "",
      source: S.videoKit.source || (job.model_id || ""),
      platform: S.videoKit.platform || "",
      button: $("studio-save-skill"),
      restoreLabel: "Save skill"
    });
  }

  function cancelSelectedJob() {
    var job = S.selectedJob;
    if (!job || !job.can_cancel) return;
    var button = $("studio-cancel-job");
    if (button) button.disabled = true;
    api("/api/studio/jobs/" + encodeURIComponent(job.id) + "/cancel", { method: "POST", body: {} })
      .then(function (updated) {
        toast("Job cancelled");
        S.selectedJob = updated;
        S.jobSyncedAt = Date.now();
        return loadJobs(true);
      })
      .catch(function (err) {
        toast(err.message);
        if (button) button.disabled = false;
      });
  }

  function jobAction(action, jobId) {
    if (action === "cancel") {
      api("/api/studio/jobs/" + encodeURIComponent(jobId) + "/cancel", { method: "POST", body: {} })
        .then(function () { toast("Cancel requested"); return loadJobs(true); })
        .catch(function (err) { toast(err.message); });
      return;
    }
    if (action === "delete") {
      if (!window.confirm("Delete this job from the activity list?")) return;
      api("/api/studio/jobs/" + encodeURIComponent(jobId), { method: "DELETE" })
        .then(function () {
          if (S.jobId === jobId) {
            S.jobId = null;
            S.selectedJob = null;
            S.stageKey = "";
          }
          toast("Job deleted");
          return loadJobs(true);
        })
        .catch(function (err) { toast(err.message); });
    }
  }

  /* ------------------------------------------------------------------ */
  /* Library                                                             */
  /* ------------------------------------------------------------------ */
  function loadLibrary(silent) {
    return api("/api/studio/library?limit=200").then(function (data) {
      S.library = SS.normalizeList(data, "items");
      var stats = data.stats || {};
      S.libraryStats = { count: stats.count || S.library.length, bytes: stats.bytes || 0 };
      setText("studio-library-stats", (S.libraryStats.count) + " files · " + SS.humanBytes(S.libraryStats.bytes));
      renderLibrary();
      renderUsage();
    }).catch(function (err) {
      if (!silent) toast(err.message);
    });
  }

  function renderLibrary() {
    var host = $("studio-library-list");
    if (!host) return;
    var query = S.libraryQuery.toLowerCase().trim();
    var items = S.library.filter(function (item) {
      if (S.libraryKind && item.kind !== S.libraryKind) return false;
      if (!query) return true;
      return [item.title, item.filename, item.kind, item.model_id].join(" ").toLowerCase().indexOf(query) !== -1;
    });
    if (!items.length) {
      host.innerHTML = '<div class="studio-empty-state"><i data-lucide="library" class="w-6 h-6"></i><span>' +
        esc(S.library.length ? "No media matches this filter." : "No saved outputs yet. Finished generations are downloaded here.") +
        '</span></div>';
      icons();
      return;
    }
    host.innerHTML = items.map(function (item) {
      var src = mediaLink(item.id, null);
      var thumb = item.kind === "video"
        ? '<video src="' + esc(src) + '" muted preload="metadata" playsinline></video>'
        : (item.kind === "image"
          ? '<img src="' + esc(src) + '" alt="' + esc(item.title || "media") + '" loading="lazy">'
          : '<i data-lucide="file" class="w-6 h-6 text-slate-500"></i>');
      return '' +
        '<div class="studio-media-card" data-media-id="' + esc(item.id) + '" role="button" tabindex="0">' +
          '<div class="studio-media-thumb">' + thumb + '</div>' +
          '<div class="studio-media-card-body">' +
            '<div class="studio-media-card-title truncate">' + esc(item.title || item.filename || item.id) + '</div>' +
            '<div class="studio-media-card-meta">' + esc(item.kind || "file") + ' · ' + esc(SS.humanBytes(item.size)) + '</div>' +
          '</div>' +
          '<div class="studio-media-card-actions">' +
            '<button class="studio-mini-btn" data-library-action="download" data-media-id="' + esc(item.id) + '" title="Download"><i data-lucide="download" class="w-3 h-3"></i></button>' +
            '<button class="studio-mini-btn" data-library-action="playlist" data-media-id="' + esc(item.id) + '" title="Add to playlist"><i data-lucide="list-plus" class="w-3 h-3"></i></button>' +
            '<button class="studio-mini-btn studio-mini-danger" data-library-action="delete" data-media-id="' + esc(item.id) + '" title="Delete"><i data-lucide="trash-2" class="w-3 h-3"></i></button>' +
          '</div>' +
        '</div>';
    }).join("");
    icons();
  }

  function mediaItem(id) { return find(S.library, id); }

  function libraryAction(action, mediaId) {
    var item = mediaItem(mediaId);
    if (action === "download") {
      if (!item) return;
      var a = document.createElement("a");
      a.href = mediaLink(item.id, null);
      a.download = item.filename || (item.id + "." + (item.kind === "image" ? "png" : "mp4"));
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      return;
    }
    if (action === "playlist") {
      openAddToPlaylist(mediaId);
      return;
    }
    if (action === "delete") {
      if (!window.confirm("Delete this file from the local library?")) return;
      api("/api/studio/library/" + encodeURIComponent(mediaId), { method: "DELETE" })
        .then(function () {
          if (S.selectedMedia && S.selectedMedia.id === mediaId) {
            S.selectedMedia = null;
            S.stageKey = "";
            renderStage();
          }
          toast("Media deleted");
          return Promise.all([loadLibrary(true), loadPlaylists(true)]);
        })
        .catch(function (err) { toast(err.message); });
    }
  }

  function renderModelCard() {
    var detail = S.detail;
    var model = detail || find(S.models, S.modelId);
    setText("studio-model-card-title", model ? (model.label || model.id) : "No model selected");
    setText("studio-model-card-provider", model
      ? ((S.provider && (S.provider.label || S.provider.id)) || model.provider || "provider") +
        (model.mode_label ? " · " + model.mode_label : "")
      : "—");
    var summary = (detail && detail.summary) || (model && model.summary) || {};
    var bits = [];
    if (model && model.kind) bits.push(model.kind);
    if (detail) bits.push(SS.acceptHint(detail) || "Documented inputs");
    if (summary.durations && summary.durations.length) bits.push(summary.durations.join("/") + "s");
    if (summary.resolutions && summary.resolutions.length) bits.push(summary.resolutions.slice(0, 3).join("/"));
    setText("studio-model-card-copy", model
      ? (bits.filter(Boolean).join(" · ") || "No additional capabilities documented.")
      : "Pick a model to see its documented capabilities.");
    var tags = (model && model.tags) || [];
    var tagHost = $("studio-model-card-tags");
    if (tagHost) {
      var values = tags.slice(0, 6).map(function (tag) { return '<span class="studio-tag">' + esc(tag) + "</span>"; });
      if (model && model.mode_label) values.unshift('<span class="studio-tag">' + esc(model.mode_label) + "</span>");
      tagHost.innerHTML = values.join("");
    }
    if (S.detail) {
      var hint = SS.acceptHint(S.detail);
      setMessage("studio-model-desc", hint ? "Documented input: " + hint + "." : "Schema-driven controls below.");
    } else {
      setMessage("studio-model-desc", "Choose a provider and model to see documented inputs.");
    }
    updateVideoKitModelHint();
  }

  function renderUsage() {
    var today = SS.countJobsSince(S.jobs, SS.startOfToday());
    var week = SS.countJobsSince(S.jobs, (Date.now() / 1000) - (7 * 24 * 60 * 60));
    setText("studio-usage-jobs", today);
    setText("studio-usage-week", week);
    setText("studio-usage-library", S.libraryStats.count || S.library.length || 0);
  }

  function setInputMode(mode) {
    if (["text", "image", "video"].indexOf(mode) === -1) return;
    var available = SS.filterModelsByInput(S.models, mode, "", "");
    if (!available.length) {
      toast("No documented " + mode + " input models for this provider.");
      return;
    }
    S.inputMode = mode;
    renderInputModes();
    var current = find(available, S.modelId);
    if (!current) {
      chooseModel(available[0].id);
    } else {
      renderModels();
    }
    var prompt = $("studio-prompt");
    if (prompt) prompt.focus();
  }

  function removePresetSuffix(text, suffix) {
    var index = text.lastIndexOf(suffix);
    if (index === -1) return text;
    return (text.slice(0, index) + text.slice(index + suffix.length))
      .replace(/[,\s]+$/g, "")
      .replace(/^[,\s]+/g, "");
  }

  function applyPreset(presetId) {
    var prompt = $("studio-prompt");
    var preset = PRESETS[presetId];
    if (!prompt || !preset) return;
    var text = prompt.value;
    if (S.preset === presetId) {
      text = removePresetSuffix(text, preset);
      S.preset = "";
    } else {
      if (S.preset && PRESETS[S.preset]) text = removePresetSuffix(text, PRESETS[S.preset]);
      text = text.replace(/[,\s]+$/g, "");
      text = (text ? text + ", " : "") + preset;
      S.preset = presetId;
    }
    prompt.value = text;
    S.values.prompt = text;
    updatePromptHint();
    updateGenerateState();
    Array.prototype.forEach.call(document.querySelectorAll("[data-preset]"), function (button) {
      button.classList.toggle("active", button.getAttribute("data-preset") === S.preset);
    });
  }

  function renderSearchResults() {
    var query = S.searchQuery.toLowerCase().trim();
    var menu = $("studio-search-menu");
    var host = $("studio-search-results");
    if (!menu || !host) return;
    if (!query) { show(menu, false); host.innerHTML = ""; return; }
    var models = SS.filterModels(S.models, null, null, query).slice(0, 6);
    var jobs = S.jobs.filter(function (job) {
      return [job.prompt, job.model_id, job.model_label].join(" ").toLowerCase().indexOf(query) !== -1;
    }).slice(0, 4);
    var media = S.library.filter(function (item) {
      return [item.title, item.filename, item.model_id, item.kind].join(" ").toLowerCase().indexOf(query) !== -1;
    }).slice(0, 4);
    var html = "";
    if (models.length) {
      html += '<div class="studio-search-group">Models</div>' + models.map(function (model) {
        return '<button type="button" class="studio-search-item" data-search-model="' + esc(model.id) + '">' +
          '<i data-lucide="cpu" class="w-3 h-3 text-fuchsia-300"></i>' +
          '<span class="truncate">' + esc(model.label || model.id) + '</span>' +
          '<small>' + esc(model.kind || "") + '</small></button>';
      }).join("");
    }
    if (jobs.length) {
      html += '<div class="studio-search-group">Prompts</div>' + jobs.map(function (job) {
        return '<button type="button" class="studio-search-item" data-search-job="' + esc(job.id) + '">' +
          '<i data-lucide="film" class="w-3 h-3 text-cyan-300"></i>' +
          '<span class="truncate">' + esc(job.prompt || SS.jobTitle(job)) + '</span>' +
          '<small>' + esc(SS.stateLabel(job.state)) + '</small></button>';
      }).join("");
    }
    if (media.length) {
      html += '<div class="studio-search-group">Library</div>' + media.map(function (item) {
        return '<button type="button" class="studio-search-item" data-search-media="' + esc(item.id) + '">' +
          '<i data-lucide="' + (item.kind === "image" ? "image" : "film") + '" class="w-3 h-3 text-emerald-300"></i>' +
          '<span class="truncate">' + esc(item.title || item.filename || item.id) + '</span>' +
          '<small>' + esc(item.kind || "") + '</small></button>';
      }).join("");
    }
    if (!html) html = '<div class="studio-rail-empty">No models, prompts or library items match.</div>';
    host.innerHTML = html;
    show(menu, true);
    icons();
  }

  function closeSearch() {
    S.searchQuery = "";
    var input = $("studio-search");
    if (input) input.value = "";
    show($("studio-search-menu"), false);
  }

  /* ------------------------------------------------------------------ */
  /* Playlists and templates                                             */
  /* ------------------------------------------------------------------ */
  function loadPlaylists(silent) {
    return api("/api/studio/playlists").then(function (data) {
      S.playlists = SS.normalizeList(data, "playlists");
      renderPlaylists();
    }).catch(function (err) {
      if (!silent) toast(err.message);
    });
  }

  function loadTemplates(silent) {
    return api("/api/studio/templates").then(function (data) {
      S.templates = SS.normalizeList(data, "templates");
      renderTemplates();
    }).catch(function (err) {
      if (!silent) toast(err.message);
    });
  }

  function playlistCover(playlist) {
    var item = playlist && playlist.items && playlist.items[0];
    if (!item || !item.media) return '<i data-lucide="list" class="w-4 h-4"></i>';
    var src = mediaLink(item.media.id, null);
    if (item.media.kind === "image") {
      return '<img src="' + esc(src) + '" alt="" loading="lazy">';
    }
    return '<i data-lucide="film" class="w-4 h-4"></i>';
  }

  function playlistRow(playlist) {
    return '<div class="studio-playlist-row">' +
      '<button type="button" class="studio-playlist-open" data-playlist-id="' + esc(playlist.id) + '">' +
        '<span class="studio-playlist-cover">' + playlistCover(playlist) + '</span>' +
        '<span class="min-w-0 flex-1">' +
          '<span class="studio-mini-title">' + esc(playlist.name) + '</span>' +
          '<span class="studio-mini-meta">' + (playlist.item_count || 0) + ' items</span>' +
        '</span>' +
      '</button>' +
      '<button class="studio-mini-btn studio-mini-danger" data-playlist-action="delete" data-playlist-id="' +
        esc(playlist.id) + '" title="Delete playlist"><i data-lucide="trash-2" class="w-3 h-3"></i></button>' +
    '</div>';
  }

  function templateRow(template, compact) {
    return '<div class="studio-template-row">' +
      '<button type="button" class="studio-template-open" data-template-id="' + esc(template.id) + '">' +
        '<span class="studio-template-icon"><i data-lucide="layout-template" class="w-3.5 h-3.5"></i></span>' +
        '<span class="min-w-0 flex-1">' +
          '<span class="studio-mini-title">' + esc(template.name) + '</span>' +
          '<span class="studio-mini-meta">' + esc(template.model_label || template.model_id) + '</span>' +
        '</span>' +
      '</button>' +
      (compact ? '' : '<button class="studio-mini-btn" data-template-action="delete" data-template-id="' +
        esc(template.id) + '" title="Delete template"><i data-lucide="trash-2" class="w-3 h-3"></i></button>') +
    '</div>';
  }

  function playlistCard(playlist) {
    var visible = (playlist.items || []).filter(function (entry) { return entry && entry.media; })[0];
    var cover = playlistCover(playlist);
    return '' +
      '<div class="studio-collection-card">' +
        '<button type="button" class="studio-collection-cover" data-playlist-id="' + esc(playlist.id) + '" title="Open playlist">' +
          cover +
          (visible ? '<span class="studio-collection-play"><i data-lucide="play" class="w-3 h-3"></i></span>' : "") +
        '</button>' +
        '<div class="studio-collection-body">' +
          '<div class="studio-collection-title">' + esc(playlist.name) + '</div>' +
          '<div class="studio-collection-meta">' + (playlist.item_count || 0) +
            (playlist.item_count === 1 ? " item" : " items") + ' · local collection</div>' +
        '</div>' +
        '<div class="studio-collection-actions">' +
          '<button type="button" class="studio-mini-btn studio-mini-danger" data-playlist-action="delete" data-playlist-id="' +
            esc(playlist.id) + '" title="Delete playlist"><i data-lucide="trash-2" class="w-3 h-3"></i></button>' +
        '</div>' +
      '</div>';
  }

  function templateCard(template) {
    return '' +
      '<div class="studio-collection-card">' +
        '<button type="button" class="studio-collection-cover" data-template-id="' + esc(template.id) + '" title="Apply template">' +
          '<i data-lucide="layout-template" class="w-7 h-7"></i>' +
        '</button>' +
        '<div class="studio-collection-body">' +
          '<div class="studio-collection-title">' + esc(template.name) + '</div>' +
          '<div class="studio-collection-meta">' + esc(template.model_label || template.model_id) + ' · saved model setup</div>' +
        '</div>' +
        '<div class="studio-collection-actions">' +
          '<button type="button" class="studio-mini-btn studio-mini-danger" data-template-action="delete" data-template-id="' +
            esc(template.id) + '" title="Delete template"><i data-lucide="trash-2" class="w-3 h-3"></i></button>' +
        '</div>' +
      '</div>';
  }

  function renderPlaylists() {
    var rail = $("studio-rail-playlists");
    var library = $("studio-library-playlists");
    var page = $("studio-playlists-page");
    setText("studio-playlists-count", S.playlists.length + (S.playlists.length === 1 ? " playlist" : " playlists"));
    if (rail) {
      rail.innerHTML = S.playlists.length
        ? S.playlists.slice(0, 3).map(playlistRow).join("")
        : '<div class="studio-rail-empty">No playlists yet.</div>';
    }
    if (library) {
      library.innerHTML = S.playlists.length
        ? S.playlists.map(playlistRow).join("")
        : '<div class="studio-rail-empty">Create a playlist, then add media from the library.</div>';
    }
    if (page) {
      page.innerHTML = S.playlists.length
        ? S.playlists.map(playlistCard).join("")
        : '<div class="studio-empty-state"><i data-lucide="list-video" class="w-7 h-7"></i>' +
          '<span>No playlists yet. Create one here or add a finished generation from the library.</span></div>';
    }
    icons();
  }

  function renderTemplates() {
    var library = $("studio-library-templates");
    var page = $("studio-templates-page");
    setText("studio-templates-count", S.templates.length + (S.templates.length === 1 ? " template" : " templates"));
    if (library) {
      library.innerHTML = S.templates.length
        ? S.templates.map(function (template) { return templateRow(template, false); }).join("")
        : '<div class="studio-rail-empty">No templates saved.</div>';
    }
    if (page) {
      page.innerHTML = S.templates.length
        ? S.templates.map(templateCard).join("")
        : '<div class="studio-empty-state"><i data-lucide="layout-template" class="w-7 h-7"></i>' +
          '<span>No templates saved. Configure a generation, then save the current setup.</span></div>';
    }
    icons();
  }

  function openModal(title, body, foot) {
    var modal = $("studio-modal");
    if (!modal) return;
    setText("studio-modal-title", title);
    var bodyEl = $("studio-modal-body");
    var footEl = $("studio-modal-foot");
    if (bodyEl) { bodyEl.onclick = null; bodyEl.innerHTML = body || ""; }
    if (footEl) footEl.innerHTML = foot || "";
    show(modal, true);
    icons();
  }

  function closeModal() {
    show($("studio-modal"), false);
    setText("studio-modal-body", "");
    setText("studio-modal-foot", "");
    var body = $("studio-modal-body");
    if (body) body.onclick = null;
    S.pendingPlaylistMedia = null;
  }

  function modalError(message) {
    var el = document.querySelector("#studio-modal-body [data-modal-error]");
    if (el) { el.textContent = message || ""; el.className = "studio-modal-error"; }
    else if (message) toast(message);
  }

  function openCreatePlaylist(mediaId) {
    S.pendingPlaylistMedia = mediaId || null;
    openModal("New playlist",
      '<label class="studio-field"><span>Playlist name</span><input id="studio-modal-name" type="text" maxlength="80" placeholder="e.g. Night city shots"></label>' +
      '<p class="studio-modal-error" data-modal-error></p>',
      '<button type="button" class="studio-btn studio-btn-ghost" data-modal-cancel>Cancel</button>' +
      '<button type="button" class="studio-btn" id="studio-modal-confirm">Create playlist</button>');
    var input = $("studio-modal-name");
    if (input) { input.focus(); input.addEventListener("keydown", function (event) { if (event.key === "Enter") submitCreatePlaylist(); }); }
    var confirm = $("studio-modal-confirm");
    if (confirm) confirm.addEventListener("click", submitCreatePlaylist);
    var cancel = document.querySelector("[data-modal-cancel]");
    if (cancel) cancel.addEventListener("click", closeModal);
  }

  function submitCreatePlaylist() {
    var input = $("studio-modal-name");
    var name = input ? input.value.trim() : "";
    if (!name) { modalError("Enter a playlist name."); return; }
    var button = $("studio-modal-confirm");
    busy(button, true, "Creating…");
    api("/api/studio/playlists", { method: "POST", body: { name: name } })
      .then(function (data) {
        var playlist = data.playlist;
        var pending = S.pendingPlaylistMedia;
        S.pendingPlaylistMedia = null;
        return loadPlaylists(true).then(function () {
          if (pending) {
            return addMediaToPlaylist(playlist.id, pending, true).then(function (added) {
              closeModal();
              toast(added ? "Playlist created and media added" : "Playlist created; media could not be added");
            });
          }
          closeModal();
          toast("Playlist created");
          return null;
        });
      })
      .catch(function (err) { modalError(err.message); busy(button, false, "Create playlist"); });
  }

  function addMediaToPlaylist(playlistId, mediaId, quiet) {
    return api("/api/studio/playlists/" + encodeURIComponent(playlistId) + "/items", {
      method: "POST", body: { media_id: mediaId }
    }).then(function () {
      if (!quiet) toast("Added to playlist");
      return loadPlaylists(true).then(function () { return true; });
    }).catch(function (err) { toast(err.message); return false; });
  }

  function openAddToPlaylist(mediaId) {
    if (!S.playlists.length) {
      openCreatePlaylist(mediaId);
      return;
    }
    var rows = S.playlists.map(function (playlist) {
      return '<div class="studio-modal-row">' +
        '<span class="studio-playlist-cover">' + playlistCover(playlist) + '</span>' +
        '<div class="studio-modal-row-body"><div class="studio-modal-row-title">' + esc(playlist.name) + '</div>' +
        '<div class="studio-modal-row-meta">' + (playlist.item_count || 0) + ' items</div></div>' +
        '<button type="button" class="studio-btn" data-add-playlist="' + esc(playlist.id) + '" data-media-id="' + esc(mediaId) + '">Add</button>' +
      '</div>';
    }).join("");
    openModal("Add to playlist",
      '<p class="studio-modal-copy">Choose a playlist for this media item.</p><div class="studio-modal-list">' + rows + '</div>',
      '<button type="button" class="studio-btn studio-btn-ghost" data-modal-cancel>Close</button>' +
      '<button type="button" class="studio-btn" id="studio-modal-new-playlist">New playlist</button>');
    var cancel = document.querySelector("[data-modal-cancel]");
    if (cancel) cancel.addEventListener("click", closeModal);
    var create = $("studio-modal-new-playlist");
    if (create) create.addEventListener("click", function () { openCreatePlaylist(mediaId); });
    var body = $("studio-modal-body");
    if (body) body.onclick = function (event) {
      var button = event.target.closest("[data-add-playlist]");
      if (!button) return;
      button.disabled = true;
      button.textContent = "Adding…";
      addMediaToPlaylist(button.getAttribute("data-add-playlist"), button.getAttribute("data-media-id"), false)
        .then(function (added) {
          button.disabled = !added;
          button.textContent = added ? "Added" : "Retry";
        });
    };
  }

  function openPlaylist(playlistId) {
    var playlist = find(S.playlists, playlistId);
    if (!playlist) return;
    var items = (playlist.items || []).map(function (entry) {
      var media = entry.media || {};
      var src = mediaLink(media.id, null);
      var thumb = media.kind === "image"
        ? '<img src="' + esc(src) + '" alt="" loading="lazy">'
        : '<video src="' + esc(src) + '" muted preload="metadata" playsinline></video>';
      return '<div class="studio-modal-row">' +
        '<button type="button" class="studio-modal-thumb" data-playlist-media="' + esc(media.id) + '">' + thumb + '</button>' +
        '<div class="studio-modal-row-body">' +
          '<div class="studio-modal-row-title">' + esc(media.title || media.filename || media.id) + '</div>' +
          '<div class="studio-modal-row-meta">' + esc(media.kind || "media") + ' · ' + esc(SS.humanBytes(media.size)) + '</div>' +
        '</div>' +
        '<button type="button" class="studio-mini-btn studio-mini-danger" data-playlist-remove="' + esc(media.id) + '" title="Remove from playlist"><i data-lucide="x" class="w-3 h-3"></i></button>' +
      '</div>';
    }).join("");
    openModal(playlist.name,
      (items ? '<div class="studio-modal-list">' + items + '</div>' : '<div class="studio-empty-state">This playlist is empty. Add media from the library.</div>'),
      '<button type="button" class="studio-btn studio-btn-ghost" data-modal-cancel>Close</button>' +
      '<button type="button" class="studio-btn" id="studio-playlist-rename">Rename</button>' +
      '<button type="button" class="studio-btn studio-btn-danger" id="studio-playlist-delete">Delete</button>');
    var cancel = document.querySelector("[data-modal-cancel]");
    if (cancel) cancel.addEventListener("click", closeModal);
    var rename = $("studio-playlist-rename");
    if (rename) rename.addEventListener("click", function () { openRenamePlaylist(playlist); });
    var remove = $("studio-playlist-delete");
    if (remove) remove.addEventListener("click", function () { deletePlaylist(playlist); });
    var body = $("studio-modal-body");
    if (body) body.onclick = function (event) {
      var pick = event.target.closest("[data-playlist-media]");
      if (pick) {
        var media = mediaItem(pick.getAttribute("data-playlist-media"));
        if (media) { closeModal(); selectMedia(media); setTab("activity"); }
        return;
      }
      var removeItem = event.target.closest("[data-playlist-remove]");
      if (removeItem) {
        api("/api/studio/playlists/" + encodeURIComponent(playlist.id) + "/items/" +
          encodeURIComponent(removeItem.getAttribute("data-playlist-remove")), { method: "DELETE" })
          .then(function () { return loadPlaylists(true); })
          .then(function () { openPlaylist(playlist.id); })
          .catch(function (err) { toast(err.message); });
      }
    };
  }

  function openRenamePlaylist(playlist) {
    openModal("Rename playlist",
      '<label class="studio-field"><span>Playlist name</span><input id="studio-modal-name" type="text" maxlength="80" value="' + esc(playlist.name) + '"></label>' +
      '<p class="studio-modal-error" data-modal-error></p>',
      '<button type="button" class="studio-btn studio-btn-ghost" data-modal-cancel>Cancel</button>' +
      '<button type="button" class="studio-btn" id="studio-modal-confirm">Save</button>');
    var cancel = document.querySelector("[data-modal-cancel]");
    if (cancel) cancel.addEventListener("click", closeModal);
    var confirm = $("studio-modal-confirm");
    if (confirm) confirm.addEventListener("click", function () {
      var input = $("studio-modal-name");
      var name = input ? input.value.trim() : "";
      if (!name) { modalError("Enter a playlist name."); return; }
      busy(confirm, true, "Saving…");
      api("/api/studio/playlists/" + encodeURIComponent(playlist.id), { method: "POST", body: { name: name } })
        .then(function () { closeModal(); toast("Playlist renamed"); return loadPlaylists(true); })
        .catch(function (err) { modalError(err.message); busy(confirm, false, "Save"); });
    });
  }

  function deletePlaylist(playlist) {
    if (!window.confirm("Delete playlist \"" + playlist.name + "\"? Media files are not deleted.")) return;
    api("/api/studio/playlists/" + encodeURIComponent(playlist.id), { method: "DELETE" })
      .then(function () { closeModal(); toast("Playlist deleted"); return loadPlaylists(true); })
      .catch(function (err) { toast(err.message); });
  }

  function openSaveTemplate() {
    if (!S.detail || !S.modelId) { toast("Choose a model first."); return; }
    var params;
    try { params = SS.buildParams(S.fields, S.values); } catch (err) { toast(err.message); return; }
    var missing = SS.missingRequired(S.fields, S.values);
    if (missing.length) { toast("Fill required fields first: " + missing.join(", ")); return; }
    var defaultName = (S.detail.label || S.modelId) +
      (params.prompt ? " · " + String(params.prompt).slice(0, 38) : "");
    openModal("Save generation template",
      '<label class="studio-field"><span>Template name</span><input id="studio-modal-name" type="text" maxlength="80" value="' + esc(defaultName) + '"></label>' +
      '<p class="studio-modal-copy">Saves the selected model and all current parameters locally. Provider credentials are never stored in templates.</p>' +
      '<p class="studio-modal-error" data-modal-error></p>',
      '<button type="button" class="studio-btn studio-btn-ghost" data-modal-cancel>Cancel</button>' +
      '<button type="button" class="studio-btn" id="studio-modal-confirm">Save template</button>');
    var cancel = document.querySelector("[data-modal-cancel]");
    if (cancel) cancel.addEventListener("click", closeModal);
    var confirm = $("studio-modal-confirm");
    if (confirm) confirm.addEventListener("click", function () {
      var input = $("studio-modal-name");
      var name = input ? input.value.trim() : "";
      if (!name) { modalError("Enter a template name."); return; }
      busy(confirm, true, "Saving…");
      api("/api/studio/templates", {
        method: "POST",
        body: { name: name, provider: S.providerId, model_id: S.modelId, params: params }
      }).then(function () {
        closeModal();
        toast("Template saved");
        return loadTemplates(true);
      }).catch(function (err) { modalError(err.message); busy(confirm, false, "Save template"); });
    });
  }

  function applyTemplate(template) {
    if (!template) return;
    closeModal();
    setTab("activity");
    var ensureProvider = template.provider === S.providerId
      ? Promise.resolve()
      : chooseProvider(template.provider);
    ensureProvider.then(function () {
      if (S.modelId !== template.model_id) {
        S.modelId = template.model_id;
        S.detail = null;
        S.fields = [];
        return loadModelDetail(template.model_id);
      }
      return null;
    }).then(function () {
      if (!S.detail) throw new Error("The template's model is not available in the pinned catalog.");
      var restored = SS.valuesFromParams(S.fields, template.params || {});
      S.values = restored.values;
      S.inputMode = SS.inputModeForModel(S.detail);
      renderInputModes();
      renderModels();
      renderPrompt();
      renderParams();
      updateGenerateState();
      renderModelCard();
      if (restored.ignored.length) {
        toast("Ignored fields no longer documented by this model: " + restored.ignored.join(", "));
      } else {
        toast("Template applied");
      }
    }).catch(function (err) { toast(err.message); });
  }

  function deleteTemplate(template) {
    if (!window.confirm("Delete template \"" + template.name + "\"?")) return;
    api("/api/studio/templates/" + encodeURIComponent(template.id), { method: "DELETE" })
      .then(function () { toast("Template deleted"); return loadTemplates(true); })
      .catch(function (err) { toast(err.message); });
  }

  function openTemplatePicker() {
    var rows = S.templates.map(function (template) {
      return '<button type="button" class="studio-modal-row" data-template-apply="' + esc(template.id) + '">' +
        '<span class="studio-template-icon"><i data-lucide="layout-template" class="w-3.5 h-3.5"></i></span>' +
        '<span class="studio-modal-row-body"><span class="studio-modal-row-title">' + esc(template.name) + '</span>' +
        '<span class="studio-modal-row-meta">' + esc(template.model_label || template.model_id) + '</span></span>' +
      '</button>';
    }).join("");
    openModal("Generate with a template",
      rows ? '<div class="studio-modal-list">' + rows + '</div>'
        : '<div class="studio-empty-state">No templates yet. Configure a generation, then use Save current.</div>',
      '<button type="button" class="studio-btn studio-btn-ghost" data-modal-cancel>Close</button>');
    var cancel = document.querySelector("[data-modal-cancel]");
    if (cancel) cancel.addEventListener("click", closeModal);
    var body = $("studio-modal-body");
    if (body) body.onclick = function (event) {
      var button = event.target.closest("[data-template-apply]");
      if (!button) return;
      var template = find(S.templates, button.getAttribute("data-template-apply"));
      if (template) applyTemplate(template);
    };
  }

  function openHistory() {
    var rows = S.jobs.map(function (job) {
      var output = SS.jobOutput(job);
      var kind = (output && output.kind) || job.kind || "video";
      var src = output ? mediaLink(output.media_id, output.url) : "";
      var thumb = src
        ? (kind === "image" ? '<img src="' + esc(src) + '" alt="" loading="lazy">'
          : '<video src="' + esc(src) + '" muted preload="metadata" playsinline></video>')
        : '<i data-lucide="film" class="w-4 h-4 text-slate-500"></i>';
      return '<div class="studio-modal-row">' +
        '<button type="button" class="studio-modal-thumb" data-history-job="' + esc(job.id) + '">' + thumb + '</button>' +
        '<div class="studio-modal-row-body">' +
          '<div class="studio-modal-row-title">' + esc(job.prompt || SS.jobTitle(job)) + '</div>' +
          '<div class="studio-modal-row-meta">' + esc(SS.stateLabel(job.state)) + ' · ' + esc(SS.relativeTime(job.age_seconds)) + '</div>' +
        '</div>' +
        (job.can_cancel ? '<button type="button" class="studio-mini-btn" data-history-action="cancel" data-job-id="' + esc(job.id) + '"><i data-lucide="x" class="w-3 h-3"></i></button>' : '') +
        (job.terminal ? '<button type="button" class="studio-mini-btn studio-mini-danger" data-history-action="delete" data-job-id="' + esc(job.id) + '"><i data-lucide="trash-2" class="w-3 h-3"></i></button>' : '') +
      '</div>';
    }).join("");
    openModal("Generation history",
      rows ? '<div class="studio-modal-list">' + rows + '</div>'
        : '<div class="studio-empty-state">No generations yet.</div>',
      '<button type="button" class="studio-btn studio-btn-ghost" data-modal-cancel>Close</button>');
    var cancel = document.querySelector("[data-modal-cancel]");
    if (cancel) cancel.addEventListener("click", closeModal);
    var body = $("studio-modal-body");
    if (body) body.onclick = function (event) {
      var action = event.target.closest("[data-history-action]");
      if (action) {
        jobAction(action.getAttribute("data-history-action"), action.getAttribute("data-job-id"));
        return;
      }
      var jobBtn = event.target.closest("[data-history-job]");
      if (jobBtn) { closeModal(); selectJob(jobBtn.getAttribute("data-history-job")); setTab("activity"); }
    };
  }

  /* ------------------------------------------------------------------ */
  /* Setup                                                               */
  /* ------------------------------------------------------------------ */
  /* ------------------------------------------------------------------ */
  /* Provider registry (Provider Setup)                                  */
  /* ------------------------------------------------------------------ */
  function renderRegistry() {
    var host = $("studio-registry");
    if (!host) return;
    api("/api/studio/registry").then(function (data) {
      var providers = (data && data.providers) || [];
      if (!providers.length) {
        host.innerHTML = '<p class="studio-setup-status warn">No providers registered.</p>';
        return;
      }
      host.innerHTML = providers.map(function (provider) {
        var statusClass = provider.connected ? "ok" : (provider.fields.length ? "warn" : "ok");
        var statusText = provider.connected
          ? "Connected"
          : (provider.fields.length ? "Not connected" : "Always available");
        var fieldsForm = "";
        if (provider.fields.length) {
          fieldsForm = '<div class="studio-registry-form' + (S.registryOpen === provider.id ? "" : " hidden") + '" data-registry-form="' + esc(provider.id) + '">' +
            provider.fields.map(function (field) {
              return '<label class="studio-field"><span>' + esc(field.label) + '</span>' +
                '<input type="password" autocomplete="off" spellcheck="false" data-registry-field="' + esc(provider.id) + ":" + esc(field.id) + '" placeholder="' + esc(provider.env.length ? "or " + provider.env.join(" / ") : "") + '"></label>';
            }).join("") +
            '<div class="studio-setup-actions">' +
            '<button type="button" class="studio-btn" data-registry-save="' + esc(provider.id) + '">Save</button>' +
            '<button type="button" class="studio-btn studio-btn-ghost" data-registry-test="' + esc(provider.id) + '">Test</button>' +
            '<button type="button" class="studio-btn studio-btn-danger" data-registry-delete="' + esc(provider.id) + '">Remove</button>' +
            '</div>' +
            '<p class="studio-setup-message" data-registry-message="' + esc(provider.id) + '"></p>' +
            '</div>';
        }
        return '<div class="studio-registry-row' + (provider.connected ? " connected" : "") + '">' +
          '<div class="studio-registry-head">' +
          '<span class="studio-dot ' + (provider.connected ? "on" : "off") + '"></span>' +
          '<div class="flex-1 min-w-0">' +
          '<b>' + esc(provider.label) + '</b>' +
          '<span class="studio-registry-note">' + esc(provider.note || "") + '</span>' +
          '</div>' +
          '<span class="studio-registry-status ' + statusClass + '">' + statusText + '</span>' +
          (fieldsForm ? '<button type="button" class="studio-btn studio-btn-ghost" data-registry-toggle="' + esc(provider.id) + '">' +
            (S.registryOpen === provider.id ? "Close" : (provider.connected ? "Edit" : "Connect")) + '</button>' : "") +
          '</div>' +
          fieldsForm +
          '</div>';
      }).join("");
      icons();
    }).catch(function (err) {
      host.innerHTML = '<p class="studio-setup-status warn">' + esc(err.message) + '</p>';
    });
  }

  function registryMessage(providerId, text, ok) {
    var el = document.querySelector('[data-registry-message="' + providerId + '"]');
    if (!el) return;
    el.textContent = text || "";
    el.className = "studio-setup-message" + (ok ? " ok" : (text ? " warn" : ""));
  }

  function registrySave(providerId) {
    var values = {};
    Array.prototype.forEach.call(
      document.querySelectorAll('[data-registry-field^="' + providerId + ':"]'), function (input) {
        var field = input.getAttribute("data-registry-field").split(":")[1];
        if (input.value.trim()) values[field] = input.value.trim();
      });
    if (!Object.keys(values).length) {
      registryMessage(providerId, "Enter at least one credential value.");
      return;
    }
    registryMessage(providerId, "Saving…");
    api("/api/studio/registry/credentials", {
      method: "POST", body: { provider: providerId, values: values }
    }).then(function (data) {
      Array.prototype.forEach.call(
        document.querySelectorAll('[data-registry-field^="' + providerId + ':"]'), function (input) {
          input.value = "";
        });
      registryMessage(providerId, "Connected. The provider is now selectable in the step dropdowns.", true);
      if (window.StudioFashion) StudioFashion.refreshRegistry();
      renderRegistry();
    }).catch(function (err) {
      registryMessage(providerId, err.message);
    });
  }

  function registryTest(providerId) {
    registryMessage(providerId, "Testing…");
    api("/api/studio/registry/test", { method: "POST", body: { provider: providerId } })
      .then(function (data) {
        registryMessage(providerId, data.message || "Connection OK.", true);
      })
      .catch(function (err) {
        registryMessage(providerId, err.message);
      });
  }

  function registryDelete(providerId) {
    if (!window.confirm("Remove the stored credentials for this provider?")) return;
    api("/api/studio/registry/credentials/delete", { method: "POST", body: { provider: providerId } })
      .then(function () {
        registryMessage(providerId, "Credentials removed.", true);
        if (window.StudioFashion) StudioFashion.refreshRegistry();
        renderRegistry();
      })
      .catch(function (err) {
        registryMessage(providerId, err.message);
      });
  }

  function renderSetup() {
    var provider = S.provider;
    var cred = (provider && provider.credentials) || {};
    renderRegistry();

    var mcp = S.mcpConfig || {};
    var mcpProvider = find(S.providers, "higgsfield_mcp") || {};
    var mcpStatus = $("studio-mcp-status");
    if (mcpStatus) {
      if (mcpProvider.connected) {
        mcpStatus.textContent = "Connected · " + ((mcpProvider.tools || []).length) + " tools available.";
        mcpStatus.className = "studio-setup-status ok";
      } else if (mcp.configured) {
        mcpStatus.textContent = "Configured but not connected.";
        mcpStatus.className = "studio-setup-status warn";
      } else {
        mcpStatus.textContent = "Not configured. The official API remains the default.";
        mcpStatus.className = "studio-setup-status";
      }
    }
    var command = $("studio-mcp-command");
    var args = $("studio-mcp-args");
    if (command && document.activeElement !== command) command.value = mcp.command || "";
    if (args && document.activeElement !== args) args.value = (mcp.args || []).join(" ");
    var env = $("studio-mcp-env");
    if (env && document.activeElement !== env) env.value = "";
    if (env) {
      env.placeholder = (mcp.env_keys || []).length
        ? "Stored env keys: " + mcp.env_keys.join(", ") + ". Enter JSON to replace them."
        : '{"HIGGSFIELD_API_KEY":"…"}';
    }

    if (S.catalog) {
      setText("studio-catalog-meta",
        (S.catalog.catalog_version || "catalog") + " · " + (S.catalog.model_count || 0) +
        " models · fetched " + (S.catalog.fetched_at || "unknown") +
        " · sha256 " + String(S.catalog.sha256 || "").slice(0, 12) + "…");
    }
  }

  function setupMessage(id, text, ok) {
    var el = $(id);
    if (!el) return;
    el.textContent = text || "";
    el.className = "studio-setup-message" + (ok ? " ok" : (text ? " warn" : ""));
  }

  function saveMcp() {
    var command = $("studio-mcp-command");
    var args = $("studio-mcp-args");
    var env = $("studio-mcp-env");
    if (!command || !args || !env) return;
    var parsedArgs;
    var parsedEnv;
    try {
      parsedArgs = SS.parseArgs(args.value);
      parsedEnv = SS.parseEnv(env.value);
    } catch (err) {
      setupMessage("studio-mcp-message", err.message);
      return;
    }
    var button = $("studio-mcp-save");
    busy(button, true);
    setupMessage("studio-mcp-message", "Saving…");
    api("/api/studio/mcp/config", {
      method: "POST",
      body: { command: command.value.trim(), args: parsedArgs, env: parsedEnv }
    }).then(function () {
      setupMessage("studio-mcp-message", "MCP configuration saved. Connect to start the local server.", true);
      env.value = "";
      return loadProviders();
    }).catch(function (err) {
      setupMessage("studio-mcp-message", err.message);
    }).then(function () { busy(button, false); });
  }

  function connectMcp() {
    var button = $("studio-mcp-connect");
    busy(button, true);
    setupMessage("studio-mcp-message", "Starting the MCP server…");
    api("/api/studio/mcp/connect", { method: "POST", body: {} })
      .then(function () {
        setupMessage("studio-mcp-message", "Connected.", true);
        return loadProviders();
      })
      .catch(function (err) { setupMessage("studio-mcp-message", err.message); })
      .then(function () { busy(button, false); });
  }

  function disconnectMcp() {
    var button = $("studio-mcp-disconnect");
    busy(button, true);
    api("/api/studio/mcp/disconnect", { method: "POST", body: {} })
      .then(function () {
        setupMessage("studio-mcp-message", "Disconnected.", true);
        return loadProviders();
      })
      .catch(function (err) { setupMessage("studio-mcp-message", err.message); })
      .then(function () { busy(button, false); });
  }

  /* ------------------------------------------------------------------ */
  /* Header, tabs and generation                                         */
  /* ------------------------------------------------------------------ */
  function updateHeader() {
    var active = S.jobs.filter(function (job) { return SS.isActive(job); })[0];
    var headerReady;
    if (S.generateBusy) setText("studio-status-text", "Submitting…");
    else if (active) setText("studio-status-text", SS.stateLabel(active.state) + " · " + SS.jobTitle(active));
    else if (S.provider && S.provider.configured) setText("studio-status-text", "Connected");
    else setText("studio-status-text", "Higgsfield setup needed");
    headerReady = !!(S.provider && S.provider.configured);
    var pill = $("studio-status");
    if (pill) {
      pill.classList.toggle("studio-pill-warn", !headerReady);
      pill.classList.toggle("studio-pill-live", !!active || S.generateBusy);
    }
    renderProviders();
  }

  function setTab(tab) {
    var higgsfieldTabs = ["activity", "library", "playlists", "templates", "history"];
    if (higgsfieldTabs.indexOf(tab) !== -1) {
      S.higgsfieldTab = tab;
      tab = "higgsfield";
    }
    if (["higgsfield", "fashion", "setup", "assets", "preset"].indexOf(tab) === -1) tab = "fashion";
    S.tab = tab;
    updateHeader();
    Array.prototype.forEach.call(document.querySelectorAll("[data-studio-tab]"), function (button) {
      button.classList.toggle("is-active", button.getAttribute("data-studio-tab") === tab);
    });
    Array.prototype.forEach.call(document.querySelectorAll("[data-studio-subtab]"), function (button) {
      button.classList.toggle("is-active", button.getAttribute("data-studio-subtab") === S.higgsfieldTab);
    });
    var inHiggsfield = tab === "higgsfield";
    var view = $("studio-view");
    if (view) view.setAttribute("data-tab", tab);
    show($("collart-stepper"), tab === "fashion");
    show($("collart-dock"), tab === "fashion");
    show($("studio-higgsfield-subtabs"), false);
    show($("studio-panel-activity"), inHiggsfield && S.higgsfieldTab === "activity");
    show($("studio-panel-library"), inHiggsfield && S.higgsfieldTab === "library");
    show($("studio-panel-playlists"), inHiggsfield && S.higgsfieldTab === "playlists");
    show($("studio-panel-templates"), inHiggsfield && S.higgsfieldTab === "templates");
    show($("studio-panel-history"), inHiggsfield && S.higgsfieldTab === "history");
    show($("studio-panel-fashion"), tab === "fashion");
    show($("studio-panel-assets"), tab === "assets");
    show($("studio-panel-preset"), tab === "preset");
    show($("studio-panel-setup"), tab === "setup");
    var settings = $("studio-settings-btn");
    if (settings) settings.classList.toggle("active", tab === "setup");
    if (inHiggsfield && S.higgsfieldTab === "library") {
      loadLibrary(true);
      loadPlaylists(true);
      loadTemplates(true);
    }
    if (inHiggsfield && S.higgsfieldTab === "playlists") loadPlaylists(true);
    if (inHiggsfield && S.higgsfieldTab === "templates") loadTemplates(true);
    if (inHiggsfield && S.higgsfieldTab === "history") loadJobs(true);
    if (tab === "fashion" && window.StudioFashion && window.StudioFashion.show) {
      window.StudioFashion.show();
    }
    if (tab === "assets" && window.StudioFashion && window.StudioFashion.showAssets) {
      window.StudioFashion.showAssets();
    }
    if (tab === "preset" && window.StudioFashion && window.StudioFashion.showPresetPage) {
      window.StudioFashion.showPresetPage();
    }
    if (tab === "setup") renderSetup();
  }

  function providerSafeUrl(url) {
    return /^https?:\/\//i.test(String(url || ""));
  }

  function setFieldUrl(name, url) {
    var field = fieldByName(name);
    if (!field || !url) return false;
    if (field.control === "url_list") {
      var current = S.values[name];
      var list = Array.isArray(current) ? current.slice() : [];
      var max = field.maxItems ? Number(field.maxItems) : 0;
      if (list.indexOf(url) !== -1) return false;
      if (max && list.length >= max) return false;
      list.push(url);
      S.values[name] = list;
      return true;
    }
    if (S.values[name]) return false;
    S.values[name] = url;
    return true;
  }

  function orderUrlFields(names, preferred) {
    var ordered = [];
    (preferred || []).forEach(function (name) {
      if (names.indexOf(name) !== -1 && ordered.indexOf(name) === -1) ordered.push(name);
    });
    names.forEach(function (name) {
      if (ordered.indexOf(name) === -1) ordered.push(name);
    });
    return ordered;
  }

  function injectVideoKitAssets() {
    var summary = (S.detail && S.detail.summary) || {};
    var imageFields = orderUrlFields(
      (summary.image_fields || []).filter(fieldByName),
      ["image_url", "image_urls", "input_images", "reference_image_url",
       "first_frame_url", "character_image_url", "end_image_url"]
    );
    var videoFields = orderUrlFields(
      (summary.video_fields || []).filter(fieldByName),
      ["video_url", "video_urls", "reference_video_url"]
    );
    var identityFields = orderUrlFields(
      imageFields,
      ["character_image_url", "reference_image_url", "image_urls", "image_url"]
    );
    if (!imageFields.length && !videoFields.length) return [];

    var attached = [];
    var ref = S.videoKit.ref || {};
    var refUrl = ref.url || "";
    if (refUrl && providerSafeUrl(refUrl)) {
      var kind = videoKitMediaKind(ref);
      if (kind === "video") {
        for (var v = 0; v < videoFields.length; v += 1) {
          if (setFieldUrl(videoFields[v], refUrl)) {
            attached.push(videoFields[v]);
            break;
          }
        }
      } else {
        for (var i = 0; i < imageFields.length; i += 1) {
          if (setFieldUrl(imageFields[i], refUrl)) {
            attached.push(imageFields[i]);
            break;
          }
        }
      }
    }

    // Identity and product references are optional extras: only attach remote
    // (provider-fetchable) URLs, never local Vault preview URLs.
    ["avatar", "product"].forEach(function (key) {
      var item = S.videoKit[key] || {};
      if (!item.url || !providerSafeUrl(item.url)) return;
      if (videoKitMediaKind(item) !== "image") return;
      for (var j = 0; j < identityFields.length; j += 1) {
        if (setFieldUrl(identityFields[j], item.url)) {
          attached.push(identityFields[j]);
          break;
        }
      }
    });

    if (attached.length) {
      renderParams();
      toast("Video Kit assets attached: " + attached.join(", "));
    }
    return attached;
  }

  function syncVideoKitPrompt() {
    if (!S.videoKit.prompt) return;
    var promptField = fieldByName("prompt");
    if (!promptField) return;
    var current = S.values.prompt;
    if (current === undefined || current === null || !String(current).trim() ||
        String(current) === S.videoKit.prompt) {
      S.values.prompt = S.videoKit.prompt;
      var area = $("studio-prompt");
      if (area && area.value !== S.videoKit.prompt) area.value = S.videoKit.prompt;
      updatePromptHint();
    }
  }

  function ensureVideoKitForGeneration() {
    if (!hasVideoKitReference() || S.videoKit.prompt) return Promise.resolve(null);
    var summary = (S.detail && S.detail.summary) || {};
    var accepts = summary.accepts || {};
    var ref = S.videoKit.ref || {};
    var refKind = videoKitMediaKind(ref);
    // For compatible image/video models the kit assets are attached directly.
    // For pure text-to-video models, a reference kind the model cannot consume,
    // or a local/attached file without a provider-fetchable URL, first turn the
    // reference into a prompt with the eyes & ears pass.
    if ((accepts.image || accepts.video) && providerSafeUrl(ref.url)) {
      var compatible = (refKind === "video" && accepts.video) ||
        (refKind === "image" && accepts.image);
      if (compatible) return Promise.resolve(null);
    }
    toast("Analyzing Video Kit reference into a generation prompt…");
    return analyzeVideoKit({ silent: true });
  }

  function generate() {
    if (S.generateBusy) return;
    if (!S.provider || !S.provider.configured) {
      toast("Configure the provider in Setup first");
      setTab("setup");
      return;
    }
    if (!S.detail || !S.modelId) { toast("Choose a model first"); return; }

    S.generateBusy = true;
    updateGenerateState();
    updateHeader();
    var button = $("studio-generate");
    busy(button, true, "Preparing…");

    return ensureVideoKitForGeneration().then(function () {
      syncVideoKitPrompt();
      injectVideoKitAssets();

      var params;
      try {
        params = SS.buildParams(S.fields, S.values);
      } catch (err) {
        toast(err.message);
        return null;
      }
      var missing = SS.missingRequired(S.fields, S.values);
      if (missing.length) {
        toast("Required: " + missing.join(", "));
        var prompt = $("studio-prompt");
        if (missing.indexOf("prompt") !== -1 && prompt) prompt.focus();
        return null;
      }

      busy(button, true, "Submitting…");
      return api("/api/studio/jobs", {
        method: "POST",
        body: {
          provider: S.provider.id,
          model_id: S.modelId,
          params: params,
          agent_id: S.agentId || undefined,
          auto_download: !!($("studio-auto-download") && $("studio-auto-download").checked)
        }
      }).then(function (data) {
        var job = data.job;
        S.selectedMedia = null;
        S.jobId = job.id;
        S.selectedJob = job;
        S.selectedVariant = 0;
        S.jobSyncedAt = Date.now();
        S.stageKey = "";
        renderStage();
        setTab("activity");
        if (data.duplicate) toast("This request is already active; reusing the existing job.");
        else toast("Job submitted");
        return loadJobs(true);
      });
    }).catch(function (err) {
      toast(err.message);
    }).then(function () {
      S.generateBusy = false;
      busy(button, false, "Generate");
      updateGenerateState();
      updateHeader();
    });
  }

  function refreshAll() {
    return loadToken().then(function () {
      return Promise.all([loadProviders(), loadAgents()]);
    }).then(function () {
      return Promise.all([
        loadVaultAvatars(true),
        loadJobs(true),
        loadLibrary(true),
        loadPlaylists(true),
        loadTemplates(true),
        loadVideoTemplates(true)
      ]);
    }).then(function () {
      if (window.StudioFashion && window.StudioFashion.refresh) {
        window.StudioFashion.refresh();
      }
    });
  }

  /* ------------------------------------------------------------------ */
  /* Wiring                                                              */
  /* ------------------------------------------------------------------ */
  function closeMenus(except) {
    ["studio-agent-menu", "studio-provider-menu", "studio-model-menu"].forEach(function (id) {
      if (id === except) return;
      show($(id), false);
    });
  }

  function toggleMenu(menuId) {
    var menu = $(menuId);
    if (!menu) return;
    var open = menu.classList.contains("hidden");
    closeMenus(menuId);
    show(menu, open);
  }

  /* ------------------------------------------------------------------ */
  /* Video Kit (3 Panels) & Video Reverse Engineering Skill Integration */
  /* ------------------------------------------------------------------ */
  function hasVideoKitReference() {
    var ref = S.videoKit.ref || {};
    return !!(ref.url || ref.name || ref.file || ref.data_base64);
  }

  function vaultAssetLink(url) {
    if (!url || url.indexOf("/api/vault/asset") !== 0) return url;
    if (!S.token || url.indexOf("token=") !== -1) return url;
    return url + (url.indexOf("?") === -1 ? "?" : "&") +
      "token=" + encodeURIComponent(S.token);
  }

  function loadVaultAvatars(force) {
    if (S.avatarsLoaded && !force) {
      renderVaultAvatarNames();
      return Promise.resolve();
    }
    return api("/api/studio/video-kit/avatars").then(function (data) {
      S.avatars = SS.normalizeList(data, "avatars");
      S.avatarsLoaded = true;
      renderVaultAvatarNames();
      return null;
    }).catch(function () {
      S.avatarsLoaded = false;
      return null;
    });
  }

  function renderVaultAvatarNames() {
    var host = $("studio-vk-avatar-names");
    if (!host) return;
    host.innerHTML = (S.avatars || []).map(function (avatar) {
      return '<option value="' + esc(avatar.name || "") + '" label="' +
        esc(avatar.url ? "Vault avatar · image ready" : "Vault name · no image") + '"></option>';
    }).join("");
  }

  function uploadVaultAvatar(match) {
    if (!match || !match.url || !S.provider || !S.provider.configured) {
      return Promise.resolve(null);
    }
    var assetUrl = vaultAssetLink(match.url);
    var filename = match.name + (match.path && /\.[a-z0-9]+$/i.test(match.path)
      ? match.path.slice(match.path.lastIndexOf(".")) : ".png");
    return fetch(assetUrl, { credentials: "same-origin" })
      .then(function (response) {
        if (!response.ok) throw new Error("Could not read the Vault image.");
        return response.blob();
      })
      .then(function (blob) {
        return new Promise(function (resolve, reject) {
          var reader = new FileReader();
          reader.onload = function () {
            var text = String(reader.result || "");
            resolve({
              data: text.indexOf(",") === -1 ? text : text.slice(text.indexOf(",") + 1),
              type: blob.type || "image/png"
            });
          };
          reader.onerror = function () { reject(new Error("Could not read the Vault image.")); };
          reader.readAsDataURL(blob);
        });
      })
      .then(function (file) {
        return api("/api/studio/uploads", {
          method: "POST",
          body: {
            provider: S.provider.id,
            filename: filename,
            content_type: file.type,
            data_base64: file.data
          }
        });
      })
      .then(function (res) {
        if (res && res.public_url) {
          match.uploaded_url = res.public_url;
          match.uploaded_media_id = (res.media && res.media.id) || null;
          var current = S.videoKit.avatar || {};
          if (String(current.name || "").toLowerCase() ===
              String(match.name || "").toLowerCase()) {
            S.videoKit.avatar.url = res.public_url;
            S.videoKit.avatar.media_id = match.uploaded_media_id;
            S.videoKit.avatar.content_type = "image/*";
            renderVideoKitPanel("avatar");
          }
        }
        return res;
      })
      .catch(function () {
        // Keep the local Vault preview/analysis path if the provider upload
        // is unavailable; Gemini can still read the Vault file server-side.
        return null;
      });
  }

  function resolveVaultAvatar(name, silent) {
    var wanted = String(name || "").trim().toLowerCase();
    if (!wanted) return null;
    var match = null;
    for (var i = 0; i < (S.avatars || []).length; i += 1) {
      if (String(S.avatars[i].name || "").toLowerCase() === wanted) {
        match = S.avatars[i];
        break;
      }
    }
    if (!match) {
      if (!silent) toast('No Vault avatar named "' + name + '"');
      return null;
    }
    S.videoKit.avatar = {
      url: match.uploaded_url || (match.url ? vaultAssetLink(match.url) : ""),
      name: match.name || name,
      file: null,
      data_base64: "",
      content_type: "image/*",
      media_id: match.uploaded_media_id || null,
      vault_path: match.path || ""
    };
    var urlInput = $("studio-vk-avatar-url");
    if (urlInput) urlInput.value = S.videoKit.avatar.url || "";
    renderVideoKitPanel("avatar");
    if (!silent) {
      toast(match.url
        ? 'Avatar "' + match.name + '" loaded from the Vault'
        : 'Avatar "' + match.name + '" found (no image attached in the Vault)');
    }
    if (match.url && !match.uploaded_url) uploadVaultAvatar(match);
    return match;
  }

  function updateVideoKitModelHint() {
    var el = $("studio-vk-model-hint");
    if (!el) return;
    var model = S.detail || find(S.models, S.modelId);
    var summary = (model && model.summary) || {};
    var accepts = summary.accepts || {};
    var bits = [];
    if (accepts.video) bits.push("video");
    if (accepts.image) bits.push("image");
    var text;
    el.classList.remove("uses-prompt", "uses-assets");
    if (!model) {
      text = "Pick a model: text-to-video uses the analyzed prompt, image/video models also receive kit assets.";
    } else if (bits.length) {
      text = "Optional for this model: " + bits.join(" + ") +
        " URL(s) from the kit are attached automatically; the analyzed prompt is used when the prompt is empty.";
      el.classList.add("uses-assets");
    } else {
      text = "Optional for this model: the Video Kit analysis becomes the text-to-video prompt automatically.";
      el.classList.add("uses-prompt");
    }
    if (S.videoKit.prompt) text += " Analysis ready.";
    el.textContent = text;
  }

  function videoTemplate(id) {
    var list = S.videoKit.templates || [];
    for (var i = 0; i < list.length; i += 1) {
      if (list[i].id === id) return list[i];
    }
    return null;
  }

  function updateVideoKitChatCount() {
    var el = $("studio-vk-chat-input");
    var count = $("studio-vk-chat-count");
    if (!el) return;
    var field = promptField();
    var max = field && field.maxLength ? Number(field.maxLength) : 2000;
    if (count) count.textContent = el.value.length + " / " + max;
  }

  function pushChatToPrompt(text, previous, force) {
    var promptArea = $("studio-prompt");
    if (!promptArea || !String(text || "").trim()) return;
    var next = String(text);
    var current = promptArea.value;
    // Do not overwrite a prompt the user wrote independently; a selected
    // template (force) or an explicit "Use in Prompt" always wins.
    if (!force && current.trim() && current !== (previous || "")) return;
    promptArea.value = next;
    S.values.prompt = next;
    S.preset = "";
    Array.prototype.forEach.call(document.querySelectorAll("[data-preset]"), function (button) {
      button.classList.remove("active");
    });
    updatePromptHint();
    updateGenerateState();
  }

  function setVideoKitChat(text, options) {
    var opts = options || {};
    var input = $("studio-vk-chat-input");
    var previous = S.videoKit.chat || "";
    var next = text == null ? "" : String(text);
    if (input) input.value = next;
    S.videoKit.chat = next;
    updateVideoKitChatCount();
    if (opts.apply) pushChatToPrompt(next, previous, true);
    else if (opts.sync) pushChatToPrompt(next, previous, false);
  }

  function updateVideoKitTemplateState() {
    var select = $("studio-vk-template");
    var note = $("studio-vk-chat-note");
    var pill = $("studio-vk-chat-template-pill");
    var template = videoTemplate(S.videoKit.templateId);
    var hasRef = hasVideoKitReference();
    if (hasRef && S.videoKit.templateId) {
      S.videoKit.templateId = "";
      template = null;
    }
    if (select) {
      select.value = S.videoKit.templateId || "";
      select.disabled = !!(S.videoKit.templatesLoading || hasRef || !(S.videoKit.templates || []).length);
    }
    if (pill) {
      if (template) {
        pill.textContent = template.title || template.name;
        pill.classList.remove("hidden");
      } else {
        pill.textContent = "";
        pill.classList.add("hidden");
      }
    }
    if (note) {
      if (S.videoKit.templatesLoading) note.textContent = "Loading Vault templates…";
      else if (hasRef) note.textContent = "Reference loaded — remove it to use a template.";
      else if (!(S.videoKit.templates || []).length) note.textContent = "No video-generation skills in the Vault yet.";
      else note.textContent = "Chat with the kit, or load a video-generation template.";
    }
  }

  function renderVideoKitTemplates() {
    var select = $("studio-vk-template");
    if (!select) return;
    var templates = S.videoKit.templates || [];
    var html = '<option value="">None</option>';
    html += templates.map(function (template) {
      var label = template.title || template.name || template.id;
      return '<option value="' + esc(template.id) + '">' + esc(label) + "</option>";
    }).join("");
    select.innerHTML = html;
    if (!videoTemplate(S.videoKit.templateId)) S.videoKit.templateId = "";
    updateVideoKitTemplateState();
    icons();
  }

  function loadVideoTemplates(force) {
    if (S.videoKit.templatesLoading) return Promise.resolve();
    if (S.videoKit.templatesLoaded && !force) {
      renderVideoKitTemplates();
      return Promise.resolve();
    }
    S.videoKit.templatesLoading = true;
    updateVideoKitTemplateState();
    return api("/api/studio/video-kit/templates").then(function (data) {
      S.videoKit.templates = (data && data.templates) || [];
      S.videoKit.templatesLoaded = true;
    }).catch(function (err) {
      S.videoKit.templatesLoaded = false;
      toast("Could not load Vault templates: " + err.message);
    }).then(function () {
      S.videoKit.templatesLoading = false;
      renderVideoKitTemplates();
    });
  }

  function onVideoTemplateChange() {
    var select = $("studio-vk-template");
    var input = $("studio-vk-chat-input");
    if (!select) return;
    var id = select.value || "";
    if (id && hasVideoKitReference()) {
      select.value = "";
      S.videoKit.templateId = "";
      updateVideoKitTemplateState();
      toast("Remove the Video Kit reference before choosing a template.");
      return;
    }
    var previous = videoTemplate(S.videoKit.templateId);
    var template = videoTemplate(id);
    S.videoKit.templateId = template ? template.id : "";
    if (!template) {
      // Choosing "None" clears text that was loaded from the previous
      // template and left untouched; edited text is preserved.
      if (previous && input && input.value === (previous.prompt || "")) {
        setVideoKitChat("");
        var promptArea = $("studio-prompt");
        if (promptArea && promptArea.value === (previous.prompt || "")) {
          promptArea.value = "";
          S.values.prompt = "";
          updatePromptHint();
          updateGenerateState();
        }
      }
      updateVideoKitTemplateState();
      return;
    }
    setVideoKitChat(template.prompt || "", { apply: true });
    if (input) input.focus();
    updateVideoKitTemplateState();
    toast('Template "' + (template.title || template.name) + '" loaded into chat');
  }

  function applyVideoKitChat() {
    var input = $("studio-vk-chat-input");
    var text = (input ? input.value : S.videoKit.chat || "").trim();
    if (!text) {
      toast("Write something in the chat box first.");
      if (input) input.focus();
      return;
    }
    pushChatToPrompt(text, S.videoKit.chat || "", true);
    toast("Chat applied to the Studio prompt");
  }

  function videoKitMediaKind(item) {
    var type = String(item && item.content_type || "").toLowerCase();
    if (type.indexOf("video/") === 0) return "video";
    if (type.indexOf("image/") === 0) return "image";
    var ref = String((item && (item.name || item.url)) || "").toLowerCase();
    if (/\.(mp4|mov|webm|m4v|m3u8)(\?|#|$)/.test(ref)) return "video";
    if (/(youtube\.com|youtu\.be|tiktok\.com|instagram\.com|instagr\.am|twitter\.com|x\.com|facebook\.com|fb\.watch|vimeo\.com|dailymotion\.com|twitch\.tv|reddit\.com|bilibili\.com|threads\.net|kwai\.com|likee\.video|weibo\.com)/.test(ref)) {
      return "video";
    }
    return "image";
  }

  function videoKitPreviewSrc(item) {
    if (!item) return "";
    if (item.url) return vaultAssetLink(item.url);
    if (item.data_base64) {
      return "data:" + (item.content_type || "application/octet-stream") +
        ";base64," + item.data_base64;
    }
    return "";
  }

  function renderVideoKitPanel(key) {
    var item = S.videoKit[key];
    var emptyEl = $("studio-vk-" + key + "-empty");
    var prevEl = $("studio-vk-" + key + "-preview");
    var urlInput = $("studio-vk-" + key + "-url");
    if (urlInput && urlInput.value !== item.url) {
      urlInput.value = item.url || "";
    }
    if (key === "avatar") {
      var nameInput = $("studio-vk-avatar-name");
      if (nameInput && document.activeElement !== nameInput &&
          item.name && item.name !== item.url) {
        nameInput.value = item.name;
      }
    }
    if (!emptyEl || !prevEl) return;
    if (item.name || item.url) {
      show(emptyEl, false);
      show(prevEl, true);
      var label = item.name || item.url;
      var src = videoKitPreviewSrc(item);
      var kind = videoKitMediaKind(item);
      var media = "";
      if (src && kind === "video") {
        media = '<div class="studio-vk-preview-media"><video src="' + esc(src) +
          '" muted loop playsinline controls preload="metadata"></video></div>';
      } else if (src) {
        media = '<div class="studio-vk-preview-media"><img src="' + esc(src) +
          '" alt="' + esc(label) + ' preview" loading="lazy"></div>';
      }
      prevEl.classList.toggle("has-media", !!media);
      prevEl.innerHTML = media +
        '<div class="studio-vk-preview-meta">' +
          '<i data-lucide="check-circle" class="w-3.5 h-3.5 text-emerald-400 shrink-0"></i>' +
          '<span class="truncate flex-1 min-w-0 font-mono text-[9.5px]" title="' + esc(label) + '">' + esc(label) + '</span>' +
          '<button type="button" class="studio-icon-btn studio-vk-del" data-vk-del="' + key + '" title="Remove item"><i data-lucide="x" class="w-3 h-3 text-slate-400"></i></button>' +
        '</div>';
    } else {
      show(emptyEl, true);
      show(prevEl, false);
      prevEl.innerHTML = "";
      prevEl.classList.remove("has-media");
    }
    if (key === "ref") updateVideoKitTemplateState();
    icons();
  }

  function handleVideoKitFile(key, file) {
    if (!file) return;
    if (file.size > MAX_UPLOAD_BYTES) { toast(file.name + " is larger than 25 MB"); return; }
    toast("Attaching " + file.name + "…");
    fileToBase64(file).then(function (b64) {
      S.videoKit[key] = {
        name: file.name,
        url: "",
        file: file,
        data_base64: b64,
        content_type: file.type || "application/octet-stream"
      };
      if (S.provider && S.provider.configured) {
        api("/api/studio/uploads", {
          method: "POST",
          body: {
            provider: S.provider.id,
            filename: file.name,
            content_type: file.type || "",
            data_base64: b64
          }
        }).then(function (res) {
          if (res.public_url) {
            S.videoKit[key].url = res.public_url;
            S.videoKit[key].media_id = (res.media && res.media.id) || null;
          }
          renderVideoKitPanel(key);
        }).catch(function () {
          renderVideoKitPanel(key);
        });
      } else {
        renderVideoKitPanel(key);
      }
    }).catch(function (err) {
      toast("Failed to read file: " + err.message);
    });
  }

  function clearVideoKit() {
    S.videoKit = {
      ref: { url: "", name: "", file: null, data_base64: "" },
      avatar: { url: "", name: "", file: null, data_base64: "" },
      product: { url: "", name: "", file: null, data_base64: "" },
      analyzing: false,
      analysis: null,
      prompt: "",
      chat: "",
      templateId: "",
      templates: S.videoKit.templates || [],
      templatesLoaded: S.videoKit.templatesLoaded,
      templatesLoading: false
    };
    ["ref", "avatar", "product"].forEach(function (k) {
      var inp = $("studio-vk-" + k + "-url");
      if (inp) inp.value = "";
      renderVideoKitPanel(k);
    });
    var avatarName = $("studio-vk-avatar-name");
    if (avatarName) avatarName.value = "";
    var chat = $("studio-vk-chat-input");
    if (chat) chat.value = "";
    updateVideoKitChatCount();
    renderVideoKitTemplates();
    var card = $("studio-vk-analysis-card");
    if (card) show(card, false);
    toast("Video Kit cleared");
  }

  function analyzeVideoKit(options) {
    var opts = options || {};
    if (S.videoKit.analyzing) return Promise.resolve(null);
    var ref = S.videoKit.ref;
    var refUrl = (ref.url || ($("studio-vk-ref-url") ? $("studio-vk-ref-url").value : "")).trim();
    if (!refUrl && !ref.name && !ref.data_base64) {
      if (!opts.silent) toast("Add a reference media URL or drop a file in Panel 1 first.");
      var input = $("studio-vk-ref-url");
      if (input && !opts.silent) input.focus();
      return Promise.resolve(null);
    }
    var avatar = S.videoKit.avatar;
    var avatarUrl = (avatar.url || ($("studio-vk-avatar-url") ? $("studio-vk-avatar-url").value : "")).trim();
    var avatarName = (($("studio-vk-avatar-name") ? $("studio-vk-avatar-name").value : "") || avatar.name || "").trim();
    var product = S.videoKit.product;
    var productUrl = (product.url || ($("studio-vk-product-url") ? $("studio-vk-product-url").value : "")).trim();

    var analyzeBtn = $("studio-vk-analyze-btn");
    busy(analyzeBtn, true, "Analyzing…");
    S.videoKit.analyzing = true;

    var currentPrompt = ($("studio-prompt") ? $("studio-prompt").value : "") || S.values.prompt || "";

    return api("/api/studio/video-kit/analyze", {
      method: "POST",
      body: {
        reference: { url: refUrl, name: ref.name || refUrl, data_base64: ref.data_base64, content_type: ref.content_type || "" },
        avatar: (avatarUrl || avatarName) ? { url: avatarUrl, name: avatarName || avatar.name || avatarUrl, data_base64: avatar.data_base64, content_type: avatar.content_type || "" } : null,
        product: (productUrl || product.name) ? { url: productUrl, name: product.name || productUrl, data_base64: product.data_base64, content_type: product.content_type || "" } : null,
        prompt: currentPrompt
      }
    }).then(function (res) {
      S.videoKit.analysis = res.analysis || "";
      S.videoKit.prompt = res.prompt || "";
      S.videoKit.platform = res.platform || "";
      S.videoKit.source = ref.name || refUrl;
      var card = $("studio-vk-analysis-card");
      var textEl = $("studio-vk-analysis-text");
      if (card && textEl) {
        textEl.textContent = res.analysis || res.prompt;
        show(card, true);
      }
      if (res.prompt) {
        var promptArea = $("studio-prompt");
        if (promptArea) {
          promptArea.value = res.prompt;
          S.values.prompt = res.prompt;
          updatePromptHint();
        }
      }
      updateVideoKitModelHint();
      if (!opts.silent) toast("Video Kit analyzed: reference recreation prompt synthesized!");
      return res;
    }).catch(function (err) {
      if (!opts.silent) toast("Analysis failed: " + err.message);
      return null;
    }).then(function (res) {
      S.videoKit.analyzing = false;
      busy(analyzeBtn, false, "");
      if (analyzeBtn) analyzeBtn.querySelector("span").textContent = "Analyze Kit";
      icons();
      return res;
    });
  }

  function videoKitSkillName() {
    var ref = S.videoKit.ref || {};
    var raw = String(ref.name || ref.url || "");
    var base = raw.split(/[?#]/)[0].split("/").pop() || "";
    base = base.replace(/\.[a-z0-9]{2,5}$/i, "").replace(/[-_]+/g, " ").trim();
    if (base.length > 70) base = base.slice(0, 70);
    return base ? "Recreate " + base : "Studio Reference Video";
  }

  function saveVideoSkill(options) {
    var opts = options || {};
    var prompt = String(opts.prompt || "").trim();
    if (!prompt) {
      toast("No generation prompt to save yet.");
      return Promise.resolve(null);
    }
    var button = opts.button || null;
    busy(button, true, "Saving…");
    return api("/api/studio/video-kit/save-skill", {
      method: "POST",
      body: {
        name: opts.name || "Studio Reference Video",
        prompt: prompt,
        analysis: opts.analysis || "",
        source: opts.source || "",
        platform: opts.platform || "",
        model: S.modelId || ""
      }
    }).then(function (res) {
      var skill = res.skill || {};
      toast("Saved to Vault: " + (skill.path || skill.name || "skill"));
      S.videoKit.templatesLoaded = false;
      loadVideoTemplates(true);
      return res;
    }).catch(function (err) {
      toast("Could not save to Vault: " + err.message);
      return null;
    }).then(function (res) {
      busy(button, false, "");
      if (button) {
        var span = button.querySelector("span");
        if (span && opts.restoreLabel) span.textContent = opts.restoreLabel;
      }
      return res;
    });
  }

  function bindVideoKit() {
    S.videoKit = S.videoKit || {
      ref: { url: "", name: "", file: null, data_base64: "" },
      avatar: { url: "", name: "", file: null, data_base64: "" },
      product: { url: "", name: "", file: null, data_base64: "" },
      analyzing: false,
      analysis: null,
      prompt: "",
      chat: "",
      templateId: "",
      templates: [],
      templatesLoaded: false,
      templatesLoading: false
    };

    var panels = ["ref", "avatar", "product"];
    panels.forEach(function (key) {
      var urlInput = $("studio-vk-" + key + "-url");
      if (urlInput) {
        urlInput.addEventListener("input", function (event) {
          S.videoKit[key].url = event.target.value.trim();
          if (key === "avatar") S.videoKit[key].vault_path = "";
          renderVideoKitPanel(key);
        });
      }
      if (key === "avatar") {
        var avatarName = $("studio-vk-avatar-name");
        if (avatarName) {
          avatarName.addEventListener("input", function (event) {
            var value = event.target.value.trim();
            S.videoKit.avatar.name = value;
            var exact = (S.avatars || []).filter(function (avatar) {
              return String(avatar.name || "").toLowerCase() === value.toLowerCase();
            })[0];
            if (exact) {
              resolveVaultAvatar(value, true);
            } else if (S.videoKit.avatar.vault_path) {
              S.videoKit.avatar.url = "";
              S.videoKit.avatar.vault_path = "";
              var field = $("studio-vk-avatar-url");
              if (field) field.value = "";
              renderVideoKitPanel("avatar");
            }
          });
          avatarName.addEventListener("change", function () {
            if (avatarName.value.trim()) resolveVaultAvatar(avatarName.value, false);
          });
          avatarName.addEventListener("keydown", function (event) {
            if (event.key !== "Enter") return;
            event.preventDefault();
            resolveVaultAvatar(avatarName.value, false);
          });
        }
      }
      var dropEl = $("studio-vk-" + key + "-drop");
      var fileInput = $("studio-vk-" + key + "-file");
      if (dropEl) {
        dropEl.addEventListener("dragover", function (event) {
          event.preventDefault();
          dropEl.classList.add("drag-over");
        });
        dropEl.addEventListener("dragleave", function () {
          dropEl.classList.remove("drag-over");
        });
        dropEl.addEventListener("drop", function (event) {
          event.preventDefault();
          dropEl.classList.remove("drag-over");
          if (event.dataTransfer && event.dataTransfer.files && event.dataTransfer.files[0]) {
            handleVideoKitFile(key, event.dataTransfer.files[0]);
          }
        });
        dropEl.addEventListener("click", function (event) {
          if (event.target.closest(".studio-vk-del") || event.target.closest("button.studio-vk-del")) return;
          if (event.target.closest(".studio-vk-preview-media")) return;
          if (fileInput) fileInput.click();
        });
      }
      if (fileInput) {
        fileInput.addEventListener("change", function () {
          if (fileInput.files && fileInput.files[0]) {
            handleVideoKitFile(key, fileInput.files[0]);
          }
        });
      }
    });

    Array.prototype.forEach.call(document.querySelectorAll("[data-browse]"), function (btn) {
      btn.addEventListener("click", function (event) {
        event.stopPropagation();
        var key = btn.getAttribute("data-browse");
        var inp = $("studio-vk-" + key + "-file");
        if (inp) inp.click();
      });
    });

    var vkWrap = $("studio-video-kit");
    if (vkWrap) {
      vkWrap.addEventListener("click", function (event) {
        var delBtn = event.target.closest("[data-vk-del]");
        if (delBtn) {
          event.stopPropagation();
          var key = delBtn.getAttribute("data-vk-del");
          S.videoKit[key] = { url: "", name: "", file: null, data_base64: "" };
          var inp = $("studio-vk-" + key + "-url");
          if (inp) inp.value = "";
          var finp = $("studio-vk-" + key + "-file");
          if (finp) finp.value = "";
          if (key === "avatar") {
            var nameInp = $("studio-vk-avatar-name");
            if (nameInp) nameInp.value = "";
          }
          renderVideoKitPanel(key);
        }
      });
    }

    var clearBtn = $("studio-vk-clear-btn");
    if (clearBtn) clearBtn.addEventListener("click", clearVideoKit);

    var analyzeBtn = $("studio-vk-analyze-btn");
    if (analyzeBtn) analyzeBtn.addEventListener("click", analyzeVideoKit);

    var usePromptBtn = $("studio-vk-use-prompt-btn");
    if (usePromptBtn) {
      usePromptBtn.addEventListener("click", function () {
        if (S.videoKit.prompt) {
          var promptArea = $("studio-prompt");
          if (promptArea) {
            promptArea.value = S.videoKit.prompt;
            S.values.prompt = S.videoKit.prompt;
            updatePromptHint();
            toast("Recreation prompt applied to Studio prompt");
          }
        }
      });
    }

    var saveSkillBtn = $("studio-vk-save-skill-btn");
    if (saveSkillBtn) {
      saveSkillBtn.addEventListener("click", function () {
        saveVideoSkill({
          name: videoKitSkillName(),
          prompt: S.videoKit.prompt,
          analysis: S.videoKit.analysis,
          source: S.videoKit.source || (S.videoKit.ref && (S.videoKit.ref.name || S.videoKit.ref.url)) || "",
          platform: S.videoKit.platform || "",
          button: saveSkillBtn,
          restoreLabel: "Add to Vault"
        });
      });
    }

    var chatInput = $("studio-vk-chat-input");
    if (chatInput) {
      if (S.videoKit.chat) chatInput.value = S.videoKit.chat;
      chatInput.addEventListener("input", function () {
        setVideoKitChat(chatInput.value, { sync: true });
      });
      updateVideoKitChatCount();
    }
    var templateSelect = $("studio-vk-template");
    if (templateSelect) templateSelect.addEventListener("change", onVideoTemplateChange);
    var chatApply = $("studio-vk-chat-apply");
    if (chatApply) chatApply.addEventListener("click", applyVideoKitChat);
    var chatClear = $("studio-vk-chat-clear");
    if (chatClear) {
      chatClear.addEventListener("click", function () {
        var previous = S.videoKit.chat || "";
        setVideoKitChat("");
        S.videoKit.templateId = "";
        var promptArea = $("studio-prompt");
        if (promptArea && previous && promptArea.value === previous) {
          promptArea.value = "";
          S.values.prompt = "";
          updatePromptHint();
          updateGenerateState();
        }
        if (chatInput) chatInput.focus();
        updateVideoKitTemplateState();
      });
    }
    renderVideoKitTemplates();
    loadVideoTemplates();
  }

  function bind() {
    document.addEventListener("click", function (event) {
      var menuBtn = event.target.closest("[data-agent-menu-btn]");
      if (menuBtn) {
        event.stopPropagation();
        var menu = document.getElementById(menuBtn.getAttribute("data-agent-menu-btn") === true ? "" : "") || null;
        var host = menuBtn.parentElement;
        var menuEl = host ? host.querySelector(".ws-menu") : null;
        if (menuEl) toggleMenu(menuEl.id);
        return;
      }
      var agentItem = event.target.closest("[data-agent]");
      if (agentItem && agentItem.closest(".ws-menu")) {
        closeMenus();
        chooseAgent(agentItem.getAttribute("data-agent"));
      }
    });

    var providerBtn = $("studio-provider-btn");
    if (providerBtn) providerBtn.addEventListener("click", function (event) {
      event.stopPropagation();
      toggleMenu("studio-provider-menu");
    });
    var providerOptions = $("studio-provider-options");
    if (providerOptions) providerOptions.addEventListener("click", function (event) {
      var item = event.target.closest("[data-provider]");
      if (!item) return;
      closeMenus();
      chooseProvider(item.getAttribute("data-provider"));
    });

    var modelBtn = $("studio-model-btn");
    if (modelBtn) modelBtn.addEventListener("click", function (event) {
      event.stopPropagation();
      var wasHidden = $("studio-model-menu").classList.contains("hidden");
      toggleMenu("studio-model-menu");
      if (wasHidden) {
        var search = $("studio-model-search");
        if (search) window.setTimeout(function () { search.focus(); }, 0);
      }
    });
    var modelOptions = $("studio-model-options");
    if (modelOptions) modelOptions.addEventListener("click", function (event) {
      var item = event.target.closest("[data-model]");
      if (!item) return;
      closeMenus();
      chooseModel(item.getAttribute("data-model"));
    });
    var modelSearch = $("studio-model-search");
    if (modelSearch) {
      modelSearch.addEventListener("input", function (event) {
        S.modelQuery = event.target.value || "";
        renderModels();
      });
      modelSearch.addEventListener("click", function (event) { event.stopPropagation(); });
    }
    var kindFilter = $("studio-kind-filter");
    if (kindFilter) kindFilter.addEventListener("click", function (event) {
      var button = event.target.closest("[data-kind]");
      if (!button) return;
      S.kindFilter = button.getAttribute("data-kind") || "";
      Array.prototype.forEach.call(kindFilter.querySelectorAll("[data-kind]"), function (item) {
        item.classList.toggle("active", item === button);
      });
      renderModels();
    });
    var modelRefresh = $("studio-model-refresh");
    if (modelRefresh) modelRefresh.addEventListener("click", function (event) {
      event.stopPropagation();
      S.detail = null;
      loadModels();
    });
    var search = $("studio-search");
    if (search) {
      search.addEventListener("input", function () {
        S.searchQuery = search.value || "";
        renderSearchResults();
      });
      search.addEventListener("focus", function () {
        if (S.searchQuery) renderSearchResults();
      });
      search.addEventListener("keydown", function (event) {
        if (event.key !== "Enter") return;
        var first = document.querySelector("#studio-search-results [data-search-model]");
        if (!first) return;
        event.preventDefault();
        var modelId = first.getAttribute("data-search-model");
        closeSearch();
        chooseModel(modelId);
      });
    }
    var searchResults = $("studio-search-results");
    if (searchResults) searchResults.addEventListener("click", function (event) {
      var modelButton = event.target.closest("[data-search-model]");
      if (modelButton) {
        var modelId = modelButton.getAttribute("data-search-model");
        closeSearch();
        setTab("activity");
        chooseModel(modelId);
        return;
      }
      var jobButton = event.target.closest("[data-search-job]");
      if (jobButton) {
        var jobId = jobButton.getAttribute("data-search-job");
        closeSearch();
        setTab("activity");
        selectJob(jobId);
        return;
      }
      var mediaButton = event.target.closest("[data-search-media]");
      if (mediaButton) {
        var item = mediaItem(mediaButton.getAttribute("data-search-media"));
        closeSearch();
        if (item) { setTab("activity"); selectMedia(item); }
      }
    });
    var modelCardMore = $("studio-model-card-more");
    if (modelCardMore) modelCardMore.addEventListener("click", function () {
      setTab("activity");
      toggleMenu("studio-model-menu");
    });

    var inputModes = $("studio-input-modes");
    if (inputModes) inputModes.addEventListener("click", function (event) {
      var button = event.target.closest("[data-input-mode]");
      if (button) setInputMode(button.getAttribute("data-input-mode"));
    });
    var presets = $("studio-presets");
    if (presets) presets.addEventListener("click", function (event) {
      var button = event.target.closest("[data-preset]");
      if (button) applyPreset(button.getAttribute("data-preset"));
    });
    var prompt = $("studio-prompt");
    if (prompt) prompt.addEventListener("input", function () {
      S.values.prompt = prompt.value;
      updatePromptHint();
      updateGenerateState();
    });
    var params = $("studio-params");
    if (params) {
      var onParam = function (event) {
        var target = event.target;
        if (!target || !target.getAttribute) return;
        updateParamFromEvent(target);
      };
      params.addEventListener("input", onParam);
      params.addEventListener("change", onParam);
      params.addEventListener("click", function (event) {
        var upload = event.target.closest("[data-upload-field]");
        if (!upload) return;
        S.pendingField = upload.getAttribute("data-upload-field");
        var input = $("studio-file-input");
        if (input) input.click();
      });
    }

    var attachBtn = $("studio-attach-btn");
    var fileInput = $("studio-file-input");
    if (attachBtn && fileInput) attachBtn.addEventListener("click", function () {
      S.pendingField = null;
      fileInput.click();
    });
    if (fileInput) fileInput.addEventListener("change", function () {
      uploadFiles(fileInput.files);
      fileInput.value = "";
    });
    var attachments = $("studio-attachments");
    if (attachments) attachments.addEventListener("click", function (event) {
      var button = event.target.closest("[data-remove-attachment]");
      if (!button) return;
      removeAttachment(Number(button.getAttribute("data-remove-attachment")));
    });

    var generateBtn = $("studio-generate");
    if (generateBtn) generateBtn.addEventListener("click", generate);

    var stageClear = $("studio-stage-clear");
    if (stageClear) stageClear.addEventListener("click", function () {
      S.selectedMedia = null;
      S.jobId = null;
      S.selectedJob = null;
      S.stageKey = "";
      renderJobs();
      renderStage();
    });
    var openMedia = $("studio-open-media");
    if (openMedia) openMedia.addEventListener("click", openOutput);
    var download = $("studio-download");
    if (download) download.addEventListener("click", downloadOutput);
    var share = $("studio-share");
    if (share) share.addEventListener("click", shareOutput);
    var saveLibrary = $("studio-save-library");
    if (saveLibrary) saveLibrary.addEventListener("click", saveOutputToLibrary);
    var saveSkill = $("studio-save-skill");
    if (saveSkill) saveSkill.addEventListener("click", saveRenderedSkill);
    var copy = $("studio-copy-url");
    if (copy) copy.addEventListener("click", copyOutput);
    var cancel = $("studio-cancel-job");
    if (cancel) cancel.addEventListener("click", cancelSelectedJob);
    var variations = $("studio-variations-list");
    if (variations) variations.addEventListener("click", function (event) {
      var button = event.target.closest("[data-variant]");
      if (!button) return;
      S.selectedVariant = Number(button.getAttribute("data-variant")) || 0;
      S.stageKey = "";
      renderStage();
    });

    var jobsList = $("studio-jobs-list");
    if (jobsList) {
      jobsList.addEventListener("click", function (event) {
        var action = event.target.closest("[data-job-action]");
        if (action) {
          event.stopPropagation();
          jobAction(action.getAttribute("data-job-action"), action.getAttribute("data-job-id"));
          return;
        }
        var card = event.target.closest("[data-job-id]");
        if (card) selectJob(card.getAttribute("data-job-id"));
      });
      jobsList.addEventListener("keydown", function (event) {
        if (event.key !== "Enter" && event.key !== " ") return;
        var card = event.target.closest("[data-job-id]");
        if (!card) return;
        event.preventDefault();
        selectJob(card.getAttribute("data-job-id"));
      });
    }

    var libraryList = $("studio-library-list");
    if (libraryList) {
      libraryList.addEventListener("click", function (event) {
        var action = event.target.closest("[data-library-action]");
        if (action) {
          event.stopPropagation();
          libraryAction(action.getAttribute("data-library-action"), action.getAttribute("data-media-id"));
          return;
        }
        var card = event.target.closest("[data-media-id]");
        var item = card ? mediaItem(card.getAttribute("data-media-id")) : null;
        if (item) { selectMedia(item); setTab("activity"); }
      });
      libraryList.addEventListener("keydown", function (event) {
        if (event.key !== "Enter" && event.key !== " ") return;
        var card = event.target.closest("[data-media-id]");
        var item = card ? mediaItem(card.getAttribute("data-media-id")) : null;
        if (!item) return;
        event.preventDefault();
        selectMedia(item);
        setTab("activity");
      });
    }
    var librarySearch = $("studio-library-search");
    if (librarySearch) librarySearch.addEventListener("input", function (event) {
      S.libraryQuery = event.target.value || "";
      renderLibrary();
    });
    var libraryFilter = $("studio-library-filter");
    if (libraryFilter) libraryFilter.addEventListener("click", function (event) {
      var button = event.target.closest("[data-library-kind]");
      if (!button) return;
      S.libraryKind = button.getAttribute("data-library-kind") || "";
      Array.prototype.forEach.call(libraryFilter.querySelectorAll("[data-library-kind]"), function (item) {
        item.classList.toggle("active", item === button);
      });
      renderLibrary();
    });

    var collectionClick = function (event) {
      var deletePlaylistButton = event.target.closest("[data-playlist-action='delete']");
      if (deletePlaylistButton) {
        var target = find(S.playlists, deletePlaylistButton.getAttribute("data-playlist-id"));
        if (target) deletePlaylist(target);
        return;
      }
      var playlistButton = event.target.closest("[data-playlist-id]");
      if (playlistButton) {
        var playlist = find(S.playlists, playlistButton.getAttribute("data-playlist-id"));
        if (playlist) openPlaylist(playlist.id);
        return;
      }
      var deleteTemplateButton = event.target.closest("[data-template-action='delete']");
      if (deleteTemplateButton) {
        var doomed = find(S.templates, deleteTemplateButton.getAttribute("data-template-id"));
        if (doomed) deleteTemplate(doomed);
        return;
      }
      var templateButton = event.target.closest("[data-template-id]");
      if (templateButton) {
        var template = find(S.templates, templateButton.getAttribute("data-template-id"));
        if (template) applyTemplate(template);
      }
    };
    [
      "studio-rail-playlists", "studio-library-playlists", "studio-playlists-page",
      "studio-library-templates", "studio-templates-page"
    ].forEach(function (id) {
      var host = $(id);
      if (host) host.addEventListener("click", collectionClick);
    });

    var newPlaylist = $("studio-new-playlist");
    if (newPlaylist) newPlaylist.addEventListener("click", function () { openCreatePlaylist(); });
    var newPlaylistLibrary = $("studio-new-playlist-library");
    if (newPlaylistLibrary) newPlaylistLibrary.addEventListener("click", function () { openCreatePlaylist(); });
    var newPlaylistPage = $("studio-new-playlist-page");
    if (newPlaylistPage) newPlaylistPage.addEventListener("click", function () { openCreatePlaylist(); });
    var saveTemplate = $("studio-save-template");
    if (saveTemplate) saveTemplate.addEventListener("click", openSaveTemplate);
    var saveTemplateLibrary = $("studio-save-template-library");
    if (saveTemplateLibrary) saveTemplateLibrary.addEventListener("click", openSaveTemplate);
    var saveTemplatePage = $("studio-save-template-page");
    if (saveTemplatePage) saveTemplatePage.addEventListener("click", openSaveTemplate);
    var playlistsViewAll = $("studio-playlists-viewall");
    if (playlistsViewAll) playlistsViewAll.addEventListener("click", function () { setTab("playlists"); });
    var historyBtn = $("studio-history-btn");
    if (historyBtn) historyBtn.addEventListener("click", function () { setTab("history"); });
    var historyRefresh = $("studio-history-refresh");
    if (historyRefresh) historyRefresh.addEventListener("click", function () { loadJobs(false); });

    var historyPage = $("studio-history-page");
    if (historyPage) {
      historyPage.addEventListener("click", function (event) {
        var action = event.target.closest("[data-history-action]");
        if (action) {
          event.stopPropagation();
          jobAction(action.getAttribute("data-history-action"), action.getAttribute("data-job-id"));
          return;
        }
        var row = event.target.closest("[data-history-job]");
        if (row) {
          selectJob(row.getAttribute("data-history-job"));
          setTab("activity");
        }
      });
      historyPage.addEventListener("keydown", function (event) {
        if (event.key !== "Enter" && event.key !== " ") return;
        var row = event.target.closest("[data-history-job]");
        if (!row) return;
        event.preventDefault();
        selectJob(row.getAttribute("data-history-job"));
        setTab("activity");
      });
    }

    Array.prototype.forEach.call(document.querySelectorAll("[data-quick-action]"), function (button) {
      button.addEventListener("click", function () {
        var action = button.getAttribute("data-quick-action");
        if (action === "image") {
          setTab("activity");
          setInputMode("image");
        } else if (action === "template") {
          openTemplatePicker();
        } else if (action === "playlist") {
          openCreatePlaylist();
        } else if (action === "library") {
          setTab("library");
        }
      });
    });
    var settings = $("studio-settings-btn");
    if (settings) settings.addEventListener("click", function () { setTab("setup"); });

    var modalClose = $("studio-modal-close");
    if (modalClose) modalClose.addEventListener("click", closeModal);
    var modal = $("studio-modal");
    if (modal) modal.addEventListener("click", function (event) {
      if (event.target === modal) closeModal();
    });

    Array.prototype.forEach.call(document.querySelectorAll("[data-studio-tab]"), function (button) {
      button.addEventListener("click", function () { setTab(button.getAttribute("data-studio-tab")); });
    });
    Array.prototype.forEach.call(document.querySelectorAll("[data-studio-subtab]"), function (button) {
      button.addEventListener("click", function () { setTab(button.getAttribute("data-studio-subtab")); });
    });

    var refresh = $("studio-refresh");
    if (refresh) refresh.addEventListener("click", refreshAll);
    var registryHost = $("studio-registry");
    if (registryHost) registryHost.addEventListener("click", function (event) {
      var toggle = event.target.closest("[data-registry-toggle]");
      if (toggle) {
        var pid = toggle.getAttribute("data-registry-toggle");
        S.registryOpen = S.registryOpen === pid ? null : pid;
        renderRegistry();
        return;
      }
      var save = event.target.closest("[data-registry-save]");
      if (save) { registrySave(save.getAttribute("data-registry-save")); return; }
      var test = event.target.closest("[data-registry-test]");
      if (test) { registryTest(test.getAttribute("data-registry-test")); return; }
      var del = event.target.closest("[data-registry-delete]");
      if (del) { registryDelete(del.getAttribute("data-registry-delete")); return; }
    });
    var mcpSave = $("studio-mcp-save");
    if (mcpSave) mcpSave.addEventListener("click", saveMcp);
    var mcpConnect = $("studio-mcp-connect");
    if (mcpConnect) mcpConnect.addEventListener("click", connectMcp);
    var mcpDisconnect = $("studio-mcp-disconnect");
    if (mcpDisconnect) mcpDisconnect.addEventListener("click", disconnectMcp);

    document.addEventListener("click", function (event) {
      var providerMenu = $("studio-provider-menu");
      var modelMenu = $("studio-model-menu");
      var searchInput = $("studio-search");
      var searchMenu = $("studio-search-menu");
      if (providerMenu && !providerMenu.classList.contains("hidden") && providerMenu.contains(event.target)) return;
      if (modelMenu && !modelMenu.classList.contains("hidden") && modelMenu.contains(event.target)) return;
      closeMenus();
      if (searchMenu && !searchMenu.classList.contains("hidden") &&
          !searchMenu.contains(event.target) && !(searchInput && searchInput.contains(event.target))) {
        show(searchMenu, false);
      }
    });
    document.addEventListener("keydown", function (event) {
      if (event.key === "Escape") {
        closeMenus();
        show($("studio-search-menu"), false);
        closeModal();
      }
    });

    bindVideoKit();
  }

  function startTimers() {
    if (!S.pollTimer) {
      S.pollTimer = window.setInterval(function () {
        if (document.hidden) return;
        loadJobs(true);
      }, POLL_MS);
    }
    if (!S.tickTimer) {
      S.tickTimer = window.setInterval(function () {
        if (!S.selectedJob || !SS.isActive(S.selectedJob)) return;
        var label = SS.elapsedLabel(jobElapsed(S.selectedJob));
        setText("studio-job-timer", label);
        setText("studio-busy-elapsed", label + " elapsed");
        setText("studio-result-elapsed", label);
      }, TICK_MS);
    }
  }

  function init() {
    S.view = $("studio-view");
    if (!S.view || S.initialized) return;
    S.initialized = true;
    bind();
    setTab("fashion");
    renderStage();
    renderParams();
    renderInputModes();
    renderModelCard();
    renderUsage();
    updateGenerateState();
    startTimers();
    loadToken().then(function () {
      return Promise.all([loadProviders(), loadAgents()]);
    }).then(function () {
      return Promise.all([
        loadVaultAvatars(true),
        loadJobs(true),
        loadLibrary(true),
        loadPlaylists(true),
        loadTemplates(true),
        loadVideoTemplates(true)
      ]);
    }).catch(function (err) {
      toast(err.message);
    });
    icons();
  }

  function refresh() {
    if (!S.initialized) { init(); return; }
    refreshAll();
  }

  function open() {
    if (window.switchMainView) window.switchMainView("studio");
    else refresh();
  }

  window.Studio = {
    init: init,
    refresh: refresh,
    open: open,
    setTab: setTab,
    toast: toast,
    openJob: function (jobId) {
      selectJob(jobId);
      setTab("history");
    },
    providerId: function () { return S.providerId; }
  };

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
