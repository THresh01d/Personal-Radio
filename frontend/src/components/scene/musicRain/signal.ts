import type { AudioAnalysisStatus, VisualMode } from "../../../composables/useAudioAnalysis";

export const RAIN_BANDS = 48;
const PAUSE_FALL_SECONDS = 1.8;

export type StageWeather = "rain" | "snow" | "christmas";
export function stageWeather(value: unknown): StageWeather {
  return value === "snow" || value === "christmas" ? value : "rain";
}
export type ReadWaveform = (buffer: Float32Array<ArrayBuffer>) => { sampleRate: number; bass: number } | null;

export type RainInput = {
  spectrum: readonly number[];
  low: number;
  mid: number;
  high: number;
  energy: number;
  beat: number;
  isPlaying: boolean;
  paused?: boolean;
  analysisStatus: AudioAnalysisStatus;
  visualMode: VisualMode;
  constrained: boolean;
  weather?: StageWeather;
  readWaveform?: ReadWaveform;
};

export type RainFrame = {
  bands: Float32Array;
  low: number;
  mid: number;
  high: number;
  energy: number;
  beat: number;
  reactive: boolean;
  pauseProgress: number | null;
  time: number;
  deltaSeconds?: number;
  ripple: { band: number; strength: number } | null;
};

export function unit(value: number) {
  return Number.isFinite(value) ? Math.min(1, Math.max(0, value)) : 0;
}

export function hasRainSignal(input: RainInput) {
  return input.isPlaying && input.analysisStatus === "active" && input.visualMode !== "quiet";
}

// 静止时用中性基线；只有真实播放信号才改变频段。
export function restingBand(index: number) {
  return .12 + .08 * Math.sin(index / (RAIN_BANDS - 1) * Math.PI);
}

export function rainQuality(width: number, constrained: boolean, mode: VisualMode) {
  const narrow = width < 700;
  return {
    particles: RAIN_BANDS * (constrained ? 70 : narrow ? 110 : mode === "immersive" ? 320 : 210),
    pixelRatio: constrained || narrow ? 1 : 1.5,
    interval: constrained ? 1000 / 20 : 1000 / 30,
  };
}

export function createRainSignal() {
  const bands = Float32Array.from({ length: RAIN_BANDS }, (_, index) => restingBand(index));
  let time = 0;
  let previousBeat = 0;
  let lastRipple = -1;
  let wasPaused = false;
  let hasPlayed = false;
  let pauseSeconds: number | null = null;

  return {
    update(input: RainInput, seconds: number): RainFrame {
      const reactive = hasRainSignal(input);
      const gain = input.visualMode === "immersive" ? 1 : 0.85;
      const deltaSeconds = Number.isFinite(seconds) ? Math.max(0, seconds) : 0;
      const dt = Math.min(0.1, deltaSeconds);
      const paused = input.paused ?? !input.isPlaying;
      if (input.isPlaying) hasPlayed = true;
      if (!paused) pauseSeconds = null;
      else if (!wasPaused && hasPlayed) pauseSeconds = 0;
      const falling = pauseSeconds !== null && pauseSeconds < PAUSE_FALL_SECONDS;
      if (pauseSeconds !== null) pauseSeconds = input.visualMode === "quiet"
        ? PAUSE_FALL_SECONDS : Math.min(PAUSE_FALL_SECONDS, pauseSeconds + dt);
      wasPaused = paused;
      const smoothing = 1 - Math.exp(-dt * 12);
      let peak = 0;
      for (let index = 0; index < RAIN_BANDS; index++) {
        // 输入本身已按对数频率分桶，沿用其顺序，不把人声/高频压在最后几列。
        const value = unit(input.spectrum[index] ?? 0);
        const target = reactive ? 0.035 + Math.pow(value, 0.8) * 0.965 * gain : restingBand(index);
        // 只在暂停收尾时保留最后的轮廓，播放中的平滑与原版完全一致。
        if (pauseSeconds === null) bands[index] = input.visualMode === "quiet" ? restingBand(index)
          : bands[index]! + (target - bands[index]!) * smoothing;
        if (bands[index]! > bands[peak]!) peak = index;
      }
      if (reactive) time += dt * (0.8 + unit(input.low) * 0.38);
      else if (falling && input.visualMode !== "quiet") time += dt;
      const beat = reactive ? unit(input.beat) : 0;
      const ripple = beat > 0.65 && previousBeat <= 0.65 && time - lastRipple > 0.18
        ? { band: peak, strength: beat * gain } : null;
      if (ripple) lastRipple = time;
      previousBeat = beat;
      return {
        bands, time, reactive, ripple, beat, deltaSeconds,
        pauseProgress: pauseSeconds === null ? null : pauseSeconds / PAUSE_FALL_SECONDS,
        low: reactive ? unit(input.low) * gain : 0,
        mid: reactive ? unit(input.mid) * gain : 0,
        high: reactive ? unit(input.high) * gain : 0,
        energy: reactive ? unit(input.energy) * gain : 0,
      };
    },
  };
}
