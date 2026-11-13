/*
 * Per-session agent dashboards.
 *
 * One dashboard per project session id: its own terminal view, its own
 * WebSocket and its own process, created lazily the first time the session is
 * shown. Hiding a dashboard (agent switch) keeps it alive: the view is never
 * destroyed or recreated, so scrollback and any running task survive.
 *
 * This module owns *routing and lifecycle only*; it creates no DOM. The caller
 * injects `createView(meta)` and the view exposes:
 *
 *   view.show()          - become visible
 *   view.hide()          - become hidden (must NOT dispose)
 *   view.focus()         - keyboard focus
 *   view.write(chunk)    - render PTY output (string)
 *   view.sendInput(data) - send keystrokes to this session's process
 *   view.resize()        - re-fit to the visible area
 *   view.dispose()       - destroy terminal + socket (only on session close)
 *
 * Output written before a view is mounted is queued and flushed on
 * markMounted(), so nothing is lost while the terminal is still being set up.
 *
 * Pure logic: unit tested under node with fake views.
 */
(function (root, factory) {
  if (typeof module === "object" && module.exports) {
    module.exports = factory();
  } else {
    root.AgentDashboards = factory();
  }
})(typeof self !== "undefined" ? self : this, function () {
  function createRegistry(options) {
    options = options || {};
    var createView = options.createView;
    if (typeof createView !== "function") {
      throw new Error("createRegistry requires createView");
    }
    var records = {};   // sessionId -> record
    var order = [];
    var activeId = null;

    function ensure(meta, host) {
      meta = meta || {};
      var id = meta.sessionId;
      if (!id) throw new Error("sessionId is required");
      var rec = records[id];
      if (rec) {
        if (meta.agentId) rec.agentId = meta.agentId;
        return rec;
      }
      rec = {
        sessionId: id,
        agentId: meta.agentId || null,
        view: createView({ sessionId: id, agentId: meta.agentId || null, host: host }),
        mounted: false,
        queue: [],
      };
      records[id] = rec;
      order.push(id);
      return rec;
    }

    function get(id) { return records[id] || null; }
    function has(id) { return !!records[id]; }
    function list() {
      return order.map(function (id) { return records[id]; }).filter(Boolean);
    }

    /* Show one dashboard, hide every other. Never disposes anything. */
    function select(id) {
      if (id && !records[id]) return null;
      activeId = id || null;
      order.forEach(function (sid) {
        var rec = records[sid];
        if (!rec || !rec.view) return;
        try {
          if (sid === activeId && rec.view.show) rec.view.show();
          else if (rec.view.hide) rec.view.hide();
        } catch (err) { /* a broken view must not break the others */ }
      });
      if (activeId && records[activeId].view.focus) {
        try { records[activeId].view.focus(); } catch (err) { /* ignore */ }
      }
      return activeId ? records[activeId] : null;
    }

    function active() { return activeId ? records[activeId] : null; }

    /* Keystrokes for one specific session's process (never another's). */
    function inputTo(sessionId, data) {
      var rec = records[sessionId];
      if (!rec || !rec.view || typeof rec.view.sendInput !== "function") return false;
      try { rec.view.sendInput(data); return true; } catch (err) { return false; }
    }

    /* Keystrokes from the focused terminal: active dashboard only. */
    function input(data) {
      if (!activeId) return false;
      return inputTo(activeId, data);
    }

    /* PTY output: queue until the view is mounted, then render in order. */
    function write(sessionId, chunk) {
      var rec = records[sessionId];
      if (!rec || !rec.view) return false;
      if (!rec.mounted) { rec.queue.push(chunk); return true; }
      try { rec.view.write(chunk); return true; } catch (err) { return false; }
    }

    function markMounted(sessionId) {
      var rec = records[sessionId];
      if (!rec) return false;
      rec.mounted = true;
      var queued = rec.queue;
      rec.queue = [];
      queued.forEach(function (chunk) {
        try { rec.view.write(chunk); } catch (err) { /* ignore */ }
      });
      return true;
    }

    function resize(sessionId) {
      var rec = records[sessionId];
      if (!rec || !rec.view || typeof rec.view.resize !== "function") return false;
      try { rec.view.resize(); return true; } catch (err) { return false; }
    }

    function resizeActive() {
      if (!activeId) return false;
      return resize(activeId);
    }

    /* Only called when a session is closed or deleted. */
    function dispose(sessionId) {
      var rec = records[sessionId];
      if (!rec) return false;
      try { if (rec.view && rec.view.dispose) rec.view.dispose(); } catch (err) { /* ignore */ }
      delete records[sessionId];
      order = order.filter(function (id) { return id !== sessionId; });
      if (activeId === sessionId) activeId = null;
      return true;
    }

    function disposeAll() {
      list().forEach(function (rec) { dispose(rec.sessionId); });
    }

    return {
      ensure: ensure,
      get: get,
      has: has,
      list: list,
      select: select,
      active: active,
      activeId: function () { return activeId; },
      input: input,
      inputTo: inputTo,
      write: write,
      markMounted: markMounted,
      resize: resize,
      resizeActive: resizeActive,
      dispose: dispose,
      disposeAll: disposeAll,
    };
  }

  return { createRegistry: createRegistry };
});
