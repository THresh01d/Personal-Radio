"""app/library.py - 本地歌库加载与检索（无 HTTP 依赖）

职责：
1. 从 SQLite 加载歌曲 + embedding 矩阵 + 档案到内存（按 db_path 缓存单例）；
2. retrieve_candidates：向量化 -> 全库点积 -> 按过滤规则逐首筛选 -> TopK。

为什么单独成一个模块而不是塞进 main.py：
- main.py 是 FastAPI 层，scripts/evaluate_topk.py（离线评测）不想 import FastAPI 与 ncm；
- 检索逻辑与 HTTP 解耦后，评测脚本与线上走同一套代码，评测结论对线上行为才有说服力；
- ncm.py 同步后要失效缓存，也从这里 import，避免 app.ncm 与 app.main 循环引用。
"""

import json
import re
import sqlite3
import threading
from collections import Counter
from contextlib import closing
from pathlib import Path

import numpy as np
from app.paths import DATA_ROOT
from app.diagnostics import timed

from app.audio_features import attach_audio_features
from app.recommendation_state import parse_intent
from app.retrieval import Filters, matches_filters, artist_identity_keys, artist_identity_names
from app.ranking import rank_candidates, tag_match_details
from app.profile_reviews import override_map, decorate
from app.platform_metadata import read_map as platform_map, merge as platform_merge
from app.platform_metadata import style_details_for_song
from app.library_removal import removal_revision
from app.style_request import match_style

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = DATA_ROOT / "library.db"

DEFAULT_TOP_K = 30  # 检索策略：5000 首里取 Top30 喂给 LLM，评测脚本会做 10/20/30/50 对比

# 内存缓存：首次请求加载后常驻（约 10MB），5000 首矩阵乘法毫秒级
_cache_lock = threading.RLock()
_cached_db_path = None
_embedding_matrix = None  # (N, dim) float32 归一化矩阵
_song_list = None         # [{song_id,title,artist,album,profile}, ...] 与矩阵行一一对应
_cached_removal_revision = None


def _current_removal_revision(db_path):
    try:
        with closing(sqlite3.connect(Path(db_path).resolve().as_uri() + "?mode=ro", uri=True)) as conn:
            return removal_revision(conn)
    except sqlite3.OperationalError:
        return ""  # 兼容未含library_meta的老测试/只读小曲库，不创建空库。


def _parse_tags(s):
    """把 song_profiles 里的 JSON 数组字符串解析成 Python 列表。

    防御：字段为空或脏数据时返回空列表，不让坏数据影响检索与展示。
    """
    if not s:
        return []
    try:
        v = json.loads(s)
        return v if isinstance(v, list) else []
    except (ValueError, TypeError):
        return [t for t in s.split(",") if t.strip()]


