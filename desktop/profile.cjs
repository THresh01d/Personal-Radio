const fs = require('node:fs');
const path = require('node:path');
const { createHash } = require('node:crypto');

const marker = '.personal-radio-migration';

function fileOrMissing(filename, read) {
  try { return read(filename); }
  catch (error) {
    if (error.code !== 'ENOENT') throw error;
    return null;
  }
}

function key(directory) {
  return path.resolve(directory).toLowerCase();
}

function contains(parent, child) {
  const prefix = key(parent);
  const entry = key(child);
  return entry === prefix || entry.startsWith(prefix + path.sep);
}

function diskPath(directory) {
  if (!/^[a-z]:[\\/]/i.test(directory) || /[\u0000-\u001f]/.test(directory)) {
    throw new Error('请选择本机磁盘上的完整目录。');
  }
  const resolved = path.resolve(directory);
  if (resolved === path.parse(resolved).root) throw new Error('请在该磁盘中选择一个子目录。');
  const existing = fileOrMissing(resolved, fs.realpathSync.native);
  return existing ?? path.join(diskPathParent(path.dirname(resolved)), path.basename(resolved));
}

function diskPathParent(directory) {
  const resolved = fileOrMissing(directory, fs.realpathSync.native);
  if (!resolved && directory === path.dirname(directory)) throw new Error('所选磁盘无法访问。');
  return resolved ?? path.join(diskPathParent(path.dirname(directory)), path.basename(directory));
}

function digest(filename, buffer) {
  const hash = createHash('sha256');
  const file = fs.openSync(filename, 'r');
  try {
    let size;
    while ((size = fs.readSync(file, buffer)) !== 0) hash.update(buffer.subarray(0, size));
  } finally { fs.closeSync(file); }
  return hash.digest('hex');
}

function snapshot(directory) {
  const files = new Map();
  const buffer = Buffer.alloc(1024 * 1024);
  function visit(folder, relative = '') {
    for (const entry of fs.readdirSync(folder, { withFileTypes: true })) {
      if (!relative && entry.name === marker) continue;
      const filename = path.join(folder, entry.name);
      const name = path.join(relative, entry.name);
      if (entry.isDirectory()) {
        files.set(name, 'directory');
        visit(filename, name);
      } else if (entry.isFile()) {
        files.set(name, digest(filename, buffer));
      } else {
        throw new Error('旧数据目录包含链接或特殊文件，无法安全迁移。');
      }
    }
  }
  if (fileOrMissing(directory, fs.lstatSync)) visit(directory);
  return files;
}

function verifyCopy(expected, directory) {
  const actual = snapshot(directory);
  if (actual.size !== expected.size || [...expected].some(([name, hash]) => actual.get(name) !== hash)) {
    throw new Error('迁移副本校验失败。原数据仍可使用。');
  }
}

function moveProfile(current, target) {
  const expected = snapshot(current);
  const targetStat = fileOrMissing(target, fs.lstatSync);
  const journal = JSON.stringify({ current, target });
  if (targetStat) {
    if (!targetStat.isDirectory()) throw new Error('请选择一个空的数据目录。');
    if (fileOrMissing(path.join(target, marker), filename => fs.readFileSync(filename, 'utf8')) === journal) {
      // 上次若在切换目录前退出，可重新校验已复制的副本。
      verifyCopy(expected, target);
      return;
    }
    if (fs.readdirSync(target).length) throw new Error('请选择一个空的数据目录，现有文件已保留。');
  }

  const parent = path.dirname(target);
  // Windows 会拒绝对已存在的盘符根目录调用递归 mkdir。
  if (!fs.existsSync(parent)) fs.mkdirSync(parent, { recursive: true });
  const staging = fs.mkdtempSync(target + '.migration-');
  const stagedProfile = path.join(staging, 'profile');
  try {
    if (expected.size) fs.cpSync(current, stagedProfile, {
      recursive: true, force: false, errorOnExist: true,
      filter: source => source !== path.join(current, marker),
    });
    else fs.mkdirSync(stagedProfile);
    verifyCopy(expected, stagedProfile);
    fs.writeFileSync(path.join(stagedProfile, marker), journal, { flag: 'wx' });
    if (targetStat) fs.rmdirSync(target);
    fs.renameSync(stagedProfile, target);
  } finally {
    // 仅清除本次创建的暂存目录。原目录供用户回退。
    fs.rmSync(staging, { recursive: true, force: true });
  }
}

function prepareProfile(legacy, control, installation) {
  const active = path.join(control, 'active.txt');
  const readDirectory = filename => fileOrMissing(filename, file =>
    diskPath(fs.readFileSync(file, 'utf16le').replace(/^\uFEFF/, '').replace(/[\r\n]+$/, '')));
  const saved = readDirectory(active);
  const current = saved ?? diskPath(legacy);
  const target = readDirectory(path.join(control, 'requested.txt')) ?? current;
  if (contains(installation, target) || contains(target, installation)
      || contains(control, target) || contains(target, control)) {
    throw new Error('程序安装位置与用户数据位置需要使用各自的目录。');
  }
  if (saved && !fs.existsSync(current)) {
    throw new Error('正在使用的用户数据目录无法访问。请连接原磁盘后再打开电台。');
  }
  if (key(current) !== key(target)) {
    if (contains(current, target) || contains(target, current)) {
      throw new Error('请选择与原数据目录独立的新目录。');
    }
    moveProfile(current, target);
  } else {
    fs.mkdirSync(target, { recursive: true });
  }
  // 目录指针只在完整副本可用后切换。
  if (saved !== target) {
    const temporary = path.join(control, 'active.tmp');
    fs.writeFileSync(temporary, '\uFEFF' + target, 'utf16le');
    fs.renameSync(temporary, active);
  }
  return target;
}

module.exports = { prepareProfile };
