<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from "vue";
import NcmLoginModal from "./components/auth/NcmLoginModal.vue";
import NcmPlaylistModal from "./components/auth/NcmPlaylistModal.vue";
import QqPlaylistModal from "./components/auth/QqPlaylistModal.vue";
import AISettingsModal from "./components/settings/AISettingsModal.vue";
import FirstRunModal from "./components/settings/FirstRunModal.vue";
import ChatDrawer from "./components/chat/ChatDrawer.vue";
import PlayerDock from "./components/player/PlayerDock.vue";
import { DJ_VOICES } from "./composables/player/djVoice";
import PlaylistDrawer from "./components/playlist/PlaylistDrawer.vue";
import SongReviewModal from "./components/playlist/SongReviewModal.vue";
import LibraryRemovalModal from "./components/playlist/LibraryRemovalModal.vue";
import type { LibraryRemovalPreview, LibraryRemovalTarget } from "./types/libraryRemoval";
import TasteProfileDrawer from "./components/taste/TasteProfileDrawer.vue";
import MusicRainBackground from "./components/scene/MusicRainBackground.vue";
import { supportsSnowScene } from "./components/scene/musicRain/loader";
import { stageWeather, type StageWeather } from "./components/scene/musicRain/signal";
import NowPlayingSign from "./components/scene/NowPlayingSign.vue";
import TopBar from "./components/scene/TopBar.vue";
import DesktopWindowControls from "./components/scene/DesktopWindowControls.vue";
import DesktopSettingsModal from "./components/settings/DesktopSettingsModal.vue";
import { useAudioPlayer, type PlaybackMode } from "./composables/useAudioPlayer";
import { useNcmAuth } from "./composables/useNcmAuth";
import { useQqAuth } from "./composables/useQqAuth";
import { useRadioSession } from "./composables/useRadioSession";
import { useToast } from "./composables/useToast";
import { useStageMotion } from "./composables/useStageMotion";
import { useGlobalHotkeys } from "./composables/useGlobalHotkeys";
import { getSetupStatus } from "./services/api";
import type { AdjustmentOperation, FeedbackAction, MusicAccount, MusicPlatform, PlaylistView, QueueSource, SetupStatus, Song } from "./types/radio";

type Drawer = "chat" | "playlist" | "taste" | null;

const drawer = ref<Drawer>(null);
const reviewSong = ref<Song | null>(null);
const removalTarget = ref<LibraryRemovalTarget | null>(null);
const libraryRevision = ref(0);
const aiSettingsOpen = ref(false);
const desktopSettingsOpen = ref(false);
const playerDock = ref<InstanceType<typeof PlayerDock> | null>(null);
const tastePanel = ref<InstanceType<typeof TasteProfileDrawer> | null>(null);
const usesChatGPTPlan = ref(false);
const firstRunOpen = ref(false);
const setupStatus = ref<SetupStatus | null>(null);
const { toast, notify } = useToast();
const checkDesktopUpdates = window.personalRadioDesktop?.checkForUpdates;
const getDesktopVersion = window.personalRadioDesktop?.getDesktopVersion;
const hasDesktopSettings = Boolean(checkDesktopUpdates && getDesktopVersion);
const canChooseWeather = supportsSnowScene;
const weatherOptions = [
  { value: "rain", label: "雨夜" },
  { value: "snow", label: "雪夜" },
  { value: "christmas", label: "圣诞夜" },
] as const;
const weather = ref<StageWeather>(stageWeather(canChooseWeather ? localStorage.getItem("personal-radio-stage-weather") : null));

function chooseWeather(next: StageWeather) {
  weather.value = next;
  localStorage.setItem("personal-radio-stage-weather", next);
}

function openDesktopSettings() {
  firstRunOpen.value = false;
  desktopSettingsOpen.value = true;
}

async function openTasteSettings() {
  desktopSettingsOpen.value = false;
  await nextTick();
  if (drawer.value === "taste") {
    tastePanel.value?.focus();
    return;
  }
  drawer.value = "taste";
}

