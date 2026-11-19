// In-OS Hermes orchestrator panel: sub-agents, goal composer and Kanban board.
// Rendering is delegated to window.Kanban (shared with the Kanban landing page).
(function () {
  'use strict';

  function ready(fn) {
    if (document.readyState !== 'loading') fn();
    else document.addEventListener('DOMContentLoaded', fn);
  }

  function init() {
    if (!document.getElementById('hermes-view')) return;
    var board = document.getElementById('hermes-board');
    var subHost = document.getElementById('hermes-subagents');
    var goal = document.getElementById('hermes-goal');
    var autonomous = document.getElementById('hermes-autonomous');
    var brainSel = document.getElementById('hermes-brain-select');
    var planBtn = document.getElementById('hermes-plan');
    var runBtn = document.getElementById('hermes-run');
    var refreshBtn = document.getElementById('hermes-refresh');

    function snap() { return (window.Kanban && window.Kanban.getState().snapshot) || null; }

    function render() {
      var s = snap();
      if (!s) return;
      var brainEl = document.getElementById('hermes-brain');
      if (brainEl) brainEl.textContent = (s.identity && s.identity.brain) || 'vault';
      var concEl = document.getElementById('hermes-concurrency');
      if (concEl) concEl.textContent = 'concurrency ' + (s.concurrency || 3);
      if (subHost) window.Kanban.renderSubagents(subHost, s.subagents);
      if (board) {
        window.Kanban.renderBoard(board, s.board, {
          onMove: function (task, status) { window.Kanban.setStatus(task.id, status).then(load); },
          onDelete: function (task) {
            if (confirm('Delete task "' + task.title + '"?')) window.Kanban.deleteTask(task.id).then(load);
          }
        });
      }
    }

    function load() { return window.Kanban.fetchState().then(render); }

    function submit(kind) {
      var text = (goal && goal.value || '').trim();
      if (!text) { window.Kanban.toast('Enter a goal first'); return; }
      var brain = brainSel ? brainSel.value : 'vault';
      var fn = kind === 'plan' ? window.Kanban.planGoal : window.Kanban.runGoal;
      fn(text, brain).then(function (r) {
        if (!r.ok) { window.Kanban.toast((r.data && r.data.error) || 'Failed'); return; }
        if (kind === 'plan' && autonomous && autonomous.checked && r.data.run) {
          window.Kanban.delegate(r.data.run.id).then(load);
        } else {
          load();
        }
        window.Kanban.toast(kind === 'plan' ? 'Planned ' + (r.data.tasks || []).length + ' tasks' : 'Run started');
      });
    }

    if (planBtn) planBtn.addEventListener('click', function () { submit('plan'); });
    if (runBtn) runBtn.addEventListener('click', function () { submit('run'); });
    if (refreshBtn) refreshBtn.addEventListener('click', load);
    if (goal) goal.addEventListener('keydown', function (e) { if (e.key === 'Enter') submit('run'); });

    window.Hermes = {
      open: function () {
        if (window.switchMainView) window.switchMainView('hermes');
        load();
      },
      reload: load
    };
  }

  ready(init);
})();
