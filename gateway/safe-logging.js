/** 上游自由格式日志不可证明安全，省略载荷而非猜测所有 Cookie/Key 字段。 */
function installSafeConsole(target = console) {
  const log = target.log.bind(target)
  const error = target.error.bind(target)
  const warn = target.warn.bind(target)
  const seen = new Set()
  // 每类只记录一次，未知上游高频输出不能无限占用磁盘。
  for (const [method, sink] of [['log', log], ['info', log], ['debug', log], ['dir', log],
    ['error', error], ['warn', warn], ['trace', warn]]) {
    target[method] = () => {
      if (seen.has(method)) return
      seen.add(method)
      sink(`[ncm-upstream] ${method} 载荷已省略`)
    }
  }
  return { log, error, warn }
}
module.exports = { installSafeConsole }
