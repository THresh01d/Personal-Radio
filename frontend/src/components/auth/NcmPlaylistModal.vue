<script setup lang="ts">
import { computed, ref } from "vue";
import PlayerGlass from "../player/PlayerGlass.vue";
import { usePanelFocus } from "../../composables/usePanelFocus";
import type { NcmPlaylist, NcmSyncResult, NcmSyncScope, TagCoverage } from "../../types/radio";

const props = defineProps<{
  playlists: NcmPlaylist[];
  selectedIds: string[];
  scope: NcmSyncScope;
  loading: boolean;
  syncing: boolean;
  task: NcmSyncResult | null;
  error: string;
  coverage?: TagCoverage | null;
  progressError?: string;
  progressReading?: boolean;
}>();

const emit = defineEmits<{
  close: [];
  reload: [];
  sync: [];
  retry: [];
  metadata: [];
  pause: [];
  reconnect: [];
  "update:scope": [value: NcmSyncScope];
  "update:selectedIds": [value: string[]];
}>();

const card = ref<HTMLElement | null>(null);
const onPanelKeydown = usePanelFocus(card, () => emit("close"), true);
const selectedCount = computed(() => props.scope === "all"
  ? props.playlists.length
  : props.scope === "liked"
    ? Math.min(1, props.playlists.filter((playlist) => playlist.is_liked).length)
    : props.selectedIds.length);

const phaseLabels: Record<string, string> = {
  queued: "任务已排队",
  authenticating: "正在确认登录状态",
  fetching_playlists: "正在读取歌单快照",
  fetching_tracks: "正在同步变化的曲目",
  embedding: "正在生成本地语义向量",
  saving: "正在保存歌单与歌曲来源",
  metadata: "曲目已保存，正在补全歌曲百科",
  finished: "本次任务已完成",
  failed: "同步失败",
  interrupted: "同步已中断",
};

const coverageInfo = computed(() => props.task?.tag_coverage || props.coverage);

const progress = computed(() => {
  const task = props.task;
  if (task?.status === "completed") return 100;
  if (task?.phase === "fetching_tracks" && task.total) {
    return Math.min(100, Math.floor((task.processed || 0) / task.total * 100));
  }
  if (task?.phase === "metadata" && task.metadata_total) {
    return Math.min(100, Math.floor((task.metadata_processed || 0) / task.metadata_total * 100));
  }
  return null;
});

const changesSaved = computed(() => props.task?.phase === "metadata"
  || props.task?.status === "completed" || props.task?.status === "partial_failed");

const phaseLabel = computed(() => {
  if (props.progressError) return "进度暂不可读，任务状态待确认";
  if (props.task?.status === "partial_failed") return "部分同步失败";
  if (props.task?.status === "failed") return "同步失败";
  if (props.task?.status === "interrupted") return "同步已中断";
  return phaseLabels[props.task?.phase || ""] || "正在准备同步";
});

function toggle(id: string) {
  const ids = new Set(props.selectedIds);
  if (ids.has(id)) ids.delete(id); else ids.add(id);
  emit("update:selectedIds", [...ids]);
}
</script>

