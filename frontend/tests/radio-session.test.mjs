import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { test } from "node:test";
import { runInNewContext } from "node:vm";

const require = createRequire(import.meta.url);
const ts = require("typescript");
const vue = require("vue");
// 会话层也必须验证迟到请求，避免播放器已取消但推荐历史仍被污染。
const compiled = ts.transpileModule(
  readFileSync(new URL("../src/composables/useRadioSession.ts", import.meta.url), "utf8"),
  { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } },
).outputText;
const song = (id) => ({ id, title: `歌曲${id}`, artist: "测试歌手" });

test('会话层转交行为来源，保存失败不阻塞或伪造新事件', async () => {
  const calls=[];
  const h=harness({sendListeningEvent:async (...args)=>{ calls.push(args); throw new Error('synthetic'); }});
  const context={contract:'playback-v2',playback_id:'p',start_reason:'user',interrupted:true,played_seconds:4};
  h.session.reportListeningEvent(song('a'),'skip',4,100,context);
  await Promise.resolve(); await Promise.resolve();
  assert.deepEqual(calls[0].slice(1),[song('a'),'skip',4,100,context]);
  assert.equal(calls.length,1); assert.equal(h.notices.length,0); h.dispose();
});
const deferred = () => {
  let resolve;
  const promise = new Promise((ok) => { resolve = ok; });
  return { promise, resolve };
};

function harness(overrides = {}) {
  const mounted = [], unmounted = [], storage = new Map(), exports = {}, timers = new Map(), notices = [];
  let timerId = 0;
  const api = {
    getAutoQueue: async () => ({ songs: [song("b")], batch_id: "auto" }),
    chat: async () => ({ answer: "新推荐", songs: [song("chat")], batch_id: "chat" }),
    clearSession: async () => undefined,
    adjustRecommendation: async () => ({ answer: "已换", songs: [song("replacement")], batch_id: "revised" }),
    getSessionMessages: async () => [],
    ...overrides,
  };
  runInNewContext(compiled, {
    exports, AbortController, Error, crypto: { randomUUID: () => "session-test" },
    window: { setTimeout: (fn) => { timers.set(++timerId, fn); return timerId; }, clearTimeout: (id) => timers.delete(id) },
    localStorage: {
      getItem: (key) => storage.get(key) ?? null,
      setItem: (key, value) => storage.set(key, value),
      removeItem: (key) => storage.delete(key),
    },
    require: (id) => {
      if (id === "vue") return { ...vue, onMounted: (callback) => mounted.push(callback), onBeforeUnmount: (fn) => unmounted.push(fn) };
      if (id === "../services/api") return api;
      throw new Error(`Unexpected dependency ${id}`);
    },
  });
  return { session: exports.useRadioSession((message) => notices.push(message)), notices, storage, timers, mount: () => mounted.forEach((fn) => fn()), dispose: () => unmounted.forEach((fn) => fn()) };
}

test('清空聊天保留独立本机记忆身份', async () => {
  const h = harness();
  assert.equal(h.storage.get('personal-radio-memory-owner'), 'session-test');
  await h.session.clear();
  assert.equal(h.storage.get('personal-radio-memory-owner'), 'session-test');
});

test('明确静默节目不能回退到歌曲旧介绍，参考提示只说明对应方向', async () => {
  const h = harness({ chat: async () => ({ answer: '直接听歌', batch_id: 'b', songs: [{ ...song('1'), round_intro: '旧介绍' }],
    dj_program: { text: '', batch_id: 'b', status: 'ready', sources: [], mode: 'metadata', max_seconds: 60 } }), sendSongFeedback: async () => {} });
  await h.session.sendMessage('来几首');
  assert.equal(h.session.songs.value[0].round_intro, '');
  await h.session.feedback({ ...song('1'), batch_id: 'b', reference_song_id: 'r' }, 'reference_like');
  assert.match(h.notices.at(-1), /参考方向/); h.dispose();
});

test("类似这首发送调用时选定的歌曲ID，不从聊天文字猜参考", async () => {
  const calls = [];
  const h = harness({ chat: async (...args) => { calls.push(args); return { answer: "参考推荐", songs: [song("next")] }; } });
  await h.session.sendMessage("  来几首类似这首的  ", "selected-reference");
  assert.deepEqual(calls[0].slice(0, 3), ["来几首类似这首的", "session-test", "selected-reference"]);
  assert.ok(calls[0][3] instanceof AbortSignal);
});

