<script setup lang="ts">
import { nextTick, onBeforeUnmount, reactive, ref, watch } from "vue";
import { getOnlineTracks, getRecommendedPlaylists, searchOnlineCatalog } from "../../services/api";
import { platformNames, searchKinds, type OnlineCollection, type OnlineSearchPage, type OnlineSong, type SearchKind } from "../../types/onlineCatalog";
import type { MusicPlatform, Song } from "../../types/radio";
import OnlineSongList from "./OnlineSongList.vue";

const props = defineProps<{ recommended: boolean; neteaseLoggedIn: boolean; currentSongId?: string; isPlaying: boolean }>();
const emit = defineEmits<{ search: []; login: []; play: [song: Song, queue: Song[]]; enqueue: [song: Song]; next: [song: Song] }>();

type ProviderResults = {
  platform: MusicPlatform; items: OnlineSearchPage["items"]; page: number; hasMore: boolean;
  error: string; request: AbortController | null;
};

const panel = ref<HTMLElement>();
const searchInput = ref<HTMLInputElement>();
const queryDraft = ref("");
const query = ref("");
const kind = ref<SearchKind>("song");
const providers = reactive<ProviderResults[]>((Object.keys(platformNames) as MusicPlatform[]).map(platform => ({
  platform, items: [], page: 0, hasMore: false, error: "", request: null,
})));
const recommendations = ref<OnlineCollection[]>([]);
const recommendationError = ref("");
const recommendationRequest = ref<AbortController | null>(null);
const selected = ref<OnlineCollection | null>(null);
const tracks = ref<Song[]>([]);
const trackPage = ref(0);
const trackHasMore = ref(false);
const trackError = ref("");
const trackRequest = ref<AbortController | null>(null);

function errorText(error: unknown) {
  return error instanceof Error ? error.message : "暂时无法读取，请重试。";
}

function mergeById<T extends { id: string }>(current: T[], next: T[]) {
  const existing = new Set(current.map(item => item.id));
  return [...current, ...next.filter(item => !existing.has(item.id))];
}

function resetSearch() {
  for (const state of providers) {
    state.request?.abort();
    Object.assign(state, { request: null, items: [], page: 0, hasMore: false, error: "" });
  }
}

async function loadSearch(state: ProviderResults) {
  if (state.request || !query.value) return;
  const request = new AbortController();
  const page = state.page + 1;
  state.request = request;
  state.error = "";
  try {
    const result = await searchOnlineCatalog(state.platform, query.value, kind.value, page, request.signal);
    if (request.signal.aborted) return;
    state.items = mergeById(state.items, result.items);
    state.page = page;
    state.hasMore = result.has_more;
  } catch (error) {
    if (!request.signal.aborted) state.error = errorText(error);
  } finally {
    if (!request.signal.aborted) state.request = null;
  }
}

function submitSearch() {
  const text = queryDraft.value.trim();
  if (!text) return;
  closeCollection();
  resetSearch();
  query.value = text;
  if (props.recommended) emit("search");
  // 每个平台独立结束或重试，慢的一侧不会挡住另一侧的结果。
  for (const state of providers) void loadSearch(state);
}

function changeKind(next: SearchKind) {
  if (kind.value === next) return;
  kind.value = next;
  if (!query.value) return;
  queryDraft.value = query.value;
  submitSearch();
}

async function loadRecommendations() {
  recommendationRequest.value?.abort();
  recommendationRequest.value = null;
  recommendations.value = [];
  recommendationError.value = "";
  if (!props.recommended || !props.neteaseLoggedIn) return;
  const request = new AbortController();
  recommendationRequest.value = request;
  try {
    const result = await getRecommendedPlaylists(request.signal);
    if (!request.signal.aborted) recommendations.value = result.playlists;
  } catch (error) {
    if (!request.signal.aborted) recommendationError.value = errorText(error);
  } finally {
    if (!request.signal.aborted) recommendationRequest.value = null;
  }
}

