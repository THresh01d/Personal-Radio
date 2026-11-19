<script setup lang="ts">
import PlayerGlass from "../player/PlayerGlass.vue";
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import { usePanelFocus } from "../../composables/usePanelFocus";
import { chatGPTAccountAction, getChatGPTModels, getChatGPTStatus, getDiagnostics, getLLMSettings, saveLLMSettings, testLLMSettings } from "../../services/api";
import type { ChatGPTStatus, LLMProviderId, LLMProviderOption, RequestDiagnostics } from "../../types/radio";

const emit = defineEmits<{
  close: [];
  notify: [message: string];
  saved: [usingChatGPT: boolean];
}>();
const card = ref<HTMLElement | null>(null);
const onPanelKeydown = usePanelFocus(card, () => emit("close"), true);

const provider = ref<LLMProviderId>("offline");
const model = ref("");
const baseUrl = ref("");
const apiKey = ref("");
const hasApiKey = ref(false);
const savedTarget = ref({ provider: "", baseUrl: "" });
const providers = ref<LLMProviderOption[]>([]);
const loading = ref(true);
const testing = ref(false);
const saving = ref(false);
const result = ref("");
const capabilities = ref<{ chat: string; tools: string; program_json: string }>();
const error = ref("");
const chatgpt = ref<ChatGPTStatus>();
const accountModels = ref<{ id: string; label: string }[]>([]);
const accountBusy = ref(false);
const authorizing = ref(false);
const accountId = ref("");
const showPlanWelcome = ref(false);
const planWelcomeKey = "personal-radio:chatgpt-plan-intro-seen";
let poll: number | undefined;
let authWindow: Window | null = null;
let disposed = false;
let savedAccount = "";
const diagnostics = ref<RequestDiagnostics | null>(null);
const diagnosticLoading = ref(false);
const diagnosticError = ref("");
const capabilityNames = { chat: "对话", tools: "工具调用", program_json: "结构化稿件" };
const capabilityStatuses: Record<string, string> = {
  passed: "通过", invalid: "返回内容不符合要求", unsupported: "模型拒绝此功能",
  llm_timeout: "超时", llm_network: "网络失败", llm_http: "服务拒绝", llm_invalid: "返回格式不可用", llm_busy: "模型繁忙",
  chatgpt_usage_limit: "ChatGPT 套餐额度暂不可用",
};
const stageNames: Record<string, string> = { model_load: "模型加载/等待", embedding: "向量编码", library_load: "曲库加载", retrieval: "检索（含编码）", llm: "模型调用/配置", gateway_ready: "网关就绪", gateway: "网关请求", tts: "播报（含缓存）", tts_generate: "语音合成" };
const categoryNames: Record<string, string> = { recommendation: "推荐", ncm: "网易云", tts: "播报", settings: "AI 设置", api: "本机接口" };

async function refreshDiagnostics() {
  if (diagnosticLoading.value) return;
  diagnosticLoading.value = true;
  diagnosticError.value = "";
  try { diagnostics.value = await getDiagnostics(); }
  catch (reason) { diagnosticError.value = reason instanceof Error ? reason.message : "诊断读取失败"; }
  finally { diagnosticLoading.value = false; }
}

const names: Record<LLMProviderId, string> = {
  offline: "离线基础模式",
  deepseek: "DeepSeek",
  doubao: "豆包 · 火山方舟",
  openai_compatible: "自定义兼容接口",
  ollama: "Ollama 本地模型",
  chatgpt: "ChatGPT 套餐",
};

const current = computed(() => providers.value.find((item) => item.id === provider.value));
const needsKey = computed(() => current.value?.needs_api_key ?? false);
// 编辑目标后不能仍展示“留空复用”，避免误导用户将旧 Key 绑定到新服务。
const hasCurrentKey = computed(() => (hasApiKey.value && provider.value === savedTarget.value.provider
  && baseUrl.value === savedTarget.value.baseUrl) || Boolean(current.value?.has_saved_key));
