export const hotkeyLabels = {
  toggle: "播放／暂停",
  previous: "上一首",
  next: "下一首",
  volume_up: "提高音量",
  volume_down: "降低音量",
} as const;
export type HotkeyAction = keyof typeof hotkeyLabels;
export const hotkeyActions = Object.keys(hotkeyLabels) as HotkeyAction[];
export const defaultHotkeys = {
  enabled: true,
  toggle: "Ctrl+Alt+P",
  previous: "Ctrl+Alt+←",
  next: "Ctrl+Alt+→",
  volume_up: "Ctrl+Alt+↑",
  volume_down: "Ctrl+Alt+↓",
};
export type HotkeyConfig = typeof defaultHotkeys;
export type HotkeyStatus = { supported: boolean; config: HotkeyConfig; active: HotkeyAction[] };
