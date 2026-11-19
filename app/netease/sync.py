"""持久化同步任务与事务执行；保留增量编码、失败重试和并发写保护。

编码后的数据变化检查与曲库写入保持在同一事务内，不为缩短函数拆散原子边界。
"""
import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from fastapi import APIRouter, HTTPException
from app.paths import DATA_ROOT
from app.embedding import embed_texts, get_model_identity
from app.embedding_store import embedding_text, read_profile, write_embedding, invalidate_profile
from app.library_db import ensure_library_schema
from app.netease import catalog, metadata
from app.netease.schemas import SyncRequest
from app.safe_logging import log_failure
from app import audio_observations
from app import library_removal

DB_PATH = DATA_ROOT / "library.db"
_sync_lock = threading.RLock()
_sync_threads = {}
_sync_stops = {}
router = APIRouter(prefix="/api/ncm")

_TASK_FIELDS = {
    "metadata_processed", "metadata_total", "metadata_failed",
    "status", "phase", "processed", "total", "added", "updated",
    "skipped_playlists", "completed_playlists", "failed_playlists", "error",
    "failed_playlist_ids", "recoverable", "finished_at",
}
def _utc_now():
    """SQLite 中统一保存 UTC ISO 时间，避免本地时区变化破坏任务排序。"""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _update_sync_task(task_id, **changes):
    """小步提交进度，使另一个请求可以在同步过程中读到真实状态。"""
    unknown = set(changes) - _TASK_FIELDS
    if unknown:
        raise ValueError(f"未知同步任务字段: {sorted(unknown)}")
    changes["updated_at"] = _utc_now()
    assignments = ", ".join(f"{key}=?" for key in changes)
    values = [json.dumps(value, ensure_ascii=False) if key == "failed_playlist_ids" else value
              for key, value in changes.items()]
    with sqlite3.connect(DB_PATH, timeout=10) as conn:
        conn.execute("PRAGMA busy_timeout=10000")
        conn.execute(
            f"UPDATE ncm_sync_tasks SET {assignments} WHERE task_id=?",
            (*values, task_id),
        )


def _task_row(task_id):
    ensure_library_schema(DB_PATH)
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM ncm_sync_tasks WHERE task_id=?", (task_id,)
        ).fetchone()
    if not row:
        return None
    data = dict(row)
    from app.library_db import library_summary
    data["tag_coverage"] = library_summary(DB_PATH)["tag_coverage"]
    data["playlist_ids"] = json.loads(data["playlist_ids"] or "[]")
    data["failed_playlist_ids"] = json.loads(data["failed_playlist_ids"] or "[]")
    data["replace"] = bool(data.pop("replace_library"))
    data["recoverable"] = bool(data["recoverable"])
    data["ok"] = data["status"] == "completed"
    return data


def _create_sync_task(req):
    """先持久化请求再启动线程；即使进程随后退出，也保留可恢复证据。"""
    ensure_library_schema(DB_PATH)
    task_id = uuid.uuid4().hex
    now = _utc_now()
    with sqlite3.connect(DB_PATH, timeout=10) as conn:
        conn.execute(
            """INSERT INTO ncm_sync_tasks(
                task_id, status, phase, scope, playlist_ids, replace_library,
                created_at, updated_at
            ) VALUES (?,?,?,?,?,?,?,?)""",
            (
                task_id, "pending", "queued", req.scope,
                json.dumps(req.playlist_ids or [], ensure_ascii=False),
                int(req.replace), now, now,
            ),
        )
    return task_id


