import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { test } from "node:test";
import { runInNewContext } from "node:vm";
import { component, mount, settle } from "./helpers/vue-host.mjs";

const require = createRequire(import.meta.url);
const { parse, compileScript } = require("@vue/compiler-sfc");
const { createSSRApp } = require("vue");
const { renderToString } = require("@vue/server-renderer");
const ts = require("typescript");
// 编译真实组件验证可访问性契约，不用另写一个只在测试中存在的播放器模板。
const source = readFileSync(new URL("../src/components/player/PlayerDock.vue", import.meta.url), "utf8");
const { descriptor } = parse(source);
const compiled = ts.transpileModule(compileScript(descriptor, {
  id: "player-dock-regression", inlineTemplate: true,
  templateOptions: { compilerOptions: { hoistStatic: false } },
}).content, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText;
const exports = {};
const PlayerGlass = component("components/player/PlayerGlass.vue", {});
runInNewContext(compiled, { exports, document: new EventTarget(), require: id => id === "./PlayerGlass.vue" ? { default: PlayerGlass } : ["./VoiceHealthPanel.vue", "./HotkeySettings.vue"].includes(id) ? { default: () => null } : require(id) });
const render = (overrides = {}) => renderToString(createSSRApp(exports.default, {
  currentSong: { id: "test", title: "测试歌曲", artist: "测试歌手" },
  isPlaying: false, loading: false, error: "", status: "STANDBY",
  progress: "0%", progressValue: 0, elapsed: 0, duration: 0,
  volume: 0.8, voiceVolume: 0.85, announceOn: false, autoPlayOn: true,
  acousticBackfillOn: false, announcing: false, visualMode: "atmosphere",
  analysisStatus: "idle", canPrevious: false, canNext: true,
  playbackMode: "sequence", smartContinueOn: true, queueSource: "manual", ...overrides,
}));

test("失败原因独立持续显示，重试按钮与原因建立可访问性关联", async () => {
  const html = await render({ error: "当前歌曲无播放权限", status: "SIGNAL LOST" });
  assert.match(html, /<p[^>]*id="playback-error"[^>]*class="playback-notice"[^>]*role="status"[^>]*aria-live="polite"/);
  assert.match(html, /当前歌曲无播放权限<\/p>/);
  assert.match(html, /aria-describedby="playback-error"/);
  assert.match(html, /aria-label="重试播放"/);
  // 旧 small 会被多个响应式断点隐藏；错误信息必须离开这条副标题链路。
  assert.doesNotMatch(html, /class="dock-error"/);
});

test("恢复播放后去掉旧错误，按钮仍保持正确的暂停语义", async () => {
  const html = await render({ isPlaying: true });
  assert.doesNotMatch(html, /id="playback-error"|aria-describedby="playback-error"/);
  assert.match(html, /aria-label="暂停"/);
});

test("加载中下一首仍可用，叠加播报保留播放键并提供独立跳过", async () => {
  const html = await render({ loading: true, announcing: true });
  assert.match(html, /aria-label="播放"/);
  assert.match(html, /aria-label="跳过 DJ 播报"/);
  assert.match(await render({ announcing: true, isPlaying: true }), /aria-label="暂停"/);
  assert.match(await render({ announcing: true, previewing: true }), /aria-label="跳过 DJ 试听"/);
  const nextButton = html.match(/<button[^>]*aria-label="下一首"[^>]*>/)?.[0];
  assert.ok(nextButton);
  assert.doesNotMatch(nextButton, /disabled/);
});

test("随机和单曲在播放控制左侧，选中态与真实模式一致且不再显示NOW PLAYING", async () => {
  for (const mode of ["sequence", "shuffle", "repeat-one"]) {
    const html = await render({ playbackMode: mode });
    assert.match(html, /aria-label="打开当前队列"/);
    assert.ok(html.indexOf('class="playback-modes"') < html.indexOf('class="transport"'));
    for (const [label, expected] of [["随机播放", mode === "shuffle"], ["单曲循环", mode === "repeat-one"]]) {
      const button = html.match(new RegExp(`<button[^>]*aria-label="${label}"[^>]*>`))?.[0];
      assert.ok(button?.includes(`aria-pressed="${expected}"`));
    }
    assert.doesNotMatch(await render({ status: "NOW PLAYING" }), /NOW PLAYING/);
  }
});

test("左侧模式按钮支持互斥选择和再次点击回到顺序，定位进度保留键盘与点击事件", async () => {
  const modes = [], seeks = [];
  const props = { currentSong: { id: "fixture", title: "合成歌", artist: "合成歌手" },
    playbackMode: "sequence", visualMode: "atmosphere", analysisStatus: "idle", volume: .8,
    progress: "40%", progressValue: 40, duration: 100, elapsed: 40,
    onUpdatePlaybackMode: mode => modes.push(mode), onSeekBy: seconds => seeks.push(seconds),
    onSeekRatio: ratio => seeks.push(ratio) };
  // 此处只验证真实播放器交互；光学子组件的浏览器能力/尺寸由独立玻璃测试覆盖。
  const interactive = {};
  runInNewContext(compiled, { exports: interactive, document: new EventTarget(),
    require: id => ["./PlayerGlass.vue", "./VoiceHealthPanel.vue", "./HotkeySettings.vue"].includes(id) ? { default: () => null } : require(id) });
  const h = mount(interactive.default, props);
  await h.click("随机播放"); assert.equal(modes.at(-1), "shuffle");
  h.props.playbackMode = "shuffle";
  await settle();
  await h.click("随机播放"); assert.equal(modes.at(-1), "sequence");
  await h.click("单曲循环"); assert.equal(modes.at(-1), "repeat-one");
  h.props.playbackMode = "repeat-one";
  await settle();
  await h.click("单曲循环"); assert.equal(modes.at(-1), "sequence");
  const slider = h.all().find(n => n.props["aria-label"] === "歌曲播放进度");
  slider.props.onKeydown({ key: "ArrowRight", preventDefault() {} });
  assert.equal(seeks.at(-1), 5);
  slider.props.onClick({ clientX: 160, currentTarget: { getBoundingClientRect: () => ({ left: 100, width: 200 }) } });
  assert.equal(seeks.at(-1), .3);
  h.dispose();
});

test("拖动进度先预览，松开只定位一次，取消或切歌不会定位", async () => {
  const seeks = [], captured = [];
  const interactive = {};
  runInNewContext(compiled, { exports: interactive, document: new EventTarget(),
    require: id => ["./PlayerGlass.vue", "./VoiceHealthPanel.vue", "./HotkeySettings.vue"].includes(id) ? { default: () => null } : require(id) });
  const h = mount(interactive.default, {
    currentSong: { id: "first", title: "合成歌", artist: "合成歌手" },
    playbackMode: "sequence", visualMode: "atmosphere", analysisStatus: "idle",
    volume: .8, progress: "40%", progressValue: 40, duration: 100,
    onSeekRatio: ratio => seeks.push(ratio),
  });
  const slider = h.all().find(node => node.props["aria-label"] === "歌曲播放进度");
  const target = { getBoundingClientRect: () => ({ left: 100, width: 200 }), setPointerCapture: id => captured.push(id) };
  const pointer = clientX => ({ clientX, currentTarget: target, isPrimary: true, button: 0, pointerId: 7, pointerType: "mouse" });
  slider.props.onPointerdown(pointer(160));
  slider.props.onPointermove(pointer(250));
  await settle();
  assert.equal(slider.props["aria-valuenow"], 75);
  assert.deepEqual(seeks, []);
  slider.props.onPointerup(pointer(350));
  slider.props.onClick(pointer(350));
  assert.deepEqual(seeks, [1]);
  slider.props.onPointerdown(pointer(160));
  slider.props.onPointercancel(pointer(160));
  slider.props.onPointerup(pointer(250));
  assert.deepEqual(seeks, [1]);
  slider.props.onPointerdown(pointer(160));
  h.props.currentSong = { id: "second", title: "另一首合成歌", artist: "合成歌手" };
  await settle();
  slider.props.onPointerup(pointer(250));
  assert.deepEqual(seeks, [1]);
  assert.deepEqual(captured, [7, 7, 7]);
  assert.equal(slider.props["aria-valuenow"], 40);
  h.dispose();
});


test("叠加播报的暂停继续与跳过按钮分别发出正确事件", async () => {
  const events=[];
  const interactive={};
  runInNewContext(compiled,{exports:interactive,document:new EventTarget(),
    require:id=>["./PlayerGlass.vue","./VoiceHealthPanel.vue","./HotkeySettings.vue"].includes(id)?{default:()=>null}:require(id)});
  const h=mount(interactive.default,{currentSong:{id:'test',title:'合成歌曲',artist:'合成艺人'},
    announcing:true,isPlaying:true,volume:.8,progress:'0%',progressValue:0,
    onToggle:()=>events.push('toggle'),onSkipAnnounce:()=>events.push('skip')});
  await h.click('暂停'); await h.click('跳过 DJ 播报');
  assert.deepEqual(events,['toggle','skip']);
  h.props.isPlaying=false; await settle(); await h.click('播放');
  assert.deepEqual(events,['toggle','skip','toggle']); h.dispose();
});
