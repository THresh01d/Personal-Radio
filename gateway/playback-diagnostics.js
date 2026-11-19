const { AsyncLocalStorage } = require('node:async_hooks')
const { createRequire } = require('node:module')

const playbackRequest = new AsyncLocalStorage()
const networkReasons = new Map([
  ['ECONNRESET', 'connection_reset'],
  ['EPIPE', 'connection_reset'],
  ['ENOTFOUND', 'dns_failure'],
  ['EAI_AGAIN', 'dns_failure'],
  ['ETIMEDOUT', 'connection_timeout'],
  ['ECONNABORTED', 'connection_timeout'],
  ['ECONNREFUSED', 'connection_refused'],
  ['CERT_HAS_EXPIRED', 'tls_error'],
  ['DEPTH_ZERO_SELF_SIGNED_CERT', 'tls_error'],
  ['SELF_SIGNED_CERT_IN_CHAIN', 'tls_error'],
  ['UNABLE_TO_VERIFY_LEAF_SIGNATURE', 'tls_error'],
])

function runGatewayRequest(app, req, res) {
  // 只给已通过本机入口认证的取址请求绑定上下文，并发请求各用自己的响应对象。
  if (req.url.split('?', 1)[0] === '/song/url/v1') {
    return playbackRequest.run(res, () => app(req, res))
  }
  return app(req, res)
}

function captureUpstream(status, error) {
  const res = playbackRequest.getStore()
  if (!res || res.headersSent || res.destroyed) return
  if (Number.isInteger(status) && status >= 100 && status <= 599) {
    res.setHeader('X-Radio-Ncm-Upstream-Status', String(status))
  } else if (error) {
    const code = typeof error.code === 'string' ? error.code : ''
    const reason = code.startsWith('ERR_TLS_') || code.startsWith('ERR_SSL_')
      ? 'tls_error'
      : networkReasons.get(code) || 'network_error'
    res.setHeader('X-Radio-Ncm-Upstream-Error', reason)
  }
}

function installPlaybackDiagnostics() {
  // 必须观察上游包实际使用的 Axios 实例，在它把异常统一成 502 前读取有限字段。
  const upstreamRequire = createRequire(require.resolve('NeteaseCloudMusicApi/util/request.js'))
  const { default: axios } = upstreamRequire('axios')
  axios.interceptors.response.use(response => {
    captureUpstream(response.status)
    return response
  }, error => {
    captureUpstream(error?.response?.status, error)
    throw error
  })
}

module.exports = { installPlaybackDiagnostics, runGatewayRequest }
