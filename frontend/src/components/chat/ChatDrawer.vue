<script setup lang="ts">
import { computed, nextTick, ref, watch } from "vue";
import { usePanelFocus } from "../../composables/usePanelFocus";
import PlayerGlass from "../player/PlayerGlass.vue";
import type { AdjustmentOperation, ChatMessage, DJProgram } from "../../types/radio";

const props = defineProps<{
  messages: ChatMessage[];
  loading: boolean;
  loadingHint?: string;
  canAdjust?: boolean;
  djProgram?: DJProgram | null;
  djPlaybackNotices?: Record<string, string>;
  usesChatGPTPlan?: boolean;
}>();

const emit = defineEmits<{
  close: [];
  send: [message: string];
  adjust: [operation: AdjustmentOperation];
}>();

const input = ref("");
const inputElement = ref<HTMLTextAreaElement | null>(null);
const suggestions = ["雨天，温柔一点", "深夜，放松一下", "来点有劲的"];
const panel = ref<HTMLElement | null>(null);
const onPanelKeydown = usePanelFocus(panel, () => emit("close"));
const messagesElement = ref<HTMLElement | null>(null);
const expandedMessages = ref<Set<number>>(new Set());
const programDetails = computed(() => {
  const program = props.djProgram;
  const openingInChat = program?.message_id !== undefined
    && props.messages.some((message) => message.id === program.message_id);
  return (program?.segments || []).filter((segment) => segment.position !== 0 || !openingInChat);
});
const programNotices = computed(() => {
  const program = props.djProgram;
  if (!program) return [];
  const notices = new Set<string>();
  const playback = props.djPlaybackNotices?.[program.batch_id];
  if (playback) notices.add(playback);
  if (program.opening_priority && program.opening_status === 'missing') {
    notices.add('本轮没有通过校验的开场，保留的介绍安排在后续自然曲间。');
  }
  return [...notices];
});

function paragraphs(content: string) {
  return content.split(/\n{2,}/).map((part) => part.trim()).filter(Boolean);
}

function copyNotice(program: DJProgram) {
  if (!program.program_version) return '';
  if (program.copy_status === 'pending' || program.copy_status === 'writing') return program.opening_priority ? '音乐先播，DJ 正在准备开场；起播后最多等待45秒，暂停时停止计时。' : '正在准备曲间节目，音乐继续。';
  if (program.copy_status === 'quiet') return '这一轮暂时没有需要展开的内容，先把时间留给音乐。';
  if (program.copy_status === 'disabled') return '按你的介绍偏好，这一轮不安排曲间节目。';
  const reasons: Record<string, string> = {
    offline: '当前为离线模式，未生成 AI 节目', llm_unconfigured: 'AI 尚未配置',
    llm_timeout: 'AI 写作超时', llm_network: 'AI 网络请求未完成', llm_http: 'AI 服务未完成写作请求',
    chatgpt_usage_limit: 'ChatGPT 套餐额度暂不可用',
    llm_invalid: 'AI 返回内容无法解析', invalid_format: 'AI 返回格式无法解析',
    missing_segments: 'AI 没有返回可安排的节目', empty_text: '部分正文为空',
    invalid_segment: '部分节目结构不完整', invalid_position: '部分节目位置不属于当前队列或重复',
    opening_context: '开场依赖已听前曲或单曲指代，未用于本轮开头', invalid_source: '部分节目引用了未提供的来源', unsupported_claim: '部分内容未通过作品事实或听众处境检查',
    opening_biography: '整轮开场改为简短接话',
    text_too_long: '部分正文过长且无法保留完整句子', text_shortened: '部分正文保留完整句子后缩短',
    writer_interrupted: '上次写作被中断，本批未自动重复调用', too_many_segments: '只保留最多三段节目',
    writer_unavailable: 'AI 写作暂时不可用',
    program_busy: '节目准备繁忙，先继续音乐', program_unavailable: '节目准备任务未能启动',
    background_unavailable: '背景资料查询未完成，本轮节目未能准备好',
    program_wait_timeout: '暂未取得节目，先继续音乐；停止等待不代表后台写作已取消',
    program_read_failed: '暂时无法读取节目，先继续音乐；没有自动重复调用AI',
  };
  const messages = new Set((program.copy_reasons || []).map((code) => {
    if (program.copy_source === 'reference_evidence' && ['unsupported_claim', 'invalid_source'].includes(code)) {
      return program.segments?.some(segment => segment.position > 0)
        ? '部分曲间介绍未采用（未通过内容检查）'
        : '曲间介绍未采用（未通过内容检查）';
    }
    return reasons[code] || '部分节目未能准备完成';
  }));
  return [...messages].join('；');
}