async function openPlaybackSettings() {
  desktopSettingsOpen.value = false;
  await nextTick();
  playerDock.value?.openSettings();
}
const {
  profile: ncmProfile,
  qrOpen,
  qrImage,
  qrStatus,
  syncing,
  syncOpen,
  playlists,
  playlistsLoading,
  playlistsError,
  syncScope,
  syncTask,
  tagCoverage,
  syncProgressError,
  syncProgressReading,
  reconnectSync,
  completeMetadata,
  pauseMetadata,
  selectedPlaylistIds,
  openQr,
  closeQr,
  openSync,
  openSyncForPlaylist,
  closeSync,
  loadPlaylists,
  logout,
  syncPlaylist,
  retrySync,
} = useNcmAuth(notify);

const qqPlaylistsOpen = ref(false);
const qqAccount = useQqAuth(notify, () => { qqPlaylistsOpen.value = true; });
const { opened: qqQrOpen, image: qqQrImage, status: qqQrStatus, loading: qqQrLoading, available: qqAvailable, restoring: qqRestoring, profile: qqProfile } = qqAccount;
watch(qqProfile, (profile) => { if (profile) firstRunOpen.value = false; });
const musicAccounts = computed<MusicAccount[]>(() => {
  const accounts: MusicAccount[] = [{ platform: "netease", profile: ncmProfile.value }];
  if (qqAvailable.value) accounts.push({ platform: "qq", profile: qqAccount.profile.value });
  return accounts;
});

function connectMusicAccount(platform: MusicPlatform) {
  if (platform === "qq") {
    qqPlaylistsOpen.value = false;
    void qqAccount.open();
  } else void openQr();
}

function disconnectMusicAccount(platform: MusicPlatform) {
  if (platform === "qq") {
    qqPlaylistsOpen.value = false;
    void qqAccount.logout();
  } else void logout();
}

function openQqPlaylists() {
  if (qqAccount.profile.value) qqPlaylistsOpen.value = true;
  else connectMusicAccount("qq");
}

function reconnectQqAccount() {
  qqProfile.value = null;
  connectMusicAccount("qq");
}

const {
  sessionId,
  messages,
  songs,
  playlistView,
  loading: chatLoading,
  loadingHint: chatLoadingHint,
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
  clear: clearRadioSession,
} = useRadioSession(notify);
const {
  currentSong,
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
  isAnnouncing,
  djPlaybackNotices,
  isDjOnAir,
  visualMode,
  audioLow,
  audioMid,
  audioHigh,
  audioEnergy,
  audioBeat,
  audioSpectrum,
  audioAnalysisStatus,
  readAudioWaveform,
  audioAnalysisMessage,
  title,
  subtitle,
  progress,
  progressValue,
  displayElapsed,
  playerStatus,
  canGoPrevious,
  canGoNext,
  playbackQueue,
  queueSource,
  prepareQueue,
  applyAdjustment,
  appendToQueue,
  appendPlaylist,
  playNext,
  removeFromQueue,
  reorderQueue,
  togglePlayback,
  previewAnnouncement,
  skipAnnouncement,
  isPreviewing,
  isPreview,
  playEntry,
  playSong,
  moveQueue,
  seekToRatio,
  seekBy,
  seekToEdge,
  clearPlayer,
} = useAudioPlayer({
  notify,
  onLoginRequired: connectMusicAccount,
  onListeningEvent: reportListeningEvent,
  onAudioFeatures: (song, features) => { if (song.platform !== "qq" && !song.online_catalog) reportAudioFeatures(song, features); },
  onQueueExhausted: extendQueueFromSong,
});

function adjustMusicVolume(percent: number) {
  const next = Math.round(volume.value * 100) + percent;
  volume.value = Math.max(0, Math.min(100, next)) / 100;
}

