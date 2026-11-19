"""app/conversation.py - 对话记忆层（SQLite，无外网、无模型）

为什么需要记忆：真正的电台 DJ 必须记得刚才聊到哪。没有记忆的系统每次请求
都失忆，永远显得『笨』。本模块把每轮对话落库，下次请求注入上下文。

注入策略（体验与成本平衡）：
- 最近 N 轮（默认 10 轮）保留原文——细节不丢；
- 更早的轮次压缩成一条摘要——超长对话才触发，且只生成一次并缓存
  （summary_count 记录已覆盖多少条，消息数没变就复用缓存，不重复花 token）；
- 摘要生成依赖外部 LLM，故用注入式 summarizer：conversation.py 只做存储与
  缓存决策，调用方（main.py）传入真实摘要函数；测试传伪摘要器。

为什么独立于 library.db：library.db 会被 build_library.py 清空重建，
对话历史不能跟着丢；conversation.db 单独维护，*.db 已在 .gitignore 中。
"""

import json
import sqlite3
import time
from pathlib import Path

from app.paths import DATA_ROOT

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = DATA_ROOT / "conversation.db"

DEFAULT_RECENT_N = 10  # 保留原文的最近轮数


def _now():
    """统一的时间戳格式（本地时间）。"""
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _init_db(db_path):
    """建表（幂等）。sessions 存会话与摘要缓存，messages 存逐轮消息。"""
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                created_at TEXT,
                summary TEXT,
                summary_count INTEGER
            );
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                related_song_ids TEXT,
                created_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_messages_session
                ON messages(session_id, id);
            CREATE TABLE IF NOT EXISTS conversation_state (
                session_id TEXT PRIMARY KEY,
                intent_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )
        conn.commit()
    finally:
        conn.close()


def add_message(session_id, role, content, related_song_ids=None, db_path=None):
    """追加一条消息；会话不存在则自动创建。

    db_path=None 时用默认 conversation.db；传 None 哨兵而不是把 DB_PATH 绑死成
    默认参数，这样测试可以 monkeypatch conversation.DB_PATH 指向临时库。
    related_song_ids 是回复关联的歌曲 ID 列表（assistant 消息用），
    落库为 JSON 字符串，供后续『翻旧账』或前端恢复使用。
    """
    db_path = db_path or DB_PATH
    _init_db(db_path)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "INSERT OR IGNORE INTO sessions (id, created_at) VALUES (?, ?)",
            (session_id, _now()),
        )
        if isinstance(related_song_ids, (list, tuple)):
            related_song_ids = json.dumps(list(related_song_ids), ensure_ascii=False)
        cursor = conn.execute(
            "INSERT INTO messages (session_id, role, content, related_song_ids, created_at)"
            " VALUES (?,?,?,?,?)",
            (session_id, role, content, related_song_ids, _now()),
        )
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()


def get_context(session_id, recent_n=DEFAULT_RECENT_N, db_path=None, summarizer=None, *, for_model=False, include_ids=False):
    """读取该会话用于注入 LLM 的上下文。

    返回 {"messages": [{"role", "content"}, ...], "summary": str | None}。
    - 消息 <= recent_n：全部原文，summary=None；
    - 消息 > recent_n：最近 recent_n 条原文 + 早期消息的摘要（缓存命中则复用）；
    - summarizer 为 None 且无缓存摘要时：只返回最近 recent_n 条（不报错）。

    messages 是 OpenAI 格式，可直接拼进对话 messages 列表；
    summary 单独返回，供调用方合并进 system prompt。
    """
    db_path = db_path or DB_PATH
    _init_db(db_path)
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT id, role, content FROM messages WHERE session_id=? ORDER BY id",
            (session_id,),
        ).fetchall()
        msgs = [{"id": r[0], "role": r[1], "content": r[2]} for r in rows]
        isolated_memory = False
        if for_model:
            from app.preference_proposal import memory_exchange
            filtered = []
            managing = False
            for message in msgs:
                if message['role'] == 'user':
                    managing = memory_exchange(message['content'])
                    if managing:
                        isolated_memory = True
                        continue
                if message['role'] == 'assistant' and managing:
                    # 保留中性回应阻断旧邀请的点头继承；不把失效偏好或原话交给模型。
                    filtered.append({**message, 'content': '（一次偏好管理操作已结束；有效偏好以后端当前记录为准。）'})
                    managing = False
                else:
                    filtered.append(message)
            msgs = filtered

        summary = None
        if len(msgs) > recent_n:
            early = msgs[:-recent_n]
            recent = msgs[-recent_n:]
            summary = _resolve_summary(conn, session_id, early, summarizer, isolated_memory=isolated_memory)
        else:
            recent = msgs

        return {
            "messages": recent if include_ids else [{"role": m["role"], "content": m["content"]} for m in recent],
            "summary": summary,
        }
    finally:
        conn.close()


