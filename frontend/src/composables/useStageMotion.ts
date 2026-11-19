import { computed, onBeforeUnmount, onMounted, ref, type Ref } from "vue";
import type { VisualMode } from "./useAudioAnalysis";

// 用户选择和运行时降级分开，恢复性能或关闭抽屉不会改写用户偏好。
export function stageMode(requested: VisualMode, reduced: boolean, constrained: boolean, panelOpen: boolean): VisualMode {
  if (reduced || requested === "quiet") return "quiet";
  return constrained || panelOpen ? "atmosphere" : requested;
}

export function useStageMotion(requested: Ref<VisualMode>, panelOpen: Ref<boolean>) {
  const media = window.matchMedia("(prefers-reduced-motion: reduce)");
  const reduced = ref(media.matches);
  const constrained = ref(false);
  const effectiveMode = computed(() => stageMode(requested.value, reduced.value, constrained.value, panelOpen.value));
  const note = computed(() => reduced.value ? "系统已启用减少动态效果，舞台保持静谧"
    : constrained.value ? "检测到持续卡顿，已降低粒子与绘制频率"
    : panelOpen.value ? "面板打开时舞台动效减弱" : "");
  let frame = 0;
  let previous = 0;
  let started = 0;
  let slow = 0;
  let count = 0;
  let healthyWindows = 0;
  function sample(timestamp: number) {
    if (document.hidden) { frame = 0; return; }
    if (previous) { count++; if (timestamp - previous > 50) slow++; }
    previous = timestamp;
    if (!started) started = timestamp;
    // 持续三秒的帧延迟才降级；一次打开面板或拖动窗口不导致频繁跳档。
    if (timestamp - started >= 3000) {
      // 首次建模/编译可能独占一个采样窗口；至少三次慢帧才构成持续卡顿。
      if (slow >= 3 && slow / count > 0.2) { constrained.value = true; healthyWindows = 0; }
      else if (constrained.value && count > 90 && slow / count < 0.05) {
        if (++healthyWindows >= 4) { constrained.value = false; healthyWindows = 0; }
      } else healthyWindows = 0;
      started = timestamp; slow = 0; count = 0;
    }
    frame = window.requestAnimationFrame(sample);
  }
  function restart() {
    window.cancelAnimationFrame(frame);
    previous = 0; started = 0; count = 0; slow = 0; healthyWindows = 0;
    if (!document.hidden) frame = window.requestAnimationFrame(sample);
  }
  function preference(event: MediaQueryListEvent) { reduced.value = event.matches; }
  onMounted(() => {
    media.addEventListener("change", preference);
    document.addEventListener("visibilitychange", restart);
    restart();
  });
  onBeforeUnmount(() => {
    window.cancelAnimationFrame(frame);
    media.removeEventListener("change", preference);
    document.removeEventListener("visibilitychange", restart);
  });
  return { effectiveMode, constrained, reduced, note };
}
