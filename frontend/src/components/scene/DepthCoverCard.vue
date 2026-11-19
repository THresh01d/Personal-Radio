<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from "vue";
import { getSongCover } from "../../services/api";
import type { Song } from "../../types/radio";

const props = defineProps<{ song: Song }>();
const coverUrl = ref("");
const loaded = ref(false);
let revision = 0;

watch(() => [props.song.id, props.song.cover_url], async () => {
  const token = ++revision;
  loaded.value = false;
  coverUrl.value = props.song.cover_url || "";
  if (coverUrl.value) return;
  const resolved = await getSongCover(props.song.id);
  // 封面请求不能在卡片离场或作品变化后覆盖另一首歌。
  if (token === revision) coverUrl.value = resolved;
}, { immediate: true });
onBeforeUnmount(() => { revision++; });
</script>

<template>
  <div class="depth-art" :class="{ 'art-loaded': loaded }">
    <div class="depth-art-fallback" aria-hidden="true">
      <i>♫</i><strong>{{ song.title }}</strong><small>{{ song.artist }}</small>
    </div>
    <img v-if="coverUrl" :key="coverUrl" :src="coverUrl" :alt="`${song.title}的专辑封面`" draggable="false"
      @load="loaded = true" @error="coverUrl = ''; loaded = false" />
  </div>
</template>

<style scoped>
.depth-art{position:relative;width:100%;height:100%;overflow:hidden;border-radius:4px;background:transparent}
.depth-art img{position:absolute;inset:0;width:100%;height:100%;object-fit:cover;border-radius:4px;opacity:0;transition:opacity .25s}
.depth-art.art-loaded img{opacity:1}
.depth-art-fallback{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;flex-direction:column;padding:18px;border-radius:4px;background:radial-gradient(circle at 75% 25%,#a77351 0,transparent 45%),linear-gradient(145deg,#36575c,#101f30);text-align:center}
.depth-art-fallback i{margin:15px 0;color:#c3ddce;font:42px serif}
.depth-art-fallback strong{display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;max-width:100%;color:#fff4df;font-size:16px;overflow-wrap:anywhere}
.depth-art-fallback small{margin-top:7px;color:#c6d5cf;font-size:10px}
@media(prefers-reduced-motion:reduce){.depth-art img{transition:none}}
</style>
