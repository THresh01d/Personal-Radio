<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import PlayerGlass from "../player/PlayerGlass.vue";
import { actSongReview, getSongReview, proposeSongReview } from "../../services/api";
import type { ReviewScope, Song, SongReview, SongReviewSnapshot } from "../../types/radio";

const props = defineProps<{ song: Song; sessionId: string }>();
const emit = defineEmits<{ close: []; changed: [] }>();
const data = ref<SongReviewSnapshot | null>(null);
const scope = ref<ReviewScope>("query_positive");
const genres = ref<string[]>([]);
const error = ref("");
const loading = ref(false);
const saving = ref(false);
const notice = ref("");
const card = ref<HTMLElement | null>(null);
let previousFocus: HTMLElement | null = null;
let revision = 0;
let controller: AbortController | null = null;

function restoreDialogFocus(version: number) {
  // 按钮禁用会让浏览器丢失焦点；异步结束仅在当前弹窗恢复，不能抢新歌曲/已关闭页面。
  void nextTick(() => {
    if (version === revision && card.value && !card.value.contains?.(document.activeElement)) card.value.focus?.();
  });
}

async function load(more = false) {
  const version = ++revision;
  controller?.abort(); controller = new AbortController();
  loading.value = true; error.value = "";
  try {
    const next = await getSongReview(props.song.id, props.sessionId, props.song.batch_id, more ? data.value?.reviews.length || 0 : 0, controller.signal);
    if (version !== revision) return;
    data.value = more && data.value ? { ...next, reviews: [...data.value.reviews, ...next.reviews] } : next;
  } catch (e) {
    if (version === revision) error.value = e instanceof Error ? e.message : "读取失败";
  } finally { if (version === revision) { loading.value = false; restoreDialogFocus(version); } }
}

async function write(item?: SongReview, operation?: "confirm" | "revoke") {
  if (!data.value || saving.value || loading.value) return;
  const version = ++revision;
  controller?.abort(); controller = new AbortController();
  saving.value = true; error.value = ""; notice.value = "";
  try {
    if (item && operation) {
      await actSongReview(item, operation, data.value.metadata_sha256, props.sessionId, controller.signal);
    } else {
      await proposeSongReview(props.song.id, { metadata_sha256: data.value.metadata_sha256, scope: scope.value,
        genres: scope.value === "global_genre" ? [...genres.value] : undefined,
        batch_id: scope.value === "query_positive" ? data.value.context?.batch_id : undefined,
        session_id: scope.value === "query_positive" ? props.sessionId : undefined }, controller.signal);
    }
    if (version !== revision) return;
    notice.value = operation === "confirm" ? (item?.payload.scope === "global_genre" ? "校正已生效，下次标签检索使用；当前播放不变" : "已记录本次需求正例，不改变全局标签或偏好")
      : operation === "revoke" ? (item?.payload.scope === "global_genre" ? "已撤销；后续标签检索恢复有效的原档案" : "本次需求正例已撤销；全局标签与偏好不变") : "待确认提案已保存，请核对范围后确认";
    emit("changed");
    saving.value = false;
    await load();
  } catch (e) { if (version === revision) error.value = e instanceof Error ? e.message : "操作失败"; }
  finally { if (version === revision) { saving.value = false; restoreDialogFocus(version); } }
}

function toggleGenre(genre: string) {
  genres.value = genres.value.includes(genre) ? genres.value.filter(g => g !== genre) : [...genres.value, genre].slice(0, 3);
}
function trapTab(event: KeyboardEvent) {
  if (event.key !== "Tab") return;
  const nodes = [...(card.value?.querySelectorAll<HTMLElement>('button:not(:disabled),input:not(:disabled),[tabindex="0"]') || [])];
  if (!nodes.length) return;
  const index = nodes.indexOf(document.activeElement as HTMLElement);
  if (index < 0 || (event.shiftKey && index === 0) || (!event.shiftKey && index === nodes.length - 1)) {
    event.preventDefault(); (event.shiftKey ? nodes[nodes.length - 1] : nodes[0])?.focus();
  }
}
watch(() => [props.song.id, props.sessionId, props.song.batch_id], () => {
  data.value = null; genres.value = []; scope.value = "query_positive"; notice.value = ""; saving.value = false;
  void load();
}, { immediate: true });
onMounted(() => { previousFocus = document.activeElement as HTMLElement | null; void nextTick(() => card.value?.focus?.()); });
onBeforeUnmount(() => { revision++; controller?.abort(); if (previousFocus?.isConnected) previousFocus.focus(); });
</script>

