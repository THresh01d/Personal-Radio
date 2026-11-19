"""推荐批次与收听行为存储。

这里刻意只保存“发生过的事实”，不急着把事实解释成喜好：
- recommendation_batches：DJ 哪一轮推荐了哪些歌；
- listening_events：用户实际播放、跳过、播完或重播了哪首歌。

画像信号由文件末尾的纯派生函数从原始事实计算，不回写事件表。日后调整规则时
可以随时重新计算，不会被早期的一次错误判断永久污染。
"""

import json
import math
import secrets
import sqlite3
import time
from pathlib import Path

from app.paths import DATA_ROOT

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = DATA_ROOT / "conversation.db"

EVENT_TYPES = frozenset({"play", "skip", "finish", "replay"})

# 隐式行为只做轻量调整，权重必须弱于用户主动点击的 👍/👎。
BEHAVIOR_DEMOTE_FACTOR = 0.70
BEHAVIOR_BOOST_FACTOR = 1.15
EARLY_SKIP_SECONDS = 30
EARLY_SKIP_RATIO = 0.25
EARLY_SKIP_THRESHOLD = 2
FINISH_BOOST_THRESHOLD = 2


def _now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _init_db(db_path):
    """幂等建表；行为数据与对话共用 conversation.db，但职责单独封装。"""
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS recommendation_batches (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                query TEXT NOT NULL,
                channel TEXT,
                song_ids TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_batches_session
                ON recommendation_batches(session_id, created_at);

            CREATE TABLE IF NOT EXISTS listening_events (
                event_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                batch_id TEXT,
                song_id TEXT NOT NULL,
                event_type TEXT NOT NULL
                    CHECK(event_type IN ('play', 'skip', 'finish', 'replay')),
                position_seconds REAL NOT NULL DEFAULT 0,
                duration_seconds REAL,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_listening_events_session
                ON listening_events(session_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_listening_events_song
                ON listening_events(song_id, event_type);

            CREATE TABLE IF NOT EXISTS listening_event_context (
                event_id TEXT PRIMARY KEY,
                playback_id TEXT NOT NULL,
                start_reason TEXT NOT NULL,
                interrupted INTEGER NOT NULL,
                played_seconds REAL NOT NULL
            );
            """
        )
        conn.commit()
    finally:
        conn.close()


def record_recommendation_batch(
    session_id, query, song_ids, *, channel=None, batch_id=None, db_path=None
):
    """记录一次实际返回给用户的歌单，并返回稳定的批次 ID。"""
    db_path = db_path or DB_PATH
    _init_db(db_path)
    batch_id = batch_id or secrets.token_hex(8)
    clean_ids = [
        str(song_id) for song_id in song_ids
        if song_id is not None and str(song_id).strip()
    ]
    if not clean_ids:
        return None

    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "INSERT INTO recommendation_batches"
            " (id, session_id, query, channel, song_ids, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (
                batch_id,
                session_id,
                query,
                channel,
                json.dumps(clean_ids, ensure_ascii=False),
                _now(),
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return batch_id


def record_listening_event(
    session_id,
    song_id,
    event_type,
    *,
    event_id=None,
    batch_id=None,
    position_seconds=0,
    duration_seconds=None,
    context=None,
    db_path=None,
):
    """追加一条收听事实；相同 event_id 重试时只保留一条。"""
    if event_type not in EVENT_TYPES:
        raise ValueError(f"不支持的收听事件: {event_type}")
    if not math.isfinite(position_seconds) or position_seconds < 0 or (duration_seconds is not None and (not math.isfinite(duration_seconds) or duration_seconds < 0)):
        raise ValueError("播放位置和时长不能为负数")
    if context is not None:
        # 内部调用与HTTP使用同一合同；旧客户端不补造来源，重复旧ID也不能升级证据。
        from app.api.schemas import ListeningContext
        context = ListeningContext.model_validate(context).model_dump()

    db_path = db_path or DB_PATH
    _init_db(db_path)
    event_id = event_id or secrets.token_hex(12)
    conn = sqlite3.connect(db_path)
    try:
        inserted = conn.execute(
            "INSERT OR IGNORE INTO listening_events"
            " (event_id, session_id, batch_id, song_id, event_type,"
            " position_seconds, duration_seconds, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                event_id,
                session_id,
                batch_id,
                str(song_id),
                event_type,
                float(position_seconds),
                float(duration_seconds) if duration_seconds is not None else None,
                _now(),
            ),
        )
        if inserted.rowcount and context:
            conn.execute('INSERT INTO listening_event_context VALUES (?,?,?,?,?)', (
                event_id, context['playback_id'], context['start_reason'], int(context['interrupted']), context['played_seconds']))
        conn.commit()
    finally:
        conn.close()
    return event_id


def clear_session_history(session_id, db_path=None):
    """清空会话时同步清掉推荐批次和行为事实，保持用户预期一致。"""
    db_path = db_path or DB_PATH
    _init_db(db_path)
    conn = sqlite3.connect(db_path)
    try:
        clear_context(conn, session_id)
        conn.execute("DELETE FROM listening_events WHERE session_id=?", (session_id,))
        conn.execute("DELETE FROM recommendation_batches WHERE session_id=?", (session_id,))
        conn.commit()
    finally:
        conn.close()


def clear_song_events(session_id, song_id, db_path=None):
    """按用户要求清除一首歌的播放器事实，保留推荐批次与其他歌曲。"""
    db_path = db_path or DB_PATH
    _init_db(db_path)
    conn = sqlite3.connect(db_path)
    try:
        clear_context(conn, session_id, song_id)
        cursor = conn.execute(
            "DELETE FROM listening_events WHERE session_id=? AND song_id=?",
            (session_id, str(song_id)),
        )
        conn.commit()
        return cursor.rowcount
    finally:
        conn.close()


def clear_context(conn, session_id, song_id=None):
    """与原始事件同一事务清理附属来源；不改写既有事件表或历史数据。"""
    condition = 'session_id=?' + (' AND song_id=?' if song_id is not None else '')
    args = (session_id, str(song_id)) if song_id is not None else (session_id,)
    conn.execute(f'DELETE FROM listening_event_context WHERE event_id IN (SELECT event_id FROM listening_events WHERE {condition})', args)


def get_listening_summary(session_id, db_path=None):
    """按歌曲汇总原始行为，供画像规则观察使用，但不直接推断喜欢/讨厌。"""
    db_path = db_path or DB_PATH
    _init_db(db_path)
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            """
            SELECT song_id,
                   SUM(event_type='play'),
                   SUM(event_type='skip'),
                   SUM(event_type='finish'),
                   SUM(event_type='replay')
              FROM listening_events
             WHERE session_id=?
             GROUP BY song_id
             ORDER BY MAX(created_at) DESC
            """,
            (session_id,),
        ).fetchall()
    finally:
        conn.close()

    songs = [
        {
            "song_id": song_id,
            "plays": int(plays or 0),
            "skips": int(skips or 0),
            "finishes": int(finishes or 0),
            "replays": int(replays or 0),
        }
        for song_id, plays, skips, finishes, replays in rows
    ]
    return {
        "songs": songs,
        "totals": {
            key: sum(song[key] for song in songs)
            for key in ("plays", "skips", "finishes", "replays")
        },
    }


def _is_early_skip(position_seconds, duration_seconds):
    """早跳 = 前 30 秒内；已知时长时还必须位于前 25%。"""
    position = float(position_seconds or 0)
    duration = float(duration_seconds or 0)
    boundary = EARLY_SKIP_SECONDS
    if duration > 0:
        boundary = min(boundary, duration * EARLY_SKIP_RATIO)
    return position <= boundary


def get_behavior_signals(session_id, db_path=None):
    """把原始行为派生为低置信度的歌曲级信号。

    规则有意保守：
    - 早跳至少 2 次，且从未播完/重播，才弱降权；
    - 播完至少 2 次，或主动重播 1 次，才弱加权；
    - 正向证据优先，避免一首后来重播的歌仍被早期跳过永久压制。
    """
    db_path = db_path or DB_PATH
    _init_db(db_path)
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT e.song_id,e.event_type,e.position_seconds,e.duration_seconds,c.playback_id,c.start_reason,c.interrupted,c.played_seconds"
            " FROM listening_events e LEFT JOIN listening_event_context c ON c.event_id=e.event_id"
            " WHERE e.session_id=? ORDER BY e.created_at,e.event_id",
            (session_id,),
        ).fetchall()
    finally:
        conn.close()

    evidence = {}
    quality = dict(legacy_events=0, loop_events=0, interrupted_events=0, insufficient_events=0)
    # 每次实际播放最多贡献一次同类证据；异步到达次序不影响最终派生结果。
    starts = {(row[0],row[4],row[5]) for row in rows if row[4] and row[1] in ('play','replay')}
    interrupted_playbacks = {(row[0],row[4]) for row in rows if row[6]}
    used = set()
    for song_id, event_type, position, duration, playback_id, reason, interrupted, played in rows:
        if not playback_id:
            quality['legacy_events'] += 1
            continue
        if (song_id,playback_id) in interrupted_playbacks:
            quality['interrupted_events'] += 1
            continue
        if reason == 'repeat_one' and event_type != 'skip':
            quality['loop_events'] += 1
            continue
        if (song_id,playback_id,reason) not in starts:
            quality['insufficient_events'] += 1
            continue
        key = (song_id,playback_id,event_type)
        if key in used:
            continue
        used.add(key)
        item = evidence.setdefault(
            song_id, {"early_skips": 0, "finishes": 0, "replays": 0}
        )
        if event_type == "skip" and _is_early_skip(position, duration):
            item["early_skips"] += 1
        elif event_type == "finish" and duration and duration > 0 and played >= duration * .8:
            item["finishes"] += 1
        elif event_type == "replay" and reason == 'user_replay':
            item["replays"] += 1
        elif event_type == 'finish':
            quality['insufficient_events'] += 1

    boost_song_ids = {
        song_id for song_id, item in evidence.items()
        if item["replays"] >= 1 or item["finishes"] >= FINISH_BOOST_THRESHOLD
    }
    demote_song_ids = {
        song_id for song_id, item in evidence.items()
        if song_id not in boost_song_ids
        and item["early_skips"] >= EARLY_SKIP_THRESHOLD
        and item["finishes"] == 0
        and item["replays"] == 0
    }
    return {
        "behavior_demote_song_ids": demote_song_ids,
        "behavior_boost_song_ids": boost_song_ids,
        "behavior_evidence": evidence,
        "behavior_quality": quality,
    }
