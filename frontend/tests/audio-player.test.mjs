import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { test } from "node:test";
import { runInNewContext } from "node:vm";

const require = createRequire(import.meta.url);
const ts = require("typescript");
const vue = require("vue");
// 运行生产 composable，只替换网络、媒体和生命周期边界；使用真正的 Vue 响应式。
const compiled = ts.transpileModule(
  readFileSync(new URL("../src/composables/useAudioPlayer.ts", import.meta.url), "utf8"),
  { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } },
).outputText;
const song = (id, extra = {}) => ({ id, title: `歌曲${id}`, artist: "测试歌手", ...extra });
const deferred = () => {
  let resolve, reject;
  const promise = new Promise((ok, fail) => { resolve = ok; reject = fail; });
  return { promise, resolve, reject };
};
class ApiError extends Error {
  constructor(message, status, retryAfterMs) { super(message); this.status = status; this.retryAfterMs = retryAfterMs; }
}
const settle = async () => { for (let i = 0; i < 15; i++) await Promise.resolve(); };

function harness(overrides = {}, options = {}) {
  const mounted = [], unmounted = [], audios = [], events = [], notices = [], calls = [];
  const timers = new Map();
  let now = Date.now(), timerId = 0;
  const scope = vue.effectScope();
  class FakeAudio extends EventTarget {
    src = "";
    currentTime = 0;
    duration = NaN;
    paused = true;
    ended = false;
    error = null;
    volume = 1;
    readyState = 0;
    seeking = false;
    onended = null;
    onerror = null;
    playImpl = null;
    constructor() { super(); audios.push(this); }
    emit(type) {
      // 模拟元数据加载时先推进就绪状态，避免事件与 HAVE_NOTHING 矛盾而误触生产恢复门控。
      if (type === "loadedmetadata") this.readyState = Math.max(this.readyState, 1);
      this.dispatchEvent(new Event(type)); this[`on${type}`]?.();
    }
    play() {
      this.paused = false;
      this.ended = false;
      this.emit("play");
      if (this.playImpl) return this.playImpl();
      this.readyState = 4;
      this.emit("playing");
      return Promise.resolve();
    }
    pause() { if (!this.paused) { this.paused = true; this.emit("pause"); } }
    removeAttribute() { this.src = ""; }
    load() { this.currentTime = 0; this.duration = NaN; this.readyState = 0; this.error = null; this.ended = false; }
    finish() { this.ended = true; this.paused = true; this.emit("ended"); }
  }
  const analysis = {
    status: vue.ref("ready"),
    prepare: async () => true,
    start() {}, stop() {}, beginFeatureCapture() {}, finishFeatureCapture: () => null,
    cancelBackgroundAnalysis() {}, analyzeUrlInBackground: async () => null,
    useOrdinaryAudio() {},
  };
  const api = {
    ApiError,
    getPlayUrl: async (id, signal) => { calls.push({ id, signal }); return { success: true, url: `https://media/${id}` }; },
    getLyrics: async () => ({ lines: [], instrumental: false }),
    getPendingAudioFeatures: async () => [],
    getTtsUrl: () => "https://media/voice",
    renewLocalAccess: async () => ({ ok: true }),
    prepareAudioObservation: async () => null,
    submitAudioObservation: async () => ({ status: "accepted" }),
    cancelAudioObservation: async () => ({ cancelled: true }),
    ...overrides,
  };
  const storage = new Map([
    ["personal-radio-announce-on", "off"], ["personal-radio-autoplay-on", "on"],
    ["personal-radio-acoustic-backfill", "off"],
    ...(options.initialStorage || []),
  ]);
  const exports = {};
  const modules = new Map();
  const sandbox = {
    exports, Audio: FakeAudio, AbortController, DOMException, Error,
    crypto: { randomUUID: () => `playback-${++timerId}` }, Date: class extends Date { static now() { return now; } },
    Math: Object.assign(Object.create(Math), { random: () => 0 }),
    localStorage: { getItem: (key) => storage.get(key) ?? null, setItem: (key, value) => storage.set(key, value) },
    navigator: {}, document: Object.assign(new EventTarget(), { hidden: false }),
    window: {
      matchMedia: () => ({ matches: false }),
      setTimeout: (callback, delay) => { timers.set(++timerId, { callback, at: now + delay }); return timerId; },
      clearTimeout: (id) => timers.delete(id),
      ...options.browser,
    },
    require: (id) => {
      if (id === "vue") return {
        ...vue, onMounted: (fn) => mounted.push(fn), onBeforeUnmount: (fn) => unmounted.push(fn),
        watch: (source, callback) => vue.watch(source, callback, { flush: "sync" }),
      };
      if (id.endsWith("/services/api")) return api;
      if (id === "./useAudioAnalysis") return { useAudioAnalysis: (_audio, _mode, fallback) => { analysis.fallback = fallback; return analysis; } };
      if (id === "./voicePlayback") return sandbox.require("./player/voicePlayback");
      if (id.startsWith("./player/")) {
        // 每个播放器实例单独加载生产模块，所有模块共用同一组假媒体、时钟与网络。
        if (!modules.has(id)) {
          const result = {};
          modules.set(id, result);
          const code = ts.transpileModule(
            readFileSync(new URL(`../src/composables/${id}.ts`, import.meta.url), "utf8"),
            { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } },
          ).outputText;
          runInNewContext(code, { ...sandbox, exports: result });
        }
        return modules.get(id);
      }
      throw new Error(`Unexpected dependency ${id}`);
    },
  };
  runInNewContext(compiled, sandbox);
  const player = scope.run(() => exports.useAudioPlayer({
    notify: (message) => notices.push(message), onLoginRequired: () => notices.push("login"),
    onListeningEvent: (track, type, position, duration, context) => events.push({ id: track.id, type, position, duration, context }),
    ...options,
  }));
  mounted.forEach((fn) => fn());
  return {
    player, storage, get audio() { return audios.length > 2 ? audios.at(-1) : audios[0]; }, audios, voice: audios[1], calls, events, notices, analysis,
    advance(ms) {
      now += ms;
      for (const [id, timer] of [...timers]) {
        if (timer.at <= now) { timers.delete(id); timer.callback(); }
      }
    },
    dispose() { unmounted.forEach((fn) => fn()); scope.stop(); },
  };
}

const observationLease = (id, sequence = 1) => ({
  observation_id: sequence.toString(16).padStart(32, "0"),
  binding: { song_id: id, library_generation: "b".repeat(32), song_revision: "c".repeat(32),
    metadata_sha256: "d".repeat(64), feature_algorithm: "web-audio-v1", capture_contract: "media-clock-v1",
    measurement_mode: "foreground", capture_configuration: "fft2048-s078-bands-v1" },
  expires_at: Date.now() / 1000 + 1200,
});
const foregroundSummary = () => ({ energy_mean: .4, energy_peak: .8, low_ratio: .5, brightness: .2,
  dynamic_range: .3, beat_density: 80, sample_seconds: 20, algorithm_version: "web-audio-v1" });

test("循环和主动重播有不同来源，暂停恢复不制造新播放", async () => {
  const h=harness(); await h.player.playSong(song('a'));
  const first=h.events[0].context.playback_id;
  h.player.togglePlayback(); h.player.togglePlayback();
  assert.equal(h.events.length,1);
  h.player.playbackMode.value='repeat-one'; h.audio.duration=100; h.audio.currentTime=100; h.audio.finish();
  assert.equal(h.events.at(-1).type,'play');
  assert.equal(h.events.at(-1).context.start_reason,'repeat_one');
  assert.notEqual(h.events.at(-1).context.playback_id,first);
  h.player.autoPlayOn.value=false; h.audio.currentTime=100; h.audio.finish(); h.player.togglePlayback();
  assert.equal(h.events.at(-1).type,'replay'); assert.equal(h.events.at(-1).context.start_reason,'user_replay');
  h.dispose();
});

test("故障只报一次中断，之后换歌不误记主动跳过", async () => {
  const h=harness(); await h.player.playSong(song('a'));
  h.audio.error={code:3}; h.audio.emit('error'); h.audio.emit('error');
  assert.equal(h.events.filter(e=>e.type==='skip').length,1);
  assert.equal(h.events.at(-1).context.interrupted,true);
  assert.equal(h.events.at(-1).context.playback_id,h.events[0].context.playback_id);
  await h.player.playSong(song('b')); assert.equal(h.events.filter(e=>e.type==='skip').length,1);
  await h.player.playSong(song('c')); assert.equal(h.events.at(-2).context.interrupted,false);
  h.dispose();
});

test("自然续播与主动重新点同一首分别记录来源", async () => {
  const h=harness(); const queue=[song('a'),song('b')];
  await h.player.playSong(queue[0],queue); h.audio.finish(); await settle();
  assert.equal(h.events.at(-1).context.start_reason,'auto_next');
  await h.player.playSong(queue[1],queue);
  assert.equal(h.events.at(-1).type,'replay'); assert.equal(h.events.at(-1).context.start_reason,'user_replay');
  h.dispose();
});

test("播放时间排除暂停缓冲和拖动，不把进度条位置当听过", async () => {
  const h=harness(); await h.player.playSong(song('a')); h.audio.duration=100;
  h.advance(10_000); h.audio.currentTime=10; h.audio.emit('timeupdate');
  h.player.togglePlayback(); h.advance(30_000); h.player.togglePlayback();
  h.player.seekToRatio(.9); h.advance(10_000); h.audio.currentTime=100; h.audio.finish();
  const finish=h.events.find(e=>e.type==='finish');
  assert.equal(finish.context.played_seconds,20);
  assert.equal(finish.context.playback_id,h.events[0].context.playback_id);
  h.dispose();
});

test("清空后从手动队列选中歌曲启动仍有完整来源", async () => {
  const h=harness(); await h.player.playSong(song('a')); h.player.clearPlayer();
  h.player.appendToQueue(song('b')); h.player.togglePlayback(); await settle();
  assert.equal(h.events.at(-1).id,'b'); assert.equal(h.events.at(-1).type,'play');
  assert.equal(h.events.at(-1).context.start_reason,'user'); h.dispose();
});

test("前台观察只在真正playing后申请，申请等待不阻塞音乐或切歌", async () => {
  const pending = deferred(); let prepares = 0, begins = 0;
  const h = harness({ prepareAudioObservation: () => { prepares++; return pending.promise; } });
  h.analysis.beginFeatureCapture = () => { begins++; };
  h.audio.playImpl = () => Promise.resolve();
  await h.player.playSong(song("a"));
  assert.equal(prepares, 0);
  h.audio.emit("playing");
  assert.equal(prepares, 1); assert.equal(begins, 0);
  assert.equal(h.player.isPlaying.value, true); assert.equal(h.player.audioLoading.value, false);
  pending.resolve(observationLease("a")); await settle();
  assert.equal(begins, 1);
  h.dispose(); await settle();
});

test("暂停缓冲和同曲重连共享租约，切歌仅提交新协议且不污染歌曲旧声学视图", async () => {
  const prepares = [], packets = [], legacy = [];
  const h = harness({
    prepareAudioObservation: async id => { prepares.push(id); return observationLease(id, prepares.length); },
    submitAudioObservation: async packet => { packets.push(packet); return { status: "accepted" }; },
  }, { onAudioFeatures: (...args) => legacy.push(args) });
  let begins = 0;
  h.analysis.beginFeatureCapture = () => { begins++; };
  h.analysis.finishFeatureCapture = foregroundSummary;
  const track = song("a");
  await h.player.playSong(track); await settle();
  h.player.togglePlayback(); h.player.togglePlayback(); await settle();
  h.audio.emit("waiting"); h.audio.emit("playing"); await settle();
  h.audio.emit("waiting"); h.advance(25_000); await settle();
  assert.equal(h.calls.length, 2);
  assert.equal(h.player.isPlaying.value, true);
  assert.deepEqual(prepares, ["a"]); assert.equal(begins, 1); assert.equal(packets.length, 0);
  await h.player.playSong(song("b")); await settle();
  assert.equal(packets.length, 1); assert.equal(packets[0].song_id, "a");
  assert.equal(legacy.length, 0); assert.equal(track.audio_features, undefined);
  h.analysis.finishFeatureCapture = () => null;
  h.dispose(); await settle();
});

