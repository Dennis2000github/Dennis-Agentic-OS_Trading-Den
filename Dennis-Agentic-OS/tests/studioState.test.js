/*
 * Node test for js/studioState.js.
 * Run with:  node tests/studioState.test.js
 *
 * Pure helpers behind the Studio console: elapsed/clock labels, model
 * filtering, schema-driven parameter coercion and media URLs.
 */
const assert = require("assert");
const SS = require("../js/studioState.js");

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

const fields = [
  { name: "prompt", title: "Prompt", control: "text", required: true },
  { name: "duration", title: "Duration", control: "select", enum: [4, 6, 10], default: 6 },
  { name: "seed", title: "Seed", control: "number", type: "integer" },
  { name: "enhance", title: "Enhance", control: "toggle", type: "boolean" },
  { name: "image_urls", title: "Images", control: "url_list", type: "array" },
  { name: "metadata", title: "Metadata", control: "json", type: "object", advanced: true }
];

ok("elapsedLabel handles seconds, minutes and hours", () => {
  assert.strictEqual(SS.elapsedLabel(0), "0s");
  assert.strictEqual(SS.elapsedLabel(9.9), "9s");
  assert.strictEqual(SS.elapsedLabel(65), "1m 05s");
  assert.strictEqual(SS.elapsedLabel(7380), "2h 03m");
});

ok("clockLabel pads minutes and seconds", () => {
  assert.strictEqual(SS.clockLabel(5), "00:05");
  assert.strictEqual(SS.clockLabel(125), "02:05");
});

ok("humanBytes switches units", () => {
  assert.strictEqual(SS.humanBytes(12), "12 B");
  assert.strictEqual(SS.humanBytes(2048), "2.0 KB");
  assert.strictEqual(SS.humanBytes(5 * 1024 * 1024), "5.0 MB");
});

ok("state labels and tones are normalized", () => {
  assert.strictEqual(SS.stateLabel("running"), "Generating");
  assert.strictEqual(SS.stateLabel("nsfw"), "Unknown");
  assert.strictEqual(SS.stateTone("succeeded"), "ok");
  assert.strictEqual(SS.stateTone("failed"), "error");
  assert.strictEqual(SS.stateTone("what"), "muted");
  assert.ok(SS.isActive({ state: "queued" }));
  assert.ok(!SS.isActive({ state: "succeeded" }));
});

ok("input modes follow documented model modes", () => {
  const models = [
    { id: "a", mode: "text_to_video", summary: { accepts: { image: true } } },
    { id: "b", mode: "image_to_video", summary: { accepts: { image: true } } },
    { id: "c", mode: "video_edit", summary: { accepts: { video: true } } },
    { id: "d", mode: "text_to_image", summary: { accepts: {} } }
  ];
  assert.strictEqual(SS.inputModeForModel(models[0]), "text");
  assert.strictEqual(SS.inputModeForModel(models[1]), "image");
  assert.strictEqual(SS.inputModeForModel(models[2]), "video");
  assert.deepStrictEqual(
    SS.filterModelsByInput(models, "image", "", "").map((m) => m.id),
    ["b"]
  );
  assert.deepStrictEqual(
    SS.filterModelsByInput(models, "text", "", "image").map((m) => m.id),
    ["d"]
  );
});

ok("valuesFromParams restores schema-shaped form values and reports unknown keys", () => {
  const restored = SS.valuesFromParams(fields, {
    prompt: "hello",
    duration: 10,
    enhance: true,
    image_urls: ["https://a.test/1.png", "https://a.test/2.png"],
    metadata: { camera: "slow" },
    obsolete: 1
  });
  assert.strictEqual(restored.values.prompt, "hello");
  assert.strictEqual(restored.values.duration, "10");
  assert.strictEqual(restored.values.enhance, true);
  assert.strictEqual(restored.values.image_urls, "https://a.test/1.png\nhttps://a.test/2.png");
  assert.strictEqual(JSON.parse(restored.values.metadata).camera, "slow");
  assert.deepStrictEqual(restored.ignored, ["obsolete"]);
});

ok("jobOutput and usage counters stay honest", () => {
  const jobs = [
    { created_at: 100, outputs: [{ url: "https://x.test/a.mp4" }] },
    { created_at: 200, outputs: [{ media_id: "med_1" }] },
    { created_at: 50, outputs: [] }
  ];
  assert.strictEqual(SS.jobOutput(jobs[0]).url, "https://x.test/a.mp4");
  assert.strictEqual(SS.jobOutput(jobs[1]).media_id, "med_1");
  assert.strictEqual(SS.jobOutput(jobs[2]), null);
  assert.strictEqual(SS.countJobsSince(jobs, 100), 2);
  assert.strictEqual(SS.countJobsSince(jobs, 0), 3);
  const noon = new Date(2026, 9, 23, 12, 0, 0).getTime();
  const midnight = new Date(2026, 9, 23, 0, 0, 0).getTime() / 1000;
  assert.strictEqual(SS.startOfToday(noon), midnight);
});