def _resolve_summary(conn, session_id, early, summarizer, isolated_memory=False):
    """决定早期消息的摘要：缓存命中则复用，否则调用 summarizer 生成并缓存。

    summary_count 存的是『摘要覆盖了多少条早期消息』；消息数一致说明早期
    部分没变过，摘要仍有效。新增消息后数量变化，下次会重新压缩。
    """
    row = conn.execute(
        "SELECT summary, summary_count FROM sessions WHERE id=?", (session_id,)
    ).fetchone()
    cached = row[0] if row else None
    count = row[1] if row else None

    # 负数合同与旧原文摘要分开，不能因条数巧合相同复用包含已撤销偏好的旧摘要。
    cache_count = -len(early) - 1 if isolated_memory else len(early)
    if cached and count == cache_count:
        return cached
    if summarizer is None:
        return None

    text = summarizer([{"role": m["role"], "content": m["content"]} for m in early])
    conn.execute(
        "UPDATE sessions SET summary=?, summary_count=? WHERE id=?",
        (text, cache_count, session_id),
    )
    conn.commit()
    return text


def clear_session(session_id, db_path=None):
    """清空该会话的全部消息与摘要缓存（幂等，不存在的会话也不报错）。"""
    db_path = db_path or DB_PATH
    _init_db(db_path)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("DELETE FROM messages WHERE session_id=?", (session_id,))
        conn.execute("DELETE FROM conversation_state WHERE session_id=?", (session_id,))
        if conn.execute("SELECT 1 FROM sqlite_master WHERE name='task_song_feedback' AND type='table'").fetchone():
            conn.execute("DELETE FROM task_song_feedback WHERE session_id=?", (session_id,))
        conn.execute("DELETE FROM sessions WHERE id=?", (session_id,))
        conn.commit()
    finally:
        conn.close()


def get_recent_song_ids(session_id, limit=12, db_path=None):
    """收集本会话最近推荐过的歌曲 ID（防重复用），倒序、去重。

    为什么复用 messages.related_song_ids 而不新建表：add_message 落库时已把
    每次推荐的歌曲 ID 存进 assistant 消息，这里只需倒序读最近 N 条并去重，
    即得『用户最近听过/被推荐过的歌』，检索时排除，实现会话内不重复。
    """
    db_path = db_path or DB_PATH
    _init_db(db_path)
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT related_song_ids FROM messages"
            " WHERE session_id=? AND role='assistant'"
            "   AND related_song_ids IS NOT NULL"
            " ORDER BY id DESC LIMIT ?",
            (session_id, limit),
        ).fetchall()
    finally:
        conn.close()
    seen, out = set(), []
    for (raw,) in rows:
        try:
            arr = json.loads(raw or "[]")
        except (ValueError, TypeError):
            arr = []
        for sid in arr:
            if sid not in seen:
                seen.add(sid)
                out.append(sid)
    return out


def save_recommendation_state(session_id, intent, db_path=None):
    """保存最近一次成功推荐采用的结构化条件；同一会话只保留当前状态。"""
    db_path = db_path or DB_PATH
    _init_db(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO conversation_state (session_id, intent_json, updated_at) VALUES (?,?,?)"
            " ON CONFLICT(session_id) DO UPDATE SET"
            " intent_json=excluded.intent_json, updated_at=excluded.updated_at",
            (session_id, json.dumps(intent, ensure_ascii=False), _now()),
        )


def get_recommendation_state(session_id, db_path=None):
    """读取当前推荐条件；没有成功推荐过或数据损坏时返回 None。"""
    db_path = db_path or DB_PATH
    _init_db(db_path)
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT intent_json FROM conversation_state WHERE session_id=?",
            (session_id,),
        ).fetchone()
    if not row:
        return None
    try:
        value = json.loads(row[0])
    except (ValueError, TypeError):
        return None
    return value if isinstance(value, dict) else None
