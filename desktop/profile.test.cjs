const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const test = require('node:test');
const { prepareProfile } = require('./profile.cjs');

function fixture(t) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'personal-radio-profile-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const legacy = path.join(root, '默认用户目录');
  const control = legacy + '-location';
  const installation = path.join(root, '应用安装');
  const picked = path.join(root, '自选数据目录');
  fs.mkdirSync(control);
  fs.mkdirSync(installation);
  const select = (directory = picked) => {
    fs.writeFileSync(path.join(control, 'requested.txt'), '\uFEFF' + directory, 'utf16le');
  };
  return { legacy, control, installation, picked, select, open: () => prepareProfile(legacy, control, installation) };
}

test('首次安装写入选定目录，重开和同目录升级沿用数据', { skip: process.platform !== 'win32' }, t => {
  const setup = fixture(t);
  setup.select();
  assert.equal(setup.open(), setup.picked);
  assert.equal(fs.existsSync(setup.legacy), false);
  const history = path.join(setup.picked, 'settings');
  fs.writeFileSync(history, '保留这次写入');
  setup.select();
  assert.equal(setup.open(), setup.picked);
  assert.equal(fs.readFileSync(history, 'utf8'), '保留这次写入');
});

test('更换目录时复制原有曲库与浏览器数据，旧数据可回退', { skip: process.platform !== 'win32' }, t => {
  const setup = fixture(t);
  assert.equal(setup.open(), setup.legacy);
  for (const [name, contents] of [['radio-data/library.db', 'library'], ['radio-data/local-port.json', 'origin'], ['Local Storage/leveldb/history', 'queue']]) {
    const filename = path.join(setup.legacy, name);
    fs.mkdirSync(path.dirname(filename), { recursive: true });
    fs.writeFileSync(filename, contents);
  }
  setup.select();
  assert.equal(setup.open(), setup.picked);
  const oldLibrary = path.join(setup.legacy, 'radio-data/library.db');
  const newLibrary = path.join(setup.picked, 'radio-data/library.db');
  assert.equal(fs.readFileSync(path.join(setup.picked, 'Local Storage/leveldb/history'), 'utf8'), 'queue');
  assert.equal(fs.readFileSync(path.join(setup.picked, 'radio-data/local-port.json'), 'utf8'), 'origin');
  assert.equal(fs.readFileSync(newLibrary, 'utf8'), 'library');
  fs.writeFileSync(newLibrary, 'updated');
  assert.equal(setup.open(), setup.picked);
  assert.equal(fs.readFileSync(newLibrary, 'utf8'), 'updated');
  assert.equal(fs.readFileSync(oldLibrary, 'utf8'), 'library');
});

test('目录冲突不会切换数据位置或覆盖旧文件', { skip: process.platform !== 'win32' }, t => {
  const setup = fixture(t);
  assert.equal(setup.open(), setup.legacy);
  const active = path.join(setup.control, 'active.txt');
  const original = fs.readFileSync(active);
  setup.select(path.join(setup.installation, 'data'));
  assert.throws(setup.open, /各自的目录/);
  fs.mkdirSync(setup.picked);
  const existing = path.join(setup.picked, '用户自己的文件');
  fs.writeFileSync(existing, '保留');
  setup.select();
  assert.throws(setup.open, /空的数据目录/);
  assert.equal(fs.readFileSync(existing, 'utf8'), '保留');
  assert.deepEqual(fs.readFileSync(active), original);
});
