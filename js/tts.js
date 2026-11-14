// =========================================================================
// DENNIS TTS CLIENT — centralized text-to-speech router client.
// Providers/presets live in the backend (data/tts_config.json); this client
// only talks to the owner-gated /api/tts/* routes, never to the inference
// service or any provider API directly. No credentials exist in the frontend.
//
// Public API:
//   DennisTTS.speak(text, opts)   opts: {preset, voice, speed, studio,
//                                         onstart, onend, onerror, allowFallback}
//   DennisTTS.cancel()            stop current speech + clear the queue
//   DennisTTS.status()            -> backend router + Kokoro service status
//   DennisTTS.voices()            -> voice catalog from the active provider
//   DennisTTS.getConfig()/saveConfig(patch)
//   DennisTTS.preview(voiceId)    short spoken sample of a voice
//   DennisTTS.startService()/stopService()
//   DennisTTS.on(event, fn)       events: "start" | "end" | "error" | "status"
//   DennisTTS.speaking            true while audio is on screen
// =========================================================================

(function () {
  'use strict';

  var SAMPLE_RATE = 24000; // Kokoro PCM output rate (s16le, mono)

  var state = {
    token: null,
    tokenPromise: null,
    queue: [],
    speaking: false,
    currentFetch: null,
    streamer: null,
    audioCtx: null,
    cachedConfig: null,
    listeners: { start: [], end: [], error: [], status: [] },
    engine: 'kokoro', // 'kokoro' | 'browser'
  };

  // ---------------------------------------------------------------------
  // Auth (same pattern as every other OS panel: short-lived session token)
  // ---------------------------------------------------------------------
  function ensureToken() {
    if (state.token) return Promise.resolve(state.token);
    if (!state.tokenPromise) {
      state.tokenPromise = fetch('/api/auth/session', { cache: 'no-store' })
        .then(function (r) { return r.json(); })
        .then(function (data) {
          state.token = data.token || '';
          return state.token;
        })
        .catch(function () { return ''; });
    }
    return state.tokenPromise;
  }

  function authHeaders() {
    var headers = { 'Content-Type': 'application/json' };
    if (state.token) headers['X-Dennis-Token'] = state.token;
    return headers;
  }

  function api(path, options) {
    return ensureToken().then(function () {
      var opts = options || {};
      opts.headers = Object.assign(authHeaders(), opts.headers || {});
      return fetch(path, opts).then(function (res) {
        if (!res.ok) {
          return res.json().catch(function () { return {}; }).then(function (body) {
            var err = new Error(body.error || ('HTTP ' + res.status));
            err.status = res.status;
            throw err;
          });
        }
        return res.json();
      });
    });
  }

  function emit(event, payload) {
    (state.listeners[event] || []).forEach(function (fn) {
      try { fn(payload); } catch (e) { console.error('[DennisTTS] listener error', e); }
    });
  }

  // ---------------------------------------------------------------------
  // Web Audio playback
  // ---------------------------------------------------------------------
  function getAudioContext() {
    if (!state.audioCtx) {
      var Ctx = window.AudioContext || window.webkitAudioContext;
      if (!Ctx) return null;
      state.audioCtx = new Ctx();
    }
    if (state.audioCtx.state === 'suspended') state.audioCtx.resume();
    return state.audioCtx;
  }

  // Schedules PCM (s16le mono) chunks onto the audio clock as they arrive.
  function PcmStreamer(ctx, sampleRate, onDone) {
    this.ctx = ctx;
    this.sampleRate = sampleRate || SAMPLE_RATE;
    this.onDone = onDone;
    this.gain = ctx.createGain();
    this.gain.gain.value = 0.95;
    this.gain.connect(ctx.destination);
    this.sources = [];
    this.nextTime = 0;
    this.remainder = null; // odd trailing byte across chunk boundaries
    this.finished = false;
    this.endedTimer = null;
  }

  PcmStreamer.prototype.push = function (arrayBuffer) {
    var ctx = this.ctx;
    var bytes = new Uint8Array(arrayBuffer);
    if (this.remainder) {
      var merged = new Uint8Array(this.remainder.length + bytes.length);
      merged.set(this.remainder, 0);
      merged.set(bytes, this.remainder.length);
      bytes = merged;
      this.remainder = null;
    }
    var usable = bytes.length - (bytes.length % 2);
    if (usable !== bytes.length) this.remainder = bytes.slice(usable);
    if (usable === 0) return;

    var samples = new Int16Array(bytes.buffer, bytes.byteOffset, usable / 2);
    var buffer = ctx.createBuffer(1, samples.length, this.sampleRate);
    var channel = buffer.getChannelData(0);
    for (var i = 0; i < samples.length; i++) channel[i] = samples[i] / 32768;

    var src = ctx.createBufferSource();
    src.buffer = buffer;
    src.connect(this.gain);
    var when = Math.max(ctx.currentTime + 0.06, this.nextTime);
    src.start(when);
    this.nextTime = when + buffer.duration;
    this.sources.push(src);
    var self = this;
    src.onended = function () {
      self.sources = self.sources.filter(function (s) { return s !== src; });
      self._checkEnded();
    };
  };

  PcmStreamer.prototype.finish = function () {
    this.finished = true;
    this._checkEnded();
  };

  PcmStreamer.prototype._checkEnded = function () {
    if (!this.finished) return;
    if (this.sources.length > 0) return;
    if (this.endedTimer) return;
    var ctx = this.ctx;
    var quiet = (this.nextTime - ctx.currentTime) * 1000;
    this.endedTimer = setTimeout(this.onDone, Math.max(0, quiet) + 60);
  };

  PcmStreamer.prototype.stop = function () {
    this.finished = true;
    if (this.endedTimer) { clearTimeout(this.endedTimer); this.endedTimer = null; }
    this.sources.forEach(function (src) {
      try { src.onended = null; src.stop(); } catch (e) { /* already stopped */ }
    });
    this.sources = [];
    try { this.gain.disconnect(); } catch (e) { /* already detached */ }
  };

  function playWav(arrayBuffer) {
    return new Promise(function (resolve, reject) {
      var ctx = getAudioContext();
      if (!ctx) return reject(new Error('Web Audio unavailable'));
      ctx.decodeAudioData(arrayBuffer.slice(0), resolve, function () {
        reject(new Error('audio decode failed'));
      });
    });
  }

  // ---------------------------------------------------------------------
  // Speech pipeline
  // ---------------------------------------------------------------------
  function streamSpeech(task) {
    var ctx = getAudioContext();
    if (!ctx) return Promise.reject(new Error('Web Audio unavailable'));
    return new Promise(function (resolve, reject) {
      var streamer = null;
      state.streamer = null;

      var controller = new AbortController();
      state.currentFetch = controller;

      fetch('/api/tts/speech', {
        method: 'POST',
        headers: authHeaders(),
        body: JSON.stringify({
          text: task.text,
          preset: task.preset,
          voice_id: task.voice,
          speed: task.speed,
          lang: task.lang,
          studio: task.studio || 'apollo',
          stream: true
        }),
        signal: controller.signal
      }).then(function (res) {
        if (!res.ok) {
          return res.json().catch(function () { return {}; }).then(function (body) {
            throw new Error(body.error || ('HTTP ' + res.status));
          });
        }
        var rateHeader = parseInt(res.headers.get('X-Audio-Sample-Rate'), 10);
        streamer = new PcmStreamer(ctx, rateHeader > 1000 ? rateHeader : SAMPLE_RATE, function () {
          cleanup('end');
        });
        state.streamer = streamer;
        var reader = res.body.getReader();
        function pump() {
          return reader.read().then(function (chunk) {
            if (chunk.done) { streamer.finish(); return; }
            streamer.push(chunk.value.buffer);
            return pump();
          });
        }
        return pump();
      }).catch(function (err) {
        if (err && err.name === 'AbortError') return; // cancelled locally
        cleanup('error', err);
      });

      function cleanup(how, err) {
        state.currentFetch = null;
        if (state.streamer) { state.streamer = null; }
        if (how === 'error') { if (streamer) { try { streamer.stop(); } catch (e) { /* noop */ } } reject(err); }
        else resolve();
      }
    });
  }

  function blobSpeech(task) {
    var controller = new AbortController();
    state.currentFetch = controller;
    return fetch('/api/tts/speech', {
      method: 'POST',
      headers: authHeaders(),
      body: JSON.stringify({
        text: task.text, preset: task.preset, voice_id: task.voice,
        speed: task.speed, lang: task.lang, studio: task.studio || 'apollo',
        response_format: 'wav'
      }),
      signal: controller.signal
    }).then(function (res) {
      if (!res.ok) {
        return res.json().catch(function () { return {}; }).then(function (body) {
          throw new Error(body.error || ('HTTP ' + res.status));
        });
      }
      return res.arrayBuffer();
    }).then(function (buf) {
      state.currentFetch = null;
      return playWav(buf).then(function (buffer) {
        return new Promise(function (resolve) {
          var ctx = getAudioContext();
          var src = ctx.createBufferSource();
          src.buffer = buffer;
          var gain = ctx.createGain();
          gain.gain.value = 0.95;
          src.connect(gain);
          gain.connect(ctx.destination);
          state.streamer = { stop: function () { try { src.stop(); } catch (e) { /* noop */ } } };
          src.onended = function () { state.streamer = null; resolve(); };
          src.start();
        });
      });
    }).catch(function (err) {
      state.currentFetch = null;
      throw err;
    });
  }

  // Browser speechSynthesis fallback (kept configurable, never the default).
  function browserSpeech(task) {
    return new Promise(function (resolve) {
      var synth = window.speechSynthesis;
      if (!synth) return resolve();
      try {
        synth.cancel();
        var utterance = new SpeechSynthesisUtterance(task.text);
        utterance.rate = task.speed || 1.0;
        utterance.onend = resolve;
        utterance.onerror = resolve;
        synth.speak(utterance);
      } catch (e) { resolve(); }
    });
  }

  function runNext() {
    if (state.speaking) return;
    var task = state.queue.shift();
    if (!task) return;
    state.speaking = true;
    emit('start', task);

    var runner;
    if (state.engine === 'browser') {
      runner = browserSpeech(task);
    } else if (task.stream !== false) {
      runner = streamSpeech(task).catch(function (err) {
        // Service unreachable / generation failed -> fall back if allowed.
        if (task.allowFallback !== false) {
          emit('error', err);
          return browserSpeech(task);
        }
        throw err;
      });
    } else {
      runner = blobSpeech(task);
    }

    runner.then(function () {
      state.speaking = false;
      emit('end', task);
      runNext();
    }).catch(function (err) {
      state.speaking = false;
      emit('error', err);
      emit('end', task);
      runNext();
    });
  }

  // ---------------------------------------------------------------------
  // Public API
  // ---------------------------------------------------------------------
  window.DennisTTS = {
    get speaking() { return state.speaking; },
    get engine() { return state.engine; },

    speak: function (text, opts) {
      var options = opts || {};
      var task = {
        text: String(text || ''),
        preset: options.preset || null,
        voice: options.voice || null,
        speed: typeof options.speed === 'number' ? options.speed : null,
        lang: options.lang || null,
        studio: options.studio || null,
        stream: options.stream !== false,
        allowFallback: options.allowFallback !== false
      };
      if (!task.text) return Promise.resolve();
      return new Promise(function (resolve) {
        task._resolve = resolve;
        state.queue.push(task);
        runNext();
      });
    },

    cancel: function () {
      state.queue = [];
      if (state.currentFetch) {
        try { state.currentFetch.abort(); } catch (e) { /* noop */ }
        state.currentFetch = null;
      }
      if (state.streamer) {
        try { state.streamer.stop(); } catch (e) { /* noop */ }
        state.streamer = null;
      }
      if (window.speechSynthesis) { try { window.speechSynthesis.cancel(); } catch (e) { /* noop */ } }
      if (state.speaking) {
        state.speaking = false;
        emit('end', { cancelled: true });
      }
    },

    status: function () { return api('/api/tts/status'); },
    voices: function () { return api('/api/tts/voices'); },
    getConfig: function () {
      return api('/api/tts/config').then(function (r) {
        state.cachedConfig = (r && r.config) ? r.config : r;
        return state.cachedConfig;
      });
    },
    saveConfig: function (patch) {
      return api('/api/tts/config', { method: 'POST', body: JSON.stringify(patch) })
        .then(function (r) { state.cachedConfig = (r && r.config) ? r.config : r; return state.cachedConfig; });
    },
    setSecret: function (name, value) {
      // Secrets never round-trip through the frontend: they go straight to
      // the backend store via the config patch endpoint.
      if (name !== 'elevenlabs_api_key') {
        return Promise.reject(new Error('unsupported secret: ' + name));
      }
      return api('/api/tts/config', {
        method: 'POST',
        body: JSON.stringify({ provider_secrets: { elevenlabs: { api_key: value } } })
      }).then(function (r) { return { set: true, name: name }; });
    },
    startService: function () {
      return api('/api/tts/service', { method: 'POST', body: JSON.stringify({ action: 'start' }) });
    },
    stopService: function () {
      return api('/api/tts/service', { method: 'POST', body: JSON.stringify({ action: 'stop' }) });
    },

    preview: function (voiceId, text) {
      return this.speak(text || 'Voice preview. This is how I will sound.', {
        voice: voiceId, stream: true, allowFallback: false
      });
    },

    on: function (event, fn) {
      if (state.listeners[event]) state.listeners[event].push(fn);
    },

    cached: function () { return state.cachedConfig; }
  };
})();
