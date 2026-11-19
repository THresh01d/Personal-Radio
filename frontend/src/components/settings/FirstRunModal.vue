<script setup lang="ts">
import { ref } from "vue";
import PlayerGlass from "../player/PlayerGlass.vue";
import { usePanelFocus } from "../../composables/usePanelFocus";
import type { SetupStatus } from "../../types/radio";

defineProps<{
  status: SetupStatus;
  qqAvailable?: boolean;
}>();

const emit = defineEmits<{
  close: [];
  login: [];
  qqLogin: [];
  sync: [];
  ai: [];
}>();
const card = ref<HTMLElement | null>(null);
const onPanelKeydown = usePanelFocus(card, () => emit("close"), true);
</script>

<template>
  <section class="qr-modal first-run-modal" role="dialog" aria-modal="true" aria-label="欢迎使用私人电台" @click.self="$emit('close')">
    <div ref="card" class="radio-panel first-run-card" tabindex="-1" @keydown="onPanelKeydown">
      <PlayerGlass :corner-radius="24" />
      <header>
        <h2>欢迎使用私人电台</h2>
        <button type="button" aria-label="稍后配置" @click="$emit('close')">
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m6 6 12 12M18 6 6 18" /></svg>
        </button>
      </header>
      <div class="setup-content">
        <ol class="setup-steps">
          <li>
            <div><strong>网易云音乐</strong><small>导入歌单到本地，供 DJ 推荐</small></div>
            <button v-if="status.netease.credential_present" type="button" @click="$emit('sync')">选择歌单</button>
            <button v-else type="button" @click="$emit('login')">登录网易云</button>
          </li>
          <li v-if="qqAvailable">
            <div><strong>QQ 音乐</strong><small>打开个人歌单，直接选歌播放</small></div>
            <button type="button" @click="$emit('qqLogin')">登录 QQ 音乐</button>
          </li>
          <li>
            <div><strong>AI DJ</strong><small>{{ status.llm.provider === 'offline' ? '离线模式无需 API Key' : '已选择 AI 服务' }}</small></div>
            <button type="button" @click="$emit('ai')">AI 设置</button>
          </li>
        </ol>
      </div>
      <footer><button type="button" @click="$emit('close')">先逛一逛</button></footer>
    </div>
  </section>
</template>

<style scoped>
.first-run-modal {
  background: #05080ed9;
  backdrop-filter: blur(8px);
}

.first-run-card {
  position: relative;
  isolation: isolate;
  display: flex;
  flex-direction: column;
  width: min(550px, 100%);
  max-height: calc(100vh - 40px);
  max-height: calc(100dvh - var(--desktop-caption-height, 0px) - 40px);
  padding: 24px 26px 18px;
  border-radius: 24px;
  color-scheme: dark;
  outline: none;
}

.first-run-card > .player-glass {
  background: linear-gradient(155deg, #1a2230d9, #0a1019eb 65%, #141d28db);
}

.first-run-card > header {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  border-bottom: 1px solid var(--line);
}

.first-run-card > header h2 { margin: 0; letter-spacing: .025em; }
.first-run-card > header button { flex: 0 0 32px; }
.first-run-card > header svg { width: 16px; height: 16px; fill: none; stroke: currentColor; stroke-width: 1.8; stroke-linecap: round; }

.setup-content {
  min-height: 0;
  overflow: auto;
  overscroll-behavior: contain;
  padding: 14px 4px 5px;
  margin-inline: -4px;
}

.setup-steps {
  display: grid;
  gap: 8px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.setup-steps li {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  align-items: center;
  gap: 12px;
  padding: 11px 13px;
  border: 1px solid var(--line);
  border-radius: 12px;
  background: var(--panel-fill);
}

.setup-steps strong, .setup-steps small { display: block; overflow-wrap: anywhere; }
.setup-steps strong { font-size: 14px; font-weight: 600; }
.setup-steps small { margin-top: 4px; color: #b0bac7; font-size: 12px; line-height: 1.45; }

.setup-steps button, footer button {
  min-height: 34px;
  padding: 6px 12px;
  border: 1px solid var(--line-strong);
  border-radius: 999px;
  background: var(--panel-fill);
  color: #d9dfe8;
  font-size: 12px;
  white-space: nowrap;
}

.setup-steps button:not(:disabled):hover, footer button:hover { background: var(--panel-fill-active); }
.setup-steps button:disabled { opacity: .45; cursor: default; }

footer {
  display: flex;
  justify-content: flex-end;
  margin-top: 12px;
  padding-top: 12px;
  border-top: 1px solid var(--line);
}

@media (max-width: 540px) {
  .first-run-card { padding: 16px 16px 13px; }
  .setup-content { padding-top: 10px; }
  .setup-steps { gap: 6px; }
  .setup-steps li { padding: 8px 10px; gap: 8px; }
  .setup-steps small { margin-top: 3px; }
  .setup-steps button { padding-inline: 9px; }
}
</style>
