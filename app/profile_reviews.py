"""歌曲级审核与标签覆盖视图：不覆盖原档案/向量，不生成长期偏好。"""

import json
import uuid

from app.embedding_store import metadata_hash, now
from app.recommendation_state import GENRES

SCOPES = {"query_positive", "global_genre"}


def ensure_schema(conn):
    # 独立追加事件，不把人工意见伪装成原 LLM 输出；由调用者显式开启事务。
    if not conn.in_transaction:
        raise RuntimeError("审核写入必须在调用方事务内")
    conn.execute("""CREATE TABLE IF NOT EXISTS song_profile_review_events (
        review_id TEXT NOT NULL, revision INTEGER NOT NULL, status TEXT NOT NULL,
        payload_json TEXT NOT NULL, recorded_at TEXT NOT NULL,
        PRIMARY KEY(review_id, revision))""")


def latest(conn, review_id):
    exists = conn.execute("SELECT 1 FROM sqlite_master WHERE name='song_profile_review_events'").fetchone()
    if not exists:
        return None
    row = conn.execute("SELECT revision,status,payload_json FROM song_profile_review_events WHERE review_id=? ORDER BY revision DESC LIMIT 1", (review_id,)).fetchone()
    return {"review_id": review_id, "revision": row[0], "status": row[1], "payload": json.loads(row[2])} if row else None


def propose(conn, song, *, scope, source, genres=None, query_key=None):
    """只创建待确认提案；本次正例不能夹带全局风格，来源是意见归属而非客观真值。"""
    if not isinstance(scope, str) or scope not in SCOPES or source != "explicit_user_review":
        raise ValueError("审核范围/来源无效，不接受自动推断冒充人工意见")
    if not isinstance(song.get("song_id"), str) or not song["song_id"]:
        raise ValueError("只允许歌曲级审核，不接受歌手范围")
    if scope == "query_positive":
        if not isinstance(query_key, str) or not query_key.strip() or genres is not None:
            raise ValueError("本次正例须绑定需求且不能修改全局标签")
    elif query_key is not None or not isinstance(genres, list) or not genres or len(genres) > 3 or any(g not in GENRES for g in genres) or len(set(genres)) != len(genres):
        raise ValueError("全局风格校正须明确给出固定词表内的完整替代值")
    row = conn.execute("SELECT title,artist,album FROM songs WHERE song_id=?", (song["song_id"],)).fetchone()
    if row is None or metadata_hash(dict(zip(("title", "artist", "album"), row))) != metadata_hash(song):
        raise ValueError("只能审核库内当前元数据绑定的单曲")
    ensure_schema(conn)
    review_id = uuid.uuid4().hex
    payload = {"song_id": song["song_id"], "metadata_sha256": metadata_hash(song),
               "scope": scope, "source": source, "genres": genres, "query_key": query_key}
    conn.execute("INSERT INTO song_profile_review_events VALUES (?,1,'pending',?,?)", (review_id, json.dumps(payload, ensure_ascii=False), now()))
    return latest(conn, review_id)


def transition(conn, review_id, *, expected_revision, action, confirmed_scope=None, song=None):
    """显式确认/撤销且校验版本；撤销也是新事件，保留旧意见及确认过程。"""
    ensure_schema(conn)
    current = latest(conn, review_id)
    if type(expected_revision) is not int or current is None or current["revision"] != expected_revision:
        raise ValueError("审核记录不存在或已变化，请重新查看")
    if action == "confirm":
        row = conn.execute("SELECT title,artist,album FROM songs WHERE song_id=?", (current["payload"]["song_id"],)).fetchone()
        if (current["status"] != "pending" or confirmed_scope != current["payload"]["scope"]
                or song is None or song.get("song_id") != current["payload"]["song_id"]
                or row is None or metadata_hash(dict(zip(("title", "artist", "album"), row))) != current["payload"]["metadata_sha256"]
                or metadata_hash(song) != current["payload"]["metadata_sha256"]):
            raise ValueError("确认范围/作品绑定不一致或提案已失效")
        if current["payload"]["scope"] == "global_genre" and any(
                item["review_id"] != review_id and item["status"] == "confirmed"
                and item["payload"]["scope"] == "global_genre"
                and item["payload"]["metadata_sha256"] == current["payload"]["metadata_sha256"]
                for item in for_song(conn, current["payload"]["song_id"])):
            raise ValueError("已有生效校正，请先撤销旧校正再确认")
        status = "confirmed"
    elif action == "revoke" and current["status"] in {"pending", "confirmed"}:
        status = "revoked"
    else:
        raise ValueError("不支持该状态变化；撤销后须新建提案")
    conn.execute("INSERT INTO song_profile_review_events VALUES (?,?,?,?,?)", (review_id, expected_revision + 1, status, json.dumps(current["payload"], ensure_ascii=False), now()))
    return latest(conn, review_id)


def effective_genres(base_genres, song, review):
    """只返回显式读取的覆盖视图；不写原档案/向量。"""
    if (review and review["status"] == "confirmed" and review["payload"]["scope"] == "global_genre"
            and review["payload"]["song_id"] == song.get("song_id")
            and review["payload"]["metadata_sha256"] == metadata_hash(song)):
        return list(review["payload"]["genres"])
    return list(base_genres or [])


def all_latest(conn):
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='song_profile_review_events'").fetchone():
        return []
    rows = conn.execute("""SELECT e.review_id,e.revision,e.status,e.payload_json,e.recorded_at
        FROM song_profile_review_events e JOIN
        (SELECT review_id,MAX(revision) revision FROM song_profile_review_events GROUP BY review_id) last
        ON e.review_id=last.review_id AND e.revision=last.revision ORDER BY e.recorded_at DESC,e.review_id""").fetchall()
    return [{"review_id": r[0], "revision": r[1], "status": r[2], "payload": json.loads(r[3]), "recorded_at": r[4]} for r in rows]


def for_song(conn, song_id):
    return [item for item in all_latest(conn) if item["payload"]["song_id"] == song_id]


def override_map(conn):
    """只读当前绑定、唯一确认的全局校正；冲突/过期不偷偷择一，也不初始化表。"""
    confirmed = [i for i in all_latest(conn) if i["status"] == "confirmed" and i["payload"]["scope"] == "global_genre"]
    if not confirmed:
        return {}
    current = {r[0]: metadata_hash(dict(zip(("title", "artist", "album"), r[1:])))
               for r in conn.execute("SELECT song_id,title,artist,album FROM songs")}
    groups = {}
    for item in confirmed:
        p = item["payload"]
        if current.get(p["song_id"]) == p["metadata_sha256"]:
            groups.setdefault(p["song_id"], []).append(item)
    return {sid: items[0] for sid, items in groups.items() if len(items) == 1}


def decorate(candidate, overrides):
    """显式保留原标签和人类来源，原向量仍是原配方，不伪造新的编码来源。"""
    item = overrides.get(candidate["id"])
    if not item:
        return candidate
    return {**candidate, "genre_original": list(candidate.get("genre") or []),
            "genre": list(item["payload"]["genres"]), "genre_source": "user_confirmed",
            "genre_review_id": item["review_id"], "genre_review_revision": item["revision"],
            "embedding_review_notice": "标签校正已生效；语义向量未重算，保留原编码来源"}