const hasChatGPTModel = computed(() => provider.value !== "chatgpt" ||
  (Boolean(accountId.value) && accountModels.value.some((option) => option.id === model.value)));

function stopPoll() {
  if (poll !== undefined) window.clearTimeout(poll);
  poll = undefined;
  authWindow?.close();
  authWindow = null;
  authorizing.value = false;
}

async function readAccount(loadModels = true, requested = "") {
  const state = await getChatGPTStatus();
  if (disposed) return;
  chatgpt.value = state;
  accountId.value = state.profiles.some((account) => account.id === requested && account.connected)
    ? requested : state.account_id || "";
  if (!loadModels) return;
  accountModels.value = [];
  if (accountId.value) {
    accountModels.value = await getChatGPTModels(accountId.value);
    if (!accountModels.value.some((option) => option.id === model.value)) model.value = accountModels.value[0]?.id || "";
    if (accountModels.value.length === 0) error.value = "这个 ChatGPT 账号没有返回可选模型。";
  }
}

async function accountAction(work: () => Promise<void>) {
  accountBusy.value = true;
  error.value = "";
  result.value = "";
  capabilities.value = undefined;
  try { await work(); }
  catch (reason) { error.value = reason instanceof Error ? reason.message : "ChatGPT 账号操作失败"; }
  finally { accountBusy.value = false; }
}

function waitForLogin() {
  poll = window.setTimeout(async () => {
    if (!authorizing.value || disposed) return;
    try {
      await readAccount(false);
      if (!authorizing.value || disposed) return;
      if (chatgpt.value?.status === "pending" || chatgpt.value?.status === "exchanging") {
        // 官方登录页可能切断窗口引用，不能把 closed 当作用户取消。
        waitForLogin();
        return;
      }
      stopPoll();
      if (chatgpt.value?.status === "connected") {
        showPlanWelcome.value = !window.localStorage.getItem(planWelcomeKey);
        await accountAction(async () => {
          await readAccount();
          if (accountModels.value.length) result.value = "授权成功。保存后，合格的 DJ 请求将消耗你的 ChatGPT 套餐额度。";
        });
      } else {
        error.value = chatgpt.value?.message || "本次 ChatGPT 授权已结束。";
      }
    } catch (reason) {
      stopPoll();
      void chatGPTAccountAction("cancel").catch(() => undefined);
      error.value = reason instanceof Error ? reason.message : "无法确认 ChatGPT 授权结果";
    }
  }, 850);
}

function dismissPlanWelcome() {
  showPlanWelcome.value = false;
  window.localStorage.setItem(planWelcomeKey, "1");
}

async function connect(profileId?: string) {
  const desktop = window.personalRadioDesktop;
  // 网页需要在点击时创建弹窗，桌面版交给系统浏览器授权。
  const browser = desktop ? null : window.open("about:blank", "_blank");
  if (!desktop && !browser) { error.value = "请允许电台打开登录窗口后重试。"; return; }
  if (browser) browser.opener = null;
  authWindow = browser;
  authorizing.value = true;
  await accountAction(async () => {
    let started = false;
    try {
      const reply = await chatGPTAccountAction<{ authorize_url: string }>("start", profileId);
      started = true;
      if (disposed || !authorizing.value) {
        await chatGPTAccountAction("cancel");
        return;
      }
      const url = new URL(reply.authorize_url);
      if (url.origin !== "https://auth.openai.com") throw new Error("未收到官方 ChatGPT 登录地址。");
      if (desktop) await desktop.openChatGPTAuthorization(url.href);
      else browser?.location.replace(url.href);
      waitForLogin();
    } catch (reason) {
      stopPoll();
      if (started) void chatGPTAccountAction("cancel").catch(() => undefined);
      throw reason;
    }
  });
}

async function cancelLogin() {
  stopPoll();
  await accountAction(async () => { await chatGPTAccountAction("cancel"); await readAccount(false); });
}

