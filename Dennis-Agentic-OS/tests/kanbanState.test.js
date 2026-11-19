/*
 * Node test for js/kanbanState.js.
 * Run with:  node tests/kanbanState.test.js
 *
 * Pure Kanban board logic shared by the Kanban landing page and the in-OS
 * Hermes panel: column grouping, status transitions, drag validation and
 * immutable moves.
 */
const assert = require("assert");
const KS = require("../js/kanbanState.js");

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

const tasks = [
  { id: "a", status: "backlog", role: "research" },
  { id: "b", status: "assigned", role: "implement" },
  { id: "c", status: "running", role: "verify" },
  { id: "d", status: "done", role: "document" },
  { id: "e", status: "bogus", role: "implement" }
];

ok("exposes the five workflow columns in order", () => {
  assert.deepStrictEqual(KS.COLUMN_IDS, ["backlog", "assigned", "running", "review", "done"]);
});

ok("columnOf falls back to backlog for unknown status", () => {
  assert.strictEqual(KS.columnOf({ status: "bogus" }), "backlog");
  assert.strictEqual(KS.columnOf({}), "backlog");
});

ok("group buckets every task into its column", () => {
  const g = KS.group(tasks);
  assert.strictEqual(g.backlog.length, 2); // a + e (bogus -> backlog)
  assert.strictEqual(g.assigned.length, 1);
  assert.strictEqual(g.running.length, 1);
  assert.strictEqual(g.done.length, 1);
  assert.strictEqual(g.review.length, 0);
});

ok("counts and summary reflect the board", () => {
  const c = KS.counts(tasks);
  assert.strictEqual(c.backlog, 2);
  const s = KS.summary(tasks);
  assert.strictEqual(s.total, 5);
  assert.strictEqual(s.done, 1);
  assert.strictEqual(s.active, 2); // assigned + running
});

ok("nextStatus/prevStatus walk the columns and clamp", () => {
  assert.strictEqual(KS.nextStatus("backlog"), "assigned");
  assert.strictEqual(KS.nextStatus("done"), "done");
  assert.strictEqual(KS.prevStatus("backlog"), "backlog");
  assert.strictEqual(KS.prevStatus("review"), "running");
});

ok("movableFrom locks the running column (orchestrator-owned)", () => {
  assert.deepStrictEqual(KS.movableFrom("running"), []);
  assert.ok(KS.movableFrom("backlog").includes("done"));
});

ok("canDrop rejects same-column and running-source moves", () => {
  assert.strictEqual(KS.canDrop({ status: "backlog" }, "backlog"), false);
  assert.strictEqual(KS.canDrop({ status: "running" }, "done"), false);
  assert.strictEqual(KS.canDrop({ status: "review" }, "done"), true);
});

ok("move returns a new array and never mutates the input", () => {
  const before = tasks.slice();
  const after = KS.move(tasks, "a", "review");
  assert.strictEqual(after.find(t => t.id === "a").status, "review");
  assert.strictEqual(tasks.find(t => t.id === "a").status, "backlog");
  assert.strictEqual(tasks.length, before.length);
});

ok("roleLabel maps known roles and falls back to Task", () => {
  assert.strictEqual(KS.roleLabel("verify"), "Verify");
  assert.strictEqual(KS.roleLabel("nope"), "Task");
});

console.log("\n" + passed + " checks passed");