test("先确认播放队列 B/C 顺序再请求写稿，且保存实际顺序供刷新恢复", async () => {
  const calls = [];
  const pending = { status: "pending", batch_id: "order", queue_order_required: true, text: "", sources: [] };
  const h = harness({
    chat: async () => ({ answer: "已选好", songs: [song("a"), song("b"), song("c")], batch_id: "order", dj_program: pending }),
    bindDJQueue: async (_session, _batch, order) => {
      calls.push(Array.from(order));
      return { ...pending, status: "ready", playback_song_ids: order, segments: [{ before_song_id: "c", after_song_id: "a", position: 1, text: "接到 C" }] };
    },
    getDJProgram: async () => assert.fail("必须先确认队列"),
  });
  const ranked = await h.session.sendMessage("推荐歌曲");
  assert.deepEqual(calls, []);
  h.session.confirmPlaybackQueue([ranked[0], ranked[2], ranked[1]]);
  await new Promise(setImmediate);
  assert.deepEqual(calls, [["a", "c", "b"]]);
  assert.equal(h.session.songs.value[1].round_segments[0].after_song_id, "a");
  const stored = JSON.parse(h.storage.get("personal-radio-rounds")).rounds[0];
  assert.deepEqual(stored.playback_song_ids, ["a", "c", "b"]);
  assert.deepEqual(stored.songs.map(item => item.id), stored.playback_song_ids);
  h.dispose();
});

test("队列在途变动时，聊天只说明没有换歌，不接受过期批次", async () => {
  const calls = [];
  const h = harness({ chat: async (...args) => {
    calls.push(args);
    if (calls.length === 1) return { answer: "原推荐", batch_id: "base", songs: ["a", "b", "c"].map(song) };
    return { answer: "把第 2 首《歌曲c》换成了《歌曲x》。", batch_id: "revised",
      songs: ["a", "x", "b"].map(song), stats: { replacement: { batch_id: "base", song_id: "c" } } };
  } });
  await h.session.sendMessage("来几首");
  const result = await h.session.sendMessage("换第二首", "a", "base", ["a", "c", "b"], () => false);
  assert.deepEqual(calls[1].slice(5), ["base", ["a", "c", "b"]]);
  assert.equal(result.length, 0);
  assert.equal(h.session.rounds.value.length, 1);
  assert.match(h.session.messages.value.at(-1).content, /这次没有换歌/);
  h.dispose();
});

for (const operation of ["chat", "gentler", "energize"]) {
  test(`相对调整资料不足保留推荐、节目和历史，${operation}返回空操作结果`, async () => {
    let calls = 0;
    const program = { batch_id: "original", text: "原节目", sources: [], status: "ready", mode: "metadata", max_seconds: 60 };
    const blocked = { answer: "资料不足，当前队列保持不变。", songs: [], stats: { queue_preserved: true }, batch_id: null, dj_program: null };
    const h = harness({ chat: async () => ++calls === 1
      ? { answer: "原推荐", songs: [song("original")], batch_id: "original", dj_program: program } : blocked,
      adjustRecommendation: async () => blocked });
    await h.session.sendMessage("来几首歌");
    const saved = h.storage.get("personal-radio-rounds");
    const result = operation === "chat" ? await h.session.sendMessage("更舒缓一点")
      : await h.session.adjustRound(operation, "original");
    assert.equal(result.length, 0);
    assert.equal(h.session.songs.value[0].id, "original");
    assert.equal(h.session.djProgram.value.batch_id, "original");
    assert.equal(h.session.rounds.value.length, 1);
    assert.equal(h.storage.get("personal-radio-rounds"), saved);
    assert.equal(h.session.messages.value.at(-1).content, blocked.answer);
  });
}

