import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { runInNewContext } from "node:vm";
import { test } from "node:test";
import { panelFocusModule } from "./helpers/panel-focus-module.mjs";

const require = createRequire(import.meta.url);
const vue = require("vue");
const { parse, compileScript } = require("@vue/compiler-sfc");
const ts = require("typescript");
// v-model 指令仍执行真实事件注册，宿主只补齐所需 DOM 边界。
globalThis.Document = class extends EventTarget {};
globalThis.ShadowRoot = class extends EventTarget {};
const song = (id) => ({ id, title: `歌曲${id}`, artist: "测试歌手", cover_url: "test-cover" });
const deferred = () => {
  let resolve;
  const promise = new Promise((done) => { resolve = done; });
  return { promise, resolve };
};
const settle = async () => { for (let index = 0; index < 12; index++) await Promise.resolve(); await vue.nextTick(); };

function component(path, dependencies = {}) {
  const { descriptor } = parse(readFileSync(new URL(`../src/${path}`, import.meta.url), "utf8"));
  // 合成宿主执行真实VNode与指令；关闭只适用于浏览器HTML解析器的静态字符串合并。
  const compiled = ts.transpileModule(compileScript(descriptor, { id: path, inlineTemplate: true,
    templateOptions: { compilerOptions: { hoistStatic: false } } }).content, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  const exports = {};
  const document = new EventTarget();
  document.querySelectorAll = () => [];
  runInNewContext(compiled, { exports, Error, document, require: (id) => {
    if (id === "vue") return path === "components/scene/TopBar.vue" ? { ...vue, Transition: vue.BaseTransition } : vue;
    if (id === "../../composables/usePanelFocus") return panelFocusModule(vue, document);
    // 背景光学有独立生命周期回归，业务组件测试不模拟浏览器绘制能力。
    if (id === "../player/PlayerGlass.vue") return { default: () => null };
    if (id === "../auth/AccountAvatar.vue") return { default: component("components/auth/AccountAvatar.vue", dependencies) };
    if (id === "./RemovedLibraryPanel.vue") return { default: () => null };
    if (id === './MemoryPreferences.vue') return { default: component('components/taste/MemoryPreferences.vue', dependencies) };
    if (id in dependencies) return dependencies[id];
    throw new Error(`Unexpected dependency ${id}`);
  } });
  return exports.default;
}

// 执行真实组件的 render 与点击处理，替换的只有宿主节点；不依赖字符串检查模拟交互。
function mount(Component, initialProps) {
  const hostDocument = new Document();
  const node = (type, text = "") => Object.defineProperties(Object.assign(new EventTarget(), {
    type, text, children: [], props: {}, parent: null, value: "", getRootNode: () => hostDocument, scrollTo() {}, focus() {},
  }), {
    options: { get() { return this.children.filter(child => child.type === 'option'); } },
    selectedOptions: { get() { return this.children.filter(child => child.type === 'option' && child.selected); } },
  });
  const renderer = vue.createRenderer({
    createElement: (type) => node(type), createText: (text) => node("text", text), createComment: (text) => node("comment", text),
    insert(child, parent, anchor = null) {
      if (child.parent) child.parent.children.splice(child.parent.children.indexOf(child), 1);
      const index = anchor ? parent.children.indexOf(anchor) : -1;
      parent.children.splice(index < 0 ? parent.children.length : index, 0, child); child.parent = parent;
    },
    remove(child) { if (child.parent) child.parent.children.splice(child.parent.children.indexOf(child), 1); child.parent = null; },
    setElementText: (item, text) => { item.text = text; item.children = []; },
    setText: (item, text) => { item.text = text; }, setComment: (item, text) => { item.text = text; },
    parentNode: (item) => item.parent,
    nextSibling: (item) => item.parent?.children[item.parent.children.indexOf(item) + 1] || null,
    patchProp: (item, key, _old, value) => { item.props[key] = value; },
  });
  const root = node("root"); const props = vue.reactive(initialProps);
  const app = renderer.createApp({ render: () => vue.h(Component, props) }); app.mount(root);
  const all = () => {
    const items = []; const walk = (item) => { items.push(item); item.children.forEach(walk); }; walk(root); return items;
  };
  const text = (item) => item.text + item.children.map(text).join("");
  const findButton = (label) => all().find((item) => item.type === "button" && (item.props["aria-label"] === label || text(item).trim() === label));
  return { props, all, text, findButton, async click(label) {
    const button = findButton(label); assert.ok(button, `Missing button ${label}`);
    assert.ok(!button.props.disabled, `Disabled button ${label}`); button.props.onClick?.({}); await settle();
  }, dispose: () => app.unmount() };
}

test("聊天组件持续展示降级原因与慢提示，诊断不代替 DJ 正文", async () => {
  const ChatDrawer = component("components/chat/ChatDrawer.vue");
  const h = mount(ChatDrawer, { messages: [{ role: "assistant", content: "已挑好歌曲", diagnostic: {
    request_id: "1234567890abcdef", elapsed_ms: 25, codes: ["llm_timeout"], notices: ["AI 响应超时，已使用本地规则"],
  } }], loading: true, loadingHint: "这次等待较久，音乐可以继续" });
  assert.ok(h.all().some(item => item.props.role === "status" && h.text(item).includes("AI 响应超时")));
  assert.ok(h.all().some(item => item.props.role === "status" && h.text(item).includes("等待较久")));
  assert.ok(h.all().some(item => item.type === "summary" && h.text(item) === "诊断"));
  assert.ok(h.all().some(item => item.type === "code" && h.text(item) === "1234567890abcdef"));
  h.props.loading = false; await settle();
  assert.ok(!h.all().some(item => item.props.role === "status" && h.text(item).includes("等待较久")));
  h.dispose();
});

for (const [name, path, props] of [
  ["二维码外壳", "components/auth/NcmLoginModal.vue", { qrImage: "", status: "合成状态，不执行登录" }],
  ["歌单同步外壳", "components/auth/NcmPlaylistModal.vue", { playlists: [], selectedIds: [], scope: "liked", loading: false, syncing: false, task: null, error: "" }],
  ["首次配置外壳", "components/settings/FirstRunModal.vue", { status: { library: { song_count: 0, ready: false }, netease: { credential_present: false }, llm: { provider: "offline", has_api_key: false } } }],
]) {
  test(`${name}的 Esc 接到真实关闭事件，不触发登录或同步`, async () => {
    let closed = 0;
    const h = mount(component(path), { ...props, onClose: () => closed++ });
    const card = h.all().find(n => n.props.tabindex === "-1" && n.props.onKeydown);
    assert.ok(card);
    card.props.onKeydown({ key: "Escape", preventDefault() {}, stopPropagation() {} });
    await settle(); assert.equal(closed, 1); h.dispose();
  });
}

test('曲间节目展示部分保留和未生成原因，不把基础资料冒充AI正文', async () => {
  const h = mount(component('components/chat/ChatDrawer.vue'), { messages: [], loading: false,
    djProgram: { program_version: 'dj-segments-v1', segments: [], text: '这段基础元数据不应冒充AI正文',
      sources: [], mode: 'metadata', status: 'pending', copy_status: 'writing' } });
  const text = () => h.text(h.all()[0]);
  assert.match(text(), /正在准备曲间节目/);
  assert.doesNotMatch(text(), /这段基础元数据不应/);
  h.props.djProgram = { ...h.props.djProgram, status: 'ready', copy_status: 'partial',
    copy_reasons: ['unsupported_claim'], segments: [{ id: 'part', before_song_id: 's', position: 1, text: '这一段有自己的重点。', sources: [] }] };
  await settle();
  assert.match(text(), /1 段节目/); assert.match(text(), /这一段有自己的重点/);
  assert.match(text(), /未通过作品事实或听众处境检查/);
  h.props.djProgram = { ...h.props.djProgram, segments: [], copy_status: 'failed', copy_reasons: ['llm_timeout'] };
  await settle(); assert.match(text(), /AI 写作超时/); assert.doesNotMatch(text(), /正在补充资料/);
  h.dispose();
});

test("DJ 资料查询中、不可用和完成但无资料分别如实说明", async () => {
  const ChatDrawer = component("components/chat/ChatDrawer.vue");
  const h = mount(ChatDrawer, { messages: [], loading: false,
    djProgram: { text: "合成元数据介绍", status: "pending", mode: "metadata", sources: [] } });
  const notes = () => h.all().filter(n => n.type === "p" && n.props.class === "dj-source-note").map(h.text).join(" ");
  assert.match(notes(), /正在核对背景资料/);
  assert.doesNotMatch(notes(), /没有可用|暂时不可用/);
  h.props.djProgram.status = "fallback"; await settle();
  assert.match(notes(), /暂时不可用.*按曲库信息/);
  h.props.djProgram.status = "ready"; await settle();
  assert.match(notes(), /本轮没有可用的背景资料/);
  assert.equal(h.all().filter(n => n.type === "a").length, 0);
  h.dispose();
});

test("AI 设置诊断只在手动刷新时读取，不测试模型或保存设置", async () => {
  let reads = 0;
  const AISettings = component("components/settings/AISettingsModal.vue", { "../../services/api": {
    getLLMSettings: async () => ({ provider: "offline", model: "local-rules", base_url: "", has_api_key: true,
      providers: [{ id: "offline", default_model: "local-rules", default_base_url: "", needs_api_key: false }] }),
    getDiagnostics: async () => { reads++; return { capacity: 100, active: [], recent: [{
      request_id: "1234567890abcdef", status: 200, category: "recommendation", elapsed_ms: 20,
      codes: ["offline"], running_stages: [], stages: { retrieval: { elapsed_ms: 10, calls: 1 } },
    }] }; },
    saveLLMSettings: () => { throw new Error("诊断不能保存设置"); },
    testLLMSettings: () => { throw new Error("诊断不能请求模型"); },
  } });
  const h = mount(AISettings, {}); await settle();
  assert.equal(reads, 0); await h.click("刷新诊断");
  assert.equal(reads, 1);
  assert.ok(h.all().some(item => item.type === "small" && h.text(item).includes("1234567890abcdef")));
  assert.ok(h.all().some(item => item.type === "p" && h.text(item).includes("检索（含编码）")));
  await settle(); assert.equal(reads, 1);
  h.dispose();
});

const SongItem = component("components/playlist/SongItem.vue", { "../../services/api": { getSongCover: async () => "" } });

test("单首换歌和长期减少推荐是不同操作", async () => {
  const calls = [];
  const h = mount(SongItem, { song: song("a"), index: 0, current: false, playing: false, expanded: false,
    allowReplace: true, onReplace: () => calls.push("replace"), onFeedback: () => calls.push("feedback") });
  await h.click("队列操作 歌曲a"); await h.click("只换这一首");
  assert.deepEqual(calls, ["replace"]); h.dispose();
});

test("快捷调整保留文字输入并禁用重复请求，节目展示来源与降级边界", async () => {
  const calls = [];
  const ChatDrawer = component("components/chat/ChatDrawer.vue");
  const h = mount(ChatDrawer, { messages: [], loading: false, canAdjust: true,
    djProgram: { text: "来源字段", status: "ready", mode: "sourced", sources: [{ id: "f", text: "事实", url: "https://musicbrainz.org/recording/a", entity_url: "https://musicbrainz.org/recording/a", source: "社区资料" }] },
    onAdjust: (op) => calls.push(op) });
  await h.click("更温柔一点"); assert.deepEqual(calls, ["gentler"]);
  assert.equal(h.all().filter((n) => n.type === "textarea").length, 1);
  assert.equal(h.all().filter((n) => n.type === "a").length, 2);
  h.props.loading = true; await settle(); assert.equal(h.findButton("换些歌手").props.disabled, true);
  h.props.djProgram = { text: "短开场", status: "ready", mode: "metadata", sources: [] }; await settle();
  assert.ok(h.all().some((n) => h.text(n).includes("不推测创作经历"))); h.dispose();
});

test("自己选歌与 DJ 入口独立发出操作，选中标记不改变播放模式", async () => {
  const calls = [];
  const TopBar = component("components/scene/TopBar.vue");
  const h = mount(TopBar, { accounts: [], syncing: false, isPlaying: false, activePanel: "playlist", onLibrary: () => calls.push("library"), onDj: () => calls.push("dj") });
  assert.equal(h.findButton("自己选歌").props["aria-pressed"], true);
  await h.click("DJ 推荐"); await h.click("自己选歌");
  assert.deepEqual(calls, ["dj", "library"]); h.dispose();
});

test("音乐账号菜单按平台连接和退出，网易云退出后保留 QQ 头像和状态", async () => {
  const TopBar = component("components/scene/TopBar.vue");
  const netease = { platform: "netease", profile: { nickname: "夜间听众", avatarUrl: "https://p1.music.126.net/test.png" } };
  const qq = { platform: "qq", profile: { nickname: "第二位听众", avatarUrl: null } };
  const calls = [];
  const h = mount(TopBar, { accounts: [netease, qq], syncing: false, isPlaying: false,
    onConnect: platform => calls.push(["connect", platform]),
    onDisconnect: platform => {
      calls.push(["disconnect", platform]);
      h.props.accounts = h.props.accounts.map(account => account.platform === platform ? { ...account, profile: null } : account);
    },
  });
  await h.click("音乐账号 · 夜间听众");
  assert.equal(h.all().filter(item => item.type === "img").length, 2);
  assert.ok(h.findButton("同步网易云歌单"));
  assert.ok(h.findButton("退出QQ 音乐"));
  await h.click("退出网易云音乐");
  await h.click("音乐账号 · 第二位听众");
  assert.ok(h.findButton("退出QQ 音乐"));
  assert.equal(h.findButton("同步网易云歌单"), undefined);
  await h.click("退出QQ 音乐");
  await h.click("音乐账号");
  await h.click("连接网易云音乐");
  assert.deepEqual(calls, [["disconnect", "netease"], ["disconnect", "qq"], ["connect", "netease"]]);
  h.dispose();
});

test("歌曲更多菜单提供下一首与队列编辑，首尾及当前歌曲有禁用边界", async () => {
  const calls = [];
  const h = mount(SongItem, { song: song("a"), index: 0, current: true, playing: false, expanded: false,
    allowNext: true, manageQueue: true, canMoveUp: false, canMoveDown: true,
    onMove: (step) => calls.push(step), onRemove: () => calls.push("remove") });
  await h.click("队列操作 歌曲a");
  assert.equal(h.findButton("下一首播放").props.disabled, true);
  assert.equal(h.findButton("上移").props.disabled, true);
  await h.click("下移"); await h.click("移出队列");
  assert.deepEqual(calls, [1, "remove"]); h.dispose();
});

test("QQ 曲目可编辑队列，并且没有本地歌曲风格审核入口", async () => {
  const actions = [];
  const h = mount(SongItem, { song: { ...song("qq:1"), platform: "qq" }, index: 0, current: false,
    playing: false, expanded: false, allowReview: true, manageQueue: true, allowNext: true,
    onRemove: () => actions.push("remove") });
  await h.click("队列操作 歌曲qq:1");
  assert.equal(h.findButton("查看／纠正风格"), undefined);
  assert.ok(h.findButton("下一首播放"));
  await h.click("移出队列");
  assert.deepEqual(actions, ["remove"]);
  h.dispose();
});

test("QQ 歌单会话过期时提供重新扫码，继续加载不会反复请求旧会话", async () => {
  class ApiError extends Error {
    constructor(message, status) { super(message); this.status = status; }
  }
  let reconnected = 0;
  const modal = component("components/auth/QqPlaylistModal.vue", { "../../services/api": {
    ApiError, getQqPlaylists: async () => { throw new ApiError("QQ 音乐登录已失效，请重新扫码", 409); },
  } });
  const h = mount(modal, { onReconnect: () => reconnected++ });
  await settle();
  assert.equal(h.findButton("重试"), undefined);
  await h.click("重新扫码");
  assert.equal(reconnected, 1);
  h.dispose();
});

function drawer(api = {}) {
  return component("components/playlist/PlaylistDrawer.vue", { "./SongItem.vue": { default: SongItem },
    "./OnlineCatalog.vue": { default: () => null }, "../../services/api": {
    getLocalLibraryPlaylists: async () => [], getNcmPlaylists: async () => [],
    getRemovedLibraryItems: async () => ({ items: [], playlist_ids: [] }),
    getLocalLibrarySongs: async () => ({ songs: [song("a"), song("b")], total: 2, has_more: false }), ...api,
  } });
}
const drawerProps = (overrides = {}) => ({ entries: [], queue: [], queueSource: "manual", view: "queue", queueCount: 0, historyCount: 0, isPlaying: false, syncing: false, ...overrides });

test("长队列只渲染 50 首，跨页上移使用全队列索引而非页内索引", async () => {
  const calls = []; const queue = Array.from({ length: 5000 }, (_, index) => song(String(index)));
  const h = mount(drawer(), drawerProps({ queue, queueCount: 5000, currentSongId: "0", onMove: (...args) => calls.push(args) }));
  assert.equal(h.all().filter((item) => item.type === "li").length, 50);
  await h.click("下一页 →"); await h.click("更多操作 歌曲50"); await h.click("上移");
  assert.deepEqual(calls, [["50", -1]]);
  h.props.currentSongId = "121"; await settle();
  assert.ok(h.findButton("播放 歌曲121"));
  assert.equal(h.all().filter((item) => item.type === "li").length, 50); h.dispose();
});

test("关闭曲库或切换视图会失效迟到手动队列，不能突然发出播放", async () => {
  for (const action of ["close", "switch"]) {
    const pending = deferred(); const calls = [];
    const h = mount(drawer({ getLocalLibrarySongs: (input) => input.limit === 5000 ? pending.promise : Promise.resolve({ songs: [song("a")], total: 1, has_more: false }) }), drawerProps({ view: "library", onPlay: (...args) => calls.push(args) }));
    await settle(); await h.click("▶ 顺序播放");
    if (action === "close") h.dispose(); else { h.props.view = "queue"; await settle(); }
    pending.resolve({ songs: [song("a")], total: 1, has_more: false }); await settle();
    assert.equal(calls.length, 0); if (action !== "close") h.dispose();
  }
});

test("顺序随机入口设置对应模式，单曲点播保留用户已有模式", async () => {
  for (const [label, mode] of [["▶ 顺序播放", "sequence"], ["⤨ 随机播放", "shuffle"], ["播放 歌曲a", undefined]]) {
    const calls = []; const h = mount(drawer(), drawerProps({ view: "library", onPlay: (...args) => calls.push(args) }));
    await settle(); await h.click(label);
    assert.equal(calls[0][2], "manual"); assert.equal(calls[0][3], mode); h.dispose();
  }
});

test("晚到旧搜索结果不覆盖新查询，错误与加载状态也不能污染新结果", async () => {
  const pending = deferred(); let count = 0;
  const h = mount(drawer({ getLocalLibrarySongs: () => ++count === 1 ? pending.promise : Promise.resolve({ songs: [song("new")], total: 1, has_more: false }) }), drawerProps({ view: "library" }));
  // 直接调用真实 form 提交处理，与输入框 v-model 保持同一生产链路。
  const input = h.all().find((item) => item.type === "input" && item.props["aria-label"] === "搜索本地曲库");
  input.props["onUpdate:modelValue"]("新查询");
  const form = h.all().find((item) => item.type === "form");
  form.props.onSubmit({ preventDefault() {} }); await settle();
  pending.resolve({ songs: [song("old")], total: 1, has_more: false }); await settle();
  assert.ok(h.findButton("播放 歌曲new")); assert.equal(h.findButton("播放 歌曲old"), undefined); h.dispose();
});


test("单曲菜单分开当前任务与长期反馈，手动曲库歌曲不显示任务意见", async () => {
  const actions = [];
  const SongItem = component("components/playlist/SongItem.vue", { "../../services/api": { getSongCover: async () => null } });
  const h = mount(SongItem, { song: { ...song("task"), batch_id: "batch" }, index: 0, current: false, playing: false, expanded: false, allowReview: true, onFeedback: a => actions.push(a) });
  await h.click("更多操作 歌曲task");
  assert.ok(h.findButton("喜欢")); assert.ok(h.findButton("减少推荐"));
  await h.click("这次不合适"); assert.deepEqual(actions, ["not_now"]);
  await h.click("更多操作 歌曲task"); await h.click("减少推荐"); assert.deepEqual(actions, ["not_now", "dislike"]);
  h.props.song = song("manual"); await settle(); await h.click("更多操作 歌曲manual");
  assert.equal(h.findButton("这次不合适"), undefined); h.dispose();
});

test("画像单独展示当前任务，撤销只调用该任务接口并重新读取", async () => {
  const calls = []; let removed = false;
  const item = { id: "1", title: "合成歌", artist: "合成艺人", genre: [], mood: [], reason: "当前任务后续避开", factor: 0, confidence: 1, source: "task_feedback", scope: "task", task_id: "task-1" };
  const makeProfile = () => ({
    explicit: { liked_songs: [], disliked_songs: [], boosted_artists: [], demoted_artists: [] },
    behavior: { boosted_songs: [], demoted_songs: [] }, summary: { plays: 0, finishes: 0, replays: 0, skips: 0 }, boundaries: {},
    acoustic: { total_songs: 0, analyzed_songs: 0, coverage_percent: 0, sample_hours: 0, recent_songs: [] },
    task_feedback: { task_id: "task-1", query: "睡前", songs: removed ? [] : [item] },
  });
  const Drawer = component("components/taste/TasteProfileDrawer.vue", { "../../services/api": {
    getTasteProfile: async () => makeProfile(),
    removeTaskFeedback: async (...args) => { calls.push(args); removed = true; },
    removeTasteFeedback: () => { throw new Error("不应撤销长期喜欢"); },
    removeTasteBehavior: () => { throw new Error("不应删除收听事实"); },
  } });
  const h = mount(Drawer, { sessionId: "s" }); await settle();
  assert.ok(h.all().some(n => n.props["aria-label"] === "当前任务不合适"));
  await h.click("撤销这次意见"); assert.deepEqual(calls, [["s", "task-1", "1"]]);
  assert.equal(h.findButton("撤销这次意见"), undefined); h.dispose();
});

test("长期场景偏好显示原话与范围，撤销不碰单曲喜欢或收听事实", async () => {
  const calls = []; let removed = false;
  const memory = { id: 'memory-1', scene: '睡前', song: { title: '合成歌曲', artist: '测试艺人', album: '合成专辑' }, source_text: '以后睡前不要推荐这首歌', effective: true, reason: '只在睡前选歌时避开，其他场景不受影响。' };
  const makeProfile = () => ({
    explicit: { liked_songs: [], disliked_songs: [], boosted_artists: [], demoted_artists: [] },
    behavior: { boosted_songs: [], demoted_songs: [] }, summary: {}, boundaries: {},
    acoustic: { total_songs: 0, analyzed_songs: 0, coverage_percent: 0, sample_hours: 0, recent_songs: [] },
    scene_memories: removed ? [] : [memory],
  });
  const Drawer = component('components/taste/TasteProfileDrawer.vue', { '../../services/api': {
    getTasteProfile: async () => makeProfile(),
    removeSceneMemory: async (...args) => { calls.push(args); removed = true; },
    removeTasteFeedback: () => { throw new Error('不能撤销单曲喜欢'); },
    removeTasteBehavior: () => { throw new Error('不能删除收听事实'); },
  } });
  const h = mount(Drawer, { sessionId: 's' }); await settle();
  assert.ok(h.all().some(n => n.props['aria-label'] === '长期场景偏好'));
  await h.click('撤销这条场景偏好');
  assert.deepEqual(calls, [['s', 'memory-1']]);
  assert.equal(h.findButton('撤销这条场景偏好'), undefined);
  assert.ok(!h.all().some(n => n.props['aria-label'] === '长期场景偏好'));
  h.dispose();
});

test("场景偏好撤销失败不假称消失，显示错误并可重新读取", async () => {
  const Drawer = component('components/taste/TasteProfileDrawer.vue', { '../../services/api': {
    getTasteProfile: async () => ({
      explicit: { liked_songs: [], disliked_songs: [], boosted_artists: [], demoted_artists: [] },
      behavior: { boosted_songs: [], demoted_songs: [] }, summary: {}, boundaries: {},
      acoustic: { total_songs: 0, analyzed_songs: 0, coverage_percent: 0, sample_hours: 0, recent_songs: [] },
      scene_memories: [{ id: 'm', scene: '睡前', song: { title: '合成歌', artist: '合成艺人' }, source_text: '合成意见', effective: false, reason: '作品版本变化，暂停使用' }],
    }),
    removeSceneMemory: async () => { throw new Error('合成撤销失败'); },
  } });
  const h = mount(Drawer, { sessionId: 's' }); await settle();
  await h.click('撤销这条场景偏好');
  assert.ok(h.findButton('再试一次'));
  await h.click('再试一次');
  assert.ok(h.findButton('撤销这条场景偏好'));
  h.dispose();
});

const memoryItem = () => ({ id: 'memory-old', kind: 'genre', scene: '工作专注', value: 'prefer', value_label: '优先一些',
  label: '纯音乐', target_key: '纯音乐', source_text: '工作时我更喜欢纯音乐', target: { label: '纯音乐' }, effective: true, version: 1, reason: '工作偏好' });
const memoryPanel = (api) => component('components/taste/MemoryPreferences.vue', { '../../services/api': api });

test('明确偏好编辑携带旧记录标识，保存后重新读取且不自行重试', async () => {
  const calls = []; let changed = 0;
  const Panel = memoryPanel({ saveMemory: async (...args) => calls.push(args) });
  const h = mount(Panel, { sessionId: 's', items: [memoryItem()], onChanged: () => changed++ });
  await h.click('修改这条偏好');
  const choice = h.all().filter(n => n.type === 'select').at(-1);
  choice.props['onUpdate:modelValue']('reduce'); await settle();
  const form = h.all().find(n => n.type === 'form');
  await form.props.onSubmit({ preventDefault() {} }); await settle();
  assert.equal(calls.length, 1); assert.equal(calls[0][0], 's');
  assert.equal(calls[0][1].value, 'reduce'); assert.equal(calls[0][1].expected_id, 'memory-old');
  assert.equal(changed, 1); assert.equal(h.findButton('保存长期偏好'), undefined); h.dispose();
});

test('明确偏好保存失败保留编辑内容和错误，迟到保存不更新已关闭面板', async () => {
  const pending = deferred(); let changed = 0;
  let failing = true;
  const Panel = memoryPanel({ saveMemory: async () => { if (failing) throw new Error('合成写入失败'); return pending.promise; } });
  const h = mount(Panel, { sessionId: 's', items: [memoryItem()], onChanged: () => changed++ });
  await h.click('修改这条偏好');
  let form = h.all().find(n => n.type === 'form');
  await form.props.onSubmit({ preventDefault() {} }); await settle();
  assert.equal(changed, 0); assert.ok(h.findButton('保存长期偏好'));
  assert.ok(h.all().some(n => n.props.role === 'alert' && h.text(n).includes('合成写入失败')));
  failing = false; form = h.all().find(n => n.type === 'form');
  form.props.onSubmit({ preventDefault() {} }); await settle(); h.dispose();
  pending.resolve(); await settle(); assert.equal(changed, 0);
});

test('偏好撤销失败不隐藏原记录，不删除收听历史或单曲按钮反馈', async () => {
  let changed = 0; let calls = 0;
  const Panel = memoryPanel({ removeMemory: async () => { calls++; throw new Error('合成撤销失败'); } });
  const h = mount(Panel, { sessionId: 's', items: [memoryItem()], onChanged: () => changed++ });
  await h.click('撤销这条偏好');
  assert.equal(calls, 1); assert.equal(changed, 0); assert.ok(h.findButton('撤销这条偏好'));
  assert.ok(h.all().some(n => n.props.role === 'alert')); h.dispose();
});

test('参考意见菜单显示真实两方标题，独立于全局单曲喜欢', async () => {
  const events = [];
  const Item = component('components/playlist/SongItem.vue', { '../../services/api': { getSongCover: async () => '' } });
  const h = mount(Item, { song: { ...song('2'), batch_id: 'batch', reference_song_id: '1', reference_title: '参考合成曲' }, index: 0,
    current: false, playing: false, expanded: false, onFeedback: value => events.push(value) });
  await h.click('队列操作 歌曲2'); await h.click('不像《参考合成曲》');
  assert.deepEqual(events, ['reference_unlike']); h.dispose();
});

test('开场优先的等待、缺失与略过原因在节目折叠外可见',async()=>{
  const h=mount(component('components/chat/ChatDrawer.vue'),{messages:[],loading:false,
    djProgram:{batch_id:'b',program_version:'dj-segments-v1',opening_priority:true,opening_status:'missing',
      copy_status:'partial',status:'ready',sources:[],segments:[],text:'',mode:'metadata'},
    djPlaybackNotices:{b:'当前曲间介绍因手动切歌已略过。'}});
  const visible=h.all().filter(n=>n.type==='p'&&n.props.role==='status'&&n.parent?.type!=='details').map(h.text).join(' ');
  assert.match(visible,/没有通过校验的开场/);assert.match(visible,/手动切歌已略过/);
  h.props.djPlaybackNotices={b:'本轮没有通过校验的开场，保留的介绍安排在后续自然曲间。'};
  await settle();
  const duplicate=h.all().filter(n=>n.type==='p'&&n.props.role==='status'&&h.text(n).includes('没有通过校验的开场'));
  assert.equal(duplicate.length,1);
  h.props.djProgram={...h.props.djProgram,status:'pending',copy_status:'writing',opening_status:'pending'};
  await settle();assert.match(h.text(h.all()[0]),/最多等待45秒/);h.dispose();
});

test('参考曲开场就绪后，内容检查只提示曲间介绍未采用', () => {
  const h = mount(component('components/chat/ChatDrawer.vue'), {
    messages: [], loading: false,
    djProgram: {batch_id:'b', program_version:'dj-segments-v1', opening_priority:true, opening_status:'ready',
      copy_status:'partial', copy_source:'reference_evidence', copy_reasons:['unsupported_claim','invalid_source'],
      status:'ready', sources:[], segments:[{id:'a', position:0, text:'参考合成曲的开场。'}], mode:'metadata'},
    djPlaybackNotices:{b:'本段 DJ 播报已结束，音乐继续。'},
  });
  const rendered = h.text(h.all()[0]);
  assert.match(rendered, /本段 DJ 播报已结束/);
  assert.match(rendered, /曲间介绍未采用/);
  assert.doesNotMatch(rendered, /部分内容未通过作品事实或听众处境检查/);
  h.dispose();
});