function closeCollection() {
  trackRequest.value?.abort();
  trackRequest.value = null;
  selected.value = null;
  tracks.value = [];
  trackPage.value = 0;
  trackHasMore.value = false;
  trackError.value = "";
  if (panel.value) panel.value.scrollTop = 0;
}

function openCollection(item: OnlineCollection) {
  closeCollection();
  selected.value = item;
  void loadTracks();
}

async function loadTracks() {
  if (!selected.value || trackRequest.value) return;
  const request = new AbortController();
  const page = trackPage.value + 1;
  trackRequest.value = request;
  trackError.value = "";
  try {
    const result = await getOnlineTracks(selected.value, page, request.signal);
    if (request.signal.aborted) return;
    tracks.value = mergeById(tracks.value, result.songs);
    trackPage.value = page;
    trackHasMore.value = result.has_more;
  } catch (error) {
    if (!request.signal.aborted) trackError.value = errorText(error);
  } finally {
    if (!request.signal.aborted) trackRequest.value = null;
  }
}

watch(() => props.recommended, (recommended) => {
  closeCollection();
  if (recommended) { query.value = ""; resetSearch(); }
  else if (!query.value) void nextTick(() => searchInput.value?.focus());
  void loadRecommendations();
}, { immediate: true });
watch(() => props.neteaseLoggedIn, () => {
  if (!props.recommended) return;
  closeCollection();
  void loadRecommendations();
});
onBeforeUnmount(() => {
  resetSearch();
  recommendationRequest.value?.abort();
  trackRequest.value?.abort();
});
</script>

<template>
  <div ref="panel" class="playlist-browser online-catalog">
    <template v-if="selected">
      <div class="library-toolbar online-detail-title">
        <button type="button" class="back-button" @click="closeCollection">← 返回{{ recommended ? '推荐歌单' : '搜索结果' }}</button>
        <h3>{{ selected.title }}</h3>
        <small>{{ platformNames[selected.platform] }}<template v-if="selected.artist"> · {{ selected.artist }}</template></small>
      </div>
      <OnlineSongList :songs="tracks" :current-song-id="currentSongId" :is-playing="isPlaying"
        @play="(song, queue) => $emit('play', song, queue)" @enqueue="$emit('enqueue', $event)" @next="$emit('next', $event)" />
      <p v-if="trackRequest" class="online-status" role="status">正在读取曲目…</p>
      <p v-else-if="trackError" class="online-status" role="status">{{ trackError }} <button type="button" @click="loadTracks">重试</button></p>
      <p v-else-if="!tracks.length" class="online-status">没有可展示的曲目。</p>
      <button v-if="trackHasMore && !trackError" type="button" class="playlist-more" :disabled="Boolean(trackRequest)" @click="loadTracks">加载更多曲目</button>
    </template>
    <template v-else>
      <div class="library-toolbar online-search">
        <form role="search" aria-label="全平台搜索" @submit.prevent="submitSearch">
          <input ref="searchInput" v-model="queryDraft" type="search" maxlength="60" placeholder="搜索歌名、歌手或专辑" aria-label="搜索歌名、歌手或专辑" />
          <button type="submit" :disabled="!queryDraft.trim()">搜索</button>
        </form>
        <div v-if="!recommended" class="library-play-actions" role="group" aria-label="搜索类型">
          <button v-for="(label, value) in searchKinds" :key="value" type="button" :aria-pressed="kind === value" @click="changeKind(value)">{{ label }}</button>
        </div>
      </div>
      <template v-if="recommended">
        <p v-if="!neteaseLoggedIn" class="online-status">登录网易云音乐后可查看首页推荐歌单。 <button type="button" @click="$emit('login')">登录网易云</button></p>
        <p v-else-if="recommendationRequest" class="online-status" role="status">正在读取网易云首页推荐…</p>
        <p v-else-if="recommendationError" class="online-status" role="status">{{ recommendationError }} <button type="button" @click="loadRecommendations">重试</button></p>
        <p v-else-if="!recommendations.length" class="online-status">网易云首页暂未返回推荐歌单。 <button type="button" @click="loadRecommendations">刷新</button></p>
        <template v-else>
          <div class="playlist-overview"><p>网易云首页推荐</p><button type="button" @click="loadRecommendations">刷新</button></div>
          <ul class="online-collections" aria-label="网易云推荐歌单">
            <li v-for="item in recommendations" :key="item.id">
              <button type="button" @click="openCollection(item)">
                <img v-if="item.cover_url" :src="item.cover_url" alt="" loading="lazy" />
                <span v-else class="online-art" aria-hidden="true">♪</span>
                <strong>{{ item.title }}</strong>
              </button>
            </li>
          </ul>
        </template>
      </template>
      <template v-else-if="query">
        <section v-for="state in providers" :key="state.platform" :aria-label="`${platformNames[state.platform]}搜索结果`" class="online-results">
          <h3>{{ platformNames[state.platform] }}</h3>
          <OnlineSongList v-if="kind === 'song'" :songs="state.items as OnlineSong[]" :current-song-id="currentSongId" :is-playing="isPlaying"
            @play="(song, queue) => $emit('play', song, queue)" @enqueue="$emit('enqueue', $event)" @next="$emit('next', $event)" />
          <ul v-else-if="state.items.length" class="online-collections">
            <li v-for="item in (state.items as OnlineCollection[])" :key="item.id">
              <button type="button" @click="openCollection(item)">
                <img v-if="item.cover_url" :src="item.cover_url" alt="" loading="lazy" />
                <span v-else class="online-art" aria-hidden="true">♪</span>
                <span class="online-copy"><strong>{{ item.title }}</strong><small v-if="item.artist">{{ item.artist }}</small></span>
              </button>
            </li>
          </ul>
          <p v-if="state.request" class="online-status" role="status">搜索中…</p>
          <p v-else-if="state.error" class="online-status" role="status">{{ state.error }} <button type="button" @click="loadSearch(state)">重试</button></p>
          <p v-else-if="!state.items.length" class="online-status">未找到匹配结果。</p>
          <button v-if="state.hasMore && !state.error" type="button" class="playlist-more" :disabled="Boolean(state.request)" @click="loadSearch(state)">加载更多</button>
        </section>
      </template>
      <p v-else class="online-status">同时搜索网易云音乐和 QQ 音乐。</p>
    </template>
  </div>
