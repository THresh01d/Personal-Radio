<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { getSongCover } from "../../services/api";
import type { FeedbackAction, Song } from "../../types/radio";

const props = defineProps<{
  song: Song;
  index: number;
  current: boolean;
  playing: boolean;
  expanded: boolean;
  allowEnqueue?: boolean;
  allowNext?: boolean;
  manageQueue?: boolean;
  canMoveUp?: boolean;
  canMoveDown?: boolean;
  allowReplace?: boolean;
  allowReview?: boolean;
  allowLibraryRemove?: boolean;
}>();

defineEmits<{
  toggle: [];
  play: [];
  feedback: [action: FeedbackAction];
  enqueue: [];
  next: [];
  remove: [];
  move: [step: -1 | 1];
  replace: [];
  review: [];
  removeLibrary: [];
}>();

const coverUrl = ref(props.song.cover_url || "");
const actionsOpen = ref(false);
const canGiveFeedback = computed(() => props.song.platform !== "qq" && !props.song.online_catalog);

watch(() => props.song.id, async (songId) => {
  coverUrl.value = props.song.cover_url || "";
  if (coverUrl.value) return;
  const resolved = await getSongCover(songId);
  if (props.song.id === songId) coverUrl.value = resolved;
}, { immediate: true });

function artworkStyle() {
  let hash = 0;
  for (const char of `${props.song.title}${props.song.artist}`) hash = ((hash << 5) - hash + char.charCodeAt(0)) | 0;
  return { "--art-hue": `${Math.abs(hash) % 360}` };
}

function monogram() {
  return props.song.title.trim().slice(0, 1).toLocaleUpperCase() || "♪";
}

function trackTime() {
  const seconds = props.song.duration_seconds;
  if (!seconds || !Number.isFinite(seconds) || seconds < 0) return "";
  return `${Math.floor(seconds / 60)}:${String(Math.floor(seconds % 60)).padStart(2, "0")}`;
}
</script>

<template>
  <li :class="{ current, expanded, 'actions-open': actionsOpen }" :aria-current="current ? 'true' : undefined" :style="{ '--entry-delay': `${Math.min(index % 50, 8) * 22}ms` }">
    <span class="track-number"><span v-if="current && playing" class="track-live" aria-label="正在播放"><i></i><i></i><i></i></span><template v-else>{{ String(index + 1).padStart(2, "0") }}</template></span>
    <div class="song-art" :class="{ 'has-cover': coverUrl }" :style="artworkStyle()" aria-hidden="true">
      <img v-if="coverUrl" :src="coverUrl" alt="" loading="lazy" @error="coverUrl = ''" />
      <template v-else><span>{{ monogram() }}</span><i></i></template>
    </div>
    <button class="song-copy" type="button" :aria-expanded="expanded" @click="$emit('toggle')">
      <strong>{{ song.title }}</strong>
      <small class="song-meta">{{ song.artist }}<template v-if="song.album"> · {{ song.album }}</template><template v-if="song.platform === 'qq' && !song.online_catalog"> · QQ 音乐</template></small>
      <small v-if="song.online_catalog" class="song-meta">{{ song.platform === 'qq' ? 'QQ 音乐' : '网易云音乐' }}</small>
      <em>{{ song.dj_intro || song.reason }}</em>
      <mark v-if="song.genre?.length || song.mood?.length">{{ [...(song.genre || []), ...(song.mood || [])].slice(0, 3).join(" · ") }}</mark>
      <small v-if="song.dj_intro || song.reason" class="detail-hint">{{ expanded ? "收起 DJ 推荐理由 ↑" : "展开 DJ 推荐理由 ↓" }}</small>
    </button>
    <time v-if="trackTime()" class="track-duration">{{ trackTime() }}</time>
    <div class="song-tools">
      <button class="track-play" type="button" :aria-label="`${current && playing ? '暂停' : '播放'} ${song.title}`" :title="`${current && playing ? '暂停' : '播放'} ${song.title}`" @click="$emit('play')">
        <svg v-if="current && playing" viewBox="0 0 24 24" aria-hidden="true"><path d="M9 7v10M15 7v10" /></svg>
        <svg v-else viewBox="0 0 24 24" aria-hidden="true"><path d="m9 7 8 5-8 5Z" /></svg>
      </button>
      <button type="button" :aria-label="`${allowReview && canGiveFeedback ? '更多操作' : '队列操作'} ${song.title}`" :aria-expanded="actionsOpen" @click="actionsOpen = !actionsOpen">•••</button>
    </div>
    <div v-if="actionsOpen" class="queue-edit" :aria-label="`${song.title}的队列操作`">
      <button v-if="allowEnqueue" type="button" @click="$emit('enqueue'); actionsOpen = false">加入当前队列</button>
      <button v-if="canGiveFeedback" type="button" @click="$emit('feedback', 'like'); actionsOpen = false">喜欢</button>
      <button v-if="song.batch_id" type="button" title="当前任务后续选歌避开；不改变长期偏好或当前播放" @click="$emit('feedback', 'not_now'); actionsOpen = false">这次不合适</button>
      <template v-if="song.batch_id && song.reference_song_id && song.reference_title">
        <button type="button" @click="$emit('feedback', 'reference_like'); actionsOpen = false">像《{{ song.reference_title }}》</button>
        <button type="button" @click="$emit('feedback', 'reference_unlike'); actionsOpen = false">不像《{{ song.reference_title }}》</button>
      </template>
      <button v-if="canGiveFeedback" type="button" title="长期减少这首歌的推荐" @click="$emit('feedback', 'dislike'); actionsOpen = false">减少推荐</button>
      <button v-if="allowReview && canGiveFeedback" type="button" @click="$emit('review')">查看／纠正风格</button>
      <button v-if="allowLibraryRemove" type="button" @click="$emit('removeLibrary'); actionsOpen = false">从本地曲库删除</button>
      <button v-if="allowNext" type="button" :disabled="current" @click="$emit('next'); actionsOpen = false">下一首播放</button>
      <button v-if="allowReplace" type="button" title="仅调整这一轮，不保存长期不喜欢" @click="$emit('replace'); actionsOpen = false">只换这一首</button>
      <template v-if="manageQueue">
        <button type="button" :disabled="!canMoveUp" @click="$emit('move', -1)">上移</button>
        <button type="button" :disabled="!canMoveDown" @click="$emit('move', 1)">下移</button>
        <button type="button" @click="$emit('remove')">移出队列</button>
      </template>
    </div>
  </li>
</template>
