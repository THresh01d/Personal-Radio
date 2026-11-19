"""无需 AI 的本地曲库浏览服务。

这里刻意只读取同步后的 SQLite，不调用模型，也不访问网易云网络。用户即使关闭
AI 或网络暂时不可用，仍可以浏览歌单与歌曲；真正点击播放时才由现有播放地址
接口按账号权限取流。
"""

import json
import sqlite3
from pathlib import Path

from app.library_db import DB_PATH, ensure_library_schema
from app.profile_reviews import override_map, decorate
from app.platform_metadata import read_map as platform_map, merge as platform_merge


def _tags(raw):
    try:
        value = json.loads(raw or "[]")
    except (TypeError, ValueError):
        return []
    return value if isinstance(value, list) else []


def list_local_playlists(db_path=DB_PATH):
    """返回已经同步落盘的网易云歌单，不触发远端刷新。"""
    db_path = Path(db_path)
    ensure_library_schema(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """SELECT playlist_id, name, track_count, synced_track_count,
                      remote_updated_at, cover_url, is_liked, synced_at
                 FROM ncm_playlists
                ORDER BY is_liked DESC, name COLLATE NOCASE, playlist_id"""
        ).fetchall()
    return [
        {
            "id": str(row["playlist_id"]),
            "name": row["name"],
            # 浏览器只展示本机真实拥有的关系数；远端声明数保留作同步诊断。
            "track_count": int(row["synced_track_count"] or 0),
            "remote_track_count": int(row["track_count"] or 0),
            "remote_updated_at": int(row["remote_updated_at"] or 0),
            "cover_url": row["cover_url"],
            "is_liked": bool(row["is_liked"]),
            "synced_at": row["synced_at"],
        }
        for row in rows
    ]


def browse_local_songs(
    *,
    playlist_id=None,
    query="",
    offset=0,
    limit=50,
    order="playlist",
    db_path=DB_PATH,
):
    """分页浏览本地歌曲，并返回可直接交给播放器的真实元数据。

    ``playlist`` 顺序只在指定歌单时使用远端原始 position；全部歌曲默认按标题。
    ``random`` 只用于用户明确点击随机播放，不参与普通分页，避免翻页时重复漂移。
    5048 首规模下 SQLite LIKE 与 RANDOM() 足够快，无需引入额外搜索服务。
    """
    db_path = Path(db_path)
    ensure_library_schema(db_path)
    playlist_id = str(playlist_id or "").strip() or None
    query = (query or "").strip()
    offset = max(0, int(offset))
    limit = max(1, min(int(limit), 5000))
    if order not in {"playlist", "title", "random"}:
        raise ValueError("不支持的曲库排序")

    joins = ["LEFT JOIN song_profiles p ON p.song_id=s.song_id"]
    where = []
    params = []
    if playlist_id:
        joins.insert(
            0,
            "JOIN ncm_playlist_songs ps ON ps.song_id=s.song_id AND ps.playlist_id=?",
        )
        params.append(playlist_id)
    if query:
        pattern = f"%{query}%"
        where.append(
            "(s.title LIKE ? COLLATE NOCASE OR s.artist LIKE ? COLLATE NOCASE "
            "OR s.album LIKE ? COLLATE NOCASE OR s.aliases LIKE ? COLLATE NOCASE)"
        )
        params.extend([pattern, pattern, pattern, pattern])

    from_sql = "FROM songs s " + " ".join(joins)
    where_sql = (" WHERE " + " AND ".join(where)) if where else ""
    if order == "random":
        order_sql = "ORDER BY RANDOM()"
    elif order == "playlist" and playlist_id:
        order_sql = "ORDER BY ps.position, s.title COLLATE NOCASE, s.song_id"
    else:
        order_sql = "ORDER BY s.title COLLATE NOCASE, s.artist COLLATE NOCASE, s.song_id"

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        if playlist_id:
            exists = conn.execute(
                "SELECT 1 FROM ncm_playlists WHERE playlist_id=?", (playlist_id,)
            ).fetchone()
            if not exists:
                raise LookupError("本地没有这个网易云歌单")
        total = conn.execute(
            f"SELECT COUNT(1) {from_sql}{where_sql}", params
        ).fetchone()[0]
        rows = conn.execute(
            f"""SELECT s.song_id, s.title, s.artist, s.album,
                       s.duration_seconds, p.genre, p.mood, p.scene,
                       p.is_instrumental, p.language
                  {from_sql}{where_sql}
                  {order_sql}
                  LIMIT ? OFFSET ?""",
            [*params, limit, offset],
        ).fetchall()

        platform = platform_map(conn)
        overrides = override_map(conn)
        # “全部歌曲”是本地去重曲库，不等于各歌单数量相加。把组成口径一起返回，
        # 避免用户把跨歌单重复曲目或已脱离歌单的本地保留曲目误认为统计错误。
        library_stats = None
        if not playlist_id and not query:
            linked_songs = conn.execute(
                "SELECT COUNT(DISTINCT song_id) FROM ncm_playlist_songs"
            ).fetchone()[0]
            library_stats = {
                "unique_songs": int(total),
                "linked_songs": int(linked_songs or 0),
                "unassigned_songs": max(0, int(total) - int(linked_songs or 0)),
            }

    songs = [
        {
            "id": str(row["song_id"]),
            "title": row["title"],
            "artist": row["artist"],
            "album": row["album"],
            "duration_seconds": int(row["duration_seconds"] or 0),
            "genre": _tags(row["genre"]),
            "mood": _tags(row["mood"]),
            "scene": _tags(row["scene"]),
            "is_instrumental": bool(row["is_instrumental"]),
            "language": row["language"],
            # 手动点播不伪装成推荐结果，也不会触发 DJ 报幕。
            "source": "manual_library",
        }
        for row in rows
    ]
    return {
        "songs": [decorate(platform_merge(s, platform), overrides) for s in songs],
        "total": int(total),
        "offset": offset,
        "limit": limit,
        "has_more": offset + len(songs) < total,
        "playlist_id": playlist_id,
        "query": query,
        "order": order,
        "library_stats": library_stats,
    }
