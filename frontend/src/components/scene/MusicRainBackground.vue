<script setup lang="ts">
import { ref } from "vue";
import { useMusicRain } from "../../composables/useMusicRain";
import { hasRainSignal, type RainInput } from "./musicRain/signal";

const props = defineProps<RainInput>();
const canvas = ref<HTMLCanvasElement | null>(null);
const { state, resetView } = useMusicRain(canvas, () => props);
</script>

<template>
  <div class="music-rain-background" :data-engine-state="state"
    :data-signal="hasRainSignal(props) ? 'live' : 'rest'" :data-mode="visualMode" :data-weather="weather || 'rain'" aria-hidden="true">
    <div class="rain-horizon"></div>
    <canvas ref="canvas" class="music-rain-canvas" />
    <div class="rain-vignette"></div>
  </div>
  <div v-if="state === 'ready'" class="rain-view-controls" aria-label="雨幕视角控制">
    <button type="button" aria-label="重置雨幕视角" @click="resetView">复位视角</button>
    <span class="rain-view-desktop">拖动旋转 · 滚轮缩放 · 双击复位</span>
    <span class="rain-view-touch">单指旋转 · 双指缩放</span>
  </div>
</template>
