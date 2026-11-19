<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from "vue";
import PlayerGlass from "./PlayerGlass.vue";
import VoiceHealthPanel from "./VoiceHealthPanel.vue";
import HotkeySettings from "./HotkeySettings.vue";
import type { Song } from "../../types/radio";
import type { AudioAnalysisStatus, VisualMode } from "../../composables/useAudioAnalysis";
import type { PlaybackMode } from "../../composables/useAudioPlayer";
import type { DJLength, QueueSource } from "../../types/radio";
import type { DJ_VOICES, DjVoice } from "../../composables/player/djVoice";

const props = defineProps<{
  currentSong: Song | null;
  isPlaying: boolean;
  loading: boolean;
  error: string;
  status: string;
  progress: string;
  progressValue: number;
  elapsed: number;
  duration: number;
  volume: number;
  voiceVolume: number;
  djVoice?: DjVoice;
  djVoices?: typeof DJ_VOICES;
  announceOn: boolean;
  djLength?: DJLength;
  autoPlayOn: boolean;
  acousticBackfillOn: boolean;
  announcing: boolean;
  previewing?: boolean;
  playbackPreview?: boolean;
  visualMode: VisualMode;
  analysisStatus: AudioAnalysisStatus;
  analysisMessage?: string;
  motionNote?: string;
  canPrevious: boolean;
  canNext: boolean;
  playbackMode: PlaybackMode;
  smartContinueOn: boolean;
  queueSource: QueueSource;
  chatOpen?: boolean;
  queueOpen?: boolean;
}>();

const emit = defineEmits<{
  toggle: [];
  previous: [];
  next: [];
  seekRatio: [ratio: number];
  seekBy: [seconds: number];
  seekEdge: [edge: "start" | "end"];
  updateVolume: [value: number];
  updateVoiceVolume: [value: number];
  updateDjVoice: [voice: DjVoice];
  toggleAnnounce: [];
  updateDjLength: [length: DJLength];
  previewAnnounce: [];
  skipAnnounce: [];
  toggleAutoplay: [];
  toggleAcousticBackfill: [];
  updateVisualMode: [mode: VisualMode];
  updatePlaybackMode: [mode: PlaybackMode];
  toggleSmartContinue: [];
  queue: [];
  chat: [];
}>();

const settingsOpen = ref(false);
const settingsElement = ref<HTMLElement | null>(null);
const seekPreview = ref<number | null>(null);
let seekPointer: number | null = null;
const modeLabels = { sequence: "顺序播放", shuffle: "随机不重复", "repeat-one": "单曲循环" };

function time(value: number) {
  if (!Number.isFinite(value)) return "--:--";
  return `${String(Math.floor(value / 60)).padStart(2, "0")}:${String(Math.floor(value % 60)).padStart(2, "0")}`;
}

function timelineRatio(event: MouseEvent) {
  const rect = (event.currentTarget as HTMLElement).getBoundingClientRect();
  return rect.width > 0 ? Math.min(1, Math.max(0, (event.clientX - rect.left) / rect.width)) : null;
}

function clickTimeline(event: MouseEvent) {
  // 指针点击已在 pointerup 提交；保留不经过指针事件的点击入口。
  if ("pointerType" in event && event.pointerType) return;
  const ratio = timelineRatio(event);
  if (ratio !== null) emit("seekRatio", ratio);
}

function beginSeek(event: PointerEvent) {
  if (!event.isPrimary || event.button !== 0 || seekPointer !== null || !props.duration) return;
  (event.currentTarget as HTMLElement).setPointerCapture(event.pointerId);
  seekPointer = event.pointerId;
  seekPreview.value = timelineRatio(event);
}

function previewSeek(event: PointerEvent) {
  if (event.pointerId === seekPointer) seekPreview.value = timelineRatio(event);
}

function clearSeek() {
  seekPointer = null;
  seekPreview.value = null;
}

function finishSeek(event: PointerEvent) {
  if (event.pointerId !== seekPointer) return;
  const ratio = timelineRatio(event);
  clearSeek();
  // 拖动期间只预览位置，松开后一次定位，避免反复跳转媒体。
  if (ratio !== null) emit("seekRatio", ratio);
}

