const assert = require('node:assert/strict');
const { test } = require('node:test');
const { findUpdate, newerRelease, releaseUrls } = require('./updates.cjs');

test('正式版本号按数值比较，预发布和伪造版本不作为正式更新', () => {
  assert.equal(newerRelease('v0.10.3', '0.9.12'), true);
  assert.equal(newerRelease('0.9.8', '0.9.8'), false);
  assert.equal(newerRelease('0.9.8', '1.0.0'), false);
  assert.throws(() => newerRelease('v0.10.3-rc.1', '0.9.12'));
});

test('提示只能指向配置仓库的发布页，上游响应不能改下载目的地', async () => {
  const result = await findUpdate('Example/Personal-Radio', '1.2.3', async (url, options) => {
    assert.equal(url, 'https://api.github.com/repos/Example/Personal-Radio/releases/latest');
    assert.equal(options.redirect, 'error');
    return { ok: true, json: async () => ({ tag_name: 'v1.3.0', html_url: 'https://example.test/install' }) };
  });
  assert.deepEqual(result, { status: 'available', tag: 'v1.3.0', page: 'https://github.com/Example/Personal-Radio/releases/latest' });
  assert.equal(releaseUrls('Example/..'), null);
  assert.equal((await findUpdate(null, '1.2.3', () => assert.fail())).status, 'unconfigured');
});
