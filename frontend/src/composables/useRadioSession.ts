import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { adjustRecommendation, bindDJQueue, chat, clearSession, getAutoQueue, getDJProgram, getSessionMessages, sendAudioFeatures, sendListeningEvent, sendSongFeedback } from "../services/api";
import type { AdjustmentOperation, AudioFeatureSummary, ChatMessage, ChatResponse, DJLength, DJProgram, FeedbackAction, ListeningContext, ListeningEventType, PlaylistEntry, PlaylistView, RecommendationRound, Song } from "../types/radio";

const SESSION_KEY = "personal-radio-session";
const ROUNDS_KEY = "personal-radio-rounds";
const DJ_LENGTH_KEY = "personal-radio-dj-length";

export function useRadioSession(notify: (message: string) => void) {
  const messages = ref<ChatMessage[]>([]);
  const songs = ref<Song[]>([]);
  const rounds = ref<RecommendationRound[]>([]);
  const playlistView = ref<PlaylistView>("queue");
  const loading = ref(false);
  const loadingHint = ref("");
  let chatRequest: AbortController | null = null;
  let slowTimer = 0;
  const djProgram = ref<DJProgram | null>(null);
  const storedLength = localStorage.getItem(DJ_LENGTH_KEY);
  const djLength = ref<DJLength | "auto">(storedLength === "short" || storedLength === "normal" ? storedLength : "auto");
  let programRequest: AbortController | null = null;
  let queryingBatch: string | undefined;
  let programTimer = 0;
  let sessionRevision = 0;
  let clearing = false;
  // 手动听歌也应形成本机收听事实，不应要求用户先和 AI 聊一句才有身份。
  const initialSessionId = localStorage.getItem(SESSION_KEY) || crypto.randomUUID();
  localStorage.setItem(SESSION_KEY, initialSessionId);
  // 旧浏览器以原身份作为记忆归属；此键独立保留，不随聊天清空重建。
  if (!localStorage.getItem('personal-radio-memory-owner')) {
    localStorage.setItem('personal-radio-memory-owner', initialSessionId);
  }
  const sessionId = ref(initialSessionId);

  function setDJLength(length: DJLength) {
    djLength.value = length;
    localStorage.setItem(DJ_LENGTH_KEY, length);
  }

  const historyQueue = computed(() => rounds.value.flatMap((round) => round.songs));
  const historyCount = computed(() => historyQueue.value.length);
  const playlistEntries = computed<PlaylistEntry[]>(() => {
    if (playlistView.value === "queue") return songs.value.map((song) => ({ song, heading: "" }));
    if (playlistView.value !== "history") return [];
    return rounds.value.flatMap((round, index) => round.songs.map((song, songIndex) => ({
      song,
      heading: songIndex === 0 ? `第 ${index + 1} 轮 · ${(round.answer || "本轮推荐").replace(/\s+/g, " ").slice(0, 28)}` : "",
    })));
  });

  function saveRounds() {
    if (!sessionId.value) return;
    localStorage.setItem(ROUNDS_KEY, JSON.stringify({ sessionId: sessionId.value, rounds: rounds.value }));
  }

  function stopProgramQuery() {
    programRequest?.abort();
    programRequest = null;
    queryingBatch = undefined;
    if (programTimer) window.clearTimeout(programTimer);
    programTimer = 0;
  }

  function updateOpeningMessage(program: DJProgram) {
    if (program.message_id === undefined) return;
    const message = messages.value.find((item) => item.role === "assistant" && item.id === program.message_id);
    const opening = program.segments?.find((segment) => segment.position === 0);
    if (message && opening) message.content = opening.text;
  }

  function acceptProgram(program: DJProgram) {
    djProgram.value = program;
    updateOpeningMessage(program);
    const round = rounds.value.find((r) => r.batch_id === program.batch_id);
    if (!round) return;
    round.dj_program = program;
    for (const song of round.songs) {
      song.round_intro = program.text;
      song.round_program_version = program.program_version;
      song.round_segments = program.segments;
      song.round_opening_priority = program.opening_priority;
      song.round_opening_independent = program.opening_independent;
      song.round_copy_status = program.copy_status;
      song.round_opening_status = program.opening_status;
    }
    saveRounds();
  }

  function queryProgram(batchId: string) {
    const round = rounds.value.find(item => item.batch_id === batchId);
    const order = round?.playback_song_ids;
    const needsBinding = !!round?.dj_program?.queue_order_required && !round.dj_program.playback_song_ids;
    if ((needsBinding && !order) || queryingBatch === batchId) return;
    stopProgramQuery();
    const request = new AbortController();
    programRequest = request;
    queryingBatch = batchId;
    const revision = sessionRevision;
    const identity = sessionId.value;
    let attempts = 0;
    let pendingBind = needsBinding;
    const poll = async () => {
      if (request.signal.aborted || revision !== sessionRevision || clearing) return;
      const timeout = window.setTimeout(() => request.abort(), 5_000);
      try {
        const program = pendingBind && order
          ? await bindDJQueue(identity, batchId, order, request.signal)
          : await getDJProgram(identity, batchId, request.signal);
        if (request.signal.aborted || revision !== sessionRevision || clearing) return;
        pendingBind = false;
        acceptProgram(program);
        if (program.status === "pending") {
          if (++attempts < 60) programTimer = window.setTimeout(() => { void poll(); }, 1_500);
          else {
            acceptProgram({ ...program, status: "fallback", copy_status: "unavailable", copy_reasons: ["program_wait_timeout"] });
            stopProgramQuery();
          }
        } else stopProgramQuery();
      } catch {
        // 查询失败有明确收尾，不把“查询中”永远留在界面；迟到旧任务仍不能更新。
        if (programRequest === request && revision === sessionRevision && !clearing && djProgram.value?.batch_id === batchId) {
          acceptProgram({ ...djProgram.value, status: "fallback", copy_status: "unavailable", copy_reasons: ["program_read_failed"] });
          stopProgramQuery();
        }
      }
      finally { window.clearTimeout(timeout); }
    };
    void poll();
  }

  function confirmPlaybackQueue(queue: Song[]) {
    const round = rounds.value.at(-1);
    if (!round?.batch_id || !round.dj_program?.queue_order_required || round.playback_song_ids) return;
    const ordered = queue.filter(song => song.batch_id === round.batch_id);
    if (!ordered.length) return;
    round.playback_song_ids = ordered.map(song => song.id);
    for (const song of ordered) song.round_queue_order_fixed = true;
    round.songs = ordered;
    songs.value = ordered;
    // 先保存播放器定下的顺序，刷新页面时使用同一队列。
    saveRounds();
    if (round.dj_program.status === "pending") queryProgram(round.batch_id);
  }

  function acceptResponse(data: ChatResponse): Song[] {
    if (data.session_id) {
      sessionId.value = data.session_id;
      localStorage.setItem(SESSION_KEY, data.session_id);
    }
    messages.value.push({ id: data.message_id, role: "assistant", content: data.answer,
      notices: data.dj_program?.notices, diagnostic: data.stats?.diagnostics || undefined });
    if (data.stats?.queue_preserved === true) {
      // 返回空操作结果，保留推荐卡片与节目；调用方也不会重建当前播放队列。
      if (djProgram.value?.status === "pending") queryProgram(djProgram.value.batch_id);
      return [];
    }
    songs.value = (data.songs || []).map((song) => ({ ...song,
      batch_id: data.batch_id || undefined, round_intro: data.dj_program ? data.dj_program.text : song.round_intro,
      round_program_version: data.dj_program?.program_version, round_segments: data.dj_program?.segments,
      round_opening_priority: data.dj_program?.opening_priority, round_opening_independent: data.dj_program?.opening_independent, round_copy_status: data.dj_program?.copy_status,
      round_opening_status: data.dj_program?.opening_status }));
    if (songs.value.length) {
      rounds.value.push({ answer: data.answer, songs: songs.value, batch_id: data.batch_id || undefined, dj_program: data.dj_program });
      saveRounds();
      djProgram.value = data.dj_program || null;
      if (data.dj_program) updateOpeningMessage(data.dj_program);
      if (data.dj_program?.status === "pending" && data.batch_id) queryProgram(data.batch_id);
    } else if (djProgram.value?.status === "pending") queryProgram(djProgram.value.batch_id);
    return songs.value;
  }

  function restoreRounds() {
    if (!sessionId.value) return;
    try {
      const saved = JSON.parse(localStorage.getItem(ROUNDS_KEY) || "null") as {
        sessionId?: string;
        rounds?: RecommendationRound[];
      } | null;
      if (!saved || saved.sessionId !== sessionId.value || !Array.isArray(saved.rounds)) return;
      rounds.value = saved.rounds;
      const latest = saved.rounds.at(-1);
      if (latest?.songs?.length) {
        songs.value = latest.songs;
        djProgram.value = latest.dj_program || null;
        if (latest.batch_id && latest.dj_program) {
          if (latest.playback_song_ids) {
            for (const song of latest.songs) song.round_queue_order_fixed = true;
          }
          if (latest.dj_program.status === "pending") queryProgram(latest.batch_id);
        }
      }
    } catch { localStorage.removeItem(ROUNDS_KEY); }
  }

  async function restoreMessages() {
    if (!sessionId.value) return;
    const revision = sessionRevision;
    try {
      const restored = await getSessionMessages(sessionId.value);
      if (revision === sessionRevision && !clearing) {
        messages.value = restored;
        if (djProgram.value) updateOpeningMessage(djProgram.value);
      }
    }
    catch { /* 历史恢复失败不影响新一轮点歌。 */ }
  }

  function stopChatWait() {
    chatRequest?.abort();
    chatRequest = null;
    if (slowTimer) window.clearTimeout(slowTimer);
    slowTimer = 0;
    loadingHint.value = "";
  }

  function beginChatWait() {
    stopChatWait();
    const request = new AbortController();
    chatRequest = request;
    // 不猜测当前服务端阶段；编号和真实阶段在请求诊断中查看。
    slowTimer = window.setTimeout(() => {
      loadingHint.value = "这次等待较久，可能在首次加载模型或等待外部服务。音乐可以继续；AI 设置中可查看请求诊断。";
    }, 8_000);
    return request;
  }

  async function sendMessage(message: string, currentSongId?: string, currentBatchId?: string, currentQueueSongIds?: string[],
    onReplacement?: (replacement: NonNullable<NonNullable<ChatResponse["stats"]>["replacement"]>) => boolean): Promise<Song[]> {
    const trimmed = message.trim();
    if (!trimmed || loading.value || clearing) return [];
    const revision = ++sessionRevision;
    stopProgramQuery();
    messages.value.push({ role: "user", content: trimmed });
    loading.value = true;
    const request = beginChatWait();
    try {
      const data = await chat(trimmed, sessionId.value || undefined, currentSongId, request.signal, djLength.value,
        currentBatchId, currentQueueSongIds);
      if (revision !== sessionRevision || clearing) return [];
      if (data.stats?.replacement && onReplacement?.(data.stats.replacement) === false) {
        return acceptResponse({ ...data, answer: "等待回复时队列已经变了，这次没有换歌。",
          dj_program: null, stats: { ...data.stats, queue_preserved: true } });
      }
      return acceptResponse(data);
    } catch (error) {
      if (revision !== sessionRevision || clearing) return [];
      messages.value.push({ role: "assistant", content: error instanceof Error ? error.message : "网络好像被雨声盖住了" });
      return [];
    } finally {
      if (revision === sessionRevision) { loading.value = false; stopChatWait(); }
    }
  }

  async function adjustRound(operation: AdjustmentOperation, batchId: string, songId?: string): Promise<Song[]> {
    if (loading.value || clearing) return [];
    const revision = ++sessionRevision;
    stopProgramQuery();
    loading.value = true;
    const request = beginChatWait();
    try {
      const labels: Record<AdjustmentOperation, string> = { replace: "只换这一首", gentler: "更温柔一点", energize: "更有劲一点", "other-artists": "换些歌手", refresh: "保持方向，换一批" };
      const data = await adjustRecommendation(sessionId.value, batchId, operation, songId, request.signal, djLength.value);
      if (revision !== sessionRevision || clearing) return [];
      messages.value.push({ role: "user", content: labels[operation] });
      return acceptResponse(data);
    } catch (error) {
      if (revision === sessionRevision && !clearing) notify(error instanceof Error ? error.message : "调整未完成，原推荐已保留");
      return [];
    } finally { if (revision === sessionRevision) { loading.value = false; stopChatWait(); } }
  }

  async function feedback(song: Song, action: FeedbackAction) {
    if (!sessionId.value) {
      notify("先和 DJ 聊一句，再记录你的偏好");
      return;
    }
    const revision = sessionRevision;
    if (clearing || loading.value) { notify("请等当前选歌完成，再记录意见"); return; }
    try {
      await sendSongFeedback(sessionId.value, song, action);
      if (revision !== sessionRevision || clearing) return;
      if (action === 'reference_like' || action === 'reference_unlike') {
        notify('已记录这首与参考曲的关系；只作用于对应参考方向，可在DJ记忆撤销');
        return;
      }
      notify(action === "not_now" ? "已记下：当前任务下次选歌会避开这首，换任务后失效；当前播放不变" : action === "like" ? "记住了，以后会多留意这首歌" : "收到，以后会少推这首歌");
    } catch (error) {
      if (revision === sessionRevision && !clearing) notify(error instanceof Error ? error.message : "意见暂时没有保存成功");
    }
  }

  function reportListeningEvent(
    song: Song,
    eventType: ListeningEventType,
    positionSeconds: number,
    durationSeconds?: number,
    context?: ListeningContext,
  ) {
    if (!sessionId.value) return;
    // 行为采集不能阻塞音乐播放；失败时安静放弃，下一次事件仍可继续提交。
    void sendListeningEvent(
      sessionId.value, song, eventType, positionSeconds, durationSeconds, context,
    ).catch(() => undefined);
  }

  function reportAudioFeatures(song: Song, summary: AudioFeatureSummary) {
    // 声学摘要属于歌曲档案，不依赖聊天 session；失败不能干扰切歌和播放。
    void sendAudioFeatures(song.id, summary).catch(() => undefined);
  }

  async function extendQueueFromSong(song: Song, signal?: AbortSignal): Promise<Song[]> {
    if (clearing || signal?.aborted) return [];
    const revision = sessionRevision;
    try {
      const data = await getAutoQueue(song.id, sessionId.value || undefined, signal, djLength.value);
      // 切歌/清空/新对话后的迟到结果也不能写回历史，否则 UI 虽没续播却留下幽灵推荐。
      if (signal?.aborted || clearing || revision !== sessionRevision) return [];
      const next = (data.songs || []).map((item) => ({
        ...item,
        batch_id: data.batch_id || undefined,
        round_intro: data.dj_program ? data.dj_program.text : item.round_intro,
        round_program_version: data.dj_program?.program_version, round_segments: data.dj_program?.segments,
      round_opening_priority: data.dj_program?.opening_priority, round_opening_independent: data.dj_program?.opening_independent, round_copy_status: data.dj_program?.copy_status,
      round_opening_status: data.dj_program?.opening_status,
      }));
      if (next.length) {
        // 补充候选的限制写入现有轮次并提示；迟到/取消结果已在上方拒绝，不留下假提示。
        const notice = data.message || "";
        songs.value = next;
        rounds.value.push({ answer: `智能续播 · 接在《${song.title}》之后${notice ? ` · ${notice}` : ""}`, songs: next, batch_id: data.batch_id || undefined, dj_program: data.dj_program });
        saveRounds();
        djProgram.value = data.dj_program || null;
        if (notice) notify(notice);
        if (data.dj_program?.status === "pending" && data.batch_id) queryProgram(data.batch_id);
      }
      return next;
    } catch {
      return [];
    }
  }

  async function clear() {
    sessionRevision += 1;
    clearing = true;
    loading.value = false;
    stopChatWait();
    stopProgramQuery();
    djProgram.value = null;
    try {
      if (sessionId.value) await clearSession(sessionId.value);
    } catch { notify("服务端记录暂时未清空，本地界面已重置"); }
    messages.value = [];
    songs.value = [];
    rounds.value = [];
    localStorage.removeItem(ROUNDS_KEY);
    clearing = false;
  }

  onMounted(() => {
    restoreRounds();
    void restoreMessages();
  });
  onBeforeUnmount(() => { ++sessionRevision; stopChatWait(); stopProgramQuery(); });

  return {
    sessionId,
    messages,
    songs,
    rounds,
    playlistView,
    loading,
    loadingHint,
    djProgram,
    djLength,
    setDJLength,
    historyQueue,
    historyCount,
    playlistEntries,
    sendMessage,
    adjustRound,
    feedback,
    reportListeningEvent,
    reportAudioFeatures,
    extendQueueFromSong,
    confirmPlaybackQueue,
    clear,
  };
}