function cancelSeek(event: PointerEvent) {
  if (event.pointerId === seekPointer) clearSeek();
}

watch([() => props.currentSong?.id, () => props.currentSong?.platform], clearSeek);

function onTimelineKeydown(event: KeyboardEvent) {
  if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
  event.preventDefault();
  if (event.key === "Home") emit("seekEdge", "start");
  else if (event.key === "End") emit("seekEdge", "end");
  else emit("seekBy", event.key === "ArrowRight" ? 5 : -5);
}

function inputValue(event: Event) {
  return Number((event.target as HTMLInputElement).value);
}

function onDocumentPointer(event: PointerEvent) {
  if (!settingsElement.value?.contains(event.target as Node)) settingsOpen.value = false;
}

function closeSettings() {
  settingsOpen.value = false;
  // Esc 移除浮层中的焦点节点后，继续从原按钮操作，不能落回整个页面起点。
  settingsElement.value?.querySelector<HTMLButtonElement>(".settings-button")?.focus({ preventScroll: true });
}

function openSettings() {
  settingsOpen.value = true;
  settingsElement.value?.querySelector<HTMLButtonElement>(".settings-button")?.focus({ preventScroll: true });
}

defineExpose({ openSettings });

onMounted(() => document.addEventListener("pointerdown", onDocumentPointer));
onBeforeUnmount(() => document.removeEventListener("pointerdown", onDocumentPointer));
</script>

