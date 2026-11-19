import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { runInNewContext } from "node:vm";
import { test } from "node:test";
import { allowDevRequest, localApiTarget } from "../dev-access.mjs";

const require = createRequire(import.meta.url);
const ts = require("typescript");
const compiled = ts.transpileModule(readFileSync(new URL("../src/main.ts", import.meta.url), "utf8"), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText;
const settle = async () => { for (let i = 0; i < 8; i++) await Promise.resolve(); };

test("开发代理端口可随启动脚本调整，但不能重定向到远程地址", () => {
  assert.equal(localApiTarget(), "http://127.0.0.1:8000");
  assert.equal(localApiTarget("8010"), "http://127.0.0.1:8010");
  for (const value of ["0", "65536", "https://evil.example", "8000/path"]) {
    assert.throws(() => localApiTarget(value));
  }
});
function start(fetch) {
  const mounts = [], root = { textContent: "" };
  runInNewContext(compiled, {
    exports: {}, fetch, AbortSignal, document: { getElementById: () => root },
    require: (id) => {
      if (id === "vue") return { createApp: () => ({ mount: (target) => mounts.push(target) }) };
      if (["./App.vue", "./style.css"].includes(id)) return {};
      throw new Error(`Unexpected import ${id}`);
    },
  });
  return { mounts, root };
}

test("真实入口完成同源握手后才挂载应用，凭证不暴露给 JS", async () => {
  let done;
  const h = start((url, options) => {
    assert.equal(url, "/api/local-access");
    assert.equal(options.credentials, "same-origin");
    assert.equal(options.cache, "no-store");
    assert.ok(options.signal);
    return new Promise(resolve => { done = resolve; });
  });
  assert.equal(h.mounts.length, 0);
  done({ ok: true }); await settle(); assert.deepEqual(h.mounts, ["#app"]);
});

for (const failure of ["http", "network"]) test(`握手失败 ${failure} 不启动后台请求并提示刷新`, async () => {
  const h = start(async () => {
    if (failure === "network") throw new Error("offline");
    return { ok: false };
  });
  await settle(); assert.equal(h.mounts.length, 0); assert.match(h.root.textContent, /刷新页面/);
});

test("开发代理只接受真实本机同源页面，先校验再映射来源", () => {
  const request = (extra = {}, peer = "127.0.0.1") => ({ socket: { remoteAddress: peer },
    headers: { host: "127.0.0.1:5173", origin: "http://127.0.0.1:5173", "sec-fetch-site": "same-origin", ...extra } });
  assert.equal(allowDevRequest(request(), 5173), true);
  for (const extra of [{ host: "evil.example:5173" }, { origin: "null" }, { origin: "http://127.0.0.1:8000" },
    { origin: "https://evil.example" }, { "sec-fetch-site": "same-site" }, { "sec-fetch-site": "cross-site" }]) {
    assert.equal(allowDevRequest(request(extra), 5173), false);
  }
  assert.equal(allowDevRequest(request({}, "203.0.113.1"), 5173), false);
});