def _execute_sync_task(task_id, req):
    """执行一次可观察的增量同步；每个歌单失败时继续处理其余歌单。"""
    try:
        _update_sync_task(task_id, status="running", phase="authenticating")
        context, error = catalog._sync_context()
        if error:
            raise RuntimeError(error)
        _, uid, headers = context
        if req.scope == "metadata":
            if req.replace:
                raise RuntimeError("补全百科不能重建曲库")
            result = _enrich_task(task_id, headers)
            _finish_metadata_task(task_id, result)
            return

        _update_sync_task(task_id, phase="fetching_playlists")
        playlists = catalog._fetch_user_playlists(uid, headers)
        selected = catalog._select_playlists(playlists, req)
        if not selected:
            message = "没找到「我喜欢的音乐」歌单" if req.scope == "liked" else "请选择至少一个自己的歌单"
            raise RuntimeError(message)

        with sqlite3.connect(DB_PATH) as conn:
            removal_revision = library_removal.removal_revision(conn)
            removed_playlists = library_removal.excluded_ids(conn, "playlist")
            removed_songs = library_removal.excluded_ids(conn, "song")
        selected = [p for p in selected if str(p["id"]) not in removed_playlists]
        if not selected:
            _update_sync_task(task_id, status="completed", phase="finished", total=0, processed=0,
                              skipped_playlists=len(removed_playlists), finished_at=_utc_now())
            return

        summaries = {str(p["id"]): catalog._playlist_summary(p) for p in selected}
        expected_total = sum(item["track_count"] for item in summaries.values())
        _update_sync_task(task_id, phase="fetching_tracks", total=expected_total)

        with sqlite3.connect(DB_PATH) as conn:
            snapshots = {
                row[0]: (int(row[1]), int(row[2]), int(row[3]))
                for row in conn.execute(
                    """SELECT playlist_id, remote_updated_at, track_count,
                              synced_track_count
                       FROM ncm_playlists"""
                )
            }

        fetched = {}
        failed = []
        retryable_failed = []
        failure_messages = []
        processed = 0
        resolved_total = expected_total
        skipped = 0
        for playlist in selected:
            pid = str(playlist["id"])
            summary = summaries[pid]
            fingerprint = (summary["remote_updated_at"], summary["track_count"])
            snapshot = snapshots.get(pid)
            if not req.replace and snapshot and snapshot[:2] == fingerprint:
                skipped += 1
                actual_count = snapshot[2]
                resolved_total += actual_count - summary["track_count"]
                processed += actual_count
                _update_sync_task(
                    task_id, processed=processed, total=resolved_total,
                    skipped_playlists=skipped,
                )
                continue
            try:
                fetched[pid] = catalog._fetch_playlist_tracks(pid, headers)
                actual_count = len(fetched[pid])
                resolved_total += actual_count - summary["track_count"]
                processed += actual_count
                _update_sync_task(
                    task_id, processed=min(processed, resolved_total), total=resolved_total,
                )
            except Exception as exc:
                failed.append(pid)
                is_recoverable = not (
                    isinstance(exc, HTTPException)
                    and exc.status_code in {400, 403, 404}
                )
                if is_recoverable:
                    retryable_failed.append(pid)
                detail = exc.detail if isinstance(exc, HTTPException) else "远端读取暂时失败，请重试"
                failure_message = f"歌单「{summary['name']}」读取失败：{detail or '未知错误'}"
                failure_messages.append(failure_message)
                _update_sync_task(
                    task_id,
                    failed_playlists=len(failed),
                    failed_playlist_ids=retryable_failed,
                    error="；".join(failure_messages),
                    recoverable=int(bool(retryable_failed)),
                )

        # “重建曲库”会清空现有数据，必须等所有远端歌单都完整取回；任何部分失败
        # 都保留旧曲库，避免一次网络抖动把用户已有数据变成残缺快照。
        if req.replace and failed:
            raise RuntimeError("部分歌单读取失败，已保留原曲库；请重试后再执行重建")

        # 只对新增或元数据实际变化的歌曲重算向量；重复同步不会加载 BGE。
        tracks_by_id = {}
        for tracks in fetched.values():
            for track in tracks:
                song = catalog._normalise_track(track)
                if song and song["song_id"] not in removed_songs:
                    tracks_by_id[song["song_id"]] = song
        with sqlite3.connect(DB_PATH) as conn:
            existing = {
                row[0]: tuple(row[1:])
                for row in conn.execute(
                    "SELECT song_id, title, artist, album, duration_seconds, aliases FROM songs"
                )
            }
            present_vectors = {row[0] for row in conn.execute("SELECT song_id FROM song_embeddings")}
            # 编码前观察档案，只为补缺向量使用；真正元数据变化不继承旧推断。
            planned_profiles = {sid: read_profile(conn, sid) for sid in tracks_by_id
                                if sid in existing and sid not in present_vectors}
        changed_songs = [
            song for song in tracks_by_id.values()
            if req.replace or existing.get(song["song_id"], ())[:4] != (
                song["title"], song["artist"], song["album"],
                song["duration_seconds"],
            ) or song["song_id"] not in present_vectors
        ]
        text_changed = {s["song_id"] for s in changed_songs if req.replace or existing.get(s["song_id"], ())[:3]
                        != (s["title"], s["artist"], s["album"])}
        embedding_songs = [s for s in changed_songs if s["song_id"] in text_changed or s["song_id"] not in present_vectors]
        encoding_profiles = {s["song_id"]: None if s["song_id"] in text_changed else planned_profiles.get(s["song_id"])
                             for s in embedding_songs}
        new_count = sum(song["song_id"] not in existing for song in changed_songs)
        updated_count = len(changed_songs) - new_count
        _update_sync_task(task_id, phase="embedding", added=new_count, updated=updated_count)
        embeddings = embed_texts([embedding_text(s, encoding_profiles[s["song_id"]]) for s in embedding_songs]) if embedding_songs else []
        if len(embeddings) != len(embedding_songs):
            raise RuntimeError("同步编码数量不一致，原曲库未更新")
        vectors_by_id = {s["song_id"]: v for s, v in zip(embedding_songs, embeddings)}
        model_identity = get_model_identity()

        _update_sync_task(task_id, phase="saving")
        now = _utc_now()
        with sqlite3.connect(DB_PATH, timeout=10) as conn:
            conn.execute("PRAGMA busy_timeout=10000")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("BEGIN IMMEDIATE")
            if library_removal.removal_revision(conn) != removal_revision:
                raise RuntimeError("同步期间本地移除设置发生变化，请重试")
            # 生成期间其他写入者可能更新作品或建档；不覆盖迟到的观察快照。
            for song in changed_songs:
                sid = song["song_id"]
                current = conn.execute("SELECT title,artist,album,duration_seconds,aliases FROM songs WHERE song_id=?", (sid,)).fetchone()
                if current != existing.get(sid):
                    raise RuntimeError("同步期间歌曲数据发生变化，请重试")
                if sid in vectors_by_id and sid not in text_changed and read_profile(conn, sid) != planned_profiles.get(sid):
                    raise RuntimeError("同步期间歌曲档案发生变化，请重试")
            if req.replace:
                # 旧租约和新曲库不能跨世代；与删旧/写新歌曲共同提交或回滚。
                audio_observations.invalidate_library(conn)
                conn.execute("DELETE FROM ncm_playlist_songs")
                conn.execute("DELETE FROM ncm_playlists")
                conn.execute("DELETE FROM song_platform_metadata")
                conn.execute("DELETE FROM song_profiles")
                conn.execute("DELETE FROM song_embeddings")
                conn.execute("DELETE FROM song_embedding_provenance")
                conn.execute("DELETE FROM song_profile_provenance")
                conn.execute("DELETE FROM songs")
                try:
                    conn.execute("DELETE FROM audio_features")
                except sqlite3.OperationalError:
                    pass
            for song in changed_songs:
                sid = song["song_id"]
                if not req.replace and sid in existing and existing[sid][:4] != (
                    song["title"], song["artist"], song["album"], song["duration_seconds"],
                ):
                    # 时长也是作品绑定的一部分；补缺向量/搜索文案不应使真实观察失效。
                    audio_observations.invalidate_song(conn, sid)
                if not req.replace and sid in text_changed and sid in existing:
                    old = dict(zip(("title", "artist", "album", "duration_seconds", "aliases"), existing[sid]))
                    invalidate_profile(conn, {"song_id": sid, **old})
                conn.execute(
                    """INSERT INTO songs VALUES (?,?,?,?,?,?,?) ON CONFLICT(song_id) DO UPDATE SET
                        title=excluded.title,artist=excluded.artist,album=excluded.album,
                        duration_seconds=excluded.duration_seconds,
                        search_text=CASE WHEN songs.title IS NOT excluded.title OR songs.artist IS NOT excluded.artist
                          OR songs.album IS NOT excluded.album THEN excluded.search_text ELSE songs.search_text END""",
                    (
                        song["song_id"], song["title"], song["artist"], song["album"],
                        song["duration_seconds"], "", song["search_text"],
                    ),
                )
                if sid in vectors_by_id:
                    write_embedding(conn, song, vectors_by_id[sid], profile=encoding_profiles[sid],
                                    writer="netease_sync", model_identity=model_identity)
            for pid, tracks in fetched.items():
                summary = summaries[pid]
                conn.execute(
                    """INSERT OR REPLACE INTO ncm_playlists(
                        playlist_id, name, track_count, synced_track_count,
                        remote_updated_at, cover_url, is_liked, synced_at
                    ) VALUES (?,?,?,?,?,?,?,?)""",
                    (
                        pid, summary["name"], summary["track_count"], len(tracks),
                        summary["remote_updated_at"],
                        summary["cover_url"], int(summary["is_liked"]), now,
                    ),
                )
                conn.execute("DELETE FROM ncm_playlist_songs WHERE playlist_id=?", (pid,))
                relations = [
                    (pid, str(track.get("id")), position, now)
                    for position, track in enumerate(tracks)
                    if str(track.get("id") or "") in tracks_by_id
                ]
                conn.executemany(
                    "INSERT INTO ncm_playlist_songs VALUES (?,?,?,?)", relations,
                )
                conn.execute("UPDATE ncm_playlists SET synced_track_count=? WHERE playlist_id=?", (len(relations), pid))
            # 歌单改名或换封面不一定改变 trackUpdateTime；即使跳过曲目，也刷新
            # 这些轻量展示字段，但不触碰歌曲关系和 embedding。
            for pid, summary in summaries.items():
                if pid not in fetched:
                    conn.execute(
                        """UPDATE ncm_playlists
                           SET name=?, cover_url=?, is_liked=? WHERE playlist_id=?""",
                        (
                            summary["name"], summary["cover_url"],
                            int(summary["is_liked"]), pid,
                        ),
                    )
            conn.execute(
                "INSERT OR REPLACE INTO library_meta(key, value) VALUES ('netease_uid', ?)",
                (str(uid),),
            )
            conn.execute(
                "INSERT OR REPLACE INTO library_meta(key, value) VALUES ('last_sync_scope', ?)",
                (req.scope,),
            )

        from app.library import invalidate_library_cache
        invalidate_library_cache()
        metadata_result = _enrich_task(task_id, headers, playlist_ids=[str(p["id"]) for p in selected])
        metadata_incomplete = metadata_result["failed"] or metadata_result["interrupted"]
        status = "partial_failed" if failed or metadata_incomplete else "completed"
        if metadata_incomplete:
            failure_messages.append("曲目已保存，百科补全未完成；可重试，现有曲库可继续使用")
        final_error = "；".join(failure_messages) or None
        _update_sync_task(
            task_id,
            status=status,
            phase="finished",
            # 完整成功收口到 100%；部分失败保留真实已处理数，让 UI 明确显示缺口。
            processed=processed if failed else resolved_total,
            total=resolved_total,
            completed_playlists=len(selected) - len(failed),
            failed_playlists=len(failed),
            failed_playlist_ids=retryable_failed,
            recoverable=int(bool(retryable_failed) or bool(metadata_incomplete)),
            error=final_error,
            finished_at=_utc_now(),
        )
        print(
            f"[ncm] 增量同步完成：{len(selected)} 个歌单，跳过 {skipped} 个，"
            f"新增 {new_count} 首，更新 {updated_count} 首，失败 {len(failed)} 个"
        )
    except Exception as exc:
        log_failure("ncm_sync_failed", exc)
        _update_sync_task(
            task_id,
            status="failed",
            phase="failed",
            error="同步未完成，请检查网络后重试；已保存的曲库仍可使用",
            recoverable=1,
            finished_at=_utc_now(),
        )
    finally:
        with _sync_lock:
            _sync_threads.pop(task_id, None)
            _sync_stops.pop(task_id, None)