test("单首调整独立于长期反馈，服务失败保留原推荐", async () => {
  let feedback = 0; const calls = [];
  const h = harness({ adjustRecommendation: async (...args) => { calls.push(args); return { answer: "调整", songs: [song("new")], batch_id: "new" }; }, sendSongFeedback: () => feedback++ });
  await h.session.sendMessage("点歌");
  const next = await h.session.adjustRound("replace", "chat", "a");
  assert.equal(next[0].id, "new"); assert.equal(feedback, 0);
  assert.deepEqual(calls[0].slice(0, 4), ["session-test", "chat", "replace", "a"]);
  assert.ok(calls[0][4] instanceof AbortSignal);
  const failed = harness({ adjustRecommendation: async () => { throw new Error("没有候选"); } });
  await failed.session.sendMessage("点歌");
  assert.equal((await failed.session.adjustRound("refresh", "chat")).length, 0);
  assert.equal(failed.session.songs.value[0].id, "chat");
});

test("清空后迟到调整与背景查询不能恢复歌曲和节目", async () => {
  const pending = deferred(); const h = harness({ adjustRecommendation: () => pending.promise });
  const adjusting = h.session.adjustRound("refresh", "b");
  await h.session.clear(); pending.resolve({ answer: "迟到", songs: [song("late")] });
  assert.equal((await adjusting).length, 0); assert.equal(h.session.rounds.value.length, 0);
  const bg = deferred(); let signal;
  const program = { batch_id: "b", text: "短开场", sources: [], status: "pending", mode: "metadata", max_seconds: 60 };
  const second = harness({ chat: async () => ({ answer: "推荐", songs: [song("a")], batch_id: "b", dj_program: program }),
    getDJProgram: (_s, _b, passed) => { signal = passed; return bg.promise; } });
  await second.session.sendMessage("点歌"); await second.session.clear();
  assert.equal(signal.aborted, true); bg.resolve({ ...program, text: "迟到背景", status: "ready" });
  for (let i = 0; i < 12; i++) await Promise.resolve();
  assert.equal(second.session.djProgram.value, null);
});

test("背景就绪更新同轮文字并保留批次，卸载取消后台查询", async () => {
  const bg = deferred(); let signal;
  const program = { batch_id: "b", text: "短开场", sources: [], status: "pending", mode: "metadata", max_seconds: 60 };
  const h = harness({ chat: async () => ({ answer: "推荐", songs: [song("a")], batch_id: "b", dj_program: program }),
    getDJProgram: (_s, _b, passed) => { signal = passed; return bg.promise; } });
  await h.session.sendMessage("点歌");
  bg.resolve({ ...program, status: "ready", text: "带来源的新文字" });
  for (let i = 0; i < 12; i++) await Promise.resolve();
  assert.equal(h.session.songs.value[0].round_intro, "带来源的新文字");
  assert.equal(h.session.rounds.value[0].batch_id, "b");
  h.dispose(); assert.equal(signal.aborted, true);
});

test("已取消续播即使返回成功也不能写推荐历史或本地存储", async () => {
  const pending = deferred(); let passedSignal;
  const h = harness({ getAutoQueue: (id, session, signal) => { passedSignal = signal; return pending.promise; } });
  const request = new AbortController();
  const extending = h.session.extendQueueFromSong(song("a"), request.signal);
  request.abort(); pending.resolve({ songs: [song("b")] });
  assert.equal((await extending).length, 0);
  assert.equal(passedSignal, request.signal);
  assert.equal(h.session.rounds.value.length, 0);
  assert.equal(h.storage.has("personal-radio-rounds"), false);
});

test("清空立即失效旧续播、聊天和历史恢复，清理期间不发新请求", async () => {
  const auto = deferred(), chat = deferred(), restore = deferred(), clear = deferred();
  let requests = 0;
  const h = harness({
    getAutoQueue: () => { requests++; return auto.promise; },
    chat: () => chat.promise, getSessionMessages: () => restore.promise,
    clearSession: () => clear.promise,
  });
  h.mount();
  const extending = h.session.extendQueueFromSong(song("a"));
  const chatting = h.session.sendMessage("想听温柔的歌");
  const clearing = h.session.clear();
  assert.equal((await h.session.extendQueueFromSong(song("a"))).length, 0);
  assert.equal((await h.session.sendMessage("新点歌")).length, 0);
  assert.equal(requests, 1);
  auto.resolve({ songs: [song("old-auto")] });
  chat.resolve({ answer: "旧聊天", songs: [song("old-chat")] });
  restore.resolve([{ role: "assistant", content: "旧历史" }]);
  await Promise.all([extending, chatting]);
  clear.resolve(); await clearing;
  assert.equal(h.session.rounds.value.length, 0);
  assert.equal(h.session.songs.value.length, 0);
  assert.equal(h.session.messages.value.length, 0);
  assert.equal(h.session.loading.value, false);
  assert.equal(h.storage.has("personal-radio-rounds"), false);
});

