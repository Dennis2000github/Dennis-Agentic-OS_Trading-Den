// =========================================================================
// DENNIS VOICE REGISTRY UI — Settings card + per-studio voiceover panels.
// All three workspaces (Apollo, Ads Studio, YouTube Studio) read the SAME
// centralized provider registry (backend/tts.py via /api/tts/*). Studio
// preferences are independent: changing Ads can never change Apollo's voice.
// No credentials live in this file; the ElevenLabs key is stored backend-side.
// =========================================================================

(function () {
  'use strict';

  function $(id) { return document.getElementById(id); }

  function api(path, options) {
    var opts = Object.assign({ headers: { 'Content-Type': 'application/json' } }, options || {});
    return fetch(path, opts).then(function (res) {
      return res.json().catch(function () { return {}; }).then(function (body) {
        if (!res.ok) throw new Error(body.error || ('HTTP ' + res.status));
        return body;
      });
    });
  }

  function escapeHtml(text) {
    return String(text == null ? '' : text)
      .replace(/&/g, '\u0026').replace(/</g, '\u003C').replace(/>/g, '\u003E')
      .replace(/\"/g, '\u0022').replace(/'/g, '\u0027');
  }

  // ------------------------------------------------------------------
  // Settings / Provider Registry card
  // ------------------------------------------------------------------
  function initRegistryCard() {
    var globalSel = $('vr-global-provider');
    var studioSels = Array.prototype.slice.call(
      document.querySelectorAll('[data-vr-studio-preset]'));
    var dot = $('vr-service-dot');
    var text = $('vr-service-text');
    var toggle = $('vr-service-toggle');
    var saveBtn = $('vr-save');
    var elKey = $('vr-elevenlabs-key');
    var elSave = $('vr-elevenlabs-save');
    var elStatus = $('vr-elevenlabs-status');
    if (!globalSel && !studioSels.length) return;

    var config = null;

    function statusLabel(status) {
      var kokoro = status.kokoro || {};
      if (!kokoro.healthy) return ['Kokoro unavailable', 'bg-rose-400'];
      if (kokoro.loaded) {
        return ['Kokoro connected • ' + (kokoro.model || 'model') + ' • ' +
                (kokoro.voice_count || 0) + ' voices', 'bg-emerald-400'];
      }
      return ['Kokoro connected • model loading…', 'bg-amber-400'];
    }

    function fill() {
      api('/api/tts/status').then(function (status) {
        var label = statusLabel(status);
        if (dot) dot.className = 'w-2 h-2 rounded-full ' + label[1];
        if (text) text.textContent = label[0];
        if (toggle) toggle.textContent = (status.kokoro || {}).healthy ? 'Stop' : 'Start';
        if (globalSel) {
          globalSel.innerHTML = Object.keys(status.providers || {}).map(function (id) {
            var slot = status.providers[id] || {};
            if (slot.fallback_only) return '';
            var label = slot.label || (id.charAt(0).toUpperCase() + id.slice(1));
            return '<option value="' + id + '">' + escapeHtml(label) + '</option>';
          }).join('');
          globalSel.value = status.global_default_provider || 'kokoro';
        }
        var eleven = status.elevenlabs || {};
        var quota = eleven.quota;
        if (elStatus) {
          elStatus.textContent = eleven.configured
            ? ('Configured' + (quota && quota.character_limit
                ? ' — quota ' + quota.character_count + ' / ' + quota.character_limit +
                  ' chars' + (quota.tier ? ' (' + quota.tier + ')' : '')
                : ''))
            : 'Not configured (Kokoro stays the default — no key needed)';
        }
        return api('/api/tts/config');
      }).then(function (resp) {
        config = resp.config;
        studioSels.forEach(function (sel) {
          var studio = sel.getAttribute('data-vr-studio-preset');
          var pref = (config.studios || {})[studio] || {};
          sel.innerHTML = Object.keys(config.presets || {}).map(function (id) {
            var preset = config.presets[id] || {};
            return '<option value="' + id + '">' + escapeHtml(preset.name || id) + '</option>';
          }).join('');
          sel.value = pref.preset || pref.voice_preset || '';
        });
      }).catch(function (err) {
        if (text) text.textContent = 'TTS router unreachable: ' + err.message;
        if (dot) dot.className = 'w-2 h-2 rounded-full bg-rose-400';
      });
    }

    function saveStudioPreset(studio, presetId) {
      var patch = { studios: {} };
      patch.studios[studio] = { voice_preset: presetId };
      return api('/api/tts/config', { method: 'POST', body: JSON.stringify(patch) });
    }

    studioSels.forEach(function (sel) {
      sel.addEventListener('change', function () {
        saveStudioPreset(sel.getAttribute('data-vr-studio-preset'), sel.value)
          .then(function () { flash(saveBtn, 'Saved ✓'); })
          .catch(function (err) { flash(saveBtn, 'Failed: ' + err.message); });
      });
    });

    if (globalSel) {
      globalSel.addEventListener('change', function () {
        api('/api/tts/config', {
          method: 'POST',
          body: JSON.stringify({ global_default_provider: globalSel.value })
        }).then(function () { flash(saveBtn, 'Saved ✓'); })
          .catch(function (err) { flash(saveBtn, 'Failed: ' + err.message); });
      });
    }

    if (toggle) {
      toggle.addEventListener('click', function () {
        var action = toggle.textContent === 'Stop' ? 'stop' : 'start';
        toggle.disabled = true;
        api('/api/tts/service', { method: 'POST', body: JSON.stringify({ action: action }) })
          .catch(function () {})
          .then(function () { return new Promise(function (r) { setTimeout(r, 1200); }); })
          .then(fill)
          .then(function () { toggle.disabled = false; });
      });
    }

    if (elSave) {
      elSave.addEventListener('click', function () {
        if (!elKey.value) return;
        // Secrets go straight to the backend store via the config patch and
        // are never echoed back by any response.
        api('/api/tts/config', {
          method: 'POST',
          body: JSON.stringify({ provider_secrets: { elevenlabs: { api_key: elKey.value } } })
        }).then(function () {
          elKey.value = '';
          flash(saveBtn, 'ElevenLabs key saved ✓');
          fill();
        }).catch(function (err) { flash(saveBtn, 'Failed: ' + err.message); });
      });
    }

    function flash(btn, message) {
      if (!btn) return;
      var original = btn.textContent;
      btn.textContent = message;
      setTimeout(function () { btn.textContent = original; }, 1800);
    }

    fill();
    // Refresh the service status periodically while the card is on screen.
    setInterval(function () {
      if (document.hidden || !$('vr-service-text')) return;
      api('/api/tts/status').then(function (status) {
        var label = statusLabel(status);
        var dotEl = $('vr-service-dot');
        var textEl = $('vr-service-text');
        var tgl = $('vr-service-toggle');
        if (dotEl) dotEl.className = 'w-2 h-2 rounded-full ' + label[1];
        if (textEl) textEl.textContent = label[0];
        if (tgl) tgl.textContent = (status.kokoro || {}).healthy ? 'Stop' : 'Start';
      }).catch(function () {});
    }, 15000);
  }

  // ------------------------------------------------------------------
  // Per-studio voiceover panels (Ads + YouTube)
  // ------------------------------------------------------------------
  function mountVoiceover(host) {
    var studio = host.getAttribute('data-voiceover-studio') || 'apollo';
    var title = studio === 'ads' ? 'Ads voiceover' : 'YouTube voiceover';
    host.innerHTML =
      '<div class="glass-panel rounded-2xl p-3 border border-blue-900/40 bg-[#090e1f]/75">' +
      '  <div class="flex items-center gap-2 mb-1.5">' +
      '    <i data-lucide="audio-lines" class="w-3.5 h-3.5 text-cyan-400"></i>' +
      '    <span class="text-[11px] font-bold text-white">' + title + '</span>' +
      '    <span class="text-[9px] text-slate-500">centralized Kokoro TTS</span>' +
      '    <select data-vo-preset class="ml-auto rounded-md bg-[#111a30] border border-slate-800 text-[10px] text-slate-200 px-1.5 py-1 outline-none"></select>' +
      '  </div>' +
      '  <textarea data-vo-text rows="2" placeholder="Paste the voiceover script…" class="w-full rounded-md bg-[#111a30] border border-slate-800 text-[11px] text-slate-200 px-2 py-1.5 outline-none focus:border-cyan-600 resize-y"></textarea>' +
      '  <div class="flex items-center gap-2 mt-1.5">' +
      '    <button data-vo-generate class="rounded-md bg-blue-600 hover:bg-blue-500 text-white text-[10.5px] font-semibold px-3 py-1.5 shadow-lg shadow-blue-600/30">Generate voiceover</button>' +
      '    <span data-vo-status class="text-[10px] text-slate-500">Kokoro runs locally — generation takes a moment on this machine.</span>' +
      '  </div>' +
      '  <div data-vo-result class="mt-2 hidden"></div>' +
      '</div>';

    var presetSel = host.querySelector('[data-vo-preset]');
    var textEl = host.querySelector('[data-vo-text]');
    var genBtn = host.querySelector('[data-vo-generate]');
    var statusEl = host.querySelector('[data-vo-status]');
    var resultEl = host.querySelector('[data-vo-result]');
    var currentPreset = null;

    api('/api/tts/config').then(function (resp) {
      var config = resp.config || {};
      var pref = (config.studios || {})[studio] || {};
      currentPreset = pref.preset || pref.voice_preset || null;
      presetSel.innerHTML = Object.keys(config.presets || {}).map(function (id) {
        var preset = config.presets[id] || {};
        return '<option value="' + id + '"' + (id === currentPreset ? ' selected' : '') + '>' +
          escapeHtml(preset.name || id) + '</option>';
      }).join('');
    }).catch(function () {});

    presetSel.addEventListener('change', function () {
      // Per-studio preset override — independent from every other studio.
      var patch = { studios: {} };
      patch.studios[studio] = { voice_preset: presetSel.value };
      api('/api/tts/config', { method: 'POST', body: JSON.stringify(patch) })
        .catch(function (err) {
          statusEl.textContent = 'Preset save failed: ' + err.message;
        });
    });

    genBtn.addEventListener('click', function () {
      var text = (textEl.value || '').trim();
      if (!text) {
        statusEl.textContent = 'Paste a script first.';
        return;
      }
      genBtn.disabled = true;
      statusEl.textContent = 'Generating with Kokoro… (local CPU synthesis)';
      resultEl.classList.add('hidden');
      api('/api/tts/speech', {
        method: 'POST',
        body: JSON.stringify({
          text: text,
          studio: studio,
          preset: presetSel.value || null,
          response_format: 'wav',
          save: true,
          name: studio + '-voiceover'
        })
      }).then(function (resp) {
        var record = (resp && resp.audio) || {};
        genBtn.disabled = false;
        statusEl.textContent = 'Saved ' + Math.round((record.bytes || 0) / 1024) + ' KB';
        resultEl.innerHTML =
          '<audio controls preload="none" src="' + escapeHtml(record.url) + '" class="w-full h-8"></audio>' +
          '<a href="' + escapeHtml(record.url) + '" download="' + escapeHtml(record.file) +
          '" class="text-[10px] text-cyan-400 hover:text-cyan-300 mt-1 inline-block">Download ' +
          escapeHtml(record.file) + '</a>';
        resultEl.classList.remove('hidden');
        if (window.lucide) window.lucide.createIcons();
      }).catch(function (err) {
        genBtn.disabled = false;
        statusEl.textContent = 'Generation failed: ' + err.message;
      });
    });
  }

  function init() {
    initRegistryCard();
    Array.prototype.forEach.call(
      document.querySelectorAll('[data-voiceover-studio]'), mountVoiceover);
    if (window.lucide) window.lucide.createIcons();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