useGlobalHotkeys(isPlaying, {
  toggle: () => { if (currentSong.value) togglePlayback(); },
  previous: () => { if (canGoPrevious.value) moveQueue(-1); },
  next: () => { if (canGoNext.value) moveQueue(1); },
  volume_up: () => adjustMusicVolume(5),
  volume_down: () => adjustMusicVolume(-5),
});
watch(playbackQueue, queue => {
  if (queueSource.value === "recommendation") confirmPlaybackQueue(queue);
});
const panelOpen = computed(() => drawer.value !== null || Boolean(reviewSong.value) || Boolean(removalTarget.value) || aiSettingsOpen.value || desktopSettingsOpen.value || qrOpen.value || qqQrOpen.value || qqPlaylistsOpen.value || syncOpen.value || firstRunOpen.value);
const { effectiveMode, constrained, reduced, note: motionNote } = useStageMotion(visualMode, panelOpen);
let queueIntentRevision = 0;
const adjustableBatch = computed(() => {
  const batch = songs.value[0]?.batch_id;
  return queueSource.value === "recommendation" && batch && playbackQueue.value.length
    && playbackQueue.value.every((song) => song.batch_id === batch) ? batch : undefined;
});

async function handleAdjustment(operation: AdjustmentOperation, song?: Song) {
  const batchId = adjustableBatch.value;
  if (!batchId || chatLoading.value) return;
  const revision = ++queueIntentRevision;
  const next = await adjustRound(operation, batchId, song?.id);
  if (revision !== queueIntentRevision || !next.length) return;
  if (applyAdjustment(batchId, next, operation === "replace" ? song?.id : undefined)) {
    notify(operation === "replace" ? "只换了这一首，不记录长期不喜欢" : "已调整待播推荐，当前音乐不被打断");
  }
}

function openLibrary() {
  playlistView.value = "playlists";
  drawer.value = "playlist";
}

function openQueue() {
  playlistView.value = "queue";
  drawer.value = "playlist";
}

function toggleDrawer(next: Exclude<Drawer, null>) {
  drawer.value = drawer.value === next ? null : next;
}

function handlePlay(song: Song, queue: Song[], source: QueueSource, mode?: PlaybackMode) {
  queueIntentRevision += 1;
  if (mode) playbackMode.value = mode;
  playEntry(song, queue, source);
}

function handleEnqueue(song: Song) {
  queueIntentRevision += 1;
  const added = appendToQueue(song, "manual");
  notify(added ? `已将《${song.title}》加入手动队列` : "这首歌已经在当前队列中");
}

function handleQqEnqueue(items: Song[]) {
  queueIntentRevision += 1;
  const added = appendPlaylist(items);
  notify(added ? `已加入 ${added} 首 QQ 音乐歌曲` : "这些歌曲已经在当前队列中");
}

function handleQqPlay(song: Song, queue: Song[], restart = false) {
  qqPlaylistsOpen.value = false;
  if (!restart) return handlePlay(song, queue, "manual");
  queueIntentRevision += 1;
  void playSong(song, queue, "manual");
}

function handlePlayNext(song: Song) {
  queueIntentRevision += 1;
  notify(playNext(song) ? `《${song.title}》将在下一首播放` : "这首歌就是当前歌曲");
}

function handleRemove(songId: string) {
  queueIntentRevision += 1;
  if (removeFromQueue(songId)) notify("已移出播放队列，不删除曲库歌曲");
}

function handleLibraryRemoved(result: LibraryRemovalPreview) {
  queueIntentRevision += 1;
  // 曲库移除不打断当前音源；其余待播项立刻移出，历史记录继续保留。
  const removedIds = new Set(result.removed_song_ids);
  for (const song of [...playbackQueue.value]) {
    if (removedIds.has(song.id) && song.id !== currentSong.value?.id) removeFromQueue(song.id);
  }
  libraryRevision.value += 1;
  notify(`已移出本地，移除 ${result.removed_song_count} 首；云端保持不变`);
}

function handleMove(songId: string, step: -1 | 1) {
  queueIntentRevision += 1;
  reorderQueue(songId, step);
}

