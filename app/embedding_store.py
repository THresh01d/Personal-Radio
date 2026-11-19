"""向量文本与事务写入的唯一入口；来源是实际写入证据，不是标签真值。"""

from datetime import datetime, timezone
import hashlib
import json

import numpy as np

METADATA_RECIPE = "metadata-v1"
PROFILE_RECIPE = "metadata-genre-mood-v1"
_UNSPECIFIED = object()


def metadata_text(song):
    return " ".join(p for p in (song.get("title"), song.get("artist"), song.get("album")) if p).strip()


def embedding_text(song, profile=None):
    """保持旧配方的顺序和空格，不偷偷加场景/语种或改变现有向量语义。"""
    parts = [song.get("title"), song.get("artist"), song.get("album")]
    if profile is not None:
        for field in ("genre", "mood"):
            tags = profile.get(field) or []
            if not isinstance(tags, (list, tuple)) or any(not isinstance(t, str) for t in tags):
                raise ValueError("向量输入标签格式无效")
            parts.extend(tags)
    return " ".join(p for p in parts if p).strip()


def metadata_hash(song):
    return hashlib.sha256(json.dumps([song.get(k) or "" for k in ("title", "artist", "album")], ensure_ascii=False).encode()).hexdigest()


def now():
    return datetime.now(timezone.utc).isoformat()


def ensure_embedding_schema(conn):
    # 不用 executescript（会隐式提交），确保调用方的整批事务仍可回滚。
    conn.execute("""CREATE TABLE IF NOT EXISTS song_embedding_provenance (
        song_id TEXT PRIMARY KEY, recipe TEXT NOT NULL, input_sha256 TEXT NOT NULL,
        metadata_sha256 TEXT NOT NULL, vector_sha256 TEXT NOT NULL, model_id TEXT NOT NULL,
        model_sha256 TEXT, writer TEXT NOT NULL, generated_at TEXT NOT NULL)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS song_profile_provenance (
        song_id TEXT PRIMARY KEY, metadata_sha256 TEXT NOT NULL, profile_sha256 TEXT NOT NULL,
        prompt_sha256 TEXT, writer TEXT NOT NULL, recorded_at TEXT NOT NULL)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS song_profile_archive (
        archive_id INTEGER PRIMARY KEY AUTOINCREMENT, song_id TEXT NOT NULL,
        observed_metadata_sha256 TEXT NOT NULL, profile_json TEXT NOT NULL,
        provenance_json TEXT, reason TEXT NOT NULL, archived_at TEXT NOT NULL)""")


def read_profile(conn, song_id):
    row = conn.execute("SELECT genre,mood,scene,is_instrumental,language FROM song_profiles WHERE song_id=?", (song_id,)).fetchone()
    if row is None:
        return None
    return {"genre": json.loads(row[0] or "[]"), "mood": json.loads(row[1] or "[]"),
            "scene": json.loads(row[2] or "[]"), "is_instrumental": bool(row[3]), "language": row[4]}


def write_embedding(conn, song, vector, *, profile=None, writer, model_identity=None):
    """不提交：向量与来源必须由调用方和歌曲/档案一起提交或回滚。"""
    if not conn.in_transaction:
        raise RuntimeError("向量写入必须先开启事务")
    ensure_embedding_schema(conn)
    vector = np.asarray(vector, dtype=np.float32)
    if vector.ndim != 1 or not vector.size or not np.all(np.isfinite(vector)) or np.linalg.norm(vector) == 0:
        raise ValueError("编码向量无效")
    text = embedding_text(song, profile)
    blob = vector.tobytes()
    identity = model_identity or {"model_id": "unknown", "files_sha256": None}
    conn.execute("INSERT OR REPLACE INTO song_embeddings(song_id,embedding) VALUES (?,?)", (song["song_id"], blob))
    conn.execute("""INSERT OR REPLACE INTO song_embedding_provenance VALUES (?,?,?,?,?,?,?,?,?)""", (
        song["song_id"], PROFILE_RECIPE if profile is not None else METADATA_RECIPE,
        hashlib.sha256(text.encode()).hexdigest(), metadata_hash(song), hashlib.sha256(blob).hexdigest(),
        identity["model_id"], identity.get("files_sha256"), writer, now(),
    ))


