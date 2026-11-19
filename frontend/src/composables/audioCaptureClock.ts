/**
 * 前后台共用的片段观察时钟；只判断媒体连续推进，不推断全曲覆盖或信号质量。
 * 1秒回调上限拒绝跨越长空白；120ms容差兼容媒体时钟取整，并非听感校准参数。
 */
export function createMediaCaptureClock() {
  let previous: { wall: number; media: number } | null = null;

  function reset() { previous = null; }

  function observe(media: HTMLMediaElement, timestamp: number, enabled: boolean): number {
    if (!enabled || media.paused || media.ended || media.seeking || media.readyState < 3
      || media.playbackRate !== 1 || !Number.isFinite(timestamp)
      || !Number.isFinite(media.currentTime) || media.currentTime < 0) {
      reset();
      return 0;
    }
    const current = { wall: timestamp, media: media.currentTime };
    if (!previous) { previous = current; return 0; }
    const wallDelta = (current.wall - previous.wall) / 1000;
    const mediaDelta = current.media - previous.media;
    if (wallDelta <= 0 || wallDelta > 1 || mediaDelta < 0
      || Math.abs(mediaDelta - wallDelta) > Math.max(0.12, wallDelta * 0.25)) {
      // 跨定位、倒退、长间隔不截短补算；当前点只作为下一段起点。
      previous = current;
      return 0;
    }
    // 媒体时间可能被浏览器取整；保留起点等待推进，冻结本身不累计。
    if (mediaDelta === 0) return 0;
    previous = current;
    // 已通过墙钟连续性检查后按媒体推进计时；逐帧取较小值会单向积累时钟抖动误差。
    return mediaDelta;
  }

  return { reset, observe };
}