def _load_songs_and_matrix(db_path):
    """从 SQLite 读歌曲、向量、档案，返回 (song_list, matrix)。不做缓存。

    为什么维数从数据推断而不是写死 512：建库脚本用的 bge-small 是 512 维，
    但这里不依赖具体模型，数据是几维就建几维，测试和未来换模型都更稳。
    """
    conn = sqlite3.connect(db_path)
    cur = conn.execute("SELECT song_id, title, artist, album FROM songs")
    songs = [
        {"song_id": r[0], "title": r[1], "artist": r[2], "album": r[3]}
        for r in cur.fetchall()
    ]
    cur = conn.execute("SELECT song_id, embedding FROM song_embeddings")
    emb_rows = cur.fetchall()
    # 加载档案；表可能还不存在，捕获异常后按「无档案」处理
    profile_map = {}
    try:
        cur = conn.execute(
            "SELECT song_id, genre, mood, scene, is_instrumental, language"
            " FROM song_profiles"
        )
        for sid, genre, mood, scene, inst, lang in cur.fetchall():
            profile_map[sid] = {
                "genre": _parse_tags(genre),
                "mood": _parse_tags(mood),
                "scene": _parse_tags(scene),
                "is_instrumental": bool(inst),
                "language": lang,
            }
    except sqlite3.OperationalError:
        pass  # song_profiles 还没建，全部视为无档案
    platform = platform_map(conn)
    overrides = override_map(conn)
    conn.close()

    # 按 song_id 对齐两表，防止顺序不一致导致错位
    id_to_idx = {s["song_id"]: i for i, s in enumerate(songs)}
    dim = len(np.frombuffer(emb_rows[0][1], dtype=np.float32)) if emb_rows else 512
    matrix = np.zeros((len(songs), dim), dtype=np.float32)
    for sid, blob in emb_rows:
        idx = id_to_idx.get(sid)
        if idx is None:  # 防御：向量表多出来的脏数据直接跳过
            continue
        matrix[idx] = np.frombuffer(blob, dtype=np.float32)

    # 每首歌挂上自己的档案（没有则为 None），检索/格式化/展示都要用
    for s in songs:
        original = profile_map.get(s["song_id"])
        viewed = platform_merge({"id": s["song_id"], **(original or {})}, platform)
        s["profile"] = {**{"genre": [], "mood": [], "scene": [], "is_instrumental": False, "language": None}, **viewed} if original or "platform_metadata" in viewed else None
        s["genre_review"] = {k: viewed[k] for k in ("genre_original", "genre_source", "platform_metadata") if k in viewed}
        if s["song_id"] in overrides:
            viewed = decorate({"id": s["song_id"], "genre": (s["profile"] or {}).get("genre", [])}, overrides)
            s["profile"] = {**(s["profile"] or {"mood": [], "scene": [], "is_instrumental": False, "language": None}), "genre": viewed["genre"]}
            s["genre_review"] = {**s.get("genre_review", {}), **{k: v for k, v in viewed.items() if k not in {"id", "genre"}}}
    return songs, matrix


@timed("library_load")
def load_library(db_path=None):
    """把 SQLite 全量加载进内存缓存，只做一次（按 db_path 区分，双检锁）。"""
    global _embedding_matrix, _song_list, _cached_db_path, _cached_removal_revision
    # 默认路径必须在调用时解析，测试和多用户环境才能安全切换曲库。
    db_path = db_path or DB_PATH
    revision = _current_removal_revision(db_path)
    if _embedding_matrix is not None and _cached_db_path == db_path and _cached_removal_revision == revision:
        return
    with _cache_lock:
        if _embedding_matrix is not None and _cached_db_path == db_path and _cached_removal_revision == revision:
            return
        songs, matrix = _load_songs_and_matrix(db_path)
        _song_list = songs
        _embedding_matrix = matrix
        _cached_db_path = db_path
        _cached_removal_revision = revision


def invalidate_library_cache():
    """让内存检索缓存失效，下次检索自动重载。

    什么时候用：ncm.sync 一键同步给本地歌库加了新歌，或者重跑了建档脚本后。
    缓存清空即可，锁保证并发下不会读到半截数据。
    """
    global _embedding_matrix, _song_list, _cached_db_path, _cached_removal_revision
    with _cache_lock:
        _embedding_matrix = None
        _song_list = None
        _cached_db_path = None
        _cached_removal_revision = None


def _split_names(s):
    """把 artist/aliases 里的多歌手/别名拆成独立名字（精确匹配用）。

    为什么用分隔符拆分而不是子串：歌手表里「甲 / 乙」是真实存在的多歌手格式，
    拆分后做整 token 相等匹配，既命中多歌手，又不会把「周杰」误匹配成「周杰伦」。
    """
    if not s:
        return []
    return [p.strip() for p in re.split(r"[/、,，&and\s]+", s) if p.strip()]


_ARTIST_GROUP_SUFFIXES = ("乐团", "乐队", "组合")


def _artist_identity_forms(name):
    """生成歌手名的安全等价形式，只处理明确的团体后缀。

    用户常把 ``ChiliChill乐团`` 简称为 ``chilichill``。这里仍坚持完整名称匹配，
    仅允许省略「乐团/乐队/组合」这几个身份后缀；绝不做任意子串匹配，因此
    ``周杰`` 仍不会误命中 ``周杰伦``。
    """
    normalized = (name or "").strip().casefold()
    if not normalized:
        return set()
    forms = {normalized}
    for suffix in _ARTIST_GROUP_SUFFIXES:
        suffix = suffix.casefold()
        if normalized.endswith(suffix):
            short = normalized[:-len(suffix)].strip()
            if short:
                forms.add(short)
    return forms


