import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { test } from "node:test";
import { runInNewContext } from "node:vm";

const require = createRequire(import.meta.url), ts = require("typescript");
const compiled = ts.transpileModule(readFileSync(new URL("../src/composables/player/foregroundObservation.ts", import.meta.url), "utf8"), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText;
const summary = (updates = {}) => ({ energy_mean: .4, energy_peak: .8, low_ratio: .5, brightness: .2,
  dynamic_range: .3, beat_density: 80, sample_seconds: 20, algorithm_version: "web-audio-v1", ...updates });
const deferred = () => { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b; }); return { promise, resolve, reject }; };
const settle = async () => { for (let i = 0; i < 25; i++) await Promise.resolve(); };
const lease = (songId = "synthetic-a", now = 1_800_000_000_000, id = "a".repeat(32)) => ({
  observation_id: id, binding: { song_id: songId, library_generation: "b".repeat(32), song_revision: "c".repeat(32),
    metadata_sha256: "d".repeat(64), feature_algorithm: "web-audio-v1", capture_contract: "media-clock-v1",
    measurement_mode: "foreground", capture_configuration: "fft2048-s078-bands-v1" }, expires_at: now / 1000 + 1200,
});

function harness(overrides = {}) {
  let now = 1_800_000_000_000, sequence = 0, begins = 0, finishes = 0, available = true, result = summary();
  const calls = [], timers = new Map(), exports = {};
  const api = {
    prepareAudioObservation: async (songId, signal) => { calls.push({ type: "prepare", songId, signal }); return lease(songId, now); },
    submitAudioObservation: async packet => { calls.push({ type: "submit", packet }); return { status: "accepted" }; },
    cancelAudioObservation: async id => { calls.push({ type: "cancel", id }); return { cancelled: true }; },
    ...overrides,
  };
  runInNewContext(compiled, { exports, AbortController, Date: { now: () => now },
    window: { setTimeout: (fn, delay) => { timers.set(++sequence, { fn, at: now + delay }); return sequence; },
      clearTimeout: id => timers.delete(id) },
    require: id => { assert.ok(id.endsWith("/services/api")); return api; },
  });
  const controller = exports.createForegroundObservation({
    beginFeatureCapture: () => { begins++; }, finishFeatureCapture: () => { finishes++; return result; },
  }, () => available);
  return { controller, calls, timers, get begins() { return begins; }, get finishes() { return finishes; },
    set result(value) { result = value; }, set available(value) { available = value; },
    advance(ms) { now += ms; for (const [id, timer] of [...timers]) if (timer.at <= now) { timers.delete(id); timer.fn(); } },
  };
}

test("prepare不阻塞调用，绑定晚到前不采样，同一观察只收尾一次且不携带旧算法字段", async () => {
  const pending = deferred(), h = harness({ prepareAudioObservation: () => pending.promise });
  assert.equal(h.controller.start("synthetic-a"), undefined);
  assert.equal(h.begins, 0);
  pending.resolve(lease()); await settle();
  assert.equal(h.begins, 1);
  h.controller.start("synthetic-a");
  assert.equal(h.begins, 1);
  h.controller.finish(); h.controller.finish(); await settle();
  const submits = h.calls.filter(c => c.type === "submit");
  assert.equal(submits.length, 1);
  assert.equal(h.finishes, 1);
  assert.deepEqual(Object.keys(submits[0].packet.summary).sort(), ["beat_density", "brightness", "dynamic_range", "energy_mean", "energy_peak", "low_ratio", "sample_seconds"]);
  assert.equal(submits[0].packet.source_extent, "unknown");
  assert.equal(submits[0].packet.confidence, "uncalibrated");
  assert.equal(h.timers.size, 0);
});

test("切歌取消在途prepare，忽略取消的迟到租约只清理，不开启新曲capture", async () => {
  const pending = deferred(); let signal;
  const h = harness({ prepareAudioObservation: (_song, incoming) => { signal = incoming; return pending.promise; } });
  h.controller.start("synthetic-a"); h.controller.finish();
  assert.equal(signal.aborted, true);
  pending.resolve(lease()); await settle();
  assert.equal(h.begins, 0);
  assert.deepEqual(h.calls.map(c => c.type), ["cancel"]);
});

test("分析已降级时迟到绑定被取消，不制造普通播放的假观察", async () => {
  const pending = deferred(), h = harness({ prepareAudioObservation: () => pending.promise });
  h.controller.start("synthetic-a"); h.available = false;
  pending.resolve(lease()); await settle();
  assert.equal(h.begins, 0);
  assert.equal(h.calls[0].type, "cancel");
});

