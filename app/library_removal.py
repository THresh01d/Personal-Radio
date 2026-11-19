"""本地移除的事务核心：保留其他来源，不触碰云端、会话或长期偏好。"""

from datetime import datetime, timezone
import hashlib
import json
import uuid


class RemovalConflict(ValueError):
    pass


def _tables(conn):
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def ensure_removal_schema(conn):
    # 不用executescript，防止隐式提交使删除只完成一半。
    conn.execute("""CREATE TABLE IF NOT EXISTS local_library_removed (
        kind TEXT NOT NULL, target_id TEXT NOT NULL, metadata_json TEXT NOT NULL,
        removed_at TEXT NOT NULL, PRIMARY KEY(kind,target_id))""")


def removal_revision(conn):
    row = conn.execute("SELECT value FROM library_meta WHERE key='local_removal_revision'").fetchone()
    return row[0] if row else ""


def excluded_ids(conn, kind):
    if "local_library_removed" not in _tables(conn):
        return set()
    return {str(r[0]) for r in conn.execute("SELECT target_id FROM local_library_removed WHERE kind=?", (kind,))}


def _assert_idle(conn):
    if "ncm_sync_tasks" in _tables(conn) and conn.execute(
        "SELECT 1 FROM ncm_sync_tasks WHERE status IN ('pending','running') LIMIT 1"
    ).fetchone():
        raise RemovalConflict("曲库正在同步，请等待同步结束后再移除")


