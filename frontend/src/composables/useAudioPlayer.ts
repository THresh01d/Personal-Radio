import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { ApiError, getPlayUrl } from "../services/api";
import type { AudioFeatureSummary, ListeningContext, ListeningEventType, MusicPlatform, QueueSource, Song } from "../types/radio";
import { useAudioAnalysis, type VisualMode } from "./useAudioAnalysis";

import type { PlaybackState, PlaybackMode } from "./player/types";
export type { PlaybackMode } from "./player/types";
import { createAcousticBackfill } from "./player/acousticBackfill";
import { createAnnouncement } from "./player/announcement";
import { DJ_VOICE_KEY, restoreDjVoice } from "./player/djVoice";
import { createMusicDucking } from "./player/musicDucking";
import { createLyrics } from "./player/lyrics";
import { createPlaybackQueue } from "./player/queue";
import { createForegroundObservation } from "./player/foregroundObservation";
import { createListeningEvidence } from "./player/listeningEvidence";

type AudioPlayerOptions = {
  notify: (message: string) => void;
  onLoginRequired: (platform: MusicPlatform) => void;
  onListeningEvent?: (
    song: Song,
    eventType: ListeningEventType,
    positionSeconds: number,
    durationSeconds?: number,
    context?: ListeningContext,
  ) => void;
  onAudioFeatures?: (song: Song, summary: AudioFeatureSummary) => void;
  onQueueExhausted?: (song: Song, signal?: AbortSignal) => Promise<Song[]>;
};

const ADDRESS_RETRY_DELAYS_MS = [600, 1_500] as const;