<template>
  <section class="qr-modal ncm-modal library-modal" role="dialog" aria-modal="true" aria-label="选择要同步的网易云歌单" @click.self="$emit('close')">
    <div ref="card" class="radio-panel ncm-card library-card" tabindex="-1" @keydown="onPanelKeydown">
      <PlayerGlass :corner-radius="24" />
      <header>
        <h2>把你的歌单留在本地</h2>
        <button type="button" aria-label="关闭" @click="$emit('close')">
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m6 6 12 12M18 6 6 18" /></svg>
        </button>
      </header>
      <div class="ncm-modal-content">
        <p class="ncm-description">同步曲目、本地向量和平台百科，不下载歌曲文件。百科使用现有网易云登录，无需 AI Key。</p>

        <section v-if="task?.tag_coverage || coverage" class="sync-progress metadata-coverage" aria-label="歌曲标签覆盖">
          <p>风格标签：{{ (task?.tag_coverage || coverage)!.genre_count }} / {{ (task?.tag_coverage || coverage)!.total }} 首；未知 {{ (task?.tag_coverage || coverage)!.unknown_genre_count }} 首</p>
          <p>平台百科：已获取 {{ (task?.tag_coverage || coverage)!.platform_ready }} · 未提供有效字段 {{ (task?.tag_coverage || coverage)!.platform_empty }} · 获取失败 {{ (task?.tag_coverage || coverage)!.platform_failed }} · 待补全 {{ (task?.tag_coverage || coverage)!.platform_pending }}</p>
          <p v-if="coverageInfo?.platform_genre_count !== undefined">其中有可用曲风 {{ coverageInfo.platform_genre_count }} 首；已获取百科但缺少可用曲风 {{ coverageInfo.platform_without_genre_count || 0 }} 首。</p>
          <p v-if="coverageInfo?.platform_eligible_count !== undefined">有网易云歌单来源 {{ coverageInfo.platform_eligible_count }} 首；暂无网易云歌单来源 {{ coverageInfo.platform_unlinked_count || 0 }} 首，不能直接从网易云补全。</p>
          <small>未知风格不会用于明确风格请求凑数。平台标签可能不完整，可在单曲“查看／纠正风格”核对来源。</small>
          <button type="button" class="sync-retry" :disabled="syncing" @click="$emit('metadata')">补全已有曲库百科</button>
        </section>

        <nav class="sync-scope" aria-label="同步范围">
          <button type="button" :disabled="syncing" :class="{ active: scope === 'liked' }" @click="$emit('update:scope', 'liked')">我喜欢的音乐</button>
          <button type="button" :disabled="syncing" :class="{ active: scope === 'selected' }" @click="$emit('update:scope', 'selected')">选择歌单</button>
          <button type="button" :disabled="syncing" :class="{ active: scope === 'all' }" @click="$emit('update:scope', 'all')">全部歌单</button>
        </nav>

        <section v-if="task" class="sync-progress" aria-live="polite">
          <header>
            <strong>{{ phaseLabel }}</strong>
            <span v-if="progress !== null">{{ progressError ? "上次读取 " : task.phase === "fetching_tracks" ? "曲目读取 " : "" }}{{ progress }}%</span>
          </header>
          <div v-if="progress !== null" class="sync-progress-track" aria-hidden="true"><i :style="{ width: `${progress}%` }"></i></div>
          <p v-if="task.phase === 'metadata'">百科已处理 {{ task.metadata_processed || 0 }} / {{ task.metadata_total || 0 }} 首，失败 {{ task.metadata_failed || 0 }} 首；可关闭此面板继续听歌。<button type="button" class="sync-retry" :disabled="!syncing || Boolean(progressError)" @click="$emit('pause')">暂停补全</button></p>
          <p v-else-if="task.total && !changesSaved">已读取曲目 {{ task.processed || 0 }} / {{ task.total }} 首</p>
          <dl>
            <div><dt>{{ changesSaved ? "已新增" : "计划新增" }}</dt><dd>{{ task.added || 0 }}</dd></div>
            <div><dt>{{ changesSaved ? "已更新" : "计划更新" }}</dt><dd>{{ task.updated || 0 }}</dd></div>
            <div><dt>跳过歌单</dt><dd>{{ task.skipped_playlists || 0 }}</dd></div>
            <div><dt>失败歌单</dt><dd>{{ task.failed_playlists || 0 }}</dd></div>
          </dl>
          <p v-if="progressError" role="alert" class="sync-task-error">{{ progressError }}<button type="button" class="sync-retry" :disabled="progressReading" @click="$emit('reconnect')">重新读取进度</button></p>
          <p v-if="task.error" class="sync-task-error">{{ task.error }}</p>
          <button v-if="task.recoverable && !syncing" class="sync-retry" type="button" @click="$emit('retry')">重试未完成部分</button>
        </section>

        <div v-if="loading && !task" class="library-loading">正在读取你的网易云歌单…</div>
        <div v-else-if="error" class="library-error">
          <span>{{ error }}</span>
          <button type="button" @click="$emit('reload')">重新读取</button>
        </div>
        <div v-else-if="!task" class="library-list">
          <label
            v-for="playlist in playlists"
            :key="playlist.id"
            :class="{
              selected: scope === 'all' || (scope === 'liked' && playlist.is_liked) || selectedIds.includes(playlist.id),
              disabled: scope !== 'selected',
            }"
          >
            <input
              type="checkbox"
              :disabled="scope !== 'selected'"
              :checked="scope === 'all' || (scope === 'liked' && playlist.is_liked) || selectedIds.includes(playlist.id)"
              @change="toggle(playlist.id)"
            />
            <img v-if="playlist.cover_url" :src="playlist.cover_url" alt="" />
            <i v-else>♫</i>
            <span><strong>{{ playlist.name }}</strong><small>{{ playlist.track_count }} 首<span v-if="playlist.is_liked"> · 默认收藏</span></small></span>
          </label>
          <p v-if="!playlists.length" class="library-empty">这个账号暂时没有可同步的歌单。</p>
        </div>

      </div>
      <footer class="library-actions">
        <span v-if="!task">将同步 {{ selectedCount }} 个歌单</span>
        <span v-else>{{ task.status === "completed" ? "本次同步已完成（标签覆盖见上方）" : "同步任务会保留在本机" }}</span>
        <button v-if="!task" type="button" :disabled="syncing || loading || selectedCount === 0" @click="$emit('sync')">
          同步并建立本地曲库
        </button>
        <button v-else-if="!syncing" type="button" @click="$emit('close')">
          {{ task.status === "completed" ? "完成" : "知道了" }}
        </button>
        <button v-else type="button" disabled>
          正在建库…
        </button>
      </footer>
    </div>
  </section>
</template>

<style scoped>
.metadata-coverage p { font-size: 13px; line-height: 1.6; }
.metadata-coverage small { display: block; line-height: 1.6; }
</style>
