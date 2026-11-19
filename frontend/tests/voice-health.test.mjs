import assert from "node:assert/strict";
import { test } from "node:test";
import { component, mount, settle } from "./helpers/vue-host.mjs";

const status = (extra = {}) => ({ loaded_version: "7.3.1", installed_version: "7.3.1", required_version: "7.3.1",
  restart_required: false, dependency_mismatch: false, cooldown_seconds: 0, checking: false,
  last_failure_code: null, last_failure_message: "", last_check: null, ...extra });
function panel(api) {
  return component("components/player/VoiceHealthPanel.vue", { "../../services/api": api });
}
test("打开设置只读状态，明确检测才合成固定短句，更新成功提示仍区分试听", async () => {
  let reads = 0, checks = 0;
  const h = mount(panel({
    getTtsHealth: async () => { reads++; return status(); },
    checkTtsHealth: async () => { checks++; return status({ last_check: { ok: true, message: "在线合成成功；实际出声请用试听确认。" } }); },
  }), {});
  await settle();
  assert.equal(reads, 1); assert.equal(checks, 0);
  assert.ok(h.all().some(n => n.props.role === "status" && h.text(n).includes("尚未检测")));
  await h.click("检测在线语音");
  assert.equal(checks, 1);
  assert.ok(h.all().some(n => n.props.role === "status" && h.text(n).includes("实际出声")));
  h.dispose();
});
test("运行版本失配明确提示重启，不能以新包已安装冒充生效", async () => {
  const h = mount(panel({ getTtsHealth: async () => status({ loaded_version: "7.2.8", restart_required: true }) }), {});
  await settle();
  assert.ok(h.all().some(n => n.props.role === "status" && h.text(n).includes("重启电台后生效")));
  h.dispose();
});
test("检测等待中防重复，关闭取消且迟到结果不更新组件", async () => {
  let done, signal, checks = 0;
  const h = mount(panel({
    getTtsHealth: async () => status(),
    checkTtsHealth: (s) => { checks++; signal = s; return new Promise(resolve => { done = resolve; }); },
  }), {});
  await settle(); await h.click("检测在线语音");
  assert.equal(h.button("检测中…").props.disabled, true);
  assert.equal(checks, 1);
  h.dispose(); assert.equal(signal.aborted, true);
  done(status()); await settle();
});
test("避让状态与检测错误持续显示，读取状态不会触发重试", async () => {
  let checks = 0, reads = 0;
  const h = mount(panel({
    getTtsHealth: async () => ++reads === 1 ? status({ cooldown_seconds: 60 }) : status({
      last_failure_message: "在线语音服务拒绝连接", last_check: { ok: true, message: "此前合成成功" },
    }),
    checkTtsHealth: async () => { checks++; throw new Error("检测过于频繁，请稍后再试。"); },
  }), {});
  await settle();
  assert.ok(h.all().some(n => n.props.role === "status" && h.text(n).includes("避让")));
  await h.click("检测在线语音");
  assert.ok(h.all().some(n => n.props.role === "status" && h.text(n).includes("过于频繁")));
  await h.click("刷新状态"); assert.equal(checks, 1);
  assert.ok(h.all().some(n => n.props.role === "status" && h.text(n).includes("拒绝连接")));
  h.dispose();
});

