import type { RainFrame, RainInput, ReadWaveform, StageWeather } from "./signal";

export type RainScene = {
  resize: (width: number, height: number, ratio: number, input: RainInput) => void;
  render: (frame: RainFrame, weather?: StageWeather, readWaveform?: ReadWaveform) => boolean;
  resetView: () => void;
  dispose: () => void;
};

// 公开版独立绘制音频光带。播放时按真实频段变化，待机保持静止。
export function createRainScene(canvas: HTMLCanvasElement, _invalidate: () => void): RainScene {
  const context = canvas.getContext("2d");
  if (!context) throw new Error("Canvas 2D unavailable");
  let width = 1, height = 1, scale = 1, quiet = false;
  return {
    resize(nextWidth, nextHeight, ratio, input) {
      width = Math.max(1, nextWidth);
      height = Math.max(1, nextHeight);
      scale = Math.max(1, Math.min(input.constrained ? 1 : 1.5, ratio));
      quiet = input.visualMode === "quiet";
      canvas.width = Math.floor(width * scale);
      canvas.height = Math.floor(height * scale);
    },
    render(frame) {
      context.setTransform(scale, 0, 0, scale, 0, 0);
      context.clearRect(0, 0, width, height);
      if (quiet) return false;
      const horizon = height * .73;
      const amplitude = Math.min(width * .18, height * .33);
      const tint = context.createLinearGradient(0, horizon - amplitude, 0, horizon + amplitude * .6);
      tint.addColorStop(0, "#98d4f060");
      tint.addColorStop(.65, "#496c922d");
      tint.addColorStop(1, "#36526b00");
      context.strokeStyle = tint;
      context.lineWidth = Math.max(1, width / 1400);
      const total = frame.bands.length;
      for (let band = 0; band < total; band++) {
        const x = width * (.08 + .84 * (band + .5) / total);
        const strength = frame.reactive ? frame.bands[band]! : .08;
        const wave = Math.sin(band * .41 + frame.time * .65) * strength * .15;
        const top = horizon - amplitude * Math.max(.05, strength + wave);
        context.beginPath();
        context.moveTo(x, top);
        context.lineTo(x, horizon + amplitude * .42);
        context.stroke();
      }
      return false;
    },
    resetView() {},
    dispose() { context.clearRect(0, 0, width, height); },
  };
}
