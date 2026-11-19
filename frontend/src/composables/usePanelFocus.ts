import { nextTick, onBeforeUnmount, onMounted, type Ref } from "vue";

export function usePanelFocus(panel: Ref<HTMLElement | null>, close: () => void, modal = false) {
  let opener: HTMLElement | null = null;
  let disposed = false;

  function onPanelKeydown(event: KeyboardEvent) {
    if (event.defaultPrevented || disposed) return;
    // 弹窗可能短暂叠放；只有最上层响应，不能一次 Esc 关掉多个层级。
    if (modal) {
      const dialogs = document.querySelectorAll<HTMLElement>('[aria-modal="true"]');
      if (dialogs.length && !dialogs[dialogs.length - 1]?.contains(panel.value)) return;
    }
    if (event.key === "Escape") {
      event.preventDefault(); event.stopPropagation(); close();
      return;
    }
    if (!modal || event.key !== "Tab") return;
    const nodes = [...(panel.value?.querySelectorAll<HTMLElement>(
      'button,input,textarea,select,a[href],summary,[tabindex]'
    ) || [])].filter(node => node.tabIndex >= 0 && !node.matches(":disabled")
      && !node.closest("[inert]") && node.getClientRects().length > 0
      && getComputedStyle(node).visibility !== "hidden");
    const index = nodes.indexOf(document.activeElement as HTMLElement);
    if (!nodes.length) {
      event.preventDefault(); panel.value?.focus();
    } else if (index < 0 || (event.shiftKey && index === 0) || (!event.shiftKey && index === nodes.length - 1)) {
      event.preventDefault(); (event.shiftKey ? nodes[nodes.length - 1] : nodes[0])?.focus();
    }
  }

  onMounted(() => {
    opener = document.activeElement as HTMLElement | null;
    // 设置按钮临时禁用时焦点可能回到 body；弹窗仍须能接住 Tab/Esc。
    if (modal) document.addEventListener("keydown", onPanelKeydown);
    void nextTick(() => { if (!disposed) panel.value?.focus({ preventScroll: true }); });
  });
  onBeforeUnmount(() => {
    disposed = true;
    if (modal) document.removeEventListener("keydown", onPanelKeydown);
    // 切到另一个入口时尊重已移动的焦点；关闭当前层才回到原入口。
    if (opener?.isConnected && (panel.value?.contains(document.activeElement) || document.activeElement === document.body)) {
      opener.focus({ preventScroll: true });
    }
  });
  return onPanelKeydown;
}
