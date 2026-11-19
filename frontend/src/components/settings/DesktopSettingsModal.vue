<script setup lang="ts">
import { onMounted, ref } from "vue";
import { usePanelFocus } from "../../composables/usePanelFocus";
import PlayerGlass from "../player/PlayerGlass.vue";

const props = defineProps<{
  checkForUpdates: () => Promise<void>;
  getVersion: () => Promise<string>;
}>();
const emit = defineEmits<{ close: []; ai: []; taste: []; playback: [] }>();
const panel = ref<HTMLElement | null>(null);
const version = ref("");
const checking = ref(false);
const error = ref("");
const onPanelKeydown = usePanelFocus(panel, () => emit("close"), true);

onMounted(async () => {
  try { version.value = await props.getVersion(); }
  catch { version.value = ""; }
});

async function check() {
  if (checking.value) return;
  checking.value = true;
  error.value = "";
  try { await props.checkForUpdates(); }
  catch { error.value = "暂时无法检查更新，请稍后重试。"; }
  finally { checking.value = false; }
}
</script>

<template>
  <section class="qr-modal glass-dialog desktop-settings-modal" role="dialog" aria-modal="true" aria-labelledby="desktop-settings-title" @click.self="$emit('close')">
    <div ref="panel" class="radio-panel glass-dialog-card desktop-settings-card" tabindex="-1" @keydown="onPanelKeydown">
      <PlayerGlass :corner-radius="24" :refract="false" />
      <header>
        <h2 id="desktop-settings-title">总设置</h2>
        <button type="button" aria-label="关闭总设置" @click="$emit('close')">
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m6 6 12 12M18 6 6 18" /></svg>
        </button>
      </header>
      <div class="settings-links glass-dialog-content">
        <button type="button" @click="$emit('ai')"><span>AI 设置</span><span aria-hidden="true">›</span></button>
        <button type="button" @click="$emit('taste')"><span>DJ 记忆</span><span aria-hidden="true">›</span></button>
        <button type="button" @click="$emit('playback')"><span>播放设置</span><span aria-hidden="true">›</span></button>
      </div>
      <footer>
        <span v-if="version">版本 {{ version }}</span>
        <button class="check-updates" type="button" :aria-disabled="checking" @click="check">
          {{ checking ? "检查中…" : "检查更新" }}
        </button>
      </footer>
      <p v-if="error" class="settings-error" role="alert">{{ error }}</p>
    </div>
  </section>
</template>

<style scoped>
.desktop-settings-card.radio-panel {
  width: min(410px, 100%);
  padding-bottom: 16px;
  outline: none;
}

.settings-links { display: grid; gap: 6px; }

.settings-links button, .check-updates {
  min-height: 38px;
  padding: 7px 12px;
  border: 1px solid var(--line-strong);
  border-radius: 12px;
  background: var(--panel-fill);
  color: inherit;
  font-size: 13px;
}

.settings-links button {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.settings-links button:hover, .check-updates:hover { background: var(--panel-fill-active); }
.check-updates[aria-disabled="true"] { cursor: wait; opacity: .7; }

footer {
  display: flex;
  flex: none;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding-top: 10px;
  border-top: 1px solid var(--line);
  color: #b1bcc9;
  font-size: 12px;
}

.check-updates { margin-inline-start: auto; }
.settings-error { margin: 11px 0 0; color: #ffb6a3; }
</style>
