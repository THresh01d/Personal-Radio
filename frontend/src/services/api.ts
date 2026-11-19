import type { AudioFeatureSummary, ChatMessage, ChatResponse, FeedbackAction, ListeningContext, ListeningEventType, LLMProviderId, LLMSettings, LocalLibraryPlaylist, LocalLibrarySongs, LoginStatus, LyricLine, NcmPlaylist, NcmSyncResult, NcmSyncScope, QqPlaylist, SetupStatus, Song, TasteProfile } from "../types/radio";
import type { ReviewScope, SongReview, SongReviewSnapshot } from "../types/radio";
import type { LibraryRemovalTarget, LibraryRemovalPreview, RemovedLibraryItem } from "../types/libraryRemoval";
import type { DJLength } from "../types/radio";
import type { HotkeyConfig, HotkeyStatus } from "../types/hotkeys";
import type { MusicPlatform } from "../types/radio";
import type { OnlineCollection, OnlineSearchPage, OnlineTracksPage, SearchKind } from "../types/onlineCatalog";
const REVIEW_TIMEOUT = "审核等待已超时，请重新读取记录确认状态，不要重复点击；停止等待不一定取消服务端操作。";

export class ApiError extends Error {
  constructor(message: string, readonly status: number, readonly retryAfterMs?: number) {
    super(message);
    this.name = "ApiError";
  }
}

export function getTtsHealth(signal?: AbortSignal): Promise<import("../types/voice").TtsHealth> {
  return boundedJson("/api/tts/status", { signal, cache: "no-store" }, "语音状态暂时无法读取，请刷新页面后重试。", 5_000);
}

export async function checkTtsHealth(signal?: AbortSignal): Promise<import("../types/voice").TtsHealth> {
  await renewLocalAccess(signal);
  // 检测会真实合成，POST只提交一次，不能因重试重复访问上游。
  return boundedJson("/api/tts/check", { method: "POST", signal, cache: "no-store" },
    "在线语音检测未完成，请稍后再试。", 12_000, "语音检测等待超时，请稍后刷新状态。");
}

export async function getSongReview(songId: string, sessionId: string, batchId?: string, offset = 0, signal?: AbortSignal): Promise<SongReviewSnapshot> {
  const query = new URLSearchParams({ session_id: sessionId, offset: String(offset) });
  if (batchId) query.set("batch_id", batchId);
  return boundedJson(`/api/profile-reviews/songs/${encodeURIComponent(songId)}?${query}`, { signal }, "歌曲审核暂时无法读取", 15_000, REVIEW_TIMEOUT);
}

export async function proposeSongReview(songId: string, body: { metadata_sha256: string; scope: ReviewScope; genres?: string[]; batch_id?: string; session_id?: string }, signal?: AbortSignal): Promise<SongReview> {
  return boundedJson(`/api/profile-reviews/songs/${encodeURIComponent(songId)}/proposals`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body), signal,
  }, "待确认提案未保存，请重新读取后检查", 15_000, REVIEW_TIMEOUT);
}

export async function actSongReview(item: SongReview, operation: "confirm" | "revoke", metadata: string, sessionId: string, signal?: AbortSignal): Promise<SongReview> {
  return boundedJson(`/api/profile-reviews/${encodeURIComponent(item.review_id)}/${operation}`, {
    method: "POST", headers: { "Content-Type": "application/json" }, signal,
    body: JSON.stringify({ expected_revision: item.revision, metadata_sha256: metadata, confirmed_scope: operation === "confirm" ? item.payload.scope : undefined,
      acknowledged: true, session_id: item.payload.scope === "query_positive" ? sessionId : undefined }),
  }, "审核操作未完成，请重新读取后检查", 15_000, REVIEW_TIMEOUT);
}