test("新聊天优先，上一轮迟到续播不能覆盖最新推荐", async () => {
  const pending = deferred(); const h = harness({ getAutoQueue: () => pending.promise });
  const extending = h.session.extendQueueFromSong(song("a"));
  await h.session.sendMessage("换个风格");
  pending.resolve({ songs: [song("old")] }); await extending;
  assert.equal(h.session.songs.value[0].id, "chat");
  assert.equal(h.session.rounds.value.length, 1);
});

test("有效续播正常保存批次、历史和持久化数据", async () => {
  const h = harness();
  const next = await h.session.extendQueueFromSong(song("a"), new AbortController().signal);
  assert.equal(next[0].id, "b");
  assert.equal(next[0].batch_id, "auto");
  assert.equal(h.session.historyCount.value, 1);
  const saved = JSON.parse(h.storage.get("personal-radio-rounds"));
  assert.equal(saved.rounds[0].songs[0].id, "b");
});

test("续播风格补充提示显示一次并保留到现有轮次历史", async () => {
  const notice = "同风格标签作品不足，补充候选可能包含不同曲风。";
  const h = harness({ getAutoQueue: async () => ({ songs: [song("next")], batch_id: "auto", message: notice }) });
  assert.equal((await h.session.extendQueueFromSong(song("a"))).length, 1);
  assert.deepEqual(h.notices, [notice]);
  assert.ok(h.session.rounds.value[0].answer.includes(notice));
  assert.ok(JSON.parse(h.storage.get("personal-radio-rounds")).rounds[0].answer.includes(notice));
});

test("取消后迟到的风格提示和空续播均不冒出提示", async () => {
  const pending = deferred();
  const h = harness({ getAutoQueue: () => pending.promise });
  const controller = new AbortController();
  const request = h.session.extendQueueFromSong(song("a"), controller.signal);
  controller.abort(); pending.resolve({ songs: [song("late")], message: "迟到提示" });
  assert.equal((await request).length, 0); assert.deepEqual(h.notices, []);
  const empty = harness({ getAutoQueue: async () => ({ songs: [], message: "没有歌曲" }) });
  await empty.session.extendQueueFromSong(song("a"));
  assert.deepEqual(empty.notices, []);
});

test("慢等待提示清空后收起，清空实际取消聊天请求且迟到结果失效", async () => {
  const pending = deferred(); let signal;
  const h = harness({ chat: (_m, _s, _song, passed) => { signal = passed; return pending.promise; } });
  const task = h.session.sendMessage("点歌");
  assert.equal(h.session.loadingHint.value, "");
  for (const fn of h.timers.values()) fn();
  assert.match(h.session.loadingHint.value, /等待较久/);
  await h.session.clear();
  assert.equal(signal.aborted, true); assert.equal(h.timers.size, 0);
  assert.equal(h.session.loadingHint.value, "");
  pending.resolve({ answer: "旧结果", songs: [song("late")] }); await task;
  assert.equal(h.session.messages.value.length, 0);
});

test("降级诊断留在本轮消息旁，不改歌曲推荐或将它当成 DJ 正文", async () => {
  const diagnostic = { request_id: "1234567890abcdef", elapsed_ms: 15, category: "recommendation", status: null, stages: {}, running_stages: [], codes: ["offline"], notices: ["离线基础模式"] };
  const h = harness({ chat: async () => ({ answer: "正常开场", songs: [song("a")], stats: { diagnostics: diagnostic } }) });
  const result = await h.session.sendMessage("点歌");
  assert.equal(result[0].id, "a");
  assert.equal(h.session.messages.value[1].content, "正常开场");
  assert.equal(h.session.messages.value[1].diagnostic.request_id, diagnostic.request_id);
  assert.equal(h.timers.size, 0);
});

