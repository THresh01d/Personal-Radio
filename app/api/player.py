"""player HTTP适配；应用保护由统一装配入口注册。"""

from fastapi import APIRouter, HTTPException
from fastapi import Query
from fastapi.responses import Response, JSONResponse
from app import audio_features, conversation, library, listening, tts
from app.diagnostics import note
from app.safe_logging import log_failure
from app.api.schemas import ListeningEventRequest, AudioFeaturesRequest, PendingAudioFeaturesRequest

router = APIRouter()


@router.get("/api/tts/status")
def tts_status():
    """打开设置只读版本与状态，不隐式发起语音合成。"""
    return JSONResponse(tts.health.snapshot(), headers={"Cache-Control": "no-store"})


@router.post("/api/tts/check")
async def check_tts():
    """固定测试句的显式连接检测，沿用本机同源保护与合成并发上限。"""
    try:
        result = await tts.check_online()
    except (tts.TTSBusy, tts.TTSCheckLimited) as exc:
        raise HTTPException(429, "语音检测繁忙或过于频繁，请稍后再试。",
                            headers={"Retry-After": str(getattr(exc, "retry_after", 2))}) from exc
    except tts.TTSUnavailable as exc:
        note(exc.code)
        raise HTTPException(503, str(exc), headers={"Retry-After": str(exc.retry_after)}) from exc
    if result["last_check"] and not result["last_check"]["ok"]:
        note(result["last_check"]["code"])
    return JSONResponse(result, headers={"Cache-Control": "no-store"})


@router.post("/api/listening-events")
def listening_event(req: ListeningEventRequest):
    """记录播放器事实，不在写入时猜测“喜欢/不喜欢”。"""
    if req.position_seconds < 0 or (
        req.duration_seconds is not None and req.duration_seconds < 0
    ):
        raise HTTPException(422, "播放位置和时长不能为负数")
    listening.record_listening_event(
        req.session_id,
        req.song_id,
        req.event_type,
        event_id=req.event_id,
        batch_id=req.batch_id,
        position_seconds=req.position_seconds,
        duration_seconds=req.duration_seconds,
        context=req.context.model_dump() if req.context else None,
        db_path=conversation.DB_PATH,
    )
    return {"ok": True, "event_id": req.event_id}


@router.post("/api/audio-features")
def save_audio_features(req: AudioFeaturesRequest):
    """持久化真实播放摘要；短样本与非曲库歌曲明确拒绝。"""
    try:
        saved = audio_features.record_audio_features(
            req.song_id, req.model_dump(exclude={"song_id"}), db_path=library.DB_PATH
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {"ok": True, "song_id": req.song_id, "features": saved}


@router.post("/api/audio-features/pending")
def pending_audio_features(req: PendingAudioFeaturesRequest):
    """供空闲补齐器查询队列缺口，只返回曲库内未分析歌曲。"""
    return {
        "song_ids": audio_features.pending_song_ids(
            req.song_ids[:100], db_path=library.DB_PATH
        )
    }


@router.get("/api/tts")
async def api_tts(
    text: str = Query(..., max_length=tts.MAX_TEXT_LEN, description="要播报的文本"),
    voice: str = Query(tts.VOICE, max_length=32, description="DJ 音色"),
):
    """电台语音播报：文本 -> mp3 字节流。

    试听与自动报幕共用此入口。语音失败走播放器原有降级路径。
    """
    if voice not in tts.DJ_VOICES:
        raise HTTPException(422, "不支持这个 DJ 音色")
    try:
        audio = await tts.synthesize(text, voice)
    except tts.TTSUnavailable as exc:
        note(exc.code)
        raise HTTPException(503, "在线语音暂时避让中，将尝试本机语音。",
                            headers={"Retry-After": str(exc.retry_after)}) from exc
    except tts.TTSBusy as exc:
        note("tts_busy")
        raise HTTPException(429, "语音服务繁忙，先直接放歌啦", headers={"Retry-After": "2"}) from exc
    except TimeoutError as exc:
        note("tts_timeout")
        log_failure("tts_timeout", exc)
        raise HTTPException(504, "语音合成超时，先直接放歌啦") from exc
    except Exception as e:
        note(tts.failure_code(e))
        log_failure("tts_failed", e)
        raise HTTPException(502, "语音合成暂时不可用，先直接放歌啦")
    return Response(content=audio, media_type="audio/mpeg")