async function readJson<T>(response: Response, fallback: string, maxRetrySeconds = 15): Promise<T> {
  if (!response.ok) {
    let message = fallback;
    try {
      const data = await response.json() as { detail?: string; error?: string; message?: string };
      message = data.detail || data.error || data.message || fallback;
    } catch { /* 非 JSON 错误页使用调用方提供的中文提示。 */ }
    const requestId = response.headers.get("X-Request-ID") || "";
    const code = response.headers.get("X-Failure-Code") || "";
    // 只展示服务端固定码和生成编号，不能把原 URL/请求内容带入错误信息。
    const diagnostic = /^[a-f0-9]{16}$/.test(requestId) ? `（请求 ${requestId}${/^[a-z_]{1,32}$/.test(code) ? ` · ${code}` : ""}）` : "";
    const retryAfter = response.headers.get("Retry-After") || "";
    const retrySeconds = /^[1-9][0-9]?$/.test(retryAfter) ? Number(retryAfter) : 0;
    const retryAfterMs = retrySeconds && retrySeconds <= maxRetrySeconds ? retrySeconds * 1000 : undefined;
    throw new ApiError(message + diagnostic, response.status, retryAfterMs);
  }
  return response.json() as Promise<T>;
}

async function boundedJson<T>(url: string, init: RequestInit, fallback: string, waitMs: number, timeoutMessage = "页面等待已超时，服务端可能仍在处理。现有播放不受影响；可查看 AI 设置里的请求诊断后重试。"): Promise<T> {
  const controller = new AbortController();
  const external = init.signal;
  const cancel = () => controller.abort();
  external?.addEventListener("abort", cancel, { once: true });
  if (external?.aborted) cancel();
  let timedOut = false;
  const timer = window.setTimeout(() => { timedOut = true; controller.abort(); }, waitMs);
  try {
    return await readJson<T>(await fetch(url, { ...init, signal: controller.signal }), fallback);
  } catch (error) {
    if (timedOut) throw new Error(timeoutMessage);
    throw error;
  } finally {
    window.clearTimeout(timer);
    external?.removeEventListener("abort", cancel);
  }
}

async function readOnlineCatalog<T>(path: string, signal: AbortSignal) {
  const request = () => boundedJson<T>(`/api/online${path}`, { signal, cache: "no-store" },
    "暂时无法读取在线目录，请重试。", 11_000, "平台响应超时，请重试。");
  try {
    return await request();
  } catch (error) {
    if (!(error instanceof ApiError) || error.status !== 401 || signal.aborted) throw error;
    await renewLocalAccess(signal);
    return request();
  }
}

export function getRecommendedPlaylists(signal: AbortSignal) {
  return readOnlineCatalog<{ playlists: OnlineCollection[] }>("/netease/recommendations", signal);
}

export function searchOnlineCatalog(platform: MusicPlatform, query: string, kind: SearchKind, page: number, signal: AbortSignal) {
  const params = new URLSearchParams({ q: query, kind, page: String(page) });
  return readOnlineCatalog<OnlineSearchPage>(`/${platform}/search?${params}`, signal);
}

export function getOnlineTracks(collection: OnlineCollection, page: number, signal: AbortSignal) {
  const { platform, kind, id } = collection;
  return readOnlineCatalog<OnlineTracksPage>(`/${platform}/${kind}/${encodeURIComponent(id)}/tracks?page=${page}`, signal);
}

export async function adjustRecommendation(sessionId: string, batchId: string, operation: import("../types/radio").AdjustmentOperation, songId?: string, signal?: AbortSignal, djLength?: DJLength | "auto"): Promise<ChatResponse> {
  return boundedJson("/api/recommendations/adjust", {
    signal,
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: sessionId, batch_id: batchId, operation, song_id: songId, dj_length: djLength, bind_dj_queue: true }),
  }, "这次调整暂时未完成，原推荐仍然保留", 90_000);
}

export async function getDJProgram(sessionId: string, batchId: string, signal?: AbortSignal): Promise<import("../types/radio").DJProgram> {
  const response = await fetch(`/api/recommendations/${encodeURIComponent(batchId)}/dj?session_id=${encodeURIComponent(sessionId)}`, { signal });
  return readJson(response, "背景资料暂时不可用");
}

export async function bindDJQueue(sessionId: string, batchId: string, songIds: string[], signal?: AbortSignal): Promise<import("../types/radio").DJProgram> {
  const response = await fetch(`/api/recommendations/${encodeURIComponent(batchId)}/dj/queue`, {
    method: "POST", signal, headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: sessionId, song_ids: songIds }),
  });
  return readJson(response, "暂时未能同步 DJ 播报顺序");
}

