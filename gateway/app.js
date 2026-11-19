#!/usr/bin/env node
/**
 * gateway/app.js - 网易云音乐 API 网关启动脚本
 *
 * 为什么单独起一个 node 进程：NeteaseCloudMusicApi 是 Node 生态的现成网关，
 * 用 Express 封装了网易云全部接口（含扫码登录、播放地址、歌单拉取），
 * Python 后端通过 HTTP 调用它即可，不必把整套登录加密逻辑重写在 Python 里。
 *
 * 启动策略（配合 app/main.py）：
 * 1. Python 后端启动时先探测配置端口，若是本网关则直接复用；
 * 2. 若端口被其他程序占用，Python 会选择空闲端口并通过环境变量传进来；
 * 3. 否则用 subprocess 拉起本脚本，并轮询健康检查直到就绪。
 */
const fs = require('fs')
const path = require('path')
const os = require('os')
const { installPlaybackDiagnostics } = require('./playback-diagnostics')

// 不能只脱敏对象字段：上游也会把完整异常/URL 拼成字符串交给 error/warn。
const safeConsole = require('./safe-logging').installSafeConsole()

async function main() {
  // 缺少后端凭证时立即退出，不先执行上游初始化或外网请求。
  const token = process.env.PERSONAL_RADIO_GATEWAY_TOKEN
  if (!token || token.length < 32) throw new Error('请通过私人电台后端启动网关')
  // 与官方入口一致：先确保 anonymous_token 文件存在，再生成匿名凭证。
  // 很多公开接口（登录二维码 key / 匿名歌单）需要匿名身份，避免 IP 被风控。
  const tmpPath = os.tmpdir()
  const tokenFile = path.resolve(tmpPath, 'anonymous_token')
  if (!fs.existsSync(tokenFile)) {
    fs.writeFileSync(tokenFile, '', 'utf-8')
  }

  try {
    const generateConfig = require('NeteaseCloudMusicApi/generateConfig')
    await generateConfig()
  } catch (e) {
    // 匿名凭证失败不致命：非登录态接口仍可用，登录后会换成正式 cookie
    safeConsole.warn('[ncm-gateway] 匿名凭据初始化失败（非致命）')
  }

  installPlaybackDiagnostics()
  const { serveNcmApi } = require('NeteaseCloudMusicApi')
  const configuredPort = Number.parseInt(process.env.NCM_GATEWAY_PORT || '3000', 10)
  const port = Number.isInteger(configuredPort) && configuredPort > 0 && configuredPort <= 65535
    ? configuredPort
    : 3000
  // 只绑定 127.0.0.1：网关只给本机 Python 后端用，不暴露到局域网
  const app = await serveNcmApi({ port, host: '127.0.0.1', checkVersion: false })
  require('./security').installGatewayBoundary(app, token, port)
  safeConsole.log(`[ncm-gateway] 受保护入口已启动，端口=${port}`)
}

main().catch((e) => {
  safeConsole.error('[ncm-gateway] 启动失败，请检查依赖、端口与后端凭证')
  process.exit(1)
})
