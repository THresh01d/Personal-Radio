import { onBeforeUnmount, onMounted, watch, type Ref } from "vue";
import { getHotkeys, renewLocalAccess, setHotkeyTarget } from "../services/api";
import { hotkeyActions, type HotkeyAction } from "../types/hotkeys";

export function useGlobalHotkeys(playing: Ref<boolean>, controls: Record<HotkeyAction, () => void>) {
  const tab = crypto.randomUUID();
  const lifecycle = new AbortController();
  let source: EventSource | null = null;
  let retry = 0;
  let retryDelay = 1000;
  let usedAt = Date.now();

  function sendTarget() {
    if (source?.readyState === EventSource.OPEN) {
      void setHotkeyTarget(tab, playing.value, usedAt, lifecycle.signal).catch(() => {});
    }
  }

  function refreshTarget() {
    usedAt = Date.now();
    sendTarget();
  }

  async function connect() {
    try {
      await renewLocalAccess(lifecycle.signal);
      if (!(await getHotkeys(lifecycle.signal)).supported || lifecycle.signal.aborted) return;
      const query = new URLSearchParams({ tab, playing: String(playing.value), used_at: String(usedAt) });
      source = new EventSource(`/api/hotkeys/events?${query}`);
      source.addEventListener("ready", () => { retryDelay = 1000; sendTarget(); });
      source.onmessage = ({ data }) => {
        if (hotkeyActions.includes(data)) controls[data as HotkeyAction]();
      };
      source.onerror = () => {
        // 服务端定期换流。浏览器会重连，只有凭证失效等终止情况需要重新取凭证。
        if (source?.readyState === EventSource.CLOSED) reconnect();
      };
    } catch {
      reconnect();
    }
  }

  function reconnect() {
    source?.close();
    source = null;
    if (lifecycle.signal.aborted) return;
    retry = window.setTimeout(() => { void connect(); }, retryDelay);
    retryDelay = Math.min(retryDelay * 2, 15000);
  }

  watch(playing, refreshTarget);
  onMounted(() => {
    window.addEventListener("focus", refreshTarget);
    void connect();
  });
  onBeforeUnmount(() => {
    lifecycle.abort();
    window.clearTimeout(retry);
    source?.close();
    window.removeEventListener("focus", refreshTarget);
  });
}
