// 图形引擎单独分包；播放器和首屏交互不等待Three.js加载。
export const loadRainScene = () => import("./scene");
export const supportsSnowScene = true;