export async function chat(message: string, sessionId?: string, currentSongId?: string, signal?: AbortSignal,
  djLength?: DJLength | "auto", currentBatchId?: string, currentQueueSongIds?: string[]): Promise<ChatResponse> {
  return boundedJson("/api/chat", {
    signal,
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      message,
      session_id: sessionId || undefined,
      current_song_id: currentSongId || undefined,
      current_batch_id: currentBatchId || undefined,
      current_queue_song_ids: currentQueueSongIds,
      dj_length: djLength,
      bind_dj_queue: true,
      memory_owner_id: typeof localStorage !== 'undefined' ? localStorage.getItem('personal-radio-memory-owner') || undefined : undefined,
    }),
  }, "DJ 暂时没有收到信号，请稍后再试", 90_000);
}

export async function getSessionMessages(sessionId: string): Promise<ChatMessage[]> {
  const response = await fetch(`/api/sessions/${encodeURIComponent(sessionId)}/messages`);
  const data = await readJson<{ messages?: ChatMessage[] }>(response, "历史对话读取失败");
  return (data.messages || []).filter((message) => message.role === "user" || message.role === "assistant");
}

export async function clearSession(sessionId: string): Promise<void> {
  const response = await fetch("/api/chat/clear", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: sessionId }),
  });
  await readJson<unknown>(response, "清空对话失败");
}

export async function sendSongFeedback(sessionId: string, song: Song, action: FeedbackAction): Promise<void> {
  if (action === 'reference_like' || action === 'reference_unlike') {
    if (!song.batch_id || !song.reference_song_id) throw new Error('请从有参考曲的推荐批次中操作');
    await boundedJson('/api/reference-feedback', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: sessionId, batch_id: song.batch_id, song_id: song.id, value: action === 'reference_like' ? 'like' : 'unlike' }),
    }, '参考意见没有确认保存，请重新读取记忆核对', 10_000);
    return;
  }
  if (action === "not_now") {
    if (!song.batch_id) throw new Error("请从当前任务的推荐歌曲里操作");
    const response = await fetch("/api/task-feedback", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: sessionId, song_id: song.id, batch_id: song.batch_id }),
    });
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      throw new Error(typeof data.detail === "string" ? data.detail : "这次意见暂时没有保存成功");
    }
    return;
  }
  const response = await fetch("/api/feedback", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: sessionId, song_id: song.id, action, artist: song.artist }),
  });
  await readJson<unknown>(response, "偏好保存失败");
}

export async function sendListeningEvent(
  sessionId: string,
  song: Song,
  eventType: ListeningEventType,
  positionSeconds: number,
  durationSeconds?: number,
  context?: ListeningContext,
): Promise<void> {
  const response = await fetch("/api/listening-events", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      event_id: crypto.randomUUID(),
      session_id: sessionId,
      batch_id: song.batch_id || undefined,
      song_id: song.id,
      event_type: eventType,
      context,
      position_seconds: Math.max(0, positionSeconds || 0),
      duration_seconds: durationSeconds && durationSeconds > 0 ? durationSeconds : undefined,
    }),
  });
  await readJson<unknown>(response, "收听记录保存失败");
}

export async function sendAudioFeatures(songId: string, summary: AudioFeatureSummary): Promise<void> {
  const response = await fetch("/api/audio-features", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ song_id: songId, ...summary }),
  });
  await readJson<unknown>(response, "声学档案保存失败");
}

export function prepareAudioObservation(songId: string, signal?: AbortSignal): Promise<unknown> {
  return boundedJson("/api/audio-observations/prepare", {
    method: "POST", cache: "no-store", signal, headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ song_id: songId, measurement_mode: "foreground" }),
  }, "声学观察准备未完成", 5_000, "声学观察准备等待超时");
}

export function submitAudioObservation(packet: import("../types/audioObservation").AudioObservationPacket): Promise<unknown> {
  // 写请求只发送一次；未知结果不换编号重试，也不回投旧聚合接口。
  return boundedJson("/api/audio-observations", {
    method: "POST", cache: "no-store", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(packet),
  }, "声学观察保存未完成", 5_000, "声学观察保存等待超时，服务端可能已接收");
}

export function cancelAudioObservation(observationId: string): Promise<unknown> {
  return boundedJson("/api/audio-observations/cancel", {
    method: "POST", cache: "no-store", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ observation_id: observationId }),
  }, "声学观察取消未完成", 5_000, "声学观察取消等待超时");
}

