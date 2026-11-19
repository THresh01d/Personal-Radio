import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { test } from "node:test";
import { runInNewContext } from "node:vm";
const require = createRequire(import.meta.url), ts = require("typescript");
const { parse, compileScript } = require("@vue/compiler-sfc");
const { createSSRApp } = require("vue"), { renderToString } = require("@vue/server-renderer");
const { descriptor } = parse(readFileSync(new URL("../src/components/scene/NowPlayingSign.vue", import.meta.url), "utf8"));
const compiled = ts.transpileModule(compileScript(descriptor, { id: "stage-sign-regression", inlineTemplate: true }).content, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText;
const exports = {};
// 只隔离子组件绘制，状态说明仍由真实舞台组件生成，避免测试自写文案。
runInNewContext(compiled, { exports, require: id => id.endsWith(".vue") ? { default: { render: () => null } } : require(id) });
const render = overrides => renderToString(createSSRApp(exports.default, {
  title: "测试曲", subtitle: "真实元数据", currentSong: { id: "test", title: "测试曲", artist: "测试歌手" },
  status: "NOW PLAYING", progressValue: 10, elapsed: 10, isPlaying: true, isAnnouncing: false,
  audioLow: 0, audioMid: 0, audioHigh: 0, audioEnergy: 0, audioBeat: 0, audioSpectrum: [],
  lyricLines: [], lyricsInstrumental: false, visualMode: "atmosphere", constrained: false,
  analysisStatus: "active", analysisMessage: "正在读取真实音频频谱", ...overrides,
}));
test("舞台只提示真实的频谱异常，正常播放保留歌曲信息", async () => {
  const active = await render({}); assert.match(active, /测试曲/); assert.doesNotMatch(active, /舞台状态|stage-analysis-note/);
  const zero = await render({ analysisStatus: "no-signal", analysisMessage: "暂未读到频谱" });
  assert.match(zero, /暂无频谱信号/);
  const ordinary = await render({ analysisStatus: "unavailable", analysisMessage: "分析不可用" });
  assert.match(ordinary, /普通播放 · 无实时频谱/);
});
test("静谧模式和暂停时没有频谱告警", async () => {
  assert.doesNotMatch(await render({ visualMode: "quiet", analysisStatus: "unavailable" }), /stage-analysis-note/);
  assert.doesNotMatch(await render({ isPlaying: false, analysisStatus: "no-signal" }), /stage-analysis-note/);
});
