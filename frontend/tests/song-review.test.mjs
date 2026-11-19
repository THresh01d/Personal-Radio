import assert from "node:assert/strict";
import { test } from "node:test";
import { component, mount, settle } from "./helpers/vue-host.mjs";

const snapshot = () => ({ metadata_sha256: "a".repeat(64), original_genres: ["流行"], effective_genres: ["流行"], active_review_id: null, conflict: false, reviews: [], total: 0, has_more: false, genre_options: ["流行", "说唱", "emo", "R&B"], context: { batch_id: "batch", query: "合成需求" }, notice: "向量未重算" });
const props = () => ({ song: { id: "a", title: "合成单曲", artist: "合成艺人", batch_id: "batch" }, sessionId: "s" });
const modal = api => component("components/playlist/SongReviewModal.vue", { "../../services/api": api });
function chooseGlobal(h) { h.all().find(n => n.type === "input" && n.props.value === "global_genre").props["onUpdate:modelValue"]("global_genre"); }

test("读取默认只绑定需求，无写入，无需求时不误保存全局", async () => {
  let writes = 0;
  const h = mount(modal({ getSongReview: async () => ({ ...snapshot(), context: null }), proposeSongReview: () => writes++, actSongReview: () => writes++ }), props());
  await settle();
  assert.equal(writes, 0);
  assert.ok(h.button("保存待确认提案").props.disabled);
  assert.ok(h.all().some(n => h.text(n).includes("原档案标签")));
  h.dispose();
});

test("保存与确认两步分开，范围与标签不会改成长期喜欢", async () => {
  let state = snapshot(); const calls = [];
  const h = mount(modal({ getSongReview: async () => state,
    proposeSongReview: async (sid, body) => { calls.push(body); state = { ...state, total: 1, reviews: [{ review_id: "r", revision: 1, status: "pending", payload: { scope: body.scope, genres: body.genres, metadata_sha256: body.metadata_sha256 } }] }; },
    actSongReview: async (item, operation) => { calls.push(operation); state = { ...state, active_review_id: "r", effective_genres: item.payload.genres, reviews: [{ ...item, revision: 2, status: "confirmed" }] }; },
  }), props());
  await settle(); chooseGlobal(h); await settle(); await h.click("说唱"); await h.click("emo"); await h.click("保存待确认提案");
  assert.equal(calls.length, 1); assert.equal(calls[0].scope, "global_genre");
  assert.equal(JSON.stringify(calls[0].genres), JSON.stringify(["说唱", "emo"]));
  assert.equal(calls[0].batch_id, undefined); assert.equal(calls[0].session_id, undefined);
  assert.ok(h.button("确认全局校正"));
  await h.click("确认全局校正");
  assert.equal(calls[1], "confirm"); assert.ok(h.button("撤销这条记录"));
  assert.ok(h.all().some(n => h.text(n).includes("下次标签检索使用")));
  h.dispose();
});

test("过期/旧需求与冲突记录不允许确认，撤销仍可达", async () => {
  const state = { ...snapshot(), conflict: true, reviews: [{ review_id: "r", revision: 1, status: "pending", stale: true, payload: { scope: "global_genre", genres: ["emo"] } }] };
  const h = mount(modal({ getSongReview: async () => state }), props()); await settle();
  assert.ok(h.button("确认全局校正").props.disabled);
  assert.ok(h.button("撤销这条记录"));
  h.dispose();
});

test("切换歌曲后迟到读取不覆盖新弹窗", async () => {
  let resolveOld; const old = new Promise(r => { resolveOld = r; });
  const h = mount(modal({ getSongReview: sid => sid === "a" ? old : Promise.resolve({ ...snapshot(), original_genres: ["R&B"] }) }), props());
  h.props.song = { ...h.props.song, id: "b" }; await settle();
  resolveOld(snapshot()); await settle();
  assert.ok(h.all().some(n => h.text(n).includes("原档案标签（可能为 AI 推断）：R&B")));
  h.dispose();
});

test("迟到保存不能改写新歌曲或在关闭后发出变更事件", async () => {
  let resolve; let changes = 0; let savedSignal;
  const pending = new Promise(done => { resolve = done; });
  const h = mount(modal({ getSongReview: async () => snapshot(),
    proposeSongReview: (sid, body, signal) => { savedSignal = signal; return pending; },
  }), { ...props(), onChanged: () => changes++ });
  await settle();
  const button = h.button("保存待确认提案"); button.props.onClick({}); await settle();
  h.props.song = { ...h.props.song, id: "b" }; await settle();
  assert.equal(savedSignal.aborted, true);
  h.dispose(); resolve({}); await settle(); assert.equal(changes, 0);
});

test("网络/冲突失败持续显示，不宣称操作已成功", async () => {
  const h = mount(modal({ getSongReview: async () => snapshot(), proposeSongReview: async () => { throw new Error("歌曲信息已变化"); } }), props());
  await settle(); await h.click("保存待确认提案");
  assert.ok(h.all().some(n => n.props.role === "alert" && h.text(n).includes("歌曲信息已变化")));
  assert.ok(h.button("重新读取")); h.dispose();
});

test("撤销需求正例明确提示标签与偏好没有改变", async () => {
  let state = { ...snapshot(), total: 1, reviews: [{ review_id: "r", revision: 2, status: "confirmed", payload: { scope: "query_positive", query_key: "batch" } }] };
  const h = mount(modal({ getSongReview: async () => state, actSongReview: async () => {
    state = { ...state, reviews: [{ ...state.reviews[0], revision: 3, status: "revoked" }] };
  } }), props());
  await settle(); await h.click("撤销这条记录");
  assert.ok(h.all().some(n => n.props.role === "status" && h.text(n).includes("本次需求正例已撤销；全局标签与偏好不变")));
  h.dispose();
});
