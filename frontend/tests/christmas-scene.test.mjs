import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { runInNewContext } from "node:vm";
import { test } from "node:test";
import * as THREE from "three";
import ts from "typescript";

const sceneDir = new URL("../src/components/scene/musicRain/", import.meta.url);
const shadersDir = new URL("rainform/", sceneDir);
const publicScene = readFileSync(new URL("loader.ts", sceneDir), "utf8").includes("supportsSnowScene = false;");
const sceneTest = (title, fn) => test(title, { skip: publicScene && "公开版使用独立背景" }, fn);
const nodeRequire = createRequire(import.meta.url);

let canvasAllocations = 0;
class CanvasFixture {
  constructor(width, height) {
    Object.assign(this, { width, height });
    canvasAllocations++;
  }
  getContext() {
    // 程序纹理尺寸与资源生命周期用真实 Three 对象验证，像素由浏览器验收。
    return new Proxy({
      createImageData: (w, h) => ({ data: new Uint8ClampedArray(w * h * 4) }),
      createLinearGradient: () => ({ addColorStop() {} }),
      createRadialGradient: () => ({ addColorStop() {} }),
    }, { get: (target, field) => target[field] ?? (() => {}) });
  }
}

function compile(url, require) {
  const exports = {};
  const { outputText } = ts.transpileModule(readFileSync(url, "utf8"), {
    compilerOptions: { allowJs: true, module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  });
  runInNewContext(outputText, { exports, require, OffscreenCanvas: CanvasFixture });
  return exports;
}

const { rainQuality, stageWeather } = compile(new URL("signal.ts", sceneDir));
const frame = (changes = {}) => ({
  time: 0, deltaSeconds: 0, reactive: false, bands: new Float32Array(48),
  low: 0, mid: 0, high: 0, beat: 0, pauseProgress: null, ripple: null, ...changes,
});
let screen, camera, renderPasses = 0, rendererCount = 0;
class Renderer {
  constructor() { rendererCount++; }
  target = null;
  toneMapping = THREE.NoToneMapping;
  toneMappingExposure = 1;
  shadowMap = { enabled: false, needsUpdate: false };
  getRenderTarget() { return this.target; }
  setRenderTarget(value) { this.target = value; }
  setPixelRatio() {}
  setSize() {}
  clear() {}
  render(scene, view) {
    renderPasses++;
    if (!this.target) { screen = scene; camera = view; }
  }
  dispose() {}
}
const cache = new Map();
function load(url) {
  if (!cache.has(url.href)) cache.set(url.href, compile(url, id => id === "three"
    ? { ...THREE, WebGLRenderer: Renderer } : id.startsWith(".") ? load(new URL(id, url)) : nodeRequire(id)));
  return cache.get(url.href);
}
const winter = () => load(new URL("christmas.js", shadersDir));
let sharedLayer;
const village = () => sharedLayer ??= winter().createChristmasLayer();
const pointLayers = layer => {
  const points = [];
  layer.root.traverse(node => { if (node.isPoints) points.push(node); });
  return points;
};

test("舞台背景读取保留雨雪旧值并使未知值回到雨夜", () => {
  for (const value of ["rain", "snow", "christmas"]) assert.equal(stageWeather(value), value);
  for (const value of [null, undefined, "winter", 123]) assert.equal(stageWeather(value), "rain");
});

sceneTest("四档分别限制飘雪与整场点数，并保留每层的点", () => {
  const layer = village();
  const cases = [
    [1912, false, "immersive", 40503, 2700],
    [1912, false, "atmosphere", 23310, 1920],
    [390, false, "immersive", 10690, 1120],
    [390, true, "immersive", 1870, 500],
  ];
  const points = pointLayers(layer);
  assert.equal(points.length, 5);
  const snowfall = layer.root.getObjectByName("christmas-snowfall");
  for (const [width, limited, mode, expected, snowCount] of cases) {
    layer.resize(844, 1, rainQuality(width, limited, mode).particles);
    assert.equal(points.reduce((sum, p) => sum + p.geometry.drawRange.count, 0), expected);
    assert.equal(snowfall.geometry.drawRange.count, snowCount);
    for (const point of points) assert.ok(point.geometry.drawRange.count > 0);
  }
});

sceneTest("同一种子保留实体枝路灯点和雪晶位置，resize 只改预算", () => {
  const first = village(), second = winter().createChristmasLayer();
  const otherPoints = pointLayers(second);
  pointLayers(first).forEach((point, i) => {
    const attribute = point.geometry.attributes.position;
    assert.deepEqual(attribute.array, otherPoints[i].geometry.attributes.position.array);
    first.resize(844, 1.5, 3360);
    assert.strictEqual(point.geometry.attributes.position, attribute);
  });
  assert.equal(first.root.scale.x, 1);
});

sceneTest("保留立体屋身、实心树枝和多层空间，静态合批降低可提交对象数", () => {
  const layer = village(), root = layer.root;
  const tree = root.getObjectByName("silver-christmas-tree");
  const cabin = root.getObjectByName("winter-cabin");
  assert.ok(tree && cabin && tree.position.x < cabin.position.x);
  assert.ok(tree.children.filter(node => node.isInstancedMesh).length >= 3);
  const bounds = new THREE.Box3().setFromObject(cabin);
  assert.ok(bounds.max.z - bounds.min.z > 4);
  let opaqueHouseMeshes = 0;
  cabin.traverse(node => { if (node.isMesh && !node.material.transparent) opaqueHouseMeshes++; });
  assert.ok(opaqueHouseMeshes < 110 && opaqueHouseMeshes > 10, opaqueHouseMeshes);
});

sceneTest("场景与纹理首次进入才建，20 次切换共用原画布、相机和圣诞资源", () => {
  const allocations = canvasAllocations, renderers = rendererCount;
  const core = load(new URL("rendering.js", shadersDir)).createRainformCore({});
  core.resize(1440, 900, 1, 15360);
  core.render(frame());
  assert.equal(canvasAllocations, allocations);
  const sameScene = screen, sameCamera = camera;
  const rainFog = screen.fog;
  core.setWeather("christmas");
  core.render(frame());
  const root = screen.getObjectByName("christmas-night");
  const allocationsAfterEnter = canvasAllocations;
  assert.ok(allocationsAfterEnter > allocations);
  let releases = 0;
  const points = root.getObjectByName("winter-tree-lights");
  points.geometry.addEventListener("dispose", () => releases++);
  for (let i = 0; i < 20; i++) {
    core.setWeather("christmas");
    assert.equal(screen.children.length, 2);
    const passes = renderPasses;
    core.render(frame());
    assert.equal(renderPasses, passes + 1);
    assert.equal(screen, sameScene);
    assert.equal(camera, sameCamera);
    assert.equal(screen.children[0].visible, false);
    core.setWeather(i % 2 ? "rain" : "snow");
    core.render(frame());
    assert.equal(screen.fog, rainFog);
    assert.equal(screen.background, null);
    assert.equal(root.visible, false);
    assert.equal(screen.children[0].visible, true);
    assert.equal(screen.getObjectByName("christmas-night"), root);
    core.setWeather("christmas");
  }
  assert.equal(releases, 0);
  assert.equal(canvasAllocations, allocationsAfterEnter);
  assert.equal(rendererCount, renderers + 1);
  core.dispose();
  core.dispose();
  assert.equal(releases, 1);
});

sceneTest("销毁遍历批次与静态阴影，所有绘制纹理不超过 512 且只释放一次", () => {
  const core = load(new URL("rendering.js", shadersDir)).createRainformCore({});
  core.resize(390, 844, 1, 3360);
  core.setWeather("christmas");
  core.render(frame());
  const root = screen.getObjectByName("christmas-night");
  const resources = new Set();
  root.traverse(node => {
    if (node.isInstancedMesh) resources.add(node);
    if (node.geometry) resources.add(node.geometry);
    if (node.material) {
      for (const material of Array.isArray(node.material) ? node.material : [node.material]) {
        resources.add(material);
        for (const value of Object.values(material)) if (value?.isTexture) resources.add(value);
        for (const value of Object.values(material.uniforms ?? {})) if (value.value?.isTexture) resources.add(value.value);
      }
    }
  });
  const shadow = root.children.find(node => node.isDirectionalLight).shadow;
  shadow.map = new THREE.WebGLRenderTarget(512, 512);
  resources.add(shadow.map);
  const hits = new Map();
  for (const item of resources) {
    if (item.isTexture) assert.ok(item.image.width <= 512 && item.image.height <= 512);
    item.addEventListener("dispose", () => hits.set(item, (hits.get(item) ?? 0) + 1));
  }
  core.dispose();
  core.dispose();
  assert.ok(resources.size > 100);
  assert.equal(hits.size, resources.size);
  assert.ok([...hits.values()].every(value => value === 1));
});

sceneTest("低、中、高频各走自己的通道；无频谱与暂停恢复静态、停止雪晶时间", () => {
  const layer = village(), camera = new THREE.PerspectiveCamera();
  const { response } = layer;
  const rows = [["low", "silver"], ["mid", "warm"], ["high", "crystal"]];
  for (const [band, field] of rows) {
    layer.resetResponse();
    const before = response.time;
    layer.update(frame({ reactive: true, [band]: 1 }), camera, .1);
    assert.ok(response[field] > 0);
    for (const [, other] of rows) if (other !== field) assert.equal(response[other], 0);
    layer.update(frame({ [band]: 1, beat: 1, pauseProgress: .6 }), camera, .1);
    assert.equal(response[field], 0);
    assert.equal(response.time, before + .1);
    assert.equal(response.pulse, 0);
  }
});

sceneTest("实际秒数驱动一次 300ms 衰减，重拍保持高位不会反复触发", () => {
  const { christmasPulse } = winter();
  assert.equal(christmasPulse(0), 1);
  assert.equal(christmasPulse(.15), .25);
  assert.equal(christmasPulse(.30), 0);
  const layer = village(), camera = new THREE.PerspectiveCamera();
  layer.resetResponse();
  const hit = frame({ reactive: true, beat: .9 });
  layer.update(hit, camera, .05);
  assert.equal(layer.response.pulse, 1);
  layer.update(hit, camera, .15);
  assert.equal(layer.response.pulse, .25);
  layer.update(hit, camera, .15);
  assert.equal(layer.response.pulse, 0);
  layer.update(frame({ reactive: true }), camera, .05);
  layer.update(hit, camera, .05);
  assert.equal(layer.response.pulse, 1);
});
