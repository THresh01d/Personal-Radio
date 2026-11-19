import { cancelAudioObservation, prepareAudioObservation, submitAudioObservation } from "../../services/api";
import type { AudioObservationLease, AudioObservationPacket } from "../../types/audioObservation";
import type { AudioFeatureSummary } from "../../types/radio";

const LEASE_MS = 20 * 60 * 1000;
const SUBMIT_MARGIN_MS = 5_000;
const MAX_WRITES = 2;
const SUMMARY_FIELDS = ["energy_mean", "energy_peak", "low_ratio", "brightness", "dynamic_range", "beat_density", "sample_seconds"] as const;
const BINDING_FIELDS = ["library_generation", "song_id", "song_revision", "metadata_sha256",
  "feature_algorithm", "capture_contract", "measurement_mode", "capture_configuration"];

type Capture = {
  beginFeatureCapture: () => void;
  finishFeatureCapture: () => AudioFeatureSummary | null;
};

function validLease(value: unknown, songId: string): value is AudioObservationLease {
  if (!value || typeof value !== "object") return false;
  const lease = value as AudioObservationLease;
  const binding = lease.binding;
  const id = (value: unknown, length: number) => typeof value === "string" && new RegExp(`^[a-f0-9]{${length}}$`).test(value);
  return id(lease.observation_id, 32) && Boolean(binding)
    && Object.keys(binding).length === BINDING_FIELDS.length && Object.keys(binding).every(key => BINDING_FIELDS.includes(key))
    && id(binding.library_generation, 32) && id(binding.song_revision, 32) && id(binding.metadata_sha256, 64)
    && binding.song_id === songId && binding.feature_algorithm === "web-audio-v1"
    && binding.capture_contract === "media-clock-v1" && binding.measurement_mode === "foreground"
    && binding.capture_configuration === "fft2048-s078-bands-v1"
    && Number.isFinite(lease.expires_at)
    && lease.expires_at * 1000 > Date.now() + SUBMIT_MARGIN_MS
    && lease.expires_at * 1000 <= Date.now() + LEASE_MS + 1_000;
}

function observationSummary(summary: AudioFeatureSummary | null): AudioObservationPacket["summary"] | null {
  if (!summary || summary.algorithm_version !== "web-audio-v1") return null;
  const values = Object.fromEntries(SUMMARY_FIELDS.map(key => [key, summary[key]])) as AudioObservationPacket["summary"];
  if (!SUMMARY_FIELDS.every(key => typeof values[key] === "number" && Number.isFinite(values[key]))) return null;
  if (values.sample_seconds < 12 || values.sample_seconds > 86400 || values.energy_peak <= 0
    || values.energy_mean > values.energy_peak + 1e-6 || values.low_ratio + values.brightness > 1 + 1e-6) return null;
  if (!SUMMARY_FIELDS.every(key => values[key] >= -1e-6
    && values[key] <= (key === "sample_seconds" ? 86400 : key === "beat_density" ? 300 : 1) + 1e-6)) return null;
  return values;
}

// 只负责一次前台观察；不拥有播放状态，不将新观察写回Song.audio_features或旧聚合。
export function createForegroundObservation(capture: Capture, canCapture: () => boolean) {
  type Session = { songId: string; request: AbortController; lease: AudioObservationLease | null; timer: number };
  let current: Session | null = null;
  let disposed = false;
  let writes = 0;

  function write(operation: () => Promise<unknown>) {
    // 快速切歌不积累无界待发队列；保存/取消都只有两路、五秒的现有HTTP边界。
    if (writes >= MAX_WRITES) return;
    writes += 1;
    void Promise.resolve().then(operation).catch(() => {}).finally(() => { writes -= 1; });
  }

  function cancel(lease: AudioObservationLease) {
    write(() => cancelAudioObservation(lease.observation_id));
  }

  function finish() {
    const session = current;
    current = null;
    if (!session) return;
    session.request.abort();
    window.clearTimeout(session.timer);
    const summary = session.lease ? observationSummary(capture.finishFeatureCapture()) : null;
    if (!session.lease) return;
    if (summary && Date.now() < session.lease.expires_at * 1000) {
      const packet: AudioObservationPacket = {
        ...session.lease.binding, observation_id: session.lease.observation_id, summary,
        source_extent: "unknown", confidence: "uncalibrated",
      };
      write(() => submitAudioObservation(packet));
    } else cancel(session.lease);
  }

  function start(songId: string) {
    if (disposed || current || !canCapture()) return;
    const session: Session = { songId, request: new AbortController(), lease: null, timer: 0 };
    current = session;
    // 申请不等待音源，也不阻塞playing；绑定前没有capture，晚到后从新起点累计。
    void prepareAudioObservation(songId, session.request.signal).then(value => {
      if (!validLease(value, songId)) return;
      if (current !== session || disposed || !canCapture()) { cancel(value); return; }
      session.lease = value;
      capture.beginFeatureCapture();
      session.timer = window.setTimeout(() => {
        if (current === session) finish();
      }, value.expires_at * 1000 - Date.now() - SUBMIT_MARGIN_MS);
    }).catch(() => {
      // 旧服务/不支持合同/限额/超时都安静放弃，不换编号重采，不投回旧路由。
    });
  }

  function dispose() {
    disposed = true;
    finish();
  }

  return { start, finish, dispose };
}
