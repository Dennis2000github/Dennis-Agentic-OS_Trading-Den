// =========================================================================
// DENNIS VOICE REGISTRY UI — Settings card (Voice Provider Registry).
// All workspaces (Apollo, Ads Studio, YouTube Studio) read the SAME
// centralized provider registry (backend/tts.py via /api/tts/*). Studio
// preferences are independent: changing Ads can never change Apollo's voice.
// Manual per-studio voiceover generation widgets were removed — voiceovers
// are produced automatically inside each studio's workflow. No credentials
// live in this file; the ElevenLabs key is stored backend-side.
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
  // Per-studio voiceover panels (Ads + YouTube) were removed: voiceover
  // generation now happens automatically inside each studio's workflow.
  // ------------------------------------------------------------------

  function init() {
    initRegistryCard();
    if (window.lucide) window.lucide.createIcons();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
