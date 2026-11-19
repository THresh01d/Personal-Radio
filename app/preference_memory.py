"""明确偏好的统一事实与消费：保留条件、作品绑定和撤销，不由旧聊天推导。"""

import json
import re
import sqlite3
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar

from app import conversation, library, scene_memory
from app.retrieval import artist_identity_keys

KINDS = ('song', 'artist', 'genre', 'expression', 'reference', 'reference_direction')
KIND_LABELS = {'song':'歌曲', 'artist':'歌手', 'genre':'风格', 'expression':'DJ介绍', 'reference':'参考关系',
               'reference_direction':'参考方向'}
VALUES = ('prefer', 'reduce', 'exclude')
EXPRESSION = {'length': ('short', 'normal'), 'mode': ('quiet', 'intro')}
LABELS = {'prefer': '优先一些', 'reduce': '减少推荐', 'exclude': '持续排除',
          'like': '像参考', 'unlike': '不像参考', 'short': '简短介绍',
          'normal': '正常介绍', 'quiet': '直接听歌', 'intro': '保留介绍'}
_exception_query = ContextVar('memory_exception_query', default='')
_request_query = ContextVar('memory_current_query', default=None)
_selected_song = ContextVar('memory_selected_song', default=None)
_dj_length = ContextVar('playback_dj_length', default=None)


@contextmanager
def dj_length_scope(length):
    token = _dj_length.set(length)
    try:
        yield
    finally:
        _dj_length.reset(token)


@contextmanager
def exception_scope(message, current_song_id=None):
    token = _exception_query.set(message if re.match(r'^本次例外[，,:：\s]', message or '') else '')
    request_token = _request_query.set(message or '')
    selected_token = _selected_song.set(current_song_id)
    try:
        yield
    finally:
        _exception_query.reset(token)
        _request_query.reset(request_token)
        _selected_song.reset(selected_token)


def excepted(row):
    text = _exception_query.get()
    target = row.get('target') or row.get('song') or {}
    label = target.get('title') or target.get('label')
    return bool(text and label and label.casefold() in text.casefold())


def _init(conn):
    conn.execute('''CREATE TABLE IF NOT EXISTS preference_memories (
        id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, kind TEXT NOT NULL,
        target_key TEXT NOT NULL, scene TEXT NOT NULL, value TEXT NOT NULL,
        target_json TEXT NOT NULL, source_session TEXT NOT NULL,
        source_text TEXT NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL,
        version INTEGER NOT NULL, status TEXT NOT NULL,
        UNIQUE(owner_id,kind,target_key,scene))''')


def canonical_scene(scene):
    from app.recommendation_state import SCENES
    scene = (scene or '').strip()
    if scene in ('工作', '学习'):
        scene = '工作专注'
    if scene and scene not in (*SCENES, '工作专注'):
        raise ValueError('请选择已有场景，或留空表示一般偏好。')
    return scene


def resolve_song(value, db_path=None):
    """结构化入口允许id，聊天入口先自行解析指代；完整歌名须唯一。"""
    song = library.find_song_by_id(value, db_path=db_path)
    if song:
        return song
    value = (value or '').strip().strip('《》“”「」')
    hits = [s for s in library.find_songs_by_title(value, db_path=db_path) if s['title'].casefold() == value.casefold()]
    if len(hits) != 1:
        raise ValueError('请选定曲库中的歌曲，或填写唯一完整歌名；重名版本不能猜。')
    return hits[0]


def _snapshot(song):
    return {k: song.get(k) for k in ('id', 'title', 'artist', 'album')} | {'fingerprint': scene_memory.fingerprint(song)}


