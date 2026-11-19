/** 使用真实 Node HTTP 服务验证入口，无上游账号、Cookie 或外网调用。 */
const test = require('node:test')
const assert = require('node:assert/strict')
const http = require('node:http')
const { allowGatewayRequest, installGatewayBoundary } = require('./security')
const token = 'only-for-unit-test-not-a-real-key'.repeat(2)

test('正确后端凭证可用，浏览器跨源、伪 Host 和空凭证均拒绝', () => {
  const headers = { host: '127.0.0.1:3000', 'x-personal-radio-gateway': token }
  assert.equal(allowGatewayRequest({ headers }, token, 3000), true)
  for (const extra of [{ origin: 'http://127.0.0.1:8000' }, { 'sec-fetch-site': 'same-origin' },
    { host: 'evil.example' }, { 'x-personal-radio-gateway': '' }]) {
    assert.equal(allowGatewayRequest({ headers: { ...headers, ...extra } }, token, 3000), false)
  }
})

test('入口守卫先于上游 CORS/静态路由执行，健康检查不触发上游', async () => {
  let calls = 0
  const app = (_req, res) => { calls++; res.setHeader('Access-Control-Allow-Origin', '*'); res.end('upstream') }
  app.server = http.createServer(app)
  await new Promise(resolve => app.server.listen(0, '127.0.0.1', resolve))
  const port = app.server.address().port
  installGatewayBoundary(app, token, port)
  const request = (path, headers) => new Promise((resolve, reject) => {
    http.get(`http://127.0.0.1:${port}${path}`, { headers }, res => {
      res.resume(); res.on('end', () => resolve(res))
    }).on('error', reject)
  })
  try {
    const denied = await request('/login/status', { Origin: 'https://evil.example' })
    assert.equal(denied.statusCode, 403)
    assert.equal(denied.headers['access-control-allow-origin'], undefined)
    assert.equal(calls, 0)
    assert.equal((await request('/__personal_radio_health', { 'X-Personal-Radio-Gateway': token })).statusCode, 200)
    assert.equal(calls, 0)
    assert.equal((await request('/__qq/login/status', {})).statusCode, 403)
    assert.equal((await request('/__qq/search?keyword=test', {})).statusCode, 403)
    for (const route of ['/search', '/singer/songs', '/getAlbumInfo']) {
      assert.equal((await request(`/__qq${route}`, { 'X-Personal-Radio-Gateway': token })).statusCode, 503)
    }
    assert.equal((await request('/__qq/user/playlist/delete', { 'X-Personal-Radio-Gateway': token })).statusCode, 404)
    assert.equal((await request('/__qq/login/status', { 'X-Personal-Radio-Gateway': token })).statusCode, 503)
    assert.equal(calls, 0)
    assert.equal((await request('/login/status', { 'X-Personal-Radio-Gateway': token })).statusCode, 200)
    assert.equal(calls, 1)
  } finally {
    await new Promise(resolve => app.server.close(resolve))
  }
})
