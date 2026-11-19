/*
 * Node test for js/agentDashboards.js.
 * Run with:  node tests/agentDashboards.test.js
 *
 * Proves per-agent isolation: one view per session id, switching never
 * disposes, input routes only to the right session, output queues until the
 * view is mounted.
 */
const assert = require("assert");
const { createRegistry } = require("../js/agentDashboards.js");

let passed = 0;
function ok(name, fn) {
  try {
    fn();
    passed++;
    console.log("  ok - " + name);
  } catch (err) {
    console.error("  FAIL - " + name);
    console.error("    " + err.message);
    process.exitCode = 1;
  }
}

function makeFactory(log) {
  return function createView(meta) {
    const view = {
      meta,
      visible: false,
      focused: 0,
      writes: [],
      inputs: [],
      shows: 0,
      hides: 0,
      disposed: 0,
      show() { this.visible = true; this.shows++; },
      hide() { this.visible = false; this.hides++; },
      focus() { this.focused++; },
      write(chunk) { this.writes.push(chunk); },
      sendInput(data) { this.inputs.push(data); },
      resize() { log.resizes++; },
      dispose() { this.disposed++; },
    };
    log.views.push(view);
    return view;
  };
}

ok("ensure creates exactly one view per session id", () => {
  const log = { views: [], resizes: 0 };
  const r = createRegistry({ createView: makeFactory(log) });
  const a1 = r.ensure({ sessionId: "A", agentId: "opencode" });
  const a2 = r.ensure({ sessionId: "A", agentId: "opencode" });
  assert.strictEqual(a1, a2);
  assert.strictEqual(log.views.length, 1);
  r.ensure({ sessionId: "B", agentId: "claude-code" });
  assert.strictEqual(log.views.length, 2);
  assert.strictEqual(r.list().length, 2);
});

ok("select shows one dashboard and hides the other without disposing", () => {
  const log = { views: [], resizes: 0 };
  const r = createRegistry({ createView: makeFactory(log) });
  const a = r.ensure({ sessionId: "A" });
  const b = r.ensure({ sessionId: "B" });
  r.select("A");
  assert.ok(a.view.visible && !b.view.visible);
  assert.ok(a.view.focused >= 1, "focused dashboard gets keyboard focus");
  r.select("B");
  assert.ok(!a.view.visible && b.view.visible);
  assert.strictEqual(a.view.disposed + b.view.disposed, 0);
  assert.strictEqual(r.activeId(), "B");
  assert.strictEqual(r.active(), b);
});

ok("keystrokes in A never reach B", () => {
  const log = { views: [], resizes: 0 };
  const r = createRegistry({ createView: makeFactory(log) });
  const a = r.ensure({ sessionId: "A" });
  const b = r.ensure({ sessionId: "B" });
  r.select("A");
  r.inputTo("A", "hello-from-A\r");
  assert.deepStrictEqual(a.view.inputs, ["hello-from-A\r"]);
  assert.deepStrictEqual(b.view.inputs, []);
  r.select("B");
  r.input("world-B");
  assert.deepStrictEqual(a.view.inputs, ["hello-from-A\r"]);
  assert.deepStrictEqual(b.view.inputs, ["world-B"]);
});

ok("output written before mount is queued and flushed in order", () => {
  const log = { views: [], resizes: 0 };
  const r = createRegistry({ createView: makeFactory(log) });
  const a = r.ensure({ sessionId: "A" });
  r.write("A", "one");
  r.write("A", "two");
  assert.deepStrictEqual(a.view.writes, [], "nothing rendered before mount");
  r.markMounted("A");
  assert.deepStrictEqual(a.view.writes, ["one", "two"]);
  r.write("A", "three");
  assert.deepStrictEqual(a.view.writes, ["one", "two", "three"]);
});

ok("write to an unknown session is a safe no-op", () => {
  const r = createRegistry({ createView: makeFactory({ views: [], resizes: 0 }) });
  assert.strictEqual(r.write("nope", "x"), false);
  assert.strictEqual(r.inputTo("nope", "x"), false);
  assert.strictEqual(r.select("nope"), null);
});

ok("dispose destroys only the closed session and leaves the other running", () => {
  const log = { views: [], resizes: 0 };
  const r = createRegistry({ createView: makeFactory(log) });
  const a = r.ensure({ sessionId: "A" });
  const b = r.ensure({ sessionId: "B" });
  r.select("A");
  r.dispose("A");
  assert.strictEqual(a.view.disposed, 1);
  assert.strictEqual(b.view.disposed, 0);
  assert.strictEqual(r.has("A"), false);
  assert.strictEqual(r.has("B"), true);
  assert.strictEqual(r.activeId(), null);
});

ok("resizeActive only touches the visible dashboard", () => {
  const log = { views: [], resizes: 0 };
  const r = createRegistry({ createView: makeFactory(log) });
  r.ensure({ sessionId: "A" });
  r.ensure({ sessionId: "B" });
  r.select("A");
  r.resizeActive();
  assert.strictEqual(log.resizes, 1);
  r.select(null);
  r.resizeActive();
  assert.strictEqual(log.resizes, 1);
});

ok("a broken view does not break the others", () => {
  const log = { views: [], resizes: 0 };
  const factory = makeFactory(log);
  const r = createRegistry({
    createView(meta) {
      const view = factory(meta);
      if (meta.sessionId === "A") view.hide = function () { throw new Error("boom"); };
      return view;
    },
  });
  r.ensure({ sessionId: "A" });
  const b = r.ensure({ sessionId: "B" });
  r.select("B");
  assert.ok(b.view.visible);
});

console.log("\n" + passed + " checks passed");
