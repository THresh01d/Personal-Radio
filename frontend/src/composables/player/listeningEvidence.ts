import type { ListeningContext } from "../../types/radio";

// 独立播放编号贯穿暂停、缓冲与重连；只有新播放或真正重播才换编号。
export function createListeningEvidence() {
  let context: ListeningContext;
  let lastTime = 0, lastPosition = 0, wasPlaying = false;
  function begin(reason: ListeningContext["start_reason"]) {
    context = { contract: "playback-v2", playback_id: crypto.randomUUID(), start_reason: reason,
      interrupted: false, played_seconds: 0 };
    lastTime = Date.now(); lastPosition = 0; wasPlaying = false;
  }
  function sample(media: HTMLAudioElement, playing = false) {
    const now = Date.now(), position = Number.isFinite(media.currentTime) ? media.currentTime : lastPosition;
    const wall = Math.max(0, (now - lastTime) / 1000), delta = position - lastPosition;
    // 拖动进度不等于听过；媒体时钟须与实际经过时间相容，暂停和缓冲不积累。
    if (context && wasPlaying && !media.seeking && delta > 0 && delta <= wall + .5) {
      context.played_seconds = Math.min(86400, context.played_seconds + Math.min(delta, wall));
    }
    lastTime = now; lastPosition = position; wasPlaying = playing;
  }
  function interrupt() { if (context) context.interrupted = true; }
  function snapshot() { return { ...context }; }
  return { begin, sample, interrupt, snapshot };
}