<template>
  <section class="qr-modal glass-dialog song-review-backdrop" @keydown.esc.stop="emit('close')" @keydown="trapTab">
    <div ref="card" class="radio-panel glass-dialog-card song-review-card" role="dialog" aria-modal="true" aria-labelledby="review-title" tabindex="-1">
      <PlayerGlass :corner-radius="24" :refract="false" />
      <header>
        <h2 id="review-title">查看／纠正风格</h2>
        <button type="button" aria-label="关闭歌曲审核" @click="emit('close')">
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m6 6 12 12M18 6 6 18" /></svg>
        </button>
      </header>
      <div class="glass-dialog-content review-content">
        <p>{{ song.title }} · {{ song.artist }}</p>
        <p v-if="error" role="alert">{{ error }} <button :disabled="saving" @click="load()">重新读取</button></p>
        <p v-if="notice" role="status">{{ notice }}</p>
        <p v-if="loading" role="status">正在读取审核记录…</p>
        <template v-if="data">
          <p>原档案标签（可能为 AI 推断）：{{ data.original_genres.join(' · ') || '尚无标签' }}</p>
          <p v-if="data.platform_metadata">网易云百科曲风：{{ data.platform_metadata.raw_genres?.join(' · ') || '平台未提供曲风' }}<small>平台提供的标签，可能不完整；与 AI 推断分开保存。</small></p>
          <p>后续检索使用：{{ data.effective_genres.join(' · ') || '尚无标签' }}<span v-if="data.active_review_id">（用户确认校正）</span></p>
          <p v-if="data.conflict" role="alert">存在多条冲突校正，暂用原档案。请先撤销旧记录。</p>
          <fieldset :disabled="saving || loading">
            <legend>这次操作的范围</legend>
            <label><input v-model="scope" type="radio" value="query_positive" /> 只记录“符合这次需求”，不改全局风格</label>
            <p v-if="data.context">当时需求：{{ data.context.query }}</p>
            <small v-else>这首歌没有关联的推荐需求，可以校正风格标签。</small>
            <label><input v-model="scope" type="radio" value="global_genre" /> 校正这首歌今后的风格标签（不影响整个歌手）</label>
            <div v-if="scope === 'global_genre'" class="review-genres">
              <small>选择完整的新标签，最多 3 个；保存后还需再次确认。</small>
              <button v-for="genre in data.genre_options" :key="genre" type="button" :aria-pressed="genres.includes(genre)" :disabled="!genres.includes(genre) && genres.length >= 3" @click="toggleGenre(genre)">{{ genre }}</button>
            </div>
            <button type="button" :disabled="scope === 'query_positive' ? !data.context : !genres.length || Boolean(data.active_review_id) || data.conflict" @click="write()">保存待确认提案</button>
            <small v-if="scope === 'global_genre' && data.active_review_id">已有生效校正，请先撤销后再改。</small>
          </fieldset>
          <h3>审核记录（{{ data.total }}）</h3>
          <article v-for="item in data.reviews" :key="item.review_id">
            <p>{{ item.payload.scope === 'global_genre' ? '全局风格：' + item.payload.genres?.join(' · ') : '这次需求正例（不改全局标签）' }} · {{ item.status === 'pending' ? '待确认' : item.status === 'confirmed' ? '已确认' : '已撤销' }}<span v-if="item.stale"> · 歌曲元数据已变，不能生效</span></p>
            <button v-if="item.status === 'pending'" type="button" :disabled="saving || loading || item.stale || (item.payload.scope === 'global_genre' && Boolean(data.active_review_id)) || (item.payload.scope === 'query_positive' && item.payload.query_key !== data.context?.batch_id)" @click="write(item, 'confirm')">{{ item.payload.scope === 'global_genre' ? '确认全局校正' : '确认这次需求正例' }}</button>
            <button v-if="item.status !== 'revoked'" type="button" :disabled="saving || loading" @click="write(item, 'revoke')">撤销这条记录</button>
          </article>
          <button v-if="data.has_more" :disabled="saving || loading" @click="load(true)">加载更多审核记录</button>
        </template>
        <small v-if="saving" role="status">正在保存；关闭页面不等于撤销已提交操作，请重新读取确认状态。</small>
      </div>
    </div>
  </section>
</template>

<style scoped>
.song-review-backdrop { z-index: 100; }
.song-review-card.radio-panel { width: min(560px, 100%); padding-bottom: 6px; outline: none; }
.review-content { font-size: 13px; line-height: 1.65; overflow-wrap: anywhere; }
.review-content p { margin: 0 0 8px; }
.review-content h3 { margin: 16px 0 8px; font-size: 14px; }
.review-content button { border: 1px solid var(--line-strong); border-radius: 9px; padding: 6px 10px; background: var(--panel-fill); color: inherit; margin: 3px 5px 3px 0; }
.review-content button:hover:not(:disabled), .review-content button[aria-pressed=true] { background: var(--panel-fill-active); }
.review-content button[aria-pressed=true] { border-color: #ffffff8c; }
.review-content button:disabled { opacity: .45; cursor: default; }
.review-content small { display: block; color: #b1bcc9; }
.review-content fieldset { min-width: 0; margin: 14px 0 0; border: 1px solid var(--line); border-radius: 12px; }
.review-content label { display: block; margin: 7px 0; cursor: pointer; }
.review-content input { accent-color: #aab8c7; }
.review-content article { border-top: 1px solid var(--line); padding: 8px 0; }
.review-content [role=alert] { color: #ffb698; }
.review-content [role=status] { color: #d8e2ef; }
</style>
