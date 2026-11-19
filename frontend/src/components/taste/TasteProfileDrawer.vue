<script setup lang="ts">
import PlayerGlass from "../player/PlayerGlass.vue";
import { computed, onMounted, ref } from "vue";
import { usePanelFocus } from "../../composables/usePanelFocus";
import {
  getTasteProfile,
  recalculateTasteProfile,
  removeTasteBehavior,
  removeTasteFeedback,
  removeTaskFeedback,
  removeSceneMemory,
  resetTasteProfile,
} from "../../services/api";
import type { TasteProfile, TasteSong, SceneMemory } from "../../types/radio";
import MemoryPreferences from './MemoryPreferences.vue';

const props = defineProps<{ sessionId: string }>();
const emit = defineEmits<{ close: [] }>();
const panel = ref<HTMLElement | null>(null);
const onPanelKeydown = usePanelFocus(panel, () => emit("close"));
defineExpose({ focus: () => panel.value?.focus({ preventScroll: true }) });

const profile = ref<TasteProfile | null>(null);
const loading = ref(false);
const error = ref("");
const actionKey = ref("");
const expandedSections = ref<Record<string, boolean>>({});
const collapsedItemCount = 3;
const preferencePanel = ref<InstanceType<typeof MemoryPreferences> | null>(null);

const songPreferenceGroups = computed(() => {
  const explicit = profile.value?.explicit;
  if (!explicit) return [];
  return [
    { key: "liked", label: "喜欢的歌曲", songs: explicit.liked_songs },
    { key: "disliked", label: "减少推荐的歌曲", songs: explicit.disliked_songs },
  ].filter(group => group.songs.length);
});
const artistPreferences = computed(() => {
  const explicit = profile.value?.explicit;
  return explicit ? [...explicit.boosted_artists, ...explicit.demoted_artists] : [];
});
const listeningGroups = computed(() => {
  const behavior = profile.value?.behavior;
  if (!behavior) return [];
  return [
    { key: "boosted", label: "经常听完或重播", effect: "略微提高推荐排序", songs: behavior.boosted_songs },
    { key: "demoted", label: "经常在前段跳过", effect: "略微降低推荐排序", songs: behavior.demoted_songs },
  ].filter(group => group.songs.length);
});
const inferenceCount = computed(() => listeningGroups.value.reduce((count, group) => count + group.songs.length, 0));
const preferenceCount = computed(() => (profile.value?.preferences?.length ?? 0)
  + (profile.value?.scene_memories?.length ?? 0) + artistPreferences.value.length
  + songPreferenceGroups.value.reduce((count, group) => count + group.songs.length, 0));

function factorLabel(factor: number) {
  return `×${factor.toFixed(2)}`;
}

function confidenceLabel(confidence: number) {
  return `${Math.round(confidence * 100)}% 置信`;
}

function visibleItems<T>(items: T[], section: string, limit = collapsedItemCount): T[] {
  return expandedSections.value[section] ? items : items.slice(0, limit);
}

function toggleSection(section: string) {
  expandedSections.value[section] = !expandedSections.value[section];
}

async function loadProfile() {
  loading.value = true;
  error.value = "";
  try {
    // 声学档案属于整座曲库；尚未聊天时使用只承载“空个人画像”的匿名会话键，
    // 仍可展示全局覆盖率，个人反馈不会与真实 session 混合。
    profile.value = await getTasteProfile(props.sessionId || "anonymous");
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : "DJ 的记忆暂时读取失败";
  } finally {
    loading.value = false;
  }
}

onMounted(() => { void loadProfile(); });

async function forgetScene(item: SceneMemory) {
  actionKey.value = `scene:${item.id}`;
  error.value = '';
  try {
    await removeSceneMemory(props.sessionId, item.id);
    await loadProfile();
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '这条场景偏好暂时无法撤销';
  } finally {
    actionKey.value = '';
  }
}

async function recalculate() {
  actionKey.value = "recalculate";
  error.value = "";
  try {
    profile.value = await recalculateTasteProfile(props.sessionId || "anonymous");
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : "画像重算失败";
  } finally {
    actionKey.value = "";
  }
}

