<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from "vue";
import { usePanelFocus } from "../../composables/usePanelFocus";
import { getRemovedLibraryItems, previewLibraryRemoval, removeLibraryItem } from "../../services/api";
import type { LibraryRemovalPreview, LibraryRemovalTarget } from "../../types/libraryRemoval";
import PlayerGlass from "../player/PlayerGlass.vue";

const props = defineProps<{ target: LibraryRemovalTarget }>();
const emit = defineEmits<{ close: []; removed: [result: LibraryRemovalPreview] }>();
const panel = ref<HTMLElement | null>(null);
const preview = ref<LibraryRemovalPreview | null>(null);
const loading = ref(false), saving = ref(false), finished = ref(false), uncertain = ref(false);
const error = ref("");
let alive = true, generation = 0;
let controller: AbortController | undefined;
let timer: ReturnType<typeof setTimeout> | undefined;
const keydown = usePanelFocus(panel, close, true);
function close() { if (!saving.value && !finished.value) emit("close"); }
function complete(result: LibraryRemovalPreview) {
  if (!alive || finished.value) return;
  finished.value = true; saving.value = false; uncertain.value = false; error.value = "";
  // 只有服务端确认成功才碎纸。先清理数据，再播放装饰动画，不用乐观消失冒充删除。
  emit("removed", result);
  const reduced = typeof window !== "undefined" && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
  timer = setTimeout(() => { if (alive) emit("close"); }, reduced ? 0 : 1450);
}
async function load() {
  const current = ++generation;
  controller?.abort(); controller = new AbortController();
  loading.value = true; error.value = "";
  try {
    if (uncertain.value && preview.value) {
      const status = await getRemovedLibraryItems(props.target.name, controller.signal);
      if (!alive || current !== generation) return;
      const saved = status.items.find(item => item.kind === props.target.kind && item.target_id === props.target.target_id);
      if (saved?.operation_revision === preview.value.revision) { complete(preview.value); return; }
    }
    const next = await previewLibraryRemoval(props.target, controller.signal);
    if (alive && current === generation) { preview.value = next; uncertain.value = false; }
  } catch (e) { if (alive && current === generation) { preview.value = uncertain.value ? preview.value : null; error.value = e instanceof Error ? e.message : "范围读取失败"; } }
  finally { if (alive && current === generation) loading.value = false; }
}
async function submit() {
  if (!preview.value || saving.value || finished.value || uncertain.value) return;
  saving.value = true; error.value = "";
  try { complete(await removeLibraryItem(props.target, preview.value.revision)); }
  catch (e) {
    if (!alive) return;
    uncertain.value = true;
    error.value = e instanceof Error ? e.message : "移除状态尚未确认，请重新核对";
  } finally { if (alive) saving.value = false; }
}
watch(() => [props.target.kind, props.target.target_id], () => { preview.value = null; uncertain.value = false; void load(); }, { immediate: true });
onBeforeUnmount(() => { alive = false; generation++; controller?.abort(); if (timer) clearTimeout(timer); });
</script>

<template>
  <div class="library-removal-backdrop" @click.self="close">
    <section ref="panel" class="library-removal-card" role="dialog" aria-modal="true" aria-labelledby="removal-title" tabindex="-1" @keydown="keydown">
      <PlayerGlass :corner-radius="24" />
      <header>
        <h2 id="removal-title">{{ finished ? "已移出本地" : target.kind === 'playlist' ? '移出这个歌单？' : '从曲库删除这首歌？' }}</h2>
        <button type="button" aria-label="关闭移除确认" :disabled="saving || finished" @click="close">×</button>
      </header>
      <div class="library-shredder" :class="{ shredding: finished }" aria-hidden="true">
        <div class="shred-feeder"><div class="shred-paper"><span>{{ target.kind === 'playlist' ? '♫' : '♪' }}</span><strong>{{ target.name }}</strong></div></div>
        <div class="shred-slot"><i></i><b></b><i></i></div>
        <div class="shred-strips"><i v-for="n in 12" :key="n" :style="{ '--strip': n, '--turn': `${n % 2 ? -1 : 1}` }"></i></div>
      </div>
      <p v-if="loading" role="status">正在核对本地范围…</p>
      <template v-else-if="preview && !finished">
        <p v-if="target.kind === 'playlist'">移除《{{ target.name }}》及仅属于它的 <strong>{{ preview.removed_song_count }}</strong> 首歌曲。另有 <strong>{{ preview.kept_shared_count }}</strong> 首在其他歌单中保留。</p>
        <p v-else>从本地曲库及 {{ preview.source_playlist_count }} 个本地歌单中移除《{{ preview.title }}》。</p>
        <p class="removal-note">网易云端保持不变。后续同步会跳过已移除内容；可在“已移除”中允许重新导入。当前音乐继续播放，待播曲目同步移除。</p>
      </template>
      <p v-if="finished" role="status">{{ target.kind === 'playlist' ? '歌单已移出，共享歌曲已保留。' : '歌曲已从本地曲库移除。' }}</p>
      <p v-if="error" class="removal-error" role="alert">{{ error }}</p>
      <footer v-if="!finished">
        <button type="button" :disabled="saving" @click="close">保留</button>
        <button v-if="error || uncertain" type="button" :disabled="saving || loading" @click="load">重新核对状态</button>
        <button v-else class="removal-confirm" type="button" :disabled="!preview || loading || saving" @click="submit">{{ saving ? '正在移除…' : '确认移出本地' }}</button>
      </footer>
    </section>
  </div>