def _enrich_task(task_id, headers, playlist_ids=None):
    ids = None
    if playlist_ids is not None:
        with sqlite3.connect(DB_PATH) as conn:
            ids = {r[0] for r in conn.execute(
                "SELECT song_id,playlist_id FROM ncm_playlist_songs") if r[1] in playlist_ids}
    return metadata.enrich(DB_PATH, headers, song_ids=ids,
        progress=lambda **kw: _update_sync_task(task_id, **kw),
        stopped=lambda: bool(_sync_stops.get(task_id) and _sync_stops[task_id].is_set()))


def _finish_metadata_task(task_id, result):
    incomplete = result["failed"] or result["interrupted"]
    _update_sync_task(task_id, status="partial_failed" if incomplete else "completed",
        phase="finished", recoverable=int(bool(incomplete)),
        error="百科补全未完成；已完成部分保留，可以重试" if incomplete else None,
        finished_at=_utc_now())


@router.post("/sync/{task_id}/pause")
def pause_metadata(task_id: str):
    task = _task_row(task_id)
    if not task or task["phase"] != "metadata":
        raise HTTPException(409, "只能暂停正在补全的百科任务")
    with _sync_lock:
        event = _sync_stops.get(task_id)
        if event:
            event.set()
    return {"ok": True}


