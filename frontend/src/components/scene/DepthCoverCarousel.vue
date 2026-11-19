<script setup lang="ts">
import { computed } from "vue";
import type { Song } from "../../types/radio";
import DepthCoverCard from "./DepthCoverCard.vue";

const props = defineProps<{
  currentSong: Song | null;
  queue: Song[];
  canPrevious: boolean;
  canNext: boolean;
  quiet: boolean;
}>();
const emit = defineEmits<{ previous: []; next: [] }>();
const index = computed(() => props.queue.findIndex(song => song.id === props.currentSong?.id));
const cards = computed(() => {
  if (!props.currentSong) return [];
  if (index.value < 0) return [{ song: props.currentSong, offset: 0 }];
  // 只给相邻卡片取封面，长歌单也不会创建成千上万个图片节点。
  const start = Math.max(0, index.value - 2);
  return props.queue.slice(start, index.value + 3).map((song, i) => ({ song, offset: start + i - index.value }));
});

function position(offset: number) {
  const distance = Math.abs(offset);
  const depth = distance * -165;
  return {
    transform: `translate(-50%, -50%) translate3d(calc(var(--cover-spread) * ${offset}), ${distance * 3}px, ${depth}px) rotateY(${-Math.sign(offset) * 24}deg)`,
    zIndex: 10 - distance,
    opacity: 1 - distance * .18,
    '--cover-dim': offset === 0 ? '0' : String(.1 + distance * .12),
  };
}

function onKey(event: KeyboardEvent) {
  if (event.altKey || event.ctrlKey || event.metaKey || !["ArrowLeft", "ArrowRight"].includes(event.key)) return;
  event.preventDefault();
  if (event.key === "ArrowLeft" && props.canPrevious) emit("previous");
  if (event.key === "ArrowRight" && props.canNext) emit("next");
}
</script>

<template>
  <section v-if="currentSong" class="depth-carousel" :class="{ 'depth-quiet': quiet }" aria-label="歌曲封面与切歌" @keydown="onKey">
    <div class="depth-rail" aria-hidden="true"></div>
    <TransitionGroup name="depth-card" tag="div" class="depth-cards">
      <figure v-for="card in cards" :key="card.song.id" class="depth-card" :class="{ 'is-current': card.offset === 0 }"
        :style="position(card.offset)" :aria-hidden="card.offset !== 0 ? true : undefined" :data-song-id="card.song.id" :data-offset="card.offset">
        <DepthCoverCard :song="card.song" />
      </figure>
    </TransitionGroup>
    <button class="depth-arrow depth-previous" type="button" aria-label="上一首" :disabled="!canPrevious" @click="emit('previous')">
      <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m14 6-6 6 6 6M8 12h12" /></svg>
    </button>
    <button class="depth-arrow depth-next" type="button" aria-label="下一首" :disabled="!canNext" @click="emit('next')">
      <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m10 6 6 6-6 6M16 12H4" /></svg>
    </button>
    <div class="depth-index" aria-hidden="true"><span>{{ index < 0 ? '01' : String(index + 1).padStart(2, '0') }}</span><i></i>{{ String(Math.max(1, queue.length)).padStart(2, '0') }}</div>
  </section>
</template>

<style scoped>
.depth-carousel {
  --cover-size: var(--stage-cover-size, 280px);
  --cover-spread: calc(var(--cover-size) * .69);
  position: relative;
  width: 100%;
  height: calc(var(--cover-size) + clamp(20px, 3svh, 32px));
  isolation: isolate;
  outline: none;
}
.depth-cards{position:absolute;inset:0;perspective:1000px;perspective-origin:50% 42%;overflow:hidden}
.depth-card{position:absolute;left:50%;top:46%;width:var(--cover-size);aspect-ratio:1;margin:0;pointer-events:none;will-change:transform;transition:transform .65s cubic-bezier(.22,.8,.22,1),opacity .5s ease;filter:drop-shadow(0 22px 18px rgba(0,0,0,.48));backface-visibility:hidden}
.depth-card::after{content:"";position:absolute;inset:0;background:rgba(1,9,17,var(--cover-dim));border-radius:4px;transition:background .5s;pointer-events:none}
.depth-rail{position:absolute;left:10%;right:10%;bottom:36px;height:45px;border-radius:50%;background:radial-gradient(ellipse,rgba(240,170,115,.1),rgba(100,209,205,.035) 45%,transparent 72%);filter:blur(9px)}
.depth-arrow{position:absolute;z-index:20;top:calc(46% - 21px);display:grid;place-items:center;width:42px;height:42px;padding:9px;border:1px solid rgba(206,231,225,.3);border-radius:50%;background:rgba(3,18,27,.78);color:#e4eee9;backdrop-filter:blur(12px);box-shadow:0 5px 20px #0003;transition:background .2s,border-color .2s,opacity .2s}
.depth-arrow svg{width:22px;fill:none;stroke:currentColor;stroke-width:1.5;stroke-linecap:round;stroke-linejoin:round}
.depth-previous{left:0}.depth-next{right:0}
.depth-arrow:hover:not(:disabled){background:rgba(55,105,103,.8);border-color:#a6dfd0}
.depth-arrow:focus-visible{outline:2px solid #b5f6e3;outline-offset:5px}
.depth-arrow:disabled{opacity:.22}
.depth-index{position:absolute;bottom:5px;left:50%;display:flex;align-items:center;gap:11px;transform:translateX(-50%);color:#849b9e;font:9px ui-monospace,monospace;letter-spacing:.13em}
.depth-index span{color:#e9d6bc}.depth-index i{width:28px;height:1px;background:#92b2ad55}
.depth-card-enter-from,.depth-card-leave-to{opacity:0!important}
.depth-card-leave-active{position:absolute}
.depth-quiet .depth-card{transition-duration:.15s;will-change:auto}
@media(max-width:700px){
  .depth-carousel{--cover-size:var(--stage-cover-size,196px);--cover-spread:calc(var(--cover-size) * .56)}
  .depth-cards{left:-18px;right:-18px;perspective:750px}
  .depth-arrow{width:36px;height:36px;padding:7px;top:calc(46% - 18px)}
  .depth-previous{left:-7px}.depth-next{right:-7px}
  .depth-index{bottom:0}
}
@media(prefers-reduced-motion:reduce){.depth-card,.depth-arrow{transition:none;will-change:auto}}
</style>
