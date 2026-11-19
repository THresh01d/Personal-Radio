"""按需查询有引用的作品资料；不读取歌词、不把模型记忆当事实。

首版采用 Wikidata 公开结构化接口。检索命中不是身份确认：必须精确匹配标题
和 performer 关系，存在多个版本时宁可返回空。只转录带外部引用的独立字段，
不从百科描述或评论中推断创作动机；来源本身仍可能出错，界面明确标注资料来源。
"""

import hashlib
import json
import re
import sqlite3
import threading
import time
import unicodedata
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from app.paths import DATA_ROOT

DB_PATH = DATA_ROOT / "background.db"
API_URL = "https://www.wikidata.org/w/api.php"
VERSION = "referenced-fields-v3"
_mb_lock = threading.Lock()
_mb_last_request = 0.0
SOURCE_HOSTS = {
    "music.apple.com", "allmusic.com", "discogs.com", "bbc.co.uk", "bbc.com",
    "rollingstone.com", "billboard.com", "sonymusic.com", "universalmusic.com",
    "warnermusic.com", "umusic.com", "warnermusic.com.tw", "sonymusic.com.tw",
    "sonymusic.com.hk", "en.wikipedia.org", "zh.wikipedia.org",
    "musicbrainz.org",
    "music.163.com",
}


def _norm(text):
    return "".join(c for c in unicodedata.normalize("NFKC", str(text)).casefold() if c.isalnum())


def _names(entity):
    values = [v.get("value", "") for v in (entity.get("labels") or {}).values()]
    for aliases in (entity.get("aliases") or {}).values():
        values.extend(a.get("value", "") for a in aliases)
    return {_norm(v) for v in values if v}


def _label(entity):
    labels = entity.get("labels") or {}
    for lang in ("zh-hans", "zh", "zh-hant", "en"):
        if labels.get(lang, {}).get("value"):
            return labels[lang]["value"]
    return ""


def _claims(entity, prop):
    # 弃用与未知值不能作为介绍素材；冲突字段随后整体省略。
    return [c for c in entity.get("claims", {}).get(prop, [])
            if c.get("rank") != "deprecated" and not c.get("qualifiers")
            and c.get("mainsnak", {}).get("snaktype") == "value"]


def _value(claim):
    return claim.get("mainsnak", {}).get("datavalue", {}).get("value")


def _entity_ids(entity, prop):
    return {v["id"] for c in _claims(entity, prop)
            if isinstance(v := _value(c), dict) and re.fullmatch(r"Q\d+", v.get("id", ""))}


def safe_source_url(value):
    """只展示已知资料站点链接；不下载引用网页，避免 SSRF 与任意网页指令。"""
    if not isinstance(value, str) or len(value) > 500:
        return None
    try:
        url = urlsplit(value)
        host = (url.hostname or "").lower().removeprefix("www.")
        if url.scheme != "https" or url.username or url.password or url.port not in (None, 443):
            return None
        if host not in SOURCE_HOSTS or "lyric" in url.path.lower():
            return None
        return value
    except ValueError:
        return None


def _reference(claim):
    for ref in claim.get("references") or []:
        for snak in ref.get("snaks", {}).get("P854", []):
            url = safe_source_url(snak.get("datavalue", {}).get("value"))
            if url:
                return url
    return None


def _request(params):
    # 固定 HTTPS 域名、不跟随重定向，每次请求与响应大小都有上限。
    with httpx.Client(timeout=3, follow_redirects=False,
                      headers={"User-Agent": "Personal-Radio/1.0 (local music background; Wikidata client)"}) as client:
        with client.stream("GET", API_URL, params={"format": "json", **params}) as response:
            response.raise_for_status()
            body = bytearray()
            for chunk in response.iter_bytes():
                body.extend(chunk)
                if len(body) > 1_000_000:
                    raise ValueError("背景响应过大")
            return json.loads(body)


