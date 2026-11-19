<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from "vue";
import { ApiError, getQqPlaylists, getQqTracks } from "../../services/api";
import { usePanelFocus } from "../../composables/usePanelFocus";
import type { QqPlaylist, Song } from "../../types/radio";
import PlayerGlass from "../player/PlayerGlass.vue";

const emit = defineEmits<{ close: []; reconnect: []; play: [song: Song, queue: Song[], restart?: boolean]; enqueue: [songs: Song[]] }>();
const panel = ref<HTMLElement | null>(null);
const onKeydown = usePanelFocus(panel, () => emit("close"), true);
const collections = ref<QqPlaylist[]>([]);
const selected = ref<QqPlaylist | null>(null);
const songs = ref<Song[]>([]);
const collectionOffset = ref<number | null>(0);
const trackOffset = ref<number | null>(0);
const total = ref<number | null>(null);
const loading = ref(false);
const error = ref("");
const needsLogin = ref(false);
const incompleteTracks = ref(false);
let revision = 0;

function showError(reason: unknown, fallback: string) {
  needsLogin.value = reason instanceof ApiError && reason.status === 409;
  error.value = reason instanceof Error ? reason.message : fallback;
}

async function loadCollections() {
  const offset = collectionOffset.value;
  if (offset === null || loading.value) return;
  const version = revision;
  loading.value = true;
  error.value = "";
  try {
    const result = await getQqPlaylists(offset);
    if (version !== revision) return;
    const seen = new Set(collections.value.map(item => item.id));
    collections.value.push(...result.playlists.filter(item => !seen.has(item.id)));
    collectionOffset.value = result.next_offset;
    if (result.more && result.next_offset === null) error.value = "QQ 音乐暂时只返回了部分歌单。";
  } catch (reason) {
    if (version === revision) showError(reason, "歌单读取失败");
  } finally {
    if (version === revision) loading.value = false;
  }
}

async function loadTracks(action?: "play" | "enqueue") {
  const collection = selected.value;
  if (!collection || loading.value) return;
  const version = revision;
  loading.value = true;
  error.value = "";
  const seen = new Set(songs.value.map(song => song.id));
  try {
    do {
      const offset = trackOffset.value;
      if (offset === null) break;
      const result = await getQqTracks(collection.id, offset);
      if (version !== revision) return;
      const fresh = result.songs.filter(song => !seen.has(song.id) && seen.add(song.id));
      songs.value.push(...fresh);
      total.value = result.total;
      incompleteTracks.value = result.incomplete || (result.next_offset !== null && (result.next_offset <= offset || !fresh.length));
      trackOffset.value = incompleteTracks.value ? null : result.next_offset;
    } while (action && trackOffset.value !== null);
    if (incompleteTracks.value) {
      error.value = "QQ 音乐暂时只返回了部分歌曲，可播放已显示的歌曲。";
      return;
    }
    const first = songs.value[0];
    if (action === "play" && first) emit("play", first, [...songs.value], true);
    if (action === "enqueue" && first) emit("enqueue", [...songs.value]);
  } catch (reason) {
    if (version === revision) showError(reason, "歌曲读取失败");
  } finally {
    if (version === revision) loading.value = false;
  }
}

function select(collection: QqPlaylist) {
  revision++;
  selected.value = collection;
  songs.value = [];
  total.value = null;
  trackOffset.value = 0;
  loading.value = false;
  incompleteTracks.value = false;
  needsLogin.value = false;
  void loadTracks();
}

function back() {
  revision++;
  selected.value = null;
  loading.value = false;
  error.value = "";
  needsLogin.value = false;
}

onMounted(loadCollections);
onBeforeUnmount(() => { revision++; });
</script>