def _start_sync_task(task_id, req):
    """每个任务只启动一次后台线程，避免双击导致重复写库。"""
    with _sync_lock:
        current = _sync_threads.get(task_id)
        if current and current.is_alive():
            return
        _sync_stops[task_id] = threading.Event()
        thread = threading.Thread(
            target=_execute_sync_task,
            args=(task_id, req),
            name=f"ncm-sync-{task_id[:8]}",
            daemon=True,
        )
        _sync_threads[task_id] = thread
        thread.start()


def _active_sync_task_id():
    """SQLite 只允许一个同步写任务；拒绝并发比让两个模型计算互相争库更可控。"""
    with _sync_lock:
        for task_id, thread in _sync_threads.items():
            if thread.is_alive():
                return task_id
    return None


@router.post("/sync", status_code=202)
def ncm_sync(req: SyncRequest | None = None):
    """创建持久化同步任务并立即返回，前端通过状态接口观察真实进度。"""
    with _sync_lock:
        active = _active_sync_task_id()
        if active:
            raise HTTPException(409, f"已有同步任务正在运行：{active}")
        req = req or SyncRequest()
        task_id = _create_sync_task(req)
        _start_sync_task(task_id, req)
    return {"ok": True, "task_id": task_id, "status": "pending", "phase": "queued"}


