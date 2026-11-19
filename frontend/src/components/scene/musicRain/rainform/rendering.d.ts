import type { RainFrame, StageWeather } from "../signal";
import type { PerspectiveCamera } from "three";

export function createRainformCore(canvas: HTMLCanvasElement): {
  camera: PerspectiveCamera;
  setWeather(mode: StageWeather): void;
  resize(width: number, height: number, pixelRatio: number, particleBudget: number): void;
  render(frame: RainFrame, pitch?: number | null): void;
  dispose(): void;
};
