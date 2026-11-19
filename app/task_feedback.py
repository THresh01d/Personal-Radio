"""当前任务内的单曲排除：与长期赞踩分表，旧意见不迁移、不复活。"""
import json
import sqlite3
import time
from app import conversation


def _exists(conn, name):
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone()


def _state(conn, session_id):
    if not _exists(conn, 'conversation_state'):
        return {}
    row = conn.execute('SELECT intent_json FROM conversation_state WHERE session_id=?', (session_id,)).fetchone()
    try:
        value = json.loads(row[0]) if row else {}
        return value if isinstance(value, dict) else {}
    except (TypeError, ValueError):
        return {}


def active_feedback(session_id, task_id=None, db_path=None):
    """只读取当前任务；旧库无表时中性返回，不为读取自动迁移。"""
    with sqlite3.connect(db_path or conversation.DB_PATH) as conn:
        state = _state(conn, session_id)
        current_id = state.get('_feedback_task_id')
        if not current_id or (task_id is not None and task_id != current_id):
            return {'task_id': current_id, 'query': state.get('query', ''), 'song_ids': []}
        rows = conn.execute('SELECT song_id FROM task_song_feedback WHERE session_id=? AND task_id=? ORDER BY created_at DESC, song_id',
                            (session_id, current_id)).fetchall() if _exists(conn, 'task_song_feedback') else []
        return {'task_id': current_id, 'query': state.get('query', ''), 'song_ids': [row[0] for row in rows]}


def record_task_feedback(session_id, batch_id, song_id, db_path=None):
    """批次归属、作品归属和当前任务在同一写事务核对，迟到点击不能污染新任务。"""
    with sqlite3.connect(db_path or conversation.DB_PATH, timeout=5) as conn:
        conn.execute('BEGIN IMMEDIATE')
        state = _state(conn, session_id)
        task_id = state.get('_feedback_task_id')
        if not task_id or not _exists(conn, 'dj_programs'):
            raise ValueError('请先发起一轮新的选歌，再标记这次不合适。')
        row = conn.execute('SELECT songs,intent FROM dj_programs WHERE batch_id=? AND session_id=?', (batch_id, session_id)).fetchone()
        try:
            songs, intent = (json.loads(row[0]), json.loads(row[1])) if row else ([], {})
            valid = intent.get('_feedback_task_id') == task_id and str(song_id) in {str(s['id']) for s in songs}
        except (ValueError, TypeError, KeyError, AttributeError):
            valid = False
        if not valid:
            raise ValueError('这首歌不属于当前选歌任务，请从当前任务的推荐里操作。')
        conn.execute("""CREATE TABLE IF NOT EXISTS task_song_feedback (
            session_id TEXT NOT NULL, task_id TEXT NOT NULL, song_id TEXT NOT NULL,
            created_at REAL NOT NULL, PRIMARY KEY(session_id,task_id,song_id))""")
        # 仅在用户明确操作时清掉本身份已失效的临时意见，避免长期堆积。
        conn.execute('DELETE FROM task_song_feedback WHERE session_id=? AND task_id<>?', (session_id, task_id))
        conn.execute('INSERT OR IGNORE INTO task_song_feedback VALUES (?,?,?,?)', (session_id, task_id, str(song_id), time.time()))
        return task_id


def revoke_task_feedback(session_id, task_id, song_id, db_path=None):
    """撤销绑定原任务，不让旧面板的撤销误删新任务中同一首歌的意见。"""
    with sqlite3.connect(db_path or conversation.DB_PATH) as conn:
        if not _exists(conn, 'task_song_feedback'):
            return False
        return conn.execute('DELETE FROM task_song_feedback WHERE session_id=? AND task_id=? AND song_id=?',
                            (session_id, task_id, str(song_id))).rowcount > 0