def _mark_task_interrupted_if_orphaned(task):
    """进程内没有对应线程时，将磁盘上的运行态解释为上次进程中断。"""
    if not task or task["status"] not in {"pending", "running"}:
        return task
    with _sync_lock:
        alive = bool(
            _sync_threads.get(task["task_id"])
            and _sync_threads[task["task_id"]].is_alive()
        )
    if alive:
        return task
    _update_sync_task(
        task["task_id"],
        status="interrupted",
        phase="interrupted",
        error="同步因服务退出而中断，可以从原范围重新尝试",
        recoverable=1,
        finished_at=_utc_now(),
    )
    return _task_row(task["task_id"])


@router.get("/sync/latest")
def ncm_latest_sync():
    """让页面刷新后找回未完成任务；完整成功的历史不强制再次弹出。"""
    ensure_library_schema(DB_PATH)
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            """SELECT task_id FROM ncm_sync_tasks
               WHERE status IN ('pending', 'running', 'partial_failed', 'failed', 'interrupted')
               ORDER BY created_at DESC LIMIT 1"""
        ).fetchone()
    if not row:
        return {"task": None}
    return {"task": _mark_task_interrupted_if_orphaned(_task_row(row[0]))}


@router.get("/sync/{task_id}")
def ncm_sync_status(task_id: str):
    """返回任务快照；服务重启遗留的运行态会显式变为可恢复中断。"""
    task = _task_row(task_id)
    if not task:
        raise HTTPException(404, "同步任务不存在")
    return _mark_task_interrupted_if_orphaned(task)


@router.post("/sync/{task_id}/retry", status_code=202)
def ncm_sync_retry(task_id: str):
    """失败歌单优先单独重试；全局失败则复用原同步范围。"""
    previous = _task_row(task_id)
    if not previous:
        raise HTTPException(404, "同步任务不存在")
    if not previous["recoverable"]:
        raise HTTPException(409, "这个同步任务不需要重试")
    failed_ids = previous["failed_playlist_ids"]
    if previous.get("metadata_failed") or previous.get("metadata_processed", 0) < previous.get("metadata_total", 0):
        # 百科与歌单都失败时重试原范围；成功项已有缓存，不应只重试失败歌单而遗漏百科。
        failed_ids = []
    req = SyncRequest(
        scope="selected" if failed_ids else previous["scope"],
        playlist_ids=failed_ids or previous["playlist_ids"],
        replace=False,  # 重试永远采用安全合并，不能重复执行破坏性的重建。
    )
    with _sync_lock:
        active = _active_sync_task_id()
        if active:
            raise HTTPException(409, f"已有同步任务正在运行：{active}")
        _update_sync_task(
            task_id, status="retried", phase="retried", recoverable=0,
        )
        new_task_id = _create_sync_task(req)
        _start_sync_task(new_task_id, req)
    return {"ok": True, "task_id": new_task_id, "status": "pending", "phase": "queued"}


@router.post("/sync/{task_id}/dismiss")
def ncm_sync_dismiss(task_id: str):
    """用户确认终态错误后隐藏任务；只改任务状态，不删除任何诊断记录。"""
    task = _task_row(task_id)
    if not task:
        raise HTTPException(404, "同步任务不存在")
    if task["status"] in {"pending", "running"}:
        raise HTTPException(409, "同步仍在运行，不能隐藏")
    _update_sync_task(
        task_id, status="dismissed", phase="dismissed", recoverable=0,
    )
    return {"ok": True}
