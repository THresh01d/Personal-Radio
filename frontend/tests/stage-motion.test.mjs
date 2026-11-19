import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { test } from "node:test";
import { runInNewContext } from "node:vm";
const require = createRequire(import.meta.url), vue = require("vue"), ts = require("typescript");
const compiled = ts.transpileModule(readFileSync(new URL("../src/composables/useStageMotion.ts", import.meta.url), "utf8"), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText;
function harness() {
  const mounted = [], unmounted = [], frames = new Map(), document = Object.assign(new EventTarget(), { hidden: false });
  const media = Object.assign(new EventTarget(), { matches: false });
  const requested = vue.ref("immersive"), panel = vue.ref(false), scope = vue.effectScope();
  let id = 0, now = 0;
  const exports = {};
  runInNewContext(compiled, { exports, document, window: { matchMedia: () => media,
    requestAnimationFrame: fn => { frames.set(++id, fn); return id; }, cancelAnimationFrame: id => frames.delete(id) },
    require: () => ({ ...vue, onMounted: fn => mounted.push(fn), onBeforeUnmount: fn => unmounted.push(fn) }) });
  const motion = scope.run(() => exports.useStageMotion(requested, panel)); mounted.forEach(fn => fn());
  return { motion, requested, panel, media, document, frames, policy: exports.stageMode,
    tick(ms, count = 1) { for (let i = 0; i < count; i++) { now += ms; const callbacks = [...frames.values()]; frames.clear(); callbacks.forEach(fn => fn(now)); } },
    dispose() { unmounted.forEach(fn => fn()); scope.stop(); } };
}
test("三档保留用户偏好，面板或性能降级只限制沉浸档", () => {
  const h = harness();
  for (const mode of ["quiet", "atmosphere", "immersive"]) {
    assert.equal(h.policy(mode, false, false, false), mode);
    assert.equal(h.policy(mode, true, false, false), "quiet");
  }
  h.panel.value = true; assert.equal(h.motion.effectiveMode.value, "atmosphere");
  assert.equal(h.requested.value, "immersive"); h.panel.value = false;
  assert.equal(h.motion.effectiveMode.value, "immersive"); h.dispose();
});
test("系统减少动态效果实时生效，即使此前保存沉浸也保持静谧", () => {
  const h = harness(); const event = Object.assign(new Event("change"), { matches: true }); h.media.dispatchEvent(event);
  assert.equal(h.motion.effectiveMode.value, "quiet"); assert.match(h.motion.note.value, /系统/);
  h.media.dispatchEvent(Object.assign(new Event("change"), { matches: false }));
  assert.equal(h.motion.effectiveMode.value, "immersive"); h.dispose();
});
test("持续卡顿降级，短暂掉帧不触发，稳定恢复不振荡", () => {
  const h = harness(); h.tick(100); h.tick(16, 180);
  assert.equal(h.motion.constrained.value, false);
  h.tick(100, 65); assert.equal(h.motion.constrained.value, true);
  assert.match(h.motion.note.value, /卡顿/);
  h.tick(16, 190); assert.equal(h.motion.constrained.value, true);
  h.tick(16, 950); assert.equal(h.motion.constrained.value, false);
  assert.equal(h.requested.value, "immersive"); h.dispose();
});
test("隐藏和卸载不保留性能采样循环，恢复不把隐藏时间当卡顿", () => {
  const h = harness(); h.document.hidden = true; h.document.dispatchEvent(new Event("visibilitychange"));
  assert.equal(h.frames.size, 0); h.tick(10000);
  h.document.hidden = false; h.document.dispatchEvent(new Event("visibilitychange")); h.tick(16, 200);
  assert.equal(h.motion.constrained.value, false); h.dispose(); assert.equal(h.frames.size, 0);
});

test("首次建模的一次长停顿不误降档，随后连续慢帧仍会触发保护", () => {
  const h = harness();
  h.tick(16);
  h.tick(4000);
  assert.equal(h.motion.constrained.value, false);
  h.tick(16, 200);
  assert.equal(h.motion.constrained.value, false);
  h.tick(1050, 8);
  assert.equal(h.motion.constrained.value, true);
  h.dispose();
});