export async function getPendingAudioFeatures(songIds: string[], signal?: AbortSignal): Promise<string[]> {
  const response = await fetch("/api/audio-features/pending", {
    method: "POST",
    signal,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ song_ids: songIds }),
  });
  const data = await readJson<{ song_ids?: string[] }>(response, "声学覆盖率读取失败");
  return data.song_ids || [];
}

export async function getAutoQueue(songId: string, sessionId?: string, signal?: AbortSignal, djLength?: DJLength | "auto"): Promise<{ songs: Song[]; batch_id?: string | null; dj_program?: import("../types/radio").DJProgram | null; message?: string }> {
  const response = await fetch("/api/auto-queue", {
    method: "POST",
    signal,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ song_id: songId, session_id: sessionId || undefined, count: 3, dj_length: djLength, bind_dj_queue: true }),
  });
  return readJson(response, "智能续播暂时没有找到信号");
}

export async function getLocalLibraryPlaylists(): Promise<LocalLibraryPlaylist[]> {
  const response = await fetch("/api/library/playlists");
  const data = await readJson<{ playlists?: LocalLibraryPlaylist[] }>(response, "本地歌单读取失败");
  return data.playlists || [];
}

const REMOVAL_WAIT = "移除状态尚未确认，请重新核对状态；不会自动重复提交。";
export function previewLibraryRemoval(target: LibraryRemovalTarget, signal?: AbortSignal): Promise<LibraryRemovalPreview> {
  return boundedJson("/api/library/removal-preview", {
    method: "POST", headers: { "Content-Type": "application/json" }, signal,
    body: JSON.stringify({ kind: target.kind, target_id: target.target_id }),
  }, "移除范围暂时无法读取", 10_000, REMOVAL_WAIT);
}
export function removeLibraryItem(target: LibraryRemovalTarget, revision: string): Promise<LibraryRemovalPreview> {
  return boundedJson("/api/library/removals", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ kind: target.kind, target_id: target.target_id, expected_revision: revision }),
  }, "移除未完成，请重新核对状态", 15_000, REMOVAL_WAIT);
}
export function getRemovedLibraryItems(query = "", signal?: AbortSignal): Promise<{ items: RemovedLibraryItem[]; playlist_ids: string[] }> {
  return boundedJson(`/api/library/removed?q=${encodeURIComponent(query)}`, { signal, cache: "no-store" }, "已移除记录读取失败", 10_000, REMOVAL_WAIT);
}
export function allowLibraryReimport(item: RemovedLibraryItem): Promise<{ ok: boolean }> {
  return boundedJson("/api/library/removals/allow-reimport", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ kind: item.kind, target_id: item.target_id }),
  }, "导入许可未更新，请刷新状态", 10_000, REMOVAL_WAIT);
}

export async function getLocalLibrarySongs(input: {
  playlistId?: string;
  query?: string;
  offset?: number;
  limit?: number;
  order?: "playlist" | "title" | "random";
} = {}): Promise<LocalLibrarySongs> {
  const params = new URLSearchParams();
  if (input.playlistId) params.set("playlist_id", input.playlistId);
  if (input.query) params.set("q", input.query);
  params.set("offset", String(input.offset || 0));
  params.set("limit", String(input.limit || 50));
  params.set("order", input.order || (input.playlistId ? "playlist" : "title"));
  const response = await fetch(`/api/library/songs?${params.toString()}`);
  return readJson<LocalLibrarySongs>(response, "本地歌曲读取失败");
}

export async function getTasteProfile(sessionId: string): Promise<TasteProfile> {
  const response = await fetch(`/api/sessions/${encodeURIComponent(sessionId)}/taste-profile`);
  return readJson<TasteProfile>(response, "DJ 的记忆暂时读取失败");
}

export async function recalculateTasteProfile(sessionId: string): Promise<TasteProfile> {
  const response = await fetch(
    `/api/sessions/${encodeURIComponent(sessionId)}/taste-profile/recalculate`,
    { method: "POST" },
  );
  return readJson<TasteProfile>(response, "画像重算失败");
}

export async function removeTaskFeedback(sessionId: string, taskId: string, songId: string): Promise<void> {
  const response = await fetch(`/api/sessions/${encodeURIComponent(sessionId)}/task-feedback/${encodeURIComponent(taskId)}/${encodeURIComponent(songId)}`, { method: "DELETE" });
  if (!response.ok) throw new Error("这次意见暂时无法撤销");
}

