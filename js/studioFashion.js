/* =====================================================================
   STUDIO · Ads Studio — five-step production workflow (Collart-style)

     1 Model           — preset / upload / generate (Prompt 1)
     2 Product/Props   — uploads + product sheet (Prompt 2)        [optional]
     3 Dressing        — model wears the product/props (Prompt 3)  [optional]
     4 Reference Video — motion clip or storyboard prompt box      [optional]
     5 Production      — final prompt + template dropdowns + result

   Every step is skippable when an earlier step already provides what the
   next one needs. Uploaded and generated images are saved to Preset
   Images automatically after 1 minute. Preset Video stays the default
   preview page; the backend owns validation and prompt composition.
   ===================================================================== */
(function () {
  "use strict";

  var MAX_BYTES = 25 * 1024 * 1024;
  var POLL_MS = 3000;
  var STEP_COUNT = 5;
  var AUTOSAVE_MS = 60 * 1000; // Preset Images auto-save after 1 minute

  var PROMPT_1 = "use the attached image to create a character sheet, grey background, 9:16. " +
    "Do not modify the skin tone and original facial features.";
  var PROMPT_2 = "use the attached images to create product sheet";
  var PROMPT_3 = "step 1 model dress/wear like step 2.";

  var S = {
    initialized: false,
    loading: false,
    submitting: false,
    uploading: 0,
    config: null,
    providerId: "higgsfield",
    configured: false,
    token: null,
    step: 1,

    /* Step 1 · Model */
    modelTab: "preset",
    filters: { engine: "", type: "", style: "", model: "" },
    modelFilter: "",
    modelId: "",
    model: { url: "", name: "" },
    modelPresetId: "",
    modelLibrary: { custom: [], hidden: [] },
    libraryLoaded: false,
    libraryLoading: null,
    createRef: { url: "", name: "" },
    creating: false,
    createJob: null,
    createResults: [],
    imageModels: null,
    modelDetailCache: {},
    menuKey: null,

    /* Step 2 · Product/Props */
    productUploads: [],
    productResults: [],
    productSelected: [],
    productCreating: false,

    /* Step 3 · Dressing */
    dressingResults: [],
    dressed: { url: "", name: "" },
    dressingCreating: false,

    /* Step 4 · Reference Video */
    refTab: "presets",
    reference: { url: "", name: "", duration: 0 },
    presetId: "",
    refSkipped: false,

    /* Step 5 · Production */
    envTemplate: "",
    videoTemplate: "",
    job: null,
    duplicate: false,
    pollTimer: null,

    assetsTab: "creations",
    assetTag: "all",
    assetsLoading: false,
    assetsItems: { output: [], input: [] },
    pendingSaves: {},
    credentialProblem: false,
    freeModels: null,
    freeModelsLoading: null,
    registry: null,
    genCatalog: null,
    registryLoading: null,
    gen: {
      "1": { provider: "auto", model: "auto" },
      "2": { provider: "auto", model: "auto" },
      "3": { provider: "auto", model: "auto" },
      "5": { provider: "auto", model: "veo-3.0-fast-generate-001" }
    },
    uploadCache: {},
    modelPending: null,
    referencePending: null,
    createRefPending: null
  };

  function $(id) { return document.getElementById(id); }

  function esc(value) {
    return String(value == null ? "" : value).replace(/[&<>"']/g, function (c) {
      return { "\x26": "\x26amp;", "<": "\x26lt;", ">": "\x26gt;", '"': "\x26quot;", "'": "\x26#39;" }[c];
    });
  }

  function icons() {
    if (window.lucide && window.lucide.createIcons) {
      try { window.lucide.createIcons(); } catch (err) { /* no-op */ }
    }
  }

  function toast(message) {
    if (window.Studio && window.Studio.toast) window.Studio.toast(message);
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

  function currentProvider() {
    if (window.Studio && window.Studio.providerId) {
      var id = window.Studio.providerId();
      if (id) return id;
    }
    return S.providerId || "higgsfield";
  }

  /* ------------------------------------------------------------------ */
  /* Credential error handling (raw engine error text is cryptic)         */
  /* ------------------------------------------------------------------ */

  function isAuthError(err) {
    var code = String((err && err.code) || "");
    var text = String((err && err.message) || "");
    return code === "authentication_failed" || code === "credentials_error" ||
      code === "invalid_key" || code === "missing_key" ||
      /invalid credential|invalid credentials|unauthorized|authentication failed|rejected the key/i.test(text);
  }

  function flagCredentialProblem() {
    S.credentialProblem = true;
    var hint = $("fashion-setup-hint");
    if (hint) hint.classList.remove("hidden");
    var text = $("fashion-setup-hint-text");
    if (text) {
      text.textContent = "Google AI rejected your key. Open Provider Setup, re-save your free Google AI key, then press Test to verify.";
    }
    renderStatus();
  }

  function clearCredentialProblem() {
    if (!S.credentialProblem) return;
    S.credentialProblem = false;
    var text = $("fashion-setup-hint-text");
    if (text) {
      text.textContent = "Images already run on free credits (no key). Add a free Google AI key in Provider Setup to edit your uploads and to generate video (Veo).";
    }
    syncUI();
  }

  function friendlyError(err) {
    var text = String((err && err.message) || "Something went wrong.");
    var code = String((err && err.code) || "");
    if (code === "missing_key") {
      flagCredentialProblem();
      return "Add your free Google AI key in Provider Setup to use this engine (get one at aistudio.google.com — it's free). " + text;
    }
    if (code === "invalid_key" || isAuthError(err)) {
      flagCredentialProblem();
      return "Google AI rejected the key (" + text + "). Open Provider Setup, re-save your free key, then press Test to verify.";
    }
    return text;
  }

  /* ------------------------------------------------------------------ */
  /* Free engines (Pollinations + Google AI) — no paid provider keys      */
  /* ------------------------------------------------------------------ */
  function ensureFreeModels(force) {
    if (S.freeModels && !force) return Promise.resolve(S.freeModels);
    if (S.freeModelsLoading) return S.freeModelsLoading;
    S.freeModelsLoading = api("/api/studio/free/models").then(function (caps) {
      S.freeModels = caps || { google: false, image_engines: [], video_engines: [] };
      var gen5 = genEntry("5");
      if (!gen5.model || !(S.freeModels.video_engines || []).some(function (e) { return e.id === gen5.model; })) {
        S.modelId = ((S.freeModels.video_engines || [])[0] || {}).id || "";
        gen5.model = S.modelId;
      } else {
        S.modelId = gen5.model;
      }
      applyModelDefaults(true);
      renderGenSelects();
      syncUI();
    }).catch(function (err) {
      toast(friendlyError(err));
    }).then(function () {
      S.freeModelsLoading = null;
    });
    return S.freeModelsLoading;
  }

  /* ------------------------------------------------------------------ */
  /* Provider registry + classified model catalog (per-step dropdowns)   */
  /* ------------------------------------------------------------------ */
  function ensureRegistry(force) {
    if (S.registry && S.genCatalog && !force) return Promise.resolve();
    if (S.registryLoading) return S.registryLoading;
    S.registryLoading = Promise.all([
      api("/api/studio/registry"),
      api("/api/studio/generation/catalog")
    ]).then(function (results) {
      S.registry = results[0] || { providers: [] };
      S.genCatalog = results[1] || { providers: {} };
      renderGenSelects();
      renderStatus();
    }).catch(function () {
      /* The registry is optional; free engines still work. */
    }).then(function () {
      S.registryLoading = null;
    });
    return S.registryLoading;
  }

  function refreshRegistry() {
    return ensureRegistry(true);
  }

  function genKind(step) {
    return String(step) === "5" ? "video" : "image";
  }

  function genEntry(step) {
    return S.gen[String(step)] || { provider: "auto", model: "auto" };
  }

  function genCatalogModels(provider, kind) {
    if (!S.genCatalog || !S.genCatalog.providers) return [];
    var entry = S.genCatalog.providers[provider];
    return (entry && entry[kind]) || [];
  }

  function genModelNeeds(provider, kind, modelId) {
    var models = genCatalogModels(provider, kind);
    for (var i = 0; i < models.length; i += 1) {
      if (models[i].id === modelId) return models[i].needs || "prompt";
    }
    return "prompt";
  }

  function providerConnected(providerId) {
    if (!S.registry || !S.registry.providers) return providerId === "auto";
    for (var i = 0; i < S.registry.providers.length; i += 1) {
      if (S.registry.providers[i].id === providerId) return !!S.registry.providers[i].connected;
    }
    return false;
  }

  function renderGenSelects() {
    var steps = ["1", "2", "3", "5"];
    var providers = (S.registry && S.registry.providers) || [];
    steps.forEach(function (step) {
      var kind = genKind(step);
      var gen = genEntry(step);
      var providerSel = $("fashion-gen-provider-" + step);
      var modelSel = $("fashion-gen-model-" + step);
      if (providerSel) {
        providerSel.innerHTML = providers.filter(function (p) {
          if (!p.adapter) return false;
          if (p.id === "auto") return true;
          if (!p.connected) return false;
          return genCatalogModels(p.id, kind).length > 0;
        }).map(function (p) {
          var label = p.label || p.id;
          if (p.id !== "auto" && genCatalogModels(p.id, kind).length === 0) {
            label += " (no " + kind + " models)";
          }
          return '<option value="' + esc(p.id) + '">' + esc(label) + "</option>";
        }).join("");
        if (providers.some(function (p) { return p.id === gen.provider; }) &&
            providerSel.querySelector('option[value="' + gen.provider + '"]')) {
          providerSel.value = gen.provider;
        } else {
          providerSel.value = "auto";
          gen.provider = "auto";
        }
      }
      if (modelSel) {
        modelSel.innerHTML = genModelOptionsHtml(gen.provider, kind);
        var models = genCatalogModels(gen.provider, kind);
        var has = models.some(function (m) { return m.id === gen.model; });
        if (!has && models.length) {
          gen.model = models[0].id;
        }
        if (gen.model) modelSel.value = gen.model;
      }
    });
  }

  function genModelOptionsHtml(provider, kind) {
    var models = genCatalogModels(provider, kind);
    if (!models.length) {
      return '<option value="">No ' + esc(kind) + ' models for this provider</option>';
    }
    var groups = {};
    models.forEach(function (model) {
      var label = model.mode_label || "Models";
      (groups[label] = groups[label] || []).push(model);
    });
    var html = "";
    Object.keys(groups).sort().forEach(function (groupLabel) {
      html += '<optgroup label="' + esc(groupLabel) + '">';
      groups[groupLabel].forEach(function (model) {
        html += '<option value="' + esc(model.id) + '">' + esc(model.label) + "</option>";
      });
      html += "</optgroup>";
    });
    return html;
  }

  function videoEngines() {
    return (S.freeModels && S.freeModels.video_engines) || [];
  }

  function selectedEngine() {
    /* The active Step 5 video model (free-engine view of the selection). */
    var activeId = genEntry("5").model;
    var engines = videoEngines();
    for (var i = 0; i < engines.length; i += 1) {
      if (engines[i].id === activeId) return engines[i];
    }
    return null;
  }

  /* True when every reference is on the public internet (Pollinations can
     fetch it themselves). Local /api/studio/media URLs are not public. */
  function allRefsPublic(refs) {
    var origin = window.location.origin;
    return refs.every(function (url) {
      return /^https?:\/\//i.test(String(url)) && String(url).indexOf(origin) !== 0;
    });
  }

  function validateFreeEngine(engine, refs) {
    /* Free-engine constraints, explained before any round trip. */
    var google = !!(S.freeModels && S.freeModels.google);
    if (engine === "google-gemini-image" || engine === "google") {
      if (!google) {
        throw new Error("Gemini Image needs a free Google AI key — connect Google in Provider Setup (aistudio.google.com, it's free), or switch the provider back to Auto.");
      }
      return;
    }
    if (engine === "pollinations-flux" || engine === "pollinations-turbo") {
      if (refs.length && !allRefsPublic(refs)) {
        throw new Error("Pollinations can only edit images that are already public — connect Google (free key) in Provider Setup to edit your uploads, or paste a public image URL.");
      }
      return;
    }
    // "auto": Google when a key exists; Pollinations edits need public refs.
    if (refs.length && !google && !allRefsPublic(refs)) {
      throw new Error("Editing uploaded images needs a free Google AI key — connect Google in Provider Setup (aistudio.google.com, it's free). Or paste a public image URL instead.");
    }
  }

  function higgsfieldConnected() {
    return providerConnected("higgsfield");
  }

  function imageFieldFor(detail) {
    var props = (detail && detail.schema && detail.schema.properties) || {};
    var arrayField = null;
    var singleField = null;
    Object.keys(props).forEach(function (name) {
      if (!/image/i.test(name)) return;
      var type = props[name] && props[name].type;
      if (type === "array" && !arrayField) arrayField = name;
      if (type === "string" && !singleField) singleField = name;
    });
    return arrayField || singleField;
  }

  function aspectFieldFor(detail) {
    var props = (detail && detail.schema && detail.schema.properties) || {};
    var names = Object.keys(props);
    for (var i = 0; i < names.length; i += 1) {
      if (/aspect/i.test(names[i])) return names[i];
    }
    return null;
  }

  function submitHiggsfieldImage(prompt, refs, modelId, aspect) {
    if (!higgsfieldConnected()) {
      return Promise.reject(new Error("Higgsfield is not connected — save its API key and secret in Provider Setup first."));
    }
    return api("/api/studio/models?id=" + encodeURIComponent(modelId)).then(function (detail) {
      var params = { prompt: prompt };
      if (refs.length) {
        var field = imageFieldFor(detail);
        if (!field) {
          throw new Error("This model does not accept reference images — pick a Text → Image model or remove the reference.");
        }
        var prop = (detail.schema && detail.schema.properties && detail.schema.properties[field]) || {};
        params[field] = prop.type === "array" ? refs.slice() : refs[0];
      }
      if (aspect) {
        var aspectField = aspectFieldFor(detail);
        if (aspectField) params[aspectField] = aspect;
      }
      return api("/api/studio/jobs", {
        method: "POST",
        body: { provider: "higgsfield", model_id: modelId, params: params, auto_download: true }
      }).then(function (data) { return (data && data.job) || null; });
    });
  }

  function limits() {
    return (S.config && S.config.limits) || { outfits: 5, outfit_images: 5, reference_seconds: 15 };
  }

  /* ------------------------------------------------------------------ */
  /* Selection helpers (what the later steps carry forward)              */
  /* ------------------------------------------------------------------ */
  function identityUrl() {
    /* The dressed model (Step 3) wins over the raw model (Step 1). */
    return S.dressed.url || S.model.url;
  }

  function productUrls() {
    return S.productSelected.slice();
  }

  function imageCount() {
    var urls = identityUrl() ? 1 : 0;
    return urls + S.productSelected.length;
  }

  /* Engine pool: the free video engines (Veo via Google AI). */
  function enginePool() {
    return videoEngines();
  }

  function selectedModel() {
    return selectedEngine();
  }

  function ensureEngineForPool() {
    var pool = enginePool();
    if (!pool.length) return;
    for (var i = 0; i < pool.length; i += 1) {
      if (pool[i].id === S.modelId) return;
    }
    S.modelId = pool[0].id;
    applyModelDefaults(true);
  }

  /* ------------------------------------------------------------------ */
  /* Step navigation                                                     */
  /* ------------------------------------------------------------------ */
  function goToStep(step) {
    S.step = Math.max(1, Math.min(STEP_COUNT, Number(step) || 1));
    updateStepUI();
  }

  function nextStep() {
    if (S.step >= STEP_COUNT) return;
    goToStep(S.step + 1);
  }

  function prevStep() {
    if (S.step > 1) goToStep(S.step - 1);
  }

  function updateStepUI() {
    for (var i = 1; i <= STEP_COUNT; i += 1) {
      var section = $("step-" + i + "-content");
      if (section) section.classList.toggle("hidden", i !== S.step);
      var chip = $("step-btn-" + i);
      if (chip) {
        var num = chip.querySelector(".step-num");
        chip.classList.toggle("is-active", i === S.step);
        chip.classList.toggle("is-done", i !== S.step && stepComplete(i));
        if (num) num.textContent = (chip.classList.contains("is-done")) ? "✓" : String(i);
      }
    }
    var prev = $("prev-btn");
    if (prev) prev.disabled = S.step <= 1;
    var next = $("next-btn");
    var generate = $("generate-btn");
    if (next && generate) {
      next.classList.toggle("hidden", S.step === STEP_COUNT);
      generate.classList.toggle("hidden", S.step !== STEP_COUNT);
    }
    var stepper = $("collart-stepper");
    if (stepper) stepper.setAttribute("data-step", String(S.step));
    icons();
  }

  function stepComplete(step) {
    if (step === 1) return !!S.model.url;
    if (step === 2) return S.productSelected.length > 0;
    if (step === 3) return !!S.dressed.url;
    if (step === 4) return !!S.reference.url || S.refSkipped;
    return !!(S.job && S.job.state === "succeeded");
  }

  function nextIncomplete() {
    if (!identityUrl()) return 1;
    return 0;
  }

  /* ------------------------------------------------------------------ */
  /* Uploads                                                             */
  /* ------------------------------------------------------------------ */
  function fileToBase64(file) {
    return new Promise(function (resolve, reject) {
      var reader = new FileReader();
      reader.onload = function () {
        var text = String(reader.result || "");
        resolve(text.slice(text.indexOf(",") + 1));
      };
      reader.onerror = function () { reject(new Error("Could not read " + file.name)); };
      reader.readAsDataURL(file);
    });
  }

  /* Local blob preview so the attach box shows the image instantly while
     the upload runs in the background. */
  function localPreviewUrl(file) {
    try { return URL.createObjectURL(file); } catch (err) { return ""; }
  }

  function uploadCacheKey(file) {
    return file.name + ":" + file.size + ":" + (file.lastModified || 0);
  }

  function uploadFile(file) {
    /* Free pipeline: uploads are stored in the local media library — no
       provider credentials involved. Identical files are served from the
       client cache (and deduped server-side by content hash), so re-attaching
       the same media is instant. */
    if (file.size > MAX_BYTES) {
      return Promise.reject(new Error(file.name + " is larger than 25 MB."));
    }
    var cacheKey = uploadCacheKey(file);
    if (S.uploadCache[cacheKey]) {
      return Promise.resolve(S.uploadCache[cacheKey]);
    }
    S.uploading += 1;
    renderDock();
    return fileToBase64(file)
      .then(function (data) {
        return api("/api/studio/free/uploads", {
          method: "POST",
          body: {
            filename: file.name,
            content_type: file.type || "image/png",
            data_base64: data
          }
        });
      })
      .then(function (result) {
        if (!result.public_url) throw new Error("The upload could not be stored.");
        var item = { url: result.public_url, name: file.name };
        S.uploadCache[cacheKey] = item;
        return item;
      })
      .then(function (item) {
        S.uploading = Math.max(0, S.uploading - 1);
        clearCredentialProblem();
        renderDock();
        return item;
      }, function (err) {
        S.uploading = Math.max(0, S.uploading - 1);
        renderDock();
        if (err && err.status === 404) {
          throw new Error("This server build predates the free upload route — restart the DENNIS server and refresh this page.");
        }
        throw err;
      });
  }

  function videoDuration(file) {
    return new Promise(function (resolve) {
      var video = document.createElement("video");
      var url = URL.createObjectURL(file);
      var settled = false;
      function finish(value) {
        if (settled) return;
        settled = true;
        URL.revokeObjectURL(url);
        resolve(value);
      }
      video.preload = "metadata";
      video.onloadedmetadata = function () {
        finish(Number.isFinite(video.duration) ? video.duration : 0);
      };
      video.onerror = function () { finish(0); };
      window.setTimeout(function () { finish(0); }, 5000);
      video.src = url;
    });
  }

  /* ------------------------------------------------------------------ */
  /* Config                                                              */
  /* ------------------------------------------------------------------ */
  function loadConfig() {
    S.loading = true;
    syncUI();
    return api("/api/studio/fashion/config?provider=" + encodeURIComponent(currentProvider()))
      .then(function (data) {
        S.config = data;
        S.loading = false;
        syncUI();
      })
      .catch(function (err) {
        S.loading = false;
        syncUI(err.message);
      });
  }

  function applyModelDefaults(force) {
    var model = selectedModel();
    if (!model) return;
    var defaults = (S.config && S.config.defaults) || {};
    var res = $("fashion-resolution");
    var dur = $("fashion-duration");
    var aspect = $("fashion-aspect");
    var audio = $("fashion-audio");
    if (audio) audio.checked = defaults.generate_audio !== false;
    if (res) {
      fillSelect(res, model.resolutions || [], function (value) { return value; });
      if (force || !res.value) {
        var preferredRes = defaults.resolution || "";
        res.value = (preferredRes && (model.resolutions || []).indexOf(preferredRes) !== -1)
          ? preferredRes
          : (res.options[0] && res.options[0].value) || "";
      }
    }
    if (dur) {
      var durations = (model.durations || []).slice();
      if (!durations.length && (model.duration_min != null || model.duration_max != null)) {
        var min = model.duration_min != null ? model.duration_min : 4;
        var max = model.duration_max != null ? model.duration_max : 15;
        for (var d = min; d <= max && durations.length < 20; d += 1) durations.push(d);
      }
      fillSelect(dur, durations, function (value) { return value + "s"; });
      if (force || !dur.value) {
        var preferredDur = defaults.duration != null ? String(defaults.duration) : "";
        dur.value = (preferredDur && durations.some(function (v) { return String(v) === preferredDur; }))
          ? preferredDur
          : (durations.length ? String(durations[0]) : "");
      }
    }
    if (aspect) {
      fillSelect(aspect, model.aspect_ratios || [], function (value) { return value; });
      if (force || !aspect.value) {
        var preferredAspect = defaults.aspect_ratio || "";
        aspect.value = (preferredAspect && (model.aspect_ratios || []).indexOf(preferredAspect) !== -1)
          ? preferredAspect
          : (aspect.options[0] && aspect.options[0].value) || "";
      }
    }
    renderEngines();
  }

  function fillSelect(select, values, label) {
    var previous = select.value;
    select.innerHTML = "";
    if (!values.length) {
      var option = document.createElement("option");
      option.value = "";
      option.textContent = "Model default";
      select.appendChild(option);
      select.disabled = true;
      return;
    }
    select.disabled = false;
    values.forEach(function (value) {
      var item = document.createElement("option");
      item.value = String(value);
      item.textContent = label ? label(value) : String(value);
      select.appendChild(item);
    });
    if (previous && values.some(function (v) { return String(v) === previous; })) {
      select.value = previous;
    }
  }

  function populateFilter(select, values, placeholder) {
    if (!select) return;
    select.innerHTML = "";
    var any = document.createElement("option");
    any.value = "";
    any.textContent = placeholder;
    select.appendChild(any);
    (values || []).forEach(function (value) {
      var option = document.createElement("option");
      option.value = value;
      option.textContent = value;
      select.appendChild(option);
    });
  }

  function setFilterSelect(id, value) {
    var select = $(id);
    if (!select) return;
    for (var i = 0; i < select.options.length; i += 1) {
      if (select.options[i].value === value) { select.value = value; return; }
    }
    select.value = "";
  }

  /* ------------------------------------------------------------------ */
  /* Preset library (Preset Models + Preset Images)                      */
  /* ------------------------------------------------------------------ */
  function loadLibrary(force) {
    if (S.libraryLoading) return S.libraryLoading;
    if (S.libraryLoaded && !force) return Promise.resolve();
    S.libraryLoading = api("/api/studio/fashion/model-library").then(function (data) {
      S.modelLibrary = {
        custom: (data && data.custom) || [],
        hidden: (data && data.hidden_presets) || []
      };
      S.libraryLoaded = true;
      renderModelGrid();
      renderProductPresets();
    }).catch(function () {
      /* The library is optional; the catalog presets still work without it. */
    }).then(function () {
      S.libraryLoading = null;
    });
    return S.libraryLoading;
  }

  function saveToLibrary(url, name, source, prompt, kind) {
    return api("/api/studio/fashion/model-library", {
      method: "POST",
      body: {
        url: url,
        name: name,
        source: source || "upload",
        prompt: prompt || "",
        kind: kind || "model"
      }
    }).then(function () {
      S.libraryLoaded = false;
      return loadLibrary(true);
    }).catch(function (err) { toast(friendlyError(err)); });
  }

  /* Every uploaded/generated image lands in Preset Images after 1 minute. */
  function schedulePresetSave(kind, item, source) {
    if (!item || !item.url || S.pendingSaves[item.url]) return;
    S.pendingSaves[item.url] = true;
    window.setTimeout(function () {
      saveToLibrary(item.url, item.name, source || item.source || "upload", item.prompt || "", kind)
        .then(function () {
          toast("Saved to Preset Images — " + (item.name || kind) + ".");
        })
        .catch(function () { delete S.pendingSaves[item.url]; });
    }, AUTOSAVE_MS);
  }

  /* ------------------------------------------------------------------ */
  /* Rendering                                                           */
  /* ------------------------------------------------------------------ */
  function syncUI(error) {
    var models = enginePool();
    var select = $("fashion-model-select");
    if (select) {
      select.innerHTML = "";
      models.forEach(function (model) {
        var option = document.createElement("option");
        option.value = model.id;
        option.textContent = model.label || model.id;
        option.selected = model.id === S.modelId;
        select.appendChild(option);
      });
      select.disabled = !models.length;
    }

    var filters = (S.config && S.config.preset_filters) || {};
    populateFilter($("fashion-filter-engine"), filters.engine, "Engine");
    populateFilter($("fashion-filter-type"), filters.type, "Type");
    populateFilter($("fashion-filter-style"), filters.style, "Style");
    populateFilter($("fashion-filter-model"), filters.model, "Model");
    populateFilter($("fashion-model-filter"), filters.identity, "Region");
    setFilterSelect("fashion-filter-engine", S.filters.engine);
    setFilterSelect("fashion-filter-type", S.filters.type);
    setFilterSelect("fashion-filter-style", S.filters.style);
    setFilterSelect("fashion-filter-model", S.filters.model);
    setFilterSelect("fashion-model-filter", S.modelFilter);

    var hint = $("fashion-setup-hint");
    if (hint) {
      var needsGoogleKey = S.freeModels && !S.freeModels.google && !S.loading;
      hint.classList.toggle("hidden", S.credentialProblem ? false : !needsGoogleKey);
      var hintText = $("fashion-setup-hint-text");
      if (hintText && !S.credentialProblem) {
        hintText.textContent = "Images already run on free credits (no key). Add a free Google AI key in Provider Setup to edit your uploads and to generate video (Veo).";
      }
    }

    renderStatus(error);
    renderModelTabs();
    renderModelGrid();
    renderModel();
    renderCreateRef();
    renderCreate();
    renderProductUploads();
    renderProductResults();
    renderProductPresets();
    renderDressingBusy();
    renderDressingResults();
    renderRefTabs();
    renderVideoGrid();
    renderVideo();
    renderTemplateSelects();
    renderGenSelects();
    renderEngines();
    renderCarried();
    renderResult();
    updateStepUI();
    renderDock();
    icons();
  }

  function renderStatus(error) {
    var note = $("fashion-status");
    if (!note) return;
    if (S.loading) { note.textContent = "Loading models…"; note.hidden = false; return; }
    if (error) { note.textContent = error; note.hidden = false; return; }
    if (S.credentialProblem) {
      note.textContent = "Google AI rejected the key — re-save it (and Test) in Provider Setup.";
      note.hidden = false;
      return;
    }
    var bits = [];
    if (S.freeModels) {
      bits.push(S.freeModels.google ? "Google AI key ✓" : "Google key —");
      bits.push("Pollinations ✓ (no key)");
    } else {
      bits.push("Loading free engines…");
    }
    bits.push(S.model.url ? "Model ✓" : "Model —");
    bits.push(S.productSelected.length ? S.productSelected.length + " product image" + (S.productSelected.length > 1 ? "s" : "") + " ✓" : "No products");
    bits.push(S.dressed.url ? "Dressed ✓" : "Not dressed");
    bits.push(S.reference.url ? "Reference video ✓" : "No reference video");
    var gen = genEntry("5");
    var genLabel = gen.provider === "higgsfield" ? "Higgsfield · " + gen.model
      : (gen.provider === "google" ? "Google · " + (gen.model !== "auto" ? gen.model : "Veo")
        : (gen.provider === "pollinations" ? "Pollinations" : "Auto (free)"));
    bits.push("Step 5: " + genLabel);
    note.textContent = bits.join(" · ") + " — configure the draft in Production.";
    note.hidden = false;
  }

  function renderModelTabs() {
    var views = { preset: "step2-preset-view", create: "step2-create-view" };
    Object.keys(views).forEach(function (key) {
      var view = $(views[key]);
      if (view) view.classList.toggle("hidden", key !== S.modelTab);
    });
    var tabs = { preset: "tab-model-preset", create: "tab-model-create" };
    Object.keys(tabs).forEach(function (key) {
      var button = $(tabs[key]);
      if (button) button.classList.toggle("is-active", key === S.modelTab);
    });
  }

  function switchModelTab(tab) {
    S.modelTab = tab === "create" ? "create" : "preset";
    renderModelTabs();
    if (S.modelTab === "preset") {
      renderModelGrid();
    } else {
      renderCreate();
      renderCreateResult();
    }
  }

  function filteredPresets() {
    var videos = (S.config && S.config.presets) || [];
    return videos.filter(function (item) {
      if (S.filters.engine && (item.engines || []).indexOf(S.filters.engine) === -1) return false;
      if (S.filters.type && (item.type || []).indexOf(S.filters.type) === -1) return false;
      if (S.filters.style && (item.style || []).indexOf(S.filters.style) === -1) return false;
      if (S.filters.model && (item.model || []).indexOf(S.filters.model) === -1) return false;
      return true;
    });
  }

  function presetById(id) {
    var videos = (S.config && S.config.presets) || [];
    for (var i = 0; i < videos.length; i += 1) {
      if (videos[i].id === id) return videos[i];
    }
    return null;
  }

  function renderVideoGrid() {
    var grid = $("video-grid");
    if (!grid) return;
    var items = filteredPresets();
    var total = ((S.config && S.config.presets) || []).length;
    var count = $("fashion-preset-count");
    if (count) count.textContent = total ? items.length + " of " + total : "";
    if (!items.length) {
      grid.innerHTML = '<p class="collart-note">No presets match these filters.</p>';
      return;
    }
    grid.innerHTML = items.map(function (item) {
      var active = item.id === S.presetId;
      var badge = item.vip
        ? '<span class="collart-badge brand">VIP</span>'
        : '<span class="collart-badge orange">FREE TRIAL</span>';
      var engine = (item.engines && item.engines[0]) || "";
      return '<button type="button" class="collart-tile' + (active ? " is-active" : "") +
        '" data-preset-id="' + esc(item.id) + '" title="' + esc(item.title + (engine ? " · " + engine : "")) + '">' +
        '<span class="collart-tile-media">' +
        (item.cover ? '<img src="' + esc(item.cover) + '" alt="" loading="lazy">' : "") +
        "</span>" +
        '<span class="collart-tile-badges">' + badge +
        '<span class="collart-badge dark">' + item.outfits + " Outfit" + (item.outfits > 1 ? "s" : "") + "</span></span>" +
        (item.duration ? '<span class="collart-badge duration"><i data-lucide="volume-2" class="w-2.5 h-2.5"></i>' + esc(item.duration) + "s</span>" : "") +
        '<span class="collart-tile-name">' + esc(item.title) + "</span>" +
        "</button>";
    }).join("");
  }

  function allModels() {
    var catalog = ((S.config && S.config.model_presets) || []).filter(function (item) {
      return S.modelLibrary.hidden.indexOf(String(item.id)) === -1;
    }).map(function (item) {
      return { key: "preset:" + item.id, id: String(item.id), title: item.title || item.id, cover: item.cover, kind: "preset" };
    });
    var custom = (S.modelLibrary.custom || []).filter(function (item) {
      return (item.kind || "model") === "model";
    }).map(function (item) {
      return { key: "custom:" + item.id, id: item.id, title: item.name || "My model", cover: item.url, kind: "custom", source: item.source };
    });
    return custom.concat(catalog);
  }

  function findModelEntry(key) {
    var parts = String(key || "").split(":");
    var kind = parts[0];
    var id = parts.slice(1).join(":");
    var items = allModels();
    for (var i = 0; i < items.length; i += 1) {
      if (items[i].kind === kind && String(items[i].id) === id) return items[i];
    }
    return null;
  }

  function renderModelGrid() {
    closeModelMenu();
    var grid = $("model-grid");
    if (!grid) return;
    var items = allModels().filter(function (item) {
      if (item.kind !== "preset") return true;
      if (!S.modelFilter) return true;
      var preset = findById((S.config && S.config.model_presets) || [], item.id);
      return !!preset && (preset.identity || []).indexOf(S.modelFilter) !== -1;
    });
    if (!items.length) {
      grid.innerHTML = '<p class="collart-note">No preset models available.</p>';
      return;
    }
    grid.innerHTML = items.map(function (item) {
      var active = item.kind === "preset"
        ? item.id === S.modelPresetId
        : item.cover === S.model.url;
      var badge = item.kind === "custom"
        ? '<span class="collart-badge custom">' + (item.source === "generated" ? "GENERATED" : "MY MODEL") + "</span>"
        : "";
      return '<div class="collart-tilewrap">' +
        '<button type="button" class="collart-tile' + (active ? " is-active" : "") +
        '" data-model-preset-key="' + esc(item.key) + '" title="' + esc(item.title) + '">' +
        '<span class="collart-tile-media">' +
        (item.cover ? '<img src="' + esc(item.cover) + '" alt="" loading="lazy">' : "") +
        "</span>" +
        '<span class="collart-tile-badges">' + badge + "</span>" +
        '<span class="collart-tile-name">' + esc(item.title) + "</span>" +
        "</button>" +
        '<button type="button" class="collart-kebab" data-model-kebab="' + esc(item.key) +
        '" title="Model actions" aria-label="Model actions"><i data-lucide="more-horizontal" class="w-3.5 h-3.5"></i></button>' +
        "</div>";
    }).join("");
    icons();
  }

  function renderModel() {
    var preview = $("fashion-model-preview");
    if (!preview) return;
    if (S.modelPending) {
      preview.classList.remove("is-empty");
      preview.innerHTML = '<img src="' + esc(S.modelPending.url) + '" alt="Model reference">' +
        '<span class="collart-uploadbadge"><span class="collart-spinner"></span>Uploading…</span>';
    } else if (S.model.url) {
      preview.classList.remove("is-empty");
      preview.innerHTML = '<img src="' + esc(S.model.url) + '" alt="Model reference">' +
        '<button type="button" class="collart-mini collart-mini-float" data-clear-model title="Remove model">×</button>';
    } else {
      preview.classList.add("is-empty");
      preview.innerHTML = '<span class="collart-dropicon"><i data-lucide="user-round" class="w-6 h-6"></i></span>' +
        "<p>Click or drag to upload a full-body model photo</p><em>JPG / PNG / WebP · up to 25 MB</em>" +
        '<span class="collart-btn">Upload Image</span>';
    }
  }

  /* ------------------------------------------------------------------ */
  /* Step 2 · Product/Props                                              */
  /* ------------------------------------------------------------------ */
  function renderProductUploads() {
    var grid = $("fashion-product-uploads");
    if (!grid) return;
    if (!S.productUploads.length) {
      grid.innerHTML = "";
      grid.classList.add("hidden");
      return;
    }
    grid.classList.remove("hidden");
    grid.innerHTML = S.productUploads.map(function (item, index) {
      if (item.pending) {
        return '<div class="collart-thumbitem is-pending" title="Uploading ' + esc(item.name || "image") + '…">' +
          '<img src="' + esc(item.url) + '" alt="" loading="lazy">' +
          '<span class="collart-uploadbadge"><span class="collart-spinner"></span>Uploading…</span>' +
          "</div>";
      }
      var selected = S.productSelected.indexOf(item.url) !== -1;
      return '<div class="collart-thumbitem' + (selected ? " is-selected" : "") +
        '" data-product-pick="' + esc(item.url) + '" title="' + esc(item.name || "Product image") + '">' +
        '<img src="' + esc(item.url) + '" alt="" loading="lazy">' +
        '<span class="collart-thumbbadge">UPLOADED</span>' +
        '<button type="button" class="collart-mini" data-product-upload-remove="' + index + '" title="Remove">×</button>' +
        "</div>";
    }).join("");
  }

  function renderProductResults() {
    var grid = $("fashion-product-results");
    if (!grid) return;
    if (!S.productResults.length) {
      grid.innerHTML = "";
      grid.classList.add("hidden");
      return;
    }
    grid.classList.remove("hidden");
    grid.innerHTML = S.productResults.map(function (item, index) {
      var selected = S.productSelected.indexOf(item.url) !== -1;
      return '<div class="collart-thumbitem' + (selected ? " is-selected" : "") +
        '" data-product-pick="' + esc(item.url) + '" title="' + esc(item.name || "Product sheet") + '">' +
        '<img src="' + esc(item.url) + '" alt="" loading="lazy">' +
        '<span class="collart-thumbbadge">GENERATED</span>' +
        '<button type="button" class="collart-mini" data-product-result-remove="' + index + '" title="Discard">×</button>' +
        "</div>";
    }).join("");
  }

  function renderProductPresets() {
    var grid = $("product-preset-grid");
    if (!grid) return;
    var custom = (S.modelLibrary.custom || []).filter(function (item) {
      return (item.kind || "model") === "product";
    }).map(function (item) {
      return { url: item.url, name: item.name, source: item.source || "upload" };
    });
    var catalog = ((S.config && S.config.outfit_presets) || []).map(function (item) {
      return { url: item.cover, name: item.title || item.id, source: "preset" };
    });
    var items = custom.concat(catalog);
    if (!items.length) {
      grid.innerHTML = '<p class="collart-note">No preset images yet — uploads and generated sheets land here automatically after 1 minute.</p>';
      return;
    }
    grid.innerHTML = items.map(function (item) {
      var selected = S.productSelected.indexOf(item.url) !== -1;
      var badge = item.source === "generated"
        ? '<span class="collart-badge custom">GENERATED</span>'
        : item.source === "upload"
          ? '<span class="collart-badge custom">UPLOADED</span>'
          : "";
      return '<button type="button" class="collart-tile small' + (selected ? " is-active" : "") +
        '" data-product-pick="' + esc(item.url) + '" title="' + esc(item.name) + '">' +
        '<span class="collart-tile-media">' +
        (item.url ? '<img src="' + esc(item.url) + '" alt="" loading="lazy">' : "") +
        "</span>" +
        '<span class="collart-tile-badges">' + badge + "</span>" +
        '<span class="collart-tile-name">' + esc(item.name) + "</span>" +
        "</button>";
    }).join("");
  }

  /* ------------------------------------------------------------------ */
  /* Step 3 · Dressing                                                   */
  /* ------------------------------------------------------------------ */
  function renderDressingBusy() {
    var busy = $("fashion-dressing-busy");
    if (!busy) return;
    busy.classList.toggle("hidden", !S.dressingCreating);
  }

  function renderDressingResults() {
    var grid = $("fashion-dressing-result");
    if (!grid) return;
    if (!S.dressingResults.length) {
      grid.innerHTML = "";
      grid.classList.add("hidden");
      return;
    }
    grid.classList.remove("hidden");
    grid.innerHTML = S.dressingResults.map(function (item, index) {
      var selected = S.dressed.url === item.url;
      return '<div class="collart-thumbitem' + (selected ? " is-selected" : "") +
        '" data-dressing-pick="' + index + '" title="' + esc(item.name || "Dressed model") + '">' +
        '<img src="' + esc(item.url) + '" alt="" loading="lazy">' +
        '<span class="collart-thumbbadge">' + (selected ? "SELECTED" : "GENERATED") + "</span>" +
        '<button type="button" class="collart-mini" data-dressing-discard="' + index + '" title="Discard">×</button>' +
        "</div>";
    }).join("");
  }

  /* ------------------------------------------------------------------ */
  /* Step 4 · Reference Video                                            */
  /* ------------------------------------------------------------------ */
  function switchRefTab(tab) {
    S.refTab = tab === "upload" ? "upload" : "presets";
    renderRefTabs();
    if (S.refTab === "upload") renderVideo();
  }

  function renderRefTabs() {
    var presetView = $("step1-preset-view");
    var uploadView = $("step1-upload-view");
    if (presetView) presetView.classList.toggle("hidden", S.refTab !== "presets");
    if (uploadView) uploadView.classList.toggle("hidden", S.refTab !== "upload");
    var tabPreset = $("tab-preset-video");
    var tabUpload = $("tab-upload-video");
    if (tabPreset) tabPreset.classList.toggle("is-active", S.refTab === "presets");
    if (tabUpload) tabUpload.classList.toggle("is-active", S.refTab === "upload");
  }

  function renderVideo() {
    var preview = $("fashion-video-preview");
    if (!preview) return;
    if (S.referencePending) {
      preview.classList.remove("is-empty");
      preview.innerHTML = '<video src="' + esc(S.referencePending.url) + '" muted playsinline preload="metadata"></video>' +
        '<span class="collart-uploadbadge"><span class="collart-spinner"></span>Uploading…</span>';
    } else if (S.reference.url) {
      preview.classList.remove("is-empty");
      preview.innerHTML = '<video src="' + esc(S.reference.url) + '" muted playsinline preload="metadata"></video>' +
        '<button type="button" class="collart-mini collart-mini-float" data-clear-video title="Remove video">×</button>';
    } else {
      preview.classList.add("is-empty");
      preview.innerHTML = '<span class="collart-dropicon"><i data-lucide="clapperboard" class="w-6 h-6"></i></span>' +
        "<p>Click or drag to upload a local reference video</p><em>MP4 / MOV / WebM · up to 15 seconds</em>" +
        '<span class="collart-btn">Upload Video</span>';
    }
    var meta = $("fashion-video-meta");
    if (meta) {
      meta.textContent = S.reference.url
        ? (S.reference.duration ? S.reference.duration.toFixed(1) + "s · " : "") + (S.reference.name || "Reference clip")
        : "";
    }
  }

  /* ------------------------------------------------------------------ */
  /* Step 5 · Production                                                 */
  /* ------------------------------------------------------------------ */
  function renderTemplateSelects() {
    if (!window.AdsTemplates) return;
    var env = $("fashion-env-template");
    if (env && env.options.length <= 1) {
      env.innerHTML = window.AdsTemplates.optionsHtml(window.AdsTemplates.env);
      env.value = S.envTemplate || "";
    }
    var video = $("fashion-video-template");
    if (video && video.options.length <= 1) {
      video.innerHTML = window.AdsTemplates.optionsHtml(window.AdsTemplates.video);
      video.value = S.videoTemplate || "";
    }
  }

  function renderEngines() {
    var host = $("fashion-engines");
    if (!host) return;
    var models = videoEngines();
    var modeNote = $("fashion-engine-mode");
    if (modeNote) {
      var engine = selectedEngine();
      modeNote.textContent = engine ? "Google Flow engine — " + engine.mode_label : "Google Flow engine (Veo)";
    }
    if (!models.length) {
      host.innerHTML = '<p class="collart-note">Loading free video engines…</p>';
      return;
    }
    var activeId = genEntry("5").model;
    host.innerHTML = models.map(function (model, index) {
      var active = model.id === activeId ? " is-active" : "";
      var letter = (model.label || model.id || "?").charAt(0).toUpperCase();
      var tags = (model.resolutions || []).length
        ? (model.resolutions[0] + "–" + model.resolutions[model.resolutions.length - 1]).toUpperCase()
        : "";
      var chips = [];
      if (tags) chips.push('<span class="collart-engine-tag">' + esc(tags) + "</span>");
      if (!S.freeModels || !S.freeModels.google) {
        chips.push('<span class="collart-engine-tag">Needs free Google key</span>');
      }
      return '<button type="button" class="collart-engine' + active + '" data-engine="' + esc(model.id) + '">' +
        '<span class="collart-engine-head"><span class="collart-engine-avatar">' + esc(letter) + "</span>" +
        "<b>" + esc(model.label || model.id) + "</b></span>" +
        "<p>" + esc(model.mode_label || "text/image → video") +
        (index === 0 ? " — recommended." : ".") + "</p>" +
        '<span class="collart-engine-tags">' + chips.join("") + "</span>" +
        "</button>";
    }).join("");
  }

  function carriedEntries() {
    var entries = [];
    if (S.model.url) {
      entries.push({ label: "Step 1 · Model", type: "image", url: S.model.url, name: S.model.name || "Model" });
    }
    if (S.productSelected.length) {
      entries.push({
        label: "Step 2 · Product/Props", type: "image", url: S.productSelected[0],
        name: S.productSelected[1] ? S.productSelected.length + " images selected" : "1 image selected"
      });
    }
    if (S.dressed.url) {
      entries.push({ label: "Step 3 · Dressed Model", type: "image", url: S.dressed.url, name: S.dressed.name || "Dressed model" });
    }
    if (S.reference.url) {
      entries.push({ label: "Step 4 · Reference Video", type: "video", url: S.reference.url, name: S.reference.name || "Reference clip" });
    }
    return entries;
  }

  function renderCarried() {
    var host = $("fashion-carried");
    if (!host) return;
    var entries = carriedEntries();
    if (!entries.length) {
      host.classList.add("hidden");
      host.innerHTML = "";
      return;
    }
    host.classList.remove("hidden");
    host.innerHTML = '<span class="collart-carried-label"><i data-lucide="layers" class="w-3.5 h-3.5"></i>Selected media for generation</span>' +
      '<div class="collart-carriedrow">' +
      entries.map(function (entry) {
        var media = entry.type === "video"
          ? '<video src="' + esc(entry.url) + '" muted playsinline preload="metadata"></video>'
          : '<img src="' + esc(entry.url) + '" alt="' + esc(entry.label) + '" loading="lazy">';
        return '<span class="collart-carrieditem">' +
          '<span class="collart-carriedmedia">' + media + "</span>" +
          "<b>" + esc(entry.label) + "</b><em>" + esc(entry.name) + "</em></span>";
      }).join("") +
      "</div>";
  }

  /* ------------------------------------------------------------------ */
  /* Result (Generated Video)                                            */
  /* ------------------------------------------------------------------ */
  function stateLabel(job) {
    if (!job) return "Queued";
    if (window.StudioState && window.StudioState.stateLabel) {
      return window.StudioState.stateLabel(job.state);
    }
    return job.state || "Queued";
  }

  function jobOutput(job) {
    if (window.StudioState && window.StudioState.jobOutput) {
      return window.StudioState.jobOutput(job);
    }
    return (job && job.outputs && job.outputs[0]) || null;
  }

  function outputSource(job) {
    var output = jobOutput(job);
    if (!output) return "";
    if (output.media_id && window.StudioState && window.StudioState.mediaUrl) {
      return window.StudioState.mediaUrl(output.media_id, S.token);
    }
    return output.url || "";
  }

  function renderResult() {
    var host = $("fashion-result");
    var body = $("fashion-result-body");
    var title = $("fashion-result-title");
    var actions = $("fashion-result-actions");
    if (!host || !body) return;
    if (!S.job) {
      host.classList.add("hidden");
      if (actions) actions.classList.add("hidden");
      return;
    }
    host.classList.remove("hidden");
    if (title) title.textContent = stateLabel(S.job) + " · " + (S.job.model_label || S.job.model_id || "Ads video");
    var output = outputSource(S.job);
    var hasVideo = !!(output && (S.job.state === "succeeded" || S.job.terminal));
    if (output) {
      body.innerHTML = '<video class="collart-result-video" controls muted loop playsinline src="' +
        esc(output) + '"></video>' +
        '<p class="collart-note">Model image + Product/Props' + (S.reference.url ? " + Reference Video" : "") +
        ' = this generated video. Saved to the Studio Assets library when the provider returns it.</p>';
    } else if (S.job.state === "failed") {
      var message = (S.job.error && (S.job.error.message || S.job.error.code)) || "The provider failed this job.";
      body.innerHTML = '<div class="collart-result-error"><i data-lucide="alert-triangle" class="w-4 h-4"></i><span>' +
        esc(message) + "</span></div>";
    } else if (S.job.terminal && S.job.state === "succeeded") {
      body.innerHTML = '<div class="collart-result-error ok"><i data-lucide="check-circle-2" class="w-4 h-4"></i>' +
        "<span>Completed, but the provider returned no downloadable output.</span></div>";
    } else {
      var elapsed = window.StudioState && window.StudioState.elapsedLabel
        ? window.StudioState.elapsedLabel(S.job.elapsed_seconds)
        : Math.round(S.job.elapsed_seconds || 0) + "s";
      body.innerHTML = '<div class="collart-result-busy"><span class="collart-spinner"></span>' +
        "<div><b>" + esc(stateLabel(S.job)) + "</b><p>" + esc(elapsed) +
        " elapsed. Studio polls the provider; no progress is invented.</p></div></div>";
    }
    if (actions) actions.classList.toggle("hidden", !hasVideo);
    icons();
  }

  /* ------------------------------------------------------------------ */
  /* Bottom dock                                                         */
  /* ------------------------------------------------------------------ */
  function renderDock() {
    var preset = presetById(S.presetId) || {};
    var slots = [
      { ok: !!S.model.url, img: S.model.url, icon: "user-round" },
      { ok: S.productSelected.length > 0, img: S.productSelected[0] || "", icon: "package" },
      { ok: !!S.dressed.url, img: S.dressed.url, icon: "shirt" },
      { ok: !!S.reference.url, img: preset.cover || "", icon: "film" },
      { ok: !!(S.job && S.job.state === "succeeded"), img: "", icon: "clapperboard" }
    ];
    var ready = slots.filter(function (slot) { return slot.ok; }).length;
    var thumbs = $("dock-thumbnails");
    if (thumbs) {
      thumbs.innerHTML = slots.map(function (slot) {
        if (slot.ok && slot.img) {
          return '<span class="collart-dockthumb ready"><img src="' + esc(slot.img) + '" alt=""></span>';
        }
        if (slot.ok) {
          return '<span class="collart-dockthumb ready"><i data-lucide="' + slot.icon + '" class="w-3.5 h-3.5"></i></span>';
        }
        return '<span class="collart-dockthumb"><i data-lucide="' + slot.icon + '" class="w-3.5 h-3.5"></i></span>';
      }).join("");
    }
    var badge = $("ready-badge");
    if (badge) {
      badge.textContent = ready + "/5 ready";
      badge.classList.toggle("all-ready", ready === 5);
    }

    var model = selectedModel();
    var max = model && model.max_images ? model.max_images : 0;
    var images = imageCount();
    var valid = !validateDraft(true);
    var generate = $("generate-btn");
    if (generate) {
      generate.disabled = !valid || S.submitting || S.uploading > 0;
      var span = generate.querySelector("span");
      if (span) {
        span.textContent = S.submitting ? "Submitting…" :
          S.uploading ? "Uploading…" : "Generate Video";
      }
    }
    var status = $("fashion-status");
    if (status && !S.loading && max && images > max) {
      status.textContent = "This engine accepts " + max + " images; the draft needs " + images + ".";
    }
    icons();
  }

  /* ------------------------------------------------------------------ */
  /* Draft actions                                                       */
  /* ------------------------------------------------------------------ */
  function setModel(item) {
    S.model = { url: item.url || "", name: item.name || "" };
    S.modelPresetId = "";
    S.modelPending = null;
    var input = $("fashion-model-url");
    if (input) input.value = "";
    renderModel();
    renderModelGrid();
    renderCarried();
    renderDock();
    renderStatus();
  }

  function setModelPreset(item) {
    S.modelPresetId = item.id;
    S.model = { url: item.cover, name: item.title || item.id };
    var input = $("fashion-model-url");
    if (input) input.value = "";
    syncUI();
  }

  function applyModelEntry(entry) {
    if (!entry) return;
    if (entry.kind === "preset") {
      var preset = findById((S.config && S.config.model_presets) || [], entry.id);
      if (preset) setModelPreset(preset);
      return;
    }
    setModel({ url: entry.cover, name: entry.title });
  }

  function toggleProductSelect(url) {
    if (!url) return;
    var index = S.productSelected.indexOf(url);
    if (index >= 0) S.productSelected.splice(index, 1);
    else S.productSelected.push(url);
    renderProductUploads();
    renderProductResults();
    renderProductPresets();
    renderCarried();
    renderDock();
    renderStatus();
  }

  function removeProductUpload(index) {
    var item = S.productUploads[index];
    if (!item) return;
    S.productUploads.splice(index, 1);
    var selected = S.productSelected.indexOf(item.url);
    if (selected >= 0) S.productSelected.splice(selected, 1);
    renderProductUploads();
    renderProductResults();
    renderProductPresets();
    renderCarried();
    renderDock();
  }

  function discardProductResult(index) {
    var item = S.productResults[index];
    if (!item) return;
    S.productResults.splice(index, 1);
    var selected = S.productSelected.indexOf(item.url);
    if (selected >= 0) S.productSelected.splice(selected, 1);
    renderProductResults();
    renderProductPresets();
    renderCarried();
    renderDock();
  }

  function setDressedFromResult(index) {
    var item = S.dressingResults[index];
    if (!item) return;
    S.dressed = { url: item.url, name: item.name || "Dressed model" };
    renderDressingResults();
    renderCarried();
    renderDock();
    renderStatus();
    toast("Dressed model selected for Production.");
  }

  function discardDressedResult(index) {
    var item = S.dressingResults[index];
    if (!item) return;
    S.dressingResults.splice(index, 1);
    if (S.dressed.url === item.url) S.dressed = { url: "", name: "" };
    renderDressingResults();
    renderCarried();
    renderDock();
  }

  function setReference(item) {
    S.reference = { url: item.url || "", name: item.name || "", duration: item.duration || 0 };
    S.refSkipped = false;
    S.referencePending = null;
    var input = $("fashion-video-url");
    if (input) input.value = "";
    ensureEngineForPool();
    syncUI();
  }

  function clearReference() {
    S.presetId = "";
    S.reference = { url: "", name: "", duration: 0 };
    S.refSkipped = false;
    S.referencePending = null;
    ensureEngineForPool();
    syncUI();
  }

  function skipReference() {
    S.refSkipped = true;
    S.presetId = "";
    S.reference = { url: "", name: "", duration: 0 };
    ensureEngineForPool();
    syncUI();
    goToStep(5);
    toast("Step 4 skipped — Production will generate from your prompt (text/image to video).");
  }

  function selectPreset(item) {
    S.presetId = item.id;
    S.reference = { url: item.video, name: item.title || item.id, duration: item.duration || 0 };
    S.refSkipped = false;
    // Presets were tuned for a specific engine; follow the suggestion.
    if (item.suggested_model_id) {
      var models = (S.config && S.config.models) || [];
      for (var i = 0; i < models.length; i += 1) {
        if (models[i].id === item.suggested_model_id) {
          S.modelId = item.suggested_model_id;
          applyModelDefaults(true);
          break;
        }
      }
    }
    ensureEngineForPool();
    syncUI();
  }

  function isHttp(value) {
    return /^https?:\/\//i.test(String(value || "").trim());
  }

  function nameFromUrl(url) {
    var tail = String(url || "").split("?")[0].split("/").pop();
    return tail || "Remote file";
  }

  /* ------------------------------------------------------------------ */
  /* Prompt composition (Step 5)                                         */
  /* ------------------------------------------------------------------ */
  function composedPrompt() {
    var parts = [];
    var final = (($("fashion-final-prompt") || {}).value || "").trim();
    var story = (($("fashion-storyboard") || {}).value || "").trim();
    if (final) parts.push(final);
    if (story) parts.push("Storyboard: " + story);
    if (window.AdsTemplates) {
      var envText = window.AdsTemplates.textFor(window.AdsTemplates.env, S.envTemplate);
      if (envText) parts.push("Model placement & environment: " + envText);
      var vidText = window.AdsTemplates.textFor(window.AdsTemplates.video, S.videoTemplate);
      if (vidText) parts.push("Video direction: " + vidText);
    }
    return parts.join("\n");
  }

  /* ------------------------------------------------------------------ */
  /* Generate + result                                                   */
  /* ------------------------------------------------------------------ */
  function validateDraft(silent) {
    if (S.loading) {
      if (!silent) toast("Studio is still loading.");
      return { message: "Loading." };
    }
    if (!S.freeModels) {
      if (!silent) toast("Loading free engines…");
      return { message: "Loading." };
    }
    var prompt = composedPrompt();
    if (S.reference.url && !identityUrl() && !prompt) {
      if (!silent) { toast("Motion transfer needs a model — pick one in Step 1."); goToStep(1); }
      return { step: 1, message: "Model missing." };
    }
    if (!identityUrl() && !prompt) {
      if (!silent) { toast("Add a model in Step 1 or write the final prompt in Step 5."); goToStep(1); }
      return { step: 1, message: "Nothing to generate." };
    }
    var gen = genEntry("5");
    if (gen.provider === "higgsfield") {
      if (!higgsfieldConnected()) {
        if (!silent) {
          toast("Higgsfield is not connected — save its API key and secret in Provider Setup.");
          goToStep(5);
        }
        return { step: 5, message: "Higgsfield not connected." };
      }
      var needs = genModelNeeds("higgsfield", "video", gen.model);
      if (needs === "reference_video" && !S.reference.url) {
        if (!silent) { toast("This model transfers motion from a reference video — add one in Step 4, or pick a Text/Image → Video model."); goToStep(4); }
        return { step: 4, message: "Reference video missing." };
      }
      if (needs === "image" && !identityUrl()) {
        if (!silent) { toast("This model animates an image — pick a model image in Step 1 first."); goToStep(1); }
        return { step: 1, message: "Model image missing." };
      }
      return null;
    }
    // Free providers (auto / google / pollinations).
    if (gen.provider === "pollinations") {
      if (!silent) { toast("Pollinations has no video models — pick Google, Auto, or Higgsfield as the Step 5 provider."); goToStep(5); }
      return { step: 5, message: "No video models." };
    }
    if (!S.freeModels.google) {
      if (!silent) {
        flagCredentialProblem();
        toast("Free video generation runs on Google Veo — connect Google in Provider Setup (aistudio.google.com, it's free).");
        goToStep(5);
      }
      return { step: 5, message: "Google key missing." };
    }
    if (S.reference.url && !silent) {
      toast("Note: free engines generate from your prompt and model image — the reference clip is used as storyboard context only.");
    }
    return null;
  }

  function parseDuration(value) {
    if (value == null || value === "") return null;
    var number = parseInt(String(value).replace(/[^0-9]/g, ""), 10);
    return Number.isFinite(number) ? number : null;
  }

  function generate() {
    if (S.submitting) return;
    if (validateDraft()) return;
    S.submitting = true;
    renderDock();
    var gen = genEntry("5");
    var submit;
    var engineLabel = gen.provider;
    if (gen.provider === "higgsfield") {
      engineLabel = "Higgsfield · " + gen.model;
      submit = submitHiggsfieldVideo(gen.model);
    } else {
      var engine = gen.model && gen.model !== "auto" ? gen.model : "veo-3.0-fast-generate-001";
      engineLabel = "Veo (Google Flow engine)";
      submit = api("/api/studio/free/jobs", {
        method: "POST",
        body: {
          kind: "video",
          engine: engine,
          prompt: composedPrompt(),
          image_urls: identityUrl() ? [identityUrl()] : [],
          aspect_ratio: ($("fashion-aspect") && $("fashion-aspect").value) || "9:16",
          duration: parseDuration($("fashion-duration") && $("fashion-duration").value)
        }
      }).then(function (data) {
        var job = (data && data.job) || null;
        if (job) job._free = true;
        return job;
      });
    }
    submit
      .then(function (job) {
        S.job = job;
        S.duplicate = false;
        clearCredentialProblem();
        renderResult();
        startPolling();
        toast("Video queued on " + engineLabel + ".");
      })
      .catch(function (err) {
        toast(friendlyError(err));
      })
      .then(function () {
        S.submitting = false;
        renderDock();
      });
  }

  function startPolling() {
    stopPolling();
    S.pollTimer = window.setInterval(function () {
      if (!S.job || !S.job.id) return stopPolling();
      api(jobEndpoint(S.job)).then(function (job) {
        job._free = !!(S.job && S.job._free);
        S.job = job;
        renderResult();
        renderDock();
        if (job.terminal) stopPolling();
      }).catch(function () { /* keep polling; transient read error */ });
    }, POLL_MS);
  }

  function stopPolling() {
    if (S.pollTimer) window.clearInterval(S.pollTimer);
    S.pollTimer = null;
  }

  function resultAction(action) {
    if (!S.job) return;
    var output = outputSource(S.job);
    if (action === "play") {
      var video = document.querySelector("#fashion-result-body video");
      if (video) {
        video.muted = false;
        var attempt = video.play();
        if (attempt && attempt.catch) attempt.catch(function () { /* autoplay blocked */ });
        video.scrollIntoView({ behavior: "smooth", block: "center" });
      }
      return;
    }
    if (action === "edit") {
      goToStep(5);
      var box = $("fashion-final-prompt");
      if (box) {
        box.focus();
        box.scrollIntoView({ behavior: "smooth", block: "center" });
      }
      return;
    }
    if (action === "library") {
      if (!output) { toast("Nothing to save yet."); return; }
      api("/api/studio/library/import", {
        method: "POST",
        body: {
          url: output,
          kind: "video",
          title: "Ads Studio video",
          provider: currentProvider(),
          model_id: (S.job && S.job.model_id) || ""
        }
      }).then(function () { toast("Saved to the Studio Library."); })
        .catch(function (err) { toast(friendlyError(err)); });
      return;
    }
    if (action === "download") {
      if (!output) { toast("Nothing to download yet."); return; }
      var link = document.createElement("a");
      link.href = output;
      link.download = "ads-studio-video.mp4";
      link.target = "_blank";
      link.rel = "noopener";
      document.body.appendChild(link);
      link.click();
      link.remove();
      return;
    }
    if (action === "delete") {
      var output0 = jobOutput(S.job) || {};
      var mediaId = output0.media_id || "";
      var clear = function () {
        S.job = null;
        renderResult();
        renderDock();
        toast("Result deleted.");
      };
      if (mediaId) {
        api("/api/studio/library/" + encodeURIComponent(mediaId), { method: "DELETE" })
          .then(clear)
          .catch(function (err) { toast(friendlyError(err)); clear(); });
      } else {
        clear();
      }
      return;
    }
  }

  /* ------------------------------------------------------------------ */
  /* Shared image job runner (Step 1 create · Step 2 sheet · Step 3 dress)*/
  /* ------------------------------------------------------------------ */
  function promptAspectRatio(prompt) {
    var match = String(prompt || "").match(/\b(\d{1,2}:\d{1,2})\b/);
    return match ? match[1] : "";
  }

  function createImageJob(prompt, refs, step) {
    /* Route the step's image generation through its selected Provider +
       Model: free engines via /api/studio/free/jobs, Higgsfield catalog
       models via /api/studio/jobs with schema-mapped params. */
    return Promise.all([ensureFreeModels(), ensureRegistry()]).then(function () {
      var gen = genEntry(step || 1);
      var aspect = promptAspectRatio(prompt);
      if (gen.provider === "higgsfield" && gen.model && gen.model !== "auto") {
        return submitHiggsfieldImage(prompt, refs, gen.model, aspect);
      }
      var engine = gen.provider === "google" ? "google-gemini-image"
        : (gen.provider === "pollinations" ? (gen.model !== "auto" ? gen.model : "pollinations-flux")
          : (gen.model !== "auto" ? gen.model : "auto"));
      validateFreeEngine(engine, refs);
      return api("/api/studio/free/jobs", {
        method: "POST",
        body: {
          kind: "image",
          engine: engine,
          prompt: prompt,
          image_urls: refs,
          aspect_ratio: aspect
        }
      }).then(function (data) {
        var job = (data && data.job) || null;
        if (job) job._free = true;
        return job;
      });
    });
  }

  function submitHiggsfieldVideo(modelId) {
    if (!higgsfieldConnected()) {
      return Promise.reject(new Error("Higgsfield is not connected — save its API key and secret in Provider Setup first."));
    }
    var needs = genModelNeeds("higgsfield", "video", modelId);
    if (needs === "reference_video" && !S.reference.url) {
      return Promise.reject(new Error("This model transfers motion from a reference video — add one in Step 4 first, or pick a Text/Image → Video model."));
    }
    if (needs === "image" && !identityUrl()) {
      return Promise.reject(new Error("This model animates an image — pick a model image in Step 1 first."));
    }
    var products = productUrls();
    var draft = {
      preset: S.presetId,
      model_preset: S.modelPresetId,
      model: identityUrl(),
      scene_image: "",
      outfits: products.length ? [{ name: "Product/Props", preset: "", images: products }] : [],
      reference_video: S.reference.url,
      template: "",
      prompt: composedPrompt(),
      resolution: ($("fashion-resolution") && $("fashion-resolution").value) || "",
      duration: parseDuration($("fashion-duration") && $("fashion-duration").value),
      aspect_ratio: ($("fashion-aspect") && $("fashion-aspect").value) || "",
      generate_audio: !!($("fashion-audio") && $("fashion-audio").checked)
    };
    return api("/api/studio/fashion/jobs", {
      method: "POST",
      body: { provider: "higgsfield", model_id: modelId, draft: draft, auto_download: true }
    }).then(function (data) { return (data && data.job) || null; });
  }

  function jobEndpoint(job) {
    if (job && job._free) return "/api/studio/free/jobs/" + encodeURIComponent(job.id);
    return "/api/studio/jobs/" + encodeURIComponent(job.id);
  }

  function watchJob(job, onTick) {
    return new Promise(function (resolve, reject) {
      if (!job || !job.id) {
        reject(new Error("The engine did not return a job."));
        return;
      }
      var timer = window.setInterval(function () {
        api(jobEndpoint(job)).then(function (fresh) {
          fresh._free = !!job._free;
          job = fresh;
          if (onTick) {
            try { onTick(job); } catch (err) { /* rendering must never kill polling */ }
          }
          if (job.terminal) {
            window.clearInterval(timer);
            if (job.state === "succeeded") resolve(job);
            else reject(new Error((job.error && job.error.message) || "Generation failed."));
          }
        }).catch(function () { /* keep polling; transient read error */ });
      }, POLL_MS);
    });
  }

  function jobOutputUrls(job) {
    var outs = (job && job.outputs) || [];
    var urls = [];
    for (var i = 0; i < outs.length; i += 1) {
      var out = outs[i] || {};
      if (out.url) urls.push(out.url);
      else if (out.media_id && window.StudioState && window.StudioState.mediaUrl) {
        urls.push(window.StudioState.mediaUrl(out.media_id, S.token));
      }
    }
    return urls;
  }

  function harvestImages(job, target, fallbackName, presetKind) {
    var urls = jobOutputUrls(job);
    if (!urls.length) {
      toast("The image model returned no output.");
      return;
    }
    clearCredentialProblem();
    var prompt = job.prompt || "";
    urls.forEach(function (url) {
      var item = { url: url, name: fallbackName, prompt: prompt };
      target.unshift(item);
      schedulePresetSave(presetKind, item, "generated");
    });
    syncUI();
  }

  /* ------------------------------------------------------------------ */
  /* Step 1 · Create Model (character sheet)                             */
  /* ------------------------------------------------------------------ */
  function renderCreateRef() {
    var wrap = $("fashion-create-ref");
    if (!wrap) return;
    wrap.classList.toggle("hidden", !S.createRef.url && !S.createRefPending);
    var thumb = $("fashion-create-ref-thumb");
    if (thumb) {
      thumb.src = (S.createRefPending && S.createRefPending.url) || S.createRef.url || "";
    }
    var name = $("fashion-create-ref-name");
    if (name) {
      name.textContent = S.createRefPending
        ? "Uploading " + (S.createRefPending.name || "reference") + "…"
        : (S.createRef.name || "Reference image");
    }
  }

  function renderCreate() {
    renderCreateRef();
    var button = $("fashion-create-generate");
    if (button) {
      button.disabled = S.creating || S.uploading > 0;
      var span = button.querySelector("span");
      if (span) span.textContent = S.creating ? "Starting…" : (S.uploading > 0 ? "Uploading…" : "Generate Model");
    }
  }

  function renderCreateResult() {
    closeModelMenu();
    var host = $("fashion-create-result");
    if (!host) return;
    var job = S.createJob;
    var busy = !!(job && !job.terminal);
    if (!busy && !S.createResults.length) {
      host.classList.add("hidden");
      host.innerHTML = "";
      return;
    }
    host.classList.remove("hidden");
    var html = "";
    if (busy) {
      var elapsed = job.elapsed_seconds ? Math.round(job.elapsed_seconds) + "s elapsed — " : "";
      html += '<div class="collart-createbusy"><span class="collart-spinner"></span>' +
        "<div><b>" + esc(stateLabel(job)) + "</b><p>" + esc(elapsed) + "generating with " +
        esc(job.model_label || job.model_id || "the image model") + ".</p></div></div>";
    }
    html += S.createResults.map(function (result, index) {
      return '<div class="collart-tilewrap">' +
        '<div class="collart-tile is-static' + (S.model.url === result.url ? " is-active" : "") +
        '" data-created-pick="' + index + '" title="Click to select this model">' +
        '<span class="collart-tile-media"><img src="' + esc(result.url) + '" alt="" loading="lazy"></span>' +
        '<span class="collart-tile-badges"><span class="collart-badge custom">GENERATED</span></span>' +
        '<span class="collart-tile-name">' + esc(result.name) + "</span>" +
        "</div>" +
        '<button type="button" class="collart-kebab" data-create-kebab="' + index +
        '" title="Image actions" aria-label="Image actions"><i data-lucide="more-horizontal" class="w-3.5 h-3.5"></i></button>' +
        "</div>";
    }).join("");
    host.innerHTML = html;
    icons();
  }

  function generateModel() {
    if (S.creating) return;
    var prompt = (($("fashion-create-prompt") || {}).value || "").trim();
    if (!prompt) { toast("Describe the model to create — or load Prompt 1."); return; }
    var hasRef = !!S.createRef.url;
    S.creating = true;
    renderCreate();
    var refs = hasRef ? [S.createRef.url] : [];
    createImageJob(prompt, refs, 1).then(function (job) {
      S.createJob = job;
      renderCreateResult();
      return watchJob(job, function (fresh) { S.createJob = fresh; renderCreateResult(); })
        .then(function (done) {
          S.createJob = null;
          harvestImages(done, S.createResults, "Created model", "model");
          toast("Model created — click an image to select it, or use the ⋯ menu to add it to the presets.");
        });
    }).catch(function (err) {
      toast(friendlyError(err));
      S.createJob = null;
      renderCreateResult();
    }).then(function () {
      S.creating = false;
      renderCreate();
      renderCreateResult();
    });
  }

  function addGeneratedToPresets(index) {
    var result = S.createResults[index];
    if (!result) return;
    askConfirm({
      title: "Add to Preset Models?",
      message: "The generated image will be saved to your preset models and can be picked in Step 1 from now on.",
      confirmLabel: "Add to presets"
    }).then(function (ok) {
      if (!ok) return;
      saveToLibrary(result.url, result.name, "generated", result.prompt, "model").then(function () {
        toast("Saved to Preset Models.");
        switchModelTab("preset");
      });
    });
  }

  function selectCreatedModel(index) {
    var result = S.createResults[index];
    if (!result) return;
    setModel({ url: result.url, name: result.name });
    toast("Model selected — continue in Product/Props (Step 2).");
  }

  function discardCreatedModel(index) {
    var result = S.createResults[index];
    if (!result) return;
    askConfirm({
      title: "Discard this image?",
      message: "The generated image will be removed from this list. Images already added to the presets stay there.",
      confirmLabel: "Discard",
      danger: true
    }).then(function (ok) {
      if (!ok) return;
      S.createResults.splice(index, 1);
      renderCreateResult();
    });
  }

  /* ------------------------------------------------------------------ */
  /* Step 2 · Product uploads + product sheet                            */
  /* ------------------------------------------------------------------ */
  function addProductFiles(files) {
    var list = Array.prototype.slice.call(files || []);
    if (!list.length) return;
    /* Show every file instantly from its local blob, then upload in
       parallel; each thumb flips to its stored URL as it lands. */
    var slots = list.map(function (file) {
      var slot = { url: localPreviewUrl(file), name: file.name, pending: true };
      S.productUploads.unshift(slot);
      return { slot: slot, file: file };
    });
    renderProductUploads();
    renderDock();
    Promise.allSettled(slots.map(function (entry) {
      return uploadFile(entry.file).then(function (item) {
        var wasSelected = S.productSelected.indexOf(entry.slot.url) !== -1;
        if (wasSelected) S.productSelected.splice(S.productSelected.indexOf(entry.slot.url), 1);
        entry.slot.url = item.url;
        entry.slot.name = item.name;
        delete entry.slot.pending;
        if (S.productSelected.indexOf(item.url) === -1) S.productSelected.push(item.url);
        schedulePresetSave("product", item, "upload");
        renderProductUploads();
        renderCarried();
        renderDock();
      });
    })).then(function (results) {
      results.forEach(function (result, index) {
        if (result.status === "rejected") {
          var at = S.productUploads.indexOf(slots[index].slot);
          if (at >= 0) S.productUploads.splice(at, 1);
          toast(friendlyError(results[index].reason));
        }
      });
      renderProductUploads();
      renderProductPresets();
      renderCarried();
      renderDock();
      renderStatus();
    });
  }

  function addProductUrl() {
    var input = $("fashion-product-url");
    var url = ((input && input.value) || "").trim();
    if (!isHttp(url)) { toast("Enter a public http(s) image URL."); return; }
    var item = { url: url, name: nameFromUrl(url) };
    S.productUploads.unshift(item);
    if (S.productSelected.indexOf(item.url) === -1) S.productSelected.push(item.url);
    schedulePresetSave("product", item, "url");
    if (input) input.value = "";
    syncUI();
    toast("Product image added and selected.");
  }

  function generateProductSheet() {
    if (S.productCreating) return;
    var prompt = (($("fashion-product-prompt") || {}).value || "").trim() || PROMPT_2;
    var refs = S.productUploads.map(function (item) { return item.url; });
    if (!refs.length) refs = productUrls();
    if (!refs.length) { toast("Upload product/props images first (left panel)."); return; }
    S.productCreating = true;
    syncUI();
    createImageJob(prompt, refs, 2).then(function (job) {
      return watchJob(job).then(function (done) {
        harvestImages(done, S.productResults, "Product sheet", "product");
        toast("Product sheet created — click it to select it for the next steps.");
      });
    }).catch(function (err) { toast(friendlyError(err)); }).then(function () {
      S.productCreating = false;
      syncUI();
    });
  }

  /* ------------------------------------------------------------------ */
  /* Step 3 · Dressing                                                   */
  /* ------------------------------------------------------------------ */
  function generateDressing() {
    if (S.dressingCreating) return;
    if (!identityUrl()) {
      toast("Pick a model in Step 1 first.");
      goToStep(1);
      return;
    }
    var prompt = (($("fashion-dressing-prompt") || {}).value || "").trim() || PROMPT_3;
    var refs = [identityUrl()].concat(productUrls());
    refs = refs.filter(function (url, index) { return refs.indexOf(url) === index; });
    S.dressingCreating = true;
    syncUI();
    createImageJob(prompt, refs, 3).then(function (job) {
      return watchJob(job).then(function (done) {
        var urls = jobOutputUrls(done);
        if (!urls.length) {
          toast("The image model returned no output.");
          return;
        }
        urls.forEach(function (url) {
          var item = { url: url, name: "Dressed model", prompt: done.prompt || prompt };
          S.dressingResults.unshift(item);
          schedulePresetSave("dressed", item, "generated");
        });
        S.dressed = { url: S.dressingResults[0].url, name: S.dressingResults[0].name };
        toast("Dressed model selected — click another result to switch.");
      });
    }).catch(function (err) { toast(friendlyError(err)); }).then(function () {
      S.dressingCreating = false;
      syncUI();
    });
  }

  /* ------------------------------------------------------------------ */
  /* Per-image action menu (⋯) + confirmation modal                      */
  /* ------------------------------------------------------------------ */
  var confirmState = null;

  function closeModelMenu() {
    var menu = $("fashion-kebab-menu");
    if (!menu) return;
    menu.classList.add("hidden");
    menu.innerHTML = "";
    var host = $("studio-panel-fashion");
    if (host && menu.parentElement !== host) host.appendChild(menu); // park it where re-renders can't destroy it
  }

  function openKebabMenu(container, items) {
    var menu = $("fashion-kebab-menu");
    if (!menu || !container) return;
    closeModelMenu();
    menu.innerHTML = items.map(function (item) {
      return '<button type="button" class="collart-kebabmenu-item' + (item.danger ? " danger" : "") +
        '" data-model-action="' + esc(item.action) + '" data-model-key="' + esc(item.key || "") + '" role="menuitem">' +
        '<i data-lucide="' + esc(item.icon || "circle-dot") + '" class="w-3.5 h-3.5"></i>' + esc(item.label) + "</button>";
    }).join("");
    container.appendChild(menu);
    menu.classList.remove("hidden");
    icons();
  }

  function toggleModelMenu(key, anchor) {
    if (S.menuKey === key && !$("fashion-kebab-menu").classList.contains("hidden")) { closeModelMenu(); return; }
    var entry = findModelEntry(key);
    if (!entry) return;
    S.menuKey = key;
    var container = anchor.closest(".collart-tilewrap");
    openKebabMenu(container, [
      { action: "select", key: key, label: "Select for next step", icon: "check" },
      {
        action: "delete", key: key, icon: "trash-2", danger: true,
        label: entry.kind === "custom" ? "Delete model" : "Remove preset"
      }
    ]);
  }

  function toggleCreateMenu(index, anchor) {
    var menuKey = "created:" + index;
    if (S.menuKey === menuKey && !$("fashion-kebab-menu").classList.contains("hidden")) { closeModelMenu(); return; }
    if (!S.createResults[index]) return;
    S.menuKey = menuKey;
    var container = anchor.closest(".collart-tilewrap");
    openKebabMenu(container, [
      { action: "add-generated", key: String(index), label: "Add to Preset Models", icon: "folder-plus" },
      { action: "select-generated", key: String(index), label: "Select for next step", icon: "check" },
      { action: "discard-generated", key: String(index), label: "Discard image", icon: "trash-2", danger: true }
    ]);
  }

  function runModelAction(action, key) {
    closeModelMenu();
    if (action === "select") { selectModelForNext(findModelEntry(key)); return; }
    if (action === "delete") { deleteModelEntry(findModelEntry(key)); return; }
    if (action === "add-generated") { addGeneratedToPresets(Number(key)); return; }
    if (action === "select-generated") { selectCreatedModel(Number(key)); return; }
    if (action === "discard-generated") { discardCreatedModel(Number(key)); return; }
  }

  function askConfirm(options) {
    var modal = $("fashion-modal");
    if (!modal) return Promise.resolve(true);
    if (confirmState) resolveConfirm(false);
    var title = $("fashion-modal-title");
    var message = $("fashion-modal-message");
    var confirm = $("fashion-modal-confirm");
    if (title) title.textContent = options.title || "Are you sure?";
    if (message) message.textContent = options.message || "";
    if (confirm) {
      confirm.textContent = options.confirmLabel || "Confirm";
      confirm.classList.toggle("danger", !!options.danger);
    }
    modal.classList.remove("hidden");
    return new Promise(function (resolve) {
      confirmState = { resolve: resolve };
    });
  }

  function resolveConfirm(result) {
    if (!confirmState) return;
    var resolve = confirmState.resolve;
    confirmState = null;
    var modal = $("fashion-modal");
    if (modal) modal.classList.add("hidden");
    resolve(result);
  }

  function selectModelForNext(entry) {
    if (!entry) return;
    askConfirm({
      title: "Use this model for the next step?",
      message: '"' + (entry.title || "Model") + '" will be the model in Product/Props.',
      confirmLabel: "Use model"
    }).then(function (ok) {
      if (!ok) return;
      applyModelEntry(entry);
      goToStep(2);
      toast("Model selected — continue in Product/Props.");
    });
  }

  function deleteModelEntry(entry) {
    if (!entry) return;
    var isCustom = entry.kind === "custom";
    askConfirm({
      title: isCustom ? "Delete this model?" : "Remove this preset model?",
      message: isCustom
        ? '"' + entry.title + '" will be removed from your model library.'
        : '"' + entry.title + '" will be removed from the preset picker.',
      confirmLabel: "Delete",
      danger: true
    }).then(function (ok) {
      if (!ok) return;
      if (isCustom) {
        api("/api/studio/fashion/model-library/" + encodeURIComponent(entry.id), { method: "DELETE" })
          .then(function () { return loadLibrary(true); })
          .then(function () {
            if (S.model.url === entry.cover) setModel({ url: "", name: "" });
            toast("Model deleted.");
          })
          .catch(function (err) { toast(friendlyError(err)); });
      } else {
        api("/api/studio/fashion/model-library/hide", {
          method: "POST",
          body: { preset_id: entry.id, hidden: true }
        }).then(function () { return loadLibrary(true); })
          .then(function () { toast("Preset model removed."); })
          .catch(function (err) { toast(friendlyError(err)); });
      }
    });
  }

  /* ------------------------------------------------------------------ */
  /* Assets dashboard (Creations / Uploads)                              */
  /* ------------------------------------------------------------------ */
  function fetchAssets(role) {
    return api("/api/studio/library?role=" + role + "&limit=60").then(function (data) {
      return (data && data.items) || [];
    }).catch(function () { return []; });
  }

  function showAssets() {
    if (!S.initialized) init();
    S.assetsLoading = true;
    renderAssets();
    Promise.all([fetchAssets("output"), fetchAssets("input")]).then(function (results) {
      S.assetsItems = { output: results[0], input: results[1] };
      S.assetsLoading = false;
      renderAssets();
    });
  }

  function mediaHref(item) {
    if (item.id && window.StudioState && window.StudioState.mediaUrl) {
      return window.StudioState.mediaUrl(item.id, S.token);
    }
    return item.url || item.remote_url || "";
  }

  function assetMatchesTag(item) {
    if (S.assetTag === "all") return true;
    if (S.assetTag === "videos") return item.kind === "video";
    return item.kind === "image";
  }

  function renderAssetGrid(items) {
    return items.map(function (item) {
      var href = mediaHref(item);
      var label = item.title || item.filename || item.id;
      var media = item.kind === "video"
        ? '<video src="' + esc(href) + '" muted playsinline preload="metadata"></video>'
        : '<img src="' + esc(href) + '" alt="" loading="lazy">';
      return '<a class="collart-assetcard" href="' + esc(href) + '" target="_blank" rel="noopener noreferrer" title="' + esc(label) + '">' +
        media +
        '<span class="collart-assetlabel">' + esc(label) + "</span>" +
        "</a>";
    }).join("");
  }

  function renderAssets() {
    var creations = (S.assetsItems.output || []).filter(assetMatchesTag);
    var uploads = (S.assetsItems.input || []).filter(assetMatchesTag);

    var tabCreations = $("assets-tab-creations");
    var tabUploads = $("assets-tab-uploads");
    if (tabCreations) tabCreations.classList.toggle("is-active", S.assetsTab === "creations");
    if (tabUploads) tabUploads.classList.toggle("is-active", S.assetsTab === "uploads");

    Array.prototype.forEach.call(document.querySelectorAll("[data-asset-tag]"), function (button) {
      button.classList.toggle("is-active", button.getAttribute("data-asset-tag") === S.assetTag);
    });

    var creationsContent = $("assets-creations-content");
    var uploadsContent = $("assets-uploads-content");
    if (creationsContent) creationsContent.classList.toggle("hidden", S.assetsTab !== "creations");
    if (uploadsContent) uploadsContent.classList.toggle("hidden", S.assetsTab !== "uploads");

    var creationsEmpty = $("assets-creations-empty");
    var creationsGrid = $("assets-creations-grid");
    if (creationsEmpty) {
      creationsEmpty.classList.toggle("hidden", S.assetsLoading || creations.length > 0);
      var heading = creationsEmpty.querySelector("h2");
      if (heading && S.assetsLoading) heading.textContent = "Loading your creations…";
    }
    if (creationsGrid) {
      creationsGrid.classList.toggle("hidden", creations.length === 0);
      creationsGrid.innerHTML = renderAssetGrid(creations);
    }

    var uploadsEmpty = $("assets-uploads-empty");
    var uploadsGrid = $("assets-uploads-grid");
    if (uploadsEmpty) {
      uploadsEmpty.classList.toggle("hidden", S.assetsLoading || uploads.length > 0);
      var uploadsHeading = uploadsEmpty.querySelector("h2");
      if (uploadsHeading && S.assetsLoading) uploadsHeading.textContent = "Loading your uploads…";
    }
    if (uploadsGrid) {
      uploadsGrid.classList.toggle("hidden", uploads.length === 0);
      uploadsGrid.innerHTML = renderAssetGrid(uploads);
    }
    icons();
  }

  function switchAssetsTab(tab) {
    S.assetsTab = tab === "uploads" ? "uploads" : "creations";
    renderAssets();
  }

  function filterAssetsTag(tag) {
    S.assetTag = tag || "all";
    renderAssets();
  }

  /* ------------------------------------------------------------------ */
  /* Preset Video browse page (default preview page)                     */
  /* ------------------------------------------------------------------ */
  function showPresetPage() {
    if (!S.initialized) init();
    var grid = $("preset-page-grid");
    if (!grid) return;
    var videos = (S.config && S.config.presets) || [];
    if (!videos.length) {
      grid.innerHTML = '<p class="collart-note">No presets available yet.</p>';
      return;
    }
    grid.innerHTML = videos.map(function (item) {
      var active = item.id === S.presetId ? " is-active" : "";
      var badge = item.vip
        ? '<span class="collart-badge brand">VIP</span>'
        : '<span class="collart-badge orange">FREE TRIAL</span>';
      return '<button type="button" class="collart-tile' + active + '" data-page-preset-id="' +
        esc(item.id) + '" title="' + esc(item.title) + '">' +
        '<span class="collart-tile-media">' +
        (item.cover ? '<img src="' + esc(item.cover) + '" alt="" loading="lazy">' : "") +
        "</span>" +
        '<span class="collart-tile-badges">' + badge +
        '<span class="collart-badge dark">' + item.outfits + " Outfit" + (item.outfits > 1 ? "s" : "") + "</span></span>" +
        '<span class="collart-tile-name">' + esc(item.title) + "</span>" +
        "</button>";
    }).join("");
    icons();
  }

  function presetPageSelect(id) {
    var preset = presetById(id);
    if (!preset) return;
    selectPreset(preset);
    toast("Preset " + preset.id + " selected — continue in Reference Video (Step 4).");
    if (window.Studio && window.Studio.setTab) window.Studio.setTab("fashion");
  }

  /* ------------------------------------------------------------------ */
  /* Small utils                                                         */
  /* ------------------------------------------------------------------ */
  function findById(items, id) {
    for (var i = 0; i < items.length; i += 1) {
      if (items[i].id === id) return items[i];
    }
    return null;
  }

  function loadToken() {
    return api("/api/auth/session").then(function (data) {
      S.token = (data && data.token) || null;
    }).catch(function () { S.token = null; });
  }

  function seedPrompt(id, text) {
    var box = $(id);
    if (!box) return;
    box.value = text;
    box.focus();
  }

  /* ------------------------------------------------------------------ */
  /* Events                                                              */
  /* ------------------------------------------------------------------ */
  function bind() {
    // The stepper chips and the bottom dock (Previous / Next / Generate) sit
    // outside #studio-panel-fashion, so delegated clicks must be bound to the
    // whole Studio view -- otherwise "Next" never advances the workflow.
    var root = $("studio-view") || $("studio-panel-fashion");
    if (!root) return;

    var assetsPanel = $("studio-panel-assets");
    if (assetsPanel) {
      assetsPanel.addEventListener("click", function (event) {
        var tab = event.target.closest("[data-assets-tab]");
        if (tab) { switchAssetsTab(tab.getAttribute("data-assets-tab")); return; }
        var tag = event.target.closest("[data-asset-tag]");
        if (tag) { filterAssetsTag(tag.getAttribute("data-asset-tag")); return; }
      });
    }

    var presetPanel = $("studio-panel-preset");
    if (presetPanel) {
      presetPanel.addEventListener("click", function (event) {
        var tile = event.target.closest("[data-page-preset-id]");
        if (tile) { presetPageSelect(tile.getAttribute("data-page-preset-id")); }
      });
    }

    var modal = $("fashion-modal");
    if (modal) {
      modal.addEventListener("click", function (event) {
        if (event.target === modal) resolveConfirm(false);
      });
      var cancelBtn = $("fashion-modal-cancel");
      if (cancelBtn) cancelBtn.addEventListener("click", function () { resolveConfirm(false); });
      var confirmBtn = $("fashion-modal-confirm");
      if (confirmBtn) confirmBtn.addEventListener("click", function () { resolveConfirm(true); });
    }

    document.addEventListener("click", function (event) {
      var menu = $("fashion-kebab-menu");
      if (menu && !menu.classList.contains("hidden") && !menu.contains(event.target)) closeModelMenu();
    });
    document.addEventListener("keydown", function (event) {
      if (event.key !== "Escape") return;
      closeModelMenu();
      resolveConfirm(false);
    });

    root.addEventListener("click", function (event) {
      var jump = event.target.closest("[data-step-jump]");
      if (jump) { goToStep(jump.getAttribute("data-step-jump")); return; }
      var dock = event.target.closest("[data-dock]");
      if (dock) {
        var action = dock.getAttribute("data-dock");
        if (action === "prev") prevStep();
        else if (action === "next") nextStep();
        else if (action === "generate") generate();
        else if (action === "review") goToStep(nextIncomplete() || S.step);
        return;
      }
      var modelTab = event.target.closest("[data-model-tab]");
      if (modelTab) { switchModelTab(modelTab.getAttribute("data-model-tab")); return; }
      var refTab = event.target.closest("[data-ref-tab]");
      if (refTab) { switchRefTab(refTab.getAttribute("data-ref-tab")); return; }
      var presetCard = event.target.closest("[data-preset-id]");
      if (presetCard) {
        var preset = presetById(presetCard.getAttribute("data-preset-id"));
        if (preset) selectPreset(preset);
        return;
      }
      var kebab = event.target.closest("[data-model-kebab]");
      if (kebab) {
        event.stopPropagation();
        toggleModelMenu(kebab.getAttribute("data-model-kebab"), kebab);
        return;
      }
      var modelAction = event.target.closest("[data-model-action]");
      if (modelAction) {
        runModelAction(modelAction.getAttribute("data-model-action"), modelAction.getAttribute("data-model-key"));
        return;
      }
      var createKebab = event.target.closest("[data-create-kebab]");
      if (createKebab) {
        event.stopPropagation();
        toggleCreateMenu(Number(createKebab.getAttribute("data-create-kebab")), createKebab);
        return;
      }
      var createdPick = event.target.closest("[data-created-pick]");
      if (createdPick) { selectCreatedModel(Number(createdPick.getAttribute("data-created-pick"))); return; }
      var modelCard = event.target.closest("[data-model-preset-key]");
      if (modelCard) {
        var entry = findModelEntry(modelCard.getAttribute("data-model-preset-key"));
        if (entry) applyModelEntry(entry);
        return;
      }
      var productPick = event.target.closest("[data-product-pick]");
      if (productPick) { toggleProductSelect(productPick.getAttribute("data-product-pick")); return; }
      var productUploadRemove = event.target.closest("[data-product-upload-remove]");
      if (productUploadRemove) {
        event.stopPropagation();
        removeProductUpload(Number(productUploadRemove.getAttribute("data-product-upload-remove")));
        return;
      }
      var productResultRemove = event.target.closest("[data-product-result-remove]");
      if (productResultRemove) {
        event.stopPropagation();
        discardProductResult(Number(productResultRemove.getAttribute("data-product-result-remove")));
        return;
      }
      var dressingPick = event.target.closest("[data-dressing-pick]");
      if (dressingPick) { setDressedFromResult(Number(dressingPick.getAttribute("data-dressing-pick"))); return; }
      var dressingDiscard = event.target.closest("[data-dressing-discard]");
      if (dressingDiscard) {
        event.stopPropagation();
        discardDressedResult(Number(dressingDiscard.getAttribute("data-dressing-discard")));
        return;
      }
      var engine = event.target.closest("[data-engine]");
      if (engine) {
        if (S.gen["5"]) S.gen["5"].model = engine.getAttribute("data-engine");
        S.modelId = engine.getAttribute("data-engine");
        applyModelDefaults(true);
        renderGenSelects();
        renderEngines();
        renderDock();
        return;
      }
      var resultAction = event.target.closest("[data-result-action]");
      if (resultAction) { resultAction(resultAction.getAttribute("data-result-action")); return; }
      if (event.target.closest("[data-ref-skip]")) { skipReference(); return; }
      if (event.target.closest("[data-dressing-skip]")) {
        S.dressed = { url: "", name: "" };
        syncUI();
        goToStep(4);
        toast("Dressing skipped — the Step 1 model is used as-is.");
        return;
      }
      if (event.target.closest("[data-clear-model]")) {
        S.model = { url: "", name: "" };
        S.modelPresetId = "";
        ensureEngineForPool();
        syncUI();
        return;
      }
      if (event.target.closest("[data-clear-video]")) { clearReference(); return; }
      if (event.target.closest("#fashion-prompt1-seed")) { seedPrompt("fashion-create-prompt", PROMPT_1); return; }
      if (event.target.closest("#fashion-prompt2-seed")) { seedPrompt("fashion-product-prompt", PROMPT_2); return; }
      if (event.target.closest("#fashion-prompt3-seed")) { seedPrompt("fashion-dressing-prompt", PROMPT_3); return; }
      if (event.target.closest("#fashion-product-generate")) { generateProductSheet(); return; }
      if (event.target.closest("#fashion-dressing-generate")) { generateDressing(); return; }
      if (event.target.closest("#fashion-create-generate")) { generateModel(); return; }
      if (event.target.closest("#fashion-create-ref-btn")) {
        var refInput = $("fashion-create-ref-file");
        if (refInput) refInput.click();
        return;
      }
      if (event.target.closest("#fashion-create-ref-clear")) {
        S.createRef = { url: "", name: "" };
        renderCreateRef();
        return;
      }
      if (event.target.closest("#fashion-product-url-apply")) { addProductUrl(); return; }
      if (event.target.closest("#fashion-product-drop")) {
        var productInput = $("fashion-product-file");
        if (productInput) productInput.click();
        return;
      }
      if (event.target.closest("#fashion-result-open") && S.job) {
        if (window.Studio && window.Studio.openJob) window.Studio.openJob(S.job.id);
        else toast("Open History from the sidebar to see this job.");
        return;
      }
      if (event.target.closest("#fashion-model-url-apply")) {
        var modelUrl = ($("fashion-model-url") && $("fashion-model-url").value || "").trim();
        if (!isHttp(modelUrl)) { toast("Enter a public http(s) image URL."); return; }
        var modelItem = { url: modelUrl, name: nameFromUrl(modelUrl) };
        setModel(modelItem);
        saveToLibrary(modelUrl, modelItem.name, "url", "", "model");
        return;
      }
      if (event.target.closest("#fashion-video-url-apply")) {
        var videoUrl = ($("fashion-video-url") && $("fashion-video-url").value || "").trim();
        if (!isHttp(videoUrl)) { toast("Enter a public http(s) video URL."); return; }
        S.presetId = "";
        setReference({ url: videoUrl, name: nameFromUrl(videoUrl) });
        return;
      }
      if (event.target.closest("#fashion-model-drop")) {
        var modelInput = $("fashion-model-file");
        if (modelInput) modelInput.click();
      }
    });

    root.addEventListener("change", function (event) {
      var input = event.target;
      if (input.id === "fashion-filter-engine") { S.filters.engine = input.value; renderVideoGrid(); return; }
      if (input.id === "fashion-filter-type") { S.filters.type = input.value; renderVideoGrid(); return; }
      if (input.id === "fashion-filter-style") { S.filters.style = input.value; renderVideoGrid(); return; }
      if (input.id === "fashion-filter-model") { S.filters.model = input.value; renderVideoGrid(); return; }
      if (input.id === "fashion-model-filter") { S.modelFilter = input.value; renderModelGrid(); return; }
      if (input.id && input.id.indexOf("fashion-gen-provider-") === 0) {
        var providerStep = input.id.replace("fashion-gen-provider-", "");
        if (S.gen[providerStep]) {
          S.gen[providerStep].provider = input.value;
          var models = genCatalogModels(input.value, genKind(providerStep));
          S.gen[providerStep].model = models.length ? models[0].id : "auto";
          renderGenSelects();
        }
        return;
      }
      if (input.id && input.id.indexOf("fashion-gen-model-") === 0) {
        var modelStep = input.id.replace("fashion-gen-model-", "");
        if (S.gen[modelStep]) {
          S.gen[modelStep].model = input.value;
          renderStatus();
        }
        return;
      }
      if (input.id === "fashion-env-template") { S.envTemplate = input.value; return; }
      if (input.id === "fashion-video-template") { S.videoTemplate = input.value; return; }
      if (input.id === "fashion-model-file") {
        var modelFile = input.files && input.files[0];
        input.value = "";
        if (!modelFile) return;
        S.modelPending = { url: localPreviewUrl(modelFile), name: modelFile.name };
        renderModel();
        uploadFile(modelFile).then(function (item) {
          setModel(item);
          saveToLibrary(item.url, item.name, "upload", "", "model");
          schedulePresetSave("model", item, "upload");
          toast("Model added to the presets.");
        }).catch(function (err) {
          S.modelPending = null;
          renderModel();
          toast(friendlyError(err));
        });
        return;
      }
      if (input.id === "fashion-product-file") {
        var productFiles = Array.prototype.slice.call(input.files || []);
        input.value = "";
        addProductFiles(productFiles);
        return;
      }
      if (input.id === "fashion-create-ref-file") {
        var refFile = input.files && input.files[0];
        input.value = "";
        if (!refFile) return;
        S.createRefPending = { url: localPreviewUrl(refFile), name: refFile.name };
        renderCreateRef();
        uploadFile(refFile).then(function (item) {
          S.createRef = { url: item.url, name: item.name || refFile.name };
          S.createRefPending = null;
          renderCreateRef();
        }).catch(function (err) {
          S.createRefPending = null;
          renderCreateRef();
          toast(friendlyError(err));
        });
        return;
      }
      if (input.id === "fashion-video-file") {
        var videoFile = input.files && input.files[0];
        input.value = "";
        if (!videoFile) return;
        videoDuration(videoFile).then(function (duration) {
          var limit = limits().reference_seconds;
          if (duration && duration > limit + 0.5) {
            throw new Error("The reference video is longer than " + limit + " seconds.");
          }
          S.referencePending = { url: localPreviewUrl(videoFile), name: videoFile.name };
          renderVideo();
          return uploadFile(videoFile).then(function (item) {
            item.duration = duration || 0;
            S.presetId = "";
            setReference(item);
            toast("Reference video uploaded.");
          }).catch(function (err) {
            S.referencePending = null;
            renderVideo();
            throw err;
          });
        }).catch(function (err) { toast(friendlyError(err)); });
        return;
      }
      if (input.id === "fashion-model-select") {
        S.modelId = input.value;
        if (S.gen["5"]) S.gen["5"].model = input.value;
        applyModelDefaults(true);
        renderGenSelects();
        renderDock();
        return;
      }
    });

    var videoDrop = $("fashion-video-drop");
    if (videoDrop) {
      videoDrop.addEventListener("dragover", function (event) {
        event.preventDefault();
        videoDrop.classList.add("is-drag");
      });
      videoDrop.addEventListener("dragleave", function () { videoDrop.classList.remove("is-drag"); });
      videoDrop.addEventListener("drop", function (event) {
        event.preventDefault();
        videoDrop.classList.remove("is-drag");
        var file = event.dataTransfer && event.dataTransfer.files && event.dataTransfer.files[0];
        if (!file) return;
        videoDuration(file).then(function (duration) {
          var limit = limits().reference_seconds;
          if (duration && duration > limit + 0.5) throw new Error("The reference video is longer than " + limit + " seconds.");
          S.referencePending = { url: localPreviewUrl(file), name: file.name };
          renderVideo();
          return uploadFile(file).then(function (item) {
            item.duration = duration || 0;
            S.presetId = "";
            setReference(item);
          }).catch(function (err) {
            S.referencePending = null;
            renderVideo();
            throw err;
          });
        }).catch(function (err) { toast(friendlyError(err)); });
      });
      videoDrop.addEventListener("keydown", function (event) {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          var input = $("fashion-video-file");
          if (input) input.click();
        }
      });
    }

    var modelDrop = $("fashion-model-drop");
    if (modelDrop) {
      modelDrop.addEventListener("dragover", function (event) {
        event.preventDefault();
        modelDrop.classList.add("is-drag");
      });
      modelDrop.addEventListener("dragleave", function () { modelDrop.classList.remove("is-drag"); });
      modelDrop.addEventListener("drop", function (event) {
        event.preventDefault();
        modelDrop.classList.remove("is-drag");
        var file = event.dataTransfer && event.dataTransfer.files && event.dataTransfer.files[0];
        if (!file) return;
        S.modelPending = { url: localPreviewUrl(file), name: file.name };
        renderModel();
        uploadFile(file).then(function (item) {
          setModel(item);
          saveToLibrary(item.url, item.name, "upload", "", "model");
          schedulePresetSave("model", item, "upload");
        }).catch(function (err) {
          S.modelPending = null;
          renderModel();
          toast(friendlyError(err));
        });
      });
      modelDrop.addEventListener("keydown", function (event) {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          var input = $("fashion-model-file");
          if (input) input.click();
        }
      });
    }

    var productDrop = $("fashion-product-drop");
    if (productDrop) {
      productDrop.addEventListener("dragover", function (event) {
        event.preventDefault();
        productDrop.classList.add("is-drag");
      });
      productDrop.addEventListener("dragleave", function () { productDrop.classList.remove("is-drag"); });
      productDrop.addEventListener("drop", function (event) {
        event.preventDefault();
        productDrop.classList.remove("is-drag");
        var files = event.dataTransfer && event.dataTransfer.files;
        if (files && files.length) addProductFiles(Array.prototype.slice.call(files));
      });
      productDrop.addEventListener("keydown", function (event) {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          var input = $("fashion-product-file");
          if (input) input.click();
        }
      });
    }
  }

  /* ------------------------------------------------------------------ */
  /* Lifecycle                                                           */
  /* ------------------------------------------------------------------ */
  function show() {
    if (!S.initialized) init();
    if (!S.config && !S.loading) loadConfig();
    ensureFreeModels(false);
    ensureRegistry(false);
  }

  function refresh() {
    if (!S.initialized) { init(); return; }
    S.config = null;
    loadConfig();
    ensureFreeModels(true);
    ensureRegistry(true);
  }

  function refreshFreeModels() {
    if (!S.initialized) return;
    ensureFreeModels(true);
  }

  function init() {
    if (S.initialized) return;
    if (!$("studio-panel-fashion")) return;
    S.initialized = true;
    bind();
    loadToken();
    loadConfig();
    ensureFreeModels(false);
    ensureRegistry(false);
    loadLibrary(false);
    goToStep(1);
  }

  window.StudioFashion = {
    init: init,
    show: show,
    refresh: refresh,
    refreshFreeModels: refreshFreeModels,
    refreshRegistry: refreshRegistry,
    showAssets: showAssets,
    showPresetPage: showPresetPage,
    state: S
  };

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
