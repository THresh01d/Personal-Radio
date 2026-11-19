"""受主应用本机边界保护的新观察接口；不迁移旧摘要、不启动采样或推荐。"""

from contextlib import closing, contextmanager
from pathlib import Path
import sqlite3
from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from app import audio_observations as observations, library
from app.api.schemas import ObservationCancelRequest, ObservationPrepareRequest, ObservationSubmitRequest
from app.safe_logging import log_failure

router = APIRouter(prefix="/api/audio-observations")
DATABASE_TIMEOUT = 1
NO_STORE = {"Cache-Control": "no-store"}


@contextmanager
def observation_transaction():
    """同一写锁中检查作品/容量并落包；连接失败不创建空的正式曲库。"""
    try:
        target = Path(library.DB_PATH).resolve().as_uri() + "?mode=rw"
        with closing(sqlite3.connect(target, uri=True, timeout=DATABASE_TIMEOUT)) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
    except observations.LeaseUnavailable:
        raise HTTPException(409, "声学观察租约不存在或已失效，不能重用旧样本", headers=NO_STORE) from None
    except observations.ObservationConflict:
        raise HTTPException(409, "声学观察编号或绑定与原包冲突", headers=NO_STORE) from None
    except observations.ObservationCapacity:
        raise HTTPException(429, "声学观察容量已满，暂时停止新分析保存", headers=NO_STORE) from None
    except observations.ObservationError:
        raise HTTPException(422, "声学观察格式无效", headers=NO_STORE) from None
    except LookupError:
        raise HTTPException(404, "歌曲不在当前曲库中", headers=NO_STORE) from None
    except sqlite3.Error as exc:
        # 不返回SQLite消息/文件路径；忙与其他存储故障均允许保留原编号稍后重试。
        log_failure("audio_observation_storage_failed", exc)
        raise HTTPException(503, "声学观察存储暂时不可用，请保留原编号和原包稍后重试",
                            headers={**NO_STORE, "Retry-After": "2"}) from None


@router.post("/prepare")
def prepare_observation(req: ObservationPrepareRequest):
    with observation_transaction() as conn:
        result = observations.prepare_observation(conn, req.song_id, req.measurement_mode)
    return JSONResponse(result, headers=NO_STORE)


@router.post("")
def submit_observation(req: ObservationSubmitRequest):
    with observation_transaction() as conn:
        result = observations.submit_observation(conn, req.observation_id,
                                                 req.model_dump(exclude={"observation_id"}))
    return JSONResponse(result, headers=NO_STORE)


@router.post("/cancel")
def cancel_observation(req: ObservationCancelRequest):
    with observation_transaction() as conn:
        cancelled = observations.cancel_observation(conn, req.observation_id)
    return JSONResponse({"observation_id": req.observation_id, "cancelled": cancelled}, headers=NO_STORE)