def _load_candidate_rows(db_path):
    """读取 songs + 档案，组装成候选 schema 列表（查找路径专用）。

    与 _load_songs_and_matrix 的区别：这里不需要 embedding 矩阵，但需要
    aliases 列（歌手/别名查找用）；song_profiles 表缺失时按「无档案」处理。
    """
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT s.song_id, s.title, s.artist, s.album, s.aliases,"
            " p.genre, p.mood, p.scene, p.is_instrumental, p.language"
            " FROM songs s LEFT JOIN song_profiles p ON s.song_id = p.song_id"
        ).fetchall()
    except sqlite3.OperationalError:
        # 档案表还没建：全部按无档案处理
        rows = conn.execute(
            "SELECT song_id, title, artist, album, aliases FROM songs"
        ).fetchall()
        rows = [r + (None, None, None, None, None) for r in rows]
    platform = platform_map(conn)
    overrides = override_map(conn)
    conn.close()

    songs = []
    for r in rows:
        sid, title, artist, album, aliases, genre, mood, scene, inst, lang = r
        songs.append(
            {
                "id": sid,
                "title": title,
                "artist": artist,
                "album": album,
                "aliases": aliases,
                "score": None,  # 查找路径不关心相似度
                "genre": _parse_tags(genre),
                "mood": _parse_tags(mood),
                "scene": _parse_tags(scene),
                "is_instrumental": bool(inst),
                "language": lang,
            }
        )
    return [decorate(platform_merge(s, platform), overrides) for s in songs]


def find_artist_hits(name, db_path=None):
    """在歌库中精确/别名查找歌手，返回命中的歌曲列表（候选 schema）。

    匹配口径（忽略大小写的精确匹配，不做 LIKE 子串）：
    - artist 整段相等；
    - artist 按分隔符拆分后的任一 token 相等（「甲 / 乙」多歌手）；
    - aliases 整段相等或其拆分 token 相等。

    为什么必须精确：阶段 5 的「诚实拒绝」依赖这个函数判断 0 命中。
    子串匹配会把「周杰」误命中「周杰伦」，把「河南说唱之神」误命中
    含「说唱」的无关歌曲，让拒绝形同虚设。0 命中返回 []，调用方据此
    直接告诉用户歌库没有 TA 的作品，绝不进入普通 RAG 硬凑。
    """
    db_path = db_path or DB_PATH
    name = (name or "").strip()
    if not name:
        return []
    requested_forms = _artist_identity_forms(name)
    hits = []
    for s in _load_candidate_rows(db_path):
        tokens = _split_names(s["artist"]) + _split_names(s.get("aliases"))
        # 英文艺人名的大小写属于展示差异，不应改变身份：rapeter 必须命中
        # Rapeter。仍然只比较完整 artist/token，绝不退化成子串或模糊搜索，
        # 因此「周杰」不会误命中「周杰伦」。casefold 比 lower 对 Unicode 更稳。
        candidate_forms = _artist_identity_forms(s["artist"])
        for token in tokens:
            candidate_forms.update(_artist_identity_forms(token))
        if requested_forms.intersection(candidate_forms):
            hits.append(s)
    return hits


def find_songs_by_title(title, db_path=None):
    """按歌名查找歌曲，返回候选 schema 列表（讨论请求用）。

    匹配优先级：完全相等 → 忽略大小写相等 → 歌名包含关键词（子串兜底）。
    「Trauma」「晴天」这类精确歌名命中靠前两级；只记得大概时用子串兜底。
    找不到返回 []，调用方诚实说明歌库里没有这首歌，不推荐无关歌曲。
    """
    db_path = db_path or DB_PATH
    title = (title or "").strip()
    if not title:
        return []
    rows = _load_candidate_rows(db_path)
    exact = [s for s in rows if s["title"] == title]
    if not exact:
        exact = [s for s in rows if s["title"].lower() == title.lower()]
    if exact:
        return exact
    low = title.lower()
    return [s for s in rows if low in s["title"].lower()]


