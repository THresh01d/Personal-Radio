import { computed, ref, watch, type Ref } from "vue";
import type { DJSegment, QueueSource, Song } from "../../types/radio";
import type { PlaybackState } from "./types";
import type { DjVoice } from "./djVoice";
import { createVoicePlayback } from "./voicePlayback";

type AnnouncementOptions = {
  voiceAudio: HTMLAudioElement;
  voiceVolume: Ref<number>;
  djVoice: Ref<DjVoice>;
  announceOn: Ref<boolean>;
  queueSource: Ref<QueueSource>;
  playbackState: Ref<PlaybackState>;
  isCurrent: (token: number) => boolean;
  loadSong: (song: Song, token: number) => Promise<void>;
  canSpeakOverMusic: () => boolean;
  getCurrentSong: () => Song | null;
  notify: (message: string) => void;
};

export function createAnnouncement({ voiceAudio, voiceVolume, djVoice, announceOn, queueSource, playbackState, isCurrent, loadSong, canSpeakOverMusic, getCurrentSong, notify }: AnnouncementOptions) {
  const speakingAttempt = ref(false);
  const openingPending = ref(false);
  const isAnnouncing = computed(() => speakingAttempt.value || openingPending.value);
  const djPlaybackNotices = ref<Record<string, string>>({});
  let cancelOpeningWait: (() => void) | null = null;
  let waitingOpeningKey: string | null = null;
  let activeBatch: string | null = null;
  function notice(batch: string | undefined | null, text: string) {
    if (!batch) return;
    djPlaybackNotices.value = Object.fromEntries([...Object.entries(djPlaybackNotices.value).filter(([id]) => id !== batch), [batch, text]].slice(-50));
  }
  const isVoiceSpeaking = ref(false);
  const isVoicePaused = ref(false);
  const isPreviewing = ref(false);
  const voice = createVoicePlayback(voiceAudio, notify);
  let activeToken: number | null = null;
  let activeSegment: string | null = null;
  const segmentKey = "personal-radio-announced-segments";
  const consumedSegments = new Set<string>();
  try {
    const saved = JSON.parse(localStorage.getItem(segmentKey) || "[]");
    if (Array.isArray(saved)) saved.filter((s): s is string => typeof s === "string").slice(-600).forEach((s) => consumedSegments.add(s));
  } catch { /* 旧存储损坏只影响去重，不阻断音乐。 */ }
  function consumeSegment(key: string) {
    consumedSegments.add(key);
    const saved = [...consumedSegments].slice(-600);
    consumedSegments.clear();
    saved.forEach((s) => consumedSegments.add(s));
    try { localStorage.setItem(segmentKey, JSON.stringify(saved)); } catch { /* 本页仍保持去重。 */ }
  }
  const announcedKey = "personal-radio-announced-batches";
  let savedAnnouncements: string[] = [];
  try {
    const parsed = JSON.parse(localStorage.getItem(announcedKey) || "[]");
    if (Array.isArray(parsed)) savedAnnouncements = parsed.filter((id): id is string => typeof id === "string").slice(-200);
  } catch { /* 旧缓存损坏只影响去重，不影响音乐播放。 */ }
  const announcedBatches = new Set(savedAnnouncements);
  function consumeAnnouncement(batchId: string) {
    announcedBatches.add(batchId);
    const ids = [...announcedBatches].slice(-200);
    announcedBatches.clear();
    ids.forEach((id) => announcedBatches.add(id));
    try { localStorage.setItem(announcedKey, JSON.stringify(ids)); }
    catch { /* 浏览器不允许持久化时，本次会话仍然按轮次去重。 */ }
  }

  async function speak(text: string, token: number, batchId?: string, segment?: string, preview = false) {
    if (!isCurrent(token)) return;
    if (voiceVolume.value <= 0) {
      notify("DJ 人声音量为零，请在播放设置中调高");
      return;
    }
    activeToken = token;
    activeSegment = segment || null;
    speakingAttempt.value = true;
    isPreviewing.value = preview;
    isVoicePaused.value = false;
    if (preview) playbackState.value = "announcing";
    voiceAudio.volume = voiceVolume.value;
    if (!preview) notice(activeBatch, "音乐继续，正在准备 DJ 声音…");
    let failed = false;
    try {
      await voice.speak(text, () => {
        // 真正开始发声后才记为已播；网络失败不能永久消耗这一轮的报幕。
        if (isCurrent(token)) { isVoiceSpeaking.value = true; if (!preview) notice(activeBatch, "DJ 正在伴着音乐播报，可暂停或跳过。"); }
        if (isCurrent(token) && segment) consumeSegment(segment);
        else if (isCurrent(token) && batchId) consumeAnnouncement(batchId);
      }, djVoice.value);
    } catch (error) {
      failed = true;
      if (isCurrent(token) && !preview) notice(activeBatch, error instanceof Error ? error.message : "DJ 语音未能播放，音乐继续。");
      if (isCurrent(token)) notify(error instanceof Error ? error.message : "DJ 语音播放失败，先继续音乐");
    } finally {
      if (activeToken === token) {
        if (!failed && !preview) notice(activeBatch, "本段 DJ 播报已结束，音乐继续。");
        activeBatch = null;
        activeToken = null;
        activeSegment = null;
        speakingAttempt.value = false;
        isVoiceSpeaking.value = false;
        isVoicePaused.value = false;
        isPreviewing.value = false;
      }
    }
  }

  function waitForOpening(song: Song, token: number): Promise<DJSegment | null> {
    return new Promise((resolve) => {
      let done = false, timer = 0, remaining = 45_000, started = 0;
      let unwatch = () => {};
      const finish = (segment: DJSegment | null, message?: string) => {
        if (done) return;
        done = true; window.clearTimeout(timer); unwatch();
        cancelOpeningWait = null; waitingOpeningKey = null; openingPending.value = false;
        if (message && isCurrent(token)) notice(song.batch_id, message);
        resolve(segment);
      };
      openingPending.value = true;
      waitingOpeningKey = song.batch_id + ':opening';
      cancelOpeningWait = () => finish(null);
      const check = () => {
        if (!isCurrent(token)) { finish(null); return; }
        if (!announceOn.value || queueSource.value === 'manual') { finish(null, 'DJ 报幕已关闭或当前为自己选歌，不安排播报。'); return; }
        const current = getCurrentSong();
        if (!current || current.batch_id !== song.batch_id) { finish(null); return; }
        const opening = current.round_segments?.find(s => s.position === 0);
        const pending = ['pending', 'writing'].includes(current.round_copy_status || '');
        if (opening && !pending && !current.round_opening_independent && opening.before_song_id !== current.id) {
          finish(null, '本轮开场针对第一首，当前点选了其他作品，未挪用单曲介绍。'); return;
        }
        if (!opening && !pending) {
          const status = current.round_copy_status;
          finish(null, status === 'quiet' ? '本轮没有生成需要展开的节目，音乐继续。' : status === 'disabled' ? '按介绍偏好，本轮不安排 DJ 播报。' : status === 'failed' || status === 'unavailable' ? '本轮开场未能准备完成，请查看节目原因；音乐继续。' : '本轮没有通过校验的开场，保留的介绍安排在后续自然曲间。');
          return;
        }
        const playing = canSpeakOverMusic();
        if (opening && playing && (current.round_opening_independent || opening.before_song_id === current.id)) { finish(opening); return; }
        if (playing && !timer) {
          started = Date.now();
          timer = window.setTimeout(() => {
            consumeSegment(song.batch_id + ':opening');
            finish(null, '开场准备超过45秒，本轮不再自动补开场；后续曲间节目仍可使用。');
          }, remaining);
        } else if (!playing && timer) {
          remaining = Math.max(1, remaining - (Date.now() - started)); window.clearTimeout(timer); timer = 0;
        }
        notice(song.batch_id, playing ? '音乐先播，DJ 开场正在准备中…' : 'DJ 开场等待已暂停，继续音乐后再接入。');
      };
      unwatch = watch(() => {
        const current = getCurrentSong();
        return [current?.round_segments, current?.round_copy_status, current?.round_opening_independent, canSpeakOverMusic(), announceOn.value, queueSource.value];
      }, check, { flush: 'sync' });
      check();
    });
  }

  async function announceThenPlay(song: Song, token: number, naturalBoundary = false, previousSongId?: string) {
    const initialSegments = song.round_segments?.slice(0, 3);
    const intro = (song.batch_id ? song.round_intro || '' : song.dj_intro || song.reason || '').trim();
    await loadSong(song, token);
    if (!isCurrent(token)) return;
    activeBatch = song.batch_id || null;
    if (queueSource.value === 'manual') { notice(song.batch_id, '当前为自己选歌，DJ 不自动报幕。'); return; }
    if (!announceOn.value) { notice(song.batch_id, 'DJ 报幕已关闭，可在播放设置中开启。'); return; }
    if (voiceVolume.value <= 0) { notice(song.batch_id, 'DJ 人声音量为零，请在播放设置中调高。'); return; }
    if (!canSpeakOverMusic()) { notice(song.batch_id, '歌曲尚未开始播放，DJ 暂未开口。'); return; }
    if (song.round_program_version === 'dj-segments-v1' && song.batch_id) {
      if (song.round_opening_priority && !announcedBatches.has(song.batch_id) && !consumedSegments.has(song.batch_id + ':opening')) {
        const opening = await waitForOpening(song, token);
        if (!isCurrent(token)) return;
        if (opening && announceOn.value && canSpeakOverMusic()) {
          // 新开场针对整轮且独立成立，可伴随本轮首次点选的推荐曲；不挪用曲间故事。
          await speak(opening.text, token, undefined, song.batch_id + ':opening');
          return;
        }
      }
      const segments = song.round_opening_priority ? getCurrentSong()?.round_segments : initialSegments;
      const segment = segments?.slice(0, 3).find(item => item.before_song_id === song.id);
      const key = segment ? song.batch_id + ':' + (song.round_opening_priority && segment.position === 0 ? 'opening' : segment.id) : '';
      if (segment && !announcedBatches.has(song.batch_id) && !consumedSegments.has(key)) {
        if (segment.position > 0 && !segment.standalone
            && (!naturalBoundary || (segment.after_song_id && segment.after_song_id !== previousSongId))) {
          // 条件没满足不算已播。如果之后从正确的前曲自然接上，这段仍然可用。
          notice(song.batch_id, '这段介绍承接上一首，等对应的自然切歌时再播。');
        } else if (isCurrent(token) && canSpeakOverMusic() && announceOn.value) {
          await speak(segment.text, token, undefined, key);
        }
      } else if (!song.round_opening_priority) {
        notice(song.batch_id, segment ? '这段介绍已经播放或略过，不重复播报。' : '本轮没有安排当前歌曲的介绍，曲间节目仅在对应的自然切歌节点播出。');
      }
      return;
    }
    if (intro && !(song.batch_id && announcedBatches.has(song.batch_id))) {
      await speak(song.batch_id ? intro : `接下来是《${song.title}》，${song.artist}。${intro}`, token, song.batch_id);
    }
  }

  function stopAnnouncement(message = "本次 DJ 播报已停止，音乐由当前播放操作控制。") {
    cancelOpeningWait?.();
    if (activeBatch) notice(activeBatch, message);
    activeBatch = null;
    if (activeSegment && (isVoiceSpeaking.value || !activeSegment.endsWith(':opening'))) consumeSegment(activeSegment);
    activeSegment = null;
    activeToken = null;
    voice.stop();
    speakingAttempt.value = false;
    isVoiceSpeaking.value = false;
    isVoicePaused.value = false;
    isPreviewing.value = false;
  }

  function skipAnnouncement() {
    if (!isAnnouncing.value) return;
    notify("已跳过 DJ 报幕");
    if (waitingOpeningKey) consumeSegment(waitingOpeningKey);
    if (activeSegment) consumeSegment(activeSegment);
    stopAnnouncement("已跳过本次 DJ 播报，音乐继续。");
  }
  function pauseAnnouncement() {
    if (!isAnnouncing.value || isPreviewing.value) return;
    isVoicePaused.value = true;
    voice.pause();
  }
  function resumeAnnouncement() {
    if (!isAnnouncing.value || !isVoicePaused.value) return;
    isVoicePaused.value = false;
    voice.resume();
  }
  watch(voiceVolume, (value) => {
    voiceAudio.volume = value;
    if (value <= 0 && isAnnouncing.value) stopAnnouncement();
  });
  return {
    isAnnouncing, djPlaybackNotices, isVoiceSpeaking, isVoicePaused, isPreviewing, pauseAnnouncement, resumeAnnouncement,
    announceThenPlay, stopAnnouncement, skipAnnouncement, consumeAnnouncement,
    hasAnnouncement: (batchId: string) => announcedBatches.has(batchId) || [...consumedSegments].some((key) => key.startsWith(batchId + ':')),
    previewVoice: (token: number) => speak("你好，这里是你的私人电台。这是 DJ 声音试听，祝你听歌愉快。", token, undefined, undefined, true),
  };
}
