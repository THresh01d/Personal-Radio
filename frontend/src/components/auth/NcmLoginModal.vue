<script setup lang="ts">
import { ref } from "vue";
import PlayerGlass from "../player/PlayerGlass.vue";
import { usePanelFocus } from "../../composables/usePanelFocus";
defineProps<{
  qrImage: string;
  status: string;
  platform?: "qq";
  loading?: boolean;
}>();

const emit = defineEmits<{
  close: [];
  refresh: [];
}>();
const card = ref<HTMLElement | null>(null);
const onPanelKeydown = usePanelFocus(card, () => emit("close"), true);
</script>

<template>
  <section class="qr-modal ncm-modal" role="dialog" aria-modal="true" aria-labelledby="ncm-login-title" @click.self="$emit('close')">
    <div ref="card" class="radio-panel ncm-card ncm-login-card" tabindex="-1" @keydown="onPanelKeydown">
      <PlayerGlass :corner-radius="24" />
      <header>
        <h2 id="ncm-login-title">{{ platform === 'qq' ? 'QQ 音乐扫码登录' : '网易云扫码登录' }}</h2>
        <button type="button" aria-label="关闭" @click="$emit('close')">
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m6 6 12 12M18 6 6 18" /></svg>
        </button>
      </header>
      <div class="ncm-modal-content">
        <p v-if="platform !== 'qq'" class="ncm-description">扫码登录后选择自己的歌单，歌曲元数据和推荐向量只保存在本机。</p>
        <div class="qr-image"><img v-if="qrImage" :src="qrImage" :alt="platform === 'qq' ? 'QQ 音乐登录二维码' : '网易云音乐登录二维码'" /><span v-else>···</span></div>
        <p class="ncm-login-status" role="status">{{ status }}</p>
      </div>
      <footer class="library-actions"><button type="button" :disabled="loading" @click="$emit('refresh')">刷新二维码</button></footer>
    </div>
  </section>
</template>
