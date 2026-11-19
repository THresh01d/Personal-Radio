<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from "vue";
import { usePanelFocus } from "../../composables/usePanelFocus";
import type { PlaybackMode } from "../../composables/useAudioPlayer";
import { getLocalLibraryPlaylists, getLocalLibrarySongs, getNcmPlaylists, getRemovedLibraryItems } from "../../services/api";
import SongItem from "./SongItem.vue";
import PlayerGlass from "../player/PlayerGlass.vue";
import RemovedLibraryPanel from "./RemovedLibraryPanel.vue";
import OnlineCatalog from "./OnlineCatalog.vue";
import type { LibraryRemovalTarget } from "../../types/libraryRemoval";
import type {
  FeedbackAction,
  LocalLibraryPlaylist,
  LocalLibrarySongs,
  NcmPlaylist,
  PlaylistEntry,
  PlaylistView,
  QueueSource,
  Song,
} from "../../types/radio";

const props = defineProps<{
  entries: PlaylistEntry[];
  queue: Song[];
  queueSource: QueueSource;
  view: PlaylistView;
  queueCount: number;
  historyCount: number;
  currentSongId?: string;
  isPlaying: boolean;
  syncing: boolean;
  adjustableBatch?: string;
  adjusting?: boolean;
  libraryRevision?: number;
  qqAvailable?: boolean;
  neteaseLoggedIn?: boolean;
}>();

const emit = defineEmits<{
  close: [];
  changeView: [view: PlaylistView];
  play: [song: Song, queue: Song[], source: QueueSource, mode?: PlaybackMode];
  enqueue: [song: Song];
  feedback: [song: Song, action: FeedbackAction];
  syncPlaylist: [playlistId: string];
  qqPlaylists: [];
  loginNetease: [];
  playNext: [song: Song];
  remove: [songId: string];
  move: [songId: string, step: -1 | 1];
  replace: [song: Song];
  review: [song: Song];
  removeLibrary: [target: LibraryRemovalTarget];
}>();

const expandedSongId = ref("");
const removedPanelOpen = ref(false);
const removedPlaylistIds = ref(new Set<string>());
const panel = ref<HTMLElement | null>(null);
const onPanelKeydown = usePanelFocus(panel, () => emit("close"));
const playlists = ref<LocalLibraryPlaylist[]>([]);
const cloudPlaylists = ref<NcmPlaylist[]>([]);
const playlistsLoaded = ref(false);
const cloudLoading = ref(false);
const cloudError = ref("");
const playlistQuery = ref("");
const visiblePlaylistLimit = ref(30);
const selectedPlaylist = ref<LocalLibraryPlaylist | null>(null);
const librarySongs = ref<Song[]>([]);
const libraryTotal = ref(0);
const libraryHasMore = ref(false);
const libraryStats = ref<LocalLibrarySongs["library_stats"]>(null);
const libraryLoading = ref(false);
const libraryError = ref("");
const searchDraft = ref("");
const activeQuery = ref("");
const pageSize = 50;
const queuePageSize = 50;
const queuePage = ref(0);
let contentRevision = 0;

type PlaylistStatus = "synced" | "stale" | "local-only" | "unsynced";
type PlaylistCard = {
  id: string;
  name: string;
  cover_url?: string | null;
  is_liked: boolean;
  owned_by_user: boolean;
  subscribed: boolean;
  status: PlaylistStatus;
  local?: LocalLibraryPlaylist;
  cloud?: NcmPlaylist;
};