def resolve_target(kind, target, reference='', library_db_path=None):
    if kind not in KINDS:
        raise ValueError('不支持这种偏好类型。')
    target = (target or '').strip()
    if kind in ('song', 'reference', 'reference_direction'):
        song = resolve_song(target, library_db_path)
        result = _snapshot(song)
        key = str(song['id'])
        if kind == 'reference':
            ref = resolve_song(reference, library_db_path)
            if str(ref['id']) == key:
                raise ValueError('参考曲和对照曲应是两首不同作品。')
            result['reference'] = _snapshot(ref)
            key = json.dumps([str(ref['id']), key])
        return key, result
    if kind == 'artist':
        hits = library.find_artist_hits(target, db_path=library_db_path)
        # 只认真实署名，旧aliases并不是可靠的歌手别名来源。
        names = {name for song in hits for name in artist_identity_keys(song.get('artist'))}
        forms = library._artist_identity_forms(target)
        exact = [name for name in names if library._artist_identity_forms(name).intersection(forms)]
        if len(exact) != 1:
            raise ValueError('请填写曲库中一位完整歌手名称；不要填写多位歌手或模糊简称。')
        return exact[0], {'label': target, 'artist_key': exact[0]}
    if kind == 'genre':
        from app.recommendation_state import GENRES, _canonical_genre
        name = _canonical_genre(target)
        if name not in GENRES:
            raise ValueError('请选择已有风格词表中的名称；未知风格暂不保存。')
        return name, {'label': name}
    if target not in EXPRESSION:
        raise ValueError('表达偏好支持介绍篇幅和是否介绍。')
    return target, {'label': '介绍篇幅' if target == 'length' else '介绍方式'}


def _valid_value(kind, target_key, value):
    options = EXPRESSION.get(target_key, ()) if kind == 'expression' else ('like', 'unlike') if kind == 'reference' else VALUES
    if value not in options:
        raise ValueError('这个对象不支持所选偏好。')


def save(session_id, kind, target, value, *, scene='', reference='', source_text='', expected_id=None,
         db_path=None, library_db_path=None, direction=None):
    scene = canonical_scene(scene)
    key, snapshot = resolve_target(kind, target, reference, library_db_path)
    _valid_value(kind, key, value)
    if kind == 'reference_direction':
        from app.reference_direction import describe
        if value != 'prefer' or scene or not direction or not describe(direction):
            raise ValueError('请明确这首参考曲和以后想优先找的情绪或风格。')
        snapshot['direction'] = direction
    owner = scene_memory.owner_for(session_id, db_path)
    now = time.time()
    with sqlite3.connect(db_path or conversation.DB_PATH, timeout=5) as conn:
        conn.execute('BEGIN IMMEDIATE')
        _init(conn)
        conn.row_factory = sqlite3.Row
        previous = None
        legacy = None
        if expected_id:
            if expected_id.startswith('scene:') and scene_memory._exists(conn, 'scene_song_memories'):
                legacy = conn.execute("SELECT * FROM scene_song_memories WHERE id=? AND owner_id=? AND status='active'", (expected_id[6:], owner)).fetchone()
            else:
                previous = conn.execute("SELECT * FROM preference_memories WHERE id=? AND owner_id=? AND status='active'", (expected_id, owner)).fetchone()
            if not previous and not legacy:
                raise ValueError('这条记忆已经变化，请重新读取后修改。')
        old = conn.execute('SELECT * FROM preference_memories WHERE owner_id=? AND kind=? AND target_key=? AND scene=?', (owner, kind, key, scene)).fetchone()
        if previous and old and old['id'] != expected_id and old['status'] == 'active':
            raise ValueError('新的范围已有一条记忆，请先核对并撤销冲突项。')
        if legacy and old and old['status'] == 'active':
            raise ValueError('新的范围已有另一条记忆，请先核对冲突。')
        if old and old['status'] == 'active' and old['value'] == value and json.loads(old['target_json']) == snapshot:
            return old['id']
        identifier = uuid.uuid4().hex
        source = source_text or f"界面设置：{KIND_LABELS[kind]}，{snapshot.get('title') or snapshot['label']}，{scene or '一般偏好'}，{LABELS[value]}"
        conn.execute('''INSERT INTO preference_memories VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(owner_id,kind,target_key,scene) DO UPDATE SET
            id=excluded.id,value=excluded.value,target_json=excluded.target_json,
            source_session=excluded.source_session,source_text=excluded.source_text,
            updated_at=excluded.updated_at,version=excluded.version,status='active' ''',
            (identifier, owner, kind, key, scene, value, json.dumps(snapshot, ensure_ascii=False), session_id,
             source, now, now, old['version'] + 1 if old else 1, 'active'))
        if previous and previous['id'] != (old['id'] if old else None):
            conn.execute("UPDATE preference_memories SET status='revoked',version=version+1,updated_at=? WHERE id=?", (now, previous['id']))
        if legacy:
            conn.execute("UPDATE scene_song_memories SET status='revoked',version=version+1,updated_at=? WHERE id=?", (now, legacy['id']))
        # 同维度的新表态替换旧单曲按钮事实，避免面板一边喜欢、一边减少推荐。
        if kind == 'song' and not scene and scene_memory._exists(conn, 'feedback'):
            conn.execute('DELETE FROM feedback WHERE session_id=? AND song_id=?', (owner, key))
        if kind == 'song' and scene and scene_memory._exists(conn, 'scene_song_memories'):
            conn.execute("UPDATE scene_song_memories SET status='revoked',updated_at=?,version=version+1 WHERE owner_id=? AND scene=? AND song_id=? AND status='active'", (now, owner, scene, key))
        return identifier