def find_song_by_id(song_id, db_path=None):
    """按 song_id 精确查询单曲，返回候选 schema 或 None（MCP 资源 song://{id} 用）。

    为什么独立函数而不是复用 find_songs_by_title：id 是主键、唯一确定一首，
    与歌名查找（可能多首命中）语义不同；且这里直接按主键取一行，不必全表扫描。
    档案表缺失时按「无档案」处理，与 _load_candidate_rows 口径一致。
    """
    db_path = db_path or DB_PATH
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT s.song_id, s.title, s.artist, s.album, s.aliases,"
            " p.genre, p.mood, p.scene, p.is_instrumental, p.language"
            " FROM songs s LEFT JOIN song_profiles p ON s.song_id = p.song_id"
            " WHERE s.song_id = ?",
            (song_id,),
        ).fetchone()
    except sqlite3.OperationalError:
        # 档案表还没建：只查 songs 表
        row = conn.execute(
            "SELECT song_id, title, artist, album, aliases FROM songs WHERE song_id = ?",
            (song_id,),
        ).fetchone()
        if row:
            row = row + (None, None, None, None, None)
    platform = platform_map(conn)
    overrides = override_map(conn)
    conn.close()
    if not row:
        return None
    sid, title, artist, album, aliases, genre, mood, scene, inst, lang = row
    return decorate(platform_merge({
        "id": sid,
        "title": title,
        "artist": artist,
        "album": album,
        "aliases": aliases,
        "score": None,  # 单曲查询不看相似度
        "genre": _parse_tags(genre),
        "mood": _parse_tags(mood),
        "scene": _parse_tags(scene),
        "is_instrumental": bool(inst),
        "language": lang,
    }, platform), overrides)


def find_songs_by_ids(song_ids, db_path=None):
    """批量补齐画像面板所需的真实歌曲元数据，返回 ``id -> song``。

    画像可能同时展示多首明确反馈和行为推断歌曲。逐首调用
    ``find_song_by_id`` 会重复打开 SQLite；这里一次读取歌库后按 id 过滤，既保持
    元数据口径一致，也避免 HTTP 请求产生 N 次数据库连接。找不到的 id 不伪造。
    """
    db_path = db_path or DB_PATH
    wanted = {str(song_id) for song_id in song_ids if song_id is not None}
    if not wanted:
        return {}
    return {
        song["id"]: song
        for song in _load_candidate_rows(db_path)
        if str(song["id"]) in wanted
    }


def find_eligible_songs(filters, *, exclude_ids=(), exclude_artists=(), db_path=None):
    """给声学召回提供当前有效视图的合格作品，不依赖文本窗口或重算向量。

    平台百科和人工校正必须先合并，再执行与文本召回相同的硬条件；
    软风格、情绪和场景仅参与后续排序，不能借白名单变成新的硬过滤。
    """
    blocked_ids = {str(value) for value in exclude_ids}
    blocked_artists = {name for credit in exclude_artists for name in artist_identity_keys(credit)}
    return {
        str(song["id"]): song
        for song in _load_candidate_rows(db_path or DB_PATH)
        if str(song["id"]) not in blocked_ids
        and not artist_identity_keys(song.get("artist")).intersection(blocked_artists)
        and matches_filters(song, filters)
    }


def _to_candidate(s, score):
    """把内存库里的歌曲行 + 相似度组装成候选 schema（检索链路的统一出口）。"""
    prof = s.get("profile")
    return {
        "id": s["song_id"],
        **s.get("genre_review", {}),
        "title": s["title"],
        "artist": s["artist"],
        "album": s["album"],
        "score": float(score),
        "genre": (prof or {}).get("genre", []),
        "mood": (prof or {}).get("mood", []),
        "scene": (prof or {}).get("scene", []),
        "is_instrumental": bool(prof and prof["is_instrumental"]),
        "language": (prof or {}).get("language"),
        **{k: (prof or {}).get(k) for k in ("language_source", "language_status", "language_inferred")},
    }


