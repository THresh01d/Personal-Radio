<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from "vue";
import { allowLibraryReimport, getRemovedLibraryItems } from "../../services/api";
import type { RemovedLibraryItem } from "../../types/libraryRemoval";
const emit = defineEmits<{ changed: []; close: [] }>();
const items = ref<RemovedLibraryItem[]>([]), query = ref("");
const loading = ref(false), saving = ref(false), error = ref(""), notice = ref("");
let alive = true, revision = 0;
async function load() {
  const current = ++revision; loading.value = true; error.value = "";
  try { const result = await getRemovedLibraryItems(query.value); if (alive && current === revision) items.value = result.items; }
  catch (e) { if (alive && current === revision) error.value = e instanceof Error ? e.message : "记录读取失败"; }
  finally { if (alive && current === revision) loading.value = false; }
}
async function restore(item: RemovedLibraryItem) {
  if (saving.value) return;
  saving.value = true; error.value = ""; notice.value = "";
  try {
    await allowLibraryReimport(item);
    if (!alive) return;
    notice.value = "已允许重新导入。请主动同步原歌单以恢复歌曲。";
    emit("changed"); await load();
  } catch (e) { if (alive) error.value = e instanceof Error ? e.message : "更新状态失败，请刷新后核对"; }
  finally { if (alive) saving.value = false; }
}
onMounted(load);
onBeforeUnmount(() => { alive = false; revision++; });
</script>
<template>
  <section class="removed-library-panel" aria-label="已移除的本地内容">
    <div class="removed-library-heading"><strong>已移除</strong><button type="button" aria-label="收起已移除" @click="emit('close')">收起</button></div>
    <p>这些内容不会随同步自动回来。允许重新导入后，再主动同步原歌单恢复。</p>
    <form role="search" @submit.prevent="load"><input v-model="query" aria-label="搜索已移除内容" placeholder="搜索已移除的歌单或歌曲" /><button type="submit" :disabled="loading || saving">查找</button></form>
    <p v-if="error" role="alert">{{ error }}<button type="button" :disabled="saving" @click="load">刷新状态</button></p>
    <p v-if="notice" role="status">{{ notice }}</p>
    <p v-if="loading" role="status">正在读取…</p>
    <p v-else-if="!items.length">没有匹配的移除记录。</p>
    <ul v-else><li v-for="item in items" :key="`${item.kind}-${item.target_id}`"><span><strong>{{ item.name }}</strong><small>{{ item.kind === 'playlist' ? '歌单' : '歌曲' }}<template v-if="item.artist"> · {{ item.artist }}</template></small></span><button type="button" :disabled="saving" @click="restore(item)">允许重新导入</button></li></ul>
    <small v-if="items.length >= 100">当前显示最近100条，可通过名称查找其他记录。</small>
  </section>
</template>
<style>
.removed-library-panel { border: 1px solid #ffffff25; border-radius: 14px; padding: 16px; margin: 12px 0; background: #0c1015aa; }
.removed-library-heading,.removed-library-panel form,.removed-library-panel li { display: flex; align-items: center; justify-content: space-between; gap: 10px; }
.removed-library-panel p,.removed-library-panel small { color: #a8b1bc; font-size: 12px; line-height: 1.6; }
.removed-library-panel form input { flex: 1; min-width: 0; border: 1px solid #ffffff28; border-radius: 8px; background: #0003; color: inherit; padding: 8px; }
.removed-library-panel button { flex-shrink: 0; padding: 6px 10px; border: 1px solid #ffffff28; border-radius: 8px; color: #d7e0e8; background: #ffffff08; font-size: 11px; }
.removed-library-panel ul { list-style: none; padding: 0; max-height: 250px; overflow: auto; }
.removed-library-panel li { padding: 12px 0; border-top: 1px solid #ffffff15; }
.removed-library-panel li span { min-width: 0; display: grid; gap: 4px; }
.removed-library-panel li strong { font-size: 12px; overflow-wrap: anywhere; }
</style>
