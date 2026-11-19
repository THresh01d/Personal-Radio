import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { runInNewContext } from "node:vm";
import { test } from "node:test";
import { mount, settle } from "./helpers/vue-host.mjs";
const require = createRequire(import.meta.url), ts = require("typescript");
const { parse, compileScript } = require("@vue/compiler-sfc");
const { descriptor } = parse(readFileSync(new URL("../src/components/player/PlayerGlass.vue", import.meta.url), "utf8"));
const compiled = ts.transpileModule(compileScript(descriptor, { id: "glass-regression", inlineTemplate: true,
  templateOptions: { compilerOptions: { hoistStatic: false } } }).content,
  { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;

function glass(userAgent = "Chrome/130", supports = true) {
  const observers = [];
  class Observer {
    constructor(callback) { this.callback = callback; observers.push(this); }
    observe() { this.observed = true; }
    disconnect() { this.disconnected = true; }
  }
  const exports = {};
  runInNewContext(compiled, { exports, require, navigator: { userAgent }, CSS: { supports: () => supports }, ResizeObserver: Observer });
  const h = mount(exports.default, {});
  const map = () => h.all().find(n => n.type === "feImage").props.href;
  const resize = async (width, height) => { observers[0].callback([{ contentRect: { width, height } }]); await settle(); };
  return { ...h, map, resize, observers };
}

test("玻璃背景随尺寸更新，忽略隐藏/异常尺寸，卸载后不接收迟到观察", async () => {
  const h = glass(); const original = h.map();
  assert.ok(h.observers[0].observed);
  await h.resize(950, 76); assert.notEqual(h.map(), original);
  const resized = h.map();
  for (const dimensions of [[0, 0], [NaN, 76], [Infinity, 76], [-1, 76]]) { await h.resize(...dimensions); assert.equal(h.map(), resized); }
  assert.ok(decodeURIComponent(resized).includes('width="950" height="76"'));
  h.dispose(); assert.ok(h.observers[0].disconnected); await h.resize(500, 80);
  assert.equal(h.all().some(n => n.type === "feImage"), false);
});

test("浏览器能力决定折射增强，其他浏览器保留装饰层及磨砂回退", async () => {
  for (const [ua, supports, enabled] of [["Chrome/130", true, true], ["Firefox/130", true, false], ["Version/18 Safari", true, false], ["Chrome/130", false, false]]) {
    const h = glass(ua, supports);
    await settle();
    const surface = h.all().find(n => n.props["aria-hidden"] === "true");
    assert.equal(surface.props.class.includes("refractive"), enabled);
    assert.equal(h.all().filter(n => n.type === "button").length, 0);
    h.dispose();
  }
});