def query_background(song, requester=None):
    """公开可注入的查询缝；生产最多三次接口调用，不调用云端模型。"""
    request = requester or _request
    title = str(song.get("title") or "").strip()
    artists = {_norm(a) for a in re.split(r"\s*[/、&]\s*", str(song.get("artist") or "")) if a.strip()}
    if not title or not artists:
        return []
    search = request({"action": "wbsearchentities", "search": title[:120],
                      "language": "zh", "uselang": "zh", "limit": 5, "type": "item"})
    ids = [x["id"] for x in search.get("search", [])
           if re.fullmatch(r"Q\d+", str(x.get("id", "")))][:5]
    if not ids:
        return []
    entities = request({"action": "wbgetentities", "ids": "|".join(ids),
                        "props": "labels|aliases|claims"}).get("entities", {})
    related = set()
    for entity in entities.values():
        for prop in ("P175", "P86", "P676", "P162", "P264"):
            related.update(_entity_ids(entity, prop))
    if not related or len(related) > 40:
        return []
    people = request({"action": "wbgetentities", "ids": "|".join(sorted(related)),
                      "props": "labels|aliases"}).get("entities", {})
    matches = []
    for entity_id, entity in entities.items():
        if _norm(title) not in _names(entity):
            continue
        performer_names = [_names(people.get(pid, {})) for pid in _entity_ids(entity, "P175")]
        performers = set().union(*performer_names)
        # 一人命中不能代表合唱版本身份；双方完整名单均须对应，别名仍可匹配。
        if (len(artists) == len(performer_names) and artists.issubset(performers)
                and all(names & artists for names in performer_names)):
            matches.append((entity_id, entity))
    if len(matches) != 1:
        return []  # 同名、翻唱或版本有歧义时，不猜测。
    entity_id, entity = matches[0]
    facts = []
    for prop, caption in (("P86", "作曲署名"), ("P676", "作词署名"),
                          ("P162", "制作人署名"), ("P264", "发行厂牌"), ("P577", "发行时间")):
        claims = _claims(entity, prop)
        unique = {json.dumps(_value(c), sort_keys=True) for c in claims}
        if len(unique) != 1:
            continue
        claim = next((c for c in claims if _reference(c)), None)
        if not claim:
            continue
        value = _value(claim)
        if prop == "P577":
            # 时间精度不足一年、历法未知、带限定版本的日期均省略。
            if (not isinstance(value, dict) or value.get("precision", 0) < 9
                    or value.get("calendarmodel") not in (
                        "http://www.wikidata.org/entity/Q1985727", "https://www.wikidata.org/entity/Q1985727")):
                continue
            year = re.match(r"\+(\d{4})-", value.get("time", ""))
            if not year or not 1900 <= int(year[1]) <= time.gmtime().tm_year:
                continue
            rendered = year[1] + "年"
        else:
            rendered = _label(people.get((value or {}).get("id"), {})) if isinstance(value, dict) else ""
        if not rendered:
            continue
        facts.append({"id": f"{entity_id}:{prop}", "text": f"《{title}》的作品资料记载，{caption}为{rendered}。",
                      "url": _reference(claim), "entity_url": f"https://www.wikidata.org/wiki/{entity_id}",
                      "source": "Wikidata 带外部引用的作品字段", "kind": prop})
    return facts[:3]


def _mb_request(path, params):
    global _mb_last_request
    # MusicBrainz 的公开接口要求客户端限速；两名后台 worker 共用同一节流器。
    with _mb_lock:
        delay = max(0, 1.1 - (time.monotonic() - _mb_last_request))
        if delay:
            time.sleep(delay)
        _mb_last_request = time.monotonic()
        with httpx.Client(timeout=3, follow_redirects=False,
                          headers={"User-Agent": "Personal-Radio/1.0 (local music background client)"}) as client:
            with client.stream("GET", "https://musicbrainz.org/ws/2/recording/" + path,
                               params={"fmt": "json", **params}) as response:
                response.raise_for_status()
                body = bytearray()
                for chunk in response.iter_bytes():
                    body.extend(chunk)
                    if len(body) > 1_000_000:
                        raise ValueError("背景响应过大")
                return json.loads(body)