<template>
  <section class="qr-modal ncm-modal" role="dialog" aria-modal="true" aria-labelledby="qq-playlists-title" @click.self="$emit('close')">
    <div ref="panel" class="radio-panel ncm-card qq-playlists" tabindex="-1" @keydown="onKeydown">
      <PlayerGlass :corner-radius="24" />
      <header>
        <h2 id="qq-playlists-title">{{ selected?.name || 'QQ 音乐歌单' }}</h2>
        <button type="button" aria-label="关闭" @click="$emit('close')">
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m6 6 12 12M18 6 6 18" /></svg>
        </button>
      </header>
      <div class="ncm-modal-content qq-content" :aria-busy="loading">
        <p v-if="!selected" class="ncm-description qq-description">在线播放或加入队列。DJ 从本地曲库选歌。</p>
        <div v-if="selected" class="qq-actions">
          <button type="button" @click="back">返回歌单</button>
          <span v-if="total !== null">{{ total }} 首</span>
          <button type="button" :disabled="loading || !songs.length || incompleteTracks || needsLogin" @click="loadTracks('play')">播放全部</button>
          <button type="button" :disabled="loading || !songs.length || incompleteTracks || needsLogin" @click="loadTracks('enqueue')">整张加入队列</button>
        </div>
        <ul v-if="selected" class="qq-list">
          <li v-for="song in songs" :key="song.id">
            <img v-if="song.cover_url" :src="song.cover_url" alt="" loading="lazy" />
            <button type="button" class="qq-label" :aria-label="`播放 ${song.title}`" @click="$emit('play', song, [...songs])">
              <strong>{{ song.title }}</strong><small>{{ song.artist }}</small>
            </button>
            <button type="button" :aria-label="`将 ${song.title} 加入队列`" @click="$emit('enqueue', [song])">加入队列</button>
          </li>
        </ul>
        <ul v-else class="qq-list">
          <li v-for="collection in collections" :key="collection.id">
            <img v-if="collection.cover_url" :src="collection.cover_url" alt="" loading="lazy" />
            <button type="button" class="qq-label" @click="select(collection)">
              <strong>{{ collection.name }}</strong><small v-if="collection.track_count !== null">{{ collection.track_count }} 首</small>
            </button>
          </li>
        </ul>
        <p v-if="loading" class="qq-message" role="status">{{ selected ? '正在读取歌曲…' : '正在读取歌单…' }}</p>
        <p v-else-if="error" class="qq-message" role="status">{{ error }}</p>
        <p v-else-if="selected && !songs.length" class="qq-message">这张歌单还没有歌曲。</p>
        <button v-if="needsLogin" type="button" class="qq-more" @click="$emit('reconnect')">重新扫码</button>
        <button v-else-if="!loading && (selected ? trackOffset !== null : collectionOffset !== null)" type="button" class="qq-more"
          @click="selected ? loadTracks() : loadCollections()">{{ error ? '重试' : '加载更多' }}</button>
      </div>
    </div>
  </section>
</template>

<style scoped>
.qq-playlists { width: min(620px, 100%); }
.qq-content { max-height: min(62dvh, 620px); min-height: 100px; overflow: auto; }
.qq-description { margin-bottom: 10px; }
.qq-actions { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; padding-bottom: 12px; }
.qq-actions span { margin-right: auto; color: var(--text-muted, #b0bec9); font-size: 12px; }
.qq-list { display: grid; gap: 6px; list-style: none; padding: 0; margin: 0; }
.qq-list li { display: flex; align-items: center; gap: 8px; border: 1px solid #dce6f220; border-radius: 11px; background: #ffffff06; padding: 5px 7px; }
.qq-list img { width: 34px; height: 34px; object-fit: cover; border-radius: 6px; }
.qq-actions button, .qq-list li > button, .qq-more { border: 1px solid #dce6f233; border-radius: 8px; background: #e0eaff0d; color: inherit; padding: 6px 9px; white-space: nowrap; }
.qq-actions button:hover:not(:disabled), .qq-list li > button:hover, .qq-more:hover { background: #e0eaff20; }
.qq-actions button:disabled { opacity: .45; }
.qq-list li > .qq-label { flex: 1; min-width: 0; display: grid; gap: 2px; border-color: transparent; background: transparent; text-align: left; }
.qq-label > * { display: block; overflow: hidden; text-overflow: ellipsis; }
.qq-label small, .qq-message { color: var(--text-muted, #b0bec9); }
.qq-more, .qq-message { margin-top: 10px; }
</style>
