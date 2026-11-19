"""平台百科的最小白名单、作品绑定与只读合并；不覆盖 AI 档案或人工审核。"""
import json
import math
import re
import time

from app.embedding_store import metadata_hash
from app.language_evidence import normalize_language, language_fields

VERSION = "ncm-wiki-v1"
SOURCE = "netease_wiki"

# 细分只采用平台明确给出的完整词，不用歌名、歌手、BPM或情绪推导子流派。
GENRE_ALIASES = {
    '嘻哈': '说唱', '嘻哈说唱': '说唱', 'hip-hop': '说唱', 'hip hop': '说唱', 'rap': '说唱',
    '中文说唱': '说唱', '日本说唱': '说唱', '二次元': 'ACG',
    '华语流行': '流行', 'j-pop': '流行', 'k-pop': '流行', 'pop': '流行',
    'rnb': 'R&B', 'r&b': 'R&B', 'rhythm & blues': 'R&B', 'folk': '民谣',
    'rock': '摇滚', 'jazz': '爵士', 'electronic': '电子',
}
DETAIL_LABELS = {
    '陷阱说唱': ('说唱', '陷阱说唱'), 'trap': ('说唱', '陷阱说唱'),
    'boom bap': ('说唱', 'Boom Bap'), '旋律说唱': ('说唱', '旋律说唱'),
    '流行说唱': ('说唱', '流行说唱'), '另类嘻哈': ('说唱', '另类嘻哈'),
    '模糊说唱': ('说唱', '模糊说唱'), '日本流行': ('流行', '日本流行'),
    'j-pop': ('流行', '日本流行'), '韩国流行': ('流行', '韩国流行'),
    'k-pop': ('流行', '韩国流行'), '华语流行': ('流行', '华语流行'),
    '另类r&b': ('R&B', '另类R&B'),
}
STYLE_DETAIL_KEYS = frozenset(f'{family}/{label}' for family, label in DETAIL_LABELS.values())


def normalize_genres(raw_genres):
    """从既有平台原文得到规范大类与细分键；不修改数据库缓存。"""
    from app.recommendation_state import GENRES
    canonical = {g.casefold(): g for g in GENRES}
    genres, details = [], []
    for raw in _strings(raw_genres if isinstance(raw_genres, list) else []):
        key = raw.casefold()
        pieces = [raw] if key in GENRE_ALIASES or key in canonical or key in DETAIL_LABELS else re.split(r'[-－/·]', raw)
        root_key = pieces[0].strip().casefold()
        parent = (GENRE_ALIASES.get(root_key) or canonical.get(root_key)) if len(pieces) > 1 else None
        for piece in pieces:
            key = piece.strip().casefold()
            detail = DETAIL_LABELS.get(key)
            # 异常的父子冲突保留父类，不把“流行-陷阱说唱”改造成可靠说唱证据。
            if detail and (parent is None or detail[0] == parent):
                family, label = detail
                genres.append(family)
                details.append(f'{family}/{label}')
            elif not detail:
                value = GENRE_ALIASES.get(key) or canonical.get(key)
                if value:
                    genres.append(value)
    return {'genre': list(dict.fromkeys(genres)), 'style_details': list(dict.fromkeys(details))}


def style_details_for_song(song):
    """平台细分须与当前有效大类一致，人工更正大类不能被旧平台子标签绕过。"""
    if song.get('genre_source') not in {'user_confirmed', SOURCE}:
        return ()
    metadata = song.get('platform_metadata') or {}
    if not isinstance(metadata, dict) or metadata.get('status') != 'ready':
        return ()
    values = metadata.get('style_details') or []
    if not isinstance(values, (list, tuple)):
        return ()
    genres = {str(g).casefold() for g in song.get('genre') or []}
    return tuple(dict.fromkeys(value for value in values if isinstance(value, str)
        and value in STYLE_DETAIL_KEYS and value.split('/', 1)[0].casefold() in genres))


