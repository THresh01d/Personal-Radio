import assert from "node:assert/strict";
import { test } from "node:test";
import { panelFocusModule } from "./helpers/panel-focus-module.mjs";

function harness(modal = false) {
  const mounted = [], unmount = [], ticks = [], listeners = new Map();
  const document = { body: {}, activeElement: null, dialogs: [], querySelectorAll() { return this.dialogs; },
    addEventListener: (name, fn) => listeners.set(name, fn), removeEventListener: name => listeners.delete(name) };
  const node = () => ({ isConnected: true, tabIndex: 0, disabled: false, visible: true, inert: false,
    focus() { document.activeElement = this; }, matches() { return this.disabled; }, closest() { return this.inert; },
    getClientRects() { return this.visible ? [{}] : []; } });
  const opener = node(), first = node(), last = node(), panel = node();
  panel.nodes = [first, last]; panel.querySelectorAll = () => panel.nodes;
  panel.contains = item => item === panel || panel.nodes.includes(item);
  opener.focus(); let closes = 0;
  const { usePanelFocus } = panelFocusModule({ nextTick: fn => ticks.push(fn), onMounted: fn => mounted.push(fn),
    onBeforeUnmount: fn => unmount.push(fn) }, document);
  const keydown = usePanelFocus({ value: panel }, () => closes++, modal);
  const key = (key, shiftKey = false) => { const event = { key, shiftKey, defaultPrevented: false,
    preventDefault() { this.defaultPrevented = true; }, stopPropagation() { this.stopped = true; } }; keydown(event); return event; };
  return { document, opener, first, last, panel, node, listeners, key, closes: () => closes,
    mount: () => mounted.forEach(fn => fn()), tick: () => ticks.splice(0).forEach(fn => fn()),
    dispose: () => unmount.forEach(fn => fn()) };
}

test("面板打开后定位，Esc 关闭且返回原入口", () => {
  const h = harness(); h.mount(); h.tick();
  assert.equal(h.document.activeElement, h.panel);
  assert.ok(h.key("Escape").defaultPrevented); assert.equal(h.closes(), 1);
  h.dispose(); assert.equal(h.document.activeElement, h.opener);
});

test("弹窗 Tab 正反循环，隐藏/禁用/负tabindex/惰性节点不进入边界", () => {
  const h = harness(true); h.mount(); h.tick();
  const skipped = [h.node(), h.node(), h.node(), h.node()];
  skipped[0].visible = false; skipped[1].disabled = true; skipped[2].tabIndex = -1; skipped[3].inert = true;
  h.panel.nodes.push(...skipped);
  h.first.focus(); assert.ok(h.key("Tab", true).defaultPrevented); assert.equal(h.document.activeElement, h.last);
  h.last.focus(); h.key("Tab"); assert.equal(h.document.activeElement, h.first);
  h.document.activeElement = h.document.body; h.listeners.get("keydown")({ key: "Tab", preventDefault() {} });
  assert.equal(h.document.activeElement, h.first);
  h.dispose(); assert.equal(h.listeners.size, 0);
});

test("侧栏允许 Tab 去播放器，切换入口不抢已移动焦点", () => {
  const h = harness(); h.mount(); h.tick();
  h.last.focus(); assert.equal(h.key("Tab").defaultPrevented, false);
  const nextEntry = h.node(); nextEntry.focus(); h.dispose();
  assert.equal(h.document.activeElement, nextEntry);
});

test("异步定位不能在卸载后抢焦点，已移除入口不能恢复", () => {
  const h = harness(); h.mount(); h.opener.isConnected = false; h.dispose(); h.tick();
  assert.equal(h.document.activeElement, h.opener);
  assert.equal(h.closes(), 0);
});

test("无可用按钮时 Tab 留在容器，只有最上层弹窗响应", () => {
  const h = harness(true); h.mount(); h.tick(); h.panel.nodes = [];
  assert.ok(h.key("Tab").defaultPrevented); assert.equal(h.document.activeElement, h.panel);
  h.document.dialogs = [{ contains: () => false }];
  assert.equal(h.key("Escape").defaultPrevented, false); assert.equal(h.closes(), 0);
});