def records(session_id, db_path=None, library_db_path=None):
    owner = scene_memory.owner_for(session_id, db_path)
    with sqlite3.connect(db_path or conversation.DB_PATH) as conn:
        if not scene_memory._exists(conn, 'preference_memories'):
            return []
        conn.row_factory = sqlite3.Row
        rows = [dict(row) for row in conn.execute("SELECT * FROM preference_memories WHERE owner_id=? AND status='active' ORDER BY updated_at DESC,id", (owner,))]
    ids = {str(s['id']) for row in rows for s in (json.loads(row['target_json']), json.loads(row['target_json']).get('reference', {})) if 'id' in s}
    metadata = library.find_songs_by_ids(ids, db_path=library_db_path)
    result = []
    for row in rows:
        target = json.loads(row['target_json'])
        effective = True
        if row['kind'] in ('song', 'reference', 'reference_direction'):
            for item in (target, target.get('reference', {})):
                if 'id' in item:
                    current = metadata.get(str(item['id']))
                    effective = effective and bool(current and scene_memory.fingerprint(current) == item['fingerprint'])
        elif row['kind'] == 'artist':
            effective = any(target['artist_key'] in artist_identity_keys(s.get('artist')) for s in library.find_artist_hits(target['label'], db_path=library_db_path))
        result.append({k: row[k] for k in ('id', 'kind', 'scene', 'value', 'source_text', 'created_at', 'updated_at', 'version')} | {
            'target': target, 'target_key': row['target_key'], 'effective': effective,
            'label': target.get('title') or target.get('label'),
            'value_label': LABELS[row['value']],
            'reason': (
                ('仅在以这首歌为参考时使用；本次明确要求优先。' if row['kind'] == 'reference_direction'
                 else '仅匹配此参考方向；候选仍须符合本次条件。' if row['kind'] == 'reference'
                 else '持续排除；需明确本次例外或撤销后才可推荐。' if row['value'] == 'exclude'
                 else '用于后续介绍；本次表达要求可临时覆盖。' if row['kind'] == 'expression'
                 else '用于后续选歌；本次明确要求可覆盖软偏好。')
                if effective else '作品或署名资料已变化，暂停使用，请核对后重新设置。'
            ),
        })
    return result


def revoke(session_id, identifier, db_path=None):
    owner = scene_memory.owner_for(session_id, db_path)
    with sqlite3.connect(db_path or conversation.DB_PATH) as conn:
        if not scene_memory._exists(conn, 'preference_memories'):
            return False
        return conn.execute("UPDATE preference_memories SET status='revoked',updated_at=?,version=version+1 WHERE owner_id=? AND id=? AND status='active'", (time.time(), owner, identifier)).rowcount > 0


def reset_in_transaction(conn, owner):
    if scene_memory._exists(conn, 'preference_memories'):
        conn.execute("UPDATE preference_memories SET status='revoked',updated_at=?,version=version+1 WHERE owner_id=? AND status='active'", (time.time(), owner))


def supersede_song_feedback(conn, owner, song_id):
    if scene_memory._exists(conn, 'preference_memories'):
        conn.execute("UPDATE preference_memories SET status='revoked',updated_at=?,version=version+1 WHERE owner_id=? AND kind='song' AND target_key=? AND scene='' AND value IN ('prefer','reduce') AND status='active'", (time.time(), owner, str(song_id)))


