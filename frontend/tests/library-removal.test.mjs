import assert from "node:assert/strict";
import { test } from "node:test";
import { component, mount, settle } from "./helpers/vue-host.mjs";

const target = { kind: "playlist", target_id: "synthetic-list", name: "合成歌单" };
const preview = { kind: "playlist", target_ids: [target.target_id], title: target.name, revision: "a".repeat(64),
  affected_song_count: 5, removed_song_count: 3, kept_shared_count: 2, source_playlist_count: 1, removed_song_ids: ["a","b","c"] };
function setup(api = {}) {
  const timers = [];
  const Modal = component("components/playlist/LibraryRemovalModal.vue", {
    "../player/PlayerGlass.vue": { default: () => null },
    "../../services/api": { previewLibraryRemoval: async () => preview, getRemovedLibraryItems: async () => ({ items:[] }),
      removeLibraryItem: async () => preview, ...api },
  }, { setTimeout: fn => { timers.push(fn); return timers.length; }, clearTimeout: () => {}, window: { matchMedia: () => ({ matches: true }) } });
  const removed = [], closed = [];
  const h = mount(Modal, { target, onRemoved: result => removed.push(result), onClose: () => closed.push(true) });
  return { h, removed, closed, timers };
}
const deferred = () => { let resolve; const promise = new Promise(done => { resolve = done; }); return { promise, resolve }; };

test("先展示删除与共享保留数量，确认前没有删除", async () => {
  let calls = 0;
  const { h } = setup({ removeLibraryItem: async () => { calls++; return preview; } });
  await settle();
  assert.ok(h.text(h.all()[0]).includes("仅属于它的 3 首歌曲"));
  assert.ok(h.text(h.all()[0]).includes("2 首在其他歌单中保留"));
  assert.equal(calls,0); await h.click("保留"); assert.equal(calls,0); h.dispose();
});
test("收到成功确认后才碎纸，保存期间不能重复确认", async () => {
  const pending=deferred(); let calls=0;
  const { h,removed,timers }=setup({removeLibraryItem: () => {calls++;return pending.promise;}});
  await settle(); await h.click("确认移出本地");
  assert.equal(calls,1); assert.equal(removed.length,0);
  assert.ok(h.button("正在移除…").props.disabled);
  assert.ok(!h.all().some(n => n.props.class?.includes?.("shredding")));
  pending.resolve(preview); await settle();
  assert.equal(removed.length,1); assert.ok(h.all().some(n=>n.props.class?.includes?.("shredding")));
  assert.equal(timers.length,1); h.dispose();
});
test("失败不播放删除动画，重新核对前不重复POST", async () => {
  let calls=0;
  const {h,removed}=setup({removeLibraryItem:async()=>{calls++;throw new Error("曲库已变化");}});
  await settle(); await h.click("确认移出本地");
  assert.equal(calls,1);assert.equal(removed.length,0);
  assert.ok(h.button("重新核对状态")); assert.ok(!h.button("确认移出本地"));
  await h.click("重新核对状态");assert.equal(calls,1);assert.ok(h.button("确认移出本地"));h.dispose();
});
test("响应丢失后按操作指纹核对成功，不重复删除", async () => {
  let calls=0;
  const {h,removed}=setup({removeLibraryItem:async()=>{calls++;throw new Error("连接中断");},
    getRemovedLibraryItems:async()=>({items:[{...target,operation_revision:preview.revision}]})});
  await settle();await h.click("确认移出本地");await h.click("重新核对状态");
  assert.equal(calls,1);assert.equal(removed.length,1);h.dispose();
});
test("关闭后的迟到预览不发出删除或成功事件", async () => {
  const pending=deferred();const {h,removed}=setup({previewLibraryRemoval:()=>pending.promise});
  h.dispose();pending.resolve(preview);await settle();assert.equal(removed.length,0);
});
test("歌曲菜单的本地删除与移出队列分别发出事件", async () => {
  const calls=[];
  const Item=component("components/playlist/SongItem.vue", {"../../services/api":{getSongCover:async()=>""}});
  const h=mount(Item,{song:{id:"a",title:"合成曲",artist:"合成艺人",cover_url:"test"}, index:0,current:false,playing:false,
    expanded:false,allowReview:true,allowLibraryRemove:true,manageQueue:true,
    onRemoveLibrary:()=>calls.push("library"),onRemove:()=>calls.push("queue")});
  await h.click("更多操作 合成曲");await h.click("从本地曲库删除");assert.deepEqual(calls,["library"]);h.dispose();
});
test("允许重新导入只更新许可并提示主动同步", async () => {
  let calls=0;
  const Panel=component("components/playlist/RemovedLibraryPanel.vue",{"../../services/api":{
    getRemovedLibraryItems:async()=>({items:[{...target,removed_at:"now"}]}),
    allowLibraryReimport:async()=>{calls++;return {ok:true};}}});
  const h=mount(Panel,{});await settle();assert.equal(calls,0);await h.click("允许重新导入");
  assert.equal(calls,1);assert.ok(h.text(h.all()[0]).includes("请主动同步原歌单"));h.dispose();
});
