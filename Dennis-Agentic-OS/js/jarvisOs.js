/* =========================================================================
   Jarvis OS upgrade layer — browser client
   -------------------------------------------------------------------------
   Renders Voice + Connections + Audit in Settings, the Genesis + Trading
   Den mission strip on the JARVIS home, and answers voice commands through
   the local tool-router.

   Rules mirrored from the backend:
     * Browser only ever receives connection STATUS — never a key.
     * Every write (Genesis Approve / Reject / Restart) requires a visible
       confirmation dialog, then POSTs confirmed:true and is audit-logged
       server-side (who / what / when / result).
     * Trading Den is strictly read-only — no control/order buttons exist.
     * Unknown data renders as "unknown" with the raw evidence alongside.
   ========================================================================= */
(function () {
  'use strict';

  var API = '/api/jarvis';
  var S = { token: null, tokenPromise: null, ttsStatus: null };

  // ---------- tiny helpers -------------------------------------------------
  function esc(v) {
    return String(v == null ? 'unknown' : v)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }
  function attr(v) { return esc(v); }

  function authToken() {
    if (S.token) return Promise.resolve(S.token);
    if (!S.tokenPromise) {
      S.tokenPromise = fetch('/api/auth/session', { cache: 'no-store' })
        .then(function (r) { return r.json(); })
        .then(function (d) { S.token = (d && d.token) || ''; return S.token; })
        .catch(function () { S.token = ''; return ''; });
    }
    return S.tokenPromise;
  }

  function fetchJson(path, opts) {
    return authToken().then(function () {
      var url = path + (path.indexOf('?') >= 0 ? '&' : '?') + 'token=' + encodeURIComponent(S.token || '');
      return fetch(url, opts || { cache: 'no-store' })
        .then(function (r) { return r.json().catch(function () { return {}; }); });
    });
  }

  function postJson(path, body) {
    return authToken().then(function (token) {
      return fetch(path, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Dennis-Token': token || '' },
        body: JSON.stringify(body || {})
      }).then(function (r) { return r.json().catch(function () { return {}; }); });
    });
  }

  // Synchronous localhost GET — used only by the synchronous voice-command
  // routing contract in galaxy.js. Local desktop context, no remote network.
  function syncGet(path) {
    try {
      var x = new XMLHttpRequest();
      var url = path + (path.indexOf('?') >= 0 ? '&' : '?') + 'token=' + encodeURIComponent(S.token || '');
      x.open('GET', url, false);
      x.send(null);
      if (x.status >= 200 && x.status < 300) return JSON.parse(x.responseText);
    } catch (e) { /* fall through to unhandled */ }
    return null;
  }

  function toast(msg) {
    if (window.hermesApollo && window.hermesApollo.showToastSafe) {
      window.hermesApollo.showToastSafe(msg);
    } else if (window.hermesApollo && window.hermesApollo.showHudToast) {
      window.hermesApollo.showHudToast(msg);
    }
  }

  // ---------- confirmation dialog ----------------------------------------
  function confirmAction(title, message, detail) {
    return new Promise(function (resolve) {
      var wrap = document.createElement('div');
      wrap.className = 'fixed inset-0 z-[9999] flex items-center justify-center bg-black/70 p-4';
      wrap.innerHTML =
        '<div class="w-full max-w-md rounded-2xl border border-amber-500/40 bg-slate-900 shadow-2xl p-5">' +
          '<div class="flex items-center gap-2 mb-2">' +
            '<i data-lucide="shield-alert" class="w-5 h-5 text-amber-400"></i>' +
            '<h3 class="text-sm font-bold text-white">' + esc(title) + '</h3>' +
          '</div>' +
          '<p class="text-xs text-slate-300 leading-relaxed">' + esc(message) + '</p>' +
          (detail ? '<pre class="mt-3 p-2 rounded-lg bg-slate-950/80 border border-slate-800 text-[11px] text-slate-400 whitespace-pre-wrap max-h-40 overflow-auto">' + esc(detail) + '</pre>' : '') +
          '<div class="mt-4 flex justify-end gap-2">' +
            '<button data-cancel class="px-3 py-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-200 text-xs font-semibold">Cancel</button>' +
            '<button data-confirm class="px-3 py-1.5 rounded-lg bg-amber-600 hover:bg-amber-500 text-white text-xs font-semibold">Confirm</button>' +
          '</div>' +
        '</div>';
      document.body.appendChild(wrap);
      if (window.lucide) window.lucide.createIcons();
      function done(v) { wrap.remove(); resolve(v); }
      wrap.querySelector('[data-cancel]').addEventListener('click', function () { done(false); });
      wrap.querySelector('[data-confirm]').addEventListener('click', function () { done(true); });
      wrap.addEventListener('click', function (e) { if (e.target === wrap) done(false); });
    });
  }

  // ---------- Voice panel --------------------------------------------------
  function renderVoice(status) {
    S.ttsStatus = status || {};
    var statusEl = document.getElementById('jarvis-voice-status');
    var listEl = document.getElementById('settings-voice-list');
    if (!listEl) return;
    var providers = (status && status.providers) || {};
    var keys = Object.keys(providers);
    if (!keys.length) {
      statusEl && (statusEl.textContent = 'unknown');
      listEl.innerHTML = '<p class="text-slate-500">Provider status: unknown — no status payload returned.</p>';
      return;
    }
    var reachable = status && status.kokoro && status.kokoro.reachable;
    statusEl && (statusEl.textContent = (reachable ? 'Kokoro online' : 'Kokoro offline'));
    statusEl && (statusEl.className = 'text-[10px] font-mono px-2 py-0.5 rounded-full border ' +
      (reachable ? 'bg-emerald-950/60 border-emerald-800/60 text-emerald-300' : 'bg-slate-800/80 border-slate-700 text-slate-400'));

    var html = '';
    keys.forEach(function (pid) {
      var p = providers[pid] || {};
      var enabled = !!p.enabled;
      var badge = enabled
        ? '<span class="text-[9px] px-1.5 py-0.5 rounded-full bg-emerald-950/60 border border-emerald-800/60 text-emerald-300">available</span>'
        : '<span class="text-[9px] px-1.5 py-0.5 rounded-full bg-slate-800 border border-slate-700 text-slate-400">needs key / offline</span>';
      var note = '';
      if (pid === 'fish') {
        note = '<div class="mt-1 text-[10px] text-slate-500">Verified against official docs: POST /v1/tts, key via FISH_API_KEY. No key configured here, so no live call tested.</div>';
      } else if (pid === 'kokoro') {
        note = '<div class="mt-1 text-[10px] text-slate-500">Local service at ' + esc((status.kokoro && status.kokoro.url) || '127.0.0.1:8880') +
          ' — ' + (reachable ? 'reachable' : 'unreachable') + '</div>';
      }
      html += '<div class="rounded-lg border border-slate-800 bg-slate-950/40 p-2 flex items-center justify-between gap-2">' +
        '<div class="min-w-0"><div class="font-semibold text-slate-200">' + esc(p.label || pid) + '</div>' +
        '<div class="text-[10px] text-slate-500">' + (p.local ? 'local' : 'cloud') + ' · ' + (p.requires_key ? 'requires key' : 'no key') + '</div>' + note + '</div>' +
        badge + '</div>';
    });
    if (status && status.browserVoiceFallback) {
      html = '<div class="rounded-lg border border-amber-700/50 bg-amber-950/30 p-2 text-[11px] text-amber-300">Browser voice API unavailable — use Push to talk or type a command. TTS playback still works.</div>' + html;
    }
    listEl.innerHTML = html;
  }

  function loadVoice() {
    return fetchJson('/api/tts/status').then(function (r) {
      var status = (r && r.config) ? r.config : r;
      // status() is returned directly; r may be the status object itself.
      if (r && r.providers) status = r;
      if (window.hermesApollo) status.browserVoiceFallback = !!window.hermesApollo.browserVoiceFallback;
      renderVoice(status);
    }).catch(function () {
      renderVoice({ providers: {}, kokoro: {} });
    });
  }

  // ---------- Connections panel -------------------------------------------
  function statusBadge(status) {
    var s = String(status || 'unknown');
    var cls = s === 'Connected'
      ? 'bg-emerald-950/60 border-emerald-800/60 text-emerald-300'
      : (s === 'Error' ? 'bg-rose-950/60 border-rose-800/60 text-rose-300'
        : 'bg-slate-800/80 border-slate-700 text-slate-400');
    return '<span class="text-[9px] font-mono px-1.5 py-0.5 rounded-full border ' + cls + '">' + esc(s) + '</span>';
  }

  function renderConnections(sources) {
    var listEl = document.getElementById('settings-connections-list');
    if (!listEl) return;
    if (!sources || !sources.length) {
      listEl.innerHTML = '<p class="text-slate-500">Connections: unknown — no status returned.</p>';
      return;
    }
    var html = '';
    sources.forEach(function (c) {
      html += '<div class="rounded-lg border border-slate-800 bg-slate-950/40 p-2" data-conn="' + attr(c.id) + '">' +
        '<div class="flex items-center justify-between gap-2">' +
          '<span class="font-semibold text-slate-200">' + esc(c.label) + '</span>' +
          '<span class="flex items-center gap-1.5">' + statusBadge(c.status) +
            '<button class="conn-test hud-icon-btn" data-id="' + attr(c.id) + '" title="Test this connection (read-only probe)"><i data-lucide="activity" class="w-3.5 h-3.5"></i></button>' +
          '</span>' +
        '</div>' +
        '<div class="mt-1 text-[10px] text-slate-500 break-words">' + esc(c.detail) + '</div>' +
        '<div class="mt-1 text-[9px] text-slate-600 font-mono break-all">source: ' + esc(c.source) + '</div>' +
      '</div>';
    });
    listEl.innerHTML = html;
    if (window.lucide) window.lucide.createIcons();
    listEl.querySelectorAll('.conn-test').forEach(function (btn) {
      btn.addEventListener('click', function () {
        var id = btn.getAttribute('data-id');
        btn.disabled = true;
        fetchJson(API + '/connections/test?id=' + encodeURIComponent(id)).then(function (r) {
          var row = btn.closest('[data-conn]');
          if (row && r) {
            var badge = row.querySelector('span.font-mono');
            if (badge) badge.outerHTML = statusBadge(r.status);
            var detail = row.querySelector('.text-\\[10px\\]');
            if (detail) detail.textContent = String(r.detail || 'unknown') + ' (tested ' + (r.tested_at || 'unknown') + ')';
          }
          toast('Connection test: ' + (r && r.label ? r.label : id) + ' — ' + (r && r.status ? r.status : 'unknown'));
        }).catch(function () { toast('Connection test failed'); })
          .then(function () { btn.disabled = false; });
      });
    });
  }

  function loadConnections() {
    return fetchJson(API + '/connections').then(function (r) {
      renderConnections(r && r.sources);
    }).catch(function () { renderConnections([]); });
  }

  // ---------- Audit trail --------------------------------------------------
  function renderAudit(entries) {
    var el = document.getElementById('settings-audit-list');
    if (!el) return;
    if (!entries || !entries.length) {
      el.innerHTML = '<p class="text-slate-500">No audit entries yet. Write actions will appear here.</p>';
      return;
    }
    el.innerHTML = entries.map(function (e) {
      return '<div class="flex flex-wrap gap-x-3 gap-y-0.5 py-0.5 border-b border-slate-800/60">' +
        '<span class="text-cyan-400">' + esc(e.who) + '</span>' +
        '<span class="text-slate-300">' + esc(e.what) + '</span>' +
        '<span class="text-slate-500">' + esc(e.when) + '</span>' +
        '<span class="text-amber-300">' + esc(e.result) + '</span>' +
      '</div>';
    }).join('');
  }

  function loadAudit() {
    return fetchJson(API + '/audit?limit=100').then(function (r) {
      renderAudit(r && r.entries);
    }).catch(function () { renderAudit([]); });
  }

  // ---------- Genesis panel ------------------------------------------------
  function genesisRow(item, kind) {
    var actions = '';
    if (kind === 'approval') {
      actions = '<button class="gen-action px-2 py-0.5 rounded bg-emerald-700 hover:bg-emerald-600 text-white text-[10px]" data-action="approve">' + esc(item.id) + ' · Approve</button>' +
        '<button class="gen-action px-2 py-0.5 rounded bg-rose-700 hover:bg-rose-600 text-white text-[10px]" data-action="reject">Reject</button>';
    } else if (kind === 'stopped') {
      actions = '<button class="gen-action px-2 py-0.5 rounded bg-amber-700 hover:bg-amber-600 text-white text-[10px]" data-action="restart">Restart (nearest action)</button>';
    }
    var extra = '';
    if (item.why) extra = '<div class="mt-1 text-[10px] text-rose-300/90">Stopped: ' + esc(item.why) + '</div>';
    if (item.question) extra += '<div class="mt-1 text-[10px] text-amber-300/90">Question: ' + esc(item.question) + '</div>';
    if (item.impact) extra += '<div class="mt-1 text-[10px] text-slate-500">Impact: ' + esc(item.impact) + '</div>';
    if (item.doing) extra += '<div class="mt-1 text-[10px] text-cyan-300/80">Doing: ' + esc(item.doing) + '</div>';
    return '<div class="rounded-lg border border-slate-800 bg-slate-950/40 p-2" data-gen-id="' + attr(item.id) + '" data-gen-kind="' + attr(kind) + '">' +
      '<div class="flex items-center justify-between gap-2">' +
        '<span class="font-semibold text-slate-200 truncate">' + esc(item.title) + ' <span class="text-[9px] font-mono text-slate-500">' + esc(item.status) + '</span></span>' +
        '<span class="flex gap-1 shrink-0">' + actions + '</span>' +
      '</div>' +
      '<div class="mt-1 text-[9px] text-slate-600 font-mono break-all">source: ' + esc(item.source) + ' · activity: ' + esc(item.activity || 'unknown') + '</div>' +
      extra + '</div>';
  }

  function renderGenesis(panel) {
    var el = document.getElementById('jarvis-genesis-content');
    if (!el) return;
    if (!panel) {
      el.innerHTML = '<p class="text-slate-500">Genesis: unknown — no data returned.</p>';
      return;
    }
    var w = panel.working_on || [];
    var a = panel.needs_approval || [];
    var s = panel.stopped || [];
    var html = '<div class="text-[10px] text-slate-500 mb-1">Read at ' + esc(panel.read_at) + ' · read-only snapshot</div>';
    html += '<div class="font-semibold text-cyan-300">Working on (' + w.length + ')</div>';
    html += w.length ? w.map(function (i) { return genesisRow(i, 'working'); }).join('') : '<div class="text-slate-500">none recorded</div>';
    html += '<div class="font-semibold text-amber-300 mt-2">Needs approval (' + a.length + ')</div>';
    html += a.length ? a.map(function (i) { return genesisRow(i, 'approval'); }).join('') : '<div class="text-slate-500">none recorded</div>';
    html += '<div class="font-semibold text-rose-300 mt-2">Stopped (' + s.length + ')</div>';
    html += s.length ? s.map(function (i) { return genesisRow(i, 'stopped'); }).join('') : '<div class="text-slate-500">none recorded</div>';
    el.innerHTML = html;
    if (window.lucide) window.lucide.createIcons();
    el.querySelectorAll('.gen-action').forEach(function (btn) {
      btn.addEventListener('click', function () { handleGenesisAction(btn); });
    });
  }

  function loadGenesis() {
    return fetchJson(API + '/genesis').then(renderGenesis)
      .catch(function () { renderGenesis(null); });
  }

  function handleGenesisAction(btn) {
    var row = btn.closest('[data-gen-id]');
    if (!row) return;
    var id = row.getAttribute('data-gen-id');
    var kind = row.getAttribute('data-gen-kind');
    var action = btn.getAttribute('data-action');
    var item = {
      id: id,
      title: row.querySelector('.font-semibold') ? row.querySelector('.font-semibold').textContent.trim() : id,
      restart: (kind === 'stopped')
        ? (String(id).indexOf('pub') === 0 ? 'publication' : 'render')
        : ''
    };
    var verb = action === 'approve' ? 'approve' : (action === 'reject' ? 'reject' : 'restart');
    var detail = 'Project / render id: ' + id + '\nAction: ' + verb;
    if (action === 'restart') {
      detail += '\nRestart maps to the nearest existing endpoint (no native restart exists).';
      var whyEl = row.querySelector('.text-rose-300\\/90');
      if (whyEl) detail += '\nStop reason shown: ' + whyEl.textContent.replace(/^Stopped:\s*/, '');
    }
    confirmAction('Confirm Genesis ' + verb,
      'This will send a ' + verb + ' request for this item through the existing local Genesis API. The action and its result will be written to the append-only audit log.',
      detail).then(function (ok) {
        if (!ok) { toast('Genesis ' + verb + ' cancelled'); return; }
        btn.disabled = true;
        postJson(API + '/genesis/action', {
          confirmed: true, kind: action, item: item,
          note: 'Confirmed via JARVIS Settings mission strip'
        }).then(function (r) {
          toast(r && r.ok ? 'Genesis ' + verb + ' OK' : 'Genesis ' + verb + ' failed: ' + ((r && r.error) || 'HTTP ' + (r && r.status)));
          return loadGenesis();
        }).catch(function (e) { toast('Genesis ' + verb + ' error: ' + e.message); })
          .then(function () { btn.disabled = false; loadAudit(); });
      });
  }

  // ---------- Trading Den brief (read-only) --------------------------------
  function renderTrading(brief) {
    var el = document.getElementById('jarvis-trading-content');
    if (!el) return;
    if (!brief) {
      el.innerHTML = '<p class="text-slate-500">Trading Den brief: unknown — no data returned.</p>';
      return;
    }
    var p = brief.pnl_today || {};
    var b = brief.bots || {};
    var arb = brief.arbitrage || {};
    var matrix = brief.matrix || {};
    var demoWarn = '<div class="rounded-lg border border-amber-600/50 bg-amber-950/30 p-2 text-[10px] font-semibold text-amber-300 mb-2">⚠ DEMO DATA — values below are seeded/labelled demo unless explicitly marked live. No buy/sell/control action is available here.</div>';
    var html = demoWarn;
    var bd = p.browser_demo || {};
    html += '<div class="rounded-lg border border-amber-700/40 bg-amber-950/20 p-2">' +
      '<div class="font-semibold text-amber-200">Demo values shown in the DenMa browser</div>' +
      '<div class="mt-1 grid grid-cols-3 gap-1 text-center">' +
        '<div><div class="text-[9px] text-slate-500">PNL today</div><div class="text-sm font-bold text-amber-300">' + esc(bd.pnl_today) + '</div></div>' +
        '<div><div class="text-[9px] text-slate-500">Realized</div><div class="text-sm font-bold text-amber-300">' + esc(bd.realized_pnl) + '</div></div>' +
        '<div><div class="text-[9px] text-slate-500">NAV</div><div class="text-sm font-bold text-amber-300">' + esc(bd.nav_connected_exchanges) + '</div></div>' +
      '</div>' +
      '<div class="mt-1 text-[9px] text-slate-600 font-mono break-all">' + esc(bd.source || 'unknown') + '</div>' +
      '</div>';
    html += '<div class="mt-1.5 rounded-lg border border-slate-800 bg-slate-950/40 p-2">' +
      '<div class="font-semibold text-slate-200">PNL today (database evidence)</div>' +
      '<div class="text-lg font-bold ' + (Number(p.value) > 0 ? 'text-emerald-300' : 'text-slate-200') + '">' + esc(p.value) + '</div>' +
      '<div class="text-[10px] text-amber-300/90">' + esc(p.label) + '</div>' +
      '<div class="mt-1 text-[10px] text-slate-500">today rows: ' + esc(p.today_rows) + ' · today amount: ' + esc(p.today_amount) + ' · unrealized: ' + esc(p.unrealized) + ' · fees: ' + esc(p.fees) + '</div>' +
      '<div class="mt-1 text-[9px] text-slate-600 font-mono break-all">' + esc(p.source || 'unknown') + '</div>' +
      '</div>';
    html += '<div class="mt-1.5 rounded-lg border border-slate-800 bg-slate-950/40 p-2">' +
      '<div class="font-semibold text-slate-200">Bots</div>' +
      '<div class="text-[11px] text-slate-300">running: ' + esc(b.running) + ' · stopped: ' + esc(b.stopped) + '</div>' +
      '<div class="text-[10px] text-amber-300/90">' + esc(b.label) + '</div>' +
      '</div>';
    html += '<div class="mt-1.5 rounded-lg border border-slate-800 bg-slate-950/40 p-2">' +
      '<div class="font-semibold text-slate-200">Arbitrage ranking</div>' +
      '<div class="text-[11px] text-slate-300">leader: ' + esc(arb.leader) + ' · margin: ' + esc(arb.margin) + '</div>' +
      '<div class="text-[10px] text-amber-300/90">' + esc(arb.label) + '</div>' +
      '</div>';
    html += '<div class="mt-1.5 rounded-lg border border-slate-800 bg-slate-950/40 p-2">' +
      '<div class="font-semibold text-slate-200">Cross-exchange matrix <span class="text-[9px] font-normal text-slate-500">' + esc(matrix.status) + '</span></div>' +
      (matrix.items && matrix.items.length
        ? matrix.items.map(function (i) {
            return '<div class="mt-1 text-[10px] text-slate-400">' + esc(i.asset) + ' · buy ' + esc(i.buy_venue) + ' → sell ' + esc(i.sell_venue) +
              ' · gross ' + esc(i.gross_spread_pct) + '% · net ' + esc(i.net_spread_pct) + '% · ' + (i.stale ? 'STALE' : 'fresh') + '</div>';
          }).join('')
        : '<div class="text-[10px] text-slate-500">No opportunities · snapshot: ' + esc(matrix.snapshot_time) + '</div>') +
      '<div class="mt-1 text-[9px] text-slate-600">' + esc(matrix.stale_flag) + '</div>' +
      '</div>';
    el.innerHTML = html;
  }

  function loadTrading() {
    return fetchJson(API + '/trading-brief').then(renderTrading)
      .catch(function () { renderTrading(null); });
  }

  // ---------- public API ---------------------------------------------------
  var JarvisOs = {
    refresh: function () {
      loadVoice();
      loadConnections();
      loadAudit();
      loadGenesis();
      loadTrading();
    },
    route: function (cmd) {
      var r = syncGet(API + '/route?q=' + encodeURIComponent(cmd || ''));
      if (r && r.handled) return r;
      return { handled: false, intent: 'unknown', speech: '', data: null };
    }
  };
  window.JarvisOs = JarvisOs;

  // ---------- DOM wiring ---------------------------------------------------
  document.addEventListener('DOMContentLoaded', function () {
    authToken(); // prime token for route + writes

    // Settings preview
    var previewBtn = document.getElementById('settings-voice-preview');
    var previewText = document.getElementById('settings-voice-preview-text');
    var previewResult = document.getElementById('settings-voice-preview-result');
    if (previewBtn) {
      previewBtn.addEventListener('click', function () {
        var text = (previewText && previewText.value.trim()) || 'At your service, sir.';
        var voice = 'bm_george';
        if (window.DennisTTS && window.DennisTTS.preview) {
          previewResult && (previewResult.textContent = 'Playing preview with ' + voice + '…');
          try {
            var p = window.DennisTTS.preview(voice, text);
            if (p && p.catch) p.catch(function (e) { previewResult && (previewResult.textContent = 'Preview failed: ' + e.message); });
          } catch (e) {
            previewResult && (previewResult.textContent = 'Preview failed: ' + e.message);
          }
        } else {
          previewResult && (previewResult.textContent = 'TTS client unavailable — preview not possible.');
        }
      });
    }
    var connRefresh = document.getElementById('settings-connections-refresh');
    if (connRefresh) connRefresh.addEventListener('click', loadConnections);
    var auditRefresh = document.getElementById('settings-audit-refresh');
    if (auditRefresh) auditRefresh.addEventListener('click', loadAudit);

    // Mission strip toggle + refresh
    var missionBtn = document.getElementById('jarvis-mission-toggle');
    var missionStrip = document.getElementById('jarvis-mission-strip');
    if (missionBtn && missionStrip) {
      missionBtn.addEventListener('click', function () {
        var show = missionStrip.classList.contains('hidden');
        missionStrip.classList.toggle('hidden', !show);
        missionBtn.setAttribute('aria-pressed', show ? 'true' : 'false');
        if (show) { loadGenesis(); loadTrading(); }
        if (window.lucide) window.lucide.createIcons();
      });
    }
    var genRefresh = document.getElementById('jarvis-genesis-refresh');
    if (genRefresh) genRefresh.addEventListener('click', loadGenesis);
    var trRefresh = document.getElementById('jarvis-trading-refresh');
    if (trRefresh) trRefresh.addEventListener('click', loadTrading);
  });
})();
