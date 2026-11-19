<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue';
import { getLocalLibrarySongs, removeMemory, saveMemory } from '../../services/api';
import type { MemoryDraft, MemoryPreference, SceneMemory, Song } from '../../types/radio';

const props = defineProps<{ sessionId: string; items: MemoryPreference[] }>();
const emit = defineEmits<{ changed: [] }>();
const open = ref(false);
const busy = ref(false);
const error = ref('');
const message = ref('');
const targetText = ref('');
const referenceText = ref('');
const choices = ref<Song[]>([]);
const referenceChoices = ref<Song[]>([]);
const draft = ref<MemoryDraft>({ kind: 'genre', target: '', value: 'prefer', scene: '', reference: '' });
const editor = ref<HTMLFormElement | null>(null);
const showAll = ref(false);
const previewCount = 3;
const visibleItems = computed(() => showAll.value ? props.items : props.items.slice(0, previewCount));
let revision = 0;
let searchRevision = 0;
onBeforeUnmount(() => { revision++; searchRevision++; });
const genres = ['说唱', '流行', '民谣', '电子', 'R&B', '摇滚', '爵士', '灵魂乐', '另类', '独立', 'Lo-fi', '蒸汽波', 'City Pop', 'ACG', '国风', '古典', '金属', '蓝调', '纯音乐', 'emo'];
const scenes = ['工作专注', '睡前', '通勤', '深夜', '雨天', '运动', '开车', '派对', '独处', '旅行'];
const values = computed(() => draft.value.kind === 'reference' ? [['like', '像参考'], ['unlike', '不像参考']]
  : draft.value.kind === 'expression' ? (draft.value.target === 'mode' ? [['quiet', '直接听歌'], ['intro', '保留介绍']] : [['short', '简短介绍'], ['normal', '正常介绍']])
    : [['prefer', '优先一些'], ['reduce', '减少推荐'], ['exclude', '持续排除']]);

function focusEditor() {
  void nextTick(() => editor.value?.focus?.());
}

function add() {
  revision++; searchRevision++;
  draft.value = { kind: 'genre', target: '', value: 'prefer', scene: '', reference: '' };
  targetText.value = ''; referenceText.value = ''; choices.value = []; referenceChoices.value = [];
  error.value = ''; message.value = ''; open.value = true;
  focusEditor();
}

watch(() => draft.value.kind, kind => {
  searchRevision++;
  if (!open.value || draft.value.expected_id) return;
  draft.value.target = kind === 'expression' ? 'length' : '';
  draft.value.value = kind === 'reference' ? 'like' : kind === 'expression' ? 'short' : 'prefer';
  choices.value = []; referenceChoices.value = [];
});
watch(values, options => {
  if (!options.some(([value]) => value === draft.value.value)) draft.value.value = options[0][0];
});

function edit(item: MemoryPreference) {
  if (item.kind === 'reference_direction') return;
  revision++; searchRevision++;
  draft.value = { kind: item.kind, target: item.target.id || item.target_key, value: item.value,
    scene: item.scene, reference: item.target.reference?.id || '', expected_id: item.id };
  targetText.value = item.label;
  referenceText.value = item.target.reference?.title || '';
  choices.value = item.target.id ? [item.target as Song] : [];
  referenceChoices.value = item.target.reference ? [item.target.reference] : [];
  open.value = true; error.value = ''; message.value = '';
  focusEditor();
}

function editLegacy(item: SceneMemory) {
  edit({ id: `scene:${item.id}`, kind: 'song', target: item.song, target_key: item.song_id,
    scene: item.scene, value: 'exclude', label: item.song.title, value_label: '持续排除',
    effective: item.effective, source_text: item.source_text, version: item.version, reason: item.reason });
}
defineExpose({ editLegacy });

