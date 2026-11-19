import { onBeforeUnmount, onMounted, ref } from "vue";
import { ApiError, cancelQqQr, checkQqQr, createQqQr, getQqStatus, logoutQq } from "../services/api";
import type { MusicAccountProfile } from "../types/radio";

export function useQqAuth(notify: (message: string) => void, onConnected: () => void) {
  const available = ref(false);
  const restoring = ref(true);
  const profile = ref<MusicAccountProfile | null>(null);
  const opened = ref(false);
  const loading = ref(false);
  const image = ref("");
  const status = ref("");
  let revision = 0;
  let timer = 0;
  let id = "";

  function close() {
    revision++;
    opened.value = false;
    loading.value = false;
    window.clearTimeout(timer);
    if (id) void cancelQqQr(id).catch(() => {});
    id = "";
  }

  async function open() {
    if (loading.value) return;
    close();
    opened.value = true;
    loading.value = true;
    image.value = "";
    status.value = "正在获取二维码…";
    await fetchQr(revision, true);
  }

  async function fetchQr(version: number, mayRetry: boolean) {
    try {
      const qr = await createQqQr();
      if (version !== revision) {
        void cancelQqQr(qr.id).catch(() => {});
        return;
      }
      id = qr.id;
      image.value = qr.image;
      loading.value = false;
      status.value = "打开 QQ 音乐 App 扫码";
      const poll = async () => {
        try {
          const result = await checkQqQr(qr.id);
          if (version !== revision) return;
          if (result.state === "confirmed") {
            id = "";
            profile.value = { nickname: result.nickname || "QQ 音乐用户", avatarUrl: result.avatar_url || null };
            close();
            notify("QQ 音乐已登录");
            onConnected();
            return;
          }
          if (result.state === "failed" || result.state === "expired") {
            id = "";
            image.value = "";
            status.value = result.state === "expired" ? "二维码已过期，请刷新" : "QQ 音乐授权失败，请刷新重试";
            return;
          }
          status.value = result.state === "scanned" ? "已扫码，请在手机上确认" : "打开 QQ 音乐 App 扫码";
        } catch (error) {
          if (version !== revision) return;
          status.value = error instanceof Error ? error.message : "扫码状态暂时无法读取，正在重试";
        }
        timer = window.setTimeout(poll, 1800);
      };
      timer = window.setTimeout(poll, 1000);
    } catch (error) {
      if (version !== revision) return;
      if (mayRetry && error instanceof ApiError && error.retryAfterMs && (error.status === 429 || error.status === 502)) {
        status.value = `QQ 音乐暂时未返回二维码，${error.retryAfterMs / 1000} 秒后自动重试`;
        timer = window.setTimeout(() => {
          status.value = "正在重新获取二维码…";
          void fetchQr(version, false);
        }, error.retryAfterMs + 250);
        return;
      }
      loading.value = false;
      status.value = error instanceof Error ? error.message : "二维码获取失败，请刷新重试";
    }
  }

  async function logout() {
    close();
    const version = revision;
    try {
      await logoutQq();
      if (version !== revision) return;
      profile.value = null;
      notify("QQ 音乐已退出");
    } catch (error) {
      if (version === revision) notify(error instanceof Error ? error.message : "QQ 音乐退出失败");
    }
  }

  onMounted(async () => {
    const version = revision;
    try {
      const result = await getQqStatus();
      if (version !== revision) return;
      available.value = Boolean(result.available);
      if (result.logged_in) profile.value = { nickname: result.nickname || "QQ 音乐用户", avatarUrl: result.avatar_url || null };
      if (result.storage_warning) notify(result.storage_warning);
    } catch (error) {
      if (version === revision && error instanceof Error) notify(error.message);
    } finally {
      restoring.value = false;
    }
  });
  onBeforeUnmount(close);
  return { available, restoring, profile, opened, loading, image, status, open, close, logout };
}
