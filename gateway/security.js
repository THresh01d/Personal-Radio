/** 网关在 Node HTTP 入口认证，先于上游静态资源、CORS 和任何音乐路由。 */
const { timingSafeEqual } = require('node:crypto')
const { runGatewayRequest } = require('./playback-diagnostics')

function allowGatewayRequest(req, token, port) {
  const host = req.headers.host
  const expected = `127.0.0.1:${port}`
  if (host !== expected || req.headers.origin !== undefined || req.headers['sec-fetch-site'] !== undefined) return false
  const supplied = req.headers['x-personal-radio-gateway']
  if (typeof supplied !== 'string') return false
  const a = Buffer.from(supplied)
  const b = Buffer.from(token)
  return a.length === b.length && timingSafeEqual(a, b)
}

function installGatewayBoundary(app, token, port) {
  const server = app.server
  // 使用公开 HTTP 监听 API 包住我们自己的 app，不依赖 Express 的私有路由栈。
  if (server.listeners('request').length !== 1 || server.listeners('request')[0] !== app) {
    throw new Error('网关入口结构不兼容，拒绝无保护启动')
  }
  server.removeListener('request', app)
  server.on('request', (req, res) => {
    if (!allowGatewayRequest(req, token, port)) {
      res.writeHead(403, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' })
      res.end(JSON.stringify({ message: '仅允许私人电台后端访问' }))
      return
    }
    if (req.url === '/__personal_radio_health') {
      res.writeHead(200, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' })
      res.end(JSON.stringify({ service: 'personal-radio-gateway-v1' }))
      return
    }
    if (req.url?.startsWith('/__qq/')) {
      require('./qq-music').handleQQMusic(req, res)
      return
    }
    runGatewayRequest(app, req, res)
  })
}

module.exports = { allowGatewayRequest, installGatewayBoundary }
