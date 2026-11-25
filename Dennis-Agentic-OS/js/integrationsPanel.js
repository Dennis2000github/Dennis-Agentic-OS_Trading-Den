/* =========================================================================
   Shared COSMOS integrations panel — connection unblock UI
   -------------------------------------------------------------------------
   One owner-facing surface for the single integration router:
     * provider truth (Shopee credentials / Composio session);
     * secure Shopee credential save (values are never echoed or logged);
     * two-step YouTube session link (get link, then explicitly open it);
     * read-only verification;
     * shared execution history and the one pending-approval queue.

   Rules:
     * the browser receives status only — never a provider secret;
     * the Composio link is never opened automatically;
     * approving a pending action is explicit and confirmed because it may
       execute an external/public/write action.
   ========================================================================= */
(function () {
  'use strict';

  var PANEL_ID = 'shared-integrations-panel';
  var state = { link: null, busy: false };

  function $(id) {
    return document.getElementById(id);
  }

  function esc(value) {
    return String(value == null ? '' : value).replace(/[&<>"']/g, function (ch) {
      return {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[ch];
    });
  }

  function short(value, limit) {
    var text = String(value == null ? '' : value);
    return text.length > (limit || 220) ? text.slice(0, limit || 220) + '…' : text;
  }

  function jsonView(value) {
    try {
      return short(JSON.stringify(value, null, 2), 2400);
    } catch (err) {
      return short(String(value), 2400);
    }
  }

  function api(path, options) {
    var opts = options || {};
    opts.headers = Object.assign({}, opts.headers || {}, {'Content-Type': 'application/json'});
    return fetch(path, opts).then(function (resp) {
      return resp.text().then(function (raw) {
        var data;
        try {
          data = raw ? JSON.parse(raw) : {};
        } catch (err) {
          data = {error: raw || ('HTTP ' + resp.status)};
        }
        if (!resp.ok) {
          var failure = new Error(data.error || data.message || ('HTTP ' + resp.status));
          failure.payload = data;
          throw failure;
        }
        return data;
      });
    });
  }

  function settled(promise) {
    return promise.then(
      function (value) { return {ok: true, value: value}; },
      function (error) { return {ok: false, error: error}; }
    );
  }

  function badge(status) {
    var raw = String(status || 'unknown').toLowerCase();
    var label = raw;
    var tone = 'bg-slate-800/80 border-slate-700 text-slate-400';
    if (raw === 'active' || raw === 'connected' || raw === 'ok' || raw === 'ready') {
      label = 'ready';
      tone = 'bg-emerald-950/70 border-emerald-800/60 text-emerald-300';
    } else if (raw === 'initiated' || raw === 'session_initiated' || raw === 'pending') {
      label = 'session pending';
      tone = 'bg-amber-950/70 border-amber-800/60 text-amber-300';
    } else if (raw === 'not_configured' || raw === 'missing' || raw === 'disabled') {
      label = raw.replace(/_/g, ' ');
      tone = 'bg-slate-800/80 border-slate-700 text-slate-400';
    } else if (raw === 'blocked' || raw === 'execution_blocked' || raw === 'failed') {
      label = 'blocked';
      tone = 'bg-rose-950/70 border-rose-800/60 text-rose-300';
    }
    return '<span class="text-[10px] font-mono px-2 py-0.5 rounded-full border ' +
      tone + '">' + esc(label) + '</span>';
  }

  function template() {
    return [
      '<div class="flex items-center justify-between">',
      '  <span id="si-overall" class="text-[11px] font-mono text-slate-400">checking router…</span>',
      '  <span class="text-[10px] font-mono text-slate-500">status only · no secrets</span>',
      '</div>',
      '<div class="grid grid-cols-1 lg:grid-cols-2 gap-3">',
      '  <section class="rounded-xl border border-slate-800 bg-slate-950/40 p-3 flex flex-col gap-2">',
      '    <div class="flex items-center justify-between">',
      '      <h4 class="text-xs font-bold text-white flex items-center gap-2"><i data-lucide="shopping-bag" class="w-3.5 h-3.5 text-emerald-400"></i> Shopee Affiliate</h4>',
      '      <span id="si-shopee-state">checking…</span>',
      '    </div>',
      '    <div id="si-shopee-detail" class="text-[11px] text-slate-400 space-y-1"></div>',
      '    <form id="si-shopee-form" class="space-y-2 text-[11px]">',
      '      <label class="block">',
      '        <span class="block text-[10px] font-mono uppercase tracking-wider text-slate-500 mb-1">AppId</span>',
      '        <input id="si-app-id" type="text" autocomplete="off" spellcheck="false" class="w-full bg-slate-950/70 border border-slate-700 rounded-lg px-2.5 py-1.5 text-xs text-slate-200 focus:border-cyan-500 focus:outline-none">',
      '      </label>',
      '      <label class="block">',
      '        <span class="block text-[10px] font-mono uppercase tracking-wider text-slate-500 mb-1">Secret (write-only)</span>',
      '        <input id="si-secret" type="password" autocomplete="new-password" spellcheck="false" class="w-full bg-slate-950/70 border border-slate-700 rounded-lg px-2.5 py-1.5 text-xs text-slate-200 focus:border-cyan-500 focus:outline-none">',
      '      </label>',
      '      <label class="block">',
      '        <span class="block text-[10px] font-mono uppercase tracking-wider text-slate-500 mb-1">Endpoint (optional; unconfirmed PH default)</span>',
      '        <input id="si-endpoint" type="text" autocomplete="off" spellcheck="false" placeholder="https://open-api.affiliate.shopee.ph/graphql" class="w-full bg-slate-950/70 border border-slate-700 rounded-lg px-2.5 py-1.5 text-xs text-slate-200 focus:border-cyan-500 focus:outline-none">',
      '      </label>',
      '      <div class="flex flex-wrap gap-2 pt-1">',
      '        <button id="si-shopee-save" type="submit" class="px-3 py-1.5 rounded-lg bg-cyan-600 hover:bg-cyan-500 text-white text-xs font-semibold transition">Save locally (0600)</button>',
      '        <button id="si-shopee-verify" type="button" class="px-3 py-1.5 rounded-lg bg-slate-700 hover:bg-slate-600 text-slate-100 text-xs font-semibold transition">Verify signing (read-only)</button>',
      '      </div>',
      '    </form>',
      '    <p class="text-[10px] text-slate-500 leading-relaxed">Credentials are saved backend-side only. The form never displays or returns the secret. Saving makes no Shopee network call.</p>',
      '    <pre id="si-shopee-result" class="hidden whitespace-pre-wrap break-all text-[10px] font-mono text-slate-400 bg-slate-950/70 border border-slate-800 rounded-lg p-2"></pre>',
      '  </section>',
      '  <section class="rounded-xl border border-slate-800 bg-slate-950/40 p-3 flex flex-col gap-2">',
      '    <div class="flex items-center justify-between">',
      '      <h4 class="text-xs font-bold text-white flex items-center gap-2"><i data-lucide="youtube" class="w-3.5 h-3.5 text-rose-400"></i> YouTube (Composio)</h4>',
      '      <span id="si-youtube-state">checking…</span>',
      '    </div>',
      '    <div id="si-youtube-detail" class="text-[11px] text-slate-400 space-y-1"></div>',
      '    <div class="flex flex-wrap gap-2 pt-1">',
      '      <button id="si-yt-link" type="button" class="px-3 py-1.5 rounded-lg bg-cyan-600 hover:bg-cyan-500 text-white text-xs font-semibold transition">Get connect link</button>',
      '      <button id="si-yt-open" type="button" class="hidden px-3 py-1.5 rounded-lg bg-emerald-600 hover:bg-emerald-500 text-white text-xs font-semibold transition">Open connect link</button>',
      '      <button id="si-yt-check" type="button" class="px-3 py-1.5 rounded-lg bg-slate-700 hover:bg-slate-600 text-slate-100 text-xs font-semibold transition">Refresh session</button>',
      '      <button id="si-yt-verify" type="button" class="px-3 py-1.5 rounded-lg bg-slate-700 hover:bg-slate-600 text-slate-100 text-xs font-semibold transition">Verify read-only</button>',
      '    </div>',
      '    <div id="si-youtube-link" class="hidden text-[10px] font-mono text-cyan-300 bg-slate-950/70 border border-slate-800 rounded-lg p-2 break-all"></div>',
      '    <p class="text-[10px] text-slate-500 leading-relaxed">The existing project connection is reused. Opening the link is a separate explicit click; verification uses GET list_channels only and never modifies the channel.</p>',
      '    <pre id="si-youtube-result" class="hidden whitespace-pre-wrap break-all text-[10px] font-mono text-slate-400 bg-slate-950/70 border border-slate-800 rounded-lg p-2"></pre>',
      '  </section>',
      '</div>',
      '<div class="grid grid-cols-1 lg:grid-cols-3 gap-3">',
      '  <section class="rounded-xl border border-slate-800 bg-slate-950/40 p-3">',
      '    <h4 class="text-xs font-bold text-white mb-2">Shared capabilities</h4>',
      '    <div id="si-capabilities" class="space-y-1 text-[11px] text-slate-400"><p class="text-slate-500">Loading…</p></div>',
      '  </section>',
      '  <section class="rounded-xl border border-slate-800 bg-slate-950/40 p-3">',
      '    <div class="flex items-center justify-between mb-2">',
      '      <h4 class="text-xs font-bold text-white">Pending approvals</h4>',
      '      <span class="text-[10px] text-slate-500">one shared queue</span>',
      '    </div>',
      '    <div id="si-approvals" class="space-y-2 text-[11px] text-slate-400"><p class="text-slate-500">Loading…</p></div>',
      '    <pre id="si-approvals-result" class="hidden whitespace-pre-wrap break-all text-[10px] font-mono text-slate-400 bg-slate-950/70 border border-slate-800 rounded-lg p-2"></pre>',
      '  </section>',
      '  <section class="rounded-xl border border-slate-800 bg-slate-950/40 p-3">',
      '    <h4 class="text-xs font-bold text-white mb-2">Execution history</h4>',
      '    <div id="si-executions" class="space-y-1 text-[10px] font-mono text-slate-400"><p class="text-slate-500">Loading…</p></div>',
      '  </section>',
      '</div>'
    ].join('');
  }

  function showBlock(id, data) {
    var el = $(id);
    if (!el) return;
    el.classList.remove('hidden');
    el.textContent = jsonView(data && data.payload ? data.payload : data);
  }

  function setBusy(on) {
    state.busy = !!on;
    var host = $(PANEL_ID);
    if (!host) return;
    var buttons = host.querySelectorAll('button');
    for (var i = 0; i < buttons.length; i += 1) {
      buttons[i].disabled = state.busy && buttons[i].id !== 'si-yt-open';
    }
  }

  function renderOverall(result) {
    var el = $('si-overall');
    if (!el) return;
    if (!result.ok) {
      el.textContent = 'COSMOS projection unavailable: ' + short(result.error.message, 160);
      return;
    }
    var view = (result.value && result.value.integrations) || {};
    if (!view.available) {
      el.textContent = 'Router unavailable: ' + short(view.error || 'unknown error', 160);
      return;
    }
    var providers = (view.providers || []).map(function (row) {
      return (row.id || '?') + ': ' + (row.status || 'unknown');
    });
    el.textContent = providers.length ? providers.join(' · ') : 'no shared providers';
  }

  function renderShopee(result) {
    var stateEl = $('si-shopee-state');
    var detailEl = $('si-shopee-detail');
    if (!stateEl || !detailEl) return;
    if (!result.ok) {
      stateEl.innerHTML = badge('failed');
      detailEl.textContent = short(result.error.message, 300);
      return;
    }
    var row = result.value || {};
    var cred = (row.detail && row.detail.credentials) || {};
    stateEl.innerHTML = badge(row.status || (cred.configured ? 'connected' : 'not_configured'));
    var lines = [
      '<div>credential source: <span class="text-slate-300">' + esc(cred.source || 'none') + '</span></div>',
      '<div>endpoint: <span class="text-slate-300 break-all">' + esc(row.detail && row.detail.endpoint || row.endpoint || 'unknown') + '</span></div>'
    ];
    if (row.warning) lines.push('<div class="text-amber-300">' + esc(row.warning) + '</div>');
    detailEl.innerHTML = lines.join('');
  }

  function renderYouTube(result) {
    var stateEl = $('si-youtube-state');
    var detailEl = $('si-youtube-detail');
    if (!stateEl || !detailEl) return;
    if (!result.ok) {
      stateEl.innerHTML = badge('failed');
      detailEl.textContent = short(result.error.message, 300);
      return;
    }
    var row = result.value || {};
    var available = !!row.execution_available;
    if (available) state.link = null;
    stateEl.innerHTML = badge(available ? 'ready' : (row.session && row.session.status) || 'blocked');
    var connection = row.connection || {};
    var session = row.session || {};
    var lines = [
      '<div>project connection: <span class="text-slate-300">' + esc(connection.status || 'unknown') + '</span>' +
        (connection.id ? ' <span class="text-slate-500">(' + esc(connection.id) + ')</span>' : '') + '</div>',
      '<div>tool-router session: <span class="text-slate-300">' + esc(session.status || 'unknown') + '</span></div>'
    ];
    if (!available && row.blocked_reason) {
      lines.push('<div class="text-amber-300">' + esc(row.blocked_reason) + '</div>');
    }
    if (!available && !state.link && session.redirect_url) {
      lines.push('<div class="text-slate-500">A session link exists; click “Get connect link” to reveal it.</div>');
    }
    detailEl.innerHTML = lines.join('');
    var linkBox = $('si-youtube-link');
    var openButton = $('si-yt-open');
    if (state.link) {
      linkBox.classList.remove('hidden');
      linkBox.textContent = state.link;
      openButton.classList.remove('hidden');
    } else {
      linkBox.classList.add('hidden');
      openButton.classList.add('hidden');
    }
  }

  function renderCapabilities(result) {
    var el = $('si-capabilities');
    if (!el) return;
    if (!result.ok) {
      el.textContent = short(result.error.message, 240);
      return;
    }
    var rows = ((result.value && result.value.integrations) || {}).capabilities || [];
    if (!rows.length) {
      el.innerHTML = '<p class="text-slate-500">No shared capabilities available yet.</p>';
      return;
    }
    el.innerHTML = rows.map(function (row) {
      return '<div class="flex items-center justify-between gap-2 border-b border-slate-800/70 pb-1">' +
        '<span class="truncate text-slate-300">' + esc(row.id || row.label || 'unknown') + '</span>' +
        badge(row.ready ? 'ready' : (row.status || 'blocked')) + '</div>';
    }).join('');
  }

  function renderExecutions(result) {
    var el = $('si-executions');
    if (!el) return;
    if (!result.ok) {
      el.textContent = short(result.error.message, 240);
      return;
    }
    var rows = (result.value && result.value.executions) || [];
    if (!rows.length) {
      el.innerHTML = '<p class="text-slate-500">No executions recorded yet.</p>';
      return;
    }
    el.innerHTML = rows.slice(0, 8).map(function (row) {
      return '<div class="flex items-center justify-between gap-2 border-b border-slate-800/70 pb-1">' +
        '<span class="truncate">' + esc(row.started_iso || row.id || '?') + ' · ' +
          esc(row.provider || '?') + '/' + esc(row.action || '?') + '</span>' +
        badge(row.status) + '</div>';
    }).join('');
  }

  function renderApprovals(result) {
    var el = $('si-approvals');
    if (!el) return;
    if (!result.ok) {
      el.textContent = short(result.error.message, 240);
      return;
    }
    var rows = (result.value && result.value.approvals) || [];
    if (!rows.length) {
      el.innerHTML = '<p class="text-slate-500">No pending actions. Writes are gated until you approve them here.</p>';
      return;
    }
    el.innerHTML = rows.map(function (row) {
      var preview = jsonView(row.preview || {});
      return '<div class="rounded-lg border border-slate-800 bg-slate-950/60 p-2 space-y-2">' +
        '<div class="flex items-center justify-between gap-2">' +
          '<span class="font-mono text-cyan-300 truncate">' + esc(row.tool || 'unknown') + '</span>' +
          badge(row.status || 'pending') + '</div>' +
        '<pre class="whitespace-pre-wrap break-all text-[10px] text-slate-500">' + esc(preview) + '</pre>' +
        '<div class="flex gap-2">' +
          '<button type="button" data-si-decision="approve" data-approval-id="' + esc(row.id) + '" class="px-2 py-1 rounded bg-emerald-700 hover:bg-emerald-600 text-white text-[10px] font-semibold">Approve &amp; execute</button>' +
          '<button type="button" data-si-decision="reject" data-approval-id="' + esc(row.id) + '" class="px-2 py-1 rounded bg-slate-700 hover:bg-slate-600 text-white text-[10px] font-semibold">Reject</button>' +
        '</div></div>';
    }).join('');
  }

  function refresh() {
    setBusy(true);
    Promise.all([
      settled(api('/api/cosmos/integrations')),
      settled(api('/api/integrations/providers/shopee')),
      settled(api('/api/integrations/youtube/connection')),
      settled(api('/api/integrations/executions?limit=8')),
      settled(api('/api/studio/approvals?status=pending'))
    ]).then(function (results) {
      renderOverall(results[0]);
      renderShopee(results[1]);
      renderYouTube(results[2]);
      renderExecutions(results[3]);
      renderApprovals(results[4]);
      renderCapabilities(results[0]);
      icons();
      setBusy(false);
    }, function () {
      setBusy(false);
    });
  }

  function icons() {
    try {
      if (window.lucide && typeof window.lucide.createIcons === 'function') {
        window.lucide.createIcons();
      }
    } catch (err) {
      /* icon library is optional */
    }
  }

  function saveShopee() {
    var appId = $('si-app-id');
    var secret = $('si-secret');
    var endpoint = $('si-endpoint');
    if (!appId.value.trim() || !secret.value.trim()) {
      showBlock('si-shopee-result', {error: 'AppId and secret are required.'});
      return;
    }
    var payload = {app_id: appId.value.trim(), secret: secret.value.trim()};
    if (endpoint.value.trim()) payload.endpoint = endpoint.value.trim();
    setBusy(true);
    api('/api/integrations/shopee/credentials', {
      method: 'POST', body: JSON.stringify(payload)
    }).then(function (data) {
      secret.value = '';
      showBlock('si-shopee-result', data);
      refresh();
    }, function (error) {
      secret.value = '';
      showBlock('si-shopee-result', error);
      setBusy(false);
    });
  }

  function getLink() {
    setBusy(true);
    api('/api/integrations/youtube/session/link', {
      method: 'POST', body: '{}'
    }).then(function (data) {
      state.link = data.redirect_url || data.session && data.session.redirect_url || null;
      if (state.link) {
        var box = $('si-youtube-link');
        var openButton = $('si-yt-open');
        box.classList.remove('hidden');
        box.textContent = state.link;
        openButton.classList.remove('hidden');
        showBlock('si-youtube-result', {
          note: 'Link disclosed. Open it explicitly, then click Refresh session.'
        });
      } else {
        showBlock('si-youtube-result', data);
      }
      setBusy(false);
    }, function (error) {
      showBlock('si-youtube-result', error);
      setBusy(false);
    });
  }

  function openLink() {
    if (!state.link) {
      showBlock('si-youtube-result', {error: 'Click “Get connect link” first.'});
      return;
    }
    if (!/^https:\/\/connect\.composio\.dev\/link\//.test(state.link)) {
      showBlock('si-youtube-result', {error: 'Refusing to open an unexpected link host.'});
      return;
    }
    window.open(state.link, '_blank', 'noopener,noreferrer');
    showBlock('si-youtube-result', {
      note: 'Link opened in a new tab. Return here and click Refresh session after completing it.'
    });
  }

  function verifyYouTube() {
    setBusy(true);
    api('/api/integrations/youtube/run/list_channels?mine=true&part=snippet')
      .then(function (data) {
        showBlock('si-youtube-result', data);
        setBusy(false);
      }, function (error) {
        showBlock('si-youtube-result', error);
        setBusy(false);
      });
  }

  function decide(button) {
    var id = button.getAttribute('data-approval-id');
    var decision = button.getAttribute('data-si-decision');
    var verb = decision === 'approve' ? 'APPROVE AND EXECUTE' : 'REJECT';
    if (!window.confirm(verb + ' this pending integration action?\n\n' +
        'Approval runs the external action; rejection only closes the request.')) {
      return;
    }
    setBusy(true);
    api('/api/studio/approvals/' + encodeURIComponent(id), {
      method: 'POST', body: JSON.stringify({decision: decision})
    }).then(function (data) {
      showBlock('si-approvals-result', data);
      refresh();
    }, function (error) {
      showBlock('si-approvals-result', error);
      setBusy(false);
    });
  }

  function closestButton(node, host) {
    while (node && node !== host) {
      if (node.tagName && node.tagName.toLowerCase() === 'button') return node;
      node = node.parentNode;
    }
    return null;
  }

  function bind(host) {
    host.addEventListener('submit', function (event) {
      if (event.target && event.target.id === 'si-shopee-form') {
        event.preventDefault();
        saveShopee();
      }
    });
    host.addEventListener('click', function (event) {
      var button = closestButton(event.target, host);
      if (!button) return;
      if (button.id === 'si-refresh') refresh();
      else if (button.id === 'si-shopee-verify') {
        setBusy(true);
        api('/api/integrations/shopee/auth/self-test').then(function (data) {
          showBlock('si-shopee-result', data);
          setBusy(false);
        }, function (error) {
          showBlock('si-shopee-result', error);
          setBusy(false);
        });
      } else if (button.id === 'si-yt-link') getLink();
      else if (button.id === 'si-yt-open') openLink();
      else if (button.id === 'si-yt-check') refresh();
      else if (button.id === 'si-yt-verify') verifyYouTube();
      else if (button.getAttribute('data-si-decision')) decide(button);
    });
  }

  function mount() {
    var host = $(PANEL_ID);
    if (!host || host.dataset && host.dataset.siMounted) return;
    if (host.dataset) host.dataset.siMounted = '1';
    host.innerHTML = template();
    bind(host);
    icons();
    refresh();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', mount);
  } else {
    mount();
  }
}());
