import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { runInNewContext } from "node:vm";
import { test } from "node:test";
import { PerspectiveCamera, Vector3 } from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";

const require = createRequire(import.meta.url), ts = require("typescript");
const source = ts.transpileModule(readFileSync(new URL("../src/components/scene/musicRain/orbit.ts", import.meta.url), "utf8"), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText;

class Surface extends EventTarget {
  listeners = new Map();
  addEventListener(type, fn, options) {
    if (!this.listeners.has(type)) this.listeners.set(type, new Set());
    this.listeners.get(type).add(fn); super.addEventListener(type, fn, options);
  }
  removeEventListener(type, fn, options) { this.listeners.get(type)?.delete(fn); super.removeEventListener(type, fn, options); }
  count(type) { return this.listeners.get(type)?.size || 0; }
}
function harness() {
  const view = new Surface(), page = Object.assign(new Surface(), { hidden: false, defaultView: view });
  const captures = new Set();
  const canvas = Object.assign(new Surface(), {
    style: {}, ownerDocument: page, clientWidth: 800, clientHeight: 600,
    getRootNode: () => page, getBoundingClientRect: () => ({ left: 0, top: 0, width: 800, height: 600 }),
    setPointerCapture: id => captures.add(id), hasPointerCapture: id => captures.has(id), releasePointerCapture: id => captures.delete(id),
  });
  const camera = new PerspectiveCamera(35, 4 / 3, 0.1, 110);
  camera.position.set(0, 3.6, 16.6); camera.lookAt(0, 3.6, 0.26);
  let invalidations = 0;
  const exports = {};
  // 原生事件表面代替浏览器DOM；相机与OrbitControls均使用已安装的真实实现。
  runInNewContext(source, { exports, require: () => ({ OrbitControls }) });
  const orbit = exports.createRainOrbit(canvas, camera, () => invalidations++);
  function emit(target, type, options = {}) {
    const event = Object.assign(new Event(type, { cancelable: true }), {
      pointerId: 1, pointerType: "mouse", button: 0, clientX: 200, clientY: 200,
      pageX: 200, pageY: 200, ctrlKey: false, shiftKey: false, metaKey: false, ...options,
    });
    target.dispatchEvent(event); return event;
  }
  return { orbit, camera, canvas, page, view, emit, captures, get invalidations() { return invalidations; } };
}
const target = new Vector3(0, 3.6, 0.26);

test("圣诞默认机位俯视岸边，切换保留已拖动的镜头，复位返回当前默认机位", () => {
  const h = harness(), rainHome = h.camera.position.clone();
  h.orbit.frameChristmas(true);
  const christmasHome = h.camera.position.clone();
  assert.ok(h.camera.getWorldDirection(new Vector3()).y < -.06);
  h.orbit.frameChristmas(true);
  assert.ok(h.camera.position.distanceTo(christmasHome) < 1e-8);
  h.orbit.setQuiet(true);
  h.emit(h.canvas, "pointerdown");
  h.emit(h.page, "pointermove", { clientX: 370, clientY: 243 });
  h.emit(h.page, "pointerup");
  const custom = h.camera.position.clone();
  h.orbit.frameChristmas(false);
  assert.ok(h.camera.position.distanceTo(custom) < 1e-8);
  h.orbit.reset();
  assert.ok(h.camera.position.distanceTo(rainHome) < 1e-8);
  h.orbit.frameChristmas(true);
  assert.ok(h.camera.position.distanceTo(christmasHome) < 1e-8);
  h.orbit.dispose();
});

test("从拖动或缩放过的其他天气切到圣诞夜，首帧构图与复位一致", () => {
  for (const [width, height] of [[1440, 900], [915, 625], [390, 844]]) {
    const h = harness();
    const clean = harness();
    const pose = camera => [...camera.position.toArray(), ...camera.quaternion.toArray(), camera.fov];
    const closeTo = (actual, expected) => {
      assert.ok(actual.every((value, index) => Math.abs(value - expected[index]) < 1e-8),
        `${width}×${height}: ${actual} != ${expected}`);
    };
    clean.orbit.frameChristmas(true, width, height);
    const home = pose(clean.camera);
    h.emit(h.canvas, "pointerdown");
    h.emit(h.page, "pointermove", { clientX: 380, clientY: 250 });
    h.emit(h.page, "pointerup");
    h.emit(h.canvas, "wheel", { deltaY: -120, deltaMode: 0 });
    h.orbit.frameChristmas(true, width, height);
    h.orbit.update();
    closeTo(pose(h.camera), home);
    h.orbit.reset();
    closeTo(pose(h.camera), home);
    h.orbit.dispose();
    clean.orbit.dispose();
  }
});

test("圣诞短窗口使用独立机位，树顶留在左上方、台阶留在右下方", () => {
  const h = harness();
  h.orbit.frameChristmas(true, 390, 844);
  assert.ok(h.camera.position.distanceTo(new Vector3(13.6, 9, 18.25)) < 1e-8);
  assert.equal(h.camera.fov, 54);
  h.orbit.frameChristmas(true, 915, 625);
  h.camera.aspect = 915 / 625;
  h.camera.updateProjectionMatrix();
  h.camera.updateMatrixWorld();
  const treeStar = new Vector3(-2.55, 9.3, -2.45).project(h.camera);
  const porchSteps = new Vector3(3.7, .28, 1).project(h.camera);
  assert.ok(treeStar.x < -.25 && treeStar.y > .65 && treeStar.y < .98);
  assert.ok(porchSteps.x > 0 && porchSteps.y < -.5 && porchSteps.y > -.85);
  h.orbit.frameChristmas(false);
  assert.equal(h.camera.fov, 35);
  assert.ok(h.camera.position.distanceTo(new Vector3(0, 3.6, 16.6)) < 1e-8);
  h.orbit.dispose();
});

test("真实OrbitControls拖动、有限惯性和角度限制生效，双击精确复位且不残留惯性", () => {
  const h = harness(), home = h.camera.position.clone();
  h.emit(h.canvas, "pointerdown");
  h.emit(h.page, "pointermove", { clientX: 480, clientY: 280 });
  h.emit(h.page, "pointerup");
  const released = h.camera.position.clone();
  assert.ok(released.distanceTo(home) > 0.1);
  h.orbit.update(); assert.ok(h.camera.position.distanceTo(released) > 0.001);
  for (let i = 0; i < 200; i++) h.orbit.update();
  assert.equal(h.orbit.update(), false);
  const offset = h.camera.position.clone().sub(target);
  assert.ok(Math.abs(Math.atan2(offset.x, offset.z)) <= Math.PI / 3 + 1e-6);
  h.emit(h.canvas, "dblclick");
  for (let i = 0; i < 30; i++) h.orbit.update();
  assert.ok(h.camera.position.distanceTo(home) < 1e-8);
  assert.ok(h.invalidations > 0); h.orbit.dispose();
});

test("滚轮距离限制、双指缩放与静谧下直接旋转生效", () => {
  const h = harness();
  for (let i = 0; i < 200; i++) h.emit(h.canvas, "wheel", { deltaY: -120, deltaMode: 0 });
  assert.ok(Math.abs(h.camera.position.distanceTo(target) - 9) < 1e-6);
  for (let i = 0; i < 200; i++) h.emit(h.canvas, "wheel", { deltaY: 120, deltaMode: 0 });
  assert.ok(Math.abs(h.camera.position.distanceTo(target) - 30) < 1e-6);
  h.orbit.reset();
  const distance = h.camera.position.distanceTo(target);
  h.emit(h.canvas, "pointerdown", { pointerType: "touch", pointerId: 1, pageX: 100 });
  h.emit(h.canvas, "pointerdown", { pointerType: "touch", pointerId: 2, pageX: 200 });
  h.emit(h.page, "pointermove", { pointerType: "touch", pointerId: 2, pageX: 400 });
  assert.ok(h.camera.position.distanceTo(target) < distance);
  h.emit(h.page, "pointerup", { pointerType: "touch", pointerId: 2 });
  h.emit(h.page, "pointerup", { pointerType: "touch", pointerId: 1 });
  h.orbit.setQuiet(true);
  h.emit(h.canvas, "pointerdown");
  h.emit(h.page, "pointermove", { clientX: 350, clientY: 220 });
  h.emit(h.page, "pointerup");
  assert.equal(h.orbit.update(), false);
  h.orbit.dispose();
});

test("中断拖动释放指针和文档监听，隐藏时不接收手势，恢复不重复绑定", () => {
  const h = harness();
  h.emit(h.canvas, "pointerdown"); assert.equal(h.captures.size, 1);
  h.view.dispatchEvent(new Event("blur"));
  assert.equal(h.captures.size, 0); assert.equal(h.page.count("pointermove"), 0);
  assert.equal(h.canvas.count("pointerdown"), 2);
  h.page.hidden = true; h.page.dispatchEvent(new Event("visibilitychange"));
  const before = h.camera.position.clone();
  h.emit(h.canvas, "wheel", { deltaY: -120, deltaMode: 0 });
  assert.ok(h.camera.position.equals(before));
  h.page.hidden = false; h.page.dispatchEvent(new Event("visibilitychange"));
  assert.equal(h.canvas.count("wheel"), 1);
  h.emit(h.canvas, "pointerdown"); h.orbit.dispose(); h.orbit.dispose();
  assert.equal(h.captures.size, 0);
  for (const surface of [h.canvas, h.page, h.view]) {
    assert.equal([...surface.listeners.values()].reduce((sum, set) => sum + set.size, 0), 0);
  }
});