test("旧prepare迟到不会重置新歌capture，新歌仍用自己的租约", async () => {
  const pending = deferred(), cancellations = [], packets = []; let oldSignal, begins = 0;
  const h = harness({
    prepareAudioObservation: (id, signal) => {
      if (id === "a") { oldSignal = signal; return pending.promise; }
      return Promise.resolve(observationLease(id, 2));
    },
    cancelAudioObservation: async id => { cancellations.push(id); },
    submitAudioObservation: async packet => { packets.push(packet); },
  });
  h.analysis.beginFeatureCapture = () => { begins++; };
  h.analysis.finishFeatureCapture = foregroundSummary;
  await h.player.playSong(song("a")); await h.player.playSong(song("b")); await settle();
  assert.equal(oldSignal.aborted, true); assert.equal(begins, 1);
  pending.resolve(observationLease("a")); await settle();
  assert.equal(begins, 1); assert.deepEqual(cancellations, [observationLease("a").observation_id]);
  h.audio.finish(); await settle();
  assert.equal(packets.length, 1); assert.equal(packets[0].song_id, "b");
  h.dispose(); await settle();
});

test("分析降级收尾已绑定片段，普通播放和后续切歌不申请假观察", async () => {
  let prepares = 0; const packets = [];
  const h = harness({
    prepareAudioObservation: async id => { prepares++; return observationLease(id); },
    submitAudioObservation: async packet => { packets.push(packet); },
  });
  h.analysis.finishFeatureCapture = foregroundSummary;
  await h.player.playSong(song("a")); await settle();
  h.analysis.fallback(); await settle();
  assert.equal(packets.length, 1); assert.equal(h.player.isPlaying.value, true);
  await h.player.playSong(song("b")); await settle();
  assert.equal(prepares, 1); assert.equal(packets.length, 1);
  h.dispose(); await settle();
});

test("最终媒体失败收尾，手动重试开启新观察但不重复记播放开始", async () => {
  let prepares = 0; const packets = [];
  const h = harness({
    prepareAudioObservation: async id => observationLease(id, ++prepares),
    submitAudioObservation: async packet => { packets.push(packet); },
  });
  h.analysis.finishFeatureCapture = foregroundSummary;
  await h.player.playSong(song("a")); await settle();
  h.audio.error = { code: 3 }; h.audio.emit("error"); await settle();
  assert.equal(h.player.playbackState.value, "error"); assert.equal(packets.length, 1);
  h.audio.error = null; h.player.togglePlayback(); await settle();
  assert.equal(prepares, 2); assert.equal(h.player.isPlaying.value, true);
  assert.deepEqual(h.events.map(e => e.type), ["play", "skip"]);
  h.analysis.finishFeatureCapture = () => null;
  assert.equal(h.events.at(-1).context.interrupted, true);
  h.dispose(); await settle();
});

test("自然结束与单曲重播各用独立观察，重复ended不重复提交", async () => {
  let prepares = 0; const packets = [];
  const h = harness({
    prepareAudioObservation: async id => observationLease(id, ++prepares),
    submitAudioObservation: async packet => { packets.push(packet); },
  });
  h.analysis.finishFeatureCapture = foregroundSummary;
  h.player.autoPlayOn.value = false;
  await h.player.playSong(song("a")); await settle();
  h.audio.finish(); h.audio.emit("ended"); await settle();
  assert.equal(packets.length, 1);
  h.player.togglePlayback(); await settle();
  assert.equal(prepares, 2);
  h.audio.finish(); await settle();
  assert.equal(packets.length, 2);
  assert.notEqual(packets[0].observation_id, packets[1].observation_id);
  h.dispose(); await settle();
});

test("清空后仅准备队列再点击播放，也为新曲开启独立观察", async () => {
  const prepares = [];
  const h = harness({ prepareAudioObservation: async id => { prepares.push(id); return observationLease(id, prepares.length); } });
  await h.player.playSong(song("a")); await settle();
  h.player.clearPlayer(); h.player.prepareQueue([song("b")], "manual");
  h.player.togglePlayback(); await settle();
  assert.deepEqual(prepares, ["a", "b"]);
  h.dispose(); await settle();
});

test("随机只调整未播放部分，切回顺序使用当前显示队列且不打断音乐", async () => {
  const h = harness();
  const queue = [song("a"), song("b"), song("c"), song("d"), song("e")];
  await h.player.playSong(queue[1], queue, "manual");
  h.audio.currentTime = 17;
  h.player.playbackMode.value = "shuffle";
  assert.deepEqual(Array.from(h.player.playbackQueue.value, ({ id }) => id), ["a", "b", "d", "e", "c"]);
  assert.equal(h.audio.currentTime, 17);
  assert.equal(h.calls.length, 1);
  assert.equal(h.player.currentSong.value.id, "b");
  assert.equal(h.storage.get("personal-radio-playback-mode"), "shuffle");
  h.player.playbackMode.value = "sequence";
  h.audio.finish(); await settle();
  assert.equal(h.player.currentSong.value.id, "d");
  h.dispose();
});

test("同一轮只播一次，切歌暂停恢复和刷新仍保留轮次去重", async () => {
  const texts = [];
  const h = harness({ getTtsUrl: (text) => { texts.push(text); return "https://media/voice"; } });
  h.player.announceOn.value = true;
  const queue = [song("a", { batch_id: "round", round_intro: "这轮是有来源的整轮开场" }), song("b", { batch_id: "round", round_intro: "相同轮次" })];
  const first = h.player.playSong(queue[0], queue); await settle(); h.voice.finish(); await first;
  await h.player.playSong(queue[1], queue);
  h.player.togglePlayback(); h.player.togglePlayback(); await settle();
  assert.deepEqual(texts, ["这轮是有来源的整轮开场"]);
  const saved = h.storage.get("personal-radio-announced-batches"); h.dispose();
  const restored = harness({ getTtsUrl: () => { throw new Error("不能重新播报"); } }, { initialStorage: [["personal-radio-announced-batches", saved]] });
  restored.player.announceOn.value = true;
  await restored.player.playSong(queue[0], queue);
  assert.equal(restored.audio.src, "https://media/a"); restored.dispose();
});

test("语音获取最多等十五秒，开始后最多一分钟，关闭原开关立刻进音乐", async () => {
  for (const path of ["timeout", "limit", "off"]) {
    const h = harness(); h.player.announceOn.value = true;
    if (path === "timeout") h.voice.playImpl = () => new Promise(() => {});
    const first = h.player.playSong(song("a", { batch_id: path, round_intro: "可靠开场" }));
    await settle();
    if (path === "off") h.player.announceOn.value = false;
    else h.advance(path === "limit" ? 60_000 : 15_000);
    await first;
    assert.equal(h.player.isAnnouncing.value, false);
    assert.equal(h.audio.src, "https://media/a"); h.dispose();
  }
});

test("手动歌曲即使带历史背景也不播报，关闭开关不消耗未播轮次", async () => {
  const h = harness(); const a = song("a", { batch_id: "r", round_intro: "开场" });
  h.player.announceOn.value = true;
  await h.player.playSong(a, [a], "manual");
  assert.equal(h.voice.src, "");
  h.player.announceOn.value = false;
  await h.player.playSong(a, [a], "recommendation");
  assert.equal(h.storage.has("personal-radio-announced-batches"), false);
  h.player.announceOn.value = true;
  const playing = h.player.playSong(a, [a], "recommendation"); await settle();
  assert.equal(h.player.isAnnouncing.value, true); h.player.skipAnnouncement(); await playing; h.dispose();
});

test("只替换非当前歌曲不重载媒体，保留显示位置并继承本轮播报", async () => {
  const h = harness(); h.player.announceOn.value = true;
  const q = [song("a", { batch_id: "base", round_intro: "开场" }), song("b", { batch_id: "base" }), song("c", { batch_id: "base" })];
  const playing = h.player.playSong(q[0], q); await settle(); h.voice.finish(); await playing;
  h.audio.currentTime = 42;
  const next = [song("a", { batch_id: "revised" }), song("x", { batch_id: "revised", round_intro: "不应重复" }), song("c", { batch_id: "revised" })];
  assert.equal(h.player.applyAdjustment("base", next, "b"), true);
  assert.equal(h.audio.currentTime, 42); assert.equal(h.calls.length, 1);
  assert.deepEqual(Array.from(h.player.playbackQueue.value, (s) => s.id), ["a", "x", "c"]);
  await h.player.playSong(next[1], h.player.playbackQueue.value);
  assert.equal(h.player.isAnnouncing.value, false); h.dispose();
});

test("换当前歌使用真实 skip 与新地址，手动边界拒绝迟到修订", async () => {
  const h = harness(); const q = [song("a", { batch_id: "base" }), song("b", { batch_id: "base" })];
  await h.player.playSong(q[0], q); h.audio.currentTime = 35;
  assert.equal(h.player.applyAdjustment("base", [song("x", { batch_id: "next" }), song("b", { batch_id: "next" })], "a"), true);
  await settle(); assert.equal(h.audio.src, "https://media/x");
  assert.equal(h.events.filter((e) => e.type === "skip").length, 1);
  h.player.appendToQueue(song("manual"));
  assert.equal(h.player.applyAdjustment("next", [song("late")]), false); h.dispose();
});

test("随机播放只换当前槽位，其他歌维持换歌前的可见顺序", async () => {
  const h = harness();
  h.player.playbackMode.value = "shuffle";
  const q = ["a", "b", "c"].map(id => song(id, { batch_id: "base" }));
  await h.player.playSong(q[0], q);
  assert.deepEqual(Array.from(h.player.playbackQueue.value, ({ id }) => id), ["a", "c", "b"]);
  const revised = ["x", "c", "b"].map(id => song(id, { batch_id: "revised" }));
  assert.equal(h.player.applyAdjustment("base", revised, "a"), true);
  await settle();
  assert.equal(h.audio.src, "https://media/x");
  assert.deepEqual(Array.from(h.player.playbackQueue.value, ({ id }) => id), ["x", "c", "b"]);
  h.dispose();
});

test("全轮调整不打断正在播放的歌，结束后接新队列；未开播时更新选中歌", async () => {
  const h = harness(); const a = song("a", { batch_id: "base" });
  await h.player.playSong(a, [a]); h.audio.currentTime = 40;
  assert.equal(h.player.applyAdjustment("base", [song("x", { batch_id: "new" })]), true);
  assert.equal(h.audio.currentTime, 40); assert.equal(h.player.currentSong.value.id, "a");
  assert.equal(h.player.canGoNext.value, true);
  h.audio.duration = 100; h.audio.finish(); await settle();
  assert.equal(h.player.currentSong.value.id, "x"); h.dispose();
  const idle = harness(); idle.player.prepareQueue([a]);
  assert.equal(idle.player.applyAdjustment("base", [song("x", { batch_id: "new" })]), true);
  assert.equal(idle.player.currentSong.value.id, "x"); assert.equal(idle.audio.src, ""); idle.dispose();
});

