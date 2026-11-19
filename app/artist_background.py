"""按当前歌曲的平台身份取少量歌手简介，不搜索同名艺人、不抓歌词或遍历全库。"""
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import time

from app import song_background as bg


def query_artist_intro(song, fetcher=None):
    sid = str(song.get('id') or '')
    if not sid.isdigit():
        return []
    if fetcher is None:
        from app import ncm
        if not ncm._gateway_healthy():
            return []
        fetcher = lambda route, params: ncm._gw(route, params=params, timeout=3)
    tracks = fetcher('/song/detail', {'ids': sid}).get('songs') or []
    track = next((s for s in tracks if str(s.get('id')) == sid), None)
    expected = {bg._norm(a) for a in re.split(r'\s*[/、&]\s*', song.get('artist', '')) if a.strip()}
    if (not track or bg._norm(track.get('name', '')) != bg._norm(song.get('title', ''))
            or {bg._norm(a.get('name', '')) for a in track.get('ar', [])} != expected):
        return []
    artist = next((a for a in track.get('ar', []) if str(a.get('id', '')).isdigit()), None)
    if not artist:
        return []
    data = fetcher('/artist/desc', {'id': str(artist['id'])})
    if data.get('code', 200) != 200:
        return []
    # 优先平台简介，职业经历段作为补充；原文保留归属，不能说成歌手本人采访。
    paragraphs = [data.get('briefDesc') or '']
    paragraphs.extend(item.get('txt') or '' for item in (data.get('introduction') or [])[:2] if isinstance(item, dict))
    excerpts = []
    for paragraph in paragraphs:
        for sentence in re.findall(r'[^。！？!?\r\n]+[。！？!?]|[^。！？!?\r\n]+?\.(?=\s|$)', str(paragraph)[:1600]):
            sentence = sentence.strip()
            if 12 <= len(sentence) <= 220 and len(sentence.split()) <= 45 and not re.search(r'<|https?://', sentence):
                excerpts.append(sentence)
                if len(excerpts) == 2:
                    break
        if len(excerpts) == 2:
            break
    url = f"https://music.163.com/#/artist?id={artist['id']}"
    return [{'id': f"netease:artist:{artist['id']}:intro", 'text': f"网易云歌手简介对{artist['name']}的介绍：" + ''.join(excerpts),
             'url': url, 'entity_url': url, 'source': '网易云平台歌手简介（非本人采访）', 'kind': 'artist_bio'}] if excerpts else []


def get_artist_background(song, db_path=None, fetcher=None):
    path = Path(db_path or bg.DB_PATH)
    key = hashlib.sha256(json.dumps(['artist-intro-v1', song.get('id'), song.get('title'), song.get('artist')], ensure_ascii=False).encode()).hexdigest()
    now = time.time()
    with sqlite3.connect(path, timeout=5) as conn:
        conn.execute('CREATE TABLE IF NOT EXISTS background_cache(cache_key TEXT PRIMARY KEY, facts TEXT, expires REAL)')
        row = conn.execute('SELECT facts,expires FROM background_cache WHERE cache_key=?', (key,)).fetchone()
    if row and row[1] > now:
        try:
            facts = json.loads(row[0])
            if bg._valid_cached_facts(facts):
                return facts
        except (TypeError, ValueError):
            pass
    try:
        facts = query_artist_intro(song, fetcher)
    except Exception:
        facts = []
    with sqlite3.connect(path, timeout=5) as conn:
        conn.execute('INSERT OR REPLACE INTO background_cache VALUES(?,?,?)',
                     (key, json.dumps(facts, ensure_ascii=False), now + (7 * 86400 if facts else 6 * 3600)))
    return facts
