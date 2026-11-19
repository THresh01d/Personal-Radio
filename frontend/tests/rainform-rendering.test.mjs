import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { runInNewContext } from "node:vm";
import { test } from "node:test";
import * as THREE from "three";

const require = createRequire(import.meta.url), ts = require("typescript");

// 使用真实 Three 几何与移植算法；只替换需要 GPU 的渲染器，着色器另由浏览器实际编译。
function engine() {
  let visibleScene;
  class Renderer {
    autoClear = true;
    target = null;
    disposed = 0;
    getRenderTarget() { return this.target; }
    setRenderTarget(target) { this.target = target; }
    clear() {}
    setPixelRatio() {}
    setSize() {}
    render(scene) { if (!this.target) visibleScene = scene; }
    dispose() { this.disposed++; }
  }
  const cache = new Map();
  function load(name) {
    if (cache.has(name)) return cache.get(name);
    const exports = {};
    const path = new URL(`../src/components/scene/musicRain/rainform/${name}.js`, import.meta.url);
    const source = ts.transpileModule(readFileSync(path, "utf8"), {
      compilerOptions: { allowJs: true, module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
    }).outputText;
    runInNewContext(source, { exports, require: id => id === "three"
      ? { ...THREE, WebGLRenderer: Renderer } : id.startsWith(".")
        ? load(new URL(id, path).href.split("/rainform/")[1].replace(/\.js$/, "")) : require(id) });
    cache.set(name, exports);
    return exports;
  }
  const core = load("rendering").createRainformCore({});
  core.resize(1440, 900, 1, 15360);
  return { core, get scene() { return visibleScene; }, get points() {
    return visibleScene.children[0].children.find(object => object.isPoints);
  } };
}
const frame = (time, bands) => ({ time, bands, low: 0.5, mid: 0.3, high: 0.2,
  energy: 0.4, beat: 0, reactive: true, ripple: null });

test("雪夜只绘制雪花，暂停固定位置，降档与切换复用同一批粒子", () => {
  const h = engine(), bands = new Float32Array(48).fill(.04);
  bands.fill(.8, 4, 12);
  h.core.render(frame(.12, bands));
  const group = h.scene.children[0];
  const rain = h.points;
  const rainPositions = rain.geometry.attributes.position.array.slice();
  const water = group.children.find(obj => obj.material.uniforms?.uRainLut);
  const fog = group.children.find(obj => obj.name === 'rain-mist-band');
  const opacity = fog.material.uniforms.uOpacity.value;
  const reflection = water.material.uniforms.uReflStrength.value;
  const camera = h.core.camera.position.clone();
  h.core.setWeather('snow');
  h.core.render({ ...frame(.22, bands), reactive: false });
  const snow = group.children.find(obj => obj.name === 'snow-night-particles');
  assert.deepEqual(Array.from(group.children.filter(object => object.visible)), [snow]);
  assert.equal(h.scene.background, null);
  assert.ok(h.core.camera.position.equals(camera));
  assert.equal(snow.material.uniforms.uBands.value, water.material.uniforms.uRainLut.value);
  assert.equal(fog.material.uniforms.uLut.value, water.material.uniforms.uRainLut.value);
  const snowPosition = snow.geometry.attributes.position;
  const flakeCount = snowPosition.count;
  assert.equal(flakeCount, 3600);
  let leftCount = 0;
  for (let i = 0; i < flakeCount; i++) if (snowPosition.getX(i) < 0) leftCount++;
  assert.equal(leftCount, flakeCount / 2);
  h.core.render(frame(.24, bands));
  assert.ok(rain.geometry.attributes.position.array.every((value, i) => value === rainPositions[i]));
  const clock = snow.material.uniforms.uTime;
  const time = clock.value;
  assert.ok(time > 0);
  h.core.render({ ...frame(.31, bands), reactive: false, pauseProgress: 1 });
  assert.equal(clock.value, time);
  assert.equal(snow.visible, true);
  assert.equal(water.visible, false);
  h.core.resize(915, 625, 1, 3360);
  assert.equal(snow.geometry.attributes.position, snowPosition);
  assert.equal(snow.geometry.drawRange.count, 1400);
  const depths = Array.from({ length: 1400 }, (_, i) => snowPosition.getZ(i));
  assert.ok(depths.some(z => z > 6) && depths.some(z => z > -7 && z < 5.5) && depths.some(z => z < -8));
  h.core.resize(1440, 900, 1, 15360);
  assert.equal(snow.geometry.drawRange.count, flakeCount);
  h.core.setWeather('rain');
  h.core.render(frame(.31, bands));
  assert.equal(h.points, rain);
  assert.ok(rain.visible && !snow.visible);
  assert.equal(fog.material.uniforms.uOpacity.value, opacity);
  assert.equal(water.material.uniforms.uReflStrength.value, reflection);
  assert.ok(fog.visible && water.visible);
  h.core.setWeather('snow');
  assert.equal(h.scene.getObjectByName('snow-night-particles'), snow);
  assert.ok(snow.visible);
  let releases = 0;
  snow.geometry.addEventListener('dispose', () => releases++);
  snow.material.addEventListener('dispose', () => releases++);
  h.core.dispose(); h.core.dispose();
  assert.equal(releases, 2);
});

test("升降音轻推不同区域的雪光，长音维持位置，无音高时回落", () => {
  const h = engine(), bands = new Float32Array(48).fill(.18);
  h.core.setWeather('snow');
  h.core.render(frame(.1, bands), 329.63);
  const { children } = h.scene.children[0];
  const snow = children.find(node => node.name === 'snow-night-particles');
  const packed = snow.material.uniforms.uBands.value.image.data;
  const peak = () => {
    let index = 0;
    for (let i = 1; i < 256; i++) if (packed[i * 4 + 1] > packed[index * 4 + 1]) index = i;
    return [index, packed[index * 4 + 1]];
  };
  const [left, strength] = peak();
  assert.ok(left < 60 && strength > .85);
  h.core.render({ ...frame(.2, bands), beat: 1 }, 880);
  const [right] = peak();
  assert.ok(right > 180);
  assert.ok(packed[left * 4 + 1] > .3);
  assert.ok(packed[120 * 4 + 1] < .08);
  h.core.render(frame(.3, bands), 880);
  const [held, heldStrength] = peak();
  assert.equal(held, right);
  assert.ok(heldStrength > .95);
  h.core.render(frame(.4, bands), 392);
  const [down] = peak();
  assert.ok(left < down && down < right);
  for (let i = 5; i <= 11; i++) h.core.render(frame(i / 10, bands));
  assert.ok(peak()[1] < .01);
  h.core.render({ ...frame(1.2, bands), reactive: false }, 880);
  assert.equal(peak()[1], 0);
  h.core.dispose();
});

test("原作雨链连续向下运动，频谱更新复用几何与材质且没有图表对象", () => {
  const h = engine(), bands = new Float32Array(48).fill(0.8);
  h.core.render(frame(0.1, bands));
  const points = h.points, geometry = points.geometry, material = points.material;
  h.core.camera.position.set(3, 4, 15);
  h.core.camera.lookAt(0, 3.6, 0.26);
  const viewPosition = h.core.camera.position.clone(), viewRotation = h.core.camera.quaternion.clone();
  h.core.resize(900, 625, 1, 15360);
  assert.ok(h.core.camera.position.equals(viewPosition));
  assert.ok(h.core.camera.quaternion.equals(viewRotation));
  const before = geometry.attributes.position.array.slice();
  h.core.render(frame(0.15, bands));
  const after = geometry.attributes.position.array;
  let movedDown = 0, compared = 0;
  for (let i = 1; i < after.length; i += 3) {
    if (before[i] < 0.2 || before[i] > 5) continue;
    compared++;
    if (after[i] < before[i]) movedDown++;
  }
  assert.ok(movedDown / compared > 0.7, "雨链应实际下落，不能只闪烁或整体漂动");
  assert.equal(h.points.geometry, geometry); assert.equal(h.points.material, material);
  assert.equal(h.scene.children[0].children.length, 5);
  assert.equal(h.scene.children[0].children.filter(object => object.isSprite).length, 0);
  h.core.dispose();
});

test("音乐频峰在对应横向位置生成高雨链，水面与薄雾同步且降档保留全频范围", () => {
  const h = engine(), bands = new Float32Array(48).fill(0.03);
  bands.fill(0.95, 4, 11);
  h.core.resize(390, 844, 1, 3360);
  h.core.render(frame(0.05, bands));
  const points = h.points, position = points.geometry.attributes.position.array;
  const alphas = points.geometry.attributes.aAlpha.array;
  let lowTop = 0, highTop = 0, lowVisible = 0, highVisible = 0;
  for (let i = 0; i < points.geometry.drawRange.count; i++) {
    if (alphas[i] < 0.02) continue;
    const x = position[i * 3], y = position[i * 3 + 1];
    if (x < -4) { lowTop = Math.max(lowTop, y); lowVisible++; }
    if (x > 4) { highTop = Math.max(highTop, y); highVisible++; }
  }
  assert.ok(lowTop > highTop + 1.5);
  assert.ok(lowVisible > 0 && highVisible > 0);
  const group = h.scene.children[0];
  const water = group.children.find(object => object.material.uniforms?.uRainLut);
  const mist = group.children.find(object => object.name === "rain-mist-band");
  assert.equal(water.material.uniforms.uRainLut.value, mist.material.uniforms.uLut.value);
  const texture = mist.material.uniforms.uLut.value, packed = texture.image.data;
  assert.ok(packed[40 * 4] > packed[220 * 4] * 10);
  const geometry = points.geometry;
  bands.fill(0.03); bands.fill(0.95, 37, 44);
  h.core.render(frame(0.1, bands));
  assert.ok(packed[220 * 4] > packed[40 * 4] * 10);
  assert.equal(h.points.geometry, geometry);
  assert.equal(water.material.uniforms.uRainLut.value, texture);
  assert.ok(group.scale.x < 1);
  h.core.dispose();
});

test("相同时间的静止帧不会继续下雨，释放完整场景与高度场且重复释放安全", () => {
  const h = engine(), bands = new Float32Array(48).fill(0.5);
  const still = { ...frame(0, bands), reactive: false };
  h.core.render(still);
  const points = h.points, first = points.geometry.attributes.position.array.slice();
  h.core.render(still);
  const next = points.geometry.attributes.position.array;
  assert.ok(next.every((value, index) => value === first[index]), "静止帧不能重新随机投放落水雨串");
  let released = 0;
  points.geometry.addEventListener("dispose", () => released++);
  h.core.dispose(); h.core.dispose();
  assert.equal(released, 1);
  assert.equal(h.scene.children.length, 0);
});

test("仅暂停让雨珠落水，播放及暂停均不调整雾带、水面材质或视角", () => {
  const h = engine(), bands = new Float32Array(48).fill(.7);
  h.core.render(frame(.1, bands));
  const group = h.scene.children[0], geometry = h.points.geometry;
  const mist = group.children.find(object => object.name === "rain-mist-band");
  const water = group.children.find(object => object.material.uniforms?.uRainLut);
  const keys = ["uRippleDisplace", "uReflStrength", "uReflFade", "uRoughness", "uSpecularStrength"];
  const settings = keys.map(key => water.material.uniforms[key].value);
  const opacity = mist.material.uniforms.uOpacity.value;
  const camera = h.core.camera.position.clone();
  const before = geometry.attributes.position.array.slice();
  h.core.render({ ...frame(.15, bands), reactive: false, pauseProgress: .25 });
  const falling = geometry.attributes.position.array;
  let lowered = 0;
  for (let i = 1; i < falling.length; i += 3) {
    assert.ok(falling[i] <= before[i] + 1e-5);
    if (falling[i] < before[i] - .01) lowered++;
  }
  assert.ok(lowered > 100);
  h.core.render({ ...frame(.2, bands), reactive: false, pauseProgress: 1 });
  assert.equal(h.points.visible, false);
  assert.ok(geometry.attributes.aAlpha.array.every(value => value === 0));
  assert.equal(group.children.find(object => object.isLineSegments).visible, false);
  assert.equal(water.visible, true);
  assert.deepEqual(keys.map(key => water.material.uniforms[key].value), settings);
  assert.equal(mist.material.uniforms.uOpacity.value, opacity);
  assert.ok(h.core.camera.position.equals(camera));
  // 相同时间恢复，只恢复原有雨串，不额外随机重排或重新生成正常播放的布局。
  h.core.render({ ...frame(.2, bands), pauseProgress: null });
  assert.equal(h.points.visible, true); assert.equal(h.points.geometry, geometry);
  const resumed = geometry.attributes.position.array;
  for (let i = 1; i < resumed.length; i += 3) assert.ok(Math.abs(resumed[i] - before[i]) < 1e-5);
  assert.deepEqual(keys.map(key => water.material.uniforms[key].value), settings);
  assert.equal(mist.material.uniforms.uOpacity.value, opacity);
  h.core.dispose();
});