export async function removeSceneMemory(sessionId: string, memoryId: string): Promise<void> {
  const response = await fetch(`/api/sessions/${encodeURIComponent(sessionId)}/scene-memories/${encodeURIComponent(memoryId)}`, { method: 'DELETE' });
  await readJson(response, '这条场景偏好暂时无法撤销');
}

export async function saveMemory(sessionId: string, draft: import('../types/radio').MemoryDraft): Promise<void> {
  await boundedJson('/api/memories', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ session_id: sessionId, ...draft,
      memory_owner_id: typeof localStorage !== 'undefined' ? localStorage.getItem('personal-radio-memory-owner') || undefined : undefined }),
  }, '偏好没有确认保存，请重新读取记忆核对', 10_000);
}

export async function removeMemory(sessionId: string, memoryId: string): Promise<boolean> {
  const result = await boundedJson<{ removed: boolean }>(`/api/sessions/${encodeURIComponent(sessionId)}/memories/${encodeURIComponent(memoryId)}`, { method: 'DELETE' }, '偏好撤销未确认，请重新读取核对', 10_000);
  return result.removed;
}

export async function removeTasteFeedback(sessionId: string, songId: string): Promise<void> {
  const response = await fetch(
    `/api/sessions/${encodeURIComponent(sessionId)}/taste-profile/feedback/${encodeURIComponent(songId)}`,
    { method: "DELETE" },
  );
  await readJson<unknown>(response, "长期偏好撤销失败");
}

export async function removeTasteBehavior(sessionId: string, songId: string): Promise<void> {
  const response = await fetch(
    `/api/sessions/${encodeURIComponent(sessionId)}/taste-profile/behavior/${encodeURIComponent(songId)}`,
    { method: "DELETE" },
  );
  await readJson<unknown>(response, "收听证据清除失败");
}

export async function resetTasteProfile(sessionId: string): Promise<void> {
  const response = await fetch(
    `/api/sessions/${encodeURIComponent(sessionId)}/taste-profile`,
    { method: "DELETE" },
  );
  await readJson<unknown>(response, "画像重置失败");
}

export type PlayUrlResponse = {
  success?: boolean;
  url?: string;
  message?: string;
  need_login?: boolean;
  preview?: boolean;
  jump_url?: string;
};

export async function getPlayUrl(songId: string, signal?: AbortSignal): Promise<PlayUrlResponse> {
  // 播放地址有有效期；断线重取时不能复用浏览器缓存中的旧地址。
  const url = songId.startsWith("qq:")
    ? `/api/qq/playurl/${encodeURIComponent(songId.slice(3))}`
    : `/api/ncm/playurl/${encodeURIComponent(songId)}`;
  let response = await fetch(url, { signal, cache: "no-store" });
  if (response.status === 401) {
    // 服务重启会轮换本机 HttpOnly 凭证；只重试这个只读请求，不重复提交写操作。
    await renewLocalAccess(signal);
    response = await fetch(url, { signal, cache: "no-store" });
  }
  return readJson<PlayUrlResponse>(response, "播放地址获取失败");
}

type QqStatus = LoginStatus & { available?: boolean };
type QqQrStatus = Pick<LoginStatus, "nickname" | "avatar_url"> & { state: "waiting" | "scanned" | "confirmed" | "expired" | "failed" };

async function qqRequest<T>(path: string, method = "GET"): Promise<T> {
  const response = await fetch(`/api/qq${path}`, { method, cache: "no-store" });
  return readJson<T>(response, "QQ 音乐请求失败，请稍后重试", path === "/login/qr" ? 30 : 15);
}

export const getQqStatus = () => qqRequest<QqStatus>("/status");
export const createQqQr = () => qqRequest<{ id: string; image: string }>("/login/qr", "POST");
export const checkQqQr = (id: string) => qqRequest<QqQrStatus>(`/login/qr/${encodeURIComponent(id)}/check`, "POST");
export const cancelQqQr = (id: string) => qqRequest(`/login/qr/${encodeURIComponent(id)}`, "DELETE");
export const logoutQq = () => qqRequest("/logout", "POST");
export const getQqPlaylists = (offset: number) => qqRequest<{ playlists: QqPlaylist[]; more: boolean; next_offset: number | null }>(`/playlists?offset=${offset}`);
export const getQqTracks = (id: string, offset: number) => qqRequest<{ songs: Song[]; total: number | null; next_offset: number | null; incomplete: boolean }>(`/playlists/${encodeURIComponent(id)}/tracks?offset=${offset}`);