test("随机模式新队列保留指定开始歌曲，整轮不重复且手动播完停止", async () => {
  let extensions = 0;
  const h = harness({}, { onQueueExhausted: async () => { extensions++; return []; } });
  h.player.playbackMode.value = "shuffle";
  const queue = [song("a"), song("b"), song("c"), song("d")];
  await h.player.playSong(queue[0], queue, "manual");
  const order = Array.from(h.player.playbackQueue.value, ({ id }) => id);
  assert.equal(order[0], "a");
  for (let index = 0; index < queue.length; index++) { h.audio.finish(); await settle(); }
  assert.deepEqual(h.events.filter(({ type }) => type === "play").map(({ id }) => id), order);
  assert.equal(new Set(order).size, queue.length);
  assert.equal(extensions, 0);
  assert.equal(h.player.playbackState.value, "ended");
  h.dispose();
});

test("单曲循环走真实重播路径，不重新取地址报幕，下一首仍可主动切换", async () => {
  const h = harness();
  h.player.playbackMode.value = "repeat-one";
  const queue = [song("a", { dj_intro: "真实台词" }), song("b")];
  await h.player.playSong(queue[0], queue, "manual");
  h.player.announceOn.value = true;
  h.audio.duration = 100; h.audio.currentTime = 100; h.audio.finish(); h.audio.emit("ended");
  assert.equal(h.audio.currentTime, 0);
  assert.equal(h.player.currentSong.value.id, "a");
  assert.equal(h.player.isPlaying.value, true);
  assert.equal(h.voice.src, "");
  assert.equal(h.calls.length, 1);
  assert.deepEqual(h.events.map(({ type }) => type), ["play", "finish", "play"]);
  h.player.moveQueue(1); await settle();
  assert.equal(h.player.currentSong.value.id, "b");
  h.dispose();
});

test("关闭自动连播优先于单曲循环，歌曲结束保持停止", async () => {
  const h = harness(); h.player.playbackMode.value = "repeat-one"; h.player.autoPlayOn.value = false;
  await h.player.playSong(song("a"), [song("a")], "manual");
  h.audio.finish(); await settle();
  assert.equal(h.player.playbackState.value, "ended");
  assert.deepEqual(h.events.map(({ type }) => type), ["play", "finish"]);
  h.dispose();
});

test("关闭 DJ 智能续播不影响普通下一首，但队列结束不请求推荐", async () => {
  let extensions = 0;
  const h = harness({}, { onQueueExhausted: async () => { extensions++; return []; } });
  h.player.smartContinueOn.value = false;
  const queue = [song("a"), song("b")];
  await h.player.playSong(queue[0], queue);
  h.audio.finish(); await settle();
  assert.equal(h.player.currentSong.value.id, "b");
  h.audio.finish(); await settle();
  assert.equal(extensions, 0);
  assert.equal(h.player.playbackState.value, "ended");
  assert.equal(h.storage.get("personal-radio-smart-continue"), "off");
  h.dispose();
});

test("关闭智能续播或改变模式会取消在途推荐，迟到结果不入队", async () => {
  for (const action of ["disable", "mode", "next", "move", "remove"]) {
    const pending = deferred(); let signal;
    const h = harness({}, { onQueueExhausted: (_song, nextSignal) => { signal = nextSignal; return pending.promise; } });
    await h.player.playSong(song("a"), [song("a")]); h.audio.finish();
    if (action === "disable") h.player.smartContinueOn.value = false;
    if (action === "mode") h.player.playbackMode.value = "repeat-one";
    if (action === "next") h.player.playNext(song("b"));
    if (action === "move") { h.player.appendToQueue(song("b")); h.player.reorderQueue("b", -1); }
    if (action === "remove") h.player.removeFromQueue("a");
    assert.equal(signal.aborted, true);
    pending.resolve([song("late")]); await settle();
    assert.equal(h.player.playbackQueue.value.some(({ id }) => id === "late"), false);
    h.dispose();
  }
});

test("下一首播放去重移动指定歌，随机模式下也优先且不重载当前媒体", async () => {
  const h = harness(); const queue = [song("a"), song("b"), song("c"), song("d")];
  await h.player.playSong(queue[0], queue);
  h.player.playbackMode.value = "shuffle"; h.audio.currentTime = 42;
  assert.equal(h.player.playNext(queue[2]), true);
  assert.deepEqual(Array.from(h.player.playbackQueue.value, ({ id }) => id), ["a", "c", "d", "b"]);
  assert.equal(h.player.queueSource.value, "manual");
  assert.equal(h.player.playNext(queue[0]), false);
  assert.equal(h.audio.currentTime, 42);
  assert.equal(h.calls.length, 1);
  h.audio.finish(); await settle();
  assert.equal(h.player.currentSong.value.id, "c");
  h.dispose();
});

test("移动和移除非当前歌保持真实位置、计数与正确上一首下一首边界", async () => {
  const h = harness(); const queue = [song("a"), song("b"), song("c")];
  await h.player.playSong(queue[1], queue); h.audio.currentTime = 12;
  assert.equal(h.player.reorderQueue("b", -1), true);
  assert.equal(h.player.canGoPrevious.value, false);
  assert.equal(h.player.reorderQueue("b", -1), false);
  assert.equal(h.player.removeFromQueue("a"), true);
  assert.equal(h.player.removeFromQueue("missing"), false);
  assert.equal(h.audio.currentTime, 12);
  assert.equal(h.player.isPlaying.value, true);
  assert.equal(h.calls.length, 1);
  assert.deepEqual(h.events.map(({ type }) => type), ["play"]);
  h.player.moveQueue(1); await settle();
  assert.equal(h.player.currentSong.value.id, "c");
  h.dispose();
});

test("移除当前歌停止且选中邻近歌曲，移除最后一首回到空状态", async () => {
  const h = harness(); const queue = [song("a"), song("b"), song("c")];
  await h.player.playSong(queue[1], queue); h.audio.currentTime = 13;
  h.player.removeFromQueue("b");
  assert.equal(h.player.currentSong.value.id, "c");
  assert.equal(h.audio.src, "");
  assert.equal(h.player.isPlaying.value, false);
  assert.deepEqual(h.events.map(({ id, type }) => `${id}:${type}`), ["b:play", "b:skip"]);
  h.player.togglePlayback(); await settle();
  assert.equal(h.audio.src, "https://media/c");
  h.player.removeFromQueue("a"); h.player.removeFromQueue("c");
  assert.equal(h.player.currentSong.value, null);
  assert.equal(h.player.playbackQueue.value.length, 0);
  assert.equal(h.player.canGoNext.value, false);
  h.dispose();
});

test("加载和报幕期间移除当前歌，旧异步结果不能重新开播", async () => {
  const pending = deferred(); let signal;
  const h = harness({ getPlayUrl: (_id, requestSignal) => { signal = requestSignal; return pending.promise; } });
  const attempt = h.player.playSong(song("a"), [song("a"), song("b")]); await settle();
  h.player.removeFromQueue("a");
  assert.equal(signal.aborted, true);
  pending.resolve({ success: true, url: "https://media/a" }); await attempt;
  assert.equal(h.audio.src, ""); assert.equal(h.player.currentSong.value.id, "b");
  assert.equal(h.events.length, 0);
  h.dispose();
  const voice = deferred(); const second = harness(); second.player.announceOn.value = true;
  second.voice.playImpl = () => voice.promise;
  const announcement = second.player.playSong(song("a", { dj_intro: "台词" }), [song("a")]); await settle();
  second.player.removeFromQueue("a"); voice.resolve(); await announcement;
  assert.equal(second.audio.src, ""); assert.equal(second.player.isAnnouncing.value, false);
  second.dispose();
});

test("空队列下一首入口只准备歌曲，不自动启动", async () => {
  const h = harness(); h.player.playNext(song("a"));
  assert.equal(h.player.currentSong.value.id, "a");
  assert.equal(h.player.queueSource.value, "manual");
  assert.equal(h.calls.length, 0);
  h.player.togglePlayback(); await settle();
  assert.equal(h.audio.src, "https://media/a");
  h.dispose();
});

test("旧播放地址返回或失败不能覆盖新歌曲，切歌会取消旧请求", async () => {
  for (const fail of [false, true]) {
    const pending = deferred();
    let oldSignal;
    const h = harness({ getPlayUrl: (id, signal) => {
      if (id === "a") { oldSignal = signal; return pending.promise; }
      return Promise.resolve({ success: true, url: `https://media/${id}` });
    } });
    const first = h.player.playSong(song("a"));
    await h.player.playSong(song("b"));
    assert.equal(oldSignal.aborted, true);
    if (fail) pending.reject(new Error("旧请求失败"));
    else pending.resolve({ success: true, url: "https://media/a" });
    await first;
    assert.equal(h.audio.src, "https://media/b");
    assert.equal(h.player.currentSong.value.id, "b");
    assert.equal(h.player.playbackError.value, "");
    assert.equal(h.player.isPlaying.value, true);
    h.dispose();
  }
});

test("旧 audio.play 拒绝不能清除新歌词或改变新播放状态", async () => {
  const pending = deferred();
  const h = harness({ getLyrics: async (id) => ({ lines: [{ text: id, time: 0 }] }) });
  h.audio.playImpl = () => pending.promise;
  const first = h.player.playSong(song("a"));
  await settle();
  h.audio.playImpl = null;
  await h.player.playSong(song("b"));
  pending.reject(new DOMException("src changed", "AbortError"));
  await first;
  assert.equal(h.player.playbackError.value, "");
  assert.equal(h.player.lyricLines.value[0].text, "b");
  assert.equal(h.player.isPlaying.value, true);
  h.dispose();
});

test("只有真正 playing 才记录开始，暂停恢复和缓冲不重复记账", async () => {
  const h = harness();
  h.audio.playImpl = () => Promise.resolve();
  await h.player.playSong(song("a"));
  assert.equal(h.events.length, 0);
  assert.equal(h.player.audioLoading.value, true);
  assert.equal(h.player.duration.value, 0);
  h.audio.duration = 120; h.audio.emit("loadedmetadata");
  assert.equal(h.player.duration.value, 120);
  assert.equal(h.events.length, 0);
  assert.equal(h.player.audioLoading.value, true);
  h.audio.emit("playing");
  h.audio.emit("playing"); h.audio.emit("waiting"); h.audio.emit("playing");
  h.player.togglePlayback(); h.player.togglePlayback(); h.audio.emit("playing");
  assert.deepEqual(h.events.map((event) => event.type), ["play"]);
  h.audio.currentTime = 17.5; h.audio.emit("timeupdate");
  h.advance(5000);
  assert.equal(h.player.elapsed.value, 17.5);
  assert.equal(h.player.duration.value, 120);
  h.dispose();
});

test("切歌后未就绪的旧时长事件不污染新曲，元数据到达后才更新时长", async () => {
  const h = harness();
  h.audio.playImpl = () => Promise.resolve();
  await h.player.playSong(song("a"));
  h.audio.duration = 120; h.audio.emit("loadedmetadata"); h.audio.emit("playing");
  await h.player.playSong(song("b"));
  assert.equal(h.audio.readyState, 0);
  // 更换 src 后仍可能收到排队的旧事件；绕过元数据完成事件，保留尚未就绪的状态。
  h.audio.duration = 120; h.audio.currentTime = 12;
  h.audio.emit("durationchange"); h.audio.emit("timeupdate");
  assert.equal(h.player.duration.value, 0);
  assert.equal(h.player.elapsed.value, 0);
  assert.equal(h.player.audioLoading.value, true);
  h.audio.duration = 90; h.audio.currentTime = 0; h.audio.emit("loadedmetadata");
  assert.equal(h.player.duration.value, 90);
  assert.deepEqual(h.events.map(({ id, type }) => `${id}:${type}`), ["a:play", "a:skip"]);
  h.audio.emit("playing");
  assert.deepEqual(h.events.map(({ id, type }) => `${id}:${type}`), ["a:play", "a:skip", "b:play"]);
  h.dispose();
});