def ensure_schema(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS song_platform_metadata (
        song_id TEXT PRIMARY KEY, metadata_sha256 TEXT NOT NULL,
        version TEXT NOT NULL, status TEXT NOT NULL,
        payload TEXT NOT NULL DEFAULT '{}', checked_at REAL NOT NULL,
        error_code TEXT)""")


def _strings(values):
    return list(dict.fromkeys(v.strip()[:100] for v in values
                             if isinstance(v, str) and v.strip()))[:30]


def parse_wiki(body):
    """只读歌曲基本信息块；乐谱、歌单名、奖项、个人听歌记忆均不能当歌曲标签。"""
    if not isinstance(body, dict) or body.get("code", 200) != 200:
        raise ValueError("wiki_unavailable")
    data = body.get("data")
    if not isinstance(data, dict) or not isinstance(data.get("blocks"), list):
        raise ValueError("wiki_format")
    result = {"raw_genres": [], "raw_moods": [], "language": None, "bpm": None}
    blocks = [b for b in data["blocks"] if isinstance(b, dict)
              and b.get("code") == "SONG_PLAY_ABOUT_SONG_BASIC"]
    language_values = []
    for block in blocks:
        for creative in block.get("creatives") or []:
            if not isinstance(creative, dict):
                continue
            kind = creative.get("creativeType")
            if kind in {"songTag", "songBizTag"}:
                labels = []
                for resource in creative.get("resources") or []:
                    if isinstance(resource, dict):
                        ui = resource.get("uiElement") or {}
                        labels.append((ui.get("mainTitle") or {}).get("title"))
                field = "raw_genres" if kind == "songTag" else "raw_moods"
                result[field] = _strings([*result[field], *labels])
            elif kind in {"language", "bpm"}:
                ui = creative.get("uiElement") or {}
                values = _strings(x.get("text") for x in ui.get("textLinks") or [] if isinstance(x, dict))
                if kind == "language":
                    language_values.extend(values)
                elif values:
                    try:
                        value = float(values[0])
                        if math.isfinite(value) and 20 <= value <= 400:
                            result["bpm"] = value
                    except ValueError:
                        pass
    result["language"] = normalize_language(language_values)
    from app.recommendation_state import MOODS
    result.update(normalize_genres(result['raw_genres']))
    mood_aliases = {"悲伤": "伤感"}
    result["mood"] = list(dict.fromkeys(mood_aliases.get(m, m) for m in result["raw_moods"]
                                        if mood_aliases.get(m, m) in MOODS))
    # 未出现纯音乐不是“有人声”的证据；此处只保留明确肯定，未知仍为 None。
    result["is_instrumental"] = True if "纯音乐" in result["genre"] else None
    return result


def read_map(conn):
    """只读且兼容旧库；绑定失效、解析版本过旧的记录不能进入推荐。"""
    try:
        rows = conn.execute("""SELECT m.song_id,m.metadata_sha256,m.payload,m.status,
            s.title,s.artist,s.album FROM song_platform_metadata m JOIN songs s USING(song_id)
            WHERE m.version=?""", (VERSION,)).fetchall()
    except Exception as exc:
        import sqlite3
        if isinstance(exc, sqlite3.OperationalError) and "no such table" in str(exc):
            return {}
        raise
    result = {}
    for sid, binding, payload, status, title, artist, album in rows:
        if binding != metadata_hash({"title": title, "artist": artist, "album": album}):
            continue
        try:
            item = json.loads(payload)
            if not isinstance(item, dict):
                continue
            item['language'] = normalize_language(item.get('language'))
            normalized = normalize_genres(item.get('raw_genres'))
            # v1缓存保留原文，可在内存补齐旧解析遗漏，无需重抓或写正式私人标签。
            result[sid] = {**item, 'style_details': normalized['style_details'], 'status': status}
            if normalized['genre']:
                result[sid]['genre'] = normalized['genre']
        except (ValueError, TypeError):
            continue
    return result


def merge(candidate, records):
    """平台曲风优先于 AI 曲风；人工校正由调用方最后应用，向量来源始终不变。"""
    item = records.get(str(candidate.get("id") or candidate.get("song_id")))
    if not item:
        return {**candidate, **language_fields(candidate)}
    result = {**candidate, "platform_metadata": item}
    if item.get("genre"):
        result.update(genre_original=list(candidate.get("genre") or []), genre=item["genre"], genre_source=SOURCE)
    for key in ("mood", "is_instrumental"):
        if item.get(key) is not None and item.get(key) != []:
            result[key] = item[key]
    return {**result, **language_fields(result)}


def coverage(conn):
    records = read_map(conn)
    # 与实际补全器使用相同的网易云歌单关联口径，来源不明不能永远列作待处理。
    import sqlite3
    try:
        eligible = {row[0] for row in conn.execute(
            "SELECT DISTINCT s.song_id FROM songs s JOIN ncm_playlist_songs ps USING(song_id)")}
    except sqlite3.OperationalError as exc:
        if "no such table" not in str(exc):
            raise
        eligible = set()
    platform = {sid: item for sid, item in records.items() if sid in eligible}
    statuses = {key: sum(v.get("status") == key for v in platform.values())
                for key in ("ready", "empty", "failed")}
    platform_genres = sum(v.get("status") == "ready" and bool(v.get("genre")) for v in platform.values())
    from app.profile_reviews import override_map, decorate
    reviews = override_map(conn)
    genres = 0
    for sid, raw in conn.execute("SELECT s.song_id,p.genre FROM songs s LEFT JOIN song_profiles p USING(song_id)"):
        try:
            base = json.loads(raw or "[]")
        except (TypeError, ValueError):
            base = []
        viewed = decorate(merge({"id": sid, "genre": base}, records), reviews)
        genres += bool(viewed.get("genre"))
    total = conn.execute("SELECT COUNT(*) FROM songs").fetchone()[0]
    return {"total": total, "genre_count": genres, "unknown_genre_count": total - genres,
            "platform_ready": statuses["ready"], "platform_empty": statuses["empty"],
            "platform_failed": statuses["failed"], "platform_pending": len(eligible - records.keys()),
            "platform_eligible_count": len(eligible), "platform_unlinked_count": total - len(eligible),
            "platform_genre_count": platform_genres,
            "platform_without_genre_count": statuses["ready"] - platform_genres}


def save(conn, song, payload, status, *, error_code=None):
    ensure_schema(conn)
    conn.execute("""INSERT OR REPLACE INTO song_platform_metadata
        (song_id,metadata_sha256,version,status,payload,checked_at,error_code) VALUES (?,?,?,?,?,?,?)""",
        (song["song_id"], metadata_hash(song), VERSION, status,
         json.dumps(payload, ensure_ascii=False), time.time(), error_code))
