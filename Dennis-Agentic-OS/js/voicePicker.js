// =========================================================================
// DENNIS VOICE PICKER — shared Provider → Model → Voice selector.
//
// One component powers the JARVIS dashboard bottom bar and all three studios
// (Apollo, Ads, YouTube). Data comes from the centralized TTS registry:
//   GET  /api/tts/catalog   free-first providers/models + live Kokoro voices
//   GET  /api/tts/config    saved global + per-studio selection
//   POST /api/tts/config    confirmed write (Step 1: confirmation + audit)
//
// No credentials live in this file. The ElevenLabs key input (dashboard
// registry section) posts straight to the backend and is never echoed back.
// =========================================================================

(function () {
  'use strict';

  var COST_LABEL = {
    'free': 'FREE',
    'free-tier': 'FREE-TIER',
    'paid': 'PAID',
    'deprecated': 'DEPRECATED'
  };
  var COST_CLASS = {
    'free': 'vp-badge-free',
    'free-tier': 'vp-badge-freetier',
    'paid': 'vp-badge-paid',
    'deprecated': 'vp-badge-deprecated'
  };
  var SCOPE_LABEL = {
    global: 'Global default',
    apollo: 'Apollo / JARVIS',
    ads: 'Ads Studio',
    youtube: 'YouTube Studio'
  };

  var pickers = [];
  var cache = { catalog: null, config: null, at: 0 };
  var panelSeq = 0;

  function $(id) { return document.getElementById(id); }

  function esc(text) {
    return String(text == null ? '' : text)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  function tts() { return window.DennisTTS || null; }

  function fetchAll(force) {
    var now = Date.now();
    if (!force && cache.catalog && cache.config && (now - cache.at) < 10000) {
      return Promise.resolve(cache);
    }
    var t = tts();
    if (!t) return Promise.reject(new Error('TTS client not loaded'));
    return Promise.all([t.catalog(), t.getConfig()]).then(function (out) {
      cache.catalog = out[0] || { providers: [] };
      cache.config = out[1] || {};
      cache.at = Date.now();
      return cache;
    });
  }

  function toast(message, bad) {
    var node = document.createElement('div');
    node.className = 'vp-toast' + (bad ? ' vp-toast-bad' : '');
    node.textContent = message;
    document.body.appendChild(node);
    setTimeout(function () { node.classList.add('vp-toast-out'); }, 2200);
    setTimeout(function () { node.remove(); }, 2700);
  }

  function confirmWrite(title, message) {
    if (window.JarvisOs && typeof window.JarvisOs.confirm === 'function') {
      return window.JarvisOs.confirm(title, message,
        'Step 1: voice writes are confirmed and appended to the audit log.');
    }
    return Promise.resolve(window.confirm(title + '\n\n' + message));
  }

  function providerById(catalog, id) {
    var list = (catalog && catalog.providers) || [];
    for (var i = 0; i < list.length; i++) {
      if (list[i].id === id) return list[i];
    }
    return null;
  }

  function modelById(provider, id) {
    var models = (provider && provider.models) || [];
    for (var i = 0; i < models.length; i++) {
      if (models[i].id === id) return models[i];
    }
    return null;
  }

  function effectiveScopeState(config, scope) {
    config = config || {};
    if (scope === 'global') {
      var provider = config.global_default_provider || 'kokoro';
      var model = config.global_default_model_id || null;
      var voice = config.global_default_voice_id || null;
      if (!voice) {
        var presets = config.presets || {};
        for (var slug in presets) {
          if (presets[slug] && presets[slug].provider === provider) {
            voice = presets[slug].voice_id || null;
            break;
          }
        }
      }
      return { provider: provider, model: model, voice: voice };
    }
    var pref = (config.studios || {})[scope] || {};
    var preset = (config.presets || {})[pref.voice_preset || pref.preset] || {};
    var provider = pref.provider || config.global_default_provider || 'kokoro';
    // Only inherit a preset voice when the preset targets the same provider.
    var presetVoice = preset.provider === provider ? preset.voice_id : null;
    return {
      provider: provider,
      model: pref.model_id || null,
      voice: pref.voice_id || presetVoice || null
    };
  }

  function voiceLabel(provider, voiceId) {
    if (!voiceId) return '';
    var voices = (provider && provider.voices) || [];
    for (var i = 0; i < voices.length; i++) {
      if (voices[i].id === voiceId) return voices[i].label || voiceId;
    }
    return voiceId; // saved custom/manual entry — show the honest id
  }

  function labelFor(catalog, config, scope) {
    var state = effectiveScopeState(config, scope);
    var provider = providerById(catalog, state.provider);
    var model = modelById(provider, state.model);
    var providerName = provider ? provider.label.split(' (')[0] : state.provider;
    var modelName = model ? model.label : (state.model || 'default model');
    var voiceLabelName = voiceLabel(provider, state.voice);
    var voice = voiceLabelName ? (' · ' + voiceLabelName) : '';
    return providerName + ' · ' + modelName + voice;
  }

  function fillProviders(select, catalog, selected) {
    var providers = (catalog && catalog.providers) || [];
    select.innerHTML = providers.map(function (p) {
      var local = p.local ? 'LOCAL' : 'CLOUD';
      return '<option value="' + esc(p.id) + '">' + esc(p.label) +
        ' — ' + local + '</option>';
    }).join('');
    if (selected) select.value = selected;
    if (!select.value && providers.length) select.value = providers[0].id;
  }

  function fillModels(select, provider, selected) {
    var models = (provider && provider.models) || [];
    select.innerHTML = models.map(function (m) {
      return '<option value="' + esc(m.id) + '">' + esc(m.label) +
        ' · ' + (COST_LABEL[m.cost] || m.cost) + '</option>';
    }).join('');
    if (selected && models.some(function (m) { return m.id === selected; })) {
      select.value = selected;
    }
    if (!select.value && models.length) select.value = models[0].id;
  }

  function fillVoices(select, input, provider, selected) {
    var voices = (provider && provider.voices) || [];
    var isList = provider && provider.voice_mode === 'list';
    if (isList) {
      select.classList.remove('hidden');
      input.classList.add('hidden');
      var seen = {};
      var html = voices.map(function (v) {
        seen[v.id] = true;
        return '<option value="' + esc(v.id) + '">' + esc(v.label || v.id) +
          (v.lang ? ' · ' + esc(v.lang) : '') + '</option>';
      }).join('');
      if (selected && !seen[selected]) {
        html = '<option value="' + esc(selected) + '">' + esc(selected) +
          ' (saved)</option>' + html;
      }
      select.innerHTML = html;
      if (selected) select.value = selected;
      if (!select.value && voices.length) select.value = voices[0].id;
    } else {
      select.classList.add('hidden');
      input.classList.remove('hidden');
      input.value = selected || '';
    }
  }

  function modelNote(provider, modelId) {
    var model = modelById(provider, modelId);
    if (!model) return '';
    var cost = COST_LABEL[model.cost] || model.cost;
    return '<span class="vp-cost ' + (COST_CLASS[model.cost] || '') + '">' +
      esc(cost) + '</span> ' + esc(model.note || '');
  }

  function providerStatus(provider, status) {
    if (!provider) return '';
    if (provider.id === 'kokoro') {
      var k = (status && status.kokoro) || {};
      if (k.reachable) {
        return 'Kokoro is live on ' + (k.url || '127.0.0.1:8880') +
          ' · ' + (k.voice_count || 0) + ' voices';
      }
      return 'Kokoro is offline — start the local service to use free voices.';
    }
    if (provider.id === 'fish') {
      var f = (status && status.fish) || {};
      return f.configured
        ? 'Fish Audio key is configured. Free model is $0 under fair use.'
        : 'Fish Audio needs an API key before it can speak.';
    }
    if (provider.id === 'elevenlabs') {
      var e = (status && status.elevenlabs) || {};
      if (!e.configured) return 'ElevenLabs needs an API key before it can speak.';
      var q = e.quota || {};
      return 'ElevenLabs configured' + (q.character_limit
        ? ' · ' + q.character_count + ' / ' + q.character_limit + ' chars (' +
          (q.tier || 'free') + ')'
        : '');
    }
    return '';
  }

  function mount(target, opts) {
    var host = typeof target === 'string' ? $(target) : target;
    if (!host) return null;
    opts = opts || {};
    var scope = opts.scope || 'global';
    var scopes = opts.scopes || [scope];
    var registry = !!opts.registry;

    host.classList.add('vp-host');
    host.innerHTML =
      '<div class="vp-wrap">' +
        '<button type="button" class="vp-btn" aria-haspopup="true" aria-expanded="false" title="Voice provider, model and voice">' +
          '<i data-lucide="audio-lines" class="vp-ico"></i>' +
          '<span class="vp-btn-label">Voice: loading…</span>' +
          '<i data-lucide="chevron-down" class="vp-chev"></i>' +
        '</button>' +
      '</div>';

    // The panel is portaled to <body>: the dashboard voice control lives in a
    // .hud-panel with clip-path, and the studio shells use overflow:hidden.
    // An in-flow absolute panel would be clipped in both places.
    var panelId = 'vp-panel-' + (++panelSeq);
    var panel = document.createElement('div');
    panel.id = panelId;
    panel.className = 'vp-panel hidden';
    panel.setAttribute('role', 'dialog');
    panel.setAttribute('aria-label', 'Voice provider picker');
    panel.innerHTML =
      '<div class="vp-head"><span>Voice provider</span>' +
        '<button type="button" class="vp-close" aria-label="Close">×</button></div>' +
      (scopes.length > 1
        ? '<label class="vp-field"><span>Apply to</span><select class="vp-scope">' +
          scopes.map(function (s) {
            return '<option value="' + esc(s) + '">' + esc(SCOPE_LABEL[s] || s) + '</option>';
          }).join('') + '</select></label>'
        : '') +
      '<label class="vp-field"><span>Provider</span><select class="vp-provider"></select></label>' +
      '<label class="vp-field"><span>Model</span><select class="vp-model"></select></label>' +
      '<div class="vp-note"></div>' +
      '<label class="vp-field"><span>Voice</span><select class="vp-voice"></select>' +
        '<input type="text" class="vp-voice-custom hidden" maxlength="64" placeholder="Provider voice ID (optional)"></label>' +
      '<div class="vp-status"></div>' +
      '<div class="vp-actions">' +
        '<button type="button" class="vp-save">Save voice</button>' +
        '<button type="button" class="vp-cancel">Cancel</button>' +
      '</div>' +
      (registry
        ? '<div class="vp-registry">' +
            '<div class="vp-registry-title">Local service &amp; keys</div>' +
            '<div class="vp-service-row"><span class="vp-service-text">Checking…</span>' +
              '<button type="button" class="vp-service-toggle">Start</button></div>' +
            '<div class="vp-key-row"><input type="password" class="vp-el-key" autocomplete="off" placeholder="ElevenLabs API key (optional)">' +
              '<button type="button" class="vp-el-save">Save key</button></div>' +
          '</div>'
        : '');
    document.body.appendChild(panel);

    var wrap = host.querySelector('.vp-wrap');
    var btn = host.querySelector('.vp-btn');
    var btnLabel = host.querySelector('.vp-btn-label');
    btn.setAttribute('aria-controls', panelId);
    var scopeSel = panel.querySelector('.vp-scope');
    var providerSel = panel.querySelector('.vp-provider');
    var modelSel = panel.querySelector('.vp-model');
    var noteEl = panel.querySelector('.vp-note');
    var voiceSel = panel.querySelector('.vp-voice');
    var voiceInput = panel.querySelector('.vp-voice-custom');
    var statusEl = panel.querySelector('.vp-status');
    var saveBtn = panel.querySelector('.vp-save');
    var cancelBtn = panel.querySelector('.vp-cancel');
    var closeBtn = panel.querySelector('.vp-close');
    var serviceText = panel.querySelector('.vp-service-text');
    var serviceToggle = panel.querySelector('.vp-service-toggle');
    var elKey = panel.querySelector('.vp-el-key');
    var elSave = panel.querySelector('.vp-el-save');

    var state = { catalog: null, config: null, status: null, busy: false };
    var isOpen = false;
    var placement = opts.placement || 'auto';

    function currentScope() { return scopeSel ? scopeSel.value : scope; }

    function setLabel() {
      btnLabel.textContent = labelFor(state.catalog, state.config, currentScope());
      btn.title = 'Voice: ' + labelFor(state.catalog, state.config, currentScope()) +
        ' — click to change provider, model or voice';
    }

    function selectedProvider() {
      return providerById(state.catalog, providerSel.value);
    }

    function syncModels(selected) {
      fillModels(modelSel, selectedProvider(), selected);
      noteEl.innerHTML = modelNote(selectedProvider(), modelSel.value);
      var provider = selectedProvider();
      var scopeState = effectiveScopeState(state.config, currentScope());
      fillVoices(voiceSel, voiceInput, provider, selected === scopeState.model ? scopeState.voice : null);
      statusEl.textContent = providerStatus(provider, state.status);
    }

    function syncFromConfig() {
      var scopeState = effectiveScopeState(state.config, currentScope());
      fillProviders(providerSel, state.catalog, scopeState.provider);
      syncModels(scopeState.model);
      setLabel();
    }

    function refresh() {
      return Promise.all([fetchAll(true), tts().status()]).then(function (out) {
        state.catalog = out[0].catalog;
        state.config = out[0].config;
        state.status = out[1] || {};
        if (serviceText) {
          var k = state.status.kokoro || {};
          serviceText.textContent = k.reachable
            ? 'Kokoro live · ' + (k.voice_count || 0) + ' voices'
            : 'Kokoro offline';
          if (serviceToggle) serviceToggle.textContent = k.reachable ? 'Stop' : 'Start';
        }
        syncFromConfig();
      }).catch(function (err) {
        statusEl.textContent = 'Voice registry unreachable: ' + err.message;
      });
    }

    function placePanel() {
      if (!isOpen) return;
      var rect = btn.getBoundingClientRect();
      var gap = 8;
      var margin = 8;
      var width = panel.offsetWidth;
      var height = panel.offsetHeight;
      var viewW = window.innerWidth || document.documentElement.clientWidth;
      var viewH = window.innerHeight || document.documentElement.clientHeight;
      var left = rect.right - width;
      left = Math.max(margin, Math.min(left, viewW - margin - width));
      var above = rect.top - height - gap;
      var below = rect.bottom + gap;
      var top;
      if (placement === 'up') {
        top = above >= margin ? above : below;
      } else {
        // 'down' and 'auto' both prefer below the trigger, then flip up.
        top = (below + height) <= (viewH - margin) ? below : above;
      }
      top = Math.max(margin, Math.min(top, viewH - margin - height));
      panel.style.left = Math.round(left) + 'px';
      panel.style.top = Math.round(top) + 'px';
    }

    function open(show) {
      if (show === isOpen) {
        if (show) { placePanel(); refresh().then(placePanel); }
        return;
      }
      isOpen = show;
      if (show) {
        panel.classList.remove('hidden');
        // Measure before paint so the panel never flashes at 0,0.
        panel.style.visibility = 'hidden';
        placePanel();
        panel.style.visibility = '';
        btn.setAttribute('aria-expanded', 'true');
        refresh().then(placePanel);
      } else {
        panel.classList.add('hidden');
        btn.setAttribute('aria-expanded', 'false');
      }
    }

    btn.addEventListener('click', function (e) {
      e.stopPropagation();
      open(!isOpen);
    });
    closeBtn.addEventListener('click', function () { open(false); });
    cancelBtn.addEventListener('click', function () { open(false); });
    document.addEventListener('click', function (e) {
      if (!isOpen) return;
      if (wrap.contains(e.target) || panel.contains(e.target)) return;
      // Clicks inside a JarvisOs confirmation overlay (e.g. raised by the
      // Save button) must not close the panel underneath the dialog.
      if (e.target && e.target.closest && e.target.closest('.fixed.inset-0')) return;
      open(false);
    });
    document.addEventListener('keydown', function (e) {
      if (isOpen && e.key === 'Escape') open(false);
    });
    window.addEventListener('resize', placePanel);
    window.addEventListener('scroll', placePanel, true);
    if (window.ResizeObserver) {
      new ResizeObserver(function () { placePanel(); }).observe(panel);
    }

    if (scopeSel) {
      scopeSel.addEventListener('change', function () { syncFromConfig(); });
    }

    providerSel.addEventListener('change', function () {
      fillModels(modelSel, selectedProvider(), null);
      noteEl.innerHTML = modelNote(selectedProvider(), modelSel.value);
      fillVoices(voiceSel, voiceInput, selectedProvider(), null);
      statusEl.textContent = providerStatus(selectedProvider(), state.status);
    });

    modelSel.addEventListener('change', function () {
      noteEl.innerHTML = modelNote(selectedProvider(), modelSel.value);
    });

    saveBtn.addEventListener('click', function () {
      if (state.busy) return;
      var scopeNow = currentScope();
      var provider = selectedProvider();
      var model = modelSel.value;
      var voice = provider && provider.voice_mode === 'list'
        ? voiceSel.value
        : (voiceInput.value || '').trim();
      if (!provider) return;

      var providerName = provider.label.split(' (')[0];
      var modelInfo = modelById(provider, model);
      var targetLabel = SCOPE_LABEL[scopeNow] || scopeNow;
      var voiceName = voiceLabel(provider, voice) || voice;
      var message = 'Set ' + targetLabel + ' to ' + providerName +
        ' · ' + (modelInfo ? modelInfo.label : model) +
        (voiceName ? ' · ' + voiceName : '') + '?';
      if (provider.cost_risk || (modelInfo && modelInfo.cost === 'paid')) {
        message += '\n\nThis model can incur provider charges.';
      }

      confirmWrite('Change voice provider', message).then(function (yes) {
        if (!yes) return;
        state.busy = true;
        saveBtn.disabled = true;
        saveBtn.textContent = 'Saving…';
        var patch;
        if (scopeNow === 'global') {
          patch = {
            global_default_provider: provider.id,
            global_default_model_id: model || null,
            global_default_voice_id: voice || null
          };
        } else {
          patch = { studios: {} };
          patch.studios[scopeNow] = {
            provider: provider.id,
            model_id: model || null,
            voice_id: voice || null
          };
        }
        tts().saveConfig(patch).then(function (config) {
          cache.config = config;
          state.config = config;
          setLabel();
          open(false);
          toast('Voice saved for ' + targetLabel);
        }).catch(function (err) {
          statusEl.textContent = 'Save failed: ' + err.message;
          toast('Voice save failed: ' + err.message, true);
        }).then(function () {
          state.busy = false;
          saveBtn.disabled = false;
          saveBtn.textContent = 'Save voice';
        });
      });
    });

    if (serviceToggle) {
      serviceToggle.addEventListener('click', function () {
        var stopping = serviceToggle.textContent === 'Stop';
        confirmWrite(stopping ? 'Stop Kokoro service' : 'Start Kokoro service',
          (stopping ? 'Stop' : 'Start') + ' the local Kokoro TTS service?')
          .then(function (yes) {
            if (!yes) return;
            serviceToggle.disabled = true;
            var run = stopping ? tts().stopService() : tts().startService();
            run.catch(function () {})
              .then(function () { return new Promise(function (r) { setTimeout(r, 1200); }); })
              .then(refresh)
              .then(function () { serviceToggle.disabled = false; });
          });
      });
    }

    if (elSave) {
      elSave.addEventListener('click', function () {
        var key = (elKey.value || '').trim();
        if (!key) return;
        confirmWrite('Save ElevenLabs key',
          'Store this ElevenLabs API key in the private backend credential store?')
          .then(function (yes) {
            if (!yes) return;
            elSave.disabled = true;
            tts().setSecret('elevenlabs_api_key', key).then(function () {
              elKey.value = '';
              toast('ElevenLabs key saved');
              refresh();
            }).catch(function (err) {
              toast('Key save failed: ' + err.message, true);
            }).then(function () { elSave.disabled = false; });
          });
      });
    }

    var entry = { refresh: refresh, setLabel: setLabel, scope: scope };
    pickers.push(entry);
    // Initial paint uses cached data if available, then refreshes on open.
    fetchAll(false).then(function (out) {
      state.catalog = out.catalog;
      state.config = out.config;
      syncFromConfig();
    }).catch(function () {
      btnLabel.textContent = 'Voice: unavailable';
    });
    if (window.lucide) window.lucide.createIcons();
    return entry;
  }

  function refreshAll() {
    cache.at = 0;
    return Promise.all(pickers.map(function (p) {
      return p.refresh().catch(function () {});
    }));
  }

  // Studio headers are static in index.html, so one auto-mount is enough.
  // The dashboard picker is mounted explicitly by jarvisDashboard.js.
  function autoMount() {
    var ads = $('studio-ads-voice');
    if (ads && !ads.getAttribute('data-vp-mounted')) {
      ads.setAttribute('data-vp-mounted', '1');
      mount(ads, { scope: 'ads', placement: 'down' });
    }
    var yt = $('studio-youtube-voice');
    if (yt && !yt.getAttribute('data-vp-mounted')) {
      yt.setAttribute('data-vp-mounted', '1');
      mount(yt, { scope: 'youtube', placement: 'down' });
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', autoMount);
  } else {
    autoMount();
  }

  window.DennisVoicePicker = {
    mount: mount,
    refreshAll: refreshAll,
    autoMount: autoMount,
    scopes: SCOPE_LABEL
  };
})();
