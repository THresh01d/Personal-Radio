/** QQ 模块复用已认证的回环网关，只开放私人电台实际使用的只读和登录操作。 */
const path = require('node:path')

const routes = new Set([
  '/login/qr/key', '/login/qr/create', '/login/qr/check', '/login/qr/cancel',
  '/login/status', '/logout', '/user/playlist', '/user/playlist-detail', '/user/liked-songs',
  '/search', '/singer/songs', '/getAlbumInfo',
])
let handler

function createHandler(secret, folder) {
  // 依赖公开入口的测试模式只装配 Koa，不另起一个未受保护的 HTTP 端口。
  const overrides = {
    NODE_ENV: 'test', LOG_LEVEL: 'silent', QQ_SESSION_SECRET: secret,
    QQ_AUTH_SESSION_PATH: path.join(folder, 'sessions.enc'),
    QQ_AUTH_STATE_PATH: path.join(folder, 'device.json'),
    QQ_PLAYBACK_FALLBACK_ENABLED: '0',
  }
  const previous = Object.fromEntries(Object.keys(overrides).map(key => [key, process.env[key]]))
  Object.assign(process.env, overrides)
  try {
    const qq = require('@yakult-green-tea/qq-music-api')
    if (qq.server !== null) {
      qq.server?.close()
      throw new Error('QQ 音乐网关入口不兼容')
    }
    return qq.default.callback()
  } finally {
    for (const [key, value] of Object.entries(previous)) {
      if (value === undefined) delete process.env[key]
      else process.env[key] = value
    }
  }
}

function handleQQMusic(req, res) {
  const target = req.url.slice('/__qq'.length)
  const pathname = target.split('?', 1)[0]
  const secret = req.headers['x-radio-qq-key']
  const folder = process.env.PERSONAL_RADIO_QQ_STATE_DIR
  if (req.method !== 'GET' || (!routes.has(pathname) && !/^\/getMusicPlay\/[a-z0-9]{1,40}$/i.test(pathname))) {
    res.writeHead(404).end()
    return
  }
  if (typeof secret !== 'string' || !/^[\w-]{43}$/.test(secret) || !folder) {
    res.writeHead(503).end()
    return
  }
  if ((pathname.startsWith('/user/') || pathname.startsWith('/getMusicPlay/')) &&
      !/^qqmusic_session=[A-Za-z0-9_.~-]{16,200}$/.test(req.headers.cookie ?? '')) {
    res.writeHead(401).end()
    return
  }
  try {
    handler ??= createHandler(secret, folder)
  } catch {
    res.writeHead(503).end()
    return
  }
  delete req.headers['x-radio-qq-key']
  req.url = target
  handler(req, res)
}

module.exports = { handleQQMusic }
