// 执行生产 Vue 组件；仅宿主节点替身，不用模板字符串检查代替点击交互。
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { runInNewContext } from "node:vm";
import { panelFocusModule } from "./panel-focus-module.mjs";
const require = createRequire(import.meta.url);
export const vue = require("vue");
const { parse, compileScript } = require("@vue/compiler-sfc");
const ts = require("typescript");
globalThis.Document = class extends EventTarget {};
globalThis.ShadowRoot = class extends EventTarget {};
export const settle = async () => { for (let i = 0; i < 18; i++) await Promise.resolve(); await vue.nextTick(); };
export function component(path, dependencies, runtime = {}) {
  const { descriptor } = parse(readFileSync(new URL(`../../src/${path}`, import.meta.url), "utf8"));
  const compiled = ts.transpileModule(compileScript(descriptor, { id: path, inlineTemplate: true }).content, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  const exports = {};
  const document = new EventTarget();
  runInNewContext(compiled, { exports, Error, AbortController, document, ...runtime, require: id => {
    if (id === "vue") return vue;
    if (id === "../../composables/usePanelFocus") return panelFocusModule(vue, document);
    if (id === "../player/PlayerGlass.vue") return { default: () => null };
    if (id in dependencies) return dependencies[id];
    throw new Error(`Unexpected dependency ${id}`);
  } });
  return exports.default;
}
export function mount(Component, initialProps) {
  const hostDocument = new Document();
  const node = (type, text = "") => Object.assign(new EventTarget(), { type, text, children: [], props: {}, parent: null, value: "", getRootNode: () => hostDocument, focus() {} });
  const renderer = vue.createRenderer({
    createElement: type => node(type), createText: text => node("text", text), createComment: text => node("comment", text),
    insert(child, parent, anchor = null) { if (child.parent) child.parent.children.splice(child.parent.children.indexOf(child), 1); const i = anchor ? parent.children.indexOf(anchor) : -1; parent.children.splice(i < 0 ? parent.children.length : i, 0, child); child.parent = parent; },
    remove(child) { if (child.parent) child.parent.children.splice(child.parent.children.indexOf(child), 1); child.parent = null; },
    setElementText(item, text) { item.text = text; item.children = []; }, setText(item, text) { item.text = text; }, setComment(item, text) { item.text = text; },
    parentNode: item => item.parent, nextSibling: item => item.parent?.children[item.parent.children.indexOf(item) + 1] || null,
    patchProp(item, key, old, value) { item.props[key] = value; },
  });
  const props = vue.reactive(initialProps); const root = node("root");
  const app = renderer.createApp({ render: () => vue.h(Component, props) }); app.mount(root);
  const all = () => { const result = []; const walk = item => { result.push(item); item.children.forEach(walk); }; walk(root); return result; };
  const text = item => item.text + item.children.map(text).join("");
  const button = label => all().find(n => n.type === "button" && (n.props["aria-label"] === label || text(n).trim() === label));
  return { props, all, text, button, async click(label) { const n = button(label); assert.ok(n, `Missing ${label}`); assert.ok(!n.props.disabled, `Disabled ${label}`); n.props.onClick?.({}); await settle(); }, dispose: () => app.unmount() };
}