def query_musicbrainz(song, requester=None):
    """独立资料源，不绕过其他站点限制；社区字段只按来源归属介绍，不伪称官方。"""
    request = requester or _mb_request
    title, artist, album = (str(song.get(k) or "").strip() for k in ("title", "artist", "album"))
    if not all((title, artist, album)):
        return []
    def escaped(value):
        return re.sub(r'([+\-!(){}\[\]^"~*?:\\/])', r'\\\1', value[:120])
    query = f'recording:"{escaped(title)}" AND artist:"{escaped(artist)}" AND release:"{escaped(album)}"'
    results = request("", {"query": query, "limit": 10}).get("recordings", [])
    artists = {_norm(a) for a in re.split(r"\s*[/、&]\s*", artist) if a.strip()}
    def identity(record):
        credits = {_norm(c.get("name") or c.get("artist", {}).get("name", "")) for c in record.get("artist-credit", []) if isinstance(c, dict)}
        return (_norm(record.get("title", "")) == _norm(title) and artists == credits
                and not record.get("disambiguation")
                and any(_norm(r.get("title", "")) == _norm(album) for r in record.get("releases", [])))
    matches = [r for r in results if identity(r)]
    if len(matches) != 1 or not re.fullmatch(r"[0-9a-f-]{36}", matches[0].get("id", "")):
        return []
    record_id = matches[0]["id"]
    record = request(record_id, {"inc": "artist-credits+artist-rels+work-rels+work-level-rels+releases"})
    if record.get("id") != record_id or not identity(record):
        return []
    source = f"https://musicbrainz.org/recording/{record_id}"
    facts = []
    relations = list(record.get("relations", []))
    for relation in record.get("relations", []):
        if relation.get("type") == "performance" and relation.get("work"):
            relations.extend(relation["work"].get("relations", []))
    for kind, caption in (("composer", "作曲署名"), ("lyricist", "作词署名"), ("producer", "制作人署名")):
        names = sorted({rel.get("artist", {}).get("name", "") for rel in relations
                        if rel.get("type") == kind and not rel.get("ended") and rel.get("artist", {}).get("name")})
        if names:
            facts.append({"id": f"{record_id}:{kind}", "text": f"MusicBrainz 的作品资料列出，《{title}》的{caption}是{'、'.join(names)}。",
                          "url": source, "entity_url": source,
                          "source": "MusicBrainz 社区作品资料（非官方采访）", "kind": kind})
    years = {r.get("date", "")[:4] for r in record.get("releases", [])
             if _norm(r.get("title", "")) == _norm(album) and r.get("status") == "Official" and re.match(r"\d{4}", r.get("date", ""))}
    if len(years) == 1:
        year = years.pop()
        if 1900 <= int(year) <= time.gmtime().tm_year:
            facts.append({"id": f"{record_id}:release", "text": f"MusicBrainz 记载，这一录音收录的《{album}》发行于{year}年。",
                          "url": source, "entity_url": source, "source": "MusicBrainz 社区作品资料（非官方采访）", "kind": "release"})
    return facts[:3]