function selectAccount(id: string) {
  void accountAction(async () => { await chatGPTAccountAction("select", id); await readAccount(); });
}

function disconnectAccount(id: string) {
  void accountAction(async () => {
    const reply = await chatGPTAccountAction<{ revoked: boolean }>("disconnect", id);
    await readAccount();
    if (id === savedAccount) emit("saved", false);
    result.value = reply.revoked ? "已退出该 ChatGPT 账号。" : "已在本机退出，未确认远端撤销。可在 ChatGPT 设置中撤销。";
  });
}

async function load() {
  loading.value = true;
  error.value = "";
  try {
    const settings = await getLLMSettings();
    providers.value = settings.providers;
    provider.value = settings.provider;
    model.value = settings.model;
    baseUrl.value = settings.base_url;
    hasApiKey.value = settings.has_api_key;
    savedTarget.value = { provider: settings.provider, baseUrl: settings.base_url };
    savedAccount = settings.chatgpt_profile_id || "";
    if (settings.provider === "chatgpt") await accountAction(() => readAccount(true, savedAccount));
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : "AI 设置读取失败";
  } finally {
    loading.value = false;
  }
}

function selectProvider(id: LLMProviderId) {
  if (id === provider.value) return;
  provider.value = id;
  const option = providers.value.find((item) => item.id === id);
  model.value = option?.default_model || "";
  baseUrl.value = option?.default_base_url || "";
  apiKey.value = "";
  result.value = "";
  capabilities.value = undefined;
  error.value = "";
  if (id === "chatgpt") void accountAction(() => readAccount(true, savedAccount));
}

function payload() {
  return {
    provider: provider.value,
    model: model.value,
    base_url: baseUrl.value,
    api_key: apiKey.value || undefined,
    chatgpt_profile_id: provider.value === "chatgpt" ? accountId.value || undefined : undefined,
  };
}

async function test() {
  testing.value = true;
  result.value = "";
  capabilities.value = undefined;
  error.value = "";
  try {
    const response = await testLLMSettings(payload(), true);
    result.value = response.message || "连接成功";
    capabilities.value = response.capabilities;
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : "连接失败";
  } finally {
    testing.value = false;
  }
}

async function save() {
  saving.value = true;
  result.value = "";
  error.value = "";
  try {
    const settings = await saveLLMSettings(payload());
    hasApiKey.value = settings.has_api_key;
    apiKey.value = "";
    emit("saved", settings.provider === "chatgpt" && settings.has_api_key);
    emit("notify", provider.value === "offline" ? "已启用离线基础推荐" : `AI DJ 已切换到 ${names[provider.value]}`);
    emit("close");
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : "设置保存失败";
  } finally {
    saving.value = false;
  }
}

onMounted(() => { void load(); });
watch([model, baseUrl, apiKey], () => {
  if (testing.value) return;
  result.value = "";
  capabilities.value = undefined;
});
onUnmounted(() => {
  disposed = true;
  if (authorizing.value) void chatGPTAccountAction("cancel").catch(() => undefined);
  stopPoll();
});
</script>

