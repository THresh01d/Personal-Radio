<script setup lang="ts">
import type { Song } from "../../types/radio";
import type { LyricLine } from "../../types/radio";
import type { AudioAnalysisStatus, VisualMode } from "../../composables/useAudioAnalysis";
import StageLyrics from "./StageLyrics.vue";
import DepthCoverCarousel from "./DepthCoverCarousel.vue";

defineProps<{
  title: string;
  subtitle: string;
  currentSong: Song | null;
  queue?: Song[];
  canPrevious?: boolean;
  canNext?: boolean;
  progressValue: number;
  elapsed: number;
  isPlaying: boolean;
  isAnnouncing: boolean;
  lyricLines: LyricLine[];
  lyricsInstrumental: boolean;
  visualMode: VisualMode;
  analysisStatus: AudioAnalysisStatus;
  analysisMessage: string;
  constrained: boolean;
}>();
const emit = defineEmits<{ previous: []; next: [] }>();
</script>

<template>
  <section
    class="signboard music-stage depth-stage"
    :class="{ active: isPlaying, announcing: isAnnouncing, empty: !currentSong, 'has-lyrics': lyricLines.length && !isAnnouncing }"
    :style="{ '--stage-progress': `${progressValue}%` }"
    aria-label="中央音乐舞台"
  >
    <div class="stage-copy">
      <div class="stage-body">
        <DepthCoverCarousel :current-song="currentSong" :queue="queue || []" :can-previous="Boolean(canPrevious)" :can-next="Boolean(canNext)"
          :quiet="visualMode === 'quiet' || constrained" @previous="emit('previous')" @next="emit('next')" />
        <div class="stage-heading">
          <h1>{{ title }}</h1>
          <p v-if="subtitle">{{ subtitle }}</p>
        </div>
        <StageLyrics
          v-if="lyricLines.length && !isAnnouncing"
          :lines="lyricLines"
          :elapsed="elapsed"
          :song-id="currentSong?.id"
        />
      </div>
      <div v-if="isPlaying && visualMode !== 'quiet' && (analysisStatus === 'unavailable' || analysisStatus === 'no-signal')" class="stage-footer">
        <span class="stage-analysis-note" :title="analysisMessage">{{ analysisStatus === "unavailable" ? "普通播放 · 无实时频谱" : "暂无频谱信号" }}</span>
      </div>
    </div>
  </section>
</template>

<style scoped>
/* 纵深舞台只重排展示区，真实媒体、频谱和歌词继续复用既有状态。 */
.music-stage.depth-stage {
  --stage-space-width: calc(100vw - 2 * var(--radio-edge, 24px) - var(--radio-stage-reserve, 0px));
  --stage-space-height: calc(100svh - var(--radio-stage-top, 68px) - var(--radio-stage-bottom, 116px));
  --stage-cover-size: clamp(104px, min(20vw, 27svh, var(--stage-space-width) * .48, var(--stage-space-height) - 128px), 280px);
  z-index: var(--radio-stage-layer, 1);
  left: var(--radio-stage-center, 50%);
  top: calc(var(--radio-stage-top, 68px) + var(--stage-space-height) / 2);
  width: min(960px, var(--stage-space-width));
  min-height: 0;
  max-height: var(--stage-space-height);
  padding: clamp(6px, 1.4svh, 18px) clamp(11px, 1.8vw, 24px);
  overflow-y: auto;
  overflow-x: hidden;
  scrollbar-width: none;
  border: 0;
  border-radius: 0;
  background: none;
  backdrop-filter: none;
  box-shadow: none !important;
}
.depth-stage::-webkit-scrollbar{display:none}
.music-stage.depth-stage::before,.music-stage.depth-stage::after{content:none;display:none}
.depth-stage .stage-copy{min-height:0}
.depth-stage .stage-body{display:flex;flex-direction:column;align-items:center;flex:initial}
.depth-stage .stage-heading{width:100%;text-align:center;margin-top:10px}
.depth-stage h1{max-width:100%;font-size:clamp(22px,min(2.5vw,4svh),39px);line-height:1.2;overflow-wrap:anywhere}
.depth-stage p{margin-top:8px;font-size:12px;color:#b6cdc9}
.depth-stage .stage-lyrics{width:100%;min-height:55px;align-items:center;text-align:center;margin:14px 0 3px}
.depth-stage .stage-lyrics.expanded{width:auto;margin:0;min-height:0}
.depth-stage .stage-footer{justify-content:center;margin-top:8px;text-shadow:0 2px 8px #001018}
.depth-stage.empty .stage-heading{margin:0}
@media(max-width:700px){
  .music-stage.depth-stage {
    --stage-cover-size: clamp(90px, min(50vw, 29svh, var(--stage-space-height) - 150px), 196px);
  }
  .depth-stage h1{font-size:clamp(20px,min(6vw,4svh),29px)}
  .depth-stage p{font-size:11px}
  .depth-stage .stage-lyrics{margin-top:10px;min-height:46px}
  .depth-stage .stage-lyrics.expanded{min-height:0;margin:0}
  /* 底部面板上方只留当前封面和歌名，切歌仍可在底栏操作。 */
  .spectral-scene:is(.dj-mode, .playlist-mode) .music-stage.depth-stage {
    --stage-cover-size: clamp(27px, min(16vw, var(--stage-space-height) - 6px), 68px);
    padding-block: 2px;
  }
  .spectral-scene:is(.dj-mode, .playlist-mode) .depth-stage .stage-body {
    flex-direction: row;
    justify-content: center;
    gap: 10px;
  }
  .spectral-scene:is(.dj-mode, .playlist-mode) .depth-stage .stage-heading { width: auto; min-width: 0; margin: 0; }
  .spectral-scene:is(.dj-mode, .playlist-mode) .depth-stage h1 {
    font-size: 17px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .spectral-scene:is(.dj-mode, .playlist-mode) .depth-stage :is(.stage-heading p, .stage-lyrics, .stage-footer) { display: none; }
  .spectral-scene:is(.dj-mode, .playlist-mode) .depth-stage :deep(.depth-carousel) {
    flex: 0 0 var(--stage-cover-size);
    width: var(--stage-cover-size);
    height: var(--stage-cover-size);
  }
  .spectral-scene:is(.dj-mode, .playlist-mode) .depth-stage :deep(.depth-cards) { inset: 0; }
  .spectral-scene:is(.dj-mode, .playlist-mode) .depth-stage :deep(.depth-card.is-current) { top: 50%; }
  .spectral-scene:is(.dj-mode, .playlist-mode) .depth-stage :deep(:is(.depth-card:not(.is-current), .depth-rail, .depth-arrow, .depth-index)) { display: none; }
}
</style>
