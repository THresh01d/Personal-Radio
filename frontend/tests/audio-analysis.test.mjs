import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { test } from "node:test";
import { runInNewContext } from "node:vm";
const require = createRequire(import.meta.url);
const ts = require("typescript"), vue = require("vue");
const compiled = ts.transpileModule(readFileSync(new URL("../src/composables/useAudioAnalysis.ts", import.meta.url), "utf8"), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText;
const clockCompiled = ts.transpileModule(readFileSync(new URL("../src/composables/audioCaptureClock.ts", import.meta.url), "utf8"), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText;
const clockExports = {};
runInNewContext(clockCompiled, { exports: clockExports });
const settle = async () => { for (let i = 0; i < 30; i++) await Promise.resolve(); };
function harness(options = {}) {
  const mounted = [], unmounted = [], contexts = [], media = [], frames = new Map(), timers = new Map();
  let now = 0, sequence = 0, fallbacks = 0;
  const scope = vue.effectScope(), document = Object.assign(new EventTarget(), { hidden: false });
  class Node {
    connections = []; disconnected = false;
    connect(target) { if (options.connectFail) throw new Error("连接失败"); this.connections.push(target); }
    disconnect() { this.disconnected = true; }
  }
  class Analyser extends Node {
    frequencyBinCount = 1024;
    getByteFrequencyData(values) { if (options.sampleFail) throw new Error("读取失败"); values.fill(options.silent ? 0 : 80); }
  }
  class Context {
    state = "suspended"; sampleRate = 48000; destination = new Node(); sources = []; closed = false;
    constructor() { contexts.push(this); }
    resume() {
      if (options.resumeHang) return new Promise(() => {});
      if (options.resumeFail) return Promise.reject(new Error("恢复失败"));
      this.state = "running"; return Promise.resolve();
    }
    createMediaElementSource(target) { const node = new Node(); node.media = target; this.sources.push(node); return node; }
    createAnalyser() { if (options.nodeFail) throw new Error("节点失败"); return new Analyser(); }
    createGain() { const gain = new Node(); gain.gain = { value: 1 }; return gain; }
    close() { this.closed = true; this.state = "closed"; return Promise.resolve(); }
  }
  class Media extends EventTarget {
    paused = true; ended = false; seeking = false; readyState = 4; playbackRate = 1;
    duration = 120; currentTime = 0; _src = "";
    constructor() { super(); media.push(this); }
    emit(type) { this.dispatchEvent(new Event(type)); this[`on${type}`]?.(); }
    set src(value) { this._src = value; if (!options.metadataHang) this.onloadedmetadata?.(); }
    get src() { return this._src; }
    play() { this.paused = false; return options.playHang ? new Promise(() => {}) : Promise.resolve(); }
    pause() { this.paused = true; }
    removeAttribute() { this._src = ""; } load() {}
  }
  const window = {
    AudioContext: options.unsupported ? undefined : Context,
    requestAnimationFrame: fn => { frames.set(++sequence, fn); return sequence; }, cancelAnimationFrame: id => frames.delete(id),
    setTimeout: (fn, delay) => { timers.set(++sequence, { fn, at: now + delay }); return sequence; }, clearTimeout: id => timers.delete(id),
    setInterval: (fn, delay) => { timers.set(++sequence, { fn, at: now + delay, interval: delay }); return sequence; }, clearInterval: id => timers.delete(id),
  };
  const exports = {};
  runInNewContext(compiled, { exports, window, document, Audio: Media, AbortController,
    localStorage: { setItem() {} }, performance: { now: () => now },
    require: id => {
      if (id === "./audioCaptureClock") return clockExports;
      assert.equal(id, "vue");
      return { ...vue, onMounted: fn => mounted.push(fn), onBeforeUnmount: fn => unmounted.push(fn) };
    },
  });
  const audio = new Media(), mode = vue.ref("atmosphere");
  const analysis = scope.run(() => exports.useAudioAnalysis(audio, mode, () => fallbacks++));
  mounted.forEach(fn => fn());
  return { analysis, audio, mode, contexts, media, options, timers, frames, document, get fallbacks() { return fallbacks; },
    async tick(ms, draw = false) {
      now += ms;
      for (const target of media) {
        if (!options.frozen && !target.paused && !target.ended && !target.seeking && target.readyState >= 3) {
          target.currentTime += ms / 1000 * target.playbackRate;
        }
      }
      for (const [id, timer] of [...timers]) if (timer.at <= now) {
        if (timer.interval) timer.at = now + timer.interval; else timers.delete(id);
        timer.fn();
      }
      if (draw) { const pending = [...frames.values()]; frames.clear(); pending.forEach(fn => fn(now)); }
      await settle();
    },
    dispose() { unmounted.forEach(fn => fn()); scope.stop(); },
  };
}

test("真实采样才标记 active，连续零信号不生成声学档案", async () => {
  for (const silent of [true, false]) {
    const h = harness({ silent }); await h.analysis.prepare();
    h.audio.paused = false; h.analysis.beginFeatureCapture(); h.analysis.start(); await settle();
    for (let i = 0; i < 370; i++) await h.tick(34, true);
    assert.equal(h.analysis.status.value, silent ? "no-signal" : "active");
    const summary = h.analysis.finishFeatureCapture();
    assert.equal(Boolean(summary), !silent);
    if (silent) assert.equal(h.analysis.spectrum.value.every(n => n === 0), true);
    h.dispose();
  }
});

test("上下文不支持、恢复拒绝或挂起时不接管媒体", async () => {
  for (const setting of ["unsupported", "resumeFail", "resumeHang"]) {
    const h = harness({ [setting]: true }); const preparing = h.analysis.prepare(); await settle();
    await h.tick(1500); assert.equal(await preparing, false);
    assert.equal(h.analysis.status.value, "unavailable"); assert.equal(h.fallbacks, 0);
    assert.equal(h.contexts.flatMap(c => c.sources).length, 0);
    assert.equal(h.timers.size, 0); h.dispose();
  }
});

test("节点、输出连接或采样失败触发普通媒体恢复且停止帧循环", async () => {
  for (const failure of ["nodeFail", "connectFail", "sampleFail"]) {
    const h = harness({ [failure]: true }); await h.analysis.prepare();
    if (failure === "sampleFail") { h.audio.paused = false; h.analysis.start(); await settle(); await h.tick(34, true); }
    assert.equal(h.fallbacks, 1); assert.equal(h.analysis.status.value, "unavailable");
    assert.equal(h.frames.size, 0); assert.equal(h.contexts[0].closed, true); h.dispose();
  }
});

test("已接管上下文暂停恢复挂起必须退出分析，迟到启动不能突破 stop", async () => {
  const h = harness(); await h.analysis.prepare(); h.contexts[0].state = "suspended"; h.options.resumeHang = true;
  h.audio.paused = false; h.analysis.start(); h.analysis.stop();
  await h.tick(1500);
  assert.equal(h.fallbacks, 1); assert.equal(h.frames.size, 0); h.dispose();
  const second = harness(); second.audio.paused = false; second.analysis.start(); second.analysis.stop(); await settle();
  assert.equal(second.frames.size, 0); second.dispose();
});

test("后台 metadata、play 等待均可立即取消，结束后释放资源", async () => {
  for (const phase of ["metadataHang", "playHang"]) {
    const h = harness({ [phase]: true }); const work = h.analysis.analyzeUrlInBackground("https://test/media"); await settle();
    h.analysis.cancelBackgroundAnalysis(); await settle();
    assert.equal(await work, null); assert.equal(h.timers.size, 0);
    assert.equal(h.media[1].paused, true); assert.equal(h.media[1].src, ""); h.dispose();
  }
});

test("后台恢复等待可取消、只允许单任务，metadata 超时允许下一次任务", async () => {
  const h = harness({ resumeHang: true }); const work = h.analysis.analyzeUrlInBackground("https://test/one"); await settle();
  assert.equal(await h.analysis.analyzeUrlInBackground("https://test/two"), null);
  h.analysis.cancelBackgroundAnalysis(); assert.equal(await work, null); h.dispose(); await h.tick(1500);
  const next = harness({ metadataHang: true }); const pending = next.analysis.analyzeUrlInBackground("https://test/one"); await settle();
  await next.tick(8000); assert.equal(await pending, null); assert.equal(next.timers.size, 0);
  next.options.metadataHang = false; const valid = next.analysis.analyzeUrlInBackground("https://test/two"); await settle();
  for (let i = 0; i < 155; i++) await next.tick(100);
  assert.ok(await valid); next.dispose();
});

test("隐藏页面、前台播放和卸载取消后台工作，不产出部分结果", async () => {
  for (const phase of ["hidden", "playing", "dispose"]) {
    const h = harness(); const work = h.analysis.analyzeUrlInBackground("https://test/media"); await settle();
    if (phase === "hidden") { h.document.hidden = true; h.document.dispatchEvent(new Event("visibilitychange")); }
    if (phase === "playing") { h.audio.paused = false; await h.tick(100); }
    if (phase === "dispose") h.dispose();
    assert.equal(await work, null); assert.equal(h.timers.size, 0);
    if (phase !== "dispose") h.dispose();
  }
});

test("静谧只关闭显示，不伪造或丢弃真实分析摘要", async () => {
  const h = harness(); h.mode.value = "quiet"; h.audio.paused = false; h.analysis.beginFeatureCapture(); h.analysis.start(); await settle();
  for (let i = 0; i < 370; i++) await h.tick(34, true);
  assert.equal(h.analysis.energy.value, 0); assert.ok(h.analysis.finishFeatureCapture()); h.dispose();
});

const draw = async (h, frames = 370, milliseconds = 34) => {
  for (let index = 0; index < frames; index++) await h.tick(milliseconds, true);
};
async function foreground(options = {}) {
  const h = harness(options);
  h.audio.paused = false;
  h.analysis.beginFeatureCapture(); h.analysis.start(); await settle();
  return h;
}

test("前台正常样本只累计连续媒体时间，首帧不凭空增加时长", async () => {
  const h = await foreground(); await draw(h);
  const result = h.analysis.finishFeatureCapture();
  assert.ok(result);
  assert.ok(Math.abs(result.sample_seconds - 369 * .034) < 1e-8);
  h.dispose();
});

test("前台频谱非零但媒体冻结、定位、未就绪、结束或非正常倍率均不形成摘要", async () => {
  for (const variant of ["frozen", "seeking", "not-ready", "ended", "rate", "nan-time"]) {
    const h = await foreground({ frozen: variant === "frozen" });
    if (variant === "seeking") h.audio.seeking = true;
    if (variant === "not-ready") h.audio.readyState = 2;
    if (variant === "ended") h.audio.ended = true;
    if (variant === "rate") h.audio.playbackRate = 1.5;
    if (variant === "nan-time") h.audio.currentTime = NaN;
    await draw(h);
    assert.equal(h.analysis.finishFeatureCapture(), null, variant);
    assert.equal(h.fallbacks, 0, variant);
    h.dispose();
  }
});

test("定位和回调长空白只重建起点，不把跳过的媒体位置算成观察", async () => {
  const h = await foreground(); await draw(h, 180);
  h.audio.currentTime += 50; await h.tick(34, true);
  h.audio.currentTime -= 20; await h.tick(34, true);
  await h.tick(3000, true);
  await draw(h, 180);
  const result = h.analysis.finishFeatureCapture();
  assert.ok(result);
  assert.ok(Math.abs(result.sample_seconds - (179 + 180) * .034) < 1e-8);
  h.dispose();
});

test("冻结、waiting与定位后不必等待特定playing事件，正常推进即可重新观察", async () => {
  const h = await foreground(); await draw(h, 180);
  h.options.frozen = true; h.audio.emit("waiting"); await draw(h, 100);
  h.audio.seeking = true; h.audio.currentTime += 20; h.audio.emit("seeking"); await draw(h, 100);
  h.audio.seeking = false; h.audio.emit("seeked"); h.options.frozen = false;
  await draw(h, 180);
  const result = h.analysis.finishFeatureCapture();
  assert.ok(result);
  assert.ok(Math.abs(result.sample_seconds - 358 * .034) < 1e-8);
  h.dispose();
});

test("下载stalled但音乐继续推进不会中断有效采样", async () => {
  const h = await foreground(); await draw(h, 180);
  h.audio.emit("stalled"); await draw(h, 190);
  const result = h.analysis.finishFeatureCapture();
  assert.ok(result);
  assert.ok(Math.abs(result.sample_seconds - 369 * .034) < 1e-8);
  h.dispose();
});

test("隐藏与暂停恢复不把空白计时，恢复后继续同次片段统计", async () => {
  const h = await foreground(); await draw(h, 180);
  h.audio.paused = true; h.audio.emit("pause"); h.analysis.stop(); await h.tick(8000, true);
  h.audio.paused = false; h.analysis.start(); await settle(); await draw(h, 90);
  h.document.hidden = true; h.document.dispatchEvent(new Event("visibilitychange")); await h.tick(8000, true);
  h.document.hidden = false; h.document.dispatchEvent(new Event("visibilitychange")); await settle(); await draw(h, 100);
  const result = h.analysis.finishFeatureCapture();
  assert.ok(result);
  assert.ok(Math.abs(result.sample_seconds - (179 + 89 + 99) * .034) < 1e-8);
  h.dispose();
});

test("事件即使发生在两帧之间也会重建基线，切普通播放及卸载会解除监听", async () => {
  const h = await foreground(); await draw(h, 180);
  h.audio.emit("waiting"); await draw(h, 190);
  const result = h.analysis.finishFeatureCapture();
  assert.ok(Math.abs(result.sample_seconds - 368 * .034) < 1e-8);
  let removed = 0;
  const remove = h.audio.removeEventListener.bind(h.audio);
  h.audio.removeEventListener = (...args) => { removed++; remove(...args); };
  h.analysis.useOrdinaryAudio(new EventTarget());
  assert.equal(removed, 8);
  h.dispose(); assert.equal(removed, 8);
  const other = await foreground(); let cleaned = 0;
  other.audio.removeEventListener = () => { cleaned++; };
  other.dispose(); assert.equal(cleaned, 8);
});

test("前后台共用时钟兼容100ms媒体进度取整，重复标记不会重复累计", () => {
  const clock = clockExports.createMediaCaptureClock();
  const media = { paused: false, ended: false, seeking: false, readyState: 3, playbackRate: 1, currentTime: 0 };
  let seconds = 0;
  for (let now = 0; now <= 15000; now += 34) {
    media.currentTime = Math.floor(now / 100) / 10;
    seconds += clock.observe(media, now, true);
  }
  assert.ok(seconds > 12 && seconds <= 15);
  assert.equal(clock.observe(media, 14994, true), 0);
  assert.equal(clock.observe(media, Infinity, true), 0);
  assert.equal(clock.observe(media, 16000, false), 0);
});

test("媒体时钟小幅抖动不能逐帧取较小值而丢掉连续有效时长", () => {
  const clock = clockExports.createMediaCaptureClock();
  const media = { paused: false, ended: false, seeking: false, readyState: 4, playbackRate: 1, currentTime: 0 };
  assert.equal(clock.observe(media, 0, true), 0);
  let seconds = 0;
  for (let index = 1; index <= 440; index++) {
    media.currentTime += index % 2 ? .06 : .008;
    seconds += clock.observe(media, index * 34, true);
  }
  assert.ok(Math.abs(seconds - 14.96) < 1e-8);
});

test("后台静音输出仍可观察，卡住或状态无效则超时清理且不输出摘要", async () => {
  for (const variant of ["normal", "frozen", "seeking", "not-ready", "ended", "rate"]) {
    const h = harness({ frozen: variant === "frozen" });
    const work = h.analysis.analyzeUrlInBackground("https://test/media", 12); await settle();
    const target = h.media[1];
    if (variant === "seeking") target.seeking = true;
    if (variant === "not-ready") target.readyState = 2;
    if (variant === "ended") target.ended = true;
    if (variant === "rate") target.playbackRate = .5;
    for (let index = 0; index < 225; index++) await h.tick(100);
    const result = await work;
    assert.equal(Boolean(result), variant === "normal", variant);
    if (result) assert.ok(Math.abs(result.sample_seconds - 12) < .11);
    assert.equal(target.paused, true); assert.equal(target.src, "");
    assert.equal(h.timers.size, 0); assert.equal(h.fallbacks, 0);
    h.dispose();
  }
});

test("后台定位与缓冲恢复会重建起点，stalled不会拦住继续推进的媒体", async () => {
  const h = harness(); const work = h.analysis.analyzeUrlInBackground("https://test/media", 12); await settle();
  const target = h.media[1];
  for (let index = 0; index < 60; index++) await h.tick(100);
  target.emit("waiting"); target.readyState = 2;
  for (let index = 0; index < 10; index++) await h.tick(100);
  target.seeking = true; target.currentTime += 40; target.emit("seeking");
  for (let index = 0; index < 10; index++) await h.tick(100);
  target.seeking = false; target.readyState = 4; target.emit("seeked"); target.emit("stalled");
  for (let index = 0; index < 65; index++) await h.tick(100);
  const result = await work; assert.ok(result);
  assert.ok(result.sample_seconds >= 12 && result.sample_seconds < 12.11);
  assert.equal(h.timers.size, 0); h.dispose();
});
