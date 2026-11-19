"""app/song_knowledge.py - 歌曲知识：网易云评论 + 歌词按需提炼（原文不落库）

设计要点（对应 spec 5.5.3）：
1. 数据源：网易云热门评论 + 歌词，走 app.ncm 的 node 网关（复用登录态）；
2. 按需抓取 + 永久缓存：只抓『进推荐歌单/被讨论的歌』，不预抓全库（5048 首成本不可控）；
3. LLM 只做提炼/转述：输入只有抓回来的真实原文，提炼结果入缓存，杜绝编造空间；
4. 歌词红线：临时抓一次原文喂 LLM 提炼主题，**原文不落库、用完即弃**——
   song_knowledge 表只有 from_comments/from_lyrics 两个提炼结果字段，无原文列；
5. 零编造兜底：抓取/提炼任一环节失败 → 返回 None，上层回退标签介绍。

fetcher/summarizer 可注入：单元测试不碰网络/模型；生产走默认实现。
"""

import json
import sqlite3
import time
from pathlib import Path

from app.paths import DATA_ROOT
from app.llm_provider import request_chat

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = DATA_ROOT / "knowledge.db"
SUMMARIZE_TIMEOUT = 15  # 提炼调用短超时，别拖慢推荐响应

_SCHEMA = """
CREATE TABLE IF NOT EXISTS song_knowledge (
    song_id TEXT PRIMARY KEY,
    from_comments TEXT,
    from_lyrics TEXT,
    fetched_at TEXT
)
"""


def _now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _init_db(db_path):
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(_SCHEMA)
        conn.commit()
    finally:
        conn.close()


def _read_cache(db_path, song_id):
    """读缓存；无缓存或全空返回 None。"""
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT from_comments, from_lyrics FROM song_knowledge WHERE song_id=?",
            (song_id,),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return None
    notes = {}
    if row[0]:
        notes["from_comments"] = row[0]
    if row[1]:
        notes["from_lyrics"] = row[1]
    return notes or None


def _write_cache(db_path, song_id, notes):
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "INSERT OR REPLACE INTO song_knowledge (song_id, from_comments, from_lyrics, fetched_at)"
            " VALUES (?,?,?,?)",
            (song_id, notes.get("from_comments"), notes.get("from_lyrics"), _now()),
        )
        conn.commit()
    finally:
        conn.close()


def _fetch_raw(song_id, fetcher=None):
    """抓评论（top5）与歌词原文。任何异常都吞掉返回空（零编造红线）。

    先探测网关健康（2s），不健康直接返回空——避免首次推荐被网关拉起卡住 30s。
    """
    if fetcher is not None:
        try:
            return fetcher(song_id)
        except Exception:
            return [], ""
    from app import ncm

    comments, lyric = [], ""
    if not ncm._gateway_healthy():
        return [], ""
    try:
        data = ncm._gw("/comment/music", params={"id": song_id, "limit": 20}, timeout=10)
        comments = [c.get("content", "") for c in ((data.get("data") or {}).get("comments") or [])][:5]
    except Exception:
        comments = []
    try:
        lyric_data = ncm._gw("/lyric", params={"id": song_id}, timeout=10)
        lyric = ((lyric_data.get("lrc") or {}).get("lyric") or "")[:3000]
    except Exception:
        lyric = ""
    return comments, lyric


def _summarize(kind, text, summarizer=None):
    """对评论或歌词原文做 LLM 提炼；失败返回 None（不抛）。"""
    if summarizer is not None:
        try:
            return summarizer(kind, text)
        except Exception:
            return None
    if kind == "comments":
        system = (
            "你是歌单整理助手。下面是某首歌的网易云热门评论（用户生成内容）。"
            "提炼 2-3 条『这首歌被人记住的点』，只转述、不加戏、不编造。"
            "输出严格 JSON：{\"summary\": \"评论区都说：……\"}"
        )
    else:
        system = (
            "你是歌曲理解助手。下面是某首歌的歌词原文。"
            "提炼一句话的主题/记忆点/情绪走向（基于歌词，不编造）。"
            "输出严格 JSON：{\"summary\": \"主题：……\"}"
        )
    try:
        data = request_chat(
            [
            {"role": "system", "content": system},
            {"role": "user", "content": text[:2000]},
            ],
            response_format={"type": "json_object"},
            temperature=0.3,
            max_tokens=200,
            timeout=SUMMARIZE_TIMEOUT,
        )
        content = data["choices"][0]["message"]["content"]
        summary = json.loads(content).get("summary", "").strip()
        return summary or None
    except Exception:
        return None


def get_song_knowledge(song_id, db_path=None, fetcher=None, summarizer=None):
    """取一首歌的知识；缓存命中直接返回，否则抓取+提炼+缓存。

    返回 {"from_comments": str, "from_lyrics": str}（字段可缺省）；
    全部失败返回 None（上层回退标签介绍，绝不假装懂）。
    """
    db_path = db_path or DB_PATH
    _init_db(db_path)
    cached = _read_cache(db_path, song_id)
    if cached:
        return cached

    comments, lyric = _fetch_raw(song_id, fetcher)
    notes = {}
    if comments:
        s = _summarize("comments", "\n".join(comments), summarizer)
        if s:
            notes["from_comments"] = s
    if lyric:
        s = _summarize("lyrics", lyric, summarizer)
        if s:
            notes["from_lyrics"] = s
    if not notes:
        return None
    _write_cache(db_path, song_id, notes)
    return notes