test("自然结束只计一次 finish，手动队列播完不请求智能续播", async () => {
  let extensions = 0;
  const h = harness({}, { onQueueExhausted: async () => { extensions++; return [song("b")]; } });
  await h.player.playSong(song("a"), [song("a")], "manual");
  h.audio.duration = 100; h.audio.finish(); h.audio.emit("ended");
  await settle();
  assert.deepEqual(h.events.map((event) => event.type), ["play", "finish"]);
  assert.equal(extensions, 0);
  assert.equal(h.player.elapsed.value, 100);
  assert.equal(h.player.playbackState.value, "ended");
  h.dispose();
});

test("播完进入下一首，未听完切歌只记录一次 skip", async () => {
  const h = harness();
  const queue = [song("a"), song("b"), song("c")];
  await h.player.playSong(queue[0], queue, "manual");
  h.audio.duration = 100; h.audio.finish();
  await settle();
  assert.equal(h.player.currentSong.value.id, "b");
  h.audio.currentTime = 5; h.player.moveQueue(1);
  await settle();
  assert.deepEqual(h.events.map(({ id, type }) => `${id}:${type}`), ["a:play", "a:finish", "b:play", "b:skip", "c:play"]);
  h.dispose();
});

test("结束后再次播放记录 replay，重复 ended 不再记录 finish", async () => {
  const h = harness();
  await h.player.playSong(song("a"), [song("a")], "manual");
  h.audio.duration = 100; h.audio.currentTime = 100; h.audio.finish();
  h.player.togglePlayback();
  h.audio.finish(); h.audio.emit("ended");
  assert.deepEqual(h.events.map((event) => event.type), ["play", "finish", "replay", "finish"]);
  h.dispose();
});

test("智能续播返回不能抢掉新的手动歌曲或被用户修改的队列", async () => {
  for (const operation of ["play", "append", "replace", "clear", "disable"]) {
    const pending = deferred();
    let signal;
    const h = harness({}, { onQueueExhausted: (track, requestSignal) => { signal = requestSignal; return pending.promise; } });
    await h.player.playSong(song("a"), [song("a")]);
    h.audio.duration = 100; h.audio.finish();
    assert.equal(h.player.smartQueueLoading.value, true);
    if (operation === "play") await h.player.playSong(song("manual"), [song("manual")], "manual");
    if (operation === "append") h.player.appendToQueue(song("manual"));
    if (operation === "replace") h.player.prepareQueue([song("manual")], "manual");
    if (operation === "clear") h.player.clearPlayer();
    if (operation === "disable") h.player.autoPlayOn.value = false;
    assert.equal(signal.aborted, true);
    pending.resolve([song("extra")]); await settle();
    assert.equal(h.player.playbackQueue.value.some((item) => item.id === "extra"), false);
    assert.equal(h.player.smartQueueLoading.value, false);
    h.dispose();
  }
});

test("缓冲中暂停取消超时，未完成 play 被暂停拒绝不应报错", async () => {
  const pending = deferred(); const h = harness();
  h.audio.playImpl = () => pending.promise;
  const playing = h.player.playSong(song("a")); await settle();
  h.audio.emit("waiting"); h.player.togglePlayback();
  pending.reject(new DOMException("paused", "AbortError")); await playing;
  h.advance(30000);
  assert.equal(h.player.playbackState.value, "paused");
  assert.equal(h.player.audioLoading.value, false);
  assert.equal(h.player.playbackError.value, "");
  assert.equal(h.events.length, 0);
  h.audio.playImpl = null; h.player.togglePlayback(); await settle();
  assert.equal(h.player.isPlaying.value, true);
  h.dispose();
});

test("智能续播超时取消请求，迟到成功不再入队", async () => {
  const pending = deferred(); let signal;
  const h = harness({}, { onQueueExhausted: (track, requestSignal) => { signal = requestSignal; return pending.promise; } });
  await h.player.playSong(song("a"), [song("a")]); h.audio.finish();
  h.advance(25000);
  assert.equal(signal.aborted, true);
  assert.equal(h.player.smartQueueLoading.value, false);
  assert.ok(h.notices.some((message) => message.includes("续播请求超时")));
  pending.resolve([song("b")]); await settle();
  assert.equal(h.player.playbackQueue.value.length, 1);
  h.dispose();
});

test("有效智能续播会接入真实队列且不重复记账", async () => {
  const h = harness({}, { onQueueExhausted: async () => [song("a"), song("b")] });
  await h.player.playSong(song("a"), [song("a")]); h.audio.finish(); await settle();
  assert.equal(h.player.currentSong.value.id, "b");
  assert.equal(h.audio.src, "https://media/b");
  assert.equal(h.player.playbackQueue.value.length, 2);
  assert.equal(h.player.smartQueueLoading.value, false);
  assert.deepEqual(h.events.map(({ id, type }) => `${id}:${type}`), ["a:play", "a:finish", "b:play"]);
  h.dispose();
});

test("随机智能续播先排去重后的新批次，再从实际首曲接播", async () => {
  const h = harness({}, {
    initialStorage: [["personal-radio-playback-mode", "shuffle"]],
    onQueueExhausted: async () => [song("a"), song("b"), song("c"), song("d")],
  });
  await h.player.playSong(song("a", { round_queue_order_fixed: true }), [song("a", { round_queue_order_fixed: true })]);
  h.audio.finish();
  await settle();
  assert.deepEqual(Array.from(h.player.playbackQueue.value, track => track.id), ["a", "c", "d", "b"]);
  assert.equal(h.player.currentSong.value.id, "c");
  assert.equal(h.audio.src, "https://media/c");
  h.dispose();
});

test("智能续播重复 ended 只请求一次，失败后解除等待", async () => {
  const pending = deferred(); let requests = 0;
  const h = harness({}, { onQueueExhausted: () => { requests++; return pending.promise; } });
  await h.player.playSong(song("a"), [song("a")]);
  h.audio.finish(); h.audio.emit("ended");
  assert.equal(requests, 1);
  pending.reject(new Error("timeout")); await settle();
  assert.equal(h.player.smartQueueLoading.value, false);
  assert.equal(h.player.playbackState.value, "ended");
  h.dispose();
});

test("播放地址超时取消旧请求并自动重取地址", async () => {
  let attempts = 0;
  const h = harness({ getPlayUrl: (id, signal) => {
    if (++attempts > 1) return Promise.resolve({ success: true, url: `https://media/${id}` });
    return new Promise((resolve, reject) => signal.addEventListener("abort", () => reject(new DOMException("timeout", "AbortError"))));
  } });
  const first = h.player.playSong(song("a"));
  h.advance(25_000); await settle();
  assert.equal(attempts, 1);
  h.advance(600); await first;
  assert.equal(h.player.audioLoading.value, false);
  assert.equal(h.player.playbackError.value, "");
  assert.equal(h.events.length, 1);
  assert.equal(attempts, 2);
  assert.equal(h.player.isPlaying.value, true);
  h.dispose();
});

test("取址连续失败时两次递延重试，最后保留请求编号并可手动重试", async () => {
  let attempts = 0;
  const h = harness({ getPlayUrl: async () => {
    if (++attempts <= 3) throw new ApiError("网易云请求失败，请稍后再试（请求 0123456789abcdef · gateway_http）", 502);
    return { success: true, url: "https://media/ok" };
  } });
  const starting = h.player.playSong(song("a"));
  await settle();
  assert.equal(h.player.audioLoading.value, true);
  assert.equal(h.player.playbackError.value, "");
  assert.match(h.notices.at(-1), /^取播放地址失败.*1\/2/);
  h.advance(599); await settle();
  assert.equal(attempts, 1);
  h.advance(1); await settle();
  assert.equal(attempts, 2);
  assert.match(h.notices.at(-1), /^取播放地址失败.*2\/2/);
  h.advance(1_499); await settle();
  assert.equal(attempts, 2);
  h.advance(1); await starting;
  assert.equal(attempts, 3);
  assert.equal(h.player.audioLoading.value, false);
  assert.match(h.player.playbackError.value, /^取播放地址失败。网易云请求失败.*0123456789abcdef.*gateway_http/);
  assert.equal(h.events.length, 0);
  h.player.togglePlayback(); await settle();
  assert.equal(attempts, 4);
  assert.equal(h.player.playbackError.value, "");
  assert.equal(h.player.isPlaying.value, true);
  h.dispose();
});

test("收到长等待建议后，最后一次重试能在同曲恢复且无需再次按播放", async () => {
  let attempts = 0;
  const h = harness({ getPlayUrl: async () => {
    if (++attempts <= 2) throw new ApiError("网易云请求失败，请稍后再试", 502, 12_000);
    return { success: true, url: "https://media/ok" };
  } });
  const starting = h.player.playSong(song("a"));
  await settle();
  h.advance(600); await settle();
  assert.equal(attempts, 2);
  assert.equal(h.player.audioLoading.value, true);
  assert.match(h.notices.at(-1), /12 秒后会自动再试/);
  h.advance(11_999); await settle();
  assert.equal(attempts, 2);
  assert.equal(h.player.isPlaying.value, false);
  h.advance(1); await starting;
  assert.equal(attempts, 3);
  assert.equal(h.player.isPlaying.value, true);
  assert.equal(h.player.playbackError.value, "");
  assert.deepEqual(h.events.map(({ id, type }) => [id, type]), [["a", "play"]]);
  h.dispose();
});

test("暂停、切歌、清空和卸载取消尚未发出的取址重试", async () => {
  for (const action of ["pause", "next", "clear", "dispose"]) {
    const calls = [];
    const h = harness({ getPlayUrl: async (id) => {
      calls.push(id);
      if (id === "a") throw new ApiError("网易云请求失败，请稍后再试", 502, 12_000);
      return { success: true, url: "https://media/b" };
    } });
    const queue = [song("a"), song("b")];
    const starting = h.player.playSong(queue[0], queue);
    await settle();
    h.advance(600); await settle();
    assert.equal(h.player.audioLoading.value, true);
    if (action === "pause") h.player.togglePlayback();
    if (action === "next") h.player.moveQueue(1);
    if (action === "clear") h.player.clearPlayer();
    if (action === "dispose") h.dispose();
    h.advance(15_000); await starting; await settle();
    assert.deepEqual(calls, action === "next" ? ["a", "a", "b"] : ["a", "a"]);
    assert.equal(h.audio.src, action === "next" ? "https://media/b" : "");
    assert.equal(h.player.playbackError.value, "");
    if (action === "pause") assert.equal(h.player.playbackState.value, "paused");
    if (action !== "dispose") h.dispose();
  }
});

test("首播取址暂时失败后，恢复音乐仍可按原计划播放一次 DJ 开场", async () => {
  let attempts = 0;
  const h = harness({ getPlayUrl: async () => {
    if (++attempts === 1) throw new ApiError("网易云请求失败，请稍后再试", 502);
    return { success: true, url: "https://media/a" };
  } });
  h.player.announceOn.value = true;
  const starting = h.player.playSong(segmentQueue()[0]);
  await settle();
  assert.equal(h.voice.src, "");
  h.advance(600); await settle();
  assert.equal(h.player.isPlaying.value, true);
  assert.equal(h.player.isDjOnAir.value, true);
  h.voice.finish(); await starting;
  assert.equal(attempts, 2);
  assert.deepEqual(h.events.map(event => event.type), ["play"]);
  h.dispose();
});