def _embed_query(query, embed_fn):
    """编码查询并做 L2 归一化，保证与库内向量同一尺度（点积即余弦相似度）。"""
    vec = embed_fn([query])[0]
    norm = np.linalg.norm(vec)
    if norm > 0:
        vec = vec / norm
    return vec


def reference_style_hint(reference_song_id, db_path=None):
    """单曲人工/平台来源可提供软线索，旧向量仍独立遵守原跨作品门槛。"""
    db_path = db_path or DB_PATH
    with _cache_lock:
        load_library(db_path)
        songs = _song_list
    reference = next((s for s in songs if str(s['song_id']) == str(reference_song_id)), None)
    if reference is None:
        return {'genres': [], 'source': None, 'semantic_genres': [], 'profiled_others': 0, 'support': {}}
    lead = artist_identity_names(reference.get('artist'))[0]
    own_genres = (reference.get('profile') or {}).get('genre') or []
    counts = Counter()
    own_title = " ".join((reference.get('title') or '').split()).casefold()
    by_title = {}
    if lead:
        for song in songs:
            genres = (song.get('profile') or {}).get('genre') or []
            title_key = " ".join((song.get('title') or '').split()).casefold()
            if (str(song['song_id']) == str(reference_song_id) or not genres
                    or not title_key or title_key == own_title
                    or artist_identity_names(song.get('artist'))[0] != lead):
                continue
            tags = {str(g).casefold() for g in genres}
            # 同名版本只算一个支持来源；版本标签冲突时仅保留共同项，不依赖行顺序。
            by_title[title_key] = by_title[title_key].intersection(tags) if title_key in by_title else tags
    profiled = len(by_title)
    for tags in by_title.values():
        counts.update(tags)
    # 至少两首其他作品、严格多数，只是保守的标签一致性门槛，不是校准置信度。
    supported = [g for g in own_genres if profiled >= 2 and counts[str(g).casefold()] * 2 > profiled]
    source = (reference.get('genre_review') or {}).get('genre_source')
    direct = source in {'user_confirmed', 'netease_wiki'}
    # 来源标记只取已通过作品绑定/唯一确认校验的有效视图，不从旧档案 source 猜人工授权。
    # 平台/人工标签可独立使用，不意味着保留旧标签的文本向量也获得了新的可靠性。
    genres = own_genres if direct else supported
    details = style_details_for_song({'genre': genres, 'genre_source': source,
        'platform_metadata': (reference.get('genre_review') or {}).get('platform_metadata')})
    return {'genres': list(dict.fromkeys(genres)),
            **({'style_details': list(details)} if details else {}),
            'source': source if direct else ('profile_inference' if own_genres else None),
            'semantic_genres': list(dict.fromkeys(supported)), 'profiled_others': profiled,
            'support': {g: counts[str(g).casefold()] for g in own_genres}}


def reference_embedding_scores(reference_song_id, song_ids, db_path=None):
    """补齐声学独有邻曲的实际参考文本分；没有有效向量时保持未知、不重新编码。"""
    db_path = db_path or DB_PATH
    wanted = {str(sid) for sid in song_ids}
    if not wanted:
        return {}
    with _cache_lock:
        load_library(db_path)
        songs, matrix = _song_list, _embedding_matrix
    index = next((i for i,s in enumerate(songs) if str(s['song_id'])==str(reference_song_id)), None)
    if index is None:
        return {}
    reference = matrix[index]
    norm = np.linalg.norm(reference)
    if not np.isfinite(norm) or norm <= 0 or not np.isfinite(reference).all():
        return {}
    result = {}
    for i,song in enumerate(songs):
        sid = str(song['song_id'])
        if sid not in wanted:
            continue
        vector = matrix[i]
        candidate_norm = np.linalg.norm(vector)
        if np.isfinite(vector).all() and np.isfinite(candidate_norm) and candidate_norm > 0:
            result[sid] = float(vector @ (reference / norm))
    return result