def supersede_scene_feedback(conn, owner, song_id, scene):
    if scene_memory._exists(conn, 'preference_memories'):
        conn.execute("UPDATE preference_memories SET status='revoked',updated_at=?,version=version+1 WHERE owner_id=? AND kind='song' AND target_key=? AND scene=? AND status='active'", (time.time(), owner, str(song_id), scene))


def context_scene(query, session_id=None, scene=''):
    from app.recommendation_state import _task_scene
    selected = _task_scene(query)
    if not selected and scene and session_id:
        previous = conversation.get_recommendation_state(session_id) or {}
        candidate = _task_scene(scene)
        if candidate == _task_scene(previous.get('query') or '', previous.get('artists') or []):
            selected = candidate
    return selected or ''


def relevant(session_id, query='', scene='', reference_id=''):
    if not session_id:
        return []
    selected = context_scene(query, session_id, scene)
    result = [r for r in records(session_id) if r['effective'] and (not r['scene'] or r['scene'] == selected)]
    # 同对象条件偏好覆盖一般偏好，不能重复叠加。
    by_target = {}
    hard = []
    for row in sorted(result, key=lambda r: bool(r['scene'])):
        if row['kind'] == 'reference' and str(row['target']['reference']['id']) != str(reference_id):
            continue
        if row['kind'] == 'reference_direction' and str(row['target']['id']) != str(reference_id):
            continue
        if row['value'] in ('exclude', 'unlike'):
            hard.append(row)
            continue
        by_target[(row['kind'], row['target_key'])] = row
    return [row for row in [*hard, *by_target.values()] if not excepted(row)]


def matches_song(row, song):
    if row['kind'] in ('song', 'reference'):
        return str(song['id']) == str(row['target']['id'])
    if row['kind'] == 'artist':
        return row['target']['artist_key'] in artist_identity_keys(song.get('artist'))
    if row['kind'] == 'genre':
        return row['target_key'] in (song.get('genre') or []) or (row['target_key'] == '纯音乐' and song.get('is_instrumental'))
    return False


def blocked(row, song):
    return row['value'] in ('exclude', 'unlike') and matches_song(row, song)


def factor(song, rows, original=1.0, query='', artist=''):
    """只替换既有15%偏好通道；不同对象意见有界合并，绝不无限相乘。"""
    from app.recommendation_state import _genre_values
    requested = _genre_values(query, None)
    if song.get('title') and f"《{song['title']}》".casefold() in query.casefold():
        return 1.0
    used = []
    for row in rows:
        if row['value'] not in ('prefer', 'reduce') or not matches_song(row, song):
            continue
        # 当前点名歌手或风格时，尊重本次明确方向，长期软偏好不反向牵制。
        if artist and row['kind'] == 'artist':
            continue
        if requested and row['kind'] == 'genre':
            continue
        used.append((2 if row['kind'] == 'song' else 1, row))
    if not used:
        return original
    specificity = max(item[0] for item in used)
    values = [r['value'] for level, r in used if level == specificity]
    return 0.15 if 'reduce' in values else 1.2


def expression(session_id, query=''):
    settings = {r['target_key']: r['value'] for r in relevant(session_id, query) if r['kind'] == 'expression'}
    length = _dj_length.get()
    if length in EXPRESSION['length']:
        settings['length'] = length
    override = _request_query.get()
    override = query if override is None else override
    if re.search(r'(?:这次|本次|现在).*(?:详细|多说|正常介绍)', override):
        settings.update(length='normal', mode='intro')
    elif re.search(r'(?:这次|本次|现在).*(?:简短|少说)', override):
        settings.update(length='short', mode='intro')
    if re.search(r'(?:这次|本次|现在).*(?:别介绍|不用介绍|直接听|直接放)', override):
        settings['mode'] = 'quiet'
    return settings


def present_program(payload, session_id, query):
    """每次读取都按当前有效偏好呈现；撤销不用等待旧节目缓存重建。"""
    settings = expression(session_id, query)
    if not settings:
        return payload
    payload = dict(payload)
    if settings.get('mode') == 'quiet':
        payload.update(text='', sources=[], segments=[], mode='metadata', status='ready',
                       copy_status='disabled', opening_status='disabled')
    return payload