ok("defaultValues keeps documented defaults and select first value", () => {
  const values = SS.defaultValues(fields);
  assert.strictEqual(values.prompt, "");
  assert.strictEqual(values.duration, 6);
  assert.strictEqual(values.enhance, false);
});

ok("buildParams coerces numbers, toggles, lists and JSON", () => {
  const params = SS.buildParams(fields, {
    prompt: "  a neon city  ",
    duration: "6",
    seed: "42.7",
    enhance: true,
    image_urls: "https://a.test/1.png, https://a.test/2.png",
    metadata: '{"camera":"slow"}'
  });
  assert.deepStrictEqual(params, {
    prompt: "a neon city",
    duration: 6,
    seed: 43,
    enhance: true,
    image_urls: ["https://a.test/1.png", "https://a.test/2.png"],
    metadata: { camera: "slow" }
  });
});

ok("buildParams omits empty optional values", () => {
  const params = SS.buildParams(fields, { prompt: "x", seed: "", image_urls: "", metadata: "" });
  assert.deepStrictEqual(params, { prompt: "x" });
});

ok("invalid JSON and numbers raise instead of being dropped", () => {
  assert.throws(() => SS.buildParams(fields, { prompt: "x", seed: "nope" }), /number/);
  assert.throws(() => SS.buildParams(fields, { prompt: "x", metadata: "{nope" }), /JSON/);
});

ok("missingRequired reports empty required fields", () => {
  assert.deepStrictEqual(SS.missingRequired(fields, {}), ["prompt"]);
  assert.deepStrictEqual(SS.missingRequired(fields, { prompt: "x" }), []);
  assert.deepStrictEqual(SS.missingRequired(fields, { prompt: "   " }), ["prompt"]);
});

ok("filterModels matches kind, mode and free text", () => {
  const models = [
    { id: "a", label: "Seedance", kind: "video", mode: "text_to_video", tags: ["1080p"] },
    { id: "b", label: "Flux", kind: "image", mode: "text_to_image", tags: ["image input"] }
  ];
  assert.strictEqual(SS.filterModels(models, "image", null, "").length, 1);
  assert.strictEqual(SS.filterModels(models, null, "text_to_video", "").length, 1);
  assert.strictEqual(SS.filterModels(models, null, null, "seedance").length, 1);
  assert.strictEqual(SS.filterModels(models, null, null, "image input")[0].id, "b");
});

ok("field groups put advanced fields last", () => {
  assert.strictEqual(SS.advancedFields(fields).length, 1);
  assert.ok(SS.basicFields(fields).every(f => !f.advanced));
  assert.strictEqual(SS.promptField(fields).name, "prompt");
});

ok("mediaUrl encodes the id and token", () => {
  assert.strictEqual(SS.mediaUrl("med_1", "a b"), "/api/studio/media/med_1?token=a%20b");
  assert.strictEqual(SS.mediaUrl("", "t"), "");
});

ok("primaryOutput prefers the first downloaded media", () => {
  const job = { kind: "video", outputs: [{ url: "u", kind: "video" }, { url: "m", media_id: "med_1" }] };
  assert.strictEqual(SS.primaryOutput(job).media_id, "med_1");
  assert.strictEqual(SS.outputKind(job, { kind: "image" }), "image");
});

ok("parseEnv and parseArgs reject malformed setup input", () => {
  assert.deepStrictEqual(SS.parseEnv('{"A":"b"}'), { A: "b" });
  assert.deepStrictEqual(SS.parseEnv(""), {});
  assert.throws(() => SS.parseEnv("[]"), /JSON object/);
  assert.deepStrictEqual(SS.parseArgs("--from pkg server"), ["--from", "pkg", "server"]);
  assert.deepStrictEqual(SS.parseArgs('["a","b"]'), ["a", "b"]);
  assert.throws(() => SS.parseArgs('{"a":1}'), /array/);
});

ok("relativeTime is coarse and human readable", () => {
  assert.strictEqual(SS.relativeTime(2), "just now");
  assert.strictEqual(SS.relativeTime(30), "30s ago");
  assert.strictEqual(SS.relativeTime(120), "2m ago");
  assert.strictEqual(SS.relativeTime(7200), "2h ago");
});

ok("normalizeList only accepts arrays", () => {
  assert.deepStrictEqual(SS.normalizeList({ jobs: [1] }, "jobs"), [1]);
  assert.deepStrictEqual(SS.normalizeList({ jobs: "x" }, "jobs"), []);
  assert.deepStrictEqual(SS.normalizeList([1, 2], "jobs"), [1, 2]);
});

console.log("\n" + passed + " checks passed");
