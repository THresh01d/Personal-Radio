"""library HTTP适配；应用保护由统一装配入口注册。"""

from fastapi import APIRouter, HTTPException
from typing import Literal
from fastapi import Query
from app import library, manual_library
from app import library_removal
from app.api.schemas import LibraryRemovalTarget, LibraryRemovalRequest
from contextlib import contextmanager
from pathlib import Path
import sqlite3
from fastapi.responses import JSONResponse

router = APIRouter()


@contextmanager
def _removal_connection(write=False):
    conn = None
    try:
        conn = sqlite3.connect(Path(library.DB_PATH).resolve().as_uri() + "?mode=rw", uri=True, timeout=1)
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("BEGIN IMMEDIATE" if write else "BEGIN")
        yield conn
        if write:
            conn.commit()
    except library_removal.RemovalConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except sqlite3.Error as exc:
        raise HTTPException(503, "曲库暂时忙碌，请稍后重新读取状态", headers={"Retry-After": "1"}) from exc
    finally:
        if conn:
            conn.close()


@router.post("/api/library/removal-preview")
def preview_library_removal(req: LibraryRemovalTarget):
    with _removal_connection() as conn:
        data = library_removal.preview_removal(conn, req.kind, [req.target_id])
    # 保留计划ID，响应丢失时能核对同一操作并正确清理待播队列，不重复提交删除。
    return JSONResponse(data, headers={"Cache-Control": "no-store"})


@router.post("/api/library/removals")
def remove_library_item(req: LibraryRemovalRequest):
    with _removal_connection(write=True) as conn:
        result = library_removal.commit_removal(conn, req.kind, [req.target_id], req.expected_revision)
    library.invalidate_library_cache()
    return JSONResponse(result, headers={"Cache-Control": "no-store"})


@router.get("/api/library/removed")
def get_removed_library_items(q: str = Query("", max_length=200)):
    with _removal_connection() as conn:
        items = library_removal.list_removed(conn, q)
        playlist_ids = sorted(library_removal.excluded_ids(conn, "playlist"))
    return JSONResponse({"items": items, "playlist_ids": playlist_ids}, headers={"Cache-Control": "no-store"})


@router.post("/api/library/removals/allow-reimport")
def allow_library_reimport(req: LibraryRemovalTarget):
    with _removal_connection(write=True) as conn:
        library_removal.allow_reimport(conn, req.kind, req.target_id)
    library.invalidate_library_cache()
    return JSONResponse({"ok": True}, headers={"Cache-Control": "no-store"})


@router.get("/api/library/playlists")
def get_local_library_playlists():
    """浏览已同步歌单；只读本地 SQLite，不要求 AI 或网易云在线。"""
    return {"playlists": manual_library.list_local_playlists(library.DB_PATH)}


@router.get("/api/library/songs")
def get_local_library_songs(
    playlist_id: str | None = None,
    q: str = "",
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=5000),
    order: Literal["playlist", "title", "random"] = "playlist",
):
    """浏览/搜索本地歌曲；结果没有 DJ 文案，可直接组成手动播放队列。"""
    try:
        return manual_library.browse_local_songs(
            playlist_id=playlist_id,
            query=q,
            offset=offset,
            limit=limit,
            order=order,
            db_path=library.DB_PATH,
        )
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
