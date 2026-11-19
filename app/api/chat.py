"""聊天HTTP适配：请求模型和状态码留在边界，业务流程交给服务。"""

from fastapi import APIRouter, HTTPException
from app import conversation, listening, dj_program, preference_memory
from app.api.schemas import ChatRequest, ClearRequest, AdjustmentRequest, AutoQueueRequest, DJQueueRequest
from app.services import chat as chat_service
from app.services.chat_response import _finish_chat_response
from app.recommendation_adjust import adjust_recommendation

router = APIRouter()


@router.post("/api/chat")
def chat(req: ChatRequest):
    """核心接口：快速通道 + Agent 主循环 + 对话记忆落库。

    返回 {answer, songs, stats, session_id}：
    - answer：电台式回答（快速通道模板 / Agent 文案 / 降级文案）；
    - songs：正式歌单（来自 recommend_playlist 工具结果，已校验）或空；
    - stats：Agent 过程统计（iterations/tool_calls/tokens/耗时/degraded）；
    - session_id：本会话 ID（客户端保存，下次请求带回恢复上下文）。
    """
    if req.memory_owner_id and req.session_id:
        from app.scene_memory import bind_owner
        try:
            bind_owner(req.session_id, req.memory_owner_id)
        except ValueError as error:
            raise HTTPException(409, str(error)) from error
    with preference_memory.dj_length_scope(req.dj_length), dj_program.queue_binding_scope(req.bind_dj_queue):
        if req.current_batch_id:
            return chat_service.respond(req.message, req.session_id, req.current_song_id,
                                        current_batch_id=req.current_batch_id,
                                        current_queue_song_ids=req.current_queue_song_ids)
        return chat_service.respond(req.message, req.session_id, req.current_song_id)


@router.post("/api/chat/clear")
def chat_clear(req: ClearRequest):
    """清空指定会话的全部历史（消息 + 摘要缓存），幂等。"""
    conversation.clear_session(req.session_id)
    listening.clear_session_history(req.session_id, db_path=conversation.DB_PATH)
    dj_program.clear_programs(req.session_id, conversation.DB_PATH)
    return {"ok": True, "session_id": req.session_id}


@router.post("/api/recommendations/adjust")
def adjust_round(req: AdjustmentRequest):
    try:
        songs, intent, message = adjust_recommendation(
            req.session_id, req.batch_id, req.operation, req.song_id, conversation.DB_PATH)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    with preference_memory.dj_length_scope(req.dj_length), dj_program.queue_binding_scope(req.bind_dj_queue):
        return _finish_chat_response(req.session_id, message,
            "已按这一轮的条件调整；这次选择不写入长期偏好。", songs,
            {"channel": "adjustment", "operation": req.operation, "_recommendation_intent": intent})


@router.get("/api/recommendations/{batch_id}/dj")
def round_program(batch_id: str, session_id: str):
    result = dj_program.read_program(batch_id, session_id, conversation.DB_PATH)
    if not result:
        raise HTTPException(404, "这轮 DJ 节目已不存在")
    # 进程中断遗留 pending 的任务按需重启，而不是永久显示查询中。
    if result["status"] == "pending":
        dj_program.schedule_program(batch_id, session_id, conversation.DB_PATH)
    return result


@router.post("/api/recommendations/{batch_id}/dj/queue")
def bind_round_queue(batch_id: str, req: DJQueueRequest):
    try:
        dj_program.bind_playback_order(batch_id, req.session_id, req.song_ids, conversation.DB_PATH)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return round_program(batch_id, req.session_id)


@router.post("/api/auto-queue")
def auto_queue(req: AutoQueueRequest):
    """队列耗尽后生成一段声学衔接；不向聊天历史塞入伪对话。"""
    with preference_memory.dj_length_scope(req.dj_length), dj_program.queue_binding_scope(req.bind_dj_queue, allow_subset=True):
        return chat_service.continue_queue(req.song_id, req.session_id, req.count)
