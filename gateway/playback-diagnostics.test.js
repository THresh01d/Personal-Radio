const test = require('node:test')
const assert = require('node:assert/strict')
const http = require('node:http')
const fs = require('node:fs')
const path = require('node:path')
const { createRequire } = require('node:module')
const { installGatewayBoundary } = require('./security')
const { installPlaybackDiagnostics } = require('./playback-diagnostics')

test('实际取址模块压平错误前记录上游状态，同批并发各自归属', async t => {
  const requestPath = require.resolve('NeteaseCloudMusicApi/util/request.js')
  const upstreamRequire = createRequire(requestPath)
  const axios = upstreamRequire('axios').default
  const anonymousToken = path.resolve(require('node:os').tmpdir(), 'anonymous_token')
  const readFile = fs.readFileSync
  // 上游模块导入时会读本机匿名凭据，验收始终给空的合成值。
  fs.readFileSync = function (file, ...args) {
    if (typeof file === 'string' && path.resolve(file) === anonymousToken) return ''
    return readFile.call(this, file, ...args)
  }
  try {
    require(requestPath)
  } finally {
    fs.readFileSync = readFile
  }
  const playback = require('NeteaseCloudMusicApi/module/song_url_v1.js')
  const previousAdapter = axios.defaults.adapter
  const previousLog = console.log
  const calls = []
  console.log = () => {}
  axios.defaults.adapter = async config => {
    const ids = new URLSearchParams(config.data).get('ids')
    calls.push(ids)
    if (ids === '[3]') {
      return { status: 200, data: { code: 200 }, headers: {}, config }
    }
    // 让第二个请求先结束，覆盖异步上下文隔离。
    if (ids === '[1]') await new Promise(resolve => setTimeout(resolve, 30))
    const error = new Error('private-url synthetic-secret')
    if (ids === '[1]') {
      error.response = { status: 429, data: { code: 429 } }
    } else if (ids === '[2]') {
      error.code = 'ECONNRESET'
    } else {
      error.code = 'unknown-private-code'
    }
    throw error
  }
  t.after(() => { axios.defaults.adapter = previousAdapter; console.log = previousLog })
  installPlaybackDiagnostics()
  const { serveNcmApi } = require('NeteaseCloudMusicApi')
  const token = 'synthetic-gateway-key-for-test'.repeat(2)
  const app = await serveNcmApi({
    port: '0',
    host: '127.0.0.1',
    checkVersion: false,
    moduleDefs: [
      { route: '/song/url/v1', module: playback },
      { route: '/lyric', module: playback },
    ],
  })
  if (!app.server.listening) {
    await new Promise(resolve => app.server.once('listening', resolve))
  }
  t.after(() => {
    const cache = require('NeteaseCloudMusicApi/util/apicache.js')
    for (const key of cache.getIndex().all) cache.clear(key)
    return new Promise(resolve => app.server.close(resolve))
  })
  const port = app.server.address().port
  installGatewayBoundary(app, token, port)
  const request = (route, auth = true) => new Promise((resolve, reject) => {
    const headers = auth ? { 'X-Personal-Radio-Gateway': token } : {}
    http.get(`http://127.0.0.1:${port}${route}&crypto=api`, { headers }, res => {
      res.resume()
      res.on('end', () => resolve(res))
    }).on('error', reject)
  })
  const [rate, reset, good, unknown, other, denied] = await Promise.all([
    request('/song/url/v1?id=1'), request('/song/url/v1?id=2'), request('/song/url/v1?id=3'),
    request('/song/url/v1?id=4'), request('/lyric?id=2'), request('/song/url/v1?id=1', false),
  ])
  assert.equal(rate.statusCode, 502)
  assert.equal(rate.headers['x-radio-ncm-upstream-status'], '429')
  assert.equal(rate.headers['x-radio-ncm-upstream-error'], undefined)
  assert.equal(reset.statusCode, 502)
  assert.equal(reset.headers['x-radio-ncm-upstream-status'], undefined)
  assert.equal(reset.headers['x-radio-ncm-upstream-error'], 'connection_reset')
  assert.equal(good.statusCode, 200)
  assert.equal(good.headers['x-radio-ncm-upstream-status'], '200')
  assert.equal(unknown.headers['x-radio-ncm-upstream-error'], 'network_error')
  assert.equal(other.headers['x-radio-ncm-upstream-error'], undefined)
  assert.equal(denied.statusCode, 403)
  assert.equal(denied.headers['x-radio-ncm-upstream-status'], undefined)
  assert.equal(calls.length, 5)
  assert.doesNotMatch(JSON.stringify([rate.headers, reset.headers, unknown.headers]), /private|secret/)
})