test("音频加载最多重连两次，耗尽后允许切歌", async () => {
  const h = harness();
  h.audio.playImpl = () => Promise.resolve();
  await h.player.playSong(song("a"), [song("a"), song("b")]);
  for (let attempt = 0; attempt < 3; attempt++) { h.advance(25000); await settle(); }
  assert.equal(h.player.audioLoading.value, false);
  assert.equal(h.player.isPlaying.value, false);
  assert.match(h.player.playbackError.value, /^歌曲音频流加载超时/);
  h.audio.playImpl = null;
  h.player.moveQueue(1); await settle();
  assert.equal(h.player.currentSong.value.id, "b");
  assert.equal(h.player.playbackError.value, "");
  h.audio.emit("waiting"); h.advance(25000); await settle();
  assert.equal(h.player.playbackState.value, "playing");
  h.dispose();
});

test("无地址和无登录权限解除加载并保持可恢复状态", async () => {
  const h = harness({ getPlayUrl: async () => ({ success: false, need_login: true, message: "请登录网易云" }) });
  await h.player.playSong(song("a"));
  assert.equal(h.player.playbackState.value, "unavailable");
  assert.equal(h.player.audioLoading.value, false);
  assert.equal(h.events.length, 0);
  assert.ok(h.notices.includes("login"));
  h.dispose();
});

test("网关登录失效保留明确原因，不自动重试并提示重新登录", async () => {
  let calls = 0;
  const h = harness({ getPlayUrl: async () => { calls++; throw new ApiError("网易云登录状态已失效，请重新扫码", 502); } });
  await h.player.playSong(song("a"));
  assert.equal(calls, 1);
  assert.equal(h.player.playbackState.value, "unavailable");
  assert.match(h.player.playbackError.value, /登录状态已失效/);
  assert.ok(h.notices.includes("login"));
  h.dispose();
});

test("本机凭证错误不误弹网易云登录", async () => {
  const h = harness({ getPlayUrl: async () => { throw new ApiError("本机访问凭证已失效，请刷新页面", 401); } });
  await h.player.playSong(song("a"));
  assert.match(h.player.playbackError.value, /本机访问凭证/);
  assert.equal(h.notices.includes("login"), false);
  h.dispose();
});

test("浏览器阻止自动播放后可手动恢复且只记录一次开始", async () => {
  const h = harness();
  h.audio.playImpl = () => Promise.reject(new DOMException("blocked", "NotAllowedError"));
  await h.player.playSong(song("a"));
  assert.equal(h.player.playbackState.value, "blocked");
  assert.equal(h.events.length, 0);
  h.audio.playImpl = null; h.player.togglePlayback(); await settle();
  assert.equal(h.player.playbackError.value, "");
  assert.deepEqual(h.events.map((event) => event.type), ["play"]);
  h.dispose();
});

test("普通流错误先自动重取地址，两次耗尽后允许手动重试", async () => {
  const h = harness();
  await h.player.playSong(song("a"));
  // 普通通道已经排除频谱/CORS因素，接下来只能有限刷新合法播放地址。
  h.analysis.fallback();
  for (let attempt = 0; attempt < 3; attempt++) {
    h.audio.error = { code: 2 }; h.audio.emit("error"); await settle();
  }
  assert.equal(h.player.isPlaying.value, false);
  assert.equal(h.player.playbackState.value, "error");
  h.player.togglePlayback(); await settle();
  assert.equal(h.calls.length, 4);
  assert.equal(h.player.isPlaying.value, true);
  assert.equal(h.events.filter((event) => event.type === "skip").every(event => event.context.interrupted), true);
  h.dispose();
});

test("分析失败重建普通媒体，恢复真实位置且同曲开始不重复记账", async () => {
  const h = harness();
  await h.player.playSong(song("a"), [song("a"), song("b")], "manual");
  const old = h.audio;
  old.currentTime = 35;
  h.analysis.fallback();
  assert.notEqual(h.audio, old);
  assert.equal(old.src, "");
  h.audio.duration = 120; h.audio.emit("loadedmetadata"); h.audio.emit("timeupdate");
  assert.equal(h.player.elapsed.value, 35);
  assert.deepEqual(h.events.map(e => e.type), ["play"]);
  old.finish(); old.emit("error");
  assert.equal(h.player.currentSong.value.id, "a");
  assert.equal(h.player.playbackError.value, "");
  h.audio.finish(); await settle();
  assert.equal(h.player.currentSong.value.id, "b");
  h.dispose();
});

test("CORS 只降级一次普通通道，之后地址重连也有次数上限", async () => {
  const h = harness(); await h.player.playSong(song("a"));
  h.audio.error = { code: 4 }; h.audio.emit("error");
  assert.equal(h.audios.length, 3);
  assert.equal(h.player.isPlaying.value, true);
  for (let attempt = 0; attempt < 3; attempt++) {
    h.audio.error = { code: 4 }; h.audio.emit("error"); await settle();
  }
  assert.equal(h.audios.length, 3);
  assert.equal(h.player.playbackState.value, "error");
  h.dispose();
});

test("CORS play 拒绝先于 error 事件也能恢复，迟到 metadata 不跳新歌曲位置", async () => {
  const h = harness(); h.audio.playImpl = () => {
    h.audio.error = { code: 4 };
    return Promise.reject(new DOMException("CORS", "NotSupportedError"));
  };
  await h.player.playSong(song("a"));
  assert.equal(h.player.isPlaying.value, true); assert.equal(h.player.playbackError.value, "");
  await h.player.playSong(song("b"));
  h.audio.duration = 120; h.audio.currentTime = 3; h.audio.emit("loadedmetadata");
  assert.equal(h.audio.currentTime, 3);
  h.dispose();
});

test("播放地址尚未返回时退出分析不使当前播放请求失效", async () => {
  const pending = deferred(); const h = harness({ getPlayUrl: () => pending.promise });
  const playing = h.player.playSong(song("a"));
  h.analysis.fallback();
  pending.resolve({ success: true, url: "https://media/a" }); await playing;
  assert.equal(h.player.isPlaying.value, true);
  assert.deepEqual(h.events.map(e => e.type), ["play"]);
  h.dispose();
});

test("后台覆盖查询取消和超时后迟到结果不能再获取音源", async () => {
  for (const operation of ["play", "timeout", "disable", "dispose"]) {
    const pending = deferred(); let signal;
    const h = harness({ getPendingAudioFeatures: (_ids, incoming) => { signal = incoming; return pending.promise; } });
    await h.player.playSong(song("a"), [song("a"), song("b")]); h.player.acousticBackfillOn.value = true;
    h.player.togglePlayback(); h.advance(20000); await settle();
    assert.ok(signal);
    if (operation === "play") h.player.togglePlayback();
    if (operation === "timeout") h.advance(10000);
    if (operation === "disable") h.player.acousticBackfillOn.value = false;
    if (operation === "dispose") h.dispose();
    assert.equal(signal.aborted, true);
    pending.resolve(["b"]); await settle();
    assert.equal(h.calls.some(call => call.id === "b"), false);
    if (operation !== "dispose") h.dispose();
  }
});

test("后台播放地址请求在前台开始时取消，不进入背景媒体采样", async () => {
  const pending = deferred(); let signal; let captures = 0;
  const h = harness({ getPendingAudioFeatures: async () => ["b"], getPlayUrl: async (id, incoming) => {
    if (id === "b") { signal = incoming; return pending.promise; }
    return { success: true, url: `https://media/${id}` };
  } });
  h.analysis.analyzeUrlInBackground = async () => { captures++; return null; };
  await h.player.playSong(song("a"), [song("a"), song("b")]); h.player.acousticBackfillOn.value = true;
  h.player.togglePlayback(); h.advance(20000); await settle();
  await h.player.playSong(song("c"), [song("c")], "manual");
  assert.equal(signal.aborted, true);
  pending.resolve({ success: true, url: "https://media/b" }); await settle();
  assert.equal(captures, 0); assert.equal(h.player.currentSong.value.id, "c"); h.dispose();
});

test("旧媒体 play 拒绝不覆盖普通播放恢复，暂停时恢复不会擅自开始", async () => {
  const pending = deferred(); const h = harness(); h.audio.playImpl = () => pending.promise;
  const playing = h.player.playSong(song("a")); await settle();
  h.analysis.fallback(); pending.reject(new Error("旧媒体关闭")); await playing;
  assert.equal(h.player.isPlaying.value, true); assert.equal(h.player.playbackError.value, "");
  h.dispose();
  const paused = harness(); await paused.player.playSong(song("b")); paused.player.togglePlayback();
  paused.analysis.fallback();
  assert.equal(paused.audio.paused, true); assert.equal(paused.player.isPlaying.value, false);
  paused.dispose();
});

test("恢复同一首歌不丢失在途歌词，换歌仍会丢弃旧歌词", async () => {
  const pending = deferred(); const h = harness({ getLyrics: () => pending.promise });
  await h.player.playSong(song("a"));
  h.player.togglePlayback(); h.player.togglePlayback(); await settle();
  pending.resolve({ lines: [{ time: 0, text: "当前歌歌词" }] }); await settle();
  assert.equal(h.player.lyricLines.value[0].text, "当前歌歌词");
  h.dispose();
});

test("缓冲事件迟到但实际进度仍前进时，不得切断音乐或刷新地址", async () => {
  const h = harness(); await h.player.playSong(song("a"));
  h.audio.emit("waiting");
  h.audio.currentTime = 12;
  h.advance(25_000);
  assert.equal(h.player.playbackState.value, "playing");
  assert.equal(h.calls.length, 1);
  assert.equal(h.player.playbackError.value, "");
  h.dispose();
});

test("断流重取地址后恢复同曲位置，不重复报幕或记录开始", async () => {
  const h = harness(); await h.player.playSong(song("a"));
  h.analysis.fallback(); await settle();
  h.audio.currentTime = 42;
  h.audio.playImpl = () => {
    h.audio.duration = 120; h.audio.readyState = 4;
    h.audio.emit("loadedmetadata"); h.audio.emit("timeupdate"); h.audio.emit("playing");
    return Promise.resolve();
  };
  h.audio.error = { code: 2 }; h.audio.emit("error"); await settle();
  assert.equal(h.audio.currentTime, 42);
  assert.equal(h.player.elapsed.value, 42);
  assert.equal(h.calls.length, 2);
  assert.deepEqual(h.events.map((event) => event.type), ["play", "skip"]);
  assert.equal(h.voice.src, "");
  assert.equal(h.events.at(-1).context.interrupted, true);
  h.dispose();
});

test("断流后的取址失败有独立提示，延时重试仍恢复同一播放的真实位置", async () => {
  let attempts = 0;
  const h = harness({ getPlayUrl: async () => {
    if (++attempts === 2) throw new ApiError("网易云请求失败，请稍后再试", 502);
    return { success: true, url: `https://media/${attempts}` };
  } });
  await h.player.playSong(song("a"));
  h.analysis.fallback(); await settle();
  h.audio.currentTime = 42;
  h.audio.playImpl = () => {
    h.audio.duration = 120; h.audio.readyState = 4;
    h.audio.emit("loadedmetadata"); h.audio.emit("timeupdate"); h.audio.emit("playing");
    return Promise.resolve();
  };
  h.audio.error = { code: 2 }; h.audio.emit("error"); await settle();
  // 原生 pause 可能在新请求发出后才到，不能把重试等待误标为用户暂停。
  h.audio.emit("pause");
  assert.equal(h.player.playbackState.value, "loading");
  assert.match(h.notices.at(-2), /^歌曲音频流中断/);
  assert.match(h.notices.at(-1), /^取播放地址失败/);
  h.advance(1_499); await settle();
  assert.equal(attempts, 2);
  h.advance(1); await settle();
  assert.equal(attempts, 3);
  assert.equal(h.audio.currentTime, 42);
  assert.equal(h.player.elapsed.value, 42);
  assert.equal(h.player.isPlaying.value, true);
  assert.deepEqual(h.events.map(event => event.type), ["play", "skip"]);
  assert.equal(h.events[0].context.playback_id, h.events[1].context.playback_id);
  h.dispose();
});