async function handlePrompt(message: string) {
  const revision = ++queueIntentRevision;
  drawer.value = "chat";
  const batchId = adjustableBatch.value;
  const queueSongIds = batchId ? playbackQueue.value.map(({ id }) => id) : undefined;
  let applyToQueue = (next: Song[]) => prepareQueue(next, "recommendation");
  const current = currentSong.value;
  const localSongId = current && current.platform !== "qq" && !current.online_catalog ? current.id : undefined;
  const nextSongs = await sendMessage(message, localSongId, batchId, queueSongIds, replacement => {
    // 随机模式也能改变可见编号，不一定经过手动编辑队列的计数器。
    if (revision !== queueIntentRevision || replacement.batch_id !== adjustableBatch.value
      || !queueSongIds || playbackQueue.value.length !== queueSongIds.length
      || playbackQueue.value.some(({ id }, index) => id !== queueSongIds[index])) return false;
    applyToQueue = (next) => { applyAdjustment(replacement.batch_id, next, replacement.song_id); };
    return true;
  });
  // 用户在等推荐期间开始手动选歌，迟到结果只留在历史，不抢走手动队列。
  if (revision === queueIntentRevision && nextSongs.length) applyToQueue(nextSongs);
}

function handleFeedback(song: Song, action: FeedbackAction) {
  void feedback(song, action);
}

function changePlaylistView(view: PlaylistView) {
  playlistView.value = view;
}

async function clearEverything() {
  queueIntentRevision += 1;
  // 先停止播放器及其续播请求，不让服务端清理等待期间又开始下一首。
  clearPlayer();
  drawer.value = null;
  await clearRadioSession();
}

onMounted(() => {
  if (songs.value.length) prepareQueue(songs.value, "recommendation");
  void getSetupStatus().then((status) => {
    setupStatus.value = status;
    usesChatGPTPlan.value = status.llm.provider === "chatgpt" && status.llm.has_api_key;
    firstRunOpen.value = status.library.song_count === 0 && !qqProfile.value;
  }).catch(() => { /* 后端旧版本或暂时不可用时不阻塞电台主界面。 */ });
});
</script>