const browsingLibrary = computed(() => props.view === "library" || (props.view === "playlists" && selectedPlaylist.value));
const queuePageCount = computed(() => Math.max(1, Math.ceil(props.queue.length / queuePageSize)));
const queueStart = computed(() => queuePage.value * queuePageSize);
const visibleEntries = computed<PlaylistEntry[]>(() => {
  if (props.view !== "queue") return props.entries;
  return props.queue
    .slice(queueStart.value, queueStart.value + queuePageSize)
    .map((song) => ({ song, heading: "" }));
});
const queueRange = computed(() => {
  if (!props.queue.length) return "";
  const start = queueStart.value + 1;
  const end = Math.min(props.queue.length, queueStart.value + queuePageSize);
  return `${start}–${end} / ${props.queue.length}`;
});
const playlistCards = computed<PlaylistCard[]>(() => {
  const localById = new Map(playlists.value.map((playlist) => [playlist.id, playlist]));
  const cloudById = new Map(cloudPlaylists.value.map((playlist) => [playlist.id, playlist]));
  const ids = new Set([...localById.keys(), ...cloudById.keys()]);
  const statusOrder: Record<PlaylistStatus, number> = { stale: 0, synced: 1, "local-only": 2, unsynced: 3 };
  return [...ids].map((id) => {
    const local = localById.get(id);
    const cloud = cloudById.get(id);
    let status: PlaylistStatus = "unsynced";
    if (local && cloud) {
      status = cloud.remote_updated_at > local.remote_updated_at ? "stale" : "synced";
    } else if (local) {
      status = "local-only";
    }
    return {
      id,
      name: cloud?.name || local?.name || "未命名歌单",
      cover_url: cloud?.cover_url || local?.cover_url,
      is_liked: Boolean(cloud?.is_liked || local?.is_liked),
      owned_by_user: cloud?.owned_by_user ?? Boolean(local?.is_liked),
      subscribed: cloud?.subscribed ?? false,
      status,
      local,
      cloud,
    };
  }).sort((a, b) => (
    statusOrder[a.status] - statusOrder[b.status]
    || Number(b.is_liked) - Number(a.is_liked)
    || Number(b.owned_by_user) - Number(a.owned_by_user)
    || a.name.localeCompare(b.name, "zh-CN")
  ));
});
const filteredPlaylistCards = computed(() => {
  const query = playlistQuery.value.trim().toLocaleLowerCase();
  return query
    ? playlistCards.value.filter((playlist) => playlist.name.toLocaleLowerCase().includes(query))
    : playlistCards.value;
});
const visiblePlaylistCards = computed(() => filteredPlaylistCards.value.slice(0, visiblePlaylistLimit.value));
const syncedPlaylistCount = computed(() => playlists.value.length);
const drawerTitle = computed(() => {
  if (props.view === "queue") return props.queueSource === "manual" ? "手动播放队列" : "DJ 播放队列";
  if (props.view === "history") return "推荐历史";
  if (props.view === "recommended") return "网易云推荐";
  if (props.view === "search") return "全平台搜索";
  if (selectedPlaylist.value) return selectedPlaylist.value.name;
  return props.view === "playlists" ? "我的歌单" : "本地曲库";
});

function toggleDetails(songId: string) {
  expandedSongId.value = expandedSongId.value === songId ? "" : songId;
}

function changeQueuePage(delta: -1 | 1) {
  queuePage.value = Math.min(queuePageCount.value - 1, Math.max(0, queuePage.value + delta));
  expandedSongId.value = "";
}

function locateCurrentQueuePage() {
  if (props.view !== "queue") return;
  const currentIndex = props.queue.findIndex((song) => song.id === props.currentSongId);
  queuePage.value = currentIndex >= 0 ? Math.floor(currentIndex / queuePageSize) : 0;
}

async function loadPlaylists(force = false) {
  if (playlistsLoaded.value && !force) return;
  libraryLoading.value = true;
  libraryError.value = "";
  try {
    playlists.value = await getLocalLibraryPlaylists();
    playlistsLoaded.value = true;
    try { removedPlaylistIds.value = new Set((await getRemovedLibraryItems()).playlist_ids); } catch { /* 旧后端不阻断既有浏览。 */ }
  } catch (error) {
    libraryError.value = error instanceof Error ? error.message : "本地歌单读取失败";
  } finally {
    libraryLoading.value = false;
  }

  // 云端读取失败不能阻断离线曲库；本地快照先展示，云端状态随后补齐。
  cloudLoading.value = true;
  cloudError.value = "";
  try {
    cloudPlaylists.value = await getNcmPlaylists();
  } catch (error) {
    cloudError.value = error instanceof Error ? error.message : "网易云歌单暂时读取失败";
  } finally {
    cloudLoading.value = false;
  }
}

