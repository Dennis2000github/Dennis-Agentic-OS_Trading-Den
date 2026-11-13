// Hermes Kanban board + orchestrator client.
// Used by the in-OS Hermes panel (js/hermes.js).
(function () {
  'use strict';
  var KS = window.KanbanState;

  var state = { token: null, snapshot: null, board: null, ws: null, logTask: null };

  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  function api(path, opts) {
    opts = opts || {};
    var headers = opts.headers || {};
    if (state.token) headers['X-Dennis-Token'] = state.token;
    if (opts.body !== undefined) headers['Content-Type'] = 'application/json';
    return fetch(path, {
      method: opts.method || 'GET',
      headers: headers,
      body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined
    }).then(function (res) {
      return res.json().catch(function () { return {}; }).then(function (data) {
        return { ok: res.ok, status: res.status, data: data };
      });
    });
  }

  function ensureToken() {
    if (state.token) return Promise.resolve(state.token);
    return api('/api/auth/session').then(function (r) {
      state.token = (r.data && r.data.token) || null;
      return state.token;
    });
  }

  function toast(msg) {
    if (window.showToast) return window.showToast(msg);
    console.log('[hermes]', msg);
  }

  function icon(name, cls) {
    return '<i data-lucide="' + name + '" class="' + (cls || 'w-3.5 h-3.5') + '"></i>';
  }

  function refreshIcons() {
    if (window.lucide && window.lucide.createIcons) window.lucide.createIcons();
  }

  // ---------------------------------------------------------------- data
  function fetchState() {
    return ensureToken().then(function () { return api('/api/hermes/state'); })
      .then(function (r) {
        if (r.ok) {
          state.snapshot = r.data;
          state.board = r.data.board;
        }
        return r;
      });
  }

  function createTask(payload) {
    return api('/api/hermes/tasks', { method: 'POST', body: payload });
  }

  function assignTask(taskId, agentId) {
    return api('/api/hermes/tasks/' + encodeURIComponent(taskId) + '/assign',
      { method: 'POST', body: { agent_id: agentId } });
  }

  function setStatus(taskId, status) {
    return api('/api/hermes/tasks/' + encodeURIComponent(taskId) + '/status',
      { method: 'POST', body: { status: status } });
  }

  function deleteTask(taskId) {
    return api('/api/hermes/tasks/' + encodeURIComponent(taskId), { method: 'DELETE' });
  }

  function planGoal(goal, brain) {
    return api('/api/hermes/plan', { method: 'POST', body: { goal: goal, brain: brain || 'vault' } });
  }

  function runGoal(goal, brain) {
    return api('/api/hermes/run', { method: 'POST', body: { goal: goal, brain: brain || 'vault' } });
  }

  function delegate(runId) {
    return api('/api/hermes/delegate', { method: 'POST', body: { run_id: runId } });
  }

  // ---------------------------------------------------------------- render
  function agentChip(task) {
    if (task.agent_id) {
      var auto = task.assignment === 'autonomous';
      return '<span class="kb-chip kb-chip-agent" title="' + (auto ? 'Assigned autonomously by Hermes' : 'Assigned manually') + '">' +
        icon('bot', 'w-3 h-3') + esc(task.agent_id) + (auto ? ' <em>auto</em>' : '') + '</span>';
    }
    return '<span class="kb-chip kb-chip-muted">' + icon('user-x', 'w-3 h-3') + 'unassigned</span>';
  }

  function cardEl(task, opts) {
    opts = opts || {};
    var card = document.createElement('div');
    card.className = 'kb-card kb-card-' + KS.columnOf(task);
    card.setAttribute('data-task', task.id);
    var preview = (task.result || '').replace(/\s+/g, ' ').trim().slice(0, 140);
    var err = task.error ? '<div class="kb-err">' + icon('alert-triangle', 'w-3 h-3') + esc(task.error) + '</div>' : '';
    card.innerHTML =
      '<div class="kb-card-top">' +
        '<span class="kb-chip kb-role kb-role-' + esc(task.role) + '">' + esc(KS.roleLabel(task.role)) + '</span>' +
        agentChip(task) +
      '</div>' +
      '<div class="kb-card-title">' + esc(task.title) + '</div>' +
      (preview ? '<div class="kb-preview">' + esc(preview) + '</div>' : '') + err +
      '<div class="kb-card-actions">' +
        (KS.movableFrom(KS.columnOf(task)).indexOf(KS.prevStatus(task.status)) !== -1 && KS.columnOf(task) !== 'backlog' ? '<button data-act="prev" title="Move back">' + icon('chevron-left', 'w-3.5 h-3.5') + '</button>' : '') +
        (KS.nextStatus(task.status) !== KS.columnOf(task) ? '<button data-act="next" title="Move forward">' + icon('chevron-right', 'w-3.5 h-3.5') + '</button>' : '') +
        (task.status === 'review' ? '<button data-act="done" title="Approve">' + icon('check', 'w-3.5 h-3.5') + '</button>' : '') +
        '<button data-act="log" title="Open live log">' + icon('terminal', 'w-3.5 h-3.5') + '</button>' +
        '<button data-act="del" class="kb-danger" title="Delete">' + icon('trash-2', 'w-3.5 h-3.5') + '</button>' +
      '</div>';

    card.addEventListener('click', function (e) {
      var btn = e.target.closest('button[data-act]');
      if (!btn) { if (opts.onSelect) opts.onSelect(task); return; }
      e.stopPropagation();
      var act = btn.dataset.act;
      if (act === 'prev' && opts.onMove) opts.onMove(task, KS.prevStatus(task.status));
      else if (act === 'next' && opts.onMove) opts.onMove(task, KS.nextStatus(task.status));
      else if (act === 'done' && opts.onMove) opts.onMove(task, 'done');
      else if (act === 'del' && opts.onDelete) opts.onDelete(task);
      else if (act === 'log') openLog(task);
    });
    return card;
  }

  function renderBoard(host, board, opts) {
    opts = opts || {};
    if (!host) return;
    if (!board) { host.innerHTML = '<div class="kb-empty">No board yet.</div>'; return; }
    var tasks = [];
    (board.columns || []).forEach(function (c) { tasks = tasks.concat(c.tasks || []); });
    var grouped = KS.group(tasks);
    var wrap = document.createElement('div');
    wrap.className = 'kb-board';
    KS.COLUMNS.forEach(function (col) {
      var colEl = document.createElement('div');
      colEl.className = 'kb-column kb-col-' + col.id;
      colEl.setAttribute('data-column', col.id);
      var items = grouped[col.id] || [];
      colEl.innerHTML = '<div class="kb-col-head"><span>' + col.label + '</span><span class="kb-count">' + items.length + '</span></div>' +
        '<div class="kb-col-body"></div>';
      var body = colEl.querySelector('.kb-col-body');
      items.forEach(function (t) { body.appendChild(cardEl(t, opts)); });
      if (!items.length) body.innerHTML = '<div class="kb-col-empty">—</div>';
      // drag & drop between columns
      if (opts.draggable !== false) {
        colEl.addEventListener('dragover', function (e) { e.preventDefault(); colEl.classList.add('kb-drop'); });
        colEl.addEventListener('dragleave', function () { colEl.classList.remove('kb-drop'); });
        colEl.addEventListener('drop', function (e) {
          e.preventDefault(); colEl.classList.remove('kb-drop');
          var id = e.dataTransfer.getData('text/plain');
          var task = items.concat(tasks).find(function (x) { return x.id === id; }) ||
                     (opts.taskLookup && opts.taskLookup(id));
          if (task && KS.canDrop(task, col.id) && opts.onMove) opts.onMove(task, col.id);
        });
      }
      wrap.appendChild(colEl);
    });
    host.innerHTML = '';
    host.appendChild(wrap);
    if (opts.draggable !== false) {
      wrap.querySelectorAll('.kb-card').forEach(function (c) {
        c.setAttribute('draggable', 'true');
        c.addEventListener('dragstart', function (e) { e.dataTransfer.setData('text/plain', c.dataset.task); });
      });
    }
    refreshIcons();
  }

  // ---------------------------------------------------------------- live log
  function ensureLogPanel() {
    var panel = document.getElementById('kb-log-panel');
    if (panel) return panel;
    panel = document.createElement('div');
    panel.id = 'kb-log-panel';
    panel.className = 'kb-log-panel hidden';
    panel.innerHTML =
      '<div class="kb-log-head"><span id="kb-log-title">task</span>' +
      '<button id="kb-log-close" title="Close">' + icon('x', 'w-4 h-4') + '</button></div>' +
      '<pre id="kb-log-body" class="kb-log-body"></pre>';
    document.body.appendChild(panel);
    panel.querySelector('#kb-log-close').addEventListener('click', closeLog);
    refreshIcons();
    return panel;
  }

  function closeLog() {
    if (state.ws) { try { state.ws.close(); } catch (e) {} state.ws = null; }
    state.logTask = null;
    var panel = document.getElementById('kb-log-panel');
    if (panel) panel.classList.add('hidden');
  }

  function openLog(task) {
    var panel = ensureLogPanel();
    panel.classList.remove('hidden');
    document.getElementById('kb-log-title').textContent = task.title || task.id;
    var body = document.getElementById('kb-log-body');
    body.textContent = '';
    state.logTask = task.id;
    if (state.ws) { try { state.ws.close(); } catch (e) {} }
    ensureToken().then(function () {
      var proto = location.protocol === 'https:' ? 'wss' : 'ws';
      var url = proto + '://' + location.host + '/api/hermes/ws?task=' +
        encodeURIComponent(task.id) + '&token=' + encodeURIComponent(state.token || '');
      var ws;
      try { ws = new WebSocket(url); } catch (e) { body.textContent = 'WebSocket unavailable'; return; }
      state.ws = ws;
      ws.onmessage = function (ev) {
        var msg;
        try { msg = JSON.parse(ev.data); } catch (e) { msg = { type: 'data', data: ev.data }; }
        if (msg.type === 'data' || msg.type === 'replay') body.textContent += msg.data;
        else if (msg.type === 'status') body.textContent += '\n· status: ' + msg.status + '\n';
        else if (msg.type === 'done') body.textContent += '\n─ ' + msg.status + (msg.error ? ' (' + msg.error + ')' : '') + '\n';
        body.scrollTop = body.scrollHeight;
      };
      ws.onclose = function () { body.textContent += '\n· connection closed\n'; };
    });
  }

  // ---------------------------------------------------------------- subagents
  function renderSubagents(host, subagents) {
    if (!host) return;
    host.innerHTML = '';
    (subagents || []).forEach(function (a) {
      var el = document.createElement('div');
      el.className = 'kb-subagent' + (a.available ? '' : ' kb-unavailable');
      el.innerHTML = '<span class="kb-sub-dot ' + (a.available ? 'on' : 'off') + '"></span>' +
        '<span class="kb-sub-name">' + esc(a.name || a.id) + '</span>' +
        '<span class="kb-sub-provider">' + esc(a.provider || '') + '</span>' +
        (a.available ? '' : '<span class="kb-sub-reason">' + esc(a.reason || '') + '</span>');
      host.appendChild(el);
    });
    if (!(subagents || []).length) host.innerHTML = '<div class="kb-col-empty">No sub-agents registered.</div>';
  }

  window.Kanban = {
    api: api, ensureToken: ensureToken, fetchState: fetchState, toast: toast,
    renderBoard: renderBoard, renderSubagents: renderSubagents, cardEl: cardEl,
    openLog: openLog, closeLog: closeLog,
    createTask: createTask, assignTask: assignTask, setStatus: setStatus,
    deleteTask: deleteTask, planGoal: planGoal, runGoal: runGoal, delegate: delegate,
    esc: esc, refreshIcons: refreshIcons, getState: function () { return state; }
  };
})();