@timed("retrieval")
def retrieve_candidates_by_filters(query, filters, top_k=DEFAULT_TOP_K, db_path=None, embed_fn=None, trace=None,
                                   *, return_pool=False, exclude_ids=(), exclude_artists=(), reference_song_id=None,
                                   context_query=None, music_direction=None):
    """向量化 query，与全库点积，按显式 Filters 硬过滤后返回 TopK 候选（含标签）。

    过滤条件由调用方显式给出（Filters 对象），而不是从消息文本解析。
    Agent 工具（LLM 结构化参数）走这里；
    普通推荐/评测仍走 retrieve_candidates。二者共享同一套点积 + 过滤逻辑，
    保证『语义理解』和『规则解析』两条路对歌曲的判定完全一致。

    正式歌单用 return_pool 保留有界召回并集，等会话偏好、近期记录和歌手配额
    生效后再选歌；search_library 等旧调用仍返回 TopK。临时硬排除提前执行，
    避免“换些歌手”时旧歌手占满召回窗口，窗口后的合格作品却没有机会。
    """
    db_path = db_path or DB_PATH
    with _cache_lock:
        load_library(db_path)
        # 移除接口可能同时使全局缓存失效；本次计算持有不可变旧快照，避免途中读到None。
        song_list, embedding_matrix = _song_list, _embedding_matrix
    query_vec = None
    if reference_song_id:
        reference_index = next((i for i, song in enumerate(song_list)
                                if str(song["song_id"]) == str(reference_song_id)), None)
        if reference_index is not None:
            reference_vec = embedding_matrix[reference_index]
            norm = np.linalg.norm(reference_vec)
            if np.isfinite(norm) and norm > 0 and np.isfinite(reference_vec).all():
                # 参考明确指定的作品档案，不重新编码“类似这首”及歌名拼句；
                # 此向量仍是文本表示，不能据此宣称声音相似或补造缺失的标签。
                query_vec = reference_vec / norm
    semantic_source = "reference_embedding" if query_vec is not None else "query_embedding"
    if query_vec is None:
        if embed_fn is None:
            from app.embedding import embed_texts  # 延迟导入：单元测试不想加载模型

            embed_fn = embed_texts
        query_vec = _embed_query(query, embed_fn)
    scores = embedding_matrix @ query_vec  # (N,)
    order = np.argsort(-scores)  # 相似度降序
    blocked_ids = {str(value) for value in exclude_ids}
    blocked_artists = set(exclude_artists)
    blocked_artist_keys = {name for credit in blocked_artists for name in artist_identity_keys(credit)}
    eligible = []
    for idx in order:
        song = _to_candidate(song_list[idx], scores[idx])
        song["semantic_source"] = semantic_source
        if (str(song["id"]) not in blocked_ids
                and not artist_identity_keys(song.get("artist")).intersection(blocked_artist_keys)
                and matches_filters(song, filters)):
            eligible.append(song)

    # 多路召回 = 语义高分池 ∪ 标签命中池。标签池从全部硬约束合格歌曲中产生，
    # 因此一首语义分不在前 30、但真实标签高度匹配的歌仍有机会进入最终候选。
    pool_size = max(top_k * 2, 50)
    semantic_pool = eligible[:pool_size]
    tagged = [
        song for song in eligible
        if tag_match_details(song, filters)[0] > 0
    ]
    tagged.sort(
        key=lambda song: (tag_match_details(song, filters)[0], float(song.get("score") or 0.0)),
        reverse=True,
    )
    tag_pool = tagged[:pool_size]
    pools = [('semantic', semantic_pool), ('tag', tag_pool)]
    if filters.style_request:
        # 每一档从完整合格库存补召回，低文本相似度的明确匹配也能进入候选。
        style_pools = {}
        for song in eligible:
            evidence = match_style(song, filters.style_request)
            song['style_match'] = evidence
            group = style_pools.setdefault(evidence['tier'], [])
            if len(group) < pool_size:
                group.append(song)
        pools.extend(('style_evidence', songs) for songs in style_pools.values())
    if music_direction:
        from app.reference_direction import matches_labels
        # 方向明确时，从同一合格库存补召回，避免旧文本窗口把更合适的作品截掉。
        pools.append(('direction', [song for song in eligible if matches_labels(song, music_direction)][:pool_size]))
    context_trace = None
    if context_query and not reference_song_id and context_query.strip() != query.strip():
        # 语境只补召回，不替换方向分数，也不把两种查询的分数取最大后混算。
        # 两路均使用同一硬约束合格库存；每路有界，合并后每首只保留一次。
        context_vector = _embed_query(context_query, embed_fn)
        context_scores = embedding_matrix @ context_vector
        context_by_id = {s['song_id']: float(context_scores[i]) for i, s in enumerate(song_list)}
        context_order = sorted(eligible, key=lambda s: context_by_id[s['id']], reverse=True)
        context_tagged = sorted(tagged, key=lambda s: (
            tag_match_details(s, filters)[0], context_by_id[s['id']]), reverse=True)
        context_semantic_pool = context_order[:pool_size]
        context_tag_pool = context_tagged[:pool_size]
        pools.extend([('context_semantic', context_semantic_pool), ('context_tag', context_tag_pool)])
        if trace is not None:
            context_trace = {'query': context_query, 'raw_scores': context_by_id,
                             'semantic_pool': [s['id'] for s in context_semantic_pool],
                             'tag_pool': [s['id'] for s in context_tag_pool]}
    by_id = {}
    for route, pool in pools:
        for song in pool:
            if song['id'] in by_id:
                by_id[song['id']]['recall_routes'].append(route)
            else:
                by_id[song['id']] = {**song, 'recall_routes': [route]}
    # 声学档案会在真实播放后更新，不能放进全局 embedding 缓存；排序前现查，
    # 下一次推荐即可使用刚学到的歌曲能量特征。
    candidates = attach_audio_features(list(by_id.values()), db_path=db_path)
    ranked = rank_candidates(candidates, filters)
    if filters.style_request:
        ranked.sort(key=lambda song: song['style_match']['tier'])
    # 仅显式本机诊断使用，不持久化、不上 HTTP；正常推荐没有额外全库追踪成本。
    # 记录实际执行的各阶段，而非在脚本复制一套召回来猜哪里丢了歌曲。
    if trace is not None:
        trace.clear()
        trace.update({
            "raw_scores": {s["song_id"]: float(scores[i]) for i, s in enumerate(song_list)},
            "semantic_order": [song_list[i]["song_id"] for i in order],
            "hard_eligible": [s["id"] for s in eligible],
            "tag_order": [s["id"] for s in tagged],
            "semantic_pool": [s["id"] for s in semantic_pool],
            "tag_pool": [s["id"] for s in tag_pool],
            "ranked_union": ranked,
            "top_k": top_k,
            "semantic_source": semantic_source,
        })
        if context_trace is not None:
            trace['context_recall'] = context_trace
    return ranked if return_pool else ranked[:top_k]


