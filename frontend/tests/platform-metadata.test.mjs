import assert from "node:assert/strict";
import { test } from "node:test";
import { component, mount, settle } from "./helpers/vue-host.mjs";

const coverage = { total: 10, genre_count: 2, unknown_genre_count: 8, platform_ready: 2, platform_empty: 1, platform_failed: 3, platform_pending: 4 };
const props = () => ({ playlists: [], selectedIds: [], scope: "liked", loading: false, syncing: false, task: null, error: "", coverage });

test("零标签库明确显示覆盖缺口并提供已有曲库补全入口", async () => {
  let clicked = 0;
  const h = mount(component("components/auth/NcmPlaylistModal.vue", {}), { ...props(), onMetadata: () => clicked++ });
  await settle();
  assert.match(h.text(h.all()[0]), /风格标签：2 \/ 10/);
  assert.match(h.text(h.all()[0]), /获取失败 3/);
  await h.click("补全已有曲库百科");
  assert.equal(clicked, 1);
  h.dispose();
});

test("百科阶段显示独立进度并允许暂停；任务完成不假称标签齐全", async () => {
  let paused = 0;
  const h = mount(component("components/auth/NcmPlaylistModal.vue", {}), {
    ...props(), syncing: true, onPause: () => paused++,
    task: { phase: "metadata", status: "running", metadata_total: 10, metadata_processed: 4, metadata_failed: 1 },
  });
  await settle();
  assert.match(h.text(h.all()[0]), /40%/);
  assert.match(h.text(h.all()[0]), /百科已处理 4 \/ 10/);
  assert.equal(h.button("补全已有曲库百科").props.disabled, true);
  await h.click("暂停补全");
  assert.equal(paused, 1);
  h.props.syncing = false;
  h.props.task = { phase: "finished", status: "completed" };
  await settle();
  assert.match(h.text(h.all()[0]), /标签覆盖见上方/);
  h.dispose();
});

test("曲目读取完成后，向量和保存阶段不显示已完成或已写入", async () => {
  const task = { status: "running", total: 5485, processed: 5485, added: 5107 };
  const h = mount(component("components/auth/NcmPlaylistModal.vue", {}), {
    ...props(), syncing: true, task: { ...task, phase: "fetching_tracks", processed: 5484 },
  });
  const text = () => h.text(h.all()[0]);
  await settle();
  assert.match(text(), /曲目读取 99%/);
  for (const [phase, label] of [["embedding", /正在生成本地语义向量/], ["saving", /正在保存歌单与歌曲来源/]]) {
    h.props.task = { ...task, phase };
    await settle();
    assert.match(text(), label);
    assert.match(text(), /已读取曲目 5485 \/ 5485 首/);
    assert.match(text(), /计划新增\s*5107/);
    assert.doesNotMatch(text(), /100%|已新增/);
  }
  h.props.task = { ...task, phase: "finished", status: "completed" };
  await settle();
  assert.match(text(), /100%/);
  assert.match(text(), /已新增\s*5107/);
  assert.doesNotMatch(text(), /计划新增/);
  h.dispose();
});

test("单曲审核分开呈现平台原始曲风和AI档案", async () => {
  const snapshot = { metadata_sha256: "a".repeat(64), original_genres: ["流行"], effective_genres: ["说唱"], platform_metadata: { raw_genres: ["说唱-中文说唱"] }, active_review_id: null, conflict: false, reviews: [], total: 0, has_more: false, genre_options: [], context: null, notice: "保留向量来源" };
  const h = mount(component("components/playlist/SongReviewModal.vue", { "../../services/api": { getSongReview: async () => snapshot } }), { song: { id: "1", title: "合成作品", artist: "合成艺人" }, sessionId: "s" });
  await settle();
  assert.match(h.text(h.all()[0]), /网易云百科曲风：说唱-中文说唱/);
  assert.match(h.text(h.all()[0]), /原档案标签.*流行/);
  h.dispose();
});

test("百科其他字段不冒充曲风，暂无平台来源不显示为待补全", async () => {
  const h = mount(component("components/auth/NcmPlaylistModal.vue", {}), { ...props(),
    coverage: { ...coverage, platform_eligible_count: 7, platform_unlinked_count: 3, platform_genre_count: 1, platform_without_genre_count: 1 },
  });
  await settle();
  assert.match(h.text(h.all()[0]), /有可用曲风 1 首/);
  assert.match(h.text(h.all()[0]), /暂无网易云歌单来源 3 首/);
  h.dispose();
});

test("进度断线保留上次值，恢复入口只读进度且禁止重发补全", async () => {
  let reconnects = 0;
  const h = mount(component("components/auth/NcmPlaylistModal.vue", {}), { ...props(), syncing: true,
    task: { task_id: "task", status: "running", phase: "metadata", metadata_total: 10, metadata_processed: 4 },
    progressError: "后台任务可能仍在运行", progressReading: false, onReconnect: () => reconnects++,
  });
  await settle();
  assert.match(h.text(h.all()[0]), /上次读取 40%/);
  assert.match(h.text(h.all()[0]), /任务状态待确认/);
  assert.ok(h.button("补全已有曲库百科").props.disabled);
  assert.ok(h.button("暂停补全").props.disabled);
  await h.click("重新读取进度");
  assert.equal(reconnects, 1);
  h.props.progressReading = true; await settle();
  assert.ok(h.button("重新读取进度").props.disabled);
  h.dispose();
});
