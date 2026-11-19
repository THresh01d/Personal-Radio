import { createRainformCore } from "./rainform/rendering.js";
import { rainQuality, type RainFrame, type RainInput, type ReadWaveform, type StageWeather } from "./signal";
import { createRainOrbit } from "./orbit";
import { createPitchDetector } from "./pitch";

export type RainScene = {
  resize: (width: number, height: number, ratio: number, input: RainInput) => void;
  render: (frame: RainFrame, weather?: StageWeather, readWaveform?: ReadWaveform) => boolean;
  resetView: () => void;
  dispose: () => void;
};

// 渲染移植与播放器信号保持边界；原作核心不接触 DOM、音源或应用设置。
export function createRainScene(canvas: HTMLCanvasElement, invalidate: () => void): RainScene {
  const core = createRainformCore(canvas);
  const waveform = new Float32Array(2048);
  const detectPitch = createPitchDetector();
  let viewportWidth = 1, viewportHeight = 1;
  let orbit: ReturnType<typeof createRainOrbit>;
  try { orbit = createRainOrbit(canvas, core.camera, invalidate); }
  catch (error) { core.dispose(); throw error; }
  return {
    resize(width, height, ratio, input) {
      viewportWidth = width;
      viewportHeight = height;
      const quality = rainQuality(width, input.constrained, input.visualMode);
      core.resize(Math.max(1, width), Math.max(1, height), Math.min(ratio || 1, quality.pixelRatio), quality.particles);
      orbit.setQuiet(input.visualMode === "quiet");
    },
    render(frame, weather = "rain", readWaveform) {
      orbit.frameChristmas(weather === "christmas", viewportWidth, viewportHeight);
      const moving = orbit.update();
      core.setWeather(weather);
      const audio = weather === "snow" && frame.reactive ? readWaveform?.(waveform) : null;
      core.render(frame, audio ? detectPitch(waveform, audio.sampleRate) : null);
      return moving;
    },
    resetView: () => orbit.reset(),
    dispose() { orbit.dispose(); core.dispose(); },
  };
}
