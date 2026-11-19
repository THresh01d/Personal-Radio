const REPOSITORY_PATTERN = /^[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?\/[A-Za-z0-9_.-]+$/;

function releaseUrls(repository) {
  if (typeof repository !== 'string' || !REPOSITORY_PATTERN.test(repository)) return null;
  const [, name] = repository.split('/');
  if (name === '.' || name === '..') return null;
  return {
    api: `https://api.github.com/repos/${repository}/releases/latest`,
    page: `https://github.com/${repository}/releases/latest`,
  };
}

function newerRelease(tag, current) {
  const parse = text => /^v?(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$/.exec(text)?.slice(1).map(Number);
  const remote = parse(tag);
  const local = parse(current);
  if (!remote || !local) throw new Error('GitHub 返回的正式版本号不可识别。');
  for (let index = 0; index < 3; index++) {
    if (remote[index] !== local[index]) return remote[index] > local[index];
  }
  return false;
}

async function findUpdate(repository, version, fetchRelease = fetch) {
  const urls = releaseUrls(repository);
  if (!urls) return { status: 'unconfigured' };
  const response = await fetchRelease(urls.api, {
    headers: { Accept: 'application/vnd.github+json' },
    redirect: 'error', signal: AbortSignal.timeout(8000),
  });
  if (response.status === 404) return { status: 'no-release' };
  if (!response.ok) throw new Error(`GitHub 暂时无法提供更新信息（${response.status}）。`);
  const release = await response.json();
  return newerRelease(release.tag_name, version)
    ? { status: 'available', tag: release.tag_name, page: urls.page }
    : { status: 'current' };
}

module.exports = { findUpdate, newerRelease, releaseUrls };