async function search(reference = false) {
  const text = (reference ? referenceText.value : targetText.value).trim();
  if (!text || busy.value) return;
  const current = ++searchRevision;
  error.value = '';
  try {
    const result = await getLocalLibrarySongs({ query: text, limit: 30 });
    if (current !== searchRevision || !open.value) return;
    if (reference) {
      referenceChoices.value = result.songs; draft.value.reference = result.songs.length === 1 ? result.songs[0].id : '';
    } else {
      choices.value = result.songs; draft.value.target = result.songs.length === 1 ? result.songs[0].id : '';
    }
    if (!result.songs.length) error.value = '没有找到作品，请核对曲库中的完整名称。';
  } catch (reason) {
    if (current === searchRevision) error.value = reason instanceof Error ? reason.message : '查找失败';
  }
}

async function submit() {
  if (busy.value) return;
  const current = revision;
  busy.value = true; error.value = ''; message.value = '';
  try {
    await saveMemory(props.sessionId, { ...draft.value });
    if (current !== revision) return;
    message.value = '已保存，后续相关选歌会使用这条偏好。';
    open.value = false; emit('changed');
  } catch (reason) {
    if (current === revision) error.value = reason instanceof Error ? reason.message : '保存未确认，请重新读取核对';
  } finally { if (current === revision) busy.value = false; }
}

async function forget(item: MemoryPreference) {
  if (busy.value) return;
  const current = revision;
  busy.value = true; error.value = ''; message.value = '';
  try {
    const removed = await removeMemory(props.sessionId, item.id);
    if (current !== revision) return;
    message.value = removed ? '已撤销；后续不再使用这条偏好。' : '这条记录已变化或不再有效，请以重新读取的列表为准。'; emit('changed');
  } catch (reason) {
    if (current === revision) error.value = reason instanceof Error ? reason.message : '撤销未确认，请重新读取核对';
  } finally { if (current === revision) busy.value = false; }
}
</script>

<template>
  <section class="explicit-memory" aria-label="已保存的偏好">
    <div class="memory-heading"><h4>已保存的偏好</h4><button class="taste-pill" type="button" :disabled="busy" @click="add">添加偏好</button></div>
    <p v-if="message" role="status" class="memory-help">{{ message }}</p>
    <p v-if="error" role="alert" class="memory-error">{{ error }}</p>
    <form v-if="open" ref="editor" class="memory-form" aria-label="编辑长期偏好" tabindex="-1" @submit.prevent="submit">
      <label>偏好类型<select v-model="draft.kind" :disabled="busy || Boolean(draft.expected_id)"><option value="song">歌曲</option><option value="artist">歌手</option><option value="genre">风格</option><option value="expression">DJ 介绍</option><option value="reference">参考歌曲关系</option></select></label>
      <label v-if="draft.kind === 'genre'">音乐风格<select v-model="draft.target" :disabled="busy"><option disabled value="">请选择风格</option><option v-for="genre in genres" :key="genre">{{ genre }}</option></select></label>
      <label v-else-if="draft.kind === 'artist'">完整歌手名<input v-model="draft.target" :disabled="busy" maxlength="200" required placeholder="填写曲库中的完整署名" /></label>
      <label v-else-if="draft.kind === 'expression'">介绍设置<select v-model="draft.target" :disabled="busy"><option value="length">介绍篇幅</option><option value="mode">介绍方式</option></select></label>
      <template v-else>
        <label>查找歌曲<input v-model="targetText" :disabled="busy" maxlength="200" placeholder="输入歌名，再查找并选择版本" @input="draft.target = ''; choices = []; searchRevision++" /></label><button type="button" :disabled="busy" @click="search(false)">查找作品</button>
        <label>歌曲版本<select v-model="draft.target" :disabled="busy" required><option disabled value="">请选择作品版本</option><option v-for="song in choices" :key="song.id" :value="song.id">{{ song.title }} · {{ song.artist }} · {{ song.album }}</option></select></label>
      </template>
      <template v-if="draft.kind === 'reference'">
        <label>查找参考曲<input v-model="referenceText" :disabled="busy" maxlength="200" placeholder="输入参考歌名" @input="draft.reference = ''; referenceChoices = []; searchRevision++" /></label><button type="button" :disabled="busy" @click="search(true)">查找参考曲</button>
        <label>参考曲版本<select v-model="draft.reference" :disabled="busy" required><option disabled value="">请选择参考版本</option><option v-for="song in referenceChoices" :key="song.id" :value="song.id">{{ song.title }} · {{ song.artist }} · {{ song.album }}</option></select></label>
      </template>
      <label>适用场景<select v-model="draft.scene" :disabled="busy"><option value="">一般偏好</option><option v-for="scene in scenes" :key="scene">{{ scene }}</option></select></label>
      <label>你的意见<select v-model="draft.value" :disabled="busy"><option v-for="[value, label] in values" :key="value" :value="value">{{ label }}</option></select></label>
      <p v-if="draft.value === 'exclude'" class="memory-help">持续排除会避开该项。若只想这次例外，可以对 DJ 说「本次例外」。</p>
      <div class="memory-actions"><button type="submit" :disabled="busy || !draft.target || (draft.kind === 'reference' && !draft.reference)">{{ busy ? '正在处理…' : '保存长期偏好' }}</button><button type="button" :disabled="busy" @click="open = false; searchRevision++">取消编辑</button></div>
    </form>
    <p v-if="!items.length && !open" class="taste-copy">可以添加偏好，也可以对 DJ 说「以后少推荐说唱」。</p>
    <article v-for="item in visibleItems" :key="item.id" class="taste-card">
      <div class="taste-card-top"><div><strong>{{ item.label }}</strong><span>{{ item.scene || '一般偏好' }}<template v-if="item.target.artist"> · {{ item.target.artist }}</template></span></div><b>{{ item.effective ? item.value_label : '已暂停' }}</b></div>
      <details class="taste-record-detail">
        <summary>查看详情</summary>
        <p v-if="item.target.album">专辑：{{ item.target.album }}</p>
        <p v-if="item.target.reference">参考：{{ item.target.reference.title }} · {{ item.target.reference.artist }} · {{ item.target.reference.album }}</p>
        <p v-if="item.target.direction">相似方向：{{ [...item.target.direction.moods, ...(item.target.direction.style ? [item.target.direction.style] : item.target.direction.genres), ...item.target.direction.exclude.map(genre => '避开' + genre)].join('、') }}</p>
        <p>{{ item.reason }}</p>
        <p v-if="item.source_text">来源：{{ item.source_text }}</p>
      </details>
      <div class="taste-inline-actions"><button v-if="item.kind !== 'reference_direction'" class="taste-correct" type="button" :disabled="busy" @click="edit(item)">修改这条偏好</button><button class="taste-correct" type="button" :disabled="busy" @click="forget(item)">撤销这条偏好</button></div>
    </article>
    <button v-if="items.length > previewCount" class="taste-correct" type="button" @click="showAll = !showAll">{{ showAll ? "收起" : `查看全部 ${items.length} 条` }}</button>
  </section>