test("用户暂停在途重连后，迟到地址不能重新开播", async () => {
  const pending = deferred(); let attempts = 0;
  const h = harness({ getPlayUrl: (id, signal) => {
    h.calls.push({ id, signal });
    return ++attempts === 2 ? pending.promise : Promise.resolve({ success: true, url: `https://media/${id}` });
  } });
  await h.player.playSong(song("a")); h.analysis.fallback(); await settle();
  h.audio.error = { code: 2 }; h.audio.emit("error");
  h.player.togglePlayback();
  assert.equal(h.calls[1].signal.aborted, true);
  pending.resolve({ success: true, url: "https://media/late" }); await settle();
  assert.equal(h.player.playbackState.value, "paused");
  assert.equal(h.player.isPlaying.value, false);
  assert.notEqual(h.audio.src, "https://media/late");
  h.dispose();
});

test("五秒尚未出声的报幕继续等待，真正开始前不消耗轮次", async () => {
  const h = harness(); h.player.announceOn.value = true;
  h.voice.playImpl = () => new Promise(() => {});
  const playing = h.player.playSong(song("a", { batch_id: "slow-voice", round_intro: "整轮开场" }));
  h.advance(5_000); await settle();
  assert.equal(h.player.isAnnouncing.value, true);
  assert.equal(h.storage.has("personal-radio-announced-batches"), false);
  h.voice.emit("playing"); h.voice.finish(); await playing;
  assert.match(h.storage.get("personal-radio-announced-batches"), /slow-voice/);
  h.dispose();
});

test("完全失败的语音保留重试资格，下一次真实开始才持久化去重", async () => {
  const h = harness(); h.player.announceOn.value = true;
  const track = song("a", { batch_id: "retry-voice", round_intro: "整轮开场" });
  h.voice.playImpl = () => Promise.reject(new Error("网络不可用"));
  await h.player.playSong(track);
  assert.equal(h.storage.has("personal-radio-announced-batches"), false);
  assert.ok(h.notices.some((message) => /未找到本机中文语音/.test(message)));
  h.voice.playImpl = null;
  const replay = h.player.playSong(track); await settle(); h.voice.finish(); await replay;
  assert.match(h.storage.get("personal-radio-announced-batches"), /retry-voice/);
  h.dispose();
});

test("网络语音未出声时降级本机中文语音，跳过会取消本机人声", async () => {
  const spoken = []; let cancelled = 0;
  const h = harness({}, { browser: {
    SpeechSynthesisUtterance: class { constructor(text) { this.text = text; } },
    speechSynthesis: {
      getVoices: () => [{ lang: "zh-CN", localService: true }], resume() {},
      speak(utterance) { spoken.push(utterance); utterance.onstart(); },
      cancel() { cancelled++; },
    },
  } });
  h.player.announceOn.value = true;
  h.voice.playImpl = () => Promise.reject(new Error("网络不可用"));
  const playing = h.player.playSong(song("a", { batch_id: "local-voice", round_intro: "整轮开场" }));
  await settle();
  assert.equal(spoken.length, 1);
  assert.equal(spoken[0].volume, .85);
  assert.match(h.storage.get("personal-radio-announced-batches"), /local-voice/);
  h.player.skipAnnouncement(); await playing;
  assert.equal(cancelled, 1);
  assert.equal(h.player.isPlaying.value, true);
  assert.equal(spoken[0].onend, null);
  h.dispose();
});

test("试听声音独立于报幕开关，暂停后原进度继续且不消费轮次", async () => {
  const h = harness(); await h.player.playSong(song("a"), [song("a")], "manual");
  h.audio.currentTime = 22;
  const preview = h.player.previewAnnouncement(); await settle();
  assert.equal(h.audio.paused, true);
  assert.equal(h.player.isAnnouncing.value, true);
  h.voice.finish(); await preview;
  assert.equal(h.audio.currentTime, 22);
  assert.equal(h.player.isPlaying.value, true);
  assert.equal(h.player.announceOn.value, false);
  assert.equal(h.storage.has("personal-radio-announced-batches"), false);
  assert.deepEqual(h.events.map((event) => event.type), ["play"]);
  h.dispose();
});

test("报幕 play 一直未完成也会超时降级，快速结束报幕不会丢回调", async () => {
  for (const immediate of [false, true]) {
    const h = harness();
    h.player.announceOn.value = true;
    h.voice.playImpl = () => {
      if (immediate) { h.voice.finish(); return Promise.resolve(); }
      return new Promise(() => {});
    };
    const playing = h.player.playSong(song("a", { dj_intro: "测试报幕" }));
    await settle();
    if (!immediate) h.advance(15000);
    await playing;
    assert.equal(h.player.isAnnouncing.value, false);
    assert.equal(h.audio.src, "https://media/a");
    assert.equal(h.player.isPlaying.value, true);
    h.dispose();
  }
});

test("旧报幕拒绝不能取消新报幕，跳过后播放当前歌曲", async () => {
  const pending = deferred(); const h = harness();
  h.player.announceOn.value = true;
  h.voice.playImpl = () => pending.promise;
  const first = h.player.playSong(song("a", { dj_intro: "旧报幕" }));
  await settle();
  h.voice.playImpl = () => Promise.resolve();
  const second = h.player.playSong(song("b", { dj_intro: "新报幕" }));
  pending.reject(new Error("旧报幕已取消")); await settle();
  assert.equal(h.player.isAnnouncing.value, true);
  h.player.skipAnnouncement(); await Promise.all([first, second]);
  assert.equal(h.audio.src, "https://media/b");
  assert.equal(h.player.isAnnouncing.value, false);
  h.dispose();
});

test("卸载或清空后旧异步回调不能重启播放器", async () => {
  for (const operation of ["dispose", "clear"]) {
    const pending = deferred(); const h = harness({ getPlayUrl: () => pending.promise });
    const playing = h.player.playSong(song("a"));
    if (operation === "dispose") h.dispose(); else h.player.clearPlayer();
    pending.resolve({ success: true, url: "https://media/a" }); await playing;
    assert.equal(h.audio.src, "");
    assert.equal(h.events.length, 0);
    if (operation === "clear") h.dispose();
  }
});

function segmentQueue() {
  const ids = ['seg-a', 'seg-b', 'seg-c'];
  const segments = ids.map((id, position) => ({ id: 'part-' + position, before_song_id: id,
    after_song_id: position ? ids[position - 1] : null, position, text: '自由正文' + position, sources: [] }));
  return ids.map((id) => song(id, { batch_id: 'segments', round_program_version: 'dj-segments-v1', round_segments: segments }));
}

test('准备推荐队列后点击底部播放，也通过节目入口开始第一段', async () => {
  const texts = [];
  const h = harness({ getTtsUrl: (text) => { texts.push(text); return 'https://media/voice'; } },
    { initialStorage: [['personal-radio-announce-on', 'on']] });
  h.player.prepareQueue(segmentQueue(), 'recommendation');
  h.player.togglePlayback(); await settle();
  assert.deepEqual(texts, ['自由正文0']);
  h.voice.finish(); await settle(); assert.equal(h.player.isAnnouncing.value, false);
  h.dispose();
});

test('最多三段在自然曲间分别播出，暂停恢复和同批回放不重复', async () => {
  const texts = [];
  const h = harness({ getTtsUrl: (text) => { texts.push(text); return 'https://media/voice'; } },
    { initialStorage: [['personal-radio-announce-on', 'on']] });
  const queue = segmentQueue();
  const starting = h.player.playSong(queue[0], queue, 'recommendation'); await settle();
  assert.deepEqual(texts, ['自由正文0']);
  h.voice.finish(); await starting;
  h.player.togglePlayback(); h.player.togglePlayback(); await settle();
  assert.equal(texts.length, 1);
  h.audio.finish(); await settle();
  assert.deepEqual(texts, ['自由正文0', '自由正文1']);
  h.voice.finish(); await settle(); h.audio.finish(); await settle();
  assert.deepEqual(texts, ['自由正文0', '自由正文1', '自由正文2']);
  h.voice.finish(); await settle();
  await h.player.playSong(queue[0], queue, 'recommendation');
  assert.equal(texts.length, 3); h.dispose();
});

test('手动跳过曲间节点时不补讲，跳过进行中的节目不留下迟到人声', async () => {
  const texts = [];
  const h = harness({ getTtsUrl: (text) => { texts.push(text); return 'https://media/voice'; } },
    { initialStorage: [['personal-radio-announce-on', 'on']] });
  const queue = segmentQueue();
  const starting = h.player.playSong(queue[0], queue, 'recommendation'); await settle();
  h.player.skipAnnouncement(); await starting;
  h.player.moveQueue(1); await settle();
  assert.deepEqual(texts, ['自由正文0']);
  assert.equal(h.player.currentSong.value.id, 'seg-b');
  assert.equal(h.player.isAnnouncing.value, false); h.dispose();
});

test('手动切歌停掉旧声并播目标曲的独立介绍，跳过 DJ 不会停音乐', async () => {
  const texts = [];
  const h = harness({ getTtsUrl: text => { texts.push(text); return 'https://media/voice'; } },
    { initialStorage: [['personal-radio-announce-on', 'on']] });
  const queue = openingQueue();
  queue[0].round_segments[2].standalone = true;
  const first = h.player.playSong(queue[0], queue, 'recommendation');
  await settle();
  assert.deepEqual(texts, ['自由正文0']);
  const target = h.player.playSong(queue[2], queue, 'recommendation');
  await settle();
  await first;
  assert.equal(h.audio.src, 'https://media/seg-c');
  assert.equal(h.audio.paused, false);
  assert.deepEqual(texts, ['自由正文0', '自由正文2']);
  h.player.skipAnnouncement();
  await target;
  assert.equal(h.audio.paused, false);
  assert.equal(h.voice.paused, true);
  await h.player.playSong(queue[2], queue, 'recommendation');
  assert.equal(texts.length, 2);
  h.dispose();
});

test('手动跳到承接上一首的段落后，正确的自然切歌仍能播它', async () => {
  const texts = [];
  const h = harness({ getTtsUrl: text => { texts.push(text); return 'https://media/voice'; } },
    { initialStorage: [['personal-radio-announce-on', 'on']] });
  const queue = openingQueue();
  const first = h.player.playSong(queue[0], queue, 'recommendation');
  await settle();
  h.voice.finish(); await first;
  await h.player.playSong(queue[2], queue, 'recommendation');
  assert.deepEqual(texts, ['自由正文0']);
  await h.player.playSong(queue[1], queue, 'recommendation');
  h.audio.finish(); await settle();
  assert.deepEqual(texts, ['自由正文0', '自由正文2']);
  h.voice.finish(); await settle(); h.dispose();
});