test("未知合同、错曲、后台配置、坏编号和不合理过期时间均不开始采样", async () => {
  const variants = [
    null, {}, { ...lease(), observation_id: "old-version" }, { ...lease(), binding: { ...lease().binding, song_id: "other" } },
    { ...lease(), binding: { ...lease().binding, feature_algorithm: "future" } },
    { ...lease(), binding: { ...lease().binding, capture_contract: "future" } },
    { ...lease(), binding: { ...lease().binding, measurement_mode: "background" } },
    { ...lease(), binding: { ...lease().binding, capture_configuration: "fft2048-s072-bands-v1" } },
    { ...lease(), binding: { ...lease().binding, source_url: "https://invalid.example/private" } },
    { ...lease(), expires_at: 0 }, { ...lease(), expires_at: Infinity },
    { ...lease(), expires_at: lease().expires_at + 1200 },
  ];
  for (const value of variants) {
    const h = harness({ prepareAudioObservation: async () => value });
    h.controller.start("synthetic-a"); await settle(); h.controller.finish(); await settle();
    assert.equal(h.begins, 0); assert.equal(h.calls.length, 0);
  }
});

test("prepare失败和未知结果不自动重试，不调用任何旧路由", async () => {
  for (const code of [401, 404, 409, 422, 429, 503, "timeout"]) {
    let prepares = 0;
    const h = harness({ prepareAudioObservation: async () => { prepares++; throw new Error(String(code)); } });
    h.controller.start("synthetic-a"); await settle(); h.controller.start("synthetic-a"); h.controller.finish(); await settle();
    assert.equal(prepares, 1); assert.equal(h.begins, 0); assert.equal(h.calls.length, 0);
  }
});

test("短样本、无信号、非有限数或未知算法只取消租约，不提交", async () => {
  for (const value of [null, summary({ sample_seconds: 11.9 }), summary({ energy_peak: 0 }),
    summary({ energy_mean: NaN }), summary({ low_ratio: .9, brightness: .8 }),
    summary({ beat_density: 301 }), summary({ algorithm_version: "future" })]) {
    const h = harness(); h.result = value;
    h.controller.start("synthetic-a"); await settle(); h.controller.finish(); await settle();
    assert.deepEqual(h.calls.map(c => c.type), ["prepare", "cancel"]);
  }
});

test("到期前留出五秒提交已绑定片段，不自动为旧片段重申请编号", async () => {
  const h = harness(); h.controller.start("synthetic-a"); await settle();
  h.advance(1_195_000); await settle();
  assert.deepEqual(h.calls.map(c => c.type), ["prepare", "submit"]);
  h.advance(20_000); h.controller.finish(); await settle();
  assert.equal(h.calls.length, 2);
});

test("长时间挂起导致租约已过期时丢弃摘要，不能换编号追认", async () => {
  const h = harness(); h.controller.start("synthetic-a"); await settle();
  h.advance(1_250_000); await settle();
  assert.deepEqual(h.calls.map(c => c.type), ["prepare", "cancel"]);
});

test("快速切歌最多保留两路写请求，超额不进入持久待发队列", async () => {
  const pending = deferred(); let writes = 0;
  const h = harness({ submitAudioObservation: () => { writes++; return pending.promise; } });
  for (const id of ["synthetic-a", "synthetic-b", "synthetic-c"]) {
    h.controller.start(id); await settle(); h.controller.finish(); await settle();
  }
  assert.equal(writes, 2); assert.equal(h.finishes, 3); assert.equal(h.timers.size, 0);
  pending.resolve({ status: "accepted" }); await settle();
  h.controller.start("synthetic-d"); await settle(); h.controller.finish(); await settle();
  assert.equal(writes, 3);
});

test("提交失败不取消已可能接受的观察、不重试、不回投旧统计", async () => {
  let attempts = 0;
  const h = harness({ submitAudioObservation: async () => { attempts++; throw new Error("unknown outcome"); } });
  h.controller.start("synthetic-a"); await settle(); h.controller.finish(); await settle();
  h.controller.finish(); await settle();
  assert.equal(attempts, 1); assert.deepEqual(h.calls.map(c => c.type), ["prepare"]);
});

test("卸载取消在途准备，迟到后只清理租约且不能再次开始", async () => {
  const pending = deferred(), h = harness({ prepareAudioObservation: () => pending.promise });
  h.controller.start("synthetic-a"); h.controller.dispose(); pending.resolve(lease()); await settle();
  h.controller.start("synthetic-b"); await settle();
  assert.equal(h.begins, 0); assert.deepEqual(h.calls.map(c => c.type), ["cancel"]);
});

test("卸载对有效片段只提交一次并清理到期计时器，不宣称能撤销在途事务", async () => {
  const h = harness(); h.controller.start("synthetic-a"); await settle();
  h.controller.dispose(); h.controller.dispose(); await settle();
  assert.deepEqual(h.calls.map(c => c.type), ["prepare", "submit"]);
  assert.equal(h.timers.size, 0);
});
