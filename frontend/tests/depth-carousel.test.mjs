import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { runInNewContext } from "node:vm";
import { test } from "node:test";
import { mount, settle, vue, component } from "./helpers/vue-host.mjs";
const require = createRequire(import.meta.url);
const { parse, compileScript } = require("@vue/compiler-sfc");
const ts = require("typescript");
const { descriptor } = parse(readFileSync(new URL("../src/components/scene/DepthCoverCarousel.vue", import.meta.url), "utf8"));
const compiled = ts.transpileModule(compileScript(descriptor, { id: "depth-regression", inlineTemplate: true }).content,
  { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
const exports = {};
// 宿主不实现浏览器过渡；保留真实组件响应性和事件，动效另在实际浏览器核对。
runInNewContext(compiled, { exports, require: id => id === "vue" ? { ...vue,
  TransitionGroup: { setup: (_, { slots }) => () => vue.h("div", slots.default?.()) } }
  : id.endsWith(".vue") ? { default: { props: ["song"], render: () => null } } : require(id) });
const song = id => ({ id: String(id), title: `合成曲${id}`, artist: "合成艺人" });
const queue = Array.from({ length: 5000 }, (_, i) => song(i));
const figures = h => h.all().filter(n => n.type === "figure");
const active = h => figures(h).find(n => n.props["data-offset"] === 0)?.props["data-song-id"];

test("长队列有界渲染，焦点只跟随真实选曲、跳选和重排", async () => {
  const h = mount(exports.default, { currentSong: queue[2500], queue, canPrevious: true, canNext: true, quiet: false });
  assert.ok(figures(h).length <= 5); assert.equal(active(h), "2500");
  assert.deepEqual(figures(h).map(n => n.props["data-offset"]), [-2, -1, 0, 1, 2]);
  h.props.currentSong = queue[4999]; await settle();
  assert.equal(active(h), "4999"); assert.equal(figures(h).length, 3);
  h.props.queue = [queue[4999], queue[1], queue[2]]; await settle();
  assert.equal(active(h), "4999"); assert.equal(figures(h).length, 3);
  h.dispose();
});

test("箭头只发切歌意图，未收到播放器新状态前不能自己翻页", async () => {
  let next = 0, previous = 0;
  const h = mount(exports.default, { currentSong: queue[1], queue: queue.slice(0, 4), canPrevious: true, canNext: true,
    quiet: false, onNext: () => next++, onPrevious: () => previous++ });
  await h.click("下一首"); await h.click("下一首"); await h.click("上一首");
  assert.equal(next, 2); assert.equal(previous, 1); assert.equal(active(h), "1");
  h.props.currentSong = queue[2]; await settle(); assert.equal(active(h), "2"); h.dispose();
});

test("空队列、单曲和队外选曲不克隆封面，键盘尊重首尾边界", async () => {
  let changes = 0;
  const h = mount(exports.default, { currentSong: null, queue: [], canPrevious: false, canNext: false, quiet: true,
    onNext: () => changes++, onPrevious: () => changes++ });
  assert.equal(figures(h).length, 0); assert.equal(h.button("下一首"), undefined);
  h.props.currentSong = song("outside"); await settle();
  assert.equal(figures(h).length, 1); assert.equal(active(h), "outside");
  assert.equal(h.button("下一首").props.disabled, true);
  const region = h.all().find(n => n.type === "section");
  for (const key of ["ArrowLeft", "ArrowRight"]) region.props.onKeydown({ key, preventDefault() {} });
  assert.equal(changes, 0);
  h.props.canNext = true; await settle();
  region.props.onKeydown({ key: "ArrowRight", preventDefault() {} }); assert.equal(changes, 1);
  region.props.onKeydown({ key: "ArrowRight", ctrlKey: true, preventDefault() {} }); assert.equal(changes, 1);
  h.dispose();
});

test("旧封面迟到不覆盖新作品，破图仍显示歌曲占位", async () => {
  const pending = new Map();
  const Card = component("components/scene/DepthCoverCard.vue", { "../../services/api": {
    getSongCover: id => new Promise(resolve => pending.set(id, resolve)),
  } });
  const h = mount(Card, { song: song("a") });
  h.props.song = song("b"); await settle();
  pending.get("b")("cover-b"); await settle();
  pending.get("a")("cover-a"); await settle();
  const img = h.all().find(n => n.type === "img"); assert.equal(img.props.src, "cover-b");
  img.props.onError(); await settle();
  assert.equal(h.all().some(n => n.type === "img"), false);
  assert.ok(h.all().some(n => n.type === "strong" && h.text(n) === "合成曲b")); h.dispose();
});

test("元数据自带封面时不额外查接口，卸载使迟到请求失效", async () => {
  let calls = 0, resolve;
  const Card = component("components/scene/DepthCoverCard.vue", { "../../services/api": {
    getSongCover: () => { calls++; return new Promise(done => { resolve = done; }); },
  } });
  const h = mount(Card, { song: { ...song("a"), cover_url: "known-cover" } });
  assert.equal(calls, 0); assert.equal(h.all().find(n => n.type === "img").props.src, "known-cover");
  h.props.song = song("b"); await settle(); assert.equal(calls, 1);
  h.dispose(); resolve("late-cover"); await settle(); assert.equal(h.all().some(n => n.type === "img"), false);
});