test('节目晚到只在后续自然曲间使用，不打断当前歌曲', async () => {
  const texts = [];
  const h = harness({ getTtsUrl: (text) => { texts.push(text); return 'https://media/voice'; } },
    { initialStorage: [['personal-radio-announce-on', 'on']] });
  const queue = segmentQueue(); const ready = queue[0].round_segments;
  for (const item of queue) item.round_segments = [];
  await h.player.playSong(queue[0], queue, 'recommendation');
  for (const item of queue) item.round_segments = ready;
  await settle(); assert.equal(texts.length, 0);
  h.audio.finish(); await settle();
  assert.deepEqual(texts, ['自由正文1']);
  h.player.skipAnnouncement(); await settle(); h.dispose();
});

test('随机顺序不符时略过依赖原前曲的节目，手动队列保持不报幕', async () => {
  const texts = [];
  const h = harness({ getTtsUrl: (text) => { texts.push(text); return 'https://media/voice'; } },
    { initialStorage: [['personal-radio-announce-on', 'on']] });
  const queue = segmentQueue();
  await h.player.playSong(queue[2], queue, 'recommendation');
  await h.player.playSong(queue[1], queue, 'recommendation', 'auto_next');
  await h.player.playSong(queue[0], queue, 'manual');
  assert.equal(texts.length, 0); h.dispose();
});

test('随机播放沿用 DJ 已确认的 A→C→B，两个自然曲间都按顺序播报', async () => {
  const texts = [];
  const h = harness({ getTtsUrl: text => { texts.push(text); return 'https://media/voice'; } },
    { initialStorage: [['personal-radio-announce-on', 'on']] });
  const [a, b, c] = segmentQueue();
  const segments = [
    { ...a.round_segments[1], id: 'a-c', before_song_id: c.id, after_song_id: a.id, position: 1, text: '从 A 接到 C' },
    { ...a.round_segments[1], id: 'c-b', before_song_id: b.id, after_song_id: c.id, position: 2, text: '从 C 接到 B' },
  ];
  const queue = [a, c, b].map(item => ({ ...item, round_segments: segments, round_queue_order_fixed: true }));
  await h.player.playSong(queue[0], queue, 'recommendation');
  h.player.playbackMode.value = 'shuffle';
  assert.deepEqual(Array.from(h.player.playbackQueue.value, item => item.id), queue.map(item => item.id));
  h.audio.finish(); await settle();
  assert.equal(h.player.currentSong.value.id, c.id);
  assert.deepEqual(texts, ['从 A 接到 C']);
  h.voice.finish(); await settle();
  h.audio.finish(); await settle();
  assert.equal(h.player.currentSong.value.id, b.id);
  assert.deepEqual(texts, ['从 A 接到 C', '从 C 接到 B']);
  h.voice.finish(); await settle(); h.dispose();
});

test('段落去重跨播放器重建保留，不把整个批次错误标成全部已播', async () => {
  const texts = [];
  const first = harness({ getTtsUrl: (text) => { texts.push(text); return 'https://media/voice'; } },
    { initialStorage: [['personal-radio-announce-on', 'on']] });
  const queue = segmentQueue();
  const playing = first.player.playSong(queue[0], queue, 'recommendation'); await settle();
  first.voice.finish(); await playing;
  const stored = first.storage.get('personal-radio-announced-segments'); first.dispose();
  const second = harness({ getTtsUrl: (text) => { texts.push(text); return 'https://media/voice'; } },
    { initialStorage: [['personal-radio-announce-on', 'on'], ['personal-radio-announced-segments', stored]] });
  await second.player.playSong(queue[0], queue, 'recommendation');
  second.audio.finish(); await settle();
  assert.deepEqual(texts, ['自由正文0','自由正文1']);
  second.player.skipAnnouncement(); await settle(); second.dispose();
});

test('音乐先于TTS起播，只有真实人声开始才平滑压低，结束恢复最新音量', async () => {
  const order=[];
  const h=harness({ getTtsUrl: () => { order.push('tts'); return 'https://media/voice'; } });
  h.audio.playImpl=() => { order.push('music'); h.audio.readyState=4; h.audio.emit('playing'); return Promise.resolve(); };
  h.voice.playImpl=() => Promise.resolve();
  h.player.announceOn.value=true;
  const starting=h.player.playSong(segmentQueue()[0]); await settle();
  assert.deepEqual(order,['music','tts']);
  assert.equal(h.audio.paused,false); assert.equal(h.voice.paused,false);
  assert.equal(h.player.playbackState.value,'playing');
  h.advance(500); assert.equal(h.audio.volume,.8);
  h.voice.emit('playing'); h.advance(125);
  assert.ok(h.audio.volume>.2 && h.audio.volume<.8);
  h.advance(125); assert.ok(Math.abs(h.audio.volume-.2)<1e-9);
  h.player.volume.value=.4; assert.ok(Math.abs(h.audio.volume-.1)<1e-9);
  h.voice.finish(); await starting; h.advance(450);
  assert.equal(h.audio.volume,.4); assert.equal(h.player.volume.value,.4);
  assert.equal(h.player.isPlaying.value,true); h.dispose();
});

test("DJ 音色选择会记住并用于下一次试听和自动报幕，切换继续放歌", async () => {
  const voices = [];
  const getTtsUrl = (_text, voice) => { voices.push(voice); return "https://media/voice"; };
  const h = harness({ getTtsUrl }, { initialStorage: [["personal-radio-dj-voice", "invalid"]] });
  assert.equal(h.player.djVoice.value, "zh-CN-XiaoxiaoNeural");
  const chosen = "zh-CN-YunxiNeural";
  h.player.djVoice.value = chosen;
  const preview = h.player.previewAnnouncement();
  await settle();
  h.voice.finish();
  await preview;
  assert.deepEqual(voices, [chosen]);
  const saved = h.storage.get("personal-radio-dj-voice");
  h.dispose();

  const reopened = harness({ getTtsUrl }, { initialStorage: [["personal-radio-dj-voice", saved]] });
  reopened.player.announceOn.value = true;
  const starting = reopened.player.playSong(segmentQueue()[0]);
  await settle();
  assert.deepEqual(voices, [chosen, chosen]);
  reopened.player.djVoice.value = "zh-CN-XiaoyiNeural";
  assert.equal(reopened.audio.paused, false);
  assert.equal(reopened.voice.paused, false);
  assert.deepEqual(voices, [chosen, chosen]);
  reopened.voice.finish();
  await starting;
  reopened.dispose();
});

test('播报中暂停同时停两路，长暂停不超时，继续原人声且不重记播放', async () => {
  let tts=0;
  const h=harness({getTtsUrl:()=>{tts++; return 'https://media/voice';}});
  h.player.announceOn.value=true;
  const starting=h.player.playSong(segmentQueue()[0]); await settle(); h.advance(250);
  h.audio.currentTime=6; h.voice.currentTime=2;
  const playback=h.events[0].context.playback_id;
  h.player.togglePlayback();
  assert.equal(h.audio.paused,true); assert.equal(h.voice.paused,true);
  assert.equal(h.player.playbackState.value,'paused');
  assert.equal(h.player.playerStatus.value,'STANDBY'); assert.equal(h.player.isDjOnAir.value,false);
  h.advance(120_000); await settle();
  assert.equal(h.player.isAnnouncing.value,true); assert.equal(h.audio.volume,.8);
  h.player.togglePlayback(); await settle(); h.advance(250);
  assert.equal(h.audio.paused,false); assert.equal(h.voice.paused,false);
  assert.equal(h.audio.currentTime,6); assert.equal(h.voice.currentTime,2);
  assert.equal(tts,1); assert.equal(h.events.length,1); assert.equal(h.events[0].context.playback_id,playback);
  assert.ok(Math.abs(h.audio.volume-.2)<1e-9);
  h.voice.finish(); await starting; h.dispose();
});

test('暂停时迟到的TTS握手只准备地址，恢复后才出声且不再请求文案', async () => {
  const pending=deferred(); let tts=0;
  const h=harness({renewLocalAccess:()=>pending.promise,getTtsUrl:()=>{tts++;return 'https://media/voice';}});
  h.player.announceOn.value=true;
  const starting=h.player.playSong(segmentQueue()[0]); await settle();
  h.player.togglePlayback(); pending.resolve({ok:true}); await settle();
  assert.equal(h.voice.paused,true); assert.equal(h.player.isAnnouncing.value,true);
  assert.equal(h.storage.has('personal-radio-announced-segments'),false);
  h.player.togglePlayback(); await settle();
  assert.equal(h.voice.paused,false); assert.equal(tts,1);
  assert.match(h.storage.get('personal-radio-announced-segments'),/part-0/);
  h.voice.finish(); await starting; h.dispose();
});

test('跳过只停DJ并恢复音乐，不重取歌曲地址或制造跳过记录', async () => {
  const h=harness(); h.player.announceOn.value=true;
  const starting=h.player.playSong(segmentQueue()[0]); await settle(); h.advance(250);
  h.audio.currentTime=12; h.player.skipAnnouncement(); await starting; h.advance(450);
  assert.equal(h.voice.src,''); assert.equal(h.audio.paused,false); assert.equal(h.audio.currentTime,12);
  assert.equal(h.audio.volume,.8); assert.equal(h.calls.length,1);
  assert.deepEqual(h.events.map(e=>e.type),['play']); h.dispose();
});

test('切歌取消未就绪的旧人声，迟到握手不串到新歌，音量最终恢复', async () => {
  const pending=deferred(); let tts=0;
  const h=harness({renewLocalAccess:()=>pending.promise,getTtsUrl:()=>{tts++;return 'https://media/voice';}});
  h.player.announceOn.value=true;
  const starting=h.player.playSong(segmentQueue()[0]); await settle();
  await h.player.playSong(song('new'),[song('new')],'manual');
  pending.resolve({ok:true}); await starting; await settle(); h.advance(450);
  assert.equal(tts,0); assert.equal(h.voice.src,''); assert.equal(h.audio.src,'https://media/new');
  assert.equal(h.audio.volume,.8); assert.equal(h.events.filter(e=>e.type==='skip').length,1); h.dispose();
});

test('歌曲权限失败或自动播放被拒绝时不发起DJ语音', async () => {
  for(const blocked of [false,true]) {
    let tts=0;
    const h=harness({...(blocked?{}:{getPlayUrl:async()=>({success:false,message:'无播放权限'})}), getTtsUrl:()=>{tts++;return 'https://media/voice';}});
    if(blocked) h.audio.playImpl=()=>Promise.reject(new DOMException('blocked','NotAllowedError'));
    h.player.announceOn.value=true;
    const original=h.audio;
    await h.player.playSong(segmentQueue()[0]);
    assert.equal(h.voice.src,''); assert.equal(tts,0); assert.equal(h.player.isAnnouncing.value,false);
    assert.equal(original.volume,.8); h.dispose();
  }
});

test('音乐缓冲暂停DJ，重新playing后继续同一人声', async () => {
  const h=harness(); h.player.announceOn.value=true;
  const starting=h.player.playSong(segmentQueue()[0]); await settle(); h.advance(250);
  h.voice.currentTime=3; h.audio.emit('waiting');
  assert.equal(h.voice.paused,true); assert.equal(h.player.audioLoading.value,true);
  h.audio.emit('playing'); await settle();
  assert.equal(h.voice.paused,false); assert.equal(h.voice.currentTime,3);
  h.voice.finish(); await starting; h.dispose();
});

test('音乐故障重连停止旧人声，去重和同曲中断来源保持', async () => {
  const h=harness(); h.player.announceOn.value=true;
  const starting=h.player.playSong(segmentQueue()[0]); await settle(); h.advance(250);
  h.analysis.fallback(); await settle(); await starting; h.advance(450);
  assert.equal(h.voice.src,''); assert.equal(h.player.isPlaying.value,true); assert.equal(h.audio.volume,.8);
  h.audio.error={code:2}; h.audio.emit('error'); await settle();
  assert.equal(h.calls.length,2); assert.equal(h.voice.src,'');
  assert.equal(h.events.at(-1).context.interrupted,true); h.dispose();
});

