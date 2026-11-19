import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { test } from "node:test";
import { runInNewContext } from "node:vm";

const require = createRequire(import.meta.url), ts = require("typescript"), vue = require("vue");
const compile = path => ts.transpileModule(readFileSync(new URL(`../src/${path}`, import.meta.url), "utf8"), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText;
const signal = {};
runInNewContext(compile("components/scene/musicRain/signal.ts"), { exports: signal });
const production = compile("composables/useMusicRain.ts");
const input = extra => ({ spectrum: Array(48).fill(0), low: 0.3, mid: 0.2, high: 0.1,
  energy: 0.25, beat: 0, isPlaying: true, analysisStatus: "active", visualMode: "immersive", constrained: false, ...extra });
const settle = async () => { for (let i = 0; i < 16; i++) await Promise.resolve(); await vue.nextTick(); };

test("雨幕按48个真实频段重塑，低频与高频峰不会混成整体音量", () => {
  for (const band of [3, 24, 43]) {
    const model = signal.createRainSignal(), spectrum = Array(48).fill(0);
    spectrum[band] = 1;
    let frame;
    for (let n = 0; n < 40; n++) frame = model.update(input({ spectrum }), 1 / 30);
    assert.equal(Array.from(frame.bands).indexOf(Math.max(...frame.bands)), band);
    assert.ok(frame.bands[band] > 0.95);
    assert.ok(frame.bands[(band + 7) % 48] < 0.05);
  }
});

test("雨幕拒绝非法频谱值和时间跳跃，保持有限的强度与稳定时间步", () => {
  const model = signal.createRainSignal();
  const frame = model.update(input({ spectrum: [NaN, Infinity, -9, 20], low: NaN, mid: Infinity, high: -3 }), 800);
  assert.ok([...frame.bands].every(value => Number.isFinite(value) && value >= 0 && value <= 1));
  assert.equal(frame.low, 0); assert.equal(frame.mid, 0); assert.equal(frame.high, 0);
  assert.ok(frame.time <= 0.12);
  assert.equal(model.update(input(), NaN).time, frame.time);
});

test("暂停、零信号和普通播放不伪造音乐响应，静谧模式冻结水面与节拍", () => {
  for (const extra of [{ isPlaying: false }, { analysisStatus: "no-signal" }, { analysisStatus: "unavailable" }, { visualMode: "quiet" }]) {
    const model = signal.createRainSignal();
    const frame = model.update(input({ spectrum: Array(48).fill(1), beat: 1, ...extra }), 0.1);
    assert.equal(frame.reactive, false); assert.equal(frame.time, 0); assert.equal(frame.ripple, null);
    assert.equal(frame.low + frame.mid + frame.high + frame.energy + frame.beat, 0);
    frame.bands.forEach((value, i) => assert.ok(Math.abs(value - signal.restingBand(i)) < 1e-6));
  }
});

test("每次低频瞬态只触发一次涟漪，持续高值不逐帧注入并设最短间隔", () => {
  const model = signal.createRainSignal();
  assert.ok(model.update(input({ beat: 1 }), 0.033).ripple);
  assert.equal(model.update(input({ beat: 1 }), 0.033).ripple, null);
  model.update(input({ beat: 0 }), 0.033);
  assert.equal(model.update(input({ beat: 1 }), 0.033).ripple, null);
  for (let i = 0; i < 8; i++) model.update(input({ beat: 0 }), 0.033);
  assert.ok(model.update(input({ beat: 1 }), 0.033).ripple);
});

test("只有明确暂停才落水，缓冲和频谱失效仍保留原来的行为", () => {
  const model = signal.createRainSignal();
  model.update(input({ paused: false }), .1);
  const waiting = model.update(input({ isPlaying: false, paused: false, analysisStatus: "ready" }), .1);
  assert.equal(waiting.pauseProgress, null);
  const paused = model.update(input({ isPlaying: false, paused: true }), .1);
  assert.ok(paused.pauseProgress > 0 && paused.pauseProgress < 1);
  assert.equal(paused.ripple, null);
  const bands = Array.from(paused.bands);
  let final;
  for (let i = 0; i < 30; i++) final = model.update(input({ isPlaying: false, paused: true }), .1);
  assert.equal(final.pauseProgress, 1);
  assert.deepEqual(Array.from(final.bands), bands);
  assert.equal(model.update(input({ isPlaying: false, paused: true }), .1).time, final.time);
  assert.equal(model.update(input({ paused: false }), .1).pauseProgress, null);
});

test("窄屏与性能受限减少粒子、像素比和绘制频率，仍覆盖全部48列", () => {
  const normal = signal.rainQuality(1440, false, "immersive"), small = signal.rainQuality(390, false, "immersive"), limited = signal.rainQuality(1440, true, "immersive");
  assert.ok(normal.particles > small.particles && small.particles > limited.particles);
  assert.equal(small.pixelRatio, 1); assert.equal(limited.pixelRatio, 1);
  assert.ok(limited.interval > normal.interval);
  for (const quality of [normal, small, limited]) assert.equal(quality.particles % 48, 0);
});

function harness({ delayed = false, unavailable = false } = {}) {
  const mounted = [], unmounted = [], frames = new Map(), renders = [], sizes = [];
  const document = Object.assign(new EventTarget(), { hidden: false }), canvas = new EventTarget();
  let now = 1000, id = 0, created = 0, disposed = 0, failRender = false, resolveImport;
  let invalidate, moving = 0, resets = 0;
  const engine = { createRainScene: (_canvas, onInvalidate) => {
    if (unavailable) throw new Error("WebGL不可用");
    created++;
    invalidate = onInvalidate;
    return { resize: (...args) => sizes.push(args), render: frame => {
      if (failRender) throw new Error("绘制失败");
      renders.push({ ...frame, bands: Array.from(frame.bands) });
      onInvalidate();
      return moving-- > 0;
    }, resetView: () => { resets++; onInvalidate(); }, dispose: () => disposed++ };
  } };
  const imported = delayed ? new Promise(resolve => { resolveImport = () => resolve(engine); }) : Promise.resolve(engine);
  const window = Object.assign(new EventTarget(), { innerWidth: 1440, innerHeight: 900, devicePixelRatio: 2,
    requestAnimationFrame: callback => { frames.set(++id, callback); return id; }, cancelAnimationFrame: id => frames.delete(id) });
  const props = vue.reactive(input()), exports = {}, scope = vue.effectScope();
  runInNewContext(production, { exports, document, window, performance: { now: () => now }, require: name => {
    if (name === "vue") return { ...vue, onMounted: fn => mounted.push(fn), onBeforeUnmount: fn => unmounted.push(fn), watch: (source, callback) => vue.watch(source, callback, { flush: "sync" }) };
    if (name.endsWith("/signal")) return signal;
    if (name.endsWith("/loader")) return { loadRainScene: () => imported };
    throw new Error(`Unexpected dependency ${name}`);
  } });
  const rain = scope.run(() => exports.useMusicRain({ value: canvas }, () => props));
  mounted.forEach(fn => fn());
  return { rain, props, canvas, document, window, frames, renders, sizes,
    invalidate: () => invalidate(), move: count => { moving = count; invalidate(); },
    get resets() { return resets; },
    get created() { return created; }, get disposed() { return disposed; },
    resolveImport: () => resolveImport(), failRender: () => { failRender = true; },
    tick(count = 1) { for (let i = 0; i < count; i++) { now += 40; const pending = [...frames.values()]; frames.clear(); pending.forEach(fn => fn(now)); } },
    dispose() { unmounted.forEach(fn => fn()); scope.stop(); },
  };
}

test("背景隐藏停止绘制，恢复不重复建场景，卸载释放RAF和GPU所有者", async () => {
  const h = harness(); await settle(); h.tick(4);
  assert.equal(h.created, 1); assert.equal(h.frames.size, 1);
  h.document.hidden = true; h.document.dispatchEvent(new Event("visibilitychange"));
  const before = h.renders.length; h.tick(50); assert.equal(h.renders.length, before); assert.equal(h.frames.size, 0);
  h.document.hidden = false; h.document.dispatchEvent(new Event("visibilitychange")); h.tick(2);
  assert.equal(h.created, 1); assert.equal(h.frames.size, 1);
  h.dispose(); assert.equal(h.disposed, 1); assert.equal(h.frames.size, 0);
  h.window.dispatchEvent(new Event("resize")); h.document.dispatchEvent(new Event("visibilitychange"));
  assert.equal(h.created, 1);
});

test("仅暂停落水期间继续RAF，落完后停止，静谧和恢复沿用原单循环", async () => {
  const h = harness(); await settle(); h.tick(3); h.props.isPlaying = false; h.tick(10);
  assert.equal(h.frames.size, 1); assert.ok(h.renders.at(-1).pauseProgress < 1);
  h.tick(50);
  assert.equal(h.frames.size, 0); assert.equal(h.renders.at(-1).reactive, false);
  h.props.isPlaying = true; h.tick(2); assert.equal(h.frames.size, 1);
  h.props.visualMode = "quiet"; assert.equal(h.frames.size, 0); assert.equal(h.renders.at(-1).reactive, false);
  h.props.visualMode = "immersive"; h.tick(2); assert.equal(h.frames.size, 1);
  h.dispose();
});

test("WebGL创建或运行失败只回退背景，不请求音源且不留下绘制循环", async () => {
  const unavailable = harness({ unavailable: true }); await settle();
  assert.equal(unavailable.rain.state.value, "fallback"); assert.equal(unavailable.frames.size, 0); unavailable.dispose();
  const broken = harness(); await settle(); broken.failRender(); broken.tick(1);
  assert.equal(broken.rain.state.value, "fallback"); assert.equal(broken.disposed, 1); assert.equal(broken.frames.size, 0); broken.dispose();
  assert.equal(broken.disposed, 1);
});

test("卸载或隐藏期间迟到的引擎导入不能创建GPU场景", async () => {
  const gone = harness({ delayed: true }); gone.dispose(); gone.resolveImport(); await settle();
  assert.equal(gone.created, 0); assert.equal(gone.frames.size, 0);
  const hidden = harness({ delayed: true }); hidden.document.hidden = true; hidden.resolveImport(); await settle();
  assert.equal(hidden.created, 0);
  hidden.document.hidden = false; hidden.document.dispatchEvent(new Event("visibilitychange")); await settle();
  assert.equal(hidden.created, 1); hidden.dispose();
});

test("图形上下文丢失释放旧场景，恢复只建一个新场景并保持原频谱输入", async () => {
  const h = harness(); await settle(); h.tick(2);
  const event = new Event("webglcontextlost", { cancelable: true }); h.canvas.dispatchEvent(event);
  assert.equal(event.defaultPrevented, true); assert.equal(h.rain.state.value, "fallback"); assert.equal(h.disposed, 1); assert.equal(h.frames.size, 0);
  h.canvas.dispatchEvent(new Event("webglcontextrestored")); await settle();
  assert.equal(h.created, 2); assert.equal(h.rain.state.value, "ready"); assert.equal(h.frames.size, 1);
  h.dispose(); assert.equal(h.disposed, 2);
});

test("暂停或静谧中的相机可按需唤醒，有限惯性结束后停止且雨滴时间不推进", async () => {
  for (const extra of [{ isPlaying: false }, { visualMode: "quiet" }]) {
    const h = harness(); await settle(); Object.assign(h.props, extra); h.tick(60);
    assert.equal(h.frames.size, 0);
    const time = h.renders.at(-1).time;
    h.move(6); h.invalidate(); h.invalidate();
    assert.equal(h.frames.size, 1);
    h.tick(10);
    assert.equal(h.frames.size, 0);
    assert.equal(h.renders.at(-1).time, time);
    h.rain.resetView(); assert.equal(h.resets, 1); assert.equal(h.frames.size, 1);
    h.tick(3); assert.equal(h.frames.size, 0);
    h.dispose();
  }
});

test("相机重绘不创建音频外的第二个循环，隐藏/丢失/卸载后迟到事件无效", async () => {
  const h = harness(); await settle();
  for (let i = 0; i < 10; i++) h.invalidate();
  assert.equal(h.frames.size, 1); h.tick(2); assert.equal(h.frames.size, 1);
  h.document.hidden = true; h.document.dispatchEvent(new Event("visibilitychange"));
  h.move(8); assert.equal(h.frames.size, 0);
  h.document.hidden = false; h.document.dispatchEvent(new Event("visibilitychange"));
  h.canvas.dispatchEvent(new Event("webglcontextlost", { cancelable: true }));
  h.invalidate(); assert.equal(h.frames.size, 0);
  h.dispose(); h.invalidate(); assert.equal(h.frames.size, 0);
});