def preview_removal(conn, kind, target_ids):
    """在同一读快照中计算影响与指纹；不建表、不保存私人确认令牌。"""
    ids = list(dict.fromkeys(str(v) for v in target_ids))
    if kind not in {"playlist", "song"} or not ids or len(ids) > 20:
        raise ValueError("移除目标不正确")
    _assert_idle(conn)
    slots = ",".join("?" for _ in ids)
    if kind == "playlist":
        targets = conn.execute(f"SELECT playlist_id,name FROM ncm_playlists WHERE playlist_id IN ({slots}) ORDER BY playlist_id", ids).fetchall()
        relations = conn.execute(f"SELECT playlist_id,song_id FROM ncm_playlist_songs WHERE playlist_id IN ({slots}) ORDER BY playlist_id,song_id", ids).fetchall()
        song_ids = sorted({str(r[1]) for r in relations})
        other_sources = conn.execute(f"""SELECT ps.playlist_id,ps.song_id FROM ncm_playlist_songs ps
            WHERE ps.playlist_id NOT IN ({slots}) AND ps.song_id IN (
                SELECT song_id FROM ncm_playlist_songs WHERE playlist_id IN ({slots}))
            ORDER BY ps.playlist_id,ps.song_id""", [*ids, *ids]).fetchall()
        shared = {str(r[1]) for r in other_sources}
        removed = [sid for sid in song_ids if sid not in shared]
        metadata = []
        # 逐批读取，避免Windows SQLite变量上限与大歌单冲突。
        for start in range(0, len(song_ids), 400):
            batch = song_ids[start:start + 400]
            metadata.extend(conn.execute(f"SELECT song_id,title,artist,album FROM songs WHERE song_id IN ({','.join('?' for _ in batch)}) ORDER BY song_id", batch).fetchall())
    else:
        targets = conn.execute(f"SELECT song_id,title,artist,album FROM songs WHERE song_id IN ({slots}) ORDER BY song_id", ids).fetchall()
        metadata = targets
        relations = conn.execute(f"SELECT playlist_id,song_id FROM ncm_playlist_songs WHERE song_id IN ({slots}) ORDER BY playlist_id,song_id", ids).fetchall()
        other_sources = []
        shared = set()
        removed = [str(r[0]) for r in targets]
        song_ids = removed
    if len(targets) != len(ids):
        raise LookupError("目标已不在本地曲库，请刷新后重试")
    state = [kind, targets, relations, other_sources, metadata, removal_revision(conn)]
    revision = hashlib.sha256(json.dumps(state, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
    return {"kind": kind, "target_ids": ids, "title": targets[0][1], "revision": revision,
            "affected_song_count": len(song_ids), "removed_song_count": len(removed),
            "kept_shared_count": len(shared), "source_playlist_count": len({r[0] for r in relations}),
            "removed_song_ids": removed}


def _bump_revision(conn):
    conn.execute("INSERT OR REPLACE INTO library_meta(key,value) VALUES ('local_removal_revision',?)", (uuid.uuid4().hex,))


def commit_removal(conn, kind, target_ids, expected_revision):
    """调用方BEGIN IMMEDIATE；整批移除与屏蔽记录一并提交或回滚。"""
    if not conn.in_transaction:
        raise RuntimeError("移除必须处于显式事务内")
    preview = preview_removal(conn, kind, target_ids)
    if preview["revision"] != expected_revision:
        raise RemovalConflict("曲库已变化，请重新查看移除范围后确认")
    ensure_removal_schema(conn)
    now = datetime.now(timezone.utc).isoformat()
    for target_id in preview["target_ids"]:
        if kind == "playlist":
            name = conn.execute("SELECT name FROM ncm_playlists WHERE playlist_id=?", (target_id,)).fetchone()[0]
            metadata = {"name": name}
        else:
            row = conn.execute("SELECT title,artist,album FROM songs WHERE song_id=?", (target_id,)).fetchone()
            metadata = dict(zip(("name", "artist", "album"), row))
            metadata["playlist_ids"] = [r[0] for r in conn.execute("SELECT playlist_id FROM ncm_playlist_songs WHERE song_id=?", (target_id,))]
        metadata["operation_revision"] = expected_revision
        conn.execute("INSERT OR REPLACE INTO local_library_removed VALUES (?,?,?,?)", (kind, target_id, json.dumps(metadata, ensure_ascii=False), now))
    # 临时表避免为几千首歌生成过长IN列表。历史审核、收听与不可变观察保留；
    # 活跃作品绑定/租约清除，重新导入必须取得新修订，旧观察不能冒充新证据。
    conn.execute("CREATE TEMP TABLE IF NOT EXISTS removal_targets(song_id TEXT PRIMARY KEY)")
    conn.execute("DELETE FROM removal_targets")
    conn.executemany("INSERT INTO removal_targets VALUES (?)", [(sid,) for sid in preview["removed_song_ids"]])
    if kind == "playlist":
        for target_id in preview["target_ids"]:
            conn.execute("DELETE FROM ncm_playlist_songs WHERE playlist_id=?", (target_id,))
            conn.execute("DELETE FROM ncm_playlists WHERE playlist_id=?", (target_id,))
    conn.execute("DELETE FROM ncm_playlist_songs WHERE song_id IN (SELECT song_id FROM removal_targets)")
    active_tables = ("song_platform_metadata", "song_embeddings", "song_profiles", "song_embedding_provenance", "song_profile_provenance",
                     "audio_features", "audio_observation_leases", "audio_observation_songs", "songs")
    tables = _tables(conn)
    for table in active_tables:
        if table in tables:
            conn.execute(f"DELETE FROM {table} WHERE song_id IN (SELECT song_id FROM removal_targets)")
    conn.execute("""UPDATE ncm_playlists SET synced_track_count=(
        SELECT COUNT(*) FROM ncm_playlist_songs ps WHERE ps.playlist_id=ncm_playlists.playlist_id)""")
    _bump_revision(conn)
    return preview


def list_removed(conn, query=""):
    if "local_library_removed" not in _tables(conn):
        return []
    rows = conn.execute("""SELECT kind,target_id,metadata_json,removed_at FROM local_library_removed
        WHERE metadata_json LIKE ? ORDER BY removed_at DESC,kind,target_id LIMIT 100""", (f"%{query}%",)).fetchall()
    return [{"kind": r[0], "target_id": r[1], **json.loads(r[2]), "removed_at": r[3]} for r in rows]


def allow_reimport(conn, kind, target_id):
    """只解除本地屏蔽，不联网、恢复旧绑定或悄悄启动同步。"""
    if not conn.in_transaction:
        raise RuntimeError("恢复导入许可必须处于显式事务内")
    _assert_idle(conn)
    if "local_library_removed" not in _tables(conn):
        raise LookupError("移除记录已不存在")
    row = conn.execute("SELECT metadata_json FROM local_library_removed WHERE kind=? AND target_id=?", (kind, target_id)).fetchone()
    if not row:
        raise LookupError("移除记录已不存在")
    if kind == "song":
        for pid in json.loads(row[0]).get("playlist_ids", []):
            # 清除指纹后下一次用户主动同步会真正取曲目，而非被判为未变化。
            conn.execute("UPDATE ncm_playlists SET remote_updated_at=0 WHERE playlist_id=?", (pid,))
    conn.execute("DELETE FROM local_library_removed WHERE kind=? AND target_id=?", (kind, target_id))
    _bump_revision(conn)