<template>
  <section class="qr-modal ai-settings-modal" role="dialog" aria-modal="true" aria-label="AI DJ 设置" @click.self="$emit('close')">
    <div ref="card" class="radio-panel ai-settings-card" tabindex="-1" @keydown="onPanelKeydown">
      <PlayerGlass :corner-radius="24" />
      <header>
        <h2>AI 设置</h2>
        <button type="button" aria-label="关闭" @click="$emit('close')"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="m6 6 12 12M18 6 6 18" /></svg></button>
      </header>

      <div class="ai-settings-content">
        <p class="ai-settings-intro">歌曲检索始终在本地完成。云模型负责理解需求和编写 DJ 话术。</p>
        <div v-if="loading" class="library-loading">正在读取本机设置…</div>
        <template v-else>
          <div class="provider-grid">
            <button
              v-for="option in providers"
              :key="option.id"
              type="button"
              :class="{ active: provider === option.id }"
              :aria-pressed="provider === option.id"
              :disabled="testing || saving || accountBusy || authorizing"
              @click="selectProvider(option.id)"
            >
              <strong>{{ names[option.id] }}</strong>
              <span v-if="option.id === 'offline'">零配置 · 永远可用</span>
              <span v-else-if="option.id === 'ollama'">数据不出本机</span>
              <span v-else-if="option.id === 'chatgpt'">登录后使用符合条件的套餐</span>
              <span v-else>{{ option.needs_api_key ? "使用自己的 API Key" : "无需 Key" }}</span>
            </button>
          </div>

          <div v-if="provider === 'chatgpt'" class="provider-fields">
            <p>符合条件的 ChatGPT Plus / Pro 用户可使用套餐额度，无需安装 Codex 或填写 Key。此功能目前处于预览。</p>
            <aside v-if="showPlanWelcome" class="offline-note" role="status">
              <strong>已授权使用你的 ChatGPT 套餐</strong>
              <p>保存后，电台支持的 AI 请求会计入你的 ChatGPT 套餐用量。你可以在 ChatGPT 设置中查看和管理。</p>
              <button class="test-button" type="button" @click="dismissPlanWelcome">知道了</button>
            </aside>
            <p v-if="authorizing" role="status">请在打开的 ChatGPT 页面完成授权。</p>
            <div class="chatgpt-actions">
              <button v-if="authorizing" type="button" class="test-button" @click="cancelLogin">取消登录</button>
              <button v-else type="button" class="test-button" :disabled="accountBusy || testing || saving" @click="connect()">
                Continue with ChatGPT
              </button>
              <a href="https://chatgpt.com/settings/usage" target="_blank" rel="noopener noreferrer">查看 ChatGPT 用量</a>
            </div>
            <div v-for="account in chatgpt?.profiles" :key="account.id" class="chatgpt-actions">
              <span>{{ account.label }}{{ account.id === accountId ? "（当前选择）" : "" }}</span>
              <button v-if="account.connected && account.id !== accountId" class="test-button" type="button"
                :disabled="accountBusy || authorizing || testing || saving" @click="selectAccount(account.id)">选择</button>
              <button v-if="account.connected" class="test-button" type="button"
                :disabled="accountBusy || authorizing || testing || saving" @click="disconnectAccount(account.id)">退出</button>
              <button v-else class="test-button" type="button"
                :disabled="accountBusy || authorizing || testing || saving" @click="connect(account.id)">重新登录</button>
            </div>
            <label v-if="accountModels.length"><span>模型</span>
              <select v-model="model" :disabled="testing || saving || accountBusy || authorizing">
                <option v-for="option in accountModels" :key="option.id" :value="option.id">{{ option.label }}</option>
              </select>
            </label>
          </div>

          <div v-else-if="provider !== 'offline'" class="provider-fields">
            <label><span>模型名称</span><input v-model.trim="model" :disabled="testing || saving" type="text" autocomplete="off" placeholder="例如 deepseek-flash" /></label>
            <label><span>API 地址</span><input v-model.trim="baseUrl" :disabled="testing || saving" type="url" :readonly="provider === 'deepseek' || provider === 'doubao'" autocomplete="off" placeholder="https://…" /></label>
            <small v-if="provider === 'openai_compatible'">仅向你信任的地址填写 Key；远程地址需 HTTPS。Key 绑定此地址，改变地址后需重新填写；旧版未绑定的自定义 Key 不自动复用。</small>
            <label v-if="needsKey">
              <span>API Key <i v-if="hasCurrentKey">已安全保存，留空保持不变</i></span>
              <input v-model.trim="apiKey" :disabled="testing || saving" type="password" autocomplete="new-password" :placeholder="hasCurrentKey ? '••••••••••••' : '请填写自己的 API Key'" />
            </label>
          </div>

          <aside v-if="provider === 'offline'" class="offline-note">
            <strong>没有 Key 也不影响听歌</strong>
            <p>DJ 会从本地曲库选歌、记住你的偏好，并用简短话术介绍歌曲。</p>
          </aside>

          <details class="request-diagnostics">
            <summary>请求诊断 · 为什么慢 / 为什么降级</summary>
            <p>仅保存本进程最近 100 条内存记录，不包含消息、歌曲、地址或凭据。手动刷新，不自动轮询。</p>
            <button type="button" :disabled="diagnosticLoading" @click="refreshDiagnostics">{{ diagnosticLoading ? '正在读取…' : '刷新诊断' }}</button>
            <p v-if="diagnosticError" role="status">{{ diagnosticError }}</p>
            <template v-if="diagnostics">
              <p>进行中 {{ diagnostics.active.length }} · 最近完成 {{ diagnostics.recent.length }}（展示最近 8 条）</p>
              <article v-for="item in [...diagnostics.active, ...diagnostics.recent.slice(-8).reverse()]" :key="item.request_id">
                <strong>{{ categoryNames[item.category] || '本机接口' }} · {{ item.status === null ? '进行中' : item.status }} · {{ Math.round(item.elapsed_ms) }} ms</strong>
                <small>请求 {{ item.request_id }}<template v-if="item.codes.length"> · {{ item.codes.join(' / ') }}</template></small>
                <p v-if="item.running_stages.length">当前：{{ item.running_stages.map((name) => stageNames[name] || name).join('、') }}</p>
                <p v-for="(timing, name) in item.stages" :key="name">{{ stageNames[name] || name }}：{{ Math.round(timing.elapsed_ms) }} ms / {{ timing.calls }} 次</p>
              </article>
              <p>阶段可能嵌套，不能相加当作总耗时。取消等待不代表后台线程或外部模型已停止；后台资料任务不在本请求计时范围。</p>
            </template>
          </details>
        </template>
      </div>
      <footer v-if="!loading" class="settings-actions">
        <div v-if="result || error || capabilities" class="settings-feedback">
          <p v-if="result" class="settings-result" role="status">{{ result }}</p>
          <ul v-if="capabilities" class="settings-result">
            <li v-for="(status, name) in capabilities" :key="name">{{ capabilityNames[name] }} · {{ capabilityStatuses[status] || "未通过" }}</li>
          </ul>
          <p v-if="error" class="settings-result error" role="alert">{{ error }}</p>
        </div>
        <div class="settings-action-row">
          <p v-if="provider !== 'offline' && provider !== 'ollama'" class="settings-test-cost">测试可能消耗所选服务的额度。</p>
          <button v-if="provider !== 'offline'" class="test-button" type="button" :disabled="testing || saving || accountBusy || authorizing || !hasChatGPTModel" @click="test">{{ testing ? "正在测试…" : "测试连接" }}</button>
          <button class="save-button" type="button" :disabled="testing || saving || accountBusy || authorizing || !hasChatGPTModel" @click="save">{{ saving ? "正在保存…" : "保存并使用" }}</button>
        </div>
      </footer>
    </div>
  </section>
</template>

<style scoped>
.chatgpt-actions { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; }
.chatgpt-actions span { overflow-wrap: anywhere; }
.chatgpt-actions a { color: var(--text); font-size: 12px; text-underline-offset: 3px; }
.settings-test-cost { margin: 0 auto 0 0; font-size: 11px; color: #adb8c8; }
.request-diagnostics { margin-top: 16px; font-size: 12px; line-height: 1.6; }
.request-diagnostics summary { cursor: pointer; color: var(--text); }
.request-diagnostics article { border-top: 1px solid var(--line); padding: 10px 0; }
.request-diagnostics small { display: block; overflow-wrap: anywhere; color: var(--muted); }
.request-diagnostics p { margin: 5px 0; }
.request-diagnostics button { padding: 7px 12px; border: 1px solid var(--line-strong); border-radius: 999px; background: var(--panel-fill); color: var(--text); }
</style>
