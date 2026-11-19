<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, useId } from "vue";

// 播放条和侧面板共用同一光路，仅背景折射，文字/按钮保持清晰。
// 默认圆角保留播放条原效果；侧面板单独指定，避免高面板的光学圆角不贴合外框。
const props = withDefaults(defineProps<{ cornerRadius?: number; refract?: boolean }>(), { cornerRadius: 38, refract: true });
const surface = ref<HTMLElement | null>(null);
const size = ref({ width: 400, height: 80 });
const refractive = ref(false);
const filterId = `player-glass-${useId().replace(/[^a-zA-Z0-9_-]/g, "")}`;
let observer: ResizeObserver | undefined;
let alive = true;

const displacementMap = computed(() => {
  const { width, height } = size.value;
  const edge = Math.min(width, height) * .035;
  const radius = Math.min(height / 2, props.cornerRadius);
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}">
    <defs><linearGradient id="x" x1="100%" x2="0%"><stop stop-color="#0000"/><stop offset="1" stop-color="red"/></linearGradient>
    <linearGradient id="y" x2="0%" y2="100%"><stop stop-color="#0000"/><stop offset="1" stop-color="blue"/></linearGradient>
    <filter id="soft"><feGaussianBlur stdDeviation="11"/></filter></defs>
    <rect width="100%" height="100%" fill="black"/>
    <rect width="100%" height="100%" rx="${radius}" fill="url(#x)"/>
    <rect width="100%" height="100%" rx="${radius}" fill="url(#y)" style="mix-blend-mode:difference"/>
    <rect x="${edge}" y="${edge}" width="${width - edge * 2}" height="${height - edge * 2}" rx="${radius}" fill="rgb(128 128 128 / .93)" filter="url(#soft)"/>
  </svg>`;
  return `data:image/svg+xml,${encodeURIComponent(svg)}`;
});

onMounted(() => {
  if (!props.refract) return;
  // Safari/Firefox不把声明支持当作真实SVG背景折射能力，保留可读的磨砂回退。
  refractive.value = /Chrome|Chromium|Edg\//.test(navigator.userAgent)
    && CSS.supports("backdrop-filter", `url(#${filterId})`);
  if (typeof ResizeObserver === "undefined") return;
  observer = new ResizeObserver(entries => {
    const rect = entries[0]?.contentRect;
    if (!alive || !rect || !Number.isFinite(rect.width) || !Number.isFinite(rect.height) || rect.width <= 0 || rect.height <= 0) return;
    const next = { width: Math.round(rect.width), height: Math.round(rect.height) };
    if (next.width !== size.value.width || next.height !== size.value.height) size.value = next;
  });
  if (surface.value) observer.observe(surface.value);
});
onBeforeUnmount(() => { alive = false; observer?.disconnect(); });
</script>

<template>
  <div ref="surface" class="player-glass" :class="{ refractive }" aria-hidden="true" :style="{ '--player-glass-filter': `url(#${filterId})` }">
    <svg v-if="refract" class="player-glass-filter" xmlns="http://www.w3.org/2000/svg" focusable="false">
      <defs>
        <filter :id="filterId" x="0%" y="0%" width="100%" height="100%" color-interpolation-filters="sRGB">
          <feImage :href="displacementMap" width="100%" height="100%" preserveAspectRatio="none" result="map" />
          <feDisplacementMap in="SourceGraphic" in2="map" scale="-180" xChannelSelector="R" yChannelSelector="G" result="dispRed" />
          <feColorMatrix in="dispRed" type="matrix" values="1 0 0 0 0  0 0 0 0 0  0 0 0 0 0  0 0 0 1 0" result="red" />
          <feDisplacementMap in="SourceGraphic" in2="map" scale="-180" xChannelSelector="R" yChannelSelector="G" result="dispGreen" />
          <feColorMatrix in="dispGreen" type="matrix" values="0 0 0 0 0  0 1 0 0 0  0 0 0 0 0  0 0 0 1 0" result="green" />
          <feDisplacementMap in="SourceGraphic" in2="map" scale="-180" xChannelSelector="R" yChannelSelector="G" result="dispBlue" />
          <feColorMatrix in="dispBlue" type="matrix" values="0 0 0 0 0  0 0 0 0 0  0 0 1 0 0  0 0 0 1 0" result="blue" />
          <feBlend in="red" in2="green" mode="screen" result="rg" />
          <feBlend in="rg" in2="blue" mode="screen" result="glass" />
          <feGaussianBlur in="glass" stdDeviation="0.5" />
        </filter>
      </defs>
    </svg>
    <span class="player-glass-optics"></span>
    <span class="player-glass-rim"></span>
  </div>
</template>

<style scoped>
.player-glass{position:absolute;z-index:-1;inset:0;pointer-events:none;border-radius:inherit;overflow:hidden;background:transparent;box-shadow:inset 0 1px 0 #ffffff26,inset 0 -1px 0 #ffffff12,0 8px 28px #0002}
.player-glass-filter{position:absolute;width:100%;height:100%;opacity:0;pointer-events:none}
.player-glass-optics,.player-glass-rim{position:absolute;inset:0;border-radius:inherit;pointer-events:none}
.player-glass-optics{background:transparent;-webkit-backdrop-filter:blur(2px);backdrop-filter:blur(2px)}
.refractive .player-glass-optics{backdrop-filter:var(--player-glass-filter);box-shadow:inset 0 0 2px 1px #ffffff26}
.player-glass-rim{padding:1px;background:linear-gradient(150deg,#ffffff66,#ffffff10 40%,#ffffff08 62%,#ffffff40);-webkit-mask:linear-gradient(#000 0 0) content-box,linear-gradient(#000 0 0);-webkit-mask-composite:xor;mask:linear-gradient(#000 0 0) content-box exclude,linear-gradient(#000 0 0)}
</style>
