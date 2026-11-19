import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { runInNewContext } from "node:vm";
import { test } from "node:test";
import ts from "typescript";

const exports = {};
const source = readFileSync(new URL("../src/components/scene/musicRain/pitch.ts", import.meta.url), "utf8");
runInNewContext(ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText, { exports });
const detectPitch = exports.createPitchDetector();
const sine = (frequency, time) => Math.sin(2 * Math.PI * frequency * time);
const windowOf = (signal, rate) => Float32Array.from({ length: 2048 }, (_, i) => signal(i / rate));

test("雪夜在播放器现有的 2048 样本窗口分辨旋律和低音", () => {
  for (const rate of [44100, 48000, 96000]) {
    for (const frequency of [329.63, 493.88, 880, 392]) {
      const window = windowOf(time => .15 * sine(frequency, time) + .032 * sine(frequency * 2, time)
        + .009 * sine(frequency * 3, time) + .10 * sine(55, time), rate);
      const pitch = detectPitch(window, rate);
      assert.ok(pitch !== null && Math.abs(pitch - frequency) < frequency * .02, `${rate}: ${pitch} ≈ ${frequency}`);
    }
  }
});

test("只有低音鼓或静默时不强行点亮旋律位置", () => {
  const kick = windowOf(time => .26 * sine(68 - time * 90, time) * Math.exp(-time * 20) + .14 * sine(55, time), 48000);
  assert.equal(detectPitch(kick, 48000), null);
  assert.equal(detectPitch(new Float32Array(2048), 48000), null);
});
