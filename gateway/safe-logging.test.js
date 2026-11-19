const test = require('node:test')
const assert = require('node:assert/strict')
const { installSafeConsole } = require('./safe-logging')

test('对象、字符串和 error/warn 自由输出不泄漏凭据且同类日志有上限', () => {
  const lines = []
  const target = { log: (...args) => lines.push(args), error: (...args) => lines.push(args), warn: (...args) => lines.push(args) }
  const own = installSafeConsole(target)
  for (let i = 0; i < 30; i++) for (const method of ['log', 'error', 'warn', 'info', 'debug', 'dir', 'trace']) {
    target[method]('MUSIC_U=synthetic-sensitive-value', { cookie: 'secret', unknown: 'secret' }, new Error('secret'))
  }
  assert.equal(lines.length, 7)
  assert.equal(JSON.stringify(lines).includes('secret'), false)
  assert.equal(JSON.stringify(lines).includes('synthetic-sensitive-value'), false)
  own.log('fixed-application-event')
  assert.equal(lines.length, 8)
})
