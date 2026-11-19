<template>
  <div class="desktop-window-controls" aria-hidden="true"><i></i><i></i><i></i></div>
</template>

<style scoped>
/* 玻璃底由页面绘制，按钮和命中区域仍由 Windows 提供。 */
.desktop-window-controls {
  position: fixed;
  inset-block-start: env(titlebar-area-y, 0px);
  inset-inline-end: 0;
  z-index: 150;
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 4px;
  width: var(--desktop-caption-width);
  height: var(--desktop-caption-height);
  padding: 7px 3px;
  pointer-events: none;
}

i {
  border: 1px solid #ffffff1c;
  border-radius: 9px;
  background: #121821b8;
}
</style>
