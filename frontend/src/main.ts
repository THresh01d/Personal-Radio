import { createApp } from "vue";
import App from "./App.vue";
import "./style.css";

// 首次同源握手让 HttpOnly Cookie 可用于 fetch 和原生 Audio；不把凭证交给 JS 存储。
async function start() {
  try {
    const response = await fetch("/api/local-access", { credentials: "same-origin", cache: "no-store", signal: AbortSignal.timeout(5_000) });
    if (!response.ok) throw new Error("本机访问验证失败");
    createApp(App).mount("#app");
  } catch {
    const root = document.getElementById("app");
    if (root) root.textContent = "本机电台连接失败，请确认服务已启动后刷新页面。";
  }
}
void start();