test("卸载取消聊天和慢提示，超时错误正文可读且允许下一次请求", async () => {
  const pending = deferred(); let signal;
  const h = harness({ chat: (_m, _s, _song, passed) => { signal = passed; return pending.promise; } });
  const task = h.session.sendMessage("点歌"); h.dispose();
  assert.equal(signal.aborted, true); assert.equal(h.timers.size, 0);
  pending.resolve({ answer: "旧结果", songs: [song("late")] }); await task;
  assert.equal(h.session.rounds.value.length, 0);
  const failed = harness({ chat: async () => { throw new Error("页面等待已超时，服务端可能仍在处理"); } });
  await failed.session.sendMessage("点歌");
  assert.match(failed.session.messages.value.at(-1).content, /服务端可能仍在处理/);
  assert.equal(failed.session.loading.value, false);
});


test("反馈提示说明本次作用范围并保留当前队列，长期提示只指单曲", async () => {
  const calls = [];
  const h = harness({ sendSongFeedback: async (...args) => calls.push(args) });
  const item = { ...song("a"), batch_id: "batch" }; h.session.songs.value = [item];
  await h.session.feedback(item, "not_now");
  assert.equal(calls[0][2], "not_now"); assert.match(h.notices.at(-1), /换任务后失效.*当前播放不变/);
  assert.equal(h.session.songs.value[0].id, "a");
  await h.session.feedback(item, "dislike"); assert.match(h.notices.at(-1), /这首歌/); assert.doesNotMatch(h.notices.at(-1), /这类/);
  h.dispose();
});

test('异步节目更新实际播放所持歌曲对象，显式空节目不回退旧整轮文字', async () => {
  const pending = deferred();
  const program = { batch_id: 'seg-b', text: '旧的基础介绍', program_version: 'dj-segments-v1',
    segments: [], sources: [], mode: 'metadata', status: 'pending', copy_status: 'pending' };
  const h = harness({ chat: async () => ({ answer: '先听歌。', songs: [song('a'), song('b')], batch_id: 'seg-b', dj_program: program }),
    getDJProgram: () => pending.promise });
  const selected = await h.session.sendMessage('听歌');
  assert.equal(selected[0].round_segments.length, 0);
  pending.resolve({ ...program, status: 'ready', copy_status: 'ready',
    segments: [{ id: 'part', before_song_id: 'b', after_song_id: 'a', position: 1, text: '曲间新正文', sources: [] }] });
  for (let i=0;i<15;i++) await Promise.resolve();
  assert.equal(selected[1].round_segments[0].text, '曲间新正文');
  assert.equal(selected[1].round_program_version, 'dj-segments-v1');
  h.dispose();
});

test("旧批次失败展示具体原因，清空后的迟到反馈不报成功", async () => {
  const pending = deferred();
  const h = harness({ sendSongFeedback: () => pending.promise });
  const result = h.session.feedback(song("a"), "not_now");
  await h.session.clear(); pending.resolve(); await result; assert.equal(h.notices.length, 0); h.dispose();
  const failed = harness({ sendSongFeedback: async () => { throw new Error("这首歌不属于当前选歌任务"); } });
  await failed.session.feedback(song("a"), "not_now"); assert.match(failed.notices[0], /不属于当前选歌任务/); failed.dispose();
});

test('开场元数据随真实歌曲对象更新，包含独立整轮开场标记和准备状态',async()=>{
  const pending=deferred();const program={batch_id:'open-b',text:'',program_version:'dj-segments-v1',segments:[],sources:[],mode:'metadata',status:'pending',copy_status:'pending',opening_priority:true,opening_status:'pending'};
  const h=harness({chat:async()=>({answer:'先听歌',songs:[song('a')],batch_id:'open-b',dj_program:program}),getDJProgram:()=>pending.promise});
  const selected=await h.session.sendMessage('给我几首情绪说唱');
  assert.equal(selected[0].round_opening_priority,true);assert.equal(selected[0].round_copy_status,'pending');
  pending.resolve({...program,status:'ready',copy_status:'ready',opening_status:'ready',opening_independent:true,segments:[{id:'o',position:0,before_song_id:'a',text:'整轮开场。',sources:[]}]});
  for(let i=0;i<15;i++)await Promise.resolve();assert.equal(selected[0].round_opening_independent,true);assert.equal(selected[0].round_copy_status,'ready');assert.equal(selected[0].round_opening_status,'ready');h.dispose();
});
