const { app, BrowserWindow, dialog, ipcMain, Menu, shell } = require('electron');
const { readFileSync, mkdirSync } = require('node:fs');
const path = require('node:path');
const { startBackend } = require('./backend.cjs');
const { findUpdate } = require('./updates.cjs');
const { prepareProfile } = require('./profile.cjs');

let window;
let backend;
let backendStarting;
let closing = false;
let canQuit = false;
let checking = false;
const savedProfile = app.commandLine.getSwitchValue('user-data-dir');
const legacyProfile = savedProfile && path.isAbsolute(savedProfile)
  ? savedProfile
  : path.join(app.getPath('appData'), 'PersonalRadio');
const location = legacyProfile + '-location';
mkdirSync(location, { recursive: true });
// 锁依附于固定位置，避免两个实例同时迁移或使用同一个曲库。
app.setPath('userData', location);
const runtime = app.isPackaged ? path.join(process.resourcesPath, 'radio-runtime')
  : path.resolve(__dirname, '.stage', 'radio-runtime');

function trustedWindowUrl(raw) {
  try {
    const address = new URL(raw);
    return address.origin === backend?.origin && !address.username && !address.password;
  } catch { return false; }
}

function openExternalPage(raw) {
  try {
    const address = new URL(raw);
    if (address.protocol === 'https:' && !address.username && !address.password) {
      return shell.openExternal(address.href);
    }
  } catch { /* 浏览器可跳转的地址只限明确的 HTTPS 页面。 */ }
  return Promise.resolve();
}

function showWindow() {
  window = new BrowserWindow({
    title: '私人电台',
    width: 1260, height: 820, minWidth: 360, minHeight: 500,
    backgroundColor: '#071221', show: false,
    accentColor: '#081520',
    titleBarStyle: 'hidden',
    titleBarOverlay: { color: '#00000000', symbolColor: '#c6d8e3', height: 46 },
    webPreferences: {
      preload: path.join(__dirname, 'preload.cjs'),
      contextIsolation: true, nodeIntegration: false, sandbox: true,
    },
  });
  window.webContents.setWindowOpenHandler(({ url }) => {
    void openExternalPage(url).catch(() => {});
    return { action: 'deny' };
  });
  const stayLocal = (event, url) => {
    if (!trustedWindowUrl(url)) event.preventDefault();
  };
  window.webContents.on('will-navigate', stayLocal);
  window.webContents.on('will-redirect', stayLocal);
  window.webContents.session.setPermissionRequestHandler((_contents, _permission, allow) => allow(false));
  window.once('ready-to-show', () => window?.show());
  window.on('closed', () => { window = null; });
  return window.loadURL(backend.origin);
}

async function checkUpdates() {
  if (checking || closing || !window) return;
  checking = true;
  try {
    const release = JSON.parse(readFileSync(path.join(__dirname, 'release-target.json'), 'utf8'));
    const result = await findUpdate(release.repository, app.getVersion());
    if (closing || !window) return;
    if (result.status === 'available') {
      const action = await dialog.showMessageBox(window, {
        type: 'info', message: `私人电台 ${result.tag} 已发布`,
        detail: '打开发布页下载安装包。退出电台后再安装，新版会继续使用你的曲库和设置。',
        buttons: ['打开发布页', '稍后'], cancelId: 1,
      });
      if (action.response === 0) await openExternalPage(result.page);
    } else {
      const messages = {
        unconfigured: '这个开发包尚未关联公开发布仓库。',
        'no-release': 'GitHub 上暂时没有可用的正式版。',
        current: `当前已是正式最新版（${app.getVersion()}）。`,
      };
      await dialog.showMessageBox(window, { type: 'info', message: messages[result.status] });
    }
  } catch (error) {
    if (!closing && window) await dialog.showMessageBox(window, { type: 'warning', message: '暂时无法检查更新', detail: String(error.message) });
  } finally { checking = false; }
}

function trustedDesktopFrame(event) {
  return window && event.sender === window.webContents
    && event.senderFrame === window.webContents.mainFrame
    && trustedWindowUrl(event.senderFrame?.url);
}

if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  let data;
  let profileError;
  try {
    const profile = prepareProfile(legacyProfile, location, path.dirname(process.execPath));
    app.setPath('userData', profile);
    app.setPath('sessionData', profile);
    data = path.join(profile, 'radio-data');
  } catch (error) {
    profileError = error;
  }
  app.on('second-instance', () => {
    if (!window) return;
    if (window.isMinimized()) window.restore();
    window.focus();
  });
  ipcMain.handle('radio:chatgpt-authorization', async (event, raw) => {
    if (!trustedDesktopFrame(event) || typeof raw !== 'string') {
      throw new Error('请在电台设置页发起授权。');
    }
    const url = new URL(raw);
    if (url.origin !== 'https://auth.openai.com' || url.pathname !== '/api/accounts/authorize'
        || url.username || url.password) throw new Error('不是官方 ChatGPT 授权地址。');
    await openExternalPage(url.href);
  });
  ipcMain.handle('radio:check-for-updates', event => {
    if (!trustedDesktopFrame(event)) throw new Error('请在电台窗口中检查更新。');
    return checkUpdates();
  });
  ipcMain.handle('radio:desktop-version', event => {
    if (!trustedDesktopFrame(event)) throw new Error('请在电台窗口中查看版本。');
    return app.getVersion();
  });
  app.on('window-all-closed', () => app.quit());
  app.on('before-quit', event => {
    if (canQuit) return;
    event.preventDefault();
    if (closing) return;
    closing = true;
    // 关闭页面后再停后端，播放器的长连接能先完成断开。
    window?.destroy();
    void (async () => {
      try {
        const running = backend || await backendStarting;
        await running?.stop();
      } catch { /* 启动失败时没有可关闭的应用服务。 */ }
      finally { canQuit = true; app.quit(); }
    })();
  });
  app.whenReady().then(async () => {
    if (closing) return;
    if (profileError) throw profileError;
    Menu.setApplicationMenu(null);
    backendStarting = startBackend(runtime, data, path.join(legacyProfile, 'radio-data'), () => {
      if (!closing && window) {
        dialog.showErrorBox('私人电台服务已停止', `请重新打开电台。运行日志位于：\n${path.join(data, 'runtime', 'radio.log')}`);
        app.quit();
      }
    });
    backend = await backendStarting;
    if (!closing) await showWindow();
  }).catch(error => {
    if (!closing) dialog.showErrorBox('私人电台无法启动', error.message);
    app.quit();
  });
}
