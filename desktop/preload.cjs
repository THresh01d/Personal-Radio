const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('personalRadioDesktop', {
  openChatGPTAuthorization: url => ipcRenderer.invoke('radio:chatgpt-authorization', url),
  checkForUpdates: () => ipcRenderer.invoke('radio:check-for-updates'),
  getDesktopVersion: () => ipcRenderer.invoke('radio:desktop-version'),
});