def query_platform_intro(song, fetcher=None):
    """平台简介只按原文短引展示；评论不是史实，歌词接口完全不调用。"""
    song_id = str(song.get("id") or "")
    if not song_id.isdigit():
        return []
    if fetcher is None:
        from app import ncm
        if not ncm._gateway_healthy():
            return []
        fetcher = lambda endpoint, params: ncm._gw(endpoint, params=params, timeout=3)
    data = fetcher("/song/detail", {"ids": song_id})
    songs = data.get("songs") or []
    track = next((s for s in songs if str(s.get("id")) == song_id), None)
    if not track or _norm(track.get("name", "")) != _norm(song.get("title", "")):
        return []
    names = {_norm(a.get("name", "")) for a in track.get("ar", [])}
    artists = {_norm(a) for a in re.split(r"\s*[/、&]\s*", song.get("artist", "")) if a.strip()}
    album = track.get("al") or {}
    album_id = str(album.get("id") or "")
    if names != artists or not album_id.isdigit() or _norm(album.get("name", "")) != _norm(song.get("album", "")):
        return []
    detail = fetcher("/album", {"id": album_id}).get("album") or {}
    if str(detail.get("id")) != album_id or _norm(detail.get("name", "")) != _norm(song.get("album", "")):
        return []
    # 简介属于专辑而非单曲，播报必须显式归属专辑，不能错安成该曲创作故事。
    description = str(detail.get("description") or detail.get("briefDesc") or "")[:1200]
    facts = []
    # 限制窗口后只取实际终止的句子，窗口末尾的半句不能冒充完整短引。
    for sentence in re.findall(r"[^。！？\r\n]+[。！？]", description):
        sentence = sentence.strip()
        if not 12 <= len(sentence) <= 75 or len(sentence.split()) > 25:
            continue
        if "<" in sentence or "http" in sentence or not any(w in sentence for w in ("专辑", "大碟", "发行", "录制", "制作", "合作", "创作")):
            continue
        # 只保存一个完整短句；文字/语音均保留引号与原文，不做模型提炼。
        url = f"https://music.163.com/#/album?id={album_id}"
        facts.append({"id": f"netease:{album_id}:intro", "text": f"网易云的《{song['album']}》专辑简介写道：“{sentence}”",
                      "url": url, "entity_url": url, "source": "网易云平台专辑简介（非单曲创作考证）", "kind": "album_quote"})
        break
    return facts


def _valid_cached_facts(facts):
    """缓存命中仍需可展示的完整来源结构；损坏内容重查，不送入节目拼接。"""
    return isinstance(facts, list) and len(facts) <= 3 and all(
        isinstance(fact, dict)
        and all(isinstance(fact.get(key), str) and fact[key].strip()
                for key in ("id", "text", "url", "entity_url", "source", "kind"))
        and safe_source_url(fact["url"])
        and (safe_source_url(fact["entity_url"])
             or re.fullmatch(r"https://www\.wikidata\.org/wiki/Q\d+", fact["entity_url"]))
        for fact in facts
    )


def _cache_key(song):
    return hashlib.sha256(json.dumps([VERSION, song.get('id'), song.get('title'),
                                     song.get('artist'), song.get('album')], ensure_ascii=False).encode()).hexdigest()


def peek_background(song, db_path=None):
    """文案只读取已有的有效缓存；缓存缺失不建库、不联网、不拖慢本轮推荐。"""
    path = Path(db_path or DB_PATH)
    if not path.is_file():
        return []
    try:
        with sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=.2) as conn:
            row = conn.execute('SELECT facts, expires FROM background_cache WHERE cache_key=?',
                               (_cache_key(song),)).fetchone()
        if row and row[1] > time.time():
            facts = json.loads(row[0])
            if _valid_cached_facts(facts):
                return facts
    except (sqlite3.Error, TypeError, ValueError):
        pass
    return []


def get_background(song, db_path=None, requester=None):
    """缓存只保存少量字段及来源；元数据变化失效，查不到也短期缓存避免反复查。"""
    path = db_path or DB_PATH
    key = _cache_key(song)
    now = time.time()
    with sqlite3.connect(path, timeout=5) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS background_cache (cache_key TEXT PRIMARY KEY, facts TEXT, expires REAL)")
        row = conn.execute("SELECT facts, expires FROM background_cache WHERE cache_key=?", (key,)).fetchone()
        if row and row[1] > now:
            try:
                cached = json.loads(row[0])
                if _valid_cached_facts(cached):
                    return cached
            except (TypeError, ValueError):
                pass
    try:
        facts = query_background(song, requester)
    except Exception:
        facts = []  # 不打印查询响应，避免日志收集不必要的私人歌名。
    if not facts and requester is None:
        try:
            facts = query_platform_intro(song)
        except Exception:
            facts = []
    if not facts and requester is None:
        try:
            facts = query_musicbrainz(song)
        except Exception:
            facts = []
    with sqlite3.connect(path, timeout=5) as conn:
        conn.execute("INSERT OR REPLACE INTO background_cache VALUES (?, ?, ?)",
                     (key, json.dumps(facts, ensure_ascii=False), now + (30 * 86400 if facts else 6 * 3600)))
        conn.execute("DELETE FROM background_cache WHERE expires < ?", (now - 86400,))
    return facts