export function renewLocalAccess(signal?: AbortSignal): Promise<{ ok: boolean }> {
  return boundedJson("/api/local-access", { signal, credentials: "same-origin", cache: "no-store" }, "本机访问验证失败，请刷新页面", 5_000, "本机连接等待超时，请刷新页面重试");
}

export function getHotkeys(signal?: AbortSignal) {
  return boundedJson<HotkeyStatus>("/api/hotkeys", { signal, cache: "no-store" }, "快捷键状态暂时无法读取。", 5000);
}

export function saveHotkeys(config: HotkeyConfig, signal?: AbortSignal) {
  return boundedJson<HotkeyStatus>("/api/hotkeys", {
    method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(config), signal,
  }, "快捷键暂时无法保存。", 5000);
}

export function beginHotkeyCapture(token: string) {
  return boundedJson("/api/hotkeys/capture", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ token }),
  }, "暂时无法录入按键。", 4000);
}

export function endHotkeyCapture(token: string) {
  return boundedJson(`/api/hotkeys/capture?${new URLSearchParams({ token })}`, { method: "DELETE" }, "暂时无法结束录入。", 4000);
}

export function setHotkeyTarget(tab: string, playing: boolean, usedAt: number, signal: AbortSignal) {
  return boundedJson("/api/hotkeys/target", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ tab, playing, used_at: usedAt }), signal,
  }, "暂时无法更新快捷键目标。", 5000);
}

export async function getLyrics(songId: string): Promise<{ success: boolean; instrumental: boolean; lines: LyricLine[] }> {
  if (songId.startsWith("qq:")) return { success: false, instrumental: false, lines: [] };
  const response = await fetch(`/api/ncm/lyrics/${encodeURIComponent(songId)}`, { cache: "no-store" });
  return readJson(response, "歌词读取失败");
}

const coverRequests = new Map<string, Promise<string>>();

export function getSongCover(songId: string): Promise<string> {
  if (songId.startsWith("qq:")) return Promise.resolve("");
  const cached = coverRequests.get(songId);
  if (cached) return cached;
  const request = fetch(`/api/ncm/song-cover/${encodeURIComponent(songId)}`)
    .then((response) => readJson<{ cover_url?: string | null }>(response, "封面获取失败"))
    .then((data) => data.cover_url || "")
    .catch(() => "");
  coverRequests.set(songId, request);
  return request;
}

export function getTtsUrl(text: string, voice?: string): string {
  const query = new URLSearchParams({ text });
  if (voice) query.set("voice", voice);
  return `/api/tts?${query}`;
}

export async function getLoginStatus(): Promise<LoginStatus> {
  const response = await fetch("/api/ncm/status");
  return readJson<LoginStatus>(response, "网易云登录状态读取失败");
}

export async function createLoginQr(): Promise<{ key?: string; qrimg?: string; detail?: string }> {
  const controller = new AbortController();
  // 冷启动时 Node 网关需要生成匿名凭据，实测可能超过 20 秒；35 秒既给它
  // 足够时间，也保证端口或网络异常时弹窗不会永久停在加载状态。
  const timer = window.setTimeout(() => controller.abort(), 35_000);
  try {
    const response = await fetch("/api/ncm/login/qr", { signal: controller.signal });
    return readJson(response, "二维码获取失败");
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") {
      throw new Error("网易云登录服务响应超时，请点击刷新重试");
    }
    throw error;
  } finally {
    window.clearTimeout(timer);
  }
}

export async function checkLoginQr(key: string): Promise<{ status?: string } & Pick<LoginStatus, "nickname" | "avatar_url">> {
  const response = await fetch(`/api/ncm/login/check?key=${encodeURIComponent(key)}`, { cache: "no-store" });
  return readJson(response, "二维码状态检查失败");
}

export async function logoutNcm(): Promise<void> {
  const response = await fetch("/api/ncm/logout", { method: "POST" });
  await readJson<unknown>(response, "退出登录失败");
}

export async function getNcmPlaylists(): Promise<NcmPlaylist[]> {
  const response = await fetch("/api/ncm/playlists");
  const data = await readJson<{ ok?: boolean; playlists?: NcmPlaylist[]; error?: string }>(response, "歌单列表读取失败");
  if (!data.ok) throw new Error(data.error || "歌单列表读取失败");
  return data.playlists || [];
}