async function loadSongs(reset = true) {
  if (!browsingLibrary.value) return;
  const revision = ++contentRevision;
  libraryLoading.value = true;
  libraryError.value = "";
  try {
    const offset = reset ? 0 : librarySongs.value.length;
    const result = await getLocalLibrarySongs({
      playlistId: selectedPlaylist.value?.id,
      query: activeQuery.value,
      offset,
      limit: pageSize,
      order: selectedPlaylist.value ? "playlist" : "title",
    });
    if (revision !== contentRevision) return;
    librarySongs.value = reset ? result.songs : [...librarySongs.value, ...result.songs];
    libraryTotal.value = result.total;
    libraryHasMore.value = result.has_more;
    if (reset) libraryStats.value = result.library_stats || null;
  } catch (error) {
    if (revision !== contentRevision) return;
    libraryError.value = error instanceof Error ? error.message : "本地歌曲读取失败";
  } finally {
    if (revision === contentRevision) libraryLoading.value = false;
  }
}

function selectPlaylist(playlist: PlaylistCard) {
  if (!playlist.local) return;
  selectedPlaylist.value = playlist.local;
  searchDraft.value = "";
  activeQuery.value = "";
  void loadSongs();
}

function playlistStatusLabel(status: PlaylistStatus) {
  return {
    synced: "已同步",
    stale: "云端有更新",
    "local-only": "本地保留",
    unsynced: "未同步",
  }[status];
}

function formatSyncedAt(value?: string | null) {
  if (!value) return "尚未同步";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : `同步于 ${date.toLocaleDateString("zh-CN")}`;
}

function requestPlaylistSync(playlist: PlaylistCard) {
  emit("syncPlaylist", playlist.id);
}

function leavePlaylist() {
  contentRevision += 1;
  libraryLoading.value = false;
  selectedPlaylist.value = null;
  librarySongs.value = [];
  libraryTotal.value = 0;
  libraryStats.value = null;
  activeQuery.value = "";
  searchDraft.value = "";
}

function submitSearch() {
  activeQuery.value = searchDraft.value.trim();
  void loadSongs();
}

async function fetchManualQueue(order: "playlist" | "title" | "random") {
  const result = await getLocalLibrarySongs({
    playlistId: selectedPlaylist.value?.id,
    query: activeQuery.value,
    offset: 0,
    limit: 5000,
    order,
  });
  return result.songs;
}

async function playManual(song?: Song, random = false) {
  if (libraryLoading.value) return;
  const revision = ++contentRevision;
  libraryLoading.value = true;
  libraryError.value = "";
  try {
    const queue = await fetchManualQueue(random ? "random" : (selectedPlaylist.value ? "playlist" : "title"));
    // 关闭面板或切换视图代表用户改变操作方向，迟到队列不能突然开播。
    if (revision !== contentRevision) return;
    if (!queue.length) return;
    const target = random ? queue[0] : queue.find((item) => item.id === song?.id) || queue[0];
    emit("play", target, queue, "manual", song ? undefined : random ? "shuffle" : "sequence");
  } catch (error) {
    if (revision !== contentRevision) return;
    libraryError.value = error instanceof Error ? error.message : "播放队列读取失败";
  } finally {
    if (revision === contentRevision) libraryLoading.value = false;
  }
}

watch(() => props.view, (view) => {
  contentRevision += 1;
  libraryLoading.value = false;
  expandedSongId.value = "";
  libraryError.value = "";
  if (view === "playlists") {
    if (selectedPlaylist.value) void loadSongs();
    else void loadPlaylists();
  }
  if (view === "library") {
    selectedPlaylist.value = null;
    searchDraft.value = "";
    activeQuery.value = "";
    void loadSongs();
  }
}, { immediate: true });
onBeforeUnmount(() => { contentRevision += 1; });

