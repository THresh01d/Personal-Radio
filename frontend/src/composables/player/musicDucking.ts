import { watch, type Ref } from "vue";

// 只改变媒体的实际增益，不改音量滑块；播报结束后恢复用户最新选择。
export function createMusicDucking(getAudio: () => HTMLAudioElement, volume: Ref<number>, speaking: Ref<boolean>, paused: Ref<boolean>, playing: Ref<boolean>) {
  let timer = 0;
  let gain = 1;
  let target = 1;
  function apply() { getAudio().volume = Math.max(0, Math.min(1, volume.value * gain)); }
  function update() {
    const next = speaking.value && !paused.value && playing.value ? 0.25 : 1;
    if (next === target) { apply(); return; }
    window.clearTimeout(timer);
    target = next;
    const from = gain, started = Date.now(), duration = next < gain ? 250 : 450;
    const step = () => {
      const progress = Math.min(1, (Date.now() - started) / duration);
      gain = from + (target - from) * progress;
      apply();
      timer = progress < 1 ? window.setTimeout(step, 20) : 0;
    };
    step();
  }
  const unwatch = watch([speaking, paused, playing, volume], update, { flush: "sync" });
  apply();
  return {
    apply,
    dispose() { unwatch(); window.clearTimeout(timer); gain = target = 1; apply(); },
  };
}
