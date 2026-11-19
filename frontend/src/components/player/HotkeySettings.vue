<script setup lang="ts">
import { computed, onBeforeUnmount, ref } from "vue";
import { beginHotkeyCapture, endHotkeyCapture, getHotkeys, renewLocalAccess, saveHotkeys } from "../../services/api";
import { defaultHotkeys, hotkeyActions, hotkeyLabels, type HotkeyAction, type HotkeyStatus } from "../../types/hotkeys";

const namedKeys: Record<string, string> = { ArrowLeft: "←", ArrowRight: "→", ArrowUp: "↑", ArrowDown: "↓", Space: "Space" };
const status = ref<HotkeyStatus | null>(null);
const draft = ref({ ...defaultHotkeys });
const busy = ref(false);
const message = ref("");
const recording = ref<HotkeyAction | null>(null);
const ready = ref(false);
let captureToken = "";
const captured = ref("");
let captureTimer = 0;
const lifetime = new AbortController();
const duplicate = computed(() => new Set(hotkeyActions.map(action => draft.value[action])).size < hotkeyActions.length);

async function startRecording(action: HotkeyAction) {
  if (busy.value) return;
  if (recording.value === action) { void stopRecording(); return; }
  if (recording.value) return;
  const token = crypto.randomUUID();
  captureToken = token;
  recording.value = action;
  message.value = "正在准备录入…";
  try {
    await renewLocalAccess(lifetime.signal);
    if (captureToken !== token) return;
    await beginHotkeyCapture(token);
    if (captureToken !== token) {
      await endHotkeyCapture(token);
      return;
    }
    ready.value = true;
    message.value = "请按下组合键，Esc 取消。";
    captureTimer = window.setTimeout(() => { void stopRecording("录入已超时，点击按键可重试。"); }, 14000);
  } catch (error) {
    if (captureToken === token) {
      void stopRecording(error instanceof Error ? error.message : "暂时无法录入按键。");
    }
  }
}

async function stopRecording(hint = "") {
  if (!captureToken) return;
  const token = captureToken;
  captureToken = "";
  captured.value = "";
  window.clearTimeout(captureTimer);
  recording.value = null;
  ready.value = false;
  message.value = hint;
  busy.value = true;
  try { await endHotkeyCapture(token); }
  catch { message.value = "快捷键将在录入超时后恢复。"; }
  finally { busy.value = false; }
}

function record(action: HotkeyAction, event: KeyboardEvent) {
  if (recording.value !== action) return;
  if (event.key === "Tab") return;
  event.preventDefault();
  event.stopPropagation();
  if (event.key === "Escape") { void stopRecording(); return; }
  if (!ready.value || event.repeat || event.isComposing || captured.value) return;
  if (["Control", "Alt", "Shift", "Meta", "AltGraph"].includes(event.key)) return;
  const modifiers = [event.ctrlKey && "Ctrl", event.altKey && "Alt", event.shiftKey && "Shift"].filter(Boolean);
  const key = namedKeys[event.code] || (/^[a-z]$/i.test(event.key) ? event.key.toUpperCase() : "")
    || (/^Digit[0-9]$/.test(event.code) ? event.code.slice(-1) : "")
    || (/^F(?:[1-9]|1[013-9]|2[0-4])$/.test(event.code) ? event.code : "");
  if (event.metaKey || event.getModifierState?.("AltGraph") || modifiers.length < 2 || !key) {
    message.value = "请按 Ctrl、Alt、Shift 中至少两个，再加字母、数字、方向键、空格或功能键（F12 除外）。";
    return;
  }
  captured.value = [...modifiers, key].join("+");
  message.value = "松开按键后即可保存。";
}

function release(event: KeyboardEvent) {
  if (!recording.value || !captured.value || event.ctrlKey || event.altKey || event.shiftKey) return;
  event.preventDefault();
  draft.value[recording.value] = captured.value;
  void stopRecording();
}

