"""把显式反馈、收听行为和真实歌曲元数据装配成可解释画像。

原始事实仍分别保存在 feedback / listening_events；本模块只负责读取和解释，
不回写任何推断。这样阈值或系数调整后，画像可由原始事实立即重算。
"""

import sqlite3

from app import audio_features, conversation, library
from app.feedback import get_taste, song_taste_factor
from app.task_feedback import active_feedback
from app.scene_memory import owner_for, records as scene_memories, reset_in_transaction
from app import preference_memory
from app.listening import (
    clear_context,
    get_behavior_signals,
    get_listening_summary,
)


def _explicit_rows(session_id, db_path):
    """读取当前有效表态；同一首歌只会有 upsert 后的最新一条。"""
    # get_taste 会幂等建 feedback 表，先调用它可兼容全新会话数据库。
    get_taste(session_id, db_path=db_path)
    with sqlite3.connect(db_path) as conn:
        return conn.execute(
            "SELECT song_id, action FROM feedback WHERE session_id=?"
            " ORDER BY created_at DESC, song_id",
            (owner_for(session_id, db_path),),
        ).fetchall()


def _song_view(
    song_id,
    metadata,
    *,
    reason,
    factor,
    confidence,
    source,
    scope,
    evidence=None,
):
    """统一歌曲展示结构；元数据缺失时明确标记，而不是编造标题。"""
    song = metadata.get(str(song_id))
    view = {
        "id": str(song_id),
        "title": song.get("title") if song else None,
        "artist": song.get("artist") if song else None,
        "album": song.get("album") if song else None,
        "genre": song.get("genre", []) if song else [],
        "mood": song.get("mood", []) if song else [],
        "reason": reason,
        "factor": factor,
        "confidence": confidence,
        "source": source,
        "scope": scope,
        "metadata_available": song is not None,
    }
    if evidence is not None:
        view["evidence"] = evidence
    return view


def _behavior_reason(evidence, positive):
    """把机器计数翻译成用户能理解、且不超出事实的中文依据。"""
    parts = []
    if positive:
        if evidence["finishes"]:
            parts.append(f"有效听完 {evidence['finishes']} 次")
        if evidence["replays"]:
            parts.append(f"主动重播 {evidence['replays']} 次")
    elif evidence["early_skips"]:
        parts.append(f"前段跳过 {evidence['early_skips']} 次")
    return "、".join(parts)


def _behavior_confidence(evidence, positive):
    """把证据强度压到保守区间；隐式判断永远不会伪装成 100% 确定。"""
    if positive:
        strength = evidence["finishes"] + evidence["replays"] * 2
        return round(min(0.9, 0.5 + strength * 0.1), 2)
    return round(min(0.85, 0.45 + evidence["early_skips"] * 0.1), 2)


def _effective_factor(song_id, taste):
    """展示与推荐共用单曲规则，避免读取署名推断其他作品的偏好。"""
    return round(song_taste_factor(song_id, taste), 4)


def reset_profile_facts(session_id, *, behavior_db_path=None):
    """在一个事务里重置画像事实，避免只删掉一半造成状态分叉。"""
    behavior_db_path = behavior_db_path or conversation.DB_PATH
    # 两个读取函数会幂等创建各自数据表，兼容第一次启动的空数据库。
    get_taste(session_id, db_path=behavior_db_path)
    get_behavior_signals(session_id, db_path=behavior_db_path)
    owner = owner_for(session_id, behavior_db_path)
    with sqlite3.connect(behavior_db_path) as conn:
        feedback_cursor = conn.execute(
            "DELETE FROM feedback WHERE session_id=?", (owner,)
        )
        clear_context(conn, session_id)
        event_cursor = conn.execute(
            "DELETE FROM listening_events WHERE session_id=?", (session_id,)
        )
        if conn.execute("SELECT 1 FROM sqlite_master WHERE name='task_song_feedback' AND type='table'").fetchone():
            conn.execute('DELETE FROM task_song_feedback WHERE session_id=?', (session_id,))
        reset_in_transaction(conn, owner)
        preference_memory.reset_in_transaction(conn, owner)
        return {
            "removed_feedback": feedback_cursor.rowcount,
            "removed_events": event_cursor.rowcount,
        }