def record_profile_provenance(conn, song, profile, *, writer, prompt_sha256=None):
    """记录这次生成/校准的观察，未提供旧提示时保持未知，不伪造旧生成过程。"""
    if not conn.in_transaction:
        raise RuntimeError("档案来源写入必须先开启事务")
    ensure_embedding_schema(conn)
    conn.execute("INSERT OR REPLACE INTO song_profile_provenance VALUES (?,?,?,?,?,?)", (
        song["song_id"], metadata_hash(song), hashlib.sha256(json.dumps(profile, sort_keys=True, ensure_ascii=False).encode()).hexdigest(),
        prompt_sha256, writer, now(),
    ))


def write_profile_embedding(conn, song, profile, vector, *, source, writer, prompt_sha256=None,
                            model_identity=None, expected_profile=_UNSPECIFIED):
    """拒绝生成期间变化的元数据；返回 False 时没有写入过期档案/向量。"""
    if not conn.in_transaction:
        raise RuntimeError("档案写入必须先开启事务")
    current = conn.execute("SELECT title,artist,album FROM songs WHERE song_id=?", (song["song_id"],)).fetchone()
    if current is None or metadata_hash(dict(zip(("title", "artist", "album"), current))) != metadata_hash(song):
        return False
    if expected_profile is not _UNSPECIFIED and read_profile(conn, song["song_id"]) != expected_profile:
        return False
    write_embedding(conn, song, vector, profile=profile, writer=writer, model_identity=model_identity)
    conn.execute("""INSERT OR REPLACE INTO song_profiles
        (song_id,genre,mood,scene,is_instrumental,language,source,created_at) VALUES (?,?,?,?,?,?,?,?)""", (
        song["song_id"], json.dumps(profile["genre"], ensure_ascii=False), json.dumps(profile["mood"], ensure_ascii=False),
        json.dumps(profile.get("scene") or [], ensure_ascii=False), int(profile.get("is_instrumental", False)),
        profile.get("language"), source, now(),
    ))
    record_profile_provenance(conn, song, profile, writer=writer, prompt_sha256=prompt_sha256)
    # search_text 只作展示/搜索文本，不参与同步是否重编码的判定。
    conn.execute("UPDATE songs SET search_text=? WHERE song_id=?", (embedding_text(song, profile), song["song_id"]))
    return True


def invalidate_profile(conn, old_song, reason="metadata_changed"):
    """归档是可恢复的失效记录，不给旧档案伪造生成绑定或提示版本。"""
    if not conn.in_transaction:
        raise RuntimeError("档案失效必须先开启事务")
    ensure_embedding_schema(conn)
    fields = ("genre", "mood", "scene", "is_instrumental", "language", "source", "created_at")
    row = conn.execute("SELECT genre,mood,scene,is_instrumental,language,source,created_at FROM song_profiles WHERE song_id=?", (old_song["song_id"],)).fetchone()
    if row is not None:
        provenance = conn.execute("SELECT * FROM song_profile_provenance WHERE song_id=?", (old_song["song_id"],)).fetchone()
        conn.execute("""INSERT INTO song_profile_archive
            (song_id,observed_metadata_sha256,profile_json,provenance_json,reason,archived_at) VALUES (?,?,?,?,?,?)""", (
            old_song["song_id"], metadata_hash(old_song), json.dumps(dict(zip(fields, row)), ensure_ascii=False),
            json.dumps(tuple(provenance), ensure_ascii=False) if provenance else None, reason, now(),
        ))
    conn.execute("DELETE FROM song_profiles WHERE song_id=?", (old_song["song_id"],))
    conn.execute("DELETE FROM song_profile_provenance WHERE song_id=?", (old_song["song_id"],))
