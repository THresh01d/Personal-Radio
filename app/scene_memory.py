"""带场景的单曲排除；结构化事实是唯一依据，不从聊天摘要重新提取。"""

import hashlib
import json
import sqlite3
import time
import uuid

from app import conversation, library


def _exists(conn, table):
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()


def owner_for(session_id, db_path=None):
    """旧身份直接兼容；读取不建表，也不遍历合并其他浏览器的历史。"""
    with sqlite3.connect(db_path or conversation.DB_PATH) as conn:
        row = conn.execute('SELECT owner_id FROM memory_sessions WHERE session_id=?', (session_id,)).fetchone() if _exists(conn, 'memory_sessions') else None
    return row[0] if row else session_id


def bind_owner(session_id, owner_id, db_path=None):
    """本机匿名归属绑定只允许一次；会话不能被后来的客户端改绑。"""
    if not session_id or not owner_id or len(session_id) > 128 or len(owner_id) > 128:
        raise ValueError('本机记忆身份无效，请刷新后重试。')
    with sqlite3.connect(db_path or conversation.DB_PATH) as conn:
        conn.execute('BEGIN IMMEDIATE')
        conn.execute('CREATE TABLE IF NOT EXISTS memory_sessions (session_id TEXT PRIMARY KEY, owner_id TEXT NOT NULL)')
        row = conn.execute('SELECT owner_id FROM memory_sessions WHERE session_id=?', (session_id,)).fetchone()
        if row and row[0] != owner_id:
            raise ValueError('当前聊天已属于另一份本机记忆，请刷新页面。')
        # 旧反馈及旧会话不能被新标识接管；前端以原session作为稳定owner兼容。
        if not row and session_id != owner_id:
            for table, column in (('sessions', 'id'), ('feedback', 'session_id')):
                if _exists(conn, table) and conn.execute(f'SELECT 1 FROM {table} WHERE {column}=? LIMIT 1', (session_id,)).fetchone():
                    raise ValueError('已有聊天不能重新归属；请保留原本机身份。')
        conn.execute('INSERT OR IGNORE INTO memory_sessions VALUES (?,?)', (session_id, owner_id))


def fingerprint(song):
    """绑定作品及专辑版本；元数据变化时停用旧判断，宁可要求用户核对。"""
    return hashlib.sha256(json.dumps([str(song.get(k) or '') for k in ('id', 'title', 'artist', 'album')], ensure_ascii=False).encode()).hexdigest()


def _init(conn):
    conn.execute('''CREATE TABLE IF NOT EXISTS scene_song_memories (
        id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, scene TEXT NOT NULL,
        song_id TEXT NOT NULL, song_json TEXT NOT NULL, fingerprint TEXT NOT NULL,
        source_session TEXT NOT NULL, source_text TEXT NOT NULL,
        created_at REAL NOT NULL, updated_at REAL NOT NULL,
        version INTEGER NOT NULL, status TEXT NOT NULL,
        UNIQUE(owner_id,scene,song_id))''')


def save(session_id, scene, song, source_text, db_path=None):
    from app.recommendation_state import SCENES
    if scene not in (*SCENES, '工作专注') or not source_text:
        raise ValueError('暂不支持这个场景，请明确说睡前、工作或其他已有场景。')
    owner = owner_for(session_id, db_path)
    now = time.time()
    with sqlite3.connect(db_path or conversation.DB_PATH) as conn:
        conn.execute('BEGIN IMMEDIATE')
        _init(conn)
        from app.preference_memory import supersede_scene_feedback
        supersede_scene_feedback(conn, owner, song['id'], scene)
        old = conn.execute('SELECT id,version,status,fingerprint FROM scene_song_memories WHERE owner_id=? AND scene=? AND song_id=?', (owner, scene, str(song['id']))).fetchone()
        if old and old[2] == 'active' and old[3] == fingerprint(song):
            return old[0]
        identifier = uuid.uuid4().hex
        snapshot = {k: song.get(k) for k in ('id', 'title', 'artist', 'album')}
        conn.execute('''INSERT INTO scene_song_memories VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(owner_id,scene,song_id) DO UPDATE SET
            id=excluded.id,song_json=excluded.song_json,fingerprint=excluded.fingerprint,
            source_session=excluded.source_session,source_text=excluded.source_text,
            updated_at=excluded.updated_at,version=excluded.version,status='active' ''',
            (identifier, owner, scene, str(song['id']), json.dumps(snapshot, ensure_ascii=False), fingerprint(song),
             session_id, source_text, now, now, old[1] + 1 if old else 1, 'active'))
        return identifier


def records(session_id, db_path=None, library_db_path=None):
    owner = owner_for(session_id, db_path)
    with sqlite3.connect(db_path or conversation.DB_PATH) as conn:
        if not _exists(conn, 'scene_song_memories'):
            return []
        conn.row_factory = sqlite3.Row
        rows = [dict(r) for r in conn.execute("SELECT * FROM scene_song_memories WHERE owner_id=? AND status='active' ORDER BY updated_at DESC,id", (owner,))]
    metadata = library.find_songs_by_ids([r['song_id'] for r in rows], db_path=library_db_path)
    result = []
    for row in rows:
        song = metadata.get(row['song_id'])
        valid = bool(song and fingerprint(song) == row['fingerprint'])
        result.append({k: row[k] for k in ('id', 'scene', 'song_id', 'source_text', 'created_at', 'updated_at', 'version')} | {
            'song': json.loads(row['song_json']), 'effective': valid,
            'reason': f"只在{row['scene']}选歌时避开，其他场景不受影响。" if valid else '作品已移除或版本资料变化，已暂停使用，请核对后重新设置。',
        })
    return result


def revoke(session_id, identifier, db_path=None):
    owner = owner_for(session_id, db_path)
    with sqlite3.connect(db_path or conversation.DB_PATH) as conn:
        if not _exists(conn, 'scene_song_memories'):
            return False
        # 重设会生成新id，旧面板和迟到撤销不能删除新意见；保留失效状态防重算复活。
        return conn.execute("UPDATE scene_song_memories SET status='revoked',updated_at=?,version=version+1 WHERE owner_id=? AND id=? AND status='active'", (time.time(), owner, identifier)).rowcount > 0


def reset_in_transaction(conn, owner):
    if _exists(conn, 'scene_song_memories'):
        conn.execute("UPDATE scene_song_memories SET status='revoked',updated_at=?,version=version+1 WHERE owner_id=? AND status='active'", (time.time(), owner))


def matching(session_id, intent=None, *, query='', scene='', db_path=None, library_db_path=None):
    """只按当前任务的明确场景消费；无场景时不根据时钟、旧聊天或模型猜测。"""
    if not session_id:
        return []
    from app.recommendation_state import _task_scene
    intent = intent or {}
    selected = _task_scene(intent.get('query') or query, intent.get('artists') or [])
    if not selected:
        # 模型单独猜出的scene不能启用排除；续播仅接受与服务端原任务一致的场景。
        text = ' '.join(intent.get('scenes') or []) if intent else scene
        requested = _task_scene(text)
        if requested:
            previous = conversation.get_recommendation_state(session_id, db_path)
            previous = previous or {}
            previous_scene = _task_scene(previous.get('query') or '', previous.get('artists') or [])
            if requested == previous_scene:
                selected = requested
    if not selected:
        return []
    from app.preference_memory import excepted
    return [r for r in records(session_id, db_path, library_db_path) if r['effective'] and r['scene'] == selected and not excepted(r)]