// 抽屉只挂载当前页的 50 行，完整队列仍留在播放器中；切歌时自动定位当前页。
watch([() => props.view, () => props.currentSongId], locateCurrentQueuePage, { immediate: true });
watch(() => props.queue.length, () => {
  queuePage.value = Math.min(queuePage.value, queuePageCount.value - 1);
});
watch(playlistQuery, () => { visiblePlaylistLimit.value = 30; });
watch(() => props.syncing, (syncing, previous) => {
  if (previous && !syncing && props.view === "playlists") void loadPlaylists(true);
});
watch(() => props.libraryRevision, () => {
  contentRevision += 1;
  if (selectedPlaylist.value) selectedPlaylist.value = null;
  playlistsLoaded.value = false;
  if (props.view === "library") void loadSongs();
  else if (props.view === "playlists") void loadPlaylists(true);
});
</script>

<template>
  <aside ref="panel" class="side-drawer playlist-drawer radio-panel" :class="{ 'library-wide': view === 'library' || view === 'playlists' }" aria-label="曲库与播放队列" tabindex="-1" @keydown="onPanelKeydown">
    <PlayerGlass :corner-radius="24" />
    <header>
      <h2>{{ drawerTitle }}</h2>
      <button v-if="view !== 'search' && view !== 'recommended'" class="online-search-toggle" type="button" aria-label="打开全平台搜索" title="全平台搜索" @click="$emit('changeView', 'search')"><svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="10.5" cy="10.5" r="5.5" /><path d="m15 15 4 4" /></svg></button>
      <button type="button" aria-label="关闭歌单" @click="$emit('close')"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="m6 6 12 12M18 6 6 18" /></svg></button>
    </header>
    <nav class="playlist-tabs" aria-label="曲库视图">
      <button type="button" :class="{ active: view === 'queue' }" @click="$emit('changeView', 'queue')">当前队列 {{ queueCount }}</button>
      <button type="button" :class="{ active: view === 'playlists' || view === 'library' }" @click="$emit('changeView', 'playlists')">我的歌单</button>
      <button type="button" :class="{ active: view === 'recommended' }" @click="$emit('changeView', 'recommended')">网易云推荐</button>
      <button type="button" :class="{ active: view === 'history' }" @click="$emit('changeView', 'history')">推荐历史 {{ historyCount }}</button>
    </nav>
    <div v-if="view === 'library' || view === 'playlists'" class="local-removal-toolbar">
      <span v-if="view === 'playlists' && !selectedPlaylist" class="playlist-service-name">网易云歌单</span>
      <button v-if="view === 'playlists' && !selectedPlaylist" type="button" @click="$emit('changeView', 'library')">本地曲库</button>
      <button v-if="view === 'playlists' && !selectedPlaylist && qqAvailable" type="button" @click="$emit('qqPlaylists')">QQ 音乐歌单 ↗</button>
      <button class="local-removed-button" type="button" title="管理从本地曲库移除的歌曲和歌单" @click="removedPanelOpen = !removedPanelOpen">{{ removedPanelOpen ? '收起已移除' : '本地已移除' }}</button>
    </div>
    <RemovedLibraryPanel v-if="removedPanelOpen && (view === 'library' || view === 'playlists')" @close="removedPanelOpen = false" @changed="loadPlaylists(true)" />

    <template v-if="view === 'queue' || view === 'history'">
      <p v-if="view === 'queue' && queue.length" class="queue-boundary">{{ queueSource === 'manual' ? '自己选歌 · 队列播完停止，不接 AI 推荐' : 'DJ 选歌 · 是否接新推荐由播放设置控制' }}</p>
      <p v-if="!visibleEntries.length" class="empty">{{ view === "history" ? "还没有推荐历史" : "队列为空，从「我的歌单」选歌或请 DJ 推荐。" }}</p>
      <div v-if="view === 'queue' && queue.length > queuePageSize" class="queue-pager" aria-label="队列分页">
        <button type="button" :disabled="queuePage === 0" @click="changeQueuePage(-1)">← 上一页</button>
        <span>第 {{ queueRange }} 首</span>
        <button type="button" :disabled="queuePage >= queuePageCount - 1" @click="changeQueuePage(1)">下一页 →</button>
      </div>
      <ol v-if="visibleEntries.length" class="playlist">
        <template v-for="(entry, index) in visibleEntries" :key="`${entry.song.id}-${queueStart + index}`">
          <li v-if="entry.heading" class="round-heading">{{ entry.heading }}</li>
          <SongItem
            :song="entry.song"
            :index="view === 'queue' ? queueStart + index : index"
            :current="entry.song.id === currentSongId"
            :playing="isPlaying"
            :expanded="expandedSongId === entry.song.id"
            allow-next
            allow-review
            :manage-queue="view === 'queue'"
            :allow-replace="view === 'queue' && !adjusting && Boolean(adjustableBatch) && entry.song.batch_id === adjustableBatch"
            :can-move-up="queueStart + index > 0"
            :can-move-down="queueStart + index < queue.length - 1"
            @toggle="toggleDetails(entry.song.id)"
            @play="emit('play', entry.song, queue, view === 'queue' ? queueSource : 'recommendation')"
            @feedback="(action) => emit('feedback', entry.song, action)"
            @next="emit('playNext', entry.song)"
            @remove="emit('remove', entry.song.id)"
            @move="(step) => emit('move', entry.song.id, step)"
            @replace="emit('replace', entry.song)"
            @review="emit('review', entry.song)"
          />
        </template>
      </ol>
    </template>

    <template v-else-if="view === 'playlists' && !selectedPlaylist">
      <p v-if="libraryLoading && !playlistsLoaded" class="drawer-status">正在读取本地歌单…</p>
      <div v-else-if="libraryError" class="drawer-status error">{{ libraryError }}<button type="button" @click="loadPlaylists()">重试</button></div>
      <div v-else class="playlist-browser">
        <div class="playlist-overview">
          <p><strong>{{ syncedPlaylistCount }}</strong> 张已导入本地</p>
          <button type="button" :disabled="cloudLoading" @click="loadPlaylists(true)">{{ cloudLoading ? "正在刷新…" : "刷新状态" }}</button>
        </div>
        <p v-if="cloudError" class="playlist-cloud-note">云端列表暂不可用，正在显示本地快照：{{ cloudError }}</p>
        <label class="playlist-search"><span class="sr-only">搜索歌单</span><input v-model="playlistQuery" type="search" placeholder="搜索网易云歌单" /></label>
        <p v-if="!playlistCards.length && !cloudLoading" class="empty">登录网易云后可查看和导入歌单。</p>
        <p v-else-if="!filteredPlaylistCards.length" class="empty">没有找到名称匹配的歌单。</p>
        <div v-else class="local-playlists">
          <article v-for="playlist in visiblePlaylistCards" :key="playlist.id" :class="`status-${playlist.status}`">
            <img v-if="playlist.cover_url" :src="playlist.cover_url" alt="" loading="lazy" />
            <span v-else class="playlist-cover-placeholder" aria-hidden="true">♪</span>
            <div class="playlist-card-copy">
              <strong :title="playlist.name">{{ playlist.name }}</strong>
              <div class="playlist-badges">
                <i :class="`status-${playlist.status}`">{{ removedPlaylistIds.has(playlist.id) ? '已移出本地' : playlistStatusLabel(playlist.status) }}</i>
                <i>{{ playlist.cloud ? (playlist.owned_by_user ? "我创建的" : "我收藏的") : "本地快照" }}</i>
              </div>
              <small>
                <template v-if="playlist.local">本地 {{ playlist.local.track_count }} 首</template>
                <template v-if="playlist.local && playlist.cloud"> · </template>
                <template v-if="playlist.cloud">云端 {{ playlist.cloud.track_count }} 首</template>
              </small>
              <time>{{ formatSyncedAt(playlist.local?.synced_at) }}</time>
            </div>
            <div class="playlist-card-actions">
              <button v-if="playlist.local" type="button" @click="selectPlaylist(playlist)">浏览</button>
              <button v-if="playlist.cloud && (playlist.status === 'unsynced' || playlist.status === 'stale') && !removedPlaylistIds.has(playlist.id)" type="button" class="primary" @click="requestPlaylistSync(playlist)">{{ playlist.status === "stale" ? "更新" : "同步" }}</button>
              <button v-if="removedPlaylistIds.has(playlist.id)" type="button" @click="removedPanelOpen = true">管理移除</button>
              <button v-if="playlist.local" type="button" :disabled="syncing" @click="emit('removeLibrary', { kind: 'playlist', target_id: playlist.id, name: playlist.name })">移出本地</button>
            </div>
          </article>
        </div>
        <button v-if="visiblePlaylistCards.length < filteredPlaylistCards.length" class="playlist-more" type="button" @click="visiblePlaylistLimit += 30">再显示 30 张（剩余 {{ filteredPlaylistCards.length - visiblePlaylistCards.length }}）</button>
      </div>
    </template>

    <OnlineCatalog v-else-if="view === 'recommended' || view === 'search'" :recommended="view === 'recommended'"
      :netease-logged-in="Boolean(neteaseLoggedIn)" :current-song-id="currentSongId" :is-playing="isPlaying"
      @search="$emit('changeView', 'search')" @login="$emit('loginNetease')"
      @play="(song, queue) => $emit('play', song, queue, 'manual')" @enqueue="$emit('enqueue', $event)" @next="$emit('playNext', $event)" />

    <template v-else>
      <div class="library-toolbar">
        <button v-if="selectedPlaylist" class="back-button" type="button" @click="leavePlaylist">← 返回歌单</button>
        <button v-else class="back-button" type="button" @click="$emit('changeView', 'playlists')">← 返回我的歌单</button>
        <form role="search" @submit.prevent="submitSearch">
          <input v-model="searchDraft" type="search" placeholder="筛选本地歌曲" aria-label="搜索本地曲库" />
          <button type="submit">搜索</button>
        </form>
        <div class="library-play-actions">
          <button type="button" :disabled="!libraryTotal || libraryLoading" @click="playManual()">▶ 顺序播放</button>
          <button type="button" :disabled="!libraryTotal || libraryLoading" @click="playManual(undefined, true)">⤨ 随机播放</button>
        </div>
        <small v-if="activeQuery">“{{ activeQuery }}”找到 {{ libraryTotal }} 首</small>
        <small v-else-if="!selectedPlaylist && libraryStats">本地去重曲库 {{ libraryStats.unique_songs }} 首 · {{ libraryStats.linked_songs }} 首来自已同步歌单<template v-if="libraryStats.unassigned_songs"> · {{ libraryStats.unassigned_songs }} 首本地保留</template></small>
        <small v-else>本地共 {{ libraryTotal }} 首</small>
      </div>
      <div v-if="libraryError" class="drawer-status error">{{ libraryError }}<button type="button" @click="loadSongs()">重试</button></div>
      <p v-else-if="libraryLoading && !librarySongs.length" class="drawer-status">正在读取本地歌曲…</p>
      <p v-else-if="!librarySongs.length" class="empty">没有找到符合条件的歌曲。</p>
      <ol v-else class="playlist local-song-list">
        <SongItem
          v-for="(song, index) in librarySongs"
          :key="song.id"
          :song="song"
          :index="index"
          :current="song.id === currentSongId"
          :playing="isPlaying"
          :expanded="expandedSongId === song.id"
          allow-enqueue
          allow-next
          allow-review
          allow-library-remove
          @toggle="toggleDetails(song.id)"
          @play="playManual(song)"
          @enqueue="emit('enqueue', song)"
          @next="emit('playNext', song)"
          @feedback="(action) => emit('feedback', song, action)"
          @review="emit('review', song)"
          @remove-library="emit('removeLibrary', { kind: 'song', target_id: song.id, name: song.title })"
        />
        <li v-if="libraryHasMore" class="load-more"><button type="button" :disabled="libraryLoading" @click="loadSongs(false)">{{ libraryLoading ? "读取中…" : "加载更多" }}</button></li>
      </ol>
    </template>
  </aside>
</template>

<style scoped>
.online-search-toggle { margin-inline: auto 6px; flex: none; }
.online-search-toggle svg { width: 17px; height: 17px; fill: none; stroke: currentColor; stroke-width: 1.65; }
</style>