</template>

<style scoped>
.memory-help { margin: .6rem 0; color: #b7c1cf; font-size: .78rem; line-height: 1.65; overflow-wrap: anywhere; }
.memory-heading { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 6px 12px; }
.memory-error { color: #ffc1b9; font-size: .8rem; }
.memory-form { display: grid; gap: .7rem; padding: .9rem; border: 1px solid var(--line); border-radius: 12px; margin: .9rem 0; color-scheme: dark; }
.memory-form:focus { outline: 1px solid var(--line-strong); outline-offset: 2px; }
.memory-form label { display: grid; min-width: 0; gap: .35rem; font-size: .8rem; color: #bac5d3; }
.memory-form input, .memory-form select { min-width: 0; width: 100%; box-sizing: border-box; padding: .55rem; background: #131b26; color: var(--text); border: 1px solid var(--line-strong); border-radius: 10px; }
.memory-actions { display: flex; gap: .6rem; flex-wrap: wrap; }
.memory-actions button, .memory-form > button { min-height: 36px; padding: .45rem .7rem; border: 1px solid var(--line-strong); border-radius: 999px; background: var(--panel-fill); color: var(--text); cursor: pointer; font: inherit; font-size: .78rem; }
.memory-actions button:hover:not(:disabled), .memory-form > button:hover:not(:disabled) { background: var(--panel-fill-active); }
.memory-form :is(input, select):focus-visible { outline: 2px solid #cbd2dd; outline-offset: 2px; }
.memory-actions button:disabled, .memory-form > button:disabled { opacity: .5; cursor: wait; }
</style>