async function correctSong(song: TasteSong) {
  const key = `${song.source}:${song.id}`;
  actionKey.value = key;
  error.value = "";
  try {
    if (song.source === "task_feedback" && song.task_id) {
      await removeTaskFeedback(props.sessionId, song.task_id, song.id);
    } else if (song.source === "explicit_feedback") {
      await removeTasteFeedback(props.sessionId || "anonymous", song.id);
    } else {
      await removeTasteBehavior(props.sessionId || "anonymous", song.id);
    }
    await loadProfile();
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : "这项判断暂时无法纠正";
  } finally {
    actionKey.value = "";
  }
}

async function resetProfile() {
  if (!window.confirm("只重置偏好画像与收听证据？聊天记录、曲库和声学档案都会保留。")) return;
  actionKey.value = "reset";
  error.value = "";
  try {
    await resetTasteProfile(props.sessionId || "anonymous");
    await loadProfile();
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : "画像重置失败";
  } finally {
    actionKey.value = "";
  }
}
</script>

<template>
  <aside ref="panel" class="side-drawer playlist-drawer taste-drawer radio-panel" aria-label="DJ 记住了什么" tabindex="-1" @keydown="onPanelKeydown">
    <PlayerGlass :corner-radius="24" />
    <header>
      <h2>DJ 记忆</h2>
      <div class="taste-header-actions">
        <button type="button" aria-label="重新读取画像" title="重新读取" :disabled="loading" @click="loadProfile">
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M20 6v5h-5M4 18v-5h5M6.1 9A7 7 0 0 1 18.5 6.5L20 8M4 16l1.5 1.5A7 7 0 0 0 17.9 15" /></svg>
        </button>
        <button type="button" aria-label="关闭画像" @click="$emit('close')"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="m6 6 12 12M18 6 6 18" /></svg></button>
      </div>
    </header>

    <div class="taste-content">
      <p v-if="loading && !profile" class="taste-state" role="status">正在读取 DJ 记忆…</p>
      <div v-if="error" class="taste-state taste-error" role="alert">
        <p>{{ error }}</p>
        <button type="button" @click="loadProfile">再试一次</button>
      </div>

      <template v-if="profile">
        <details class="taste-group" open>
          <summary>
            <h3>我的长期偏好</h3><span class="taste-count">{{ preferenceCount }} 条</span>
          </summary>
          <div class="taste-group-content">
            <MemoryPreferences v-if="profile.preferences" ref="preferencePanel" :session-id="sessionId" :items="profile.preferences" @changed="loadProfile" />
            <p v-else-if="preferenceCount === 0" class="taste-copy">还没有长期偏好。你可以在播放时标记喜欢或减少推荐。</p>

            <details v-if="profile.scene_memories?.length" class="taste-subgroup">
              <summary><span>场景偏好</span><span class="taste-count">{{ profile.scene_memories.length }} 条</span></summary>
              <section aria-label="长期场景偏好" class="taste-list">
                <article v-for="item in profile.scene_memories" :key="item.id" class="taste-card">
                  <div class="taste-card-top"><div><strong>{{ item.song.title }}</strong><span>{{ item.song.artist }} · {{ item.song.album }}</span></div><b>{{ item.scene }} · {{ item.effective ? '避开' : '已暂停' }}</b></div>
                  <details class="taste-record-detail">
                    <summary>查看原话</summary>
                    <p>{{ item.reason }}</p><p>你说：“{{ item.source_text }}”</p>
                  </details>
                  <div class="taste-inline-actions">
                    <button v-if="profile.preferences" class="taste-correct" type="button" :disabled="Boolean(actionKey)" @click="preferencePanel?.editLegacy(item)">修改场景偏好</button>
                    <button class="taste-correct" type="button" :disabled="Boolean(actionKey)" @click="forgetScene(item)">{{ actionKey === `scene:${item.id}` ? '正在撤销…' : '撤销这条场景偏好' }}</button>
                  </div>
                </article>
              </section>
            </details>

            <details v-for="group in songPreferenceGroups" :key="group.key" class="taste-subgroup">
              <summary><span>{{ group.label }}</span><span class="taste-count">{{ group.songs.length }} 首</span></summary>
              <div class="taste-list">
                <article v-for="song in visibleItems(group.songs, group.key)" :key="song.id" class="taste-card">
                  <div class="taste-card-top"><div><strong>{{ song.title || `歌曲 ${song.id}` }}</strong><span>{{ song.artist || "歌手信息已不在歌库" }}<template v-if="song.album"> · {{ song.album }}</template></span></div></div>
                  <details v-if="song.reason" class="taste-record-detail">
                    <summary>查看详情</summary>
                    <p>{{ song.reason }}</p>
                  </details>
                  <button class="taste-correct" type="button" :disabled="Boolean(actionKey)" @click="correctSong(song)">{{ actionKey === `${song.source}:${song.id}` ? "正在撤销…" : "撤销这条偏好" }}</button>
                </article>
                <button v-if="group.songs.length > collapsedItemCount" class="taste-correct" type="button" @click="toggleSection(group.key)">{{ expandedSections[group.key] ? "收起" : "展开全部" }}</button>
              </div>
            </details>

            <details v-if="artistPreferences.length" class="taste-subgroup">
              <summary><span>歌手偏好</span><span class="taste-count">{{ artistPreferences.length }} 位</span></summary>
              <div class="taste-list">
                <article v-for="artist in visibleItems(artistPreferences, 'artists')" :key="`${artist.artist}-${artist.factor}`" class="taste-card">
                  <div class="taste-card-top"><div><strong>{{ artist.artist }}</strong></div><b>{{ artist.factor >= 1 ? '优先推荐' : '减少推荐' }}</b></div>
                  <p>{{ artist.reason }}</p>
                </article>
                <button v-if="artistPreferences.length > collapsedItemCount" class="taste-correct" type="button" @click="toggleSection('artists')">{{ expandedSections.artists ? "收起" : "展开全部" }}</button>
              </div>
            </details>
          </div>
        </details>

        <details v-if="profile.task_feedback?.songs.length" class="taste-group">
          <summary><h3>本轮反馈</h3><span class="taste-count">{{ profile.task_feedback.songs.length }} 条</span></summary>
          <section class="taste-group-content taste-list" aria-label="当前任务不合适">
            <p class="taste-copy">只影响本轮点歌「{{ profile.task_feedback.query }}」。</p>
            <article v-for="song in profile.task_feedback.songs" :key="`task-${song.task_id}-${song.id}`" class="taste-card">
              <div class="taste-card-top"><div><strong>{{ song.title || `歌曲 ${song.id}` }}</strong><span>{{ song.artist || "歌曲资料已不可用" }}</span></div><b>本轮避开</b></div>
              <details v-if="song.reason" class="taste-record-detail">
                <summary>查看详情</summary>
                <p>{{ song.reason }}</p>
              </details>
              <button class="taste-correct" type="button" :disabled="Boolean(actionKey)" @click="correctSong(song)">{{ actionKey === `${song.source}:${song.id}` ? "正在撤销…" : "撤销这次意见" }}</button>
            </article>
          </section>
        </details>

        <details class="taste-group">
          <summary><h3>DJ 从收听中学到的</h3><span class="taste-count">{{ inferenceCount ? `${inferenceCount} 条推断` : '查看统计' }}</span></summary>
          <div class="taste-group-content">
            <div class="taste-summary" role="group" aria-label="收听统计">
              <div><strong>{{ profile.summary.plays ?? 0 }}</strong><span>播放</span></div>
              <div><strong>{{ profile.summary.finishes ?? 0 }}</strong><span>听完</span></div>
              <div><strong>{{ profile.summary.replays ?? 0 }}</strong><span>重播</span></div>
              <div><strong>{{ profile.summary.skips ?? 0 }}</strong><span>跳过或中断</span></div>
            </div>
            <p v-if="!inferenceCount" class="taste-copy">还没有足够的记录形成推断。</p>

            <section v-for="group in listeningGroups" :key="group.key" class="taste-list">
              <h4>{{ group.label }}</h4>
              <article v-for="song in visibleItems(group.songs, group.key)" :key="song.id" class="taste-card">
                <div class="taste-card-top"><div><strong>{{ song.title || `歌曲 ${song.id}` }}</strong><span>{{ song.artist || "歌手信息已不在歌库" }}</span></div></div>
                <p>{{ song.reason }}。{{ group.effect }}。</p>
                <details class="taste-record-detail">
                  <summary>查看推断依据</summary>
                  <p>{{ confidenceLabel(song.confidence) }}，排序权重 {{ factorLabel(song.factor) }}，仅当前会话。</p>
                  <p v-if="song.genre.length || song.mood.length">{{ [...song.genre, ...song.mood].join(' · ') }}</p>
                </details>
                <button class="taste-correct" type="button" :disabled="Boolean(actionKey)" @click="correctSong(song)">{{ actionKey === `${song.source}:${song.id}` ? "正在清除…" : "清除这条推断" }}</button>
              </article>
              <button v-if="group.songs.length > collapsedItemCount" class="taste-correct" type="button" @click="toggleSection(group.key)">{{ expandedSections[group.key] ? "收起" : "展开全部" }}</button>
            </section>

            <details class="taste-subgroup" aria-label="歌曲分析进度">
              <summary><span>歌曲分析进度</span><span class="taste-count">{{ profile.acoustic.coverage_percent.toFixed(1) }}%</span></summary>
              <div class="taste-list">
                <div class="acoustic-progress" role="progressbar" aria-label="歌曲分析覆盖率" :aria-valuenow="profile.acoustic.coverage_percent" aria-valuemin="0" aria-valuemax="100"><i :style="{ width: `${profile.acoustic.coverage_percent}%` }"></i></div>
                <p class="taste-copy">已分析 {{ profile.acoustic.analyzed_songs }} / {{ profile.acoustic.total_songs }} 首，累计有效采样 {{ profile.acoustic.sample_hours.toFixed(2) }} 小时。电台只保存声音摘要。</p>
                <article v-for="song in visibleItems(profile.acoustic.recent_songs, 'acoustic')" :key="song.id" class="taste-card">
                  <div class="taste-card-top"><div><strong>{{ song.title }}</strong><span>{{ song.artist }} · {{ song.observation_count }} 次观察</span></div></div>
                  <p>能量 {{ song.energy_mean.toFixed(2) }} · 低频 {{ Math.round(song.low_ratio * 100) }}% · 明亮度 {{ Math.round(song.brightness * 100) }}% · 瞬态 {{ Math.round(song.beat_density) }}/min</p>
                </article>
                <button v-if="profile.acoustic.recent_songs.length > collapsedItemCount" class="taste-correct" type="button" @click="toggleSection('acoustic')">{{ expandedSections.acoustic ? "收起" : "展开全部" }}</button>
              </div>
            </details>
          </div>
        </details>

        <div class="taste-utilities">
          <details>
            <summary>这些记录怎么用</summary>
            <div class="taste-list taste-copy">
              <p>明确偏好优先于收听推断。长期偏好会在清空聊天后保留，场景偏好只在对应场景使用。</p>
              <p>明确的主动重播、前段跳过和有效听完可轻微影响排序。循环播放和故障中断不计入推断，歌曲分析只描述声音。</p>
              <p v-if="profile.behavior.quality" role="note" aria-label="行为记录依据">
                未计入推断的记录：旧版来源不明 {{ profile.behavior.quality.legacy_events }} 条、循环 {{ profile.behavior.quality.loop_events }} 条、故障相关 {{ profile.behavior.quality.interrupted_events }} 条、其他证据不足 {{ profile.behavior.quality.insufficient_events }} 条。
              </p>
              <div v-for="(boundary, key) in profile.boundaries" :key="key">
                <strong>{{ boundary.label }}</strong><p>{{ boundary.description }}</p>
              </div>
            </div>
          </details>
          <details>
            <summary>管理记忆</summary>
            <div class="taste-list taste-copy">
              <p>重算使用现有收听记录。重置会清除偏好画像与收听证据，保留聊天、曲库和歌曲分析。</p>
              <div class="taste-profile-actions">
                <button type="button" :disabled="Boolean(actionKey)" @click="recalculate">{{ actionKey === "recalculate" ? "正在重算…" : "从原始记录重算" }}</button>
                <button class="danger" type="button" :disabled="Boolean(actionKey)" @click="resetProfile">{{ actionKey === "reset" ? "正在重置…" : "重置偏好画像" }}</button>
              </div>
            </div>
          </details>
        </div>
      </template>
    </div>
  </aside>
</template>
