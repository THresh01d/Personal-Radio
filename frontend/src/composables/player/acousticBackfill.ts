import { getPendingAudioFeatures, getPlayUrl } from "../../services/api";
import type { AudioFeatureSummary, Song } from "../../types/radio";
import type { useAudioAnalysis } from "../useAudioAnalysis";

type BackfillOptions = {
  canRun: () => boolean;
  getCandidates: () => Song[];
  audioAnalysis: Pick<ReturnType<typeof useAudioAnalysis>, "cancelBackgroundAnalysis" | "analyzeUrlInBackground">;
  onAudioFeatures?: (song: Song, summary: AudioFeatureSummary) => void;
};

// 空闲采样独立拥有计时器、请求与世代，正常播放只负责通知取消。
export function createAcousticBackfill({ canRun, getCandidates, audioAnalysis, onAudioFeatures }: BackfillOptions) {
  let backfillGeneration = 0;
  let backfillTimer = 0;
  let backfillRequest: AbortController | null = null;

  function cancelBackfill() {
    backfillGeneration += 1;
    window.clearTimeout(backfillTimer);
    backfillTimer = 0;
    backfillRequest?.abort();
    backfillRequest = null;
    audioAnalysis.cancelBackgroundAnalysis();
  }

  function scheduleBackfill(delay = 20_000) {
    window.clearTimeout(backfillTimer);
    if (!canRun()) return;
    backfillTimer = window.setTimeout(() => { void runBackfill(); }, delay);
  }

  async function runBackfill() {
    backfillTimer = 0;
    if (!canRun()) return;
    const queue = getCandidates();
    if (!queue.length) return;
    const generation = backfillGeneration;
    const request = new AbortController();
    backfillRequest = request;
    const timer = window.setTimeout(() => request.abort(), 10_000);
    const stillIdle = () => generation === backfillGeneration && !request.signal.aborted && canRun();
    try {
      const pending = await getPendingAudioFeatures(queue.map((song) => song.id), request.signal);
      const target = queue.find((song) => pending.includes(song.id));
      if (!target || !stillIdle()) return;
      const playable = await getPlayUrl(target.id, request.signal);
      window.clearTimeout(timer);
      if (!playable.success || !playable.url || !stillIdle()) return;
      const summary = await audioAnalysis.analyzeUrlInBackground(playable.url, 15);
      if (!summary || !stillIdle()) return;
      target.audio_features = { ...summary, observation_count: 1 };
      onAudioFeatures?.(target, summary);
      // 每轮只补一首，留出冷却时间；它不会抢占用户点击或持续占用网络。
      scheduleBackfill(30_000);
    } catch {
      // 空闲增强失败不影响正常播放，下次暂停后还可以重试。
    } finally {
      window.clearTimeout(timer);
      if (backfillRequest === request) backfillRequest = null;
    }
  }
  return { cancelBackfill, scheduleBackfill };
}