<template>
  <footer class="player-dock glass-player-dock" :class="{ loading, error }">
    <PlayerGlass :refract="false" />
    <!-- 失败原因不能放在会被窄屏隐藏的歌手副标题中，也不能只依赖短暂 toast。 -->
    <p v-if="error" id="playback-error" class="playback-notice" role="status" aria-live="polite">{{ error }}</p>
    <div class="playback-modes" role="group" aria-label="播放模式">
      <button class="mode-button" type="button" aria-label="随机播放" :title="playbackMode === 'shuffle' ? '关闭随机，恢复顺序播放' : '随机不重复'" :aria-pressed="playbackMode === 'shuffle'" @click="$emit('updatePlaybackMode', playbackMode === 'shuffle' ? 'sequence' : 'shuffle')">
        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 6h3c5 0 7 12 12 12h3m-4-4 4 4-4 4M3 18h3c2 0 4-3 6-6s4-6 6-6h3m-4-4 4 4-4 4" /></svg>
      </button>
      <button class="mode-button" type="button" aria-label="单曲循环" :title="playbackMode === 'repeat-one' ? '关闭单曲循环，恢复顺序播放' : '单曲循环'" :aria-pressed="playbackMode === 'repeat-one'" @click="$emit('updatePlaybackMode', playbackMode === 'repeat-one' ? 'sequence' : 'repeat-one')">
        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m17 2 4 4-4 4M3 11V9a3 3 0 0 1 3-3h15M7 22l-4-4 4-4m14-1v2a3 3 0 0 1-3 3H3M11 10l1-1v6" /></svg>
      </button>
    </div>
    <div class="transport">
      <button class="skip-button" type="button" aria-label="上一首" :disabled="!canPrevious" @click="$emit('previous')"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 6v12M18 7l-8 5 8 5Z" /></svg></button>
      <button class="main-play" type="button" :disabled="!currentSong" :aria-describedby="error ? 'playback-error' : undefined" :aria-label="previewing ? '跳过 DJ 试听' : error ? '重试播放' : isPlaying ? '暂停' : '播放'" @click="$emit('toggle')">
        <svg v-if="previewing" viewBox="0 0 24 24" aria-hidden="true"><path d="m7 7 7 5-7 5ZM17 7v10" /></svg>
        <i v-else-if="loading" class="buffer-spinner" aria-hidden="true"></i>
        <svg v-else-if="isPlaying" viewBox="0 0 24 24" aria-hidden="true"><path d="M9 7v10M15 7v10" /></svg>
        <svg v-else viewBox="0 0 24 24" aria-hidden="true"><path d="m9 7 8 5-8 5Z" /></svg>
      </button>
      <button class="skip-button" type="button" aria-label="下一首" :disabled="!canNext" @click="$emit('next')"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="m6 7 8 5-8 5ZM18 6v12" /></svg></button>
    </div>

    <div class="dock-track">
      <div class="dock-meta">
        <div class="dock-title"><strong>{{ currentSong?.title || "未选歌" }}</strong><small v-if="currentSong">{{ currentSong.artist }}{{ playbackPreview ? " · 试听" : "" }}</small><button v-if="announcing" class="skip-voice" type="button" aria-label="跳过 DJ 播报" @click="$emit('skipAnnounce')">跳过 DJ</button></div>
        <span class="clock">{{ duration > 0 ? time(elapsed) : "--:--" }} <i>/</i> {{ duration > 0 ? time(duration) : "--:--" }}</span>
      </div>
      <div class="timeline" :class="{ 'is-seeking': seekPreview !== null }" role="slider" tabindex="0" aria-label="歌曲播放进度" aria-valuemin="0" aria-valuemax="100" :aria-valuenow="Math.round(seekPreview === null ? progressValue : seekPreview * 100)"
        @click="clickTimeline" @keydown="onTimelineKeydown" @pointerdown="beginSeek" @pointermove="previewSeek" @pointerup="finishSeek" @pointercancel="cancelSeek" @lostpointercapture="cancelSeek">
        <i :style="{ width: seekPreview === null ? progress : `${seekPreview * 100}%` }"></i>
      </div>
    </div>

    <div class="dock-panels" role="group" aria-label="电台面板">
      <button class="queue-access" type="button" aria-label="打开 DJ 对话" :aria-expanded="Boolean(chatOpen)" :class="{ selected: chatOpen }" @click="$emit('chat')"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 5h14v10H9l-4 4V5Zm4 4h6m-6 3h4" /></svg><span>DJ</span></button>
      <button class="queue-access" type="button" aria-label="打开当前队列" :aria-expanded="Boolean(queueOpen)" :class="{ selected: queueOpen }" @click="$emit('queue')"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 6h16M4 12h10M4 18h10m4-8v10m-3-3 3 3 3-3" /></svg><span>队列</span></button>
    </div>
    <label class="volume" title="音乐音量"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 10v4h4l5 4V6L9 10H5Zm12-1.5a5 5 0 0 1 0 7" /></svg><input aria-label="音乐音量" :value="volume" type="range" min="0" max="1" step="0.01" @input="$emit('updateVolume', inputValue($event))" /></label>

    <div ref="settingsElement" class="player-settings" @keydown.esc.stop.prevent="closeSettings">
      <button class="settings-button" type="button" aria-label="播放设置" :aria-expanded="settingsOpen" @click="settingsOpen = !settingsOpen">
        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 15.5a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7Z" /><path d="M19.4 15a1.7 1.7 0 0 0 .34 1.88l.06.06-2.83 2.83-.06-.06a1.7 1.7 0 0 0-1.88-.34 1.7 1.7 0 0 0-1.03 1.55V21h-4v-.08A1.7 1.7 0 0 0 8.94 19.4a1.7 1.7 0 0 0-1.88.34l-.06.06-2.83-2.83.06-.06A1.7 1.7 0 0 0 4.57 15 1.7 1.7 0 0 0 3 14H3v-4h.08A1.7 1.7 0 0 0 4.6 8.94a1.7 1.7 0 0 0-.34-1.88L4.2 7l2.83-2.83.06.06A1.7 1.7 0 0 0 9 4.57 1.7 1.7 0 0 0 10 3.08V3h4v.08A1.7 1.7 0 0 0 15.06 4.6a1.7 1.7 0 0 0 1.88-.34L17 4.2 19.83 7l-.06.06A1.7 1.7 0 0 0 19.43 9 1.7 1.7 0 0 0 20.92 10H21v4h-.08A1.7 1.7 0 0 0 19.4 15Z" /></svg>
      </button>
      <Transition name="menu-pop">
        <section v-if="settingsOpen" class="settings-popover" aria-label="播放设置">
          <header><strong>播放设置</strong></header>
          <div class="settings-grid">
            <div class="settings-group" role="group" aria-label="播放">
              <div class="mode-setting">
                <strong>播放模式</strong>
                <div class="setting-options" role="group" aria-label="播放模式选择">
                  <button v-for="mode in (['sequence', 'shuffle', 'repeat-one'] as PlaybackMode[])" :key="mode" type="button"
                    :aria-pressed="playbackMode === mode" @click="$emit('updatePlaybackMode', mode)">{{ modeLabels[mode] }}</button>
                </div>
              </div>
              <button class="setting-row" type="button" :aria-pressed="autoPlayOn" @click="$emit('toggleAutoplay')">
                <span>自动连播<small v-if="!autoPlayOn">本曲播完后停止</small></span>
                <i aria-hidden="true" :class="{ on: autoPlayOn }"></i>
              </button>
              <button class="setting-row" type="button" :aria-pressed="smartContinueOn" @click="$emit('toggleSmartContinue')">
                <span>DJ 智能续播<small>{{ queueSource === 'manual' ? '仅 DJ 队列生效' : '接上新推荐' }}</small></span>
                <i aria-hidden="true" :class="{ on: smartContinueOn }"></i>
              </button>
            </div>
            <div class="settings-group" role="group" aria-label="DJ 声音">
              <button class="setting-row" type="button" :aria-pressed="announceOn" @click="$emit('toggleAnnounce')">
                <span>DJ 报幕<small>伴随音乐播报</small></span><i aria-hidden="true" :class="{ on: announceOn }"></i>
              </button>
              <div v-if="djVoices" class="mode-setting">
                <strong>DJ 音色</strong>
                <div class="setting-options" role="group" aria-label="DJ 音色">
                  <button v-for="voice in djVoices" :key="voice.id" type="button"
                    :aria-pressed="djVoice === voice.id" @click="$emit('updateDjVoice', voice.id)">{{ voice.label }}</button>
                </div>
              </div>
              <div class="mode-setting">
                <strong>文案长度</strong>
                <div class="setting-options length-options" role="group" aria-label="DJ 文案长度">
                  <button type="button" :aria-pressed="djLength === 'short'" @click="$emit('updateDjLength', 'short')">简短</button>
                  <button type="button" :aria-pressed="djLength !== 'short'" @click="$emit('updateDjLength', 'normal')">标准</button>
                </div>
              </div>
              <label class="setting-volume">
                <span>DJ 人声音量</span>
                <input :value="voiceVolume" type="range" min="0" max="1" step="0.01" @input="$emit('updateVoiceVolume', inputValue($event))" />
              </label>
              <button class="voice-preview" type="button" :disabled="loading" @click="$emit('previewAnnounce')">
                <span aria-hidden="true">{{ announcing ? '■' : '▷' }}</span>
                {{ announcing ? '跳过 DJ 声音' : '试听 DJ 声音' }}
              </button>
            </div>
            <div class="settings-group settings-stage" role="group" aria-label="舞台与声学">
              <div class="mode-setting">
                <strong>舞台动效</strong>
                <div class="setting-options" role="group" aria-label="舞台动效强度">
                  <button v-for="mode in (['quiet', 'atmosphere', 'immersive'] as VisualMode[])" :key="mode" type="button"
                    :aria-pressed="visualMode === mode" @click="$emit('updateVisualMode', mode)">{{ { quiet: '静谧', atmosphere: '氛围', immersive: '沉浸' }[mode] }}</button>
                </div>
                <small v-if="analysisMessage || analysisStatus === 'active'">{{ analysisMessage || '正在读取音频频谱' }}</small>
                <small v-if="motionNote">{{ motionNote }}</small>
              </div>
              <button class="setting-row" type="button" :aria-pressed="acousticBackfillOn" @click="$emit('toggleAcousticBackfill')">
                <span>空闲声学补齐<small>暂停时分析队列</small></span>
                <i aria-hidden="true" :class="{ on: acousticBackfillOn }"></i>
              </button>
            </div>
          </div>
          <HotkeySettings />
          <VoiceHealthPanel />
        </section>
      </Transition>
    </div>
  </footer>
</template>

<style scoped src="./playerDock.css"></style>
