"""taste HTTP适配；应用保护由统一装配入口注册。"""

from fastapi import APIRouter, HTTPException
from app import conversation, library, listening
from app.feedback import delete_feedback, record_feedback
from app.taste_profile import build_taste_profile, reset_profile_facts
from app.api.schemas import FeedbackRequest, TaskFeedbackRequest, MemoryRequest, ReferenceFeedbackRequest
from app.task_feedback import record_task_feedback, revoke_task_feedback

router = APIRouter()


@router.post('/api/memories')
def save_memory(req: MemoryRequest):
    from app import preference_memory, scene_memory
    try:
        if req.memory_owner_id:
            scene_memory.bind_owner(req.session_id, req.memory_owner_id)
        identifier = preference_memory.save(req.session_id, req.kind, req.target, req.value, scene=req.scene,
            reference=req.reference, expected_id=req.expected_id)
    except ValueError as error:
        raise HTTPException(409, str(error)) from error
    return {'ok': True, 'id': identifier}


@router.delete('/api/sessions/{session_id}/memories/{memory_id}')
def remove_memory(session_id: str, memory_id: str):
    from app.preference_memory import revoke
    return {'ok': True, 'removed': revoke(session_id, memory_id)}


@router.post('/api/reference-feedback')
def reference_feedback(req: ReferenceFeedbackRequest):
    from app import dj_program, preference_memory
    program = dj_program.read_program(req.batch_id, req.session_id, conversation.DB_PATH, internal=True)
    ref_id = (program or {}).get('intent', {}).get('reference_song_id')
    if not program or not ref_id or req.song_id not in {str(s['id']) for s in program['songs']}:
        raise HTTPException(409, '请从有真实参考曲的推荐批次中操作。')
    # 批次和当前曲库均须对应同一作品版本，不能把旧卡片反馈套到更新后的作品。
    current = library.find_songs_by_ids([ref_id, req.song_id])
    snapshots = {str(s['id']): s for s in program['songs']}
    from app.scene_memory import fingerprint
    if snapshots[req.song_id].get('reference_song_id', ref_id) != ref_id:
        raise HTTPException(409, '这首歌使用了其他参考方向，请从与它对应的推荐批次中反馈。')
    if req.song_id not in current or fingerprint(current[req.song_id]) != fingerprint(snapshots[req.song_id]):
        raise HTTPException(409, '这首歌的版本资料已变化，请重新选歌后反馈。')
    reference_snapshot = program['intent'].get('_memory_reference')
    if not reference_snapshot or str(ref_id) not in current or fingerprint(current[str(ref_id)]) != fingerprint(reference_snapshot):
        raise HTTPException(409, '这轮缺少有效参考版本绑定，请重新按参考选歌。')
    try:
        identifier = preference_memory.save(req.session_id, 'reference', req.song_id, req.value, reference=str(ref_id),
            source_text=f"以《{current[str(ref_id)]['title']}》为参考时，《{current[req.song_id]['title']}》{preference_memory.LABELS[req.value]}")
    except ValueError as error:
        raise HTTPException(409, str(error)) from error
    return {'ok': True, 'id': identifier}


@router.delete('/api/sessions/{session_id}/scene-memories/{memory_id}')
def remove_scene_memory(session_id: str, memory_id: str):
    from app.scene_memory import revoke
    return {'ok': True, 'removed': revoke(session_id, memory_id)}


@router.post("/api/feedback")
def feedback(req: FeedbackRequest):
    """记录用户对某首推荐歌的 👍/👎（品味画像素材）。

    只落库最新表态，不立刻改动本轮已展示的歌单；后续 recommend_playlist 会按
    会话画像对候选做软降权（👎 沉底、👍 加权，详见 tools._apply_taste / feedback.py）。
    匿名身份 = 前端 localStorage 持久化的 session_id，无需登录。
    """
    record_feedback(req.session_id, req.song_id, req.action, artist=req.artist)
    return {"ok": True}


@router.post("/api/task-feedback")
def task_feedback(req: TaskFeedbackRequest):
    try:
        task_id = record_task_feedback(req.session_id, req.batch_id, req.song_id)
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return {"ok": True, "task_id": task_id}


@router.delete("/api/sessions/{session_id}/task-feedback/{task_id}/{song_id}")
def remove_task_feedback(session_id: str, task_id: str, song_id: str):
    return {"ok": True, "removed": revoke_task_feedback(session_id, task_id, song_id)}


@router.get("/api/sessions/{session_id}/messages")
def session_messages(session_id: str):
    """恢复最近 50 条对话，按消息 ID 还原已经就绪的节目开场。"""
    ctx = conversation.get_context(session_id, recent_n=50, include_ids=True)
    from app.dj_program import project_chat_messages
    messages = project_chat_messages(session_id, ctx["messages"], conversation.DB_PATH)
    return {"session_id": session_id, "messages": messages, "summary": ctx["summary"]}


@router.get("/api/sessions/{session_id}/listening-summary")
def session_listening_summary(session_id: str):
    """原始行为观察口：只返回事实统计，方便检查画像推断的依据。"""
    return {
        "session_id": session_id,
        **listening.get_listening_summary(session_id, db_path=conversation.DB_PATH),
    }


@router.get("/api/sessions/{session_id}/taste-profile")
def session_taste_profile(session_id: str):
    """返回 DJ 当前记住的显式偏好、行为推断及其可核对依据。"""
    return {
        "session_id": session_id,
        **build_taste_profile(
            session_id,
            behavior_db_path=conversation.DB_PATH,
            library_db_path=library.DB_PATH,
        ),
    }


@router.delete("/api/sessions/{session_id}/taste-profile/feedback/{song_id}")
def remove_taste_feedback(session_id: str, song_id: str):
    """撤销一项长期显式判断；不存在时也保持幂等。"""
    removed = delete_feedback(
        session_id, song_id, db_path=conversation.DB_PATH
    )
    return {"ok": True, "removed": removed}


@router.delete("/api/sessions/{session_id}/taste-profile/behavior/{song_id}")
def remove_taste_behavior(session_id: str, song_id: str):
    """清除一首歌的会话行为证据，让对应推断立即消失。"""
    removed_events = listening.clear_song_events(
        session_id, song_id, db_path=conversation.DB_PATH
    )
    return {"ok": True, "removed_events": removed_events}


@router.post("/api/sessions/{session_id}/taste-profile/recalculate")
def recalculate_taste_profile(session_id: str):
    """画像没有隐藏缓存；显式重算入口直接从现存事实重新派生。"""
    return session_taste_profile(session_id)


@router.delete("/api/sessions/{session_id}/taste-profile")
def reset_taste_profile(session_id: str):
    """只重置个人画像，保留聊天、推荐批次、曲库与声学档案。"""
    removed = reset_profile_facts(
        session_id, behavior_db_path=conversation.DB_PATH
    )
    return {"ok": True, **removed}
