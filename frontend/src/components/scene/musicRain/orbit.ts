import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import type { PerspectiveCamera } from "three";

const winterViews = {
  // 把树的主枝留在封面左侧，右侧暖窗与门廊仍在画面内。
  desktop: { position: [8.2, 8.05, 15.35], target: [1.52, 3.87, -2.55], fov: 38 },
  portrait: { position: [13.6, 9, 18.25], target: [-.12, 4.05, -2.7], fov: 54 },
  short: { position: [7.8, 8.4, 16.8], target: [1.65, 3.65, -2.45], fov: 39 },
} as const;

// 相机交互独立于雨滴时间：暂停/静谧仍可观察，只有视角变化和有限惯性需要重绘。
export function createRainOrbit(canvas: HTMLCanvasElement, camera: PerspectiveCamera, invalidate: () => void) {
  const controls = new OrbitControls(camera, canvas);
  const pointers = new Set<number>();
  const page = canvas.ownerDocument;
  const view = page.defaultView!;
  let disposed = false;
  let connected = true;
  let framed: keyof typeof winterViews | "rain" = "rain";
  controls.target.set(0, 3.6, 0.26);
  controls.enablePan = false;
  controls.enableDamping = true;
  controls.dampingFactor = 0.12;
  controls.rotateSpeed = 0.55;
  controls.zoomSpeed = 0.8;
  controls.minDistance = 9;
  controls.maxDistance = 30;
  controls.minPolarAngle = 52 * Math.PI / 180;
  controls.maxPolarAngle = 93 * Math.PI / 180;
  controls.minAzimuthAngle = -Math.PI / 3;
  controls.maxAzimuthAngle = Math.PI / 3;
  controls.cursorStyle = "grab";
  controls.update();
  controls.saveState();
  const rainPosition = controls.position0.clone(), rainTarget = controls.target0.clone();
  const rainFov = camera.fov;
  controls.addEventListener("change", invalidate);

  function clearMomentum() {
    const damping = controls.enableDamping;
    controls.enableDamping = false;
    controls.update();
    controls.enableDamping = damping;
  }
  function releasePointers() {
    for (const id of pointers) if (canvas.hasPointerCapture(id)) canvas.releasePointerCapture(id);
    pointers.clear();
  }
  function interrupt() {
    if (disposed) return;
    releasePointers();
    if (connected) controls.disconnect();
    connected = !page.hidden;
    clearMomentum();
    if (connected) { controls.connect(canvas); controls.cursorStyle = "grab"; }
  }
  function reset() {
    if (disposed) return;
    interrupt();
    // reset自身不清惯性，先排空余量，确保双击后不会继续偏离正面。
    controls.reset();
    camera.fov = framed === "rain" ? rainFov : winterViews[framed].fov;
    camera.updateProjectionMatrix();
    invalidate();
  }
  function frameChristmas(enabled: boolean, width = 1440, height = 900) {
    const next = !enabled ? "rain" : width / height < .85 ? "portrait" : height < 700 ? "short" : "desktop";
    if (disposed || next === framed) return;
    const enteringChristmas = framed === "rain" && next !== "rain";
    // 进入圣诞夜时与复位共用机位，避免带入上一场景的视角和惯性。
    // 同一场景调整窗口尺寸时，仍尊重用户已拖动或缩放的视角。
    const atDefault = camera.position.distanceToSquared(controls.position0) < .0004
      && controls.target.distanceToSquared(controls.target0) < .0004;
    const fov = next === "rain" ? rainFov : winterViews[next].fov;
    if (next === "rain") {
      controls.position0.copy(rainPosition);
      controls.target0.copy(rainTarget);
    } else {
      controls.position0.fromArray(winterViews[next].position);
      controls.target0.fromArray(winterViews[next].target);
    }
    framed = next;
    if (enteringChristmas) { reset(); return; }
    if (atDefault) {
      camera.position.copy(controls.position0);
      controls.target.copy(controls.target0);
      controls.update();
    }
    camera.fov = fov;
    camera.updateProjectionMatrix();
  }
  const down = (event: PointerEvent) => pointers.add(event.pointerId);
  const up = (event: PointerEvent) => pointers.delete(event.pointerId);
  canvas.addEventListener("pointerdown", down);
  page.addEventListener("pointerup", up);
  page.addEventListener("pointercancel", up);
  canvas.addEventListener("dblclick", reset);
  view.addEventListener("blur", interrupt);
  page.addEventListener("visibilitychange", interrupt);

  return {
    update: () => disposed ? false : controls.update(),
    setQuiet(quiet: boolean) { controls.enableDamping = !quiet; },
    frameChristmas,
    reset,
    dispose() {
      if (disposed) return;
      disposed = true;
      releasePointers();
      controls.removeEventListener("change", invalidate);
      controls.dispose();
      canvas.removeEventListener("pointerdown", down);
      page.removeEventListener("pointerup", up);
      page.removeEventListener("pointercancel", up);
      canvas.removeEventListener("dblclick", reset);
      view.removeEventListener("blur", interrupt);
      page.removeEventListener("visibilitychange", interrupt);
    },
  };
}