export function useAudioPlayer({ notify, onLoginRequired, onListeningEvent, onAudioFeatures, onQueueExhausted }: AudioPlayerOptions) {
  const listening = createListeningEvidence();
  const isPlaying = ref(false);
  const audioLoading = ref(false);
  const playbackError = ref("");
  const isPreview = ref(false);
  const playbackState = ref<PlaybackState>("idle");
  const elapsed = ref(0);
  const duration = ref(0);
  const volume = ref(0.8);
  const voiceVolume = ref(0.85);
  const djVoice = ref(restoreDjVoice(localStorage.getItem(DJ_VOICE_KEY)));
  const legacyRadioOn = localStorage.getItem("personal-radio-radio-on") !== "off";
  const announceSetting = localStorage.getItem("personal-radio-announce-on");
  const autoplaySetting = localStorage.getItem("personal-radio-autoplay-on");
  const announceOn = ref(announceSetting === null ? legacyRadioOn : announceSetting !== "off");
  const autoPlayOn = ref(autoplaySetting === null ? legacyRadioOn : autoplaySetting !== "off");
  const smartContinueOn = ref(localStorage.getItem("personal-radio-smart-continue") !== "off");
  const savedPlaybackMode = localStorage.getItem("personal-radio-playback-mode");
  const playbackMode = ref<PlaybackMode>(savedPlaybackMode === "shuffle" || savedPlaybackMode === "repeat-one" ? savedPlaybackMode : "sequence");

  const { currentSong, playbackQueue, queueSource, queueIndex, prepareQueue, shuffleUpcoming, assignQueue,
    playNext, reorderQueue, removeFromQueue, appendToQueue, appendPlaylist } = createPlaybackQueue({
    playbackMode,
    beforeChange: () => { invalidateSmartQueue(); cancelBackfill(); },
    beforeRemoveCurrent: () => {
      if (audio.src && startReported && !finishedCurrentSong && !interruptionReported) {
        reportListening("skip");
      }
      clearPlayer();
    },
  });
  const backfillSetting = localStorage.getItem("personal-radio-acoustic-backfill");
  const saveData = Boolean((navigator as Navigator & { connection?: { saveData?: boolean } }).connection?.saveData);
  const acousticBackfillOn = ref(backfillSetting === null ? !saveData : backfillSetting === "on");
  const smartQueueLoading = ref(false);
  const savedVisualMode = localStorage.getItem("personal-radio-visual-mode");
  const prefersReducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const visualMode = ref<VisualMode>(
    savedVisualMode === "quiet" || savedVisualMode === "atmosphere" || savedVisualMode === "immersive"
      ? savedVisualMode
      : prefersReducedMotion ? "quiet" : "atmosphere",
  );

  let audio = new Audio();
  // Web Audio 读取跨域媒体必须在设置 src 前启用 CORS。网易云当前音频 CDN
  // 已通过独立诊断验证会返回 Access-Control-Allow-Origin: *。
  audio.crossOrigin = "anonymous";
  const voiceAudio = new Audio();
  const audioAnalysis = useAudioAnalysis(audio, visualMode, () => restoreOrdinaryPlayback("音频分析失败，音乐使用普通播放"));
  let playToken = 0;
  let songRevision = 0;
  let queueRevision = 0;
  let playUrlRequest: AbortController | null = null;
  let smartQueueRequest: AbortController | null = null;
  let smartQueueTimer = 0;
  let mediaTimer = 0;
  let startReported = false;
  let finishedCurrentSong = false;
  let nextStartEvent: ListeningEventType = "play";
  let interruptionReported = false;
  let disposed = false;
  let ordinaryPlayback = false;
  let recoveryAttempts = 0;
  let pendingResume: number | null = null;
  let needsObservation = true;
  const foregroundObservation = createForegroundObservation(audioAnalysis,
    () => !disposed && !ordinaryPlayback && audioAnalysis.status.value !== "unavailable");

  const { lyricLines, lyricsInstrumental, loadLyrics } = createLyrics(() => songRevision);
  const { isAnnouncing, djPlaybackNotices, isVoiceSpeaking, isVoicePaused, isPreviewing, pauseAnnouncement, resumeAnnouncement, announceThenPlay, stopAnnouncement, skipAnnouncement, consumeAnnouncement, hasAnnouncement, previewVoice } = createAnnouncement({
    voiceAudio, voiceVolume, djVoice, announceOn, queueSource, playbackState, loadSong, notify,
    isCurrent: (token) => token === playToken,
    getCurrentSong: () => currentSong.value,
    canSpeakOverMusic: () => isPlaying.value && !audio.paused && !audioLoading.value,
  });
  const ducking = createMusicDucking(() => audio, volume, isVoiceSpeaking, isVoicePaused, isPlaying);
  const { cancelBackfill, scheduleBackfill } = createAcousticBackfill({
    canRun: () => acousticBackfillOn.value && !isPlaying.value && !isAnnouncing.value
      && !audioLoading.value && !smartQueueLoading.value && !document.hidden,
    getCandidates: () => playbackQueue.value.filter((song) => song.id !== currentSong.value?.id && song.platform !== "qq" && !song.online_catalog),
    audioAnalysis, onAudioFeatures,
  });

  const title = computed(() => currentSong.value?.title || "想听什么，告诉 DJ");
  const subtitle = computed(() => currentSong.value
    ? `${currentSong.value.artist}${currentSong.value.album ? ` · ${currentSong.value.album}` : ""}${isPreview.value ? " · 试听" : ""}`
    : "");
  const progressValue = computed(() => duration.value > 0
    ? Math.min(100, Math.max(0, (elapsed.value / duration.value) * 100))
    : 0);
  const progress = computed(() => `${progressValue.value}%`);
  const displayElapsed = computed(() => duration.value > 0 ? Math.min(elapsed.value, duration.value) : elapsed.value);
  const isDjOnAir = computed(() => isVoiceSpeaking.value && !isVoicePaused.value);
  const playerStatus = computed(() => playbackError.value
    ? "SIGNAL LOST"
    : isDjOnAir.value ? "DJ ON AIR"
      : smartQueueLoading.value ? "CURATING NEXT"
      : audioLoading.value ? "TUNING" : isPlaying.value ? "NOW PLAYING" : "STANDBY");
  const canGoPrevious = computed(() => queueIndex.value > 0 && playbackQueue.value.length > 0);
  const canGoNext = computed(() => playbackQueue.value.length > 0 && queueIndex.value < playbackQueue.value.length - 1);

  function clearMediaTimeout() {
    window.clearTimeout(mediaTimer);
    mediaTimer = 0;
  }

  function reportListening(type: ListeningEventType) {
    if (!currentSong.value || currentSong.value.platform === "qq" || currentSong.value.online_catalog) return;
    listening.sample(audio, isPlaying.value && !audio.paused && !audioLoading.value);
    onListeningEvent?.(currentSong.value, type, Math.max(0, audio.currentTime || 0),
      Number.isFinite(audio.duration) && audio.duration > 0 ? audio.duration : undefined, listening.snapshot());
  }

  function markInterrupted() {
    listening.interrupt();
    // 出错即记录一次带故障来源的中断，让后端撤回该次播放已有的弱信号。
    if (startReported && !interruptionReported && !finishedCurrentSong) {
      interruptionReported = true;
      reportListening("skip");
    }
  }

  function isPausedPlayback() {
    // 媒体事件会在 await 期间修改状态，读取此刻真实状态而不是请求开始时的状态。
    return audio.paused && playbackState.value === "paused";
  }

  function failPlayback(message: string, state: PlaybackState = "error") {
    stopAnnouncement();
    markInterrupted();
    clearMediaTimeout();
    audio.pause();
    audioAnalysis.stop();
    foregroundObservation.finish();
    needsObservation = true;
    audioLoading.value = false;
    isPlaying.value = false;
    playbackState.value = state;
    playbackError.value = message;
    cancelBackfill();
  }

  function watchMediaTimeout(token: number) {
    clearMediaTimeout();
    const media = audio;
    const position = media.currentTime;
    // 只监测加载是否完成，不推进时间；进度始终读取真实音频元素。
    mediaTimer = window.setTimeout(() => {
      mediaTimer = 0;
      if (token !== playToken || media !== audio || !audioLoading.value) return;
      // 后台页的媒体事件可能晚于计时器；真实进度仍在走就不能主动切断音乐。
      if (!media.paused && media.readyState >= 3 && media.currentTime > position + 0.05) {
        onPlaying();
        return;
      }
      if (recoverPlayback()) return;
      failPlayback("歌曲音频流加载超时，点击播放重试或切换下一首");
      notify(playbackError.value);
    }, 25_000);
  }

  function recoverPlayback() {
    const song = currentSong.value;
    if (disposed || !song || recoveryAttempts >= ADDRESS_RETRY_DELAYS_MS.length) return false;
    stopAnnouncement();
    markInterrupted();
    recoveryAttempts += 1;
    const position = pendingResume ?? (Number.isFinite(audio.currentTime) ? audio.currentTime : elapsed.value);
    const token = ++playToken;
    playUrlRequest?.abort();
    clearMediaTimeout();
    cancelBackfill();
    audio.pause();
    isPlaying.value = false;
    pendingResume = position;
    audio.removeAttribute("src");
    audio.load();
    // 只刷新当前合法播放地址；不重置队列、报幕、听歌计数或已采集的声学数据。
    notify(`歌曲音频流中断，正在从原进度重连（${recoveryAttempts}/${ADDRESS_RETRY_DELAYS_MS.length}）…`);
    void loadSong(song, token, position);
    return true;
  }

  function handlePlayFailure(error: unknown, token: number, media: HTMLAudioElement) {
    if (token !== playToken || media !== audio) return;
    if (error instanceof DOMException && error.name === "AbortError") {
      if (isPausedPlayback() || (!media.error && !media.paused && media.readyState >= 3)) return;
    }
    const blocked = error instanceof DOMException && error.name === "NotAllowedError";
    if (!blocked && (tryOrdinaryPlayback() || recoverPlayback())) return;
    failPlayback(blocked ? "浏览器阻止了自动播放，请点击播放继续" : "歌曲音频流中断，点击播放重试或切换下一首", blocked ? "blocked" : "error");
    notify(playbackError.value);
  }

  function invalidateSmartQueue() {
    queueRevision += 1;
    smartQueueRequest?.abort();
    smartQueueRequest = null;
    window.clearTimeout(smartQueueTimer);
    smartQueueTimer = 0;
    smartQueueLoading.value = false;
  }

  function flushAudioFeatures() {
    foregroundObservation.finish();
  }

  function restoreOrdinaryPlayback(reason: string) {
    if (disposed || ordinaryPlayback) return;
    foregroundObservation.finish();
    stopAnnouncement();
    ordinaryPlayback = true;
    const previous = audio;
    const url = previous.src;
    const position = pendingResume ?? Math.max(0, previous.currentTime || 0);
    const resume = Boolean(url && (!previous.paused || audioLoading.value));
    // MediaElementSource 不能可靠地“解绑”；重建普通元素才可退出被接管的输出。
    // 保留同曲计数、队列和真实位置，不把恢复误记为跳过或重播。
    const token = url ? ++playToken : playToken;
    bindMedia(previous, false);
    previous.pause();
    previous.removeAttribute("src");
    previous.load();
    audio = new Audio();
    ducking.apply();
    audioAnalysis.useOrdinaryAudio(audio, reason);
    bindMedia(audio, true);
    if (!url) return;
    const media = audio;
    const revision = songRevision;
    media.addEventListener("loadedmetadata", () => {
      if (media !== audio || revision !== songRevision || !Number.isFinite(media.duration)) return;
      media.currentTime = Math.min(position, Math.max(0, media.duration - 0.05));
    }, { once: true });
    pendingResume = position;
    media.src = url;
    if (resume) {
      audioLoading.value = true;
      isPlaying.value = false;
      playbackState.value = "loading";
      watchMediaTimeout(token);
      void media.play().catch((error) => handlePlayFailure(error, token, media));
    }
  }

  function tryOrdinaryPlayback() {
    if (ordinaryPlayback || audio.crossOrigin !== "anonymous" || !audio.error || ![2, 4].includes(audio.error.code)) return false;
    restoreOrdinaryPlayback("频谱音源加载失败，已切换普通播放（无实时频谱）");
    return true;
  }

  function resetMedia() {
    clearMediaTimeout();
    pendingResume = null;
    needsObservation = true;
    audio.pause();
    audio.removeAttribute("src");
    audio.load();
    elapsed.value = 0;
    duration.value = 0;
    isPlaying.value = false;
    audioLoading.value = false;
    playbackError.value = "";
    playbackState.value = "idle";
  }

  function togglePlayback() { togglePlaybackWithReason("user_replay"); }

  function togglePlaybackWithReason(reason: ListeningContext["start_reason"]) {
    if (isPreviewing.value) {
      skipAnnouncement();
      return;
    }
    if (audioLoading.value && playUrlRequest) {
      // 取地址/重连时元素可能尚未播放，点击仍然应能暂停，而不是再启动一轮请求。
      ++playToken;
      playUrlRequest.abort();
      playUrlRequest = null;
      clearMediaTimeout();
      audio.pause();
      audioLoading.value = false;
      isPlaying.value = false;
      playbackState.value = "paused";
      return;
    }
    if (!startReported && audio.paused && currentSong.value?.round_opening_priority) {
      // 首次音乐尚未playing就暂停，会结束旧play Promise；恢复必须重新经过开场入口。
      void playSong(currentSong.value, playbackQueue.value, queueSource.value, "user");
      return;
    }
    if ((!audio.src || playbackState.value === "error" || playbackState.value === "unavailable") && currentSong.value) {
      if (!audio.src && !startReported && playbackState.value === "idle") {
        // prepareQueue只选歌；第一次按底部播放也必须走节目入口，不能直接绕到loadSong。
        void playSong(currentSong.value, playbackQueue.value, queueSource.value, "user");
        return;
      }
      if (!startReported) { listening.begin("user"); nextStartEvent = "play"; }
      recoveryAttempts = 0;
      cancelBackfill();
      invalidateSmartQueue();
      void audioAnalysis.prepare();
      void loadSong(currentSong.value, ++playToken, pendingResume ?? elapsed.value);
      return;
    }
    if (!audio.src) return;
    if (audio.paused) {
      cancelBackfill();
      invalidateSmartQueue();
      void audioAnalysis.prepare();
      if (pendingResume !== null && playbackState.value !== "blocked" && currentSong.value) {
        recoveryAttempts = 0;
        void loadSong(currentSong.value, ++playToken, pendingResume);
        return;
      }
      if (audio.ended || (Number.isFinite(audio.duration) && audio.currentTime >= audio.duration - 0.05)) {
        audio.currentTime = 0;
        needsObservation = true;
        startReported = false;
        finishedCurrentSong = false;
        listening.begin(reason);
        interruptionReported = false;
        nextStartEvent = reason === "repeat_one" ? "play" : "replay";
      }
      const token = isAnnouncing.value ? playToken : ++playToken;
      const media = audio;
      const position = pendingResume;
      if (position !== null && position > 0) {
        const restorePosition = () => {
          if (token !== playToken || media !== audio || !Number.isFinite(media.duration)) return;
          media.currentTime = Math.min(position, Math.max(0, media.duration - 0.05));
        };
        if (media.readyState >= 1) restorePosition();
        else media.addEventListener("loadedmetadata", restorePosition, { once: true });
      }
      audioLoading.value = true;
      playbackState.value = "loading";
      playbackError.value = "";
      watchMediaTimeout(token);
      void media.play().catch((error) => handlePlayFailure(error, token, media));
    } else audio.pause();
  }

  async function previewAnnouncement() {
    if (isAnnouncing.value) { skipAnnouncement(); return; }
    if (audioLoading.value) return;
    if (voiceVolume.value <= 0) {
      notify("请先调高 DJ 人声音量，再试听");
      return;
    }
    const resume = Boolean(audio.src && !audio.paused && !audio.ended);
    const previousState = playbackState.value;
    const token = ++playToken;
    invalidateSmartQueue();
    cancelBackfill();
    clearMediaTimeout();
    audio.pause();
    await previewVoice(token);
    if (token !== playToken) return;
    if (resume) {
      const media = audio;
      audioLoading.value = true;
      playbackState.value = "loading";
      watchMediaTimeout(token);
      void audioAnalysis.prepare();
      void media.play().catch((error) => handlePlayFailure(error, token, media));
    } else {
      playbackState.value = previousState;
      scheduleBackfill();
    }
  }

  function playEntry(song: Song, queue: Song[], source: QueueSource = queueSource.value) {
    if (song.id === currentSong.value?.id && audio.src) {
      prepareQueue(queue, source);
      togglePlayback();
    }
    else void playSong(song, queue, source);
  }

  function moveQueue(step: -1 | 1) {
    const nextSong = playbackQueue.value[queueIndex.value + step];
    if (!nextSong) {
      notify(step > 0 ? "已经是这轮歌单的最后一首" : "已经是这轮歌单的第一首");
      return;
    }
    void playSong(nextSong, playbackQueue.value, queueSource.value);
  }

  function applyAdjustment(batchId: string, songs: Song[], replacedId?: string) {
    if (queueSource.value !== "recommendation" || !playbackQueue.value.length
      || playbackQueue.value.some((song) => song.batch_id !== batchId) || !songs.length) return false;
    invalidateSmartQueue();
    cancelBackfill();
    const oldQueue = playbackQueue.value;
    const oldId = currentSong.value?.id;
    let next = songs;
    if (replacedId) {
      const ids = new Set(oldQueue.map((song) => song.id));
      const replacement = songs.find((song) => !ids.has(song.id));
      if (!replacement) return false;
      next = oldQueue.map((song) => song.id === replacedId ? replacement : songs.find((s) => s.id === song.id) || song);
      // 单首修订不额外打断本轮；已经播过的整轮开场继承到修订批次。
      if (hasAnnouncement(batchId) && replacement.batch_id) consumeAnnouncement(replacement.batch_id);
      playbackQueue.value = next;
      queueIndex.value = next.findIndex((song) => song.id === oldId);
      if (oldId === replacedId) void playSong(replacement, playbackQueue.value, "recommendation");
    } else {
      // 全轮调整仅更新待播队列，不自动抢走仍在播放的歌曲。
      if (!audio.src) {
        clearPlayer();
        prepareQueue(next, "recommendation");
      } else assignQueue(next);
    }
    return true;
  }

  async function loadSong(song: Song, token: number, position = 0, retryDelayMs = 0): Promise<void> {
    if (token !== playToken) return;
    audioLoading.value = true;
    playbackState.value = "loading";
    playbackError.value = "";
    pendingResume = Math.max(0, position);
    const request = new AbortController();
    playUrlRequest = request;
    let requestTimer = 0;
    let media = audio;
    let addressReady = false;
    const restorePosition = () => {
      if (token !== playToken || media !== audio || !Number.isFinite(media.duration)) return;
      media.currentTime = Math.min(Math.max(0, position), Math.max(0, media.duration - 0.05));
    };
    try {
      if (retryDelayMs > 0) {
        // 等待期间沿用取址请求的取消入口，暂停或切歌时清掉定时器。
        await new Promise<void>((resolve) => {
          const timer = window.setTimeout(finishWaiting, retryDelayMs);
          function finishWaiting() {
            window.clearTimeout(timer);
            request.signal.removeEventListener("abort", finishWaiting);
            resolve();
          }
          request.signal.addEventListener("abort", finishWaiting, { once: true });
        });
        if (request.signal.aborted || token !== playToken) return;
      }
      requestTimer = window.setTimeout(() => request.abort(), 25_000);
      const data = await getPlayUrl(song.id, request.signal);
      if (token !== playToken) return;
      window.clearTimeout(requestTimer);
      if (!data.success || !data.url) {
        const message = data.message || "取播放地址失败，点击播放重试或切换下一首";
        failPlayback(message, "unavailable");
        lyricLines.value = [];
        lyricsInstrumental.value = false;
        notify(message);
        if (data.need_login) onLoginRequired(song.platform ?? "netease");
        return;
      }
      isPreview.value = Boolean(data.preview);
      audio.src = data.url;
      media = audio;
      addressReady = true;
      ducking.apply();
      if (position > 0) media.addEventListener("loadedmetadata", restorePosition, { once: true });
      watchMediaTimeout(token);
      await audio.play();
    } catch (error) {
      // src 更换会拒绝旧的 play() Promise；旧请求不允许改写新歌曲的任何状态。
      if (token !== playToken || (addressReady && media !== audio)) return;
      if (addressReady) handlePlayFailure(error, token, media);
      else {
        // 服务端已经收敛为可展示的中文错误；保留请求编号和登录原因，避免全盖成信号失败。
        const apiError = error instanceof ApiError ? error : null;
        const loginRequired = apiError && /网易云.*登录|登录状态已失效|重新扫码/.test(apiError.message);
        const retryable = !apiError || (!loginRequired && apiError.status >= 500);
        if (retryable && recoveryAttempts < ADDRESS_RETRY_DELAYS_MS.length) {
          markInterrupted();
          const normalDelay = ADDRESS_RETRY_DELAYS_MS[recoveryAttempts];
          recoveryAttempts += 1;
          // 先保留快速恢复机会，最后一次才等待服务端建议的较长时间。
          const lastRetry = recoveryAttempts === ADDRESS_RETRY_DELAYS_MS.length;
          const delay = lastRetry ? Math.max(normalDelay, apiError?.retryAfterMs ?? 0) : normalDelay;
          notify(delay > normalDelay
            ? `暂未取到播放地址，约 ${delay / 1000} 秒后会自动再试一次…`
            : `取播放地址失败，稍后自动重试（${recoveryAttempts}/${ADDRESS_RETRY_DELAYS_MS.length}）…`);
          // 同一次取址保留播放令牌，成功后原进度和尚未开始的 DJ 节目仍由原流程接管。
          return loadSong(song, token, position, delay);
        }
        let message = apiError?.message || "点击播放重试或切换下一首";
        if (!loginRequired && apiError?.status !== 401) {
          const stage = request.signal.aborted ? "取播放地址超时" : "取播放地址失败";
          message = `${stage}。${message}`;
        }
        failPlayback(message, loginRequired ? "unavailable" : "error");
        notify(playbackError.value);
        if (loginRequired) onLoginRequired(song.platform ?? "netease");
      }
    } finally {
      window.clearTimeout(requestTimer);
      media.removeEventListener("loadedmetadata", restorePosition);
      if (playUrlRequest === request) playUrlRequest = null;
    }
  }

  async function playSong(
    song: Song,
    queue: Song[] = playbackQueue.value,
    source: QueueSource = queueSource.value,
    reason: ListeningContext["start_reason"] = "user",
  ) {
    // 用户点击发生时立即唤醒 AudioContext；等网络请求返回后再唤醒，浏览器可能
    // 已经丢失用户手势，从而拒绝启动分析上下文。
    const token = ++playToken;
    recoveryAttempts = 0;
    const previousSongId = currentSong.value?.id;
    const lyricsToken = ++songRevision;
    playUrlRequest?.abort();
    invalidateSmartQueue();
    cancelBackfill();
    if (
      currentSong.value && currentSong.value.id !== song.id && audio.src && startReported && !finishedCurrentSong && !interruptionReported
    ) {
      reportListening("skip");
    }
    const replay = reason === "user" && currentSong.value?.id === song.id && startReported;
    listening.begin(replay ? "user_replay" : reason);
    interruptionReported = false;
    flushAudioFeatures();
    resetMedia();
    void audioAnalysis.prepare();
    currentSong.value = song;
    isPreview.value = false;
    queueSource.value = source;
    assignQueue(queue);
    startReported = false;
    finishedCurrentSong = false;
    nextStartEvent = replay ? "replay" : "play";
    stopAnnouncement();
    // 恢复同一首歌会更换播放尝试 token，但不应丢弃仍在请求中的正确歌词。
    void loadLyrics(song.id, lyricsToken);
    await announceThenPlay(song, token, reason === 'auto_next', previousSongId);
  }

  function seekToRatio(ratio: number) {
    const mediaDuration = Number.isFinite(audio.duration) ? audio.duration : duration.value;
    if (!mediaDuration) return;
    listening.sample(audio);
    audio.currentTime = Math.min(1, Math.max(0, ratio)) * mediaDuration;
    listening.sample(audio, isPlaying.value);
    elapsed.value = audio.currentTime;
  }

  function seekBy(seconds: number) {
    if (!duration.value) return;
    listening.sample(audio);
    audio.currentTime = Math.min(duration.value, Math.max(0, audio.currentTime + seconds));
    listening.sample(audio, isPlaying.value);
    elapsed.value = audio.currentTime;
  }

  function seekToEdge(edge: "start" | "end") {
    if (!duration.value) return;
    listening.sample(audio);
    audio.currentTime = edge === "start" ? 0 : duration.value;
    listening.sample(audio, isPlaying.value);
    elapsed.value = audio.currentTime;
  }

  function clearPlayer() {
    ++playToken;
    ++songRevision;
    playUrlRequest?.abort();
    invalidateSmartQueue();
    cancelBackfill();
    stopAnnouncement();
    flushAudioFeatures();
    resetMedia();
    startReported = false;
    finishedCurrentSong = false;
    nextStartEvent = "play";
    interruptionReported = false;
    currentSong.value = null;
    isPreview.value = false;
    lyricLines.value = [];
    lyricsInstrumental.value = false;
    playbackQueue.value = [];
    queueSource.value = "recommendation";
    queueIndex.value = -1;
  }

  const onPlay = () => {
    if (!audio.src || audio.paused) return;
    cancelBackfill();
  };
  const onPlaying = () => {
    if (!audio.src || audio.paused) return;
    listening.sample(audio, true);
    clearMediaTimeout();
    pendingResume = null;
    audioLoading.value = false;
    playbackError.value = "";
    playbackState.value = "playing";
    isPlaying.value = true;
    resumeAnnouncement();
    audioAnalysis.start();
    if (needsObservation && currentSong.value && currentSong.value.platform !== "qq" && !currentSong.value.online_catalog && !ordinaryPlayback) {
      needsObservation = false;
      foregroundObservation.start(currentSong.value.id);
    }
    if (!startReported && currentSong.value) {
      reportListening(nextStartEvent);
      startReported = true;
      nextStartEvent = "play";
    }
  };
  const onPause = () => {
    if (!audio.paused) return;
    pauseAnnouncement();
    listening.sample(audio);
    isPlaying.value = false;
    // 重连清空旧音源时，浏览器可能迟到派发 pause；此时新一轮取址仍应保持加载状态。
    const fetchingAddress = audioLoading.value && playUrlRequest && !audio.src;
    if (!fetchingAddress && !audio.ended && (playbackState.value === "playing" || playbackState.value === "loading")) {
      clearMediaTimeout();
      audioLoading.value = false;
      playbackState.value = "paused";
    }
    audioAnalysis.stop();
    scheduleBackfill();
  };
  const onWaiting = () => {
    if (!audio.src || audio.paused) return;
    pauseAnnouncement();
    listening.sample(audio);
    audioLoading.value = true;
    isPlaying.value = false;
    playbackState.value = "loading";
    audioAnalysis.stop();
    if (!mediaTimer) watchMediaTimeout(playToken);
  };
  const onError = () => {
    if (!audio.src || !audio.error) return;
    markInterrupted();
    // CORS 模式可能让本可普通播放的合法音源加载失败。只尝试一次非分析通道；
    // 真实坏地址/权限不足仍由原生媒体错误报告，不代理或绕过平台授权。
    if (tryOrdinaryPlayback()) return;
    if ([2, 4].includes(audio.error.code) && recoverPlayback()) return;
    failPlayback("歌曲音频流暂时无法播放，点击播放重试或切换下一首");
  };
  const onTimeUpdate = () => {
    listening.sample(audio, isPlaying.value && !audio.paused && !audioLoading.value);
    if (pendingResume !== null && audio.readyState < 1) return;
    const mediaDuration = Number.isFinite(audio.duration) ? audio.duration : duration.value;
    elapsed.value = mediaDuration > 0 ? Math.min(audio.currentTime, mediaDuration) : Math.max(0, audio.currentTime);
    if (audioLoading.value && !audio.paused && !audio.seeking && audio.readyState >= 3 && !audio.error) onPlaying();
  };
  const syncDuration = () => {
    if (pendingResume !== null && audio.readyState < 1) return;
    duration.value = Number.isFinite(audio.duration) && audio.duration > 0 ? audio.duration : 0;
    elapsed.value = duration.value > 0 ? Math.min(audio.currentTime, duration.value) : 0;
  };
  const onEnded = () => {
    // ended 可被重复派发；未真正开始、旧媒体事件、同次播放已结束都不能再记账或续播。
    if (!audio.src || !audio.ended || !startReported || finishedCurrentSong) return;
    finishedCurrentSong = true;
    stopAnnouncement();
    clearMediaTimeout();
    isPlaying.value = false;
    audioLoading.value = false;
    playbackState.value = "ended";
    audioAnalysis.stop();
    flushAudioFeatures();
    if (Number.isFinite(audio.duration) && audio.duration > 0) {
      duration.value = audio.duration;
      elapsed.value = audio.duration;
    }
    if (currentSong.value) {
      reportListening("finish");
    }
    if (!autoPlayOn.value) {
      scheduleBackfill();
      return;
    }
    if (playbackMode.value === "repeat-one") {
      // 自动循环保留播放事实，但不能冒充用户主动重播。
      togglePlaybackWithReason("repeat_one");
      return;
    }
    const next = playbackQueue.value[queueIndex.value + 1];
    if (next) void playSong(next, playbackQueue.value, queueSource.value, "auto_next");
    else if (smartContinueOn.value && queueSource.value === "recommendation" && currentSong.value && !currentSong.value.online_catalog && onQueueExhausted) {
      const finishedSong = currentSong.value;
      const token = playToken;
      const revision = queueRevision;
      const request = new AbortController();
      smartQueueRequest = request;
      const isCurrent = () => token === playToken && revision === queueRevision && autoPlayOn.value && smartContinueOn.value
        && queueSource.value === "recommendation" && !request.signal.aborted;
      smartQueueLoading.value = true;
      smartQueueTimer = window.setTimeout(() => {
        if (!isCurrent()) return;
        invalidateSmartQueue();
        notify("智能续播请求超时，可以手动选择下一首");
        scheduleBackfill();
      }, 25_000);
      notify("DJ 正在按这首歌的声音接下一段…");
      void onQueueExhausted(finishedSong, request.signal).then((extra) => {
        if (!isCurrent()) return;
        if (!extra.length) {
          notify("这轮歌单播完了，暂时没有合适的声学衔接");
          scheduleBackfill();
          return;
        }
        const known = new Set(playbackQueue.value.map((song) => song.id));
        const additions = extra.filter((song) => !known.has(song.id));
        if (!additions.length) return;
        // 先排定新增批次，再让 DJ 读取这批的实际顺序；之前已经播放的部分保持原位。
        const ordered = playbackMode.value === "shuffle" ? shuffleUpcoming([finishedSong, ...additions]).slice(1) : additions;
        const extended = [...playbackQueue.value, ...ordered];
        playbackQueue.value = extended;
        void playSong(ordered[0], playbackQueue.value, "recommendation", "auto_next");
      }).catch(() => {
        if (isCurrent()) notify("智能续播暂时失败，可以手动选择下一首");
      }).finally(() => {
        if (smartQueueRequest === request) {
          window.clearTimeout(smartQueueTimer);
          smartQueueRequest = null;
          smartQueueLoading.value = false;
          scheduleBackfill();
        }
      });
    } else if (queueSource.value === "manual") {
      notify("手动队列已经播完");
      scheduleBackfill();
    } else notify("这轮歌单播完了，想再来几首就和 DJ 说一声");
  };

  function bindMedia(media: HTMLAudioElement, attach: boolean) {
    const handlers = { play: onPlay, pause: onPause, waiting: onWaiting, playing: onPlaying,
      error: onError, timeupdate: onTimeUpdate, loadedmetadata: syncDuration, durationchange: syncDuration, ended: onEnded };
    for (const [name, handler] of Object.entries(handlers)) {
      if (attach) media.addEventListener(name, handler);
      else media.removeEventListener(name, handler);
    }
  }
  function onVisibility() {
    if (document.hidden) cancelBackfill();
    else scheduleBackfill();
  }
  onMounted(() => { bindMedia(audio, true); document.addEventListener("visibilitychange", onVisibility); });

  watch(announceOn, (value) => {
    localStorage.setItem("personal-radio-announce-on", value ? "on" : "off");
    if (!value && isAnnouncing.value) stopAnnouncement("DJ 报幕已关闭，本次播报已停止。");
    if (value) notify("DJ 报幕已开启：每批最多三段，在合适的曲间自然开口；可随时跳过");
  });
  watch(djVoice, (value) => { localStorage.setItem(DJ_VOICE_KEY, value); });
  watch(autoPlayOn, (value) => {
    localStorage.setItem("personal-radio-autoplay-on", value ? "on" : "off");
    if (!value) invalidateSmartQueue();
  });
  watch(smartContinueOn, (value) => {
    localStorage.setItem("personal-radio-smart-continue", value ? "on" : "off");
    if (!value) invalidateSmartQueue();
  });
  watch(playbackMode, (value) => {
    localStorage.setItem("personal-radio-playback-mode", value);
    invalidateSmartQueue();
    if (value === "shuffle") {
      if (queueSource.value === "recommendation" && playbackQueue.value.some(song => song.round_queue_order_fixed)) {
        notify("这批歌已按当前顺序安排 DJ，下一批开始随机播放");
        return;
      }
      playbackQueue.value = shuffleUpcoming(playbackQueue.value);
      queueIndex.value = playbackQueue.value.findIndex((song) => song.id === currentSong.value?.id);
    }
  }, { flush: "sync" });
  watch(acousticBackfillOn, (value) => {
    localStorage.setItem("personal-radio-acoustic-backfill", value ? "on" : "off");
    if (value) scheduleBackfill();
    else cancelBackfill();
  });

  onBeforeUnmount(() => {
    disposed = true;
    ++playToken;
    ++songRevision;
    playUrlRequest?.abort();
    invalidateSmartQueue();
    clearMediaTimeout();
    ducking.dispose();
    bindMedia(audio, false);
    document.removeEventListener("visibilitychange", onVisibility);
    stopAnnouncement();
    cancelBackfill();
    foregroundObservation.dispose();
    audio.pause();
    audio.removeAttribute("src");
  });

  return {
    currentSong,
    playbackQueue,
    queueSource,
    isPlaying,
    audioLoading,
    playbackError,
    playbackState,
    lyricLines,
    lyricsInstrumental,
    elapsed,
    duration,
    volume,
    voiceVolume,
    djVoice,
    announceOn,
    autoPlayOn,
    smartContinueOn,
    playbackMode,
    acousticBackfillOn,
    smartQueueLoading,
    isAnnouncing,
    djPlaybackNotices,
    isDjOnAir,
    isPreviewing,
    isPreview,
    visualMode,
    audioLow: audioAnalysis.low,
    audioMid: audioAnalysis.mid,
    audioHigh: audioAnalysis.high,
    audioEnergy: audioAnalysis.energy,
    audioBeat: audioAnalysis.beat,
    audioSpectrum: audioAnalysis.spectrum,
    audioAnalysisStatus: audioAnalysis.status,
    readAudioWaveform: audioAnalysis.readWaveform,
    audioAnalysisMessage: audioAnalysis.message,
    title,
    subtitle,
    progress,
    progressValue,
    displayElapsed,
    playerStatus,
    canGoPrevious,
    canGoNext,
    prepareQueue,
    applyAdjustment,
    appendToQueue,
    appendPlaylist,
    playNext,
    removeFromQueue,
    reorderQueue,
    togglePlayback,
    playEntry,
    moveQueue,
    playSong,
    skipAnnouncement,
    previewAnnouncement,
    seekToRatio,
    seekBy,
    seekToEdge,
    clearPlayer,
  };
}