function accept(result: HotkeyStatus) {
  status.value = result;
  draft.value = { ...result.config };
  const inactive = hotkeyActions.filter(action => !result.active.includes(action)).map(action => hotkeyLabels[action]);
  message.value = !result.supported ? "全局快捷键适用于 Windows 本机电台。"
    : !result.config.enabled ? "全局快捷键已关闭。"
    : inactive.length ? `${inactive.join("、")}未注册。请改用其他组合键并保存。` : "全局快捷键已启用。";
}

async function refresh(event: Event) {
  if (!(event.target as HTMLDetailsElement).open) { void stopRecording(); return; }
  if (status.value || busy.value) return;
  busy.value = true;
  try {
    await renewLocalAccess(lifetime.signal);
    accept(await getHotkeys(lifetime.signal));
  } catch {
    message.value = "暂时无法读取快捷键设置。重新打开此项可重试。";
  } finally {
    busy.value = false;
  }
}

async function save() {
  if (busy.value || duplicate.value || recording.value) return;
  busy.value = true;
  try {
    await renewLocalAccess(lifetime.signal);
    accept(await saveHotkeys(draft.value, lifetime.signal));
  } catch (reason) {
    message.value = reason instanceof Error ? reason.message : "快捷键暂时无法保存。";
  } finally {
    busy.value = false;
  }
}

onBeforeUnmount(() => {
  lifetime.abort();
  void stopRecording();
});
</script>

<template>
  <details class="hotkey-settings" @toggle="refresh">
    <summary>全局快捷键</summary>
    <p v-if="status?.supported">点击当前组合，再按你想用的快捷键。</p>
    <form v-if="status?.supported" @submit.prevent="save">
      <label class="hotkey-toggle"><input v-model="draft.enabled" type="checkbox" :disabled="busy || recording !== null" />启用全局快捷键</label>
      <div v-for="action in hotkeyActions" :key="action" class="hotkey-row">
        <span>{{ hotkeyLabels[action] }}</span>
        <button class="hotkey-binding" type="button" :aria-label="'录入' + hotkeyLabels[action] + '快捷键'"
          :aria-pressed="recording === action" :disabled="busy || (recording !== null && recording !== action)"
          @click="startRecording(action)" @keydown="record(action, $event)" @keyup="release" @blur="stopRecording()">
          {{ (recording === action ? (captured || '按下组合键…') : draft[action]).replaceAll('+', ' + ').replace('Space', '空格') }}
        </button>
      </div>
      <div class="hotkey-actions">
        <button type="submit" :disabled="busy || duplicate || recording !== null">保存快捷键</button>
        <button type="button" :disabled="busy || recording !== null" @click="draft = { ...defaultHotkeys }; message = ''">填入默认键</button>
      </div>
    </form>
    <p role="status">{{ recording ? message : duplicate ? "各项快捷键必须不同。" : busy ? "正在更新快捷键…" : message }}</p>
  </details>
</template>

<style scoped>
.hotkey-settings { margin-top: 10px; border-top: 1px solid #ffffff14; color: #bdc6d3; font-size: 12px; }
summary { cursor: pointer; padding-block: 10px; }
p { margin: 0 0 8px; color: #a7b2c2; line-height: 1.5; overflow-wrap: anywhere; }
.hotkey-toggle, .hotkey-actions { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; margin-block: 8px; }
.hotkey-row { display: grid; grid-template-columns: 1fr minmax(124px, 1fr); gap: 8px; align-items: center; margin-bottom: 6px; }
button { min-width: 0; min-height: 29px; border: 1px solid #ffffff24; border-radius: 8px; padding: 4px 6px; background: #161b23; color: #d2d9e3; font: inherit; }
.hotkey-binding[aria-pressed="true"] { border-color: #cbd2dd; color: #edf0f5; }
button:disabled { opacity: .45; }
</style>
