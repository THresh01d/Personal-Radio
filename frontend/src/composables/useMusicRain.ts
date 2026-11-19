import { onBeforeUnmount, onMounted, ref, watch, type Ref } from "vue";
import { createRainSignal, hasRainSignal, rainQuality, type RainInput } from "../components/scene/musicRain/signal";
import { loadRainScene } from "../components/scene/musicRain/loader";
import type { RainScene } from "../components/scene/musicRain/scene";

export function useMusicRain(canvas: Ref<HTMLCanvasElement | null>, readInput: () => RainInput) {
  const state = ref<"loading" | "ready" | "fallback">("loading");
  const signal = createRainSignal();
  let scene: RainScene | null = null;
  let pending: Promise<void> | null = null;
  let disposed = false;
  let contextLost = false;
  let frame = 0;
  let previous = 0;
  let settleUntil = 0;
  let revision = 0;
  let drawing = false;
  let viewPending = false;
  let viewMoving = false;
  let falling = false;

  function stop() { window.cancelAnimationFrame(frame); frame = 0; previous = 0; }
  function fail() {
    stop();
    scene?.dispose();
    scene = null;
    state.value = "fallback";
  }
  function resize() {
    if (!scene || disposed || document.hidden) return;
    try { scene.resize(window.innerWidth, window.innerHeight, window.devicePixelRatio, readInput()); }
    catch { fail(); }
  }
  function draw(timestamp: number) {
    frame = 0;
    if (!scene || disposed || contextLost || document.hidden) return;
    const input = readInput();
    const interval = rainQuality(window.innerWidth, input.constrained, input.visualMode).interval;
    const active = hasRainSignal(input);
    if (viewPending || !previous || timestamp - previous >= interval - 0.5) {
      const dt = previous ? (timestamp - previous) / 1000 : 1 / 30;
      previous = timestamp;
      viewPending = false;
      drawing = true;
      try {
        const rainFrame = signal.update(input, dt);
        falling = rainFrame.pauseProgress !== null && rainFrame.pauseProgress < 1;
        viewMoving = scene.render(rainFrame, input.weather, input.readWaveform);
      }
      catch { fail(); return; }
      finally { drawing = false; }
    }
    if (active || viewMoving || falling || (input.visualMode !== "quiet" && timestamp < settleUntil)) frame = window.requestAnimationFrame(draw);
  }
  function invalidateView() {
    if (drawing || disposed || contextLost || document.hidden || !scene) return;
    viewPending = true;
    // 与音频共用一个RAF，静止时由交互唤醒，不建立第二个永久循环。
    if (!frame) { previous = 0; frame = window.requestAnimationFrame(draw); }
  }
  function resetView() { scene?.resetView(); }
  function sync() {
    stop();
    if (disposed || contextLost || document.hidden) return;
    if (!scene) { if (state.value !== "fallback") void initialize(); return; }
    resize();
    // 暂停/失去频谱后只用一个短收敛窗口，不保留永远空转的后台动画。
    settleUntil = performance.now() + (readInput().visualMode === "quiet" ? 0 : 700);
    draw(performance.now());
  }
  async function initialize() {
    if (pending || disposed || contextLost || document.hidden) return;
    const current = ++revision;
    state.value = "loading";
    pending = (async () => {
      try {
        const module = await loadRainScene();
        if (disposed || contextLost || document.hidden || current !== revision || !canvas.value) return;
        scene = module.createRainScene(canvas.value, invalidateView);
        state.value = "ready";
        sync();
      } catch { if (!disposed && current === revision) fail(); }
      finally { if (current === revision) pending = null; }
    })();
    return pending;
  }
  function lost(event: Event) {
    event.preventDefault();
    contextLost = true;
    revision++;
    pending = null;
    fail();
  }
  function restored() {
    if (disposed) return;
    contextLost = false;
    state.value = "loading";
    void initialize();
  }
  function onResize() { sync(); }

  onMounted(() => {
    canvas.value?.addEventListener("webglcontextlost", lost);
    canvas.value?.addEventListener("webglcontextrestored", restored);
    window.addEventListener("resize", onResize);
    document.addEventListener("visibilitychange", sync);
    void initialize();
  });
  watch(() => {
    const input = readInput();
    return [input.visualMode, input.constrained, input.isPlaying, input.analysisStatus, input.paused, input.weather];
  }, sync);
  onBeforeUnmount(() => {
    disposed = true;
    revision++;
    stop();
    canvas.value?.removeEventListener("webglcontextlost", lost);
    canvas.value?.removeEventListener("webglcontextrestored", restored);
    window.removeEventListener("resize", onResize);
    document.removeEventListener("visibilitychange", sync);
    scene?.dispose();
    scene = null;
  });
  return { state, resetView };
}