</template>

<style>
.library-removal-backdrop { position: fixed; inset: 0; z-index: 130; display: grid; place-items: center; padding: 20px; background: #0008; backdrop-filter: blur(5px); }
.library-removal-card { position: relative; isolation: isolate; width: min(460px,100%); max-height: calc(100dvh - 40px); overflow: auto; border: 1px solid #ffffff30; border-radius: 24px; padding: 24px; background: #0b0e14b8; color: #e8e9ed; box-shadow: 0 30px 100px #0009; outline: none; }
.library-removal-card header,.library-removal-card footer { display: flex; align-items: center; justify-content: space-between; gap: 10px; }
.library-removal-card header button { display: grid; place-items: center; flex-shrink: 0; width: 30px; height: 30px; padding: 0; font-size: 22px; line-height: 1; }
.library-removal-card h2 { font-size: clamp(17px, 4vw, 21px); margin: 0; }
.library-removal-card p { font-size: 13px; line-height: 1.8; overflow-wrap: anywhere; }
.library-removal-card .removal-note { color: #a4aab4; font-size: 12px; }
.library-removal-card footer { justify-content: flex-end; margin-top: 22px; flex-wrap: wrap; }
.library-removal-card button { border: 1px solid #ffffff25; border-radius: 10px; padding: 8px 14px; background: #ffffff08; color: inherit; }
.library-removal-card button.removal-confirm { background: #dedfe8; color: #1b1c22; border-color: #fff8; }
.library-removal-card button:disabled { opacity: .4; cursor: default; }
.library-removal-card button:focus-visible { outline: 2px solid #d7e3ef; outline-offset: 3px; }
.removal-error { color: #efc4b8; }
.library-shredder { position: relative; width: min(290px,100%); height: 210px; margin: 16px auto 0; perspective: 600px; }
.shred-feeder { position: absolute; inset: 0 12px auto; height: 130px; overflow: hidden; }
.shred-paper { display: grid; align-content: center; gap: 8px; min-height: 118px; padding: 14px 18px; box-sizing: border-box; border: 1px solid #edf0f345; border-radius: 10px 10px 0 0; background: linear-gradient(130deg,#c5cbd726,#e3e7ed12); box-shadow: inset 0 1px #fff3; }
.shred-paper span { font-size: 25px; color: #e2e5ef; }
.shred-paper strong { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 14px; }
.shred-slot { position: absolute; top: 124px; left: 0; right: 0; height: 14px; display: flex; align-items: center; gap: 8px; border: 1px solid #ffffff40; border-radius: 8px; padding: 0 12px; background: linear-gradient(#383e48,#12151c); box-shadow: 0 6px 16px #0008; z-index: 2; }
.shred-slot b { flex: 1; height: 3px; background: #030508; box-shadow: 0 1px #c6d5e43b; }
.shred-slot i { width: 3px; height: 3px; border-radius: 50%; background: #cad5e2; }
.shred-strips { position: absolute; top: 134px; left: 12px; right: 12px; display: flex; gap: 2px; transform-style: preserve-3d; }
.shred-strips i { flex: 1; height: 83px; opacity: 0; transform-origin: top; background: linear-gradient(90deg,#596577,#c3cbd9 55%,#7a8494); border-radius: 0 0 2px 2px; }
.shredding .shred-paper { animation: shred-feed .8s cubic-bezier(.5,0,.75,.7) both; }
.shredding .shred-strips i { animation: shred-curl 1.2s calc(var(--strip) * 14ms) ease-in both; }
@keyframes shred-feed { to { transform: translateY(140px); opacity: .2; } }
@keyframes shred-curl { 0%,12% { opacity: 0; transform: scaleY(0); } 40% { opacity: .85; transform: scaleY(.65) rotateX(12deg); } 65% { opacity: .75; transform: scaleY(1) rotateX(42deg) rotateZ(calc(var(--turn) * 8deg)); } 100% { opacity: 0; transform: translateY(44px) rotateX(70deg) rotateZ(calc(var(--turn) * 18deg)); } }
@media (prefers-reduced-motion: reduce) { .shredding .shred-paper,.shredding .shred-strips i { animation: none; } .shredding .shred-paper { opacity: 0; } }
@media (max-width: 480px) { .library-removal-backdrop { padding: 12px; } .library-removal-card { padding: 20px; border-radius: 20px; } }
</style>