<template>
  <main
    class="radio-scene spectral-scene"
    :class="[
      {
        'desktop-shell': hasDesktopSettings,
        'is-playing': isPlaying,
        'audio-reactive': audioAnalysisStatus === 'active',
        'motion-reduced': reduced,
        'drawer-open': drawer !== null,
        'dj-mode': drawer === 'chat',
        'playlist-mode': drawer === 'playlist',
        'queue-mode': drawer === 'playlist' || drawer === 'taste',
      },
      `visual-${effectiveMode}`,
    ]"
    :style="{
      '--audio-low': effectiveMode === 'quiet' ? 0 : audioLow * (panelOpen ? .25 : 1),
      '--audio-mid': effectiveMode === 'quiet' ? 0 : audioMid * (panelOpen ? .25 : 1),
      '--audio-high': effectiveMode === 'quiet' ? 0 : audioHigh * (panelOpen ? .25 : 1),
      '--audio-energy': effectiveMode === 'quiet' ? 0 : audioEnergy * (panelOpen ? .25 : 1),
      '--audio-beat': effectiveMode === 'quiet' ? 0 : audioBeat * (panelOpen ? .25 : 1),
    }"
  >
    <DesktopWindowControls v-if="hasDesktopSettings" />
    <MusicRainBackground :spectrum="audioSpectrum" :low="audioLow" :mid="audioMid" :high="audioHigh"
      :energy="audioEnergy" :beat="audioBeat" :is-playing="isPlaying" :paused="playbackState === 'paused'" :analysis-status="audioAnalysisStatus"
      :visual-mode="effectiveMode" :constrained="constrained || panelOpen" :weather="weather" :read-waveform="readAudioWaveform" />
    <div v-if="canChooseWeather && !panelOpen" class="stage-weather-switch" role="group" aria-label="舞台背景">
      <button v-for="option in weatherOptions" :key="option.value" type="button"
        :aria-pressed="weather === option.value" @click="chooseWeather(option.value)">{{ option.label }}</button>
    </div>

    <TopBar
      :desktop="hasDesktopSettings"
      :accounts="musicAccounts"
      :syncing="syncing"
      :active-panel="drawer === 'playlist' && (playlistView === 'queue' || playlistView === 'history') ? null : drawer"
      @library="openLibrary"
      @dj="drawer = 'chat'"
      @home="drawer = null"
      @connect="connectMusicAccount"
      @sync="openSync"
      @qq-playlists="openQqPlaylists"
      @disconnect="disconnectMusicAccount"
      @clear="clearEverything"
      @taste="toggleDrawer('taste')"
      @settings="aiSettingsOpen = true"
      @desktop-settings="openDesktopSettings"
    />

    <NowPlayingSign
      :title="title"
      :subtitle="subtitle"
      :current-song="currentSong"
      :queue="playbackQueue"
      :can-previous="canGoPrevious"
      :can-next="canGoNext"
      @previous="moveQueue(-1)"
      @next="moveQueue(1)"
      :progress-value="progressValue"
      :elapsed="displayElapsed"
      :is-playing="isPlaying"
      :is-announcing="isDjOnAir"
      :lyric-lines="lyricLines"
      :lyrics-instrumental="lyricsInstrumental"
      :visual-mode="effectiveMode"
      :constrained="constrained || panelOpen"
      :analysis-status="audioAnalysisStatus"
      :analysis-message="audioAnalysisMessage"
    />
    <Transition name="scrim">
      <button v-if="drawer" class="drawer-scrim" type="button" aria-label="关闭当前面板" @click="drawer = null"></button>
    </Transition>

    <Transition name="radio-panel-left">
      <ChatDrawer v-if="drawer === 'chat'" :messages="messages" :loading="chatLoading" :loading-hint="chatLoadingHint" :can-adjust="Boolean(adjustableBatch)" :dj-program="djProgram" :usesChatGPTPlan="usesChatGPTPlan"
        :dj-playback-notices="djPlaybackNotices" @close="drawer = null" @send="handlePrompt" @adjust="handleAdjustment" />
    </Transition>

    <Transition name="radio-panel-right">
      <PlaylistDrawer
        v-if="drawer === 'playlist'"
        :entries="playlistView === 'history' ? playlistEntries : []"
        :queue="playlistView === 'history' ? historyQueue : playbackQueue"
        :queue-source="queueSource"
        :view="playlistView"
        :queue-count="playbackQueue.length"
        :history-count="historyCount"
        :current-song-id="currentSong?.id"
        :is-playing="isPlaying"
        :syncing="syncing"
        :qq-available="qqAvailable"
        :adjustable-batch="adjustableBatch"
        :adjusting="chatLoading"
        :library-revision="libraryRevision"
        :netease-logged-in="Boolean(ncmProfile)"
        @close="drawer = null"
        @change-view="changePlaylistView"
        @login-netease="connectMusicAccount('netease')"
        @play="handlePlay"
        @enqueue="handleEnqueue"
        @qq-playlists="openQqPlaylists"
        @play-next="handlePlayNext"
        @remove="handleRemove"
        @move="handleMove"
        @feedback="handleFeedback"
        @replace="handleAdjustment('replace', $event)"
        @review="reviewSong = $event"
        @remove-library="removalTarget = $event"
        @sync-playlist="openSyncForPlaylist"
      />
    </Transition>

    <Transition name="radio-panel-right">
      <TasteProfileDrawer
        v-if="drawer === 'taste'"
        ref="tastePanel"
        :session-id="sessionId"
        @close="drawer = null"
      />
    </Transition>

    <SongReviewModal v-if="reviewSong" :song="reviewSong" :session-id="sessionId" @close="reviewSong = null" />
    <LibraryRemovalModal v-if="removalTarget" :key="`${removalTarget.kind}-${removalTarget.target_id}`" :target="removalTarget" @close="removalTarget = null" @removed="handleLibraryRemoved" />

    <PlayerDock
      ref="playerDock"
      :current-song="currentSong"
      :is-playing="isPlaying"
      :loading="audioLoading"
      :error="playbackError"
      :status="playerStatus"
      :progress="progress"
      :progress-value="progressValue"
      :elapsed="displayElapsed"
      :duration="duration"
      :volume="volume"
      :voice-volume="voiceVolume"
      :dj-voice="djVoice"
      :dj-voices="DJ_VOICES"
      :announce-on="announceOn"
      :dj-length="djLength === 'auto' ? (djProgram?.copy_length ?? 'normal') : djLength"
      :auto-play-on="autoPlayOn"
      :smart-continue-on="smartContinueOn"
      :playback-mode="playbackMode"
      :queue-source="queueSource"
      :acoustic-backfill-on="acousticBackfillOn"
      :announcing="isAnnouncing"
      :previewing="isPreviewing"
      :playback-preview="isPreview"
      :chat-open="drawer === 'chat'"
      :queue-open="drawer === 'playlist'"
      :visual-mode="visualMode"
      :analysis-status="audioAnalysisStatus"
      :analysis-message="audioAnalysisMessage"
      :motion-note="motionNote"
      :can-previous="canGoPrevious"
      :can-next="canGoNext"
      @toggle="togglePlayback"
      @previous="moveQueue(-1)"
      @next="moveQueue(1)"
      @seek-ratio="seekToRatio"
      @seek-by="seekBy"
      @seek-edge="seekToEdge"
      @update-volume="volume = $event"
      @update-voice-volume="voiceVolume = $event"
      @update-dj-voice="djVoice = $event"
      @toggle-announce="announceOn = !announceOn"
      @update-dj-length="setDJLength"
      @preview-announce="previewAnnouncement"
      @skip-announce="skipAnnouncement"
      @toggle-autoplay="autoPlayOn = !autoPlayOn"
      @toggle-smart-continue="smartContinueOn = !smartContinueOn"
      @update-playback-mode="playbackMode = $event"
      @queue="openQueue"
      @chat="toggleDrawer('chat')"
      @toggle-acoustic-backfill="acousticBackfillOn = !acousticBackfillOn"
      @update-visual-mode="visualMode = $event"
    />

    <Transition name="modal">
      <NcmLoginModal v-if="qqQrOpen" key="qq" platform="qq" :qr-image="qqQrImage" :status="qqQrStatus" :loading="qqQrLoading" @close="qqAccount.close" @refresh="qqAccount.open" />
      <NcmLoginModal v-else-if="qrOpen" key="netease" :qr-image="qrImage" :status="qrStatus" @close="closeQr" @refresh="openQr" />
    </Transition>
    <Transition name="modal">
      <QqPlaylistModal v-if="qqPlaylistsOpen" @close="qqPlaylistsOpen = false" @reconnect="reconnectQqAccount" @play="handleQqPlay" @enqueue="handleQqEnqueue" />
    </Transition>
    <Transition name="modal">
      <NcmPlaylistModal
        v-if="syncOpen"
        :playlists="playlists"
        :selected-ids="selectedPlaylistIds"
        :scope="syncScope"
        :loading="playlistsLoading"
        :syncing="syncing"
        :task="syncTask"
        :coverage="tagCoverage"
        :progress-error="syncProgressError"
        :progress-reading="syncProgressReading"
        @reconnect="reconnectSync"
        @metadata="completeMetadata"
        @pause="pauseMetadata"
        :error="playlistsError"
        @close="closeSync"
        @reload="loadPlaylists"
        @sync="syncPlaylist"
        @retry="retrySync"
        @update:scope="syncScope = $event"
        @update:selected-ids="selectedPlaylistIds = $event"
      />
    </Transition>
    <Transition name="modal">
      <AISettingsModal v-if="aiSettingsOpen" @close="aiSettingsOpen = false" @notify="notify" @saved="usesChatGPTPlan = $event" />
    </Transition>
    <Transition name="modal">
      <FirstRunModal
        v-if="firstRunOpen && setupStatus && !qqRestoring"
        :status="setupStatus"
        :qq-available="qqAvailable"
        @close="firstRunOpen = false"
        @login="firstRunOpen = false; openQr()"
        @qq-login="firstRunOpen = false; connectMusicAccount('qq')"
        @sync="firstRunOpen = false; openSync()"
        @ai="firstRunOpen = false; aiSettingsOpen = true"
      />
    </Transition>
    <Transition name="modal">
      <DesktopSettingsModal v-if="desktopSettingsOpen && checkDesktopUpdates && getDesktopVersion"
        :check-for-updates="checkDesktopUpdates" :get-version="getDesktopVersion"
        @close="desktopSettingsOpen = false"
        @ai="desktopSettingsOpen = false; aiSettingsOpen = true"
        @taste="openTasteSettings"
        @playback="openPlaybackSettings" />
    </Transition>
    <Transition name="toast"><p v-if="toast" class="toast">{{ toast }}</p></Transition>
  </main>
</template>
