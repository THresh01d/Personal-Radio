const { spawn, spawnSync } = require('node:child_process');
const { createWriteStream, existsSync } = require('node:fs');
const fs = require('node:fs/promises');
const path = require('node:path');
const readline = require('node:readline');

function childEnvironment(runtime, data, credentialIdentity) {
  const env = { ...process.env };
  const inheritedConfig = new Set([
    'DEEPSEEK_API_KEY', 'DOUBAO_API_KEY', 'OPENAI_COMPATIBLE_API_KEY',
    'PYTHONHOME', 'PYTHONPATH', 'NODE_OPTIONS', 'NCM_GATEWAY_PORT',
  ]);
  for (const name of Object.keys(env)) {
    if (inheritedConfig.has(name.toUpperCase())) delete env[name];
  }
  const pathKey = Object.keys(env).find(name => name.toUpperCase() === 'PATH') || 'Path';
  env[pathKey] = path.join(runtime, 'node') + path.delimiter + (env[pathKey] || '');
  env.PERSONAL_RADIO_DATA_DIR = data;
  env.PERSONAL_RADIO_CREDENTIAL_IDENTITY = credentialIdentity;
  env.BGE_MODEL_PATH = path.join(runtime, 'application', 'models', 'BAAI', 'bge-small-zh-v1.5');
  env.HF_HOME = path.join(data, 'huggingface');
  env.HF_HUB_OFFLINE = '1';
  env.TRANSFORMERS_OFFLINE = '1';
  env.TEMP = env.TMP = path.join(data, 'runtime');
  env.PYTHONUTF8 = '1';
  return env;
}

async function startBackend(runtime, data, credentialIdentity, onExit) {
  const python = path.join(runtime, 'python', 'python.exe');
  const application = path.join(runtime, 'application');
  if (!existsSync(python) || !existsSync(path.join(application, 'static', 'dist', 'index.html'))) {
    throw new Error('安装包缺少电台运行文件，请重新安装。');
  }
  await fs.mkdir(path.join(data, 'runtime'), { recursive: true });
  // 相同端口让 Chromium 的本地设置和会话在升级、重开后仍属于同一来源。
  const config = path.join(data, 'local-port.json');
  let port = 0;
  try {
    port = JSON.parse(await fs.readFile(config, 'utf8')).port;
    if (!Number.isInteger(port) || port < 1024 || port > 65535) throw new Error('本机端口记录无效。');
  } catch (error) {
    if (error.code !== 'ENOENT') throw error;
  }
  const log = createWriteStream(path.join(data, 'runtime', 'radio.log'), { flags: 'w' });
  log.on('error', () => {});
  const child = spawn(python, ['-I', '-X', 'utf8', '-u', '-B', path.join(application, 'desktop-server.py'),
    '--application', application, '--port', String(port)], {
    cwd: application, env: childEnvironment(runtime, data, credentialIdentity), windowsHide: true, stdio: ['pipe', 'pipe', 'pipe'],
  });
  child.stderr.pipe(log, { end: false });
  child.stdin.on('error', () => {});
  const lines = readline.createInterface({ input: child.stdout });
  let stopped = false;
  let exited = false;
  let exitError;
  const exit = new Promise(resolve => {
    child.once('close', code => {
      exited = true;
      log.end();
      resolve();
      if (!stopped && onExit) onExit(code);
    });
    child.once('error', error => { exitError = error; });
  });
  const forceStop = () => {
    if (!exited && child.pid) {
      // 只清理本次启动的 Python 和它拥有的网关子进程。
      spawnSync(path.join(process.env.SystemRoot || 'C:\\Windows', 'System32', 'taskkill.exe'),
        ['/pid', String(child.pid), '/t', '/f'], { windowsHide: true, timeout: 3000 });
    }
  };
  async function stop() {
    if (stopped) return;
    stopped = true;
    child.stdin.end();
    const timer = setTimeout(forceStop, 8500);
    const deadline = new Promise(resolve => {
      const limit = setTimeout(resolve, 12500);
      limit.unref();
    });
    try { await Promise.race([exit, deadline]); } finally { clearTimeout(timer); }
  }
  const ready = new Promise((resolve, reject) => {
    lines.on('line', line => {
      const prefix = 'PERSONAL_RADIO_READY ';
      if (!line.startsWith(prefix)) { log.write(line + '\n'); return; }
      try {
        const value = JSON.parse(line.slice(prefix.length)).port;
        if (!Number.isInteger(value) || value < 1024 || value > 65535 || (port !== 0 && port !== value)) {
          throw new Error('本机服务没有返回有效地址。');
        }
        resolve(value);
      } catch (error) { reject(error); }
    });
    void exit.then(() => reject(exitError || new Error(port
      ? '原来的电台端口无法使用。查看运行日志，或关闭占用端口的程序后重试。'
      : '本机电台启动失败。请查看运行日志。')));
  });
  let timer;
  try {
    const actualPort = await Promise.race([ready, new Promise((_, reject) => {
      timer = setTimeout(() => reject(new Error('本机电台启动超时，请查看运行日志。')), 25000);
    })]);
    if (!port) await fs.writeFile(config, JSON.stringify({ port: actualPort }), { flag: 'wx' });
    return { origin: `http://127.0.0.1:${actualPort}`, stop };
  } catch (error) {
    await stop();
    throw error;
  } finally { clearTimeout(timer); }
}

module.exports = { startBackend };