test('歌曲自然结束而人声尚未结束，关闭连播时两路都结束', async () => {
  const h=harness(); h.player.announceOn.value=true; h.player.autoPlayOn.value=false;
  const starting=h.player.playSong(segmentQueue()[0]); await settle(); h.advance(250);
  h.audio.finish(); await starting; h.advance(450);
  assert.equal(h.voice.src,''); assert.equal(h.player.isAnnouncing.value,false);
  assert.equal(h.player.isPlaying.value,false); assert.equal(h.audio.volume,.8);
  assert.deepEqual(h.events.map(e=>e.type),['play','finish']); h.dispose();
});

test('本机降级人声同样暂停恢复，暂停期间不消耗超时预算', async () => {
  const spoken=[]; let pauses=0,resumes=0;
  const h=harness({}, {browser:{SpeechSynthesisUtterance:class {constructor(text){this.text=text;}},
    speechSynthesis:{getVoices:()=>[{lang:'zh-CN',localService:true}],
      pause(){pauses++;},resume(){resumes++;},speak(u){spoken.push(u);u.onstart();},cancel(){}}}});
  h.voice.playImpl=()=>Promise.reject(new Error('offline')); h.player.announceOn.value=true;
  const starting=h.player.playSong(segmentQueue()[0]); await settle(); h.advance(250);
  assert.equal(spoken.length,1); assert.ok(Math.abs(h.audio.volume-.2)<1e-9);
  h.player.togglePlayback(); assert.equal(pauses,1);
  h.advance(120_000); await settle(); assert.equal(h.player.isAnnouncing.value,true);
  h.player.togglePlayback(); await settle(); assert.equal(spoken.length,1); assert.equal(resumes,2);
  spoken[0].onend(); await starting; h.advance(450); assert.equal(h.audio.volume,.8); h.dispose();
});

test('关闭报幕或人声静音、清空和卸载均解除压低，不留下人声', async () => {
  for(const operation of ['off','mute','clear','dispose']) {
    const h=harness(); h.player.announceOn.value=true;
    const starting=h.player.playSong(segmentQueue()[0]); await settle(); h.advance(250);
    if(operation==='off')h.player.announceOn.value=false;
    if(operation==='mute')h.player.voiceVolume.value=0;
    if(operation==='clear')h.player.clearPlayer();
    if(operation==='dispose')h.dispose();
    await starting; h.advance(450);
    assert.equal(h.voice.src,''); assert.equal(h.audio.volume,.8);
    if(operation!=='dispose')h.dispose();
  }
});


test('人声尚未playing时暂停，旧play拒绝不结束已恢复的人声', async () => {
  const pending=deferred(); const h=harness(); h.player.announceOn.value=true;
  h.voice.playImpl=()=>pending.promise;
  const starting=h.player.playSong(segmentQueue()[0]); await settle();
  h.player.togglePlayback(); assert.equal(h.voice.paused,true);
  h.voice.playImpl=null; h.player.togglePlayback(); await settle();
  pending.reject(new DOMException('paused','AbortError')); await settle();
  assert.equal(h.player.isAnnouncing.value,true); assert.equal(h.voice.paused,false);
  h.voice.finish(); await starting; h.dispose();
});


test('已出声的DJ故障只结束人声，恢复音乐且不换声线重念', async () => {
  let tts=0;const h=harness({getTtsUrl:()=>{tts++;return 'https://media/voice';}});
  h.player.announceOn.value=true;
  const starting=h.player.playSong(segmentQueue()[0]);await settle();h.advance(250);
  assert.ok(Math.abs(h.audio.volume-.2)<1e-9);
  h.voice.error={code:2};h.voice.emit('error');await starting;h.advance(450);
  assert.equal(h.audio.volume,.8);assert.equal(h.audio.paused,false);assert.equal(tts,1);
  assert.equal(h.calls.length,1);assert.equal(h.player.playbackState.value,'playing');h.dispose();
});

test('音乐原本静音时叠加和结束均保持静音，不擅自提高用户音量', async () => {
  const h=harness();h.player.announceOn.value=true;h.player.volume.value=0;
  const starting=h.player.playSong(segmentQueue()[0]);await settle();h.advance(250);
  assert.equal(h.audio.volume,0);h.voice.finish();await starting;h.advance(450);
  assert.equal(h.audio.volume,0);assert.equal(h.player.volume.value,0);h.dispose();
});

function openingQueue(pending=false) {
  return segmentQueue().map(s=>({...s, batch_id:'opening-batch',round_opening_priority:true,
    round_opening_independent:!pending,round_copy_status:pending?'pending':'ready',round_opening_status:pending?'pending':'ready',
    round_segments:pending?[]:s.round_segments}));
}
function readyOpening(h, queue=openingQueue()) {
  const current=h.player.currentSong.value;
  // 模拟真实轮询逐字段更新，而非事先给播放器一份已完成的节目。
  current.round_segments=queue[0].round_segments;
  current.round_opening_independent=true;
  current.round_copy_status='ready';current.round_opening_status='ready';
}

test('开场晚到20秒时歌曲先播，实际歌曲对象更新后叠加开场且只播一次',async()=>{
  const h=harness();h.player.announceOn.value=true;
  const starting=h.player.playSong(openingQueue(true)[0]);await settle();
  assert.equal(h.audio.paused,false);assert.equal(h.voice.src,'');assert.equal(h.player.isAnnouncing.value,true);
  assert.match(h.player.djPlaybackNotices.value['opening-batch'],/正在准备/);
  h.advance(20_000);readyOpening(h);await settle();h.advance(250);
  assert.equal(h.voice.paused,false);assert.ok(Math.abs(h.audio.volume-.2)<1e-9);
  h.voice.finish();await starting;
  await h.player.playSong(openingQueue()[0]);assert.equal(h.voice.src,'');h.dispose();
});

test('同轮首次点选第三首也能使用独立整轮开场，不挪用曲间段',async()=>{
  const texts=[];const h=harness({getTtsUrl:t=>{texts.push(t);return 'https://media/voice';}});h.player.announceOn.value=true;
  const queue=openingQueue();const starting=h.player.playSong(queue[2],queue,'recommendation');await settle();
  assert.deepEqual(texts,['自由正文0']);assert.equal(h.audio.src,'https://media/seg-c');
  h.voice.finish();await starting;h.dispose();
});

test('非独立的旧单曲开场不会挪到其他歌曲并清楚说明原因',async()=>{
  const h=harness();h.player.announceOn.value=true;
  const queue=openingQueue();queue.forEach(s=>s.round_opening_independent=false);
  await h.player.playSong(queue[1],queue,'recommendation');
  assert.equal(h.voice.src,'');assert.match(h.player.djPlaybackNotices.value['opening-batch'],/未挪用|承接上一首/);h.dispose();
});

test('开场等待期间暂停冻结45秒窗口，继续后播放同一开场',async()=>{
  const h=harness();h.player.announceOn.value=true;
  const starting=h.player.playSong(openingQueue(true)[0]);await settle();
  h.advance(30_000);h.player.togglePlayback();h.advance(120_000);readyOpening(h);await settle();
  assert.equal(h.voice.src,'');assert.equal(h.audio.paused,true);assert.equal(h.player.isAnnouncing.value,true);
  h.player.togglePlayback();await settle();assert.equal(h.voice.paused,false);
  h.voice.finish();await starting;assert.deepEqual(h.events.map(e=>e.type),['play']);h.dispose();
});

test('开场超时停止等待且晚到不突然开口，页面保留明确原因',async()=>{
  const h=harness();h.player.announceOn.value=true;
  const starting=h.player.playSong(openingQueue(true)[0]);await settle();h.advance(45_000);await starting;
  assert.match(h.player.djPlaybackNotices.value['opening-batch'],/超过45秒/);
  readyOpening(h);await settle();assert.equal(h.voice.src,'');assert.equal(h.audio.paused,false);h.dispose();
});

test('跳过准备中的开场持久化略过标记，不把后续两段全部取消',async()=>{
  const h=harness();h.player.announceOn.value=true;
  const q=openingQueue(true);const starting=h.player.playSong(q[0],q);await settle();h.player.skipAnnouncement();await starting;
  assert.match(h.player.djPlaybackNotices.value['opening-batch'],/已跳过/);
  readyOpening(h);await settle();assert.equal(h.voice.src,'');
  const ready=openingQueue();const next=h.player.playSong(ready[1],ready,'recommendation','auto_next');await settle();
  assert.equal(h.voice.paused,false);h.voice.finish();await next;h.dispose();
});

test('切换其他批次取消开场等待，旧节目迟到不能开口',async()=>{
  const h=harness();h.player.announceOn.value=true;
  const starting=h.player.playSong(openingQueue(true)[0]);await settle();
  await h.player.playSong(song('new'),[song('new')],'manual');await starting;readyOpening(h);await settle();
  assert.equal(h.voice.src,'');assert.equal(h.audio.src,'https://media/new');assert.equal(h.player.isAnnouncing.value,false);h.dispose();
});

test('同轮切歌在TTS尚未发声时不消耗开场，旧play拒绝不影响新声',async()=>{
  const pending=deferred();const texts=[];const h=harness({getTtsUrl:t=>{texts.push(t);return 'https://media/voice';}});
  h.player.announceOn.value=true;h.voice.playImpl=()=>pending.promise;
  const q=openingQueue();const first=h.player.playSong(q[0],q);await settle();
  h.voice.playImpl=null;const second=h.player.playSong(q[1],q);await settle();pending.reject(new DOMException('old','AbortError'));await settle();
  assert.equal(h.player.isAnnouncing.value,true);assert.equal(h.voice.paused,false);assert.equal(texts.length,2);
  h.voice.finish();await Promise.all([first,second]);h.dispose();
});

test('开场缺失和写作失败均有原因，不能拿曲间稿提前念',async()=>{
  for(const status of ['ready','failed','quiet','disabled','unavailable']) {
    const h=harness();h.player.announceOn.value=true;const q=openingQueue(true);
    const starting=h.player.playSong(q[0],q);await settle();
    h.player.currentSong.value.round_segments=status==='ready'?openingQueue()[0].round_segments.slice(2):[];
    h.player.currentSong.value.round_copy_status=status;await starting;
    assert.equal(h.voice.src,'');assert.ok(h.player.djPlaybackNotices.value['opening-batch']);h.dispose();
  }
});

test('首次音乐未开始时暂停并恢复，仍经过开场入口且只记一次真实开始',async()=>{
  for(const phase of ['url','play']) {
    const pending=deferred();let calls=0;
    const h=harness({getPlayUrl:()=>{calls++;return phase==='url'&&calls===1?pending.promise:Promise.resolve({success:true,url:'https://media/seg-a'});}});
    h.player.announceOn.value=true;if(phase==='play')h.audio.playImpl=()=>pending.promise;
    const starting=h.player.playSong(openingQueue()[0]);await settle();h.player.togglePlayback();
    h.audio.playImpl=null;h.player.togglePlayback();await settle();
    assert.equal(h.voice.paused,false);assert.equal(h.events.filter(e=>e.type==='play').length,1);
    if(phase==='url')pending.resolve({success:true,url:'https://media/old'});else pending.reject(new DOMException('paused','AbortError'));
    await settle();h.voice.finish();await starting;assert.equal(h.events.filter(e=>e.type==='play').length,1);h.dispose();
  }
});
