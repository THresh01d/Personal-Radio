<script setup lang="ts">
import { computed, ref, watch } from "vue";
import type { LyricLine } from "../../types/radio";

const props = defineProps<{
  lines: LyricLine[];
  elapsed: number;
  songId?: string;
}>();

const expanded = ref(false);
let touchStartedOutside = false;

function checkTouch(event: PointerEvent) {
  const button = event.currentTarget as HTMLButtonElement;
  // 触屏浏览器会把近处的空白吸附到按钮，按落点重新确认。
  touchStartedOutside = !expanded.value && event.pointerType === "touch"
    && !button.contains(button.ownerDocument.elementFromPoint(event.clientX, event.clientY));
}

function toggleLyrics(event: MouseEvent) {
  if (!touchStartedOutside || event.detail === 0) expanded.value = !expanded.value;
  touchStartedOutside = false;
}

const activeIndex = computed(() => {
  let low = 0;
  let high = props.lines.length - 1;
  let match = -1;
  // 二分查找让长歌词也只做少量比较；提前 0.12 秒切换，补偿界面刷新延迟。
  const position = Math.max(0, props.elapsed + 0.12);
  while (low <= high) {
    const middle = Math.floor((low + high) / 2);
    if ((props.lines[middle]?.time || 0) <= position) {
      match = middle;
      low = middle + 1;
    } else high = middle - 1;
  }
  return match;
});

const primaryIndex = computed(() => Math.max(0, activeIndex.value));
const currentLine = computed(() => props.lines[primaryIndex.value]);
const nextLine = computed(() => props.lines[primaryIndex.value + 1]);
const expandedLines = computed(() => {
  const start = Math.max(0, primaryIndex.value - 2);
  return props.lines.slice(start, primaryIndex.value + 3).map((line, offset) => ({
    ...line,
    index: start + offset,
  }));
});

watch(() => props.songId, () => { expanded.value = false; });
watch(() => props.lines.length, (length) => {
  if (!length) expanded.value = false;
});
</script>

<template>
  <button
    v-if="lines.length"
    class="stage-lyrics"
    :class="{ expanded }"
    type="button"
    :aria-label="expanded ? '收起沉浸歌词' : '展开沉浸歌词'"
    :aria-expanded="expanded"
    @pointerdown="checkTouch"
    @click="toggleLyrics"
  >
    <template v-if="!expanded">
      <Transition name="lyric-shift" mode="out-in">
        <span :key="primaryIndex" class="lyric-primary">
          <span class="lyric-current" :class="{ upcoming: activeIndex < 0 }">{{ currentLine?.text }}</span>
          <small v-if="currentLine?.translation" class="lyric-translation">{{ currentLine.translation }}</small>
        </span>
      </Transition>
      <span v-if="nextLine" class="lyric-next">{{ nextLine.text }}</span>
    </template>
    <span
      v-for="line in expandedLines"
      v-else
      :key="`${line.time}-${line.index}`"
      class="lyric-context"
      :class="{ active: line.index === primaryIndex }"
    >
      <b>{{ line.text }}</b>
      <small v-if="line.translation">{{ line.translation }}</small>
    </span>
  </button>
</template>