function isLongMessage(message: ChatMessage) {
  return message.role === "assistant" && (message.content.length > 260 || paragraphs(message.content).length > 3);
}

function toggleMessage(index: number) {
  const next = new Set(expandedMessages.value);
  if (next.has(index)) next.delete(index);
  else next.add(index);
  expandedMessages.value = next;
}

function submit() {
  const message = input.value.trim();
  if (!message || props.loading) return;
  emit("send", message);
  input.value = "";
}

function useSuggestion(value: string) {
  // 轻点只填写草稿，用户确认发送后才发起推荐请求。
  input.value = value;
  inputElement.value?.focus();
}

function onKeydown(event: KeyboardEvent) {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    submit();
  }
}

watch(() => [props.messages.length, props.loading], async () => {
  await nextTick();
  messagesElement.value?.scrollTo({ top: messagesElement.value.scrollHeight, behavior: "smooth" });
});
</script>

<template>
  <aside ref="panel" class="side-drawer chat-drawer radio-panel" aria-label="DJ 对话" tabindex="-1" @keydown="onPanelKeydown">
    <PlayerGlass :corner-radius="30" />
    <header>
      <h2>DJ 对话</h2>
      <button type="button" aria-label="关闭对话" @click="$emit('close')"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="m6 6 12 12M18 6 6 18" /></svg></button>
    </header>
    <div ref="messagesElement" class="messages">
      <div v-if="!messages.length" class="conversation-welcome">
        <div class="welcome-record" aria-hidden="true"><i></i><span></span></div>
        <h3>想听什么，告诉 DJ</h3>
        <div class="conversation-suggestions"><button v-for="suggestion in suggestions" :key="suggestion" type="button" @click="useSuggestion(suggestion)">{{ suggestion }} <span aria-hidden="true">↗</span></button></div>
      </div>
      <article v-for="(message, index) in messages" :key="index" :class="message.role"
        :aria-label="message.role === 'user' ? '你的消息' : 'DJ 回复'" :style="{ '--entry-delay': `${Math.min(index, 5) * 28}ms` }">
        <div class="message-body">
          <div class="message-copy" :class="{ collapsed: isLongMessage(message) && !expandedMessages.has(index) }">
            <p v-for="(paragraph, paragraphIndex) in paragraphs(message.content)" :key="paragraphIndex">{{ paragraph }}</p>
          </div>
          <p v-for="notice in [...(message.notices || []), ...(message.diagnostic?.notices || [])].filter(text => text !== message.content)" :key="notice" class="request-notice" role="status">{{ notice }}</p>
          <details v-if="message.diagnostic" class="request-summary">
            <summary>诊断</summary>
            <p>请求编号 <code>{{ message.diagnostic.request_id }}</code></p>
            <p>响应准备 {{ Math.round(message.diagnostic.elapsed_ms) }} ms；完整耗时以 AI 设置里的记录为准。</p>
            <p v-if="message.diagnostic.codes.length">原因：{{ message.diagnostic.codes.join(' / ') }}</p>
          </details>
          <button v-if="isLongMessage(message)" class="message-expand" type="button" @click="toggleMessage(index)">
            {{ expandedMessages.has(index) ? "收起完整回复 ↑" : "展开完整回复 ↓" }}
          </button>
        </div>
      </article>
      <p v-if="loading" class="loading" role="status">{{ loadingHint || 'DJ 正在翻唱片' }}<span>···</span></p>
      <p v-for="notice in programNotices" :key="notice" class="request-notice" role="status">{{ notice }}</p>
      <details v-if="djProgram && !djProgram.confirmation_only" class="dj-program">
        <summary>本轮 DJ 节目 · {{ djProgram.status === 'pending' ? '节目准备中' : djProgram.program_version ? (djProgram.segments?.length || 0) + ' 段节目' : djProgram.mode === 'sourced' ? '有来源资料' : '简短介绍' }}</summary>
        <template v-if="djProgram.program_version">
          <p v-for="segment in programDetails" :key="segment.id">{{ segment.text }}</p>
          <p v-if="copyNotice(djProgram)" class="dj-source-note" role="status">{{ copyNotice(djProgram) }}</p>
        </template>
        <p v-else>{{ djProgram.text }}</p>
        <p class="dj-source-note">开场伴随本轮第一首实际播放的歌。切歌会停掉旧播报，目标歌曲有独立介绍时可接着播，也可以单独跳过 DJ。</p>
        <template v-if="djProgram.sources.length">
          <div v-for="source in djProgram.sources" :key="source.id" class="dj-source">
            <p>{{ source.text }}</p>
            <a :href="source.url" target="_blank" rel="noopener noreferrer">外部引用 ↗</a>
            <a :href="source.entity_url" target="_blank" rel="noopener noreferrer">作品字段 ↗</a>
            <small>{{ source.source }}。按来源原意介绍，资料仍可能有误。</small>
          </div>
        </template>
        <p v-else-if="djProgram.status === 'pending'" class="dj-source-note">正在核对背景资料，可以先播放音乐。</p>
        <p v-else-if="!djProgram.program_version && djProgram.status === 'fallback'" class="dj-source-note">背景资料暂时不可用，先按曲库信息介绍，不推测创作经历。</p>
        <p v-else-if="!djProgram.program_version" class="dj-source-note">本轮没有可用的背景资料，先按曲库信息介绍，不推测创作经历。</p>
      </details>
    </div>
    <div v-if="canAdjust" class="recommend-adjust" role="group" aria-label="只调整这一轮，不记成长期偏好">
      <small>只调整这一轮</small>
      <button type="button" :disabled="loading" @click="$emit('adjust', 'gentler')">更温柔一点</button>
      <button type="button" :disabled="loading" @click="$emit('adjust', 'energize')">更有劲一点</button>
      <button type="button" :disabled="loading" @click="$emit('adjust', 'other-artists')">换些歌手</button>
      <button type="button" :disabled="loading" @click="$emit('adjust', 'refresh')">保持方向，换一批</button>
    </div>
    <form class="composer" @submit.prevent="submit">
      <textarea ref="inputElement" v-model="input" rows="2" aria-label="告诉 DJ 你想听什么" placeholder="说说你现在想听什么…" @keydown="onKeydown"></textarea>
      <button type="submit" aria-label="发送" aria-description="Enter 发送，Shift + Enter 换行" title="Enter 发送 · Shift + Enter 换行" :disabled="loading || !input.trim()"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 19V5m-6 6 6-6 6 6" /></svg></button>
    </form>
    <p v-if="usesChatGPTPlan" class="composer-hint">
      <a href="https://chatgpt.com/settings/usage" target="_blank" rel="noopener noreferrer">使用 ChatGPT 套餐 · 查看用量</a>
    </p>
  </aside>
</template>

<style scoped>
.composer-hint a { color: inherit; text-decoration: underline; text-underline-offset: 2px; }
.request-notice { color: #b8bec8; font-size: 12px; line-height: 1.7; border-left: 2px solid #747e8d; padding-left: 8px; }
.request-summary { font-size: 11px; color: #9ba6b4; margin: 4px 0 0; overflow-wrap: anywhere; }
.request-summary summary { width: fit-content; padding: 2px 0; cursor: pointer; }
.request-summary summary:hover { color: #e0e6eb; }
.request-summary[open] { border-top: 1px solid #b5c3ce26; padding-top: 4px; }
.request-summary p { font-size: inherit; color: inherit; }
</style>
