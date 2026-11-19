<script setup lang="ts">
import { ref } from "vue";
import { useMusicRain } from "../../composables/useMusicRain";
import { hasRainSignal, type RainInput } from "./musicRain/signal";

const props = defineProps<RainInput>();
const canvas = ref<HTMLCanvasElement | null>(null);
const { state } = useMusicRain(canvas, () => props);
</script>

<template>
  <div class="music-rain-background" :data-engine-state="state"
    :data-signal="hasRainSignal(props) ? 'live' : 'rest'" :data-mode="visualMode" :data-weather="weather || 'rain'" aria-hidden="true">
    <div class="rain-horizon"></div>
    <canvas ref="canvas" class="music-rain-canvas" />
    <div class="rain-vignette"></div>
  </div>
</template>
