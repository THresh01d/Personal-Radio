"""有界并发百科补全；单线程落盘，保留逐首缓存、暂停和作品绑定保护。"""
import sqlite3
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

from app import platform_metadata as metadata
from app.embedding_store import metadata_hash
from app.library_removal import removal_revision
from app.netease import gateway

EMPTY_TTL = 7 * 86400
MAX_IN_FLIGHT = 3
START_RATE = 8
FAST_RATE = 10
FALLBACK_RATE = 5
REQUEST_INTERVAL = 1 / START_RATE
SUCCESS_WINDOW = 30
RECOVERY_SECONDS = 30
FAILURE_LIMIT = 3
FAILURE_BACKOFF = 0.5
MAX_BACKOFF = 2.0


class _AdaptivePace:
    """成功窗口才升档，故障后先冷却；状态仅属于本次任务，不持久化私人数据。"""

    def __init__(self):
        self.rate = START_RATE
        self._successes = 0
        self._recover_at = 0.0

    @property
    def interval(self):
        # 沿用单一任务间隔；测试将基准置零时，各档等待也同步归零。
        return REQUEST_INTERVAL * START_RATE / self.rate

    def record(self, failed, now):
        if failed:
            self.rate = FALLBACK_RATE
            self._successes = 0
            self._recover_at = now + RECOVERY_SECONDS
        elif now >= self._recover_at and self.rate < FAST_RATE:
            self._successes += 1
            if self._successes >= SUCCESS_WINDOW:
                self.rate = START_RATE if self.rate == FALLBACK_RATE else FAST_RATE
                self._successes = 0


def fetch_wiki(song_id, headers):
    return gateway._gw("/song/wiki/summary", params={"id": song_id}, headers=headers, timeout=8)


def _fetch_result(song, headers):
    """线程只取最小百科，不操作数据库、任务进度或共享曲库缓存。"""
    try:
        # UGC 响应夹带完整歌词和临时音源，本阶段只读取百科基本信息。
        payload = metadata.parse_wiki(fetch_wiki(song["song_id"], headers))
        status = "ready" if any(payload.get(k) for k in ("genre", "raw_genres", "raw_moods", "language", "bpm")) else "empty"
        return payload, status
    except Exception:
        # 异常可能带 Cookie；跨线程也只传固定状态，不保留异常或响应。
        return {}, "failed"


def enrich(db_path, headers, *, song_ids=None, progress=lambda **kw: None, stopped=lambda: False):
    with sqlite3.connect(db_path) as conn:
        metadata.ensure_schema(conn)
        rows = conn.execute("""SELECT DISTINCT s.song_id,s.title,s.artist,s.album
            FROM songs s JOIN ncm_playlist_songs ps USING(song_id) ORDER BY s.song_id""").fetchall()
        cached = {r[0]: r[1:] for r in conn.execute(
            "SELECT song_id,metadata_sha256,version,status,checked_at FROM song_platform_metadata")}
        revision = removal_revision(conn)
    wanted = None if song_ids is None else set(song_ids)
    pending = []
    for row in rows:
        song = dict(zip(("song_id", "title", "artist", "album"), row))
        sid = song["song_id"]
        if wanted is not None and sid not in wanted:
            continue
        old = cached.get(sid)
        if old and old[0] == metadata_hash(song) and old[1] == metadata.VERSION:
            if old[2] == "ready" or (old[2] == "empty" and time.time() - old[3] < EMPTY_TTL):
                continue
        pending.append(song)
    total, processed, failed, consecutive = len(pending), 0, 0, 0
    progress(phase="metadata", metadata_total=total, metadata_processed=0, metadata_failed=0)
    if not total:
        return {"failed": 0, "interrupted": False}

    cursor, next_submit = 0, 0.0
    accepting, stale = True, False
    inflight = {}
    pace = _AdaptivePace()
    # 三首在途共用自适应间隔，升档也不能让每个线程分别按该频率请求。
    with ThreadPoolExecutor(max_workers=MAX_IN_FLIGHT, thread_name_prefix="ncm-wiki") as executor:
        while inflight or (accepting and cursor < total):
            if stopped():
                accepting = False
            ready = sorted((f for f in inflight if f.done()), key=lambda f: inflight[f][0])
            for future in ready:
                _, song = inflight.pop(future)
                payload, status = future.result()
                if stale:
                    continue
                with sqlite3.connect(db_path, timeout=10) as conn:
                    conn.execute("BEGIN IMMEDIATE")
                    current = conn.execute("SELECT title,artist,album FROM songs WHERE song_id=?", (song["song_id"],)).fetchone()
                    if removal_revision(conn) != revision or current != (song["title"], song["artist"], song["album"]):
                        # 发现移除或作品变化后停止补发，其余在途只等待结束，不再写入。
                        stale, accepting = True, False
                        continue
                    metadata.save(conn, song, payload, status, error_code="wiki_unavailable" if status == "failed" else None)
                processed += 1
                pace.record(status == "failed", time.monotonic())
                if status == "failed":
                    failed += 1
                    consecutive += 1
                    delay = min(MAX_BACKOFF, FAILURE_BACKOFF * 2 ** (consecutive - 1))
                    next_submit = max(next_submit, time.monotonic() + delay)
                    if consecutive >= FAILURE_LIMIT:
                        accepting = False
                else:
                    consecutive = 0
                progress(metadata_processed=processed, metadata_failed=failed)
                from app.library import invalidate_library_cache
                invalidate_library_cache()
            # 尚未确认的请求也占失败预算，避免三次连续故障后额外发出一批请求。
            capacity = min(MAX_IN_FLIGHT, FAILURE_LIMIT - consecutive)
            can_submit = accepting and cursor < total and len(inflight) < capacity
            now = time.monotonic()
            if can_submit and now >= next_submit and not stopped():
                inflight[executor.submit(_fetch_result, pending[cursor], headers)] = (cursor, pending[cursor])
                cursor += 1
                next_submit = time.monotonic() + pace.interval
                continue
            if not inflight and (not accepting or cursor >= total):
                break
            delay = min(0.05, max(0.0, next_submit - now)) if can_submit else 0.05
            if inflight:
                wait(inflight, timeout=delay, return_when=FIRST_COMPLETED)
            else:
                time.sleep(delay)
    # 暂停会等待最多三首已发出的请求落盘；返回后没有线程再写库。
    return {"failed": failed, "interrupted": processed < total}
