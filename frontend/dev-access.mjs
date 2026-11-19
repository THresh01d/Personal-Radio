/** 开发代理也要先拒绝跨源请求，不能通过改写 Origin 绕过后端保护。 */
export function localApiTarget(value = "8000") {
  if (!/^\d+$/.test(value) || Number(value) < 1 || Number(value) > 65535) {
    throw new Error("PERSONAL_RADIO_DEV_API_PORT 必须是有效本机端口");
  }
  return `http://127.0.0.1:${Number(value)}`;
}

export function allowDevRequest(req, port) {
  const peer = req.socket?.remoteAddress;
  if (!["127.0.0.1", "::1", "::ffff:127.0.0.1"].includes(peer)) return false;
  try {
    const host = new URL(`http://${req.headers.host}`);
    if (!["localhost", "127.0.0.1", "[::1]"].includes(host.hostname)
      || Number(host.port) !== port || host.username || host.password || host.pathname !== "/"
      || host.search || host.hash) return false;
    const origin = req.headers.origin;
    if (origin !== undefined && origin !== host.origin) return false;
    return [undefined, "none", "same-origin"].includes(req.headers["sec-fetch-site"]);
  } catch { return false; }
}

export function localDevBoundary() {
  return {
    name: "personal-radio-local-development",
    configureServer(server) {
      server.middlewares.use((req, res, next) => {
        const port = server.httpServer?.address()?.port;
        if (!allowDevRequest(req, port)) {
          res.statusCode = 403;
          res.end("仅允许本机同源开发页面访问");
          return;
        }
        next();
      });
    },
  };
}
