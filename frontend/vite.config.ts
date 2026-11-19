import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";
import { localDevBoundary, localApiTarget } from "./dev-access.mjs";

const apiTarget = localApiTarget(process.env.PERSONAL_RADIO_DEV_API_PORT);

// 开发时代理 API，生产构建写入 FastAPI 的 static/dist，保持一个部署入口。
export default defineConfig({
  plugins: [localDevBoundary(), vue()],
  base: "/dist/",
  server: {
    host: "127.0.0.1",
    strictPort: true,
    proxy: { "/api": {
      target: apiTarget,
      changeOrigin: true,
      // 已在入口校验开发页面同源；仅将合法代理请求映射成后端自己的来源。
      configure(proxy) {
        proxy.on("proxyReq", (proxyReq, req) => {
          if (req.headers.origin !== undefined) proxyReq.setHeader("Origin", apiTarget);
        });
      },
    } },
  },
  build: { outDir: "../static/dist", emptyOutDir: true },
});