def build_taste_profile(session_id, *, behavior_db_path=None, library_db_path=None):
    """返回一个会话当前可见、可解释、可重算的完整画像。"""
    behavior_db_path = behavior_db_path or conversation.DB_PATH
    library_db_path = library_db_path or library.DB_PATH

    taste = get_taste(session_id, db_path=behavior_db_path)
    rows = _explicit_rows(session_id, behavior_db_path)
    behavior = get_behavior_signals(session_id, db_path=behavior_db_path)
    summary = get_listening_summary(session_id, db_path=behavior_db_path)["totals"]

    task = active_feedback(session_id, db_path=behavior_db_path)
    all_song_ids = {row[0] for row in rows}
    all_song_ids.update(task["song_ids"])
    all_song_ids.update(behavior["behavior_evidence"])
    metadata = library.find_songs_by_ids(all_song_ids, db_path=library_db_path)

    liked = []
    disliked = []
    for song_id, action in rows:
        if action == "like":
            liked.append(_song_view(
                song_id,
                metadata,
                reason="你明确点过喜欢；这份偏好仅作用于这首歌",
                factor=_effective_factor(str(song_id), taste),
                confidence=1.0,
                source="explicit_feedback",
                scope="long_term",
            ))
        elif action == "dislike":
            disliked.append(_song_view(
                song_id,
                metadata,
                reason="你明确点过减少推荐；这份偏好仅作用于这首歌",
                factor=_effective_factor(str(song_id), taste),
                confidence=1.0,
                source="explicit_feedback",
                scope="long_term",
            ))

    # 原面板字段继续返回空列表，防止旧客户端把单曲表态显示成明确歌手偏好。
    boosted_artists = []
    demoted_artists = []

    evidence = behavior["behavior_evidence"]
    behavior_boosted = [
        _song_view(
            song_id,
            metadata,
            reason=_behavior_reason(evidence[song_id], positive=True),
            factor=_effective_factor(
                song_id, taste
            ),
            confidence=_behavior_confidence(evidence[song_id], positive=True),
            source="listening_behavior",
            scope="session",
            evidence=evidence[song_id],
        )
        for song_id in sorted(behavior["behavior_boost_song_ids"])
    ]
    behavior_demoted = [
        _song_view(
            song_id,
            metadata,
            reason=_behavior_reason(evidence[song_id], positive=False),
            factor=_effective_factor(
                song_id, taste
            ),
            confidence=_behavior_confidence(evidence[song_id], positive=False),
            source="listening_behavior",
            scope="session",
            evidence=evidence[song_id],
        )
        for song_id in sorted(behavior["behavior_demote_song_ids"])
    ]

    task_songs = [dict(_song_view(
        song_id, metadata, reason="你标记了这次不合适；当前任务后续选歌避开，换任务后失效。",
        factor=0.0, confidence=1.0, source="task_feedback", scope="task"),
        task_id=task['task_id']) for song_id in task['song_ids']]
    return {
        "task_feedback": {"task_id": task['task_id'], "query": task['query'], "songs": task_songs},
        "scene_memories": scene_memories(session_id, behavior_db_path, library_db_path),
        "preferences": preference_memory.records(session_id, behavior_db_path, library_db_path),
        "explicit": {
            "liked_songs": liked,
            "disliked_songs": disliked,
            "boosted_artists": boosted_artists,
            "demoted_artists": demoted_artists,
        },
        "behavior": {
            "quality": behavior['behavior_quality'],
            "boosted_songs": behavior_boosted,
            "demoted_songs": behavior_demoted,
            "evidence": evidence,
        },
        "summary": summary,
        "boundaries": {
            "long_term": {
                "label": "长期偏好",
                "description": "主动点击的喜欢或减少推荐仅作用于该单曲，不推广到整位歌手；清空聊天后仍保留。",
            },
            "session": {
                "label": "当前会话信号",
                "description": "仅用新版来源明确的收听记录作弱推断；自动循环、故障中断与来源不明的旧记录不参与。清空聊天时一并清除。",
            },
            "context": {
                "label": "当次场景",
                "description": "“这次不合适”只在当前选歌任务中避开该曲；继续换歌仍有效，换任务或清空聊天后失效。",
            },
        },
        # 声学档案属于整座私人曲库，而非某一次会话；与个人喜好分开呈现，
        # 用户能看见系统实际听过多少首，而不是把声音特征说成“喜欢”。
        "acoustic": audio_features.get_coverage(library_db_path),
    }
