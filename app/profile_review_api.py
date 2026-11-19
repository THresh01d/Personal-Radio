"""仅由本机受保护页面显式操作的审核接口；不读评测正例、不调用模型。"""

from contextlib import closing
import sqlite3
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, StrictBool

from app import conversation, library, profile_reviews as reviews
from app.embedding_store import metadata_hash
from app.recommendation_state import GENRES

router = APIRouter(prefix="/api/profile-reviews")


class Proposal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    metadata_sha256: str = Field(min_length=64, max_length=64)
    scope: Literal["query_positive", "global_genre"]
    genres: list[str] | None = Field(default=None, max_length=3)
    batch_id: str | None = Field(default=None, max_length=128)
    session_id: str | None = Field(default=None, max_length=128)


class Action(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1, strict=True)
    metadata_sha256: str = Field(min_length=64, max_length=64)
    confirmed_scope: Literal["query_positive", "global_genre"] | None = None
    acknowledged: StrictBool
    session_id: str | None = Field(default=None, max_length=128)


def song_row(conn, sid):
    row = conn.execute("SELECT song_id,title,artist,album FROM songs WHERE song_id=?", (sid,)).fetchone()
    if not row:
        raise HTTPException(404, "本地曲库没有这首歌曲")
    return dict(zip(("song_id", "title", "artist", "album"), row))


def context(batch_id, session_id):
    path = conversation.DB_PATH
    if not batch_id or not session_id or not path.exists():
        return None
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as conn:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='recommendation_batches'").fetchone():
            return None
        row = conn.execute("SELECT query FROM recommendation_batches WHERE id=? AND session_id=?", (batch_id, session_id)).fetchone()
    return {"batch_id": batch_id, "query": row[0]} if row else None


@router.get("/songs/{sid}")
def read_review(sid: str, batch_id: str | None = Query(None, max_length=128), session_id: str | None = Query(None, max_length=128), offset: int = Query(0, ge=0)):
    with closing(sqlite3.connect(library.DB_PATH)) as conn:
        song = song_row(conn, sid)
        row = conn.execute("SELECT genre FROM song_profiles WHERE song_id=?", (sid,)).fetchone()
        original = library._parse_tags(row[0]) if row else []
        from app.platform_metadata import read_map, merge
        platform = read_map(conn)
        viewed = merge({"id": sid, "genre": original}, platform)
        base_genres = viewed["genre"]
        platform_info = platform.get(sid)
        items = reviews.for_song(conn, sid)
        binding = metadata_hash(song)
        for item in items:
            item["stale"] = item["payload"]["metadata_sha256"] != binding
        active = [i for i in items if i["status"] == "confirmed" and not i["stale"] and i["payload"]["scope"] == "global_genre"]
    return {"song": song, "metadata_sha256": binding, "original_genres": original,
            "platform_metadata": platform_info,
            "effective_genres": reviews.effective_genres(base_genres, song, active[0]) if len(active) == 1 else base_genres,
            "active_review_id": active[0]["review_id"] if len(active) == 1 else None,
            "conflict": len(active) > 1, "reviews": items[offset:offset + 30], "total": len(items),
            "has_more": offset + 30 < len(items), "genre_options": [g for g in GENRES if g != "纯音乐"],
            "context": context(batch_id, session_id),
            "notice": "全局校正仅影响后续标签检索；原档案与语义向量保留，不改变正在播放的队列"}


@router.post("/songs/{sid}/proposals")
def propose_review(sid: str, req: Proposal):
    with closing(sqlite3.connect(library.DB_PATH)) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        song = song_row(conn, sid)
        if metadata_hash(song) != req.metadata_sha256:
            raise HTTPException(409, "歌曲信息已变化，请重新读取再审核")
        if req.scope == "query_positive":
            if not context(req.batch_id, req.session_id):
                raise HTTPException(409, "原推荐需求不存在或已清除，不能记录这次需求正例")
        elif req.batch_id is not None or req.session_id is not None:
            raise HTTPException(422, "全局风格校正不能附带需求范围")
        if req.genres and "纯音乐" in req.genres:
            raise HTTPException(422, "此入口只校正风格，不修改纯音乐类型")
        try:
            return reviews.propose(conn, song, scope=req.scope, source="explicit_user_review",
                                   genres=req.genres, query_key=req.batch_id if req.scope == "query_positive" else None)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None


@router.post("/{review_id}/{operation}")
def act_review(review_id: str, operation: Literal["confirm", "revoke"], req: Action):
    if not req.acknowledged:
        raise HTTPException(422, "需要明确确认该审核操作")
    with closing(sqlite3.connect(library.DB_PATH)) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        item = reviews.latest(conn, review_id)
        if not item:
            raise HTTPException(404, "审核记录不存在")
        song = song_row(conn, item["payload"]["song_id"])
        if metadata_hash(song) != req.metadata_sha256:
            raise HTTPException(409, "歌曲信息已变化，请重新读取")
        if operation == "confirm" and item["payload"]["scope"] == "query_positive" and not context(item["payload"]["query_key"], req.session_id):
            raise HTTPException(409, "原需求已清除或不属于此会话，不能确认正例")
        try:
            result = reviews.transition(conn, review_id, expected_revision=req.expected_revision, action=operation,
                                        confirmed_scope=req.confirmed_scope, song=song)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None
    # 同一进程下一次推荐重新装载覆盖视图，确认/撤销均不重写原向量。
    library.invalidate_library_cache()
    return result