export async function syncNcmPlaylists(
  scope: NcmSyncScope,
  playlistIds: string[] = [],
  replace = false,
): Promise<NcmSyncResult> {
  const response = await fetch("/api/ncm/sync", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ scope, playlist_ids: playlistIds, replace }),
  });
  return readJson(response, "同步失败");
}

export async function pauseNcmMetadata(taskId: string): Promise<void> {
  const response = await fetch(`/api/ncm/sync/${encodeURIComponent(taskId)}/pause`, { method: "POST" });
  await readJson(response, "暂停百科补全失败");
}

export async function getNcmSyncStatus(taskId: string): Promise<NcmSyncResult> {
  const read = () => boundedJson<NcmSyncResult>(`/api/ncm/sync/${encodeURIComponent(taskId)}`, { cache: "no-store" },
    "同步进度读取失败", 10_000, "读取进度超时；后台任务可能仍在运行，请重新读取。");
  try { return await read(); }
  catch (error) {
    if (!(error instanceof ApiError) || error.status !== 401) throw error;
    // 本机服务重启会轮换访问凭证，只恢复只读进度，绝不重发同步任务。
    await renewLocalAccess();
    return read();
  }
}

export async function getLatestNcmSync(): Promise<NcmSyncResult | null> {
  const response = await fetch("/api/ncm/sync/latest", { cache: "no-store" });
  const data = await readJson<{ task?: NcmSyncResult | null }>(response, "同步任务读取失败");
  return data.task || null;
}

export async function retryNcmSync(taskId: string): Promise<NcmSyncResult> {
  const response = await fetch(`/api/ncm/sync/${encodeURIComponent(taskId)}/retry`, { method: "POST" });
  return readJson(response, "同步重试失败");
}

export async function dismissNcmSync(taskId: string): Promise<void> {
  const response = await fetch(`/api/ncm/sync/${encodeURIComponent(taskId)}/dismiss`, { method: "POST" });
  await readJson<unknown>(response, "同步任务状态更新失败");
}

export async function getLLMSettings(): Promise<LLMSettings> {
  const response = await fetch("/api/settings/llm");
  return readJson<LLMSettings>(response, "AI 设置读取失败");
}

export async function getSetupStatus(): Promise<SetupStatus> {
  const response = await fetch("/api/setup/status");
  return readJson<SetupStatus>(response, "首次启动状态读取失败");
}

export type LLMSettingsInput = {
  provider: LLMProviderId;
  model: string;
  base_url: string;
  api_key?: string;
  chatgpt_profile_id?: string;
};

export function getChatGPTStatus() {
  return boundedJson<import("../types/radio").ChatGPTStatus>("/api/settings/chatgpt", { cache: "no-store" }, "无法读取 ChatGPT 账号状态", 5_000);
}

export function getChatGPTModels(profileId: string) {
  return boundedJson<{ id: string; label: string }[]>(
    `/api/settings/chatgpt/models?profile_id=${encodeURIComponent(profileId)}`, { cache: "no-store" }, "无法读取 ChatGPT 可用模型", 22_000);
}

export function chatGPTAccountAction<T>(action: "start" | "select" | "cancel" | "disconnect", profileId?: string) {
  return boundedJson<T>(`/api/settings/chatgpt/${action}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ profile_id: profileId }),
  }, "ChatGPT 账号操作失败", 22_000);
}

export async function testLLMSettings(input: LLMSettingsInput, checkCapabilities = false): Promise<{
  ok: boolean; message: string; capabilities?: Record<"chat" | "tools" | "program_json", string>;
}> {
  return boundedJson("/api/settings/llm/test", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(checkCapabilities ? { ...input, check_capabilities: true } : input),
  }, "模型连接测试失败", checkCapabilities ? 30_000 : 25_000);
}

export async function getDiagnostics(): Promise<import("../types/radio").RequestDiagnostics> {
  return boundedJson("/api/diagnostics", { cache: "no-store" }, "请求诊断暂不可用", 5_000);
}

export async function saveLLMSettings(input: LLMSettingsInput): Promise<LLMSettings> {
  const response = await fetch("/api/settings/llm", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  });
  return readJson(response, "AI 设置保存失败");
}
