import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { test } from "node:test";
import { runInNewContext } from "node:vm";

const require = createRequire(import.meta.url);
const ts = require("typescript");
const compiled = ts.transpileModule(readFileSync(new URL("../src/services/api.ts", import.meta.url), "utf8"), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText;

function apiHarness(fetch, localStorage) {
  const exports = {}, timers = new Map(); let index = 0;
  runInNewContext(compiled, { exports, fetch, localStorage, AbortController, Error, DOMException, URLSearchParams,
    crypto: { randomUUID: () => 'synthetic-event-id' },
    window: { setTimeout: (fn, ms) => { timers.set(++index, { fn, ms }); return index; }, clearTimeout: id => timers.delete(id) },
  });
  return { api: exports, timers };
}

test('收听接口原样携带新版来源且不为旧调用猜测来源', async () => {
  const bodies=[];
  const h=apiHarness(async (_url,init)=>{ bodies.push(JSON.parse(init.body)); return new Response('{"ok":true}'); });
  const context={contract:'playback-v2',playback_id:'p',start_reason:'repeat_one',interrupted:false,played_seconds:12};
  await h.api.sendListeningEvent('s',{id:'song'},'play',0,100,context);
  await h.api.sendListeningEvent('s',{id:'song'},'play',0,100);
  assert.deepEqual(bodies[0].context,context);
  assert.equal(bodies[1].context,undefined);
});

test('聊天携带独立记忆归属，撤销精确指定记录且不自动重试', async () => {
  const calls = [];
  const h = apiHarness(async (url, init) => {
    calls.push({ url, init });
    return new Response(JSON.stringify({ ok: true, answer: '合成回复' }));
  }, { getItem: key => key === 'personal-radio-memory-owner' ? 'stable-owner' : null });
  await h.api.chat('以后睡前不要推荐这首', 'new-chat', 'song');
  assert.equal(JSON.parse(calls[0].init.body).memory_owner_id, 'stable-owner');
  await h.api.removeSceneMemory('new-chat', 'memory-1');
  assert.equal(calls[1].url, '/api/sessions/new-chat/scene-memories/memory-1');
  assert.equal(calls[1].init.method, 'DELETE');
  assert.equal(calls.length, 2);
});

test('偏好管理保留条件和预期记录，参考反馈携带真实批次而非直接改全局喜好', async () => {
  const calls = [];
  const h = apiHarness(async (url, init) => { calls.push({ url, init }); return new Response(JSON.stringify({ ok: true })); });
  await h.api.saveMemory('s', { kind: 'genre', target: '纯音乐', value: 'reduce', scene: '工作专注', reference: '', expected_id: 'old' });
  await h.api.sendSongFeedback('s', { id: '2', batch_id: 'b', reference_song_id: '1' }, 'reference_like');
  assert.equal(calls[0].url, '/api/memories');
  assert.equal(JSON.parse(calls[0].init.body).expected_id, 'old');
  assert.equal(JSON.parse(calls[0].init.body).scene, '工作专注');
  assert.equal(calls[1].url, '/api/reference-feedback');
  assert.deepEqual(JSON.parse(calls[1].init.body), { session_id: 's', batch_id: 'b', song_id: '2', value: 'like' });
  await assert.rejects(h.api.sendSongFeedback('s', { id: '2' }, 'reference_unlike'), /参考曲/);
  assert.equal(calls.length, 2);
});

test("新观察POST只发一次且禁用缓存，旧服务或写入失败不重握手或回投旧接口", async () => {
  const calls = [];
  const h = apiHarness(async (url, init) => {
    calls.push({ url, init });
    return new Response(JSON.stringify({ detail: "合成失败" }), { status: url.endsWith("/prepare") ? 404 : 503 });
  });
  await assert.rejects(h.api.prepareAudioObservation("synthetic-a"), e => e.status === 404);
  await assert.rejects(h.api.submitAudioObservation({ observation_id: "a".repeat(32) }), e => e.status === 503);
  await assert.rejects(h.api.cancelAudioObservation("a".repeat(32)), e => e.status === 503);
  assert.deepEqual(calls.map(c => c.url), ["/api/audio-observations/prepare", "/api/audio-observations", "/api/audio-observations/cancel"]);
  assert.ok(calls.every(c => c.init.method === "POST" && c.init.cache === "no-store"));
  assert.deepEqual(JSON.parse(calls[0].init.body), { song_id: "synthetic-a", measurement_mode: "foreground" });
  assert.equal(h.timers.size, 0);
});

test("新观察准备取消及保存五秒截止都清理计时器，未知结果不重发", async () => {
  let calls = 0;
  const h = apiHarness((_url, init) => {
    calls++;
    return new Promise((_ok, fail) => init.signal.addEventListener("abort", () => fail(new DOMException("cancelled", "AbortError")), { once: true }));
  });
  const request = new AbortController();
  const preparing = h.api.prepareAudioObservation("synthetic-a", request.signal);
  request.abort(); await assert.rejects(preparing, e => e.name === "AbortError");
  assert.equal(h.timers.size, 0);
  const saving = h.api.submitAudioObservation({ observation_id: "a".repeat(32) });
  const timer = [...h.timers.values()][0]; assert.equal(timer.ms, 5000); timer.fn();
  await assert.rejects(saving, /服务端可能已接收/);
  assert.equal(calls, 2); assert.equal(h.timers.size, 0);
});

test("播放地址遇到本机401仅重新握手一次，刷新地址全程禁用缓存", async () => {
  const calls = [];
  const h = apiHarness(async (url, init) => {
    calls.push({ url, init });
    if (calls.length === 1) return new Response(JSON.stringify({ detail: "本机访问凭证已失效" }), { status: 401 });
    return new Response(JSON.stringify(url === "/api/local-access" ? { ok: true } : { success: true, url: "https://media/fixture" }));
  });
  const result = await h.api.getPlayUrl("fixture");
  assert.equal(result.success, true);
  assert.deepEqual(calls.map(({ url }) => url), ["/api/ncm/playurl/fixture", "/api/local-access", "/api/ncm/playurl/fixture"]);
  assert.ok(calls.every(({ init }) => init.cache === "no-store"));
  assert.equal(calls[1].init.credentials, "same-origin");
  assert.equal(h.timers.size, 0);
});

test("重新握手后仍401时不循环，保留可识别的HTTP状态", async () => {
  let calls = 0;
  const h = apiHarness(async url => {
    calls++;
    return new Response(JSON.stringify(url === "/api/local-access" ? { ok: true } : { detail: "本机访问凭证已失效，请刷新页面" }), { status: url === "/api/local-access" ? 200 : 401 });
  });
  await assert.rejects(h.api.getPlayUrl("fixture"), error => error instanceof h.api.ApiError && error.status === 401);
  assert.equal(calls, 3);
  assert.equal(h.timers.size, 0);
});

test("聊天HTTP载荷保留显式参考ID，不传时不编造字段", async () => {
  const payloads = [];
  const h = apiHarness(async (_url, init) => {
    payloads.push(JSON.parse(init.body));
    return new Response(JSON.stringify({ answer: "参考推荐", songs: [] }));
  });
  await h.api.chat("类似这首", "session", "selected-reference");
  await h.api.chat("普通点歌", "session");
  assert.equal(payloads[0].current_song_id, "selected-reference");
  assert.equal(Object.hasOwn(payloads[1], "current_song_id"), false);
});

test("HTTP 错误使用固定码与编号，忽略伪造诊断头", async () => {
  const h = apiHarness(async () => new Response(JSON.stringify({ detail: "服务繁忙" }), { status: 429,
    headers: { "X-Request-ID": "1234567890abcdef", "X-Failure-Code": "request_busy" } }));
  await assert.rejects(h.api.chat("点歌"), /服务繁忙.*1234567890abcdef.*request_busy/);
  assert.equal(h.timers.size, 0);
  const forged = apiHarness(async () => new Response("bad", { status: 502,
    headers: { "X-Request-ID": "secret-url", "X-Failure-Code": "private-key" } }));
  await assert.rejects(forged.api.chat("点歌"), error => !error.message.includes("secret-url"));
});

test("聊天超时取消网络并诚实提示后台可能未停，不自动提交第二次", async () => {
  let signal, calls = 0;
  const h = apiHarness((_url, init) => { calls++; signal = init.signal; return new Promise((_ok, fail) => {
    signal.addEventListener("abort", () => fail(new DOMException("aborted", "AbortError")), { once: true });
  }); });
  const task = h.api.chat("点歌");
  const timer = [...h.timers.values()][0]; assert.equal(timer.ms, 90_000); timer.fn();
  await assert.rejects(task, /服务端可能仍在处理/);
  assert.equal(signal.aborted, true); assert.equal(calls, 1); assert.equal(h.timers.size, 0);
});

test("超时覆盖 JSON 响应体读取，非只覆盖响应头", async () => {
  let ready;
  const reading = new Promise(resolve => { ready = resolve; });
  const h = apiHarness(async (_url, init) => ({ ok: true, json: () => new Promise((_ok, fail) => {
    init.signal.addEventListener("abort", () => fail(new DOMException("aborted", "AbortError")), { once: true });
    ready();
  }) }));
  const task = h.api.chat("点歌"); await reading;
  [...h.timers.values()][0].fn();
  await assert.rejects(task, /页面等待已超时/); assert.equal(h.timers.size, 0);
});

test("调用方取消调整与超时区分，已取消信号不重新变为有效请求", async () => {
  const h = apiHarness((_url, init) => new Promise((_ok, fail) => {
    if (init.signal.aborted) fail(new DOMException("aborted", "AbortError"));
    else init.signal.addEventListener("abort", () => fail(new DOMException("aborted", "AbortError")), { once: true });
  }));
  const controller = new AbortController();
  const task = h.api.adjustRecommendation("s", "b", "refresh", undefined, controller.signal);
  controller.abort(); await assert.rejects(task, error => error.name === "AbortError" && !/超时/.test(error.message));
  await assert.rejects(h.api.chat("点歌", "s", undefined, controller.signal), error => error.name === "AbortError");
  assert.equal(h.timers.size, 0);
});

test("诊断只读有五秒边界且不缓存；连接测试不隐式保存设置", async () => {
  const calls = [];
  const h = apiHarness(async (url, init) => {
    calls.push({ url, init });
    const timer = [...h.timers.values()][0];
    assert.equal(timer.ms, url === "/api/diagnostics" ? 5_000 : 25_000);
    return new Response(JSON.stringify({ recent: [], active: [], ok: true, message: "OK" }));
  });
  await h.api.getDiagnostics(); await h.api.testLLMSettings({ provider: "offline" });
  assert.equal(calls[0].init.cache, "no-store");
  assert.equal(calls[0].init.method, undefined);
  assert.equal(calls[1].url, "/api/settings/llm/test");
  assert.equal(h.timers.size, 0);
});

test("同步进度读取十秒截止，不重发任何写请求", async () => {
  const calls = [];
  const h = apiHarness((url, init) => { calls.push(url); return new Promise((_ok, fail) => {
    init.signal.addEventListener("abort", () => fail(new DOMException("aborted", "AbortError")), { once: true });
  }); });
  const waiting = h.api.getNcmSyncStatus("task");
  const timer = [...h.timers.values()][0]; assert.equal(timer.ms, 10_000); timer.fn();
  await assert.rejects(waiting, /后台任务可能仍在运行/);
  assert.deepEqual(calls, ["/api/ncm/sync/task"]); assert.equal(h.timers.size, 0);
});


test("进度读取遇到本机401只握手一次，再读取同一任务", async () => {
  const calls = [];
  const h = apiHarness(async (url, init) => {
    calls.push({ url, init });
    if (calls.length === 1) return new Response(JSON.stringify({ detail: "expired" }), { status: 401 });
    return new Response(JSON.stringify(url === "/api/local-access" ? { ok: true } : { task_id: "task", status: "running" }));
  });
  assert.equal((await h.api.getNcmSyncStatus("task")).status, "running");
  assert.deepEqual(calls.map(c => c.url), ["/api/ncm/sync/task", "/api/local-access", "/api/ncm/sync/task"]);
  assert.ok(calls.every(c => !c.init.method && c.init.cache === "no-store"));
  assert.equal(calls[1].init.credentials, "same-origin");
  assert.equal(h.timers.size, 0);
});

test("进度重新握手仍401时终止，不循环也不重发任务", async () => {
  const calls = [];
  const h = apiHarness(async url => {
    calls.push(url);
    return new Response(JSON.stringify(url === "/api/local-access" ? { ok: true } : { detail: "expired" }), { status: url === "/api/local-access" ? 200 : 401 });
  });
  await assert.rejects(h.api.getNcmSyncStatus("task"), error => error.status === 401);
  assert.deepEqual(calls, ["/api/ncm/sync/task", "/api/local-access", "/api/ncm/sync/task"]);
  assert.equal(h.timers.size, 0);
});


test("当前任务反馈携带批次且与长期接口分离，旧批次错误不会误报已保存", async () => {
  const calls = [];
  const h = apiHarness(async (url, init) => { calls.push({ url, init }); return new Response(JSON.stringify({ detail: "这首歌不属于当前选歌任务" }), { status: url === "/api/task-feedback" ? 409 : 200 }); });
  await assert.rejects(h.api.sendSongFeedback("s", { id: "1", batch_id: "old" }, "not_now"), /不属于当前选歌任务/);
  assert.equal(calls[0].url, "/api/task-feedback");
  assert.deepEqual(JSON.parse(calls[0].init.body), { session_id: "s", song_id: "1", batch_id: "old" });
  await h.api.sendSongFeedback("s", { id: "1" }, "dislike"); assert.equal(calls[1].url, "/api/feedback");
  await assert.rejects(h.api.sendSongFeedback("s", { id: "1" }, "not_now"), /当前任务/);
  assert.equal(calls.length, 2);
});

test("播放地址错误保留合法的重试等待建议，拒绝超范围或非数字时间", async () => {
  for (const [header, delay] of [["12", 12_000], ["9999", undefined], ["invalid", undefined]]) {
    const h = apiHarness(async () => new Response(JSON.stringify({ detail: "合成取址失败" }), {
      status: 502, headers: { "Retry-After": header },
    }));
    await assert.rejects(h.api.getPlayUrl("fixture"), error =>
      error instanceof h.api.ApiError && error.status === 502 && error.retryAfterMs === delay);
  }
});