def retrieve_candidates(message, top_k=DEFAULT_TOP_K, db_path=None, embed_fn=None):
    """向量化消息，与全库归一化向量做点积，按文本解析出的过滤规则返回 TopK 候选。

    embed_fn 可注入（测试传伪向量，避免加载 BGE 模型），默认走 app.embedding。
    入库时已归一化，所以 query 也归一化后，矩阵乘法结果就是余弦相似度。
    过滤（纯音乐/语种）在取 TopK 前逐首判断，保证过滤后仍能凑满 top_k 首。
    """
    # 评测、脚本和旧调用方也必须走与线上工具一致的结构化意图，不能再维护一套
    # 只识别语种/纯音乐的简化解析器，否则离线结论无法代表真实推荐链。
    intent, _ = parse_intent(message, {"query": message})
    filters = Filters(
        want_instrumental=bool(intent["instrumental"]),
        want_emo=not intent.get("style_request") and "emo" in {str(item).casefold() for item in intent["genres"]},
        lang=intent["language"],
        required_genres=tuple(intent.get("required_genres") or []),
        genres=tuple(intent["genres"]),
        moods=tuple(intent["moods"]),
        scenes=tuple(intent["scenes"]),
        exclude_genres=tuple(intent["excluded_genres"]),
        energy=intent["energy"],
        style_request=intent.get("style_request"),
    )
    return retrieve_candidates_by_filters(
        message, filters, top_k=top_k, db_path=db_path, embed_fn=embed_fn
    )
