import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { test } from "node:test";
import { runInNewContext } from "node:vm";

const require = createRequire(import.meta.url);
const ts = require("typescript"), vue = require("vue");
const compiled = ts.transpileModule(readFileSync(new URL("../src/composables/useNcmAuth.ts", import.meta.url), "utf8"), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText;
function harness(api) {
  const mounted = [], timers = [], messages = [], exports = {}, unmounted = [], timeouts = new Map(); let timerId = 0;
  runInNewContext(compiled, { exports, Error, window: {
    clearInterval() {}, clearTimeout(id) { timeouts.delete(id); }, setInterval: (fn) => { timers.push(fn); return 1; }, setTimeout(fn, ms) { timeouts.set(++timerId, { fn, ms }); return timerId; },
  }, require: (id) => {
    if (id === "vue") return { ...vue, onMounted: (fn) => mounted.push(fn), onBeforeUnmount: fn => unmounted.push(fn) };
    if (id === "../services/api") return api;
    throw new Error(`Unexpected import ${id}`);
  } });
  return { auth: exports.useNcmAuth((message) => messages.push(message)), mounted, timers, messages, timeouts, dispose: () => unmounted.forEach(fn => fn()) };
}
const settle = async () => { for (let i = 0; i < 8; i++) await Promise.resolve(); };

test("安全迁移失败仍保留登录并给用户明确提醒", async () => {
  const h = harness({ getLoginStatus: async () => ({ logged_in: true, nickname: "测试用户", avatar_url: "https://p1.music.126.net/test.png", storage_warning: "旧凭据迁移待重试" }) });
  h.mounted[0](); await settle();
  assert.equal(h.auth.profile.value.nickname, "测试用户");
  assert.equal(h.auth.profile.value.avatarUrl, "https://p1.music.126.net/test.png");
  assert.deepEqual(h.messages, ["旧凭据迁移待重试"]);
});

test("扫码时更新头像，迟到的启动状态不会清掉已确认的账号资料", async () => {
  let resolveStatus;
  const h = harness({
    getLoginStatus: () => new Promise(resolve => { resolveStatus = resolve; }),
    createLoginQr: async () => ({ key: "test", qrimg: "test-image" }),
    checkLoginQr: async () => ({ status: "confirmed", nickname: "扫码用户", avatar_url: "https://p2.music.126.net/avatar.png" }),
    logoutNcm: async () => {},
  });
  h.mounted[0]();
  await h.auth.openQr(); await h.timers[0]();
  resolveStatus({ logged_in: false }); await settle();
  assert.equal(h.auth.profile.value.nickname, "扫码用户");
  assert.equal(h.auth.profile.value.avatarUrl, "https://p2.music.126.net/avatar.png");
  await h.auth.logout();
  assert.equal(h.auth.profile.value, null);
});

test("扫码确认的安全存储失败可见，保留二维码与重试而非永久等待", async () => {
  const h = harness({ createLoginQr: async () => ({ key: "test", qrimg: "test-image" }),
    checkLoginQr: async () => { throw new Error("安全凭据库暂不可用，请重试"); } });
  await h.auth.openQr(); await h.timers[0](); await settle();
  assert.equal(h.auth.qrImage.value, "test-image");
  assert.equal(h.auth.qrOpen.value, true);
  assert.match(h.auth.qrStatus.value, /安全凭据库暂不可用/);
});

test("补全百科不改变原歌单选择，补全中可关闭面板继续后台任务", async () => {
  let requested;
  const h = harness({
    syncNcmPlaylists: async scope => { requested = scope; return { ok: true, task_id: "task" }; },
    getNcmSyncStatus: async () => ({ task_id: "task", status: "completed", phase: "finished" }),
    getSetupStatus: async () => ({ library: { tag_coverage: { genre_count: 1, total: 4 } } }),
  });
  h.auth.syncScope.value = "selected";
  await h.auth.completeMetadata();
  assert.equal(requested, "metadata");
  assert.equal(h.auth.syncScope.value, "selected");
  assert.equal(h.auth.tagCoverage.value.genre_count, 1);
  h.auth.syncOpen.value = true;
  h.auth.syncing.value = true;
  h.auth.syncTask.value = { task_id: "task", status: "running", phase: "metadata" };
  h.auth.closeSync();
  assert.equal(h.auth.syncOpen.value, false);
  assert.equal(h.auth.syncing.value, true);
  assert.equal(h.auth.syncTask.value.task_id, "task");
});

test("读进度失败保留后台运行状态，恢复只读原任务且不重发同步", async () => {
  let posts = 0, reads = 0; const ids = [];
  const h = harness({
    syncNcmPlaylists: async () => { posts++; return { ok: true, task_id: "task" }; },
    getNcmSyncStatus: async id => { ids.push(id); if (++reads === 1) throw new Error("offline"); return { task_id: id, status: "completed" }; },
    getSetupStatus: async () => ({ library: {} }),
  });
  await h.auth.completeMetadata();
  assert.equal(h.auth.syncing.value, true);
  assert.equal(h.auth.syncTask.value.status, undefined);
  assert.match(h.auth.syncProgressError.value, /可能仍在运行/);
  await h.auth.completeMetadata(); await h.auth.retrySync();
  assert.equal(posts, 1);
  await h.auth.reconnectSync();
  assert.equal(h.auth.syncing.value, false);
  assert.equal(h.auth.syncProgressError.value, "");
  assert.deepEqual(ids, ["task", "task"]);
  assert.equal(h.timeouts.size, 0);
});

test("连续三次进度失败后停止自动轮询，可手动恢复", async () => {
  let reads = 0;
  const h = harness({
    syncNcmPlaylists: async () => ({ ok: true, task_id: "task" }),
    getNcmSyncStatus: async () => { reads++; throw new Error("offline"); },
  });
  await h.auth.completeMetadata();
  for (let i = 0; i < 2; i++) {
    const [id, timer] = [...h.timeouts.entries()][0];
    h.timeouts.delete(id); timer.fn(); await settle();
  }
  assert.equal(reads, 3); assert.equal(h.timeouts.size, 0);
  assert.equal(h.auth.syncing.value, true);
  h.auth.syncOpen.value = true; h.auth.closeSync();
  assert.equal(h.auth.syncOpen.value, false);
  h.dispose();
});

test("迟到的旧进度不能覆盖手动恢复后的终态", async () => {
  let oldResolve, reads = 0;
  const old = new Promise(resolve => { oldResolve = resolve; });
  const h = harness({
    syncNcmPlaylists: async () => ({ ok: true, task_id: "task" }),
    getNcmSyncStatus: () => ++reads === 1 ? old : Promise.resolve({ task_id: "task", status: "completed" }),
    getSetupStatus: async () => ({ library: {} }),
  });
  const starting = h.auth.completeMetadata(); await settle();
  await h.auth.reconnectSync();
  oldResolve({ task_id: "task", status: "running", phase: "metadata" });
  await starting;
  assert.equal(h.auth.syncTask.value.status, "completed");
  assert.equal(h.auth.syncing.value, false);
  assert.equal(h.timeouts.size, 0);
});

test("组件卸载后迟到读取不再修改进度或启动轮询", async () => {
  let resolveRead;
  const h = harness({
    syncNcmPlaylists: async () => ({ ok: true, task_id: "task" }),
    getNcmSyncStatus: () => new Promise(resolve => { resolveRead = resolve; }),
  });
  const starting = h.auth.completeMetadata(); await settle(); h.dispose();
  resolveRead({ task_id: "task", status: "running" }); await starting;
  assert.equal(h.auth.syncTask.value.status, undefined);
  assert.equal(h.timeouts.size, 0); assert.equal(h.messages.length, 0);
});