</template>

<style scoped>
.online-catalog { min-height: 0; }
.online-search { padding-bottom: 9px; }
.online-search .library-play-actions { grid-template-columns: repeat(3, minmax(0, 1fr)); }
.online-search button[aria-pressed="true"] { color: #edf2f8; border-color: #fff5; background: #fff2; }
.online-results { border-top: 1px solid #fff2; padding: 8px 0; }
.online-results h3, .online-detail-title h3 { margin: 4px 0; font-size: 14px; overflow-wrap: anywhere; }
.online-detail-title { display: grid; gap: 6px; }
.online-detail-title .back-button { justify-self: start; }
.online-status { margin: 10px 0; color: #bec6d2; font-size: 12px; overflow-wrap: anywhere; }
.online-status button { padding: 4px 7px; border: 1px solid #fff3; border-radius: 7px; color: #e5edf6; background: #fff1; }
.online-collections { display: grid; gap: 6px; list-style: none; padding: 4px 0; margin: 0; }
.online-collections button { display: flex; align-items: center; gap: 9px; width: 100%; min-height: 52px; padding: 5px; text-align: left; color: #e2e9f2; background: #ffffff07; border: 1px solid #ffffff13; border-radius: 8px; }
.online-collections button:hover { background: #ffffff14; }
.online-collections :is(img, .online-art) { width: 38px; height: 38px; flex: none; object-fit: cover; border-radius: 5px; }
.online-art { display: grid; place-items: center; background: #ffffff12; }
.online-collections strong { font-size: 12px; overflow-wrap: anywhere; }
.online-copy { display: grid; gap: 4px; min-width: 0; }
.online-copy small { color: #adb7c6; font-size: 11px; overflow-wrap: anywhere; }
</style>
