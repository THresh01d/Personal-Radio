<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { checkTtsHealth, getTtsHealth } from "../../services/api";
import type { TtsHealth } from "../../types/voice";

const health = ref<TtsHealth | null>(null);
const busy = ref(false);
const testing = ref(false);
const error = ref("");
let revision = 0;
let request: AbortController | null = null;
const message = computed(() => {
  if (error.value) return error.value;
  if (health.value?.restart_required) return "语音依赖已更新，重启电台后生效。";
  if (health.value?.dependency_mismatch) return "语音依赖与项目版本不一致，请按更新说明检查。";
  if (health.value?.cooldown_seconds) return "在线语音暂时避让中，将尝试本机语音；稍后随报幕重试。";
  if (health.value?.last_failure_message) return health.value.last_failure_message;
  return health.value?.last_check ? `上次检测：${health.value.last_check.message}` : "尚未检测在线连接。";
});

async function refresh(check = false) {
  if (busy.value) return;
  const current = ++revision;
  request = new AbortController();
  busy.value = true;
  testing.value = check;
  error.value = "";
  try {
    // 打开设置只读状态；只有明确点击检测才调用固定短句合成接口。
    const result = await (check ? checkTtsHealth(request.signal) : getTtsHealth(request.signal));
    if (current === revision) health.value = result;
  } catch (cause) {
    if (current === revision) error.value = cause instanceof Error ? cause.message : "语音状态暂时无法读取。";
  } finally {
    if (current === revision) { busy.value = false; testing.value = false; request = null; }
  }
}
onMounted(() => { void refresh(); });
onBeforeUnmount(() => { ++revision; request?.abort(); });
</script>

<template>
  <details class="voice-health">
    <summary>在线语音</summary>
    <p role="status" aria-live="polite">{{ testing ? "正在检测在线连接…" : busy ? "正在读取语音状态…" : message }}</p>
    <div class="voice-health-actions">
      <button type="button" :disabled="busy" @click="refresh()">刷新状态</button>
      <button type="button" :disabled="busy || health?.checking" @click="refresh(true)">{{ testing ? "检测中…" : "检测在线语音" }}</button>
    </div>
    <small>合成固定测试句，检测期间可继续听歌。</small>
    <details v-if="health"><summary>版本信息</summary><dl>
      <dt>当前进程</dt><dd>{{ health.loaded_version }}</dd>
      <dt>已安装</dt><dd>{{ health.installed_version }}</dd>
      <dt>项目要求</dt><dd>{{ health.required_version }}</dd>
    </dl></details>
  </details>
</template>

<style scoped>
.voice-health { margin-top: 10px; color: #bdc6d3; }
.voice-health > summary {
  display: flex;
  align-items: center;
  justify-content: space-between;
  min-height: 34px;
  border-top: 1px solid #ffffff14;
  list-style: none;
  font-size: 12px;
}
.voice-health > summary::-webkit-details-marker { display: none; }
.voice-health > summary::after {
  content: "";
  width: 6px;
  height: 6px;
  margin-right: 4px;
  border: solid currentColor;
  border-width: 0 1px 1px 0;
  transform: rotate(45deg);
}
.voice-health[open] > summary::after { transform: rotate(225deg); }
.voice-health-actions { display: flex; flex-wrap: wrap; gap: 6px; }
.voice-health button{border:1px solid #ffffff20;background:#ffffff05;color:#d2d9e3;border-radius:8px;padding:6px 9px;font-size:10px;min-height:29px}
.voice-health button:hover:not(:disabled){background:#ffffff0c;border-color:#ffffff40;transform:none}
.voice-health button:disabled{opacity:.4}
.voice-health p{margin:0 0 8px;font-size:11px;line-height:1.6;color:#a7b2c2;overflow-wrap:anywhere}
.voice-health>small{display:block;margin-top:7px;color:#7f8a9b;font-size:10px;line-height:1.7}
.voice-health details{margin-top:9px;color:#8592a4;font-size:10px}
.voice-health summary{cursor:pointer}
.voice-health dl{display:grid;grid-template-columns:1fr auto;gap:5px;margin:8px 0}
.voice-health dd{margin:0;font-family:ui-monospace,monospace;color:#b9c3d2}
.voice-health button:focus-visible,.voice-health summary:focus-visible{outline:2px solid #cbd2dd;outline-offset:3px}
</style>

