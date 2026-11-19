// Kanban state helpers for Hermes. Pure, dependency-free and node-testable.
// Shared by js/kanban.js (board renderer) and js/hermes.js (OS panel).
(function (root, factory) {
  var api = factory();
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  if (root) root.KanbanState = api;
})(typeof window !== 'undefined' ? window : null, function () {
  'use strict';

  var COLUMNS = [
    { id: 'backlog', label: 'Backlog' },
    { id: 'assigned', label: 'Assigned' },
    { id: 'running', label: 'Running' },
    { id: 'review', label: 'Review' },
    { id: 'done', label: 'Done' }
  ];
  var COLUMN_IDS = COLUMNS.map(function (c) { return c.id; });

  var ROLE_LABELS = {
    research: 'Research',
    plan: 'Plan',
    implement: 'Implement',
    verify: 'Verify',
    document: 'Document'
  };

  var STATUS_LABELS = {
    backlog: 'Backlog',
    assigned: 'Assigned',
    running: 'Running',
    review: 'Review',
    done: 'Done'
  };

  function isColumn(id) { return COLUMN_IDS.indexOf(id) !== -1; }

  function columnOf(task) {
    var s = task && task.status;
    return isColumn(s) ? s : 'backlog';
  }

  function roleLabel(role) { return ROLE_LABELS[role] || 'Task'; }
  function statusLabel(status) { return STATUS_LABELS[status] || 'Backlog'; }

  function group(tasks) {
    var out = {};
    COLUMN_IDS.forEach(function (id) { out[id] = []; });
    (tasks || []).forEach(function (t) { out[columnOf(t)].push(t); });
    return out;
  }

  function index(status) { return COLUMN_IDS.indexOf(status); }

  function nextStatus(status) {
    var i = index(status);
    return i < 0 || i >= COLUMN_IDS.length - 1 ? COLUMN_IDS[COLUMN_IDS.length - 1] : COLUMN_IDS[i + 1];
  }

  function prevStatus(status) {
    var i = index(status);
    return i <= 0 ? COLUMN_IDS[0] : COLUMN_IDS[i - 1];
  }

  // Columns a task can be dragged to. `running`/`review` are owned by the
  // orchestrator while a run is live, so the UI keeps those read-only.
  function movableFrom(status) {
    if (status === 'running') return [];
    return COLUMN_IDS.filter(function (id) { return id !== status; });
  }

  function canDrop(task, status) {
    if (!task || !isColumn(status)) return false;
    if (columnOf(task) === status) return false;
    return movableFrom(columnOf(task)).indexOf(status) !== -1;
  }

  // Move a task to a new column, returning a new array (never mutates input).
  function move(tasks, taskId, status) {
    if (!isColumn(status)) return (tasks || []).slice();
    return (tasks || []).map(function (t) {
      return t.id === taskId ? Object.assign({}, t, { status: status }) : t;
    });
  }

  function counts(tasks) {
    var g = group(tasks);
    var out = {};
    COLUMN_IDS.forEach(function (id) { out[id] = g[id].length; });
    return out;
  }

  function summary(tasks) {
    var c = counts(tasks);
    return {
      total: (tasks || []).length,
      done: c.done,
      active: c.assigned + c.running,
      review: c.review,
      backlog: c.backlog
    };
  }

  return {
    COLUMNS: COLUMNS,
    COLUMN_IDS: COLUMN_IDS,
    ROLE_LABELS: ROLE_LABELS,
    isColumn: isColumn,
    columnOf: columnOf,
    roleLabel: roleLabel,
    statusLabel: statusLabel,
    group: group,
    nextStatus: nextStatus,
    prevStatus: prevStatus,
    movableFrom: movableFrom,
    canDrop: canDrop,
    move: move,
    counts: counts,
    summary: summary
  };
});
