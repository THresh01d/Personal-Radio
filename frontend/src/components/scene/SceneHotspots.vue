<script setup lang="ts">
defineProps<{ songCount: number }>();
defineEmits<{ chat: []; playlist: [] }>();
</script>

<template>
  <button class="coffee-neon" type="button" aria-label="打开播放列表" @click="$emit('playlist')">
    <span class="edge-kicker">QUEUE</span>
    <span class="steam one"></span><span class="steam two"></span><span class="steam three"></span>
    <span class="cup">◡</span><strong>今晚歌单</strong><small>{{ songCount || "—" }} 首 · 点击展开</small>
  </button>

  <button class="menu-board" type="button" @click="$emit('chat')">
    <span>AI DJ · ON DUTY</span><strong>今晚，想听<br />什么心情？</strong><em>点击和 DJ 说说 →</em>
  </button>
</template>
