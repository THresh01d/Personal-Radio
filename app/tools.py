"""app/tools.py - 工具注册表（每个工具 = JSON Schema + handler）

Agent 化重构的核心：LLM 不再被塞进一段固定的推荐链路，而是通过工具访问
歌库能力。本模块统一注册 4 个工具，schema 给 LLM 看（决定什么时候调、
传什么参数），handler 实际执行（复用 library / retrieval 的确定性函数）。

工具设计原则：
1. 确定性逻辑全部留在工具里：检索、防幻觉、同歌手限流、DJ 介绍兜底，
   都是可测试、可预期的「肌肉反射」；LLM 只负责理解意图、挑工具、写人话；
2. 每个工具都有明确边界：
   - search_library：检索候选，供 LLM 查看歌库有什么；
   - get_artist_songs：歌手精确查找，0 命中 = 诚实拒绝的依据；
   - get_song_profile：单曲档案，讨论请求用；
   - recommend_playlist：唯一返回正式歌单的工具，走完整校验链；
3. handler 是普通同步函数：检索/校验都是本地 SQLite + numpy，没有 IO，
   不需要 async，测试也无需事件循环。
"""

import json
import re
import time
from dataclasses import replace

from app.language_evidence import language_fields
from app.audio_features import (
    attach_audio_features,
    find_similar_features,
    get_audio_feature,
)
from app.conversation import get_recent_song_ids
from app.feedback import get_taste, song_taste_factor
from app.library import (
    find_artist_hits,
    find_eligible_songs,
    find_songs_by_ids,
    find_songs_by_title,
    retrieve_candidates_by_filters,
    reference_style_hint,
    reference_embedding_scores,
)
from app.ranking import (
    REGIONAL_POP_DETAILS, apply_taste_factors, narrow_regional_pop_reference,
    prioritize_reference_candidates, rank_candidates, reference_priority_summary,
)
from app.recommendation_state import MOODS, genre_text_signals, parse_intent, song_reference_mode
from app.style_request import families, match_style, opening_notice, selection_message
from app.retrieval import (
    Filters,
    MAX_PICKS,
    MAX_PER_ARTIST,
    MIN_PICKS,
    artist_identity_keys,
    artist_quota_capacity,
    build_dj_intro,
    build_dj_opener,
    build_evidence,
    build_known_why,
    build_song_material,   # 本任务先 import，Task 8 才调用注入
    matches_filters,
    validate_and_fill,
)
from app.scene import (SCENE_STRATEGIES, ACTIVITY_STRATEGIES, activity_from_query,
                       valid_activity_direction, activity_selection_summary, scene_from_query)
from app.song_knowledge import get_song_knowledge
from app.voice_author import author_song_intros
from app.rain_context import rain_listening_plan, prioritize_rain_direction, rain_direction_summary, rain_title_term
from app.relative_strength import (capture_baseline, baseline_ids, prepare_observed_comparison,
                                   prioritize_candidates, observed_summary)
from app.relative_observations import read_observations
from app.relative_tags import prepare_tag_comparison, prioritize_tag_candidates

# 检索数量边界（与检索层的 top_k 同口径）
DEFAULT_SEARCH_COUNT = 20
MAX_SEARCH_COUNT = 50
# recommend_playlist 拿更多候选做校验补足，但最终只回 3-8 首
DEFAULT_PLAYLIST_POOL = 30
DEFAULT_TARGET_COUNT = 3
VAGUE_MORE_TARGET_COUNT = 5

_CHINESE_DIGITS = {
    "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
    "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
}
_TARGET_COUNT_PATTERN = re.compile(
    r"(?:再来|再放|再听|继续来|继续放|继续听|想听|推荐|给我|来|放|听)"
    r"\s*(?:上)?\s*(\d{1,2}|[一二两三四五六七八九十]{1,3})\s*首"
)


def _parse_chinese_count(text):
    """解析 1-99 的简单中文数量；最终仍由 3-8 边界钳制。"""
    if text == "十":
        return 10
    if "十" in text:
        left, right = text.split("十", 1)
        tens = _CHINESE_DIGITS.get(left, 1) if left else 1
        ones = _CHINESE_DIGITS.get(right, 0) if right else 0
        return tens * 10 + ones
    return _CHINESE_DIGITS.get(text)


def resolve_target_count(query, explicit=None):
    """确定最终歌单数量：用户原话优先，其次工具参数，最后默认 3 首。

    count 继续只控制召回候选池；target_count 专门控制最终返回数量，避免过去
    “描述支持 3-8 首、生产却永远补到 3 首”的接口语义错位。
    """
    match = _TARGET_COUNT_PATTERN.search(query or "")
    if match:
        raw = match.group(1)
        requested = int(raw) if raw.isdigit() else _parse_chinese_count(raw)
    elif any(phrase in (query or "") for phrase in (
        "多来几首", "多来点", "多放几首", "多推荐几首", "来多几首",
    )):
        requested = VAGUE_MORE_TARGET_COUNT
    else:
        try:
            requested = int(explicit) if explicit is not None else DEFAULT_TARGET_COUNT
        except (TypeError, ValueError):
            requested = DEFAULT_TARGET_COUNT
    return max(MIN_PICKS, min(requested or DEFAULT_TARGET_COUNT, MAX_PICKS))


def _brief(song):
    """把候选压缩成给 LLM 看的精简字段（工具结果回填时会 JSON 序列化）。"""
    return {
        "id": song["id"],
        "title": song["title"],
        "artist": song["artist"],
        "album": song["album"],
        "genre": song.get("genre") or [],
        "mood": song.get("mood") or [],
        "scene": song.get("scene") or [],
        **language_fields(song),
        "is_instrumental": bool(song.get("is_instrumental")),
        "score": round(float(song.get("score") or 0.0), 4),
        "semantic_score": round(float(song.get("semantic_score") or song.get("score") or 0.0), 4),
        "tag_score": round(float(song.get("tag_score") or 0.0), 4),
        "taste_score": round(float(song.get("taste_score", 0.5)), 4),
        "transition_score": song.get("transition_score"),
        "freshness_score": round(float(song.get("freshness_score", 1.0)), 4),
        "acoustic_score": song.get("acoustic_score"),
        "audio_features": song.get("audio_features"),
        "final_score": round(float(song.get("final_score") or song.get("score") or 0.0), 4),
        "ranking_version": song.get("ranking_version"),
        "ranking_weights": song.get("ranking_weights") or {},
        "score_components": song.get("score_components") or {},
        "playability": song.get("playability", "unknown"),
        "score_explanation": list(song.get("score_explanation") or []),
    }


def _filters_from_args(args):
    """把 LLM 结构化参数转成检索层的 Filters（缺省 = 不约束）。"""
    instrumental = args.get("instrumental")
    scene = args.get("scene")
    scenes = tuple(scene) if isinstance(scene, (list, tuple)) else ((scene,) if scene else ())
    return Filters(
        want_instrumental=bool(instrumental),
        lang=args.get("lang") or None,
        required_genres=tuple(args.get("_required_genres") or []),
        genres=tuple(args.get("genre") or []),
        moods=tuple(args.get("mood") or []),
        scenes=scenes,
        exclude_genres=tuple(args.get("exclude") or []),
        energy=args.get("energy") or None,
    )


def _merge_scene(filters, args, query):
    """合并场景策略：显式 scene 参数优先 → 策略表按 key 命中 → query 文本兜底。

    返回 (merged_filters, strategy)。策略只在缺省过滤项时补全（不覆盖显式参数），
    strategy 供素材包取 opener；识别不出场景返回 (filters, None)。
    """
    activity_text = (query or '').replace(args.get('artist') or '\0', '')
    stored_direction = args.get('_activity')
    authoritative = valid_activity_direction(stored_direction)
    direction = stored_direction if authoritative else activity_from_query(activity_text)
    if direction:
        if not authoritative:
            from app.recommendation_state import parse_intent
            normalized, _ = parse_intent(query, args)
            filters = replace(filters, genres=tuple(normalized['genres']), moods=tuple(normalized['moods']),
                scenes=('运动',), lang=normalized['language'], want_instrumental=normalized['instrumental'])
        return replace(filters, scenes=('运动',), energy=direction['energy']), ACTIVITY_STRATEGIES[direction['mode']]
    strategy = None
    key = (args.get("scene") or "").strip()
    if key:
        for s in SCENE_STRATEGIES:
            if s.key == key:
                strategy = s
                break
    if strategy is None:
        strategy = scene_from_query(query or "")
        if strategy in ACTIVITY_STRATEGIES.values() and not activity_from_query(activity_text):
            strategy = None
    if strategy is None:
        return filters, None
    merged = Filters(
        want_instrumental=filters.want_instrumental,
        want_emo=filters.want_emo,
        lang=filters.lang,
        required_genres=filters.required_genres,
        genres=filters.genres or strategy.genres,
        moods=filters.moods or strategy.moods,
        scenes=filters.scenes or (strategy.key,),
        exclude_genres=filters.exclude_genres or strategy.exclude_genres,
        energy=filters.energy or strategy.energy,
    )
    return merged, strategy


# query 文本兜底可识别的明确风格词（多字词，避免 emo/acg 这类短词子串误伤）。
# 注意：不放「纯音乐」——它由 want_instrumental 硬条件控制，
# 「不要纯音乐」里的『纯音乐』绝不能变成 genre 过滤，否则会把结果全部过滤掉。
_QUERY_GENRE_TERMS = (
    "说唱", "流行", "民谣", "电子", "摇滚", "爵士", "灵魂乐", "另类",
    "独立", "蒸汽波", "国风", "古典", "金属", "蓝调",
    "r&b", "city pop", "lo-fi",
)


def _query_fallback_filters(query):
    """从 query 文本确定性兜底提取硬过滤条件（模型漏传参数时的肌肉反射）。

    语种和纯音乐是『用户说了就必须做到』的硬条件，不能依赖模型每次都能正确
    解析进工具参数。这里识别确定性关键词：
    - 语种：英文/英语→英文；中文/国语→中文；日文/日语→日文；
    - 纯音乐：「纯音乐/轻音乐/钢琴」→ 要纯音乐；「不要/别/不带 纯音乐」→ 排除；
    - 风格：只匹配多字明确风格词（说唱/r&b/国风…），emo/acg 等短词不做子串匹配。
    语义模糊的部分（情绪、场景、复合描述）仍交给模型，这里只兜硬条件。
    """
    q = (query or "").lower()
    lang = None
    if any(k in q for k in ("英文", "英语")):
        lang = "英文"
    elif any(k in q for k in ("中文", "国语")):
        lang = "中文"
    elif any(k in q for k in ("日文", "日语")):
        lang = "日文"

    want_inst = any(k in q for k in ("纯音乐", "轻音乐", "钢琴", "instrumental"))
    if any(k in q for k in ("不要纯音乐", "别纯音乐", "不带纯音乐", "不要纯音")):
        want_inst = False

    positive_text, excluded = genre_text_signals(query)
    genres = tuple(g for g in _QUERY_GENRE_TERMS if g in positive_text.lower())
    energy = None
    if any(k in q for k in ("高能量", "燃一点", "燃起来", "躁一点", "强节奏", "有劲")):
        energy = "high"
    elif any(k in q for k in ("低能量", "安静", "舒缓", "睡前")):
        energy = "low"
    elif any(k in q for k in ("温柔", "轻一点", "柔和")):
        energy = "medium-low"
    from app.recommendation_state import _genre_values
    required = tuple(g for g in _genre_values(query, None) if g != "纯音乐")
    return Filters(
        required_genres=required,
        want_instrumental=want_inst, lang=lang, genres=genres, energy=energy,
        exclude_genres=tuple(excluded),
    )


def _merge_query_fallbacks(filters, query, *, explicit_exclude=()):
    """显式参数优先，query 兜底补缺（两个检索工具共用）。

    保留 filters.exclude_genres（场景策略注入的排除）但协调冲突：
    用户明确要的风格优先于场景默认排除——『深夜emo说唱』的 query 兜底把『说唱』
    提进 genres，而深夜策略排除『说唱』，matches_filters 同时要求『含说唱』且
    『排除说唱』→ 结果必空。策略排除是默认偏好，用户明确表达的需求让策略让位。
    """
    fb = _query_fallback_filters(query)
    # 只有场景默认排除可以让位；用户原话/结构化参数中的硬排除不能被兜底补词抹掉。
    explicit = tuple(dict.fromkeys((*explicit_exclude, *fb.exclude_genres)))
    blocked = {g.casefold() for g in explicit}
    genres = tuple(g for g in filters.genres if g.casefold() not in blocked)
    genres = genres or tuple(g for g in fb.genres if g.casefold() not in blocked)
    requested = {g.casefold() for g in (*genres, *filters.required_genres, *fb.required_genres)}
    exclude_genres = tuple(dict.fromkeys((
        *(e for e in filters.exclude_genres if e.casefold() not in requested or e.casefold() in blocked),
        *explicit,
    )))
    return Filters(
        want_emo=filters.want_emo or "emo" in (filters.required_genres or fb.required_genres),
        want_instrumental=filters.want_instrumental or fb.want_instrumental,
        lang=filters.lang or fb.lang,
        required_genres=tuple(g for g in (filters.required_genres or fb.required_genres) if g.casefold() not in blocked),
        genres=genres,
        moods=filters.moods,
        scenes=filters.scenes,
        exclude_genres=exclude_genres,
        energy=filters.energy or fb.energy,
    )


def _playlist_filters(args, query):
    filters, strategy = _merge_scene(_filters_from_args(args), args, query)
    if "_required_genres" not in args and "_style_request" not in args:
        intent, _ = parse_intent(query, args)
        if intent.get("style_request"):
            args = {**args, "_style_request": intent["style_request"], "exclude": intent["excluded_genres"]}
            filters = replace(filters, genres=tuple(intent["genres"]), moods=tuple(intent["moods"]),
                              lang=intent["language"], want_instrumental=intent["instrumental"])
    style = args.get("_style_request")
    if style:
        # 场景的默认排除可以让位，用户明确排除始终有效。改写 query 只作语义检索。
        requested = {g.casefold() for term in style["styles"] for g in families(term)}
        requested.update(term.casefold() for term in style["styles"])
        excluded = tuple(dict.fromkeys([
            *(g for g in filters.exclude_genres if g.casefold() not in requested),
            *(args.get("exclude") or ()),
        ]))
        return replace(filters, required_genres=(), want_emo=False, style_request=style,
                       exclude_genres=excluded), strategy
    filters = _merge_query_fallbacks(filters, query, explicit_exclude=args.get("exclude") or ())
    if "_required_genres" in args:
        required = tuple(args["_required_genres"])
        filters = replace(filters, required_genres=required, want_emo="emo" in required)
    return filters, strategy


def _clamp_count(args, default):
    """读取 count 参数并钳到安全区间，防 LLM 传超大值打爆 prompt。"""
    try:
        n = int(args.get("count") or default)
    except (TypeError, ValueError):
        n = default
    return max(1, min(n, MAX_SEARCH_COUNT))


# ---------- 工具 1：search_library ----------

def _search_library_handler(args, session_id=None):
    """按语义检索候选歌曲（向量相似度 + 结构化硬过滤）。

    session_id 目前不参与检索（检索只看语义），保留签名是为了统一 handler
    接口（(args, session_id=None)），避免 execute_tool 分发时区分对待。
    """
    query = (args.get("query") or "").strip()
    if not query:
        return {"songs": [], "count": 0, "message": "query 不能为空"}
    # 先并场景策略（显式 scene 参数 → 策略表 → query 文本兜底），再并 query 硬条件
    filters, _ = _playlist_filters(args, query)
    candidates = retrieve_candidates_by_filters(
        query, filters, top_k=_clamp_count(args, DEFAULT_SEARCH_COUNT)
    )
    songs = [_brief(c) for c in candidates]
    return {"songs": songs, "count": len(songs)}


# ---------- 工具 2：get_artist_songs ----------

def _get_artist_songs_handler(args, session_id=None):
    """歌手精确/别名查找；0 命中返回空列表，上层据此诚实拒绝。

    session_id 忽略：歌手查找与『最近听过什么』无关，保留签名统一接口。
    """
    artist = (args.get("artist") or "").strip()
    if not artist:
        return {"songs": [], "count": 0, "message": "artist 不能为空"}
    hits = find_artist_hits(artist)
    songs = [_brief(h) for h in hits]
    return {"songs": songs, "count": len(songs)}


# ---------- 工具 3：get_song_profile ----------

def _get_song_profile_handler(args, session_id=None):
    """单曲档案（讨论请求用）；0 命中返回空列表，如实告知。

    session_id 忽略：讨论单曲与『最近听过什么』无关，保留签名统一接口。
    """
    title = (args.get("title") or "").strip()
    if not title:
        return {"songs": [], "count": 0, "message": "title 不能为空"}
    hits = find_songs_by_title(title)
    # 最多取前 3 首命中，各附歌曲知识（评论/歌词提炼）。
    # 为什么取前 3：讨论场景用户一次只关心 1-2 首，前 3 首足够覆盖重名命中；
    # 串行抓取即可（数量少，且 song_knowledge 缓存命中后几乎零成本）。
    songs = []
    for h in hits[:3]:
        song = _brief(h)
        song["knowledge"] = get_song_knowledge(h["id"])
        songs.append(song)
    return {"songs": songs, "count": len(songs)}


# ---------- 工具 4：recommend_playlist ----------

def _apply_taste(candidates, session_id, *, query='', artist='', preferences=None):
    """将单曲赞踩和行为信号映射到有界的个人偏好通道。

    反馈只改变所指作品，不由歌手署名扩散。保留软调整和15%通道权重，
    不让个人偏好覆盖本轮硬条件；与画像面板共用同一个系数函数。
    """
    taste = get_taste(session_id)
    def factor(candidate):
        original = song_taste_factor(candidate["id"], taste)
        if not preferences:
            return original
        from app.preference_memory import factor as preference_factor
        selected = preferences
        if str(candidate['id']) in taste.get('boost_song_ids', set()) | taste.get('demote_song_ids', set()):
            selected = [r for r in preferences if r['kind'] == 'song']
        return preference_factor(candidate, selected, original, query, artist)

    # 正式链路已经带六路分数；这里保留一次兼容补齐，方便纯函数测试和旧调用方
    # 传入尚未评分的候选，不让阶段升级把稳定内部 API 变成隐式炸点。
    if candidates and any("score_components" not in item for item in candidates):
        candidates = rank_candidates(candidates, Filters())
    # 画像只替换其中 15% 的个人偏好
    # 通道，不再对总分做无上限乘法，避免一次反馈压倒语义、标签和声学事实。
    return apply_taste_factors(candidates, factor)


def _fallback_reason(song, message):
    """失败或未写单曲说明时，只展示已有档案线索，不替 AI 补一段套路话。"""
    tags = []
    if song.get("genre"):
        tags.append("风格偏" + "、".join(song["genre"]))
    if song.get("mood"):
        tags.append("情绪带点" + "、".join(song["mood"]))
    return "档案线索：" + "，".join(tags) if tags else "按本次条件从曲库选出。"


def _recommend_playlist_handler(args, session_id=None):
    """完整推荐流程：检索 → 防重复排除已听 → 防幻觉校验 → 同歌手限流 → DJ 介绍兜底。

    返回的 songs 已保证：
    1. id 全部来自真实候选池（validate_and_fill 过滤幻觉 ID）；
    2. 同一歌手最多 2 首；
    3. 不足 3 首按相似度补足；
    4. 每首带确定性 dj_intro（只引用真实元数据/标签）与 evidence。
    5. 普通语义推荐会排除本会话最近推荐过的歌（session_id 为空则不排除）。
    """
    query = (args.get("query") or "").strip() or "随便听听"
    artist = (args.get("artist") or "").strip()
    reference_song_id = str(args.get("reference_song_id") or "").strip()
    title_term = rain_title_term(query)
    if song_reference_mode(query) == "clear":
        reference_song_id = ""
    blocked_ids = {str(value) for value in args.get("_exclude_song_ids", [])}
    from app import preference_memory
    memory_query = query
    if session_id and query == '延续当前歌曲的声音与节奏':
        from app import conversation
        active_task = conversation.get_recommendation_state(session_id) or {}
        if active_task.get('_feedback_task_id') == args.get('_feedback_task_id'):
            memory_query = active_task.get('query') or query
    preferences = preference_memory.relevant(session_id, memory_query, args.get('scene') or '', reference_song_id)
    from app import reference_direction
    remembered = next((row['target']['direction'] for row in preferences if row['kind'] == 'reference_direction'), None)
    correction = args.get('_replacement_direction')
    if any(row['value'] in ('exclude', 'unlike') for row in preferences):
        # 在召回前过滤，避免持续排除的作品占满窗口后才被剔除。
        from app import library as song_library
        all_metadata = song_library._load_candidate_rows(song_library.DB_PATH)
        blocked_ids.update(str(s['id']) for s in all_metadata if any(preference_memory.blocked(row, s) for row in preferences))
    from app.scene_memory import matching
    blocked_ids.update(r['song_id'] for r in matching(session_id, query=query, scene=args.get('scene') or ''))
    if session_id and args.get('_feedback_task_id'):
        from app.task_feedback import active_feedback
        blocked_ids.update(active_feedback(session_id, args['_feedback_task_id'])['song_ids'])
    if reference_song_id:
        blocked_ids.add(reference_song_id)
    comparison = None
    snapshot = None
    cutoff = time.time()
    direction = args.get('_relative_strength_direction')
    if direction in ('lower', 'higher') and session_id:
        # 相对调整直接用确定性说明与已有台词模板，不为标签回退新增模型开销。
        args = {**args, '_defer_copy': True}
        from app import conversation, dj_program
        batch_id = args.get('_relative_strength_batch_id')
        if batch_id:
            # 快捷按钮绑定被点击的真实批次，不能错拿会话里更新的另一批。
            program = dj_program.read_program(batch_id, session_id, conversation.DB_PATH, internal=True)
            snapshot = capture_baseline(program['songs']) if program else None
        else:
            snapshot = (conversation.get_recommendation_state(session_id) or {}).get('_strength_baseline')
        prior_ids = baseline_ids(snapshot)
        comparison = prepare_observed_comparison(direction, snapshot, read_observations(prior_ids, cutoff=cutoff))
        # 这次明确换一批；不足也不把前批作品补回来冒充相对调整。
        blocked_ids.update(prior_ids)
    blocked_artists = set(args.get("_exclude_artists", []))
    blocked_artist_keys = {name for credit in blocked_artists for name in artist_identity_keys(credit)}
    target_count = 1 if args.get("_single_replacement") else resolve_target_count(query, args.get("target_count"))
    if comparison and not prior_ids:
        summary = observed_summary([], comparison, target_count, batch_choice=bool(args.get('_relative_strength_batch_id')))
        return {'songs': [], 'count': 0, 'target_count': target_count,
                'relative_strength': summary, 'message': summary['message'], 'queue_preserved': True}
    # 先并场景策略（显式 scene 参数 → 策略表 → query 文本兜底），再并 query 硬条件。
    # strategy 供 Task 6 素材包取场景 opener；本任务暂不使用也无妨。
    filters, strategy = _playlist_filters(args, query)
    if correction or artist or filters.genres or filters.moods or filters.exclude_genres or comparison or query == '延续当前歌曲的声音与节奏':
        remembered = None
    activity_direction = args.get('_activity') if valid_activity_direction(args.get('_activity')) else activity_from_query(
        query.replace(artist or '\0', ''))
    rain_plan = rain_listening_plan(query, artist=bool(artist), reference=bool(reference_song_id),
                                    other_strategy=bool(strategy) or bool(activity_direction))
    if rain_plan:
        # 天气只保留为任务语境；旧雨天标签不是听感证据，不与明确音乐方向互相抵偿。
        filters = replace(filters, moods=tuple(rain_plan['moods']), scenes=())
    reference_metadata = None
    reference_features = None
    style_hint = {'genres': [], 'source': None, 'semantic_genres': [], 'profiled_others': 0, 'support': {}}
    if reference_song_id:
        reference_metadata = find_songs_by_ids([reference_song_id]).get(reference_song_id)
        if reference_metadata is None:
            # 不信任客户端/模型给出的ID，也不能借孤立声学记录把普通歌单说成参考推荐。
            return {"songs": [], "count": 0, "error": "reference_not_found",
                    "message": "参考歌曲已不在当前曲库，请重新选择一首后再试。",
                    "reference_song": None, "acoustic_reference_available": False}
        reference_features = get_audio_feature(reference_song_id)
        if not artist and not filters.genres:
            style_hint = reference_style_hint(reference_song_id)
            excluded = {str(g).casefold() for g in filters.exclude_genres}
            genres = tuple(g for g in style_hint['genres'] if str(g).casefold() not in excluded)
            semantic_genres = tuple(g for g in style_hint.get('semantic_genres', style_hint['genres'])
                                    if str(g).casefold() not in excluded)
            filters = replace(filters, reference_genres=genres,
                              reference_genre_source=style_hint.get('source'),
                              reference_semantic_genres=semantic_genres,
                              reference_style_details=tuple(detail for detail in style_hint.get('style_details', [])
                                  if detail.split('/', 1)[0].casefold() not in excluded))
            if (set(genres) == {'流行'} and set(filters.reference_style_details) & REGIONAL_POP_DETAILS
                    and query != '延续当前歌曲的声音与节奏'
                    and not (filters.moods or filters.scenes or filters.energy or comparison or activity_direction or remembered)):
                platform_moods = (reference_metadata.get('platform_metadata') or {}).get('mood') or ()
                if len(set(platform_moods).intersection(MOODS)) >= 2:
                    filters = replace(filters, reference_moods=tuple(mood for mood in MOODS if mood in platform_moods))

    recent = set(get_recent_song_ids(session_id, limit=12)) if session_id else set()
    if filters.style_request and args.get("_style_follow_up"):
        blocked_ids.update(str(song_id) for song_id in recent)
    if artist:
        # 歌手限定：用户明确点名歌手时，候选池 = 该歌手的全部歌曲（精确匹配，
        # 不走向量检索）。这样歌单 100% 来自这位歌手，不会混入相似风格的其他人。
        candidates = attach_audio_features(find_artist_hits(artist))
        candidates = [c for c in candidates if str(c["id"]) != reference_song_id]
        if not candidates:
            return {"songs": [], "count": 0, "message": f"歌库没有找到「{artist}」的作品"}
        candidates = [
            {**candidate, "recall_routes": ["artist"]}
            for candidate in candidates
            if matches_filters(candidate, filters)
        ]
        candidates = rank_candidates(candidates, filters, recent_ids=recent, reference_features=reference_features)
    elif title_term and not reference_song_id:
        # 用户明确按名称找作品时直接检查当前合格库存，不受语义召回窗口限制。
        candidates = attach_audio_features(find_eligible_songs(
            filters, exclude_ids=blocked_ids, exclude_artists=blocked_artists).values())
        candidates = [{**s, 'recall_routes': ['title']} for s in candidates
                      if title_term in s['title'].casefold()]
        candidates = rank_candidates(candidates, filters, recent_ids=recent)
    else:
        retrieval_query = rain_plan['retrieval_query'] if rain_plan else query
        if reference_metadata and not reference_features:
            # 尚无声学档案时，用参考歌的真实元数据/标签丰富语义查询；仍排除参考歌，
            # 并在响应里把 acoustic_reference_available 标为 false，绝不假称声音相似。
            retrieval_query = " ".join(filter(None, [
                query,
                reference_metadata.get("title"),
                reference_metadata.get("artist"),
                *reference_metadata.get("genre", []),
                *reference_metadata.get("mood", []),
            ]))
        candidates = retrieve_candidates_by_filters(
            retrieval_query, filters, top_k=_clamp_count(args, DEFAULT_PLAYLIST_POOL),
            return_pool=True, exclude_ids=blocked_ids, exclude_artists=blocked_artists,
            reference_song_id=reference_song_id,
            **({'context_query': query} if rain_plan else {}),
            **({'music_direction': correction or remembered} if correction or remembered else {}),
        )
        if reference_features:
            # 全库的有效标签/本轮排除先决定声学资格，不能先截近邻再删不合格作品。
            # 近期作品沿用原声学通道排除规则；容量不足时仍由后续文本候选回退。
            metadata = find_eligible_songs(
                filters, exclude_ids=blocked_ids | {str(sid) for sid in recent},
                exclude_artists=blocked_artists,
            )
            neighbors = find_similar_features(
                reference_song_id,
                limit=_clamp_count(args, DEFAULT_PLAYLIST_POOL),
                exclude_ids=recent,
                eligible_ids=metadata,
            )
            text_scores = reference_embedding_scores(reference_song_id, (item["id"] for item in neighbors))
            by_candidate_id = {str(item["id"]): dict(item) for item in candidates}
            for neighbor in neighbors:
                song = metadata.get(neighbor["id"])
                if not song or not matches_filters(song, filters):
                    continue
                if neighbor["id"] in by_candidate_id:
                    existing = by_candidate_id[neighbor["id"]]
                    existing["audio_features"] = neighbor["audio_features"]
                    routes = list(existing.get("recall_routes") or [])
                    if "acoustic" not in routes:
                        routes.append("acoustic")
                    existing["recall_routes"] = routes
                else:
                    by_candidate_id[neighbor["id"]] = {
                        **song,
                        "score": text_scores.get(neighbor['id']),
                        "semantic_source": 'reference_embedding' if neighbor['id'] in text_scores else None,
                        "audio_features": neighbor["audio_features"],
                        "recall_routes": ["acoustic"],
                    }
            candidates = list(by_candidate_id.values())
        else:
            reference_features = None
        # 检索层通常已经执行硬过滤；这里仍做一次便宜的最终校验，确保未来增加
        # 新召回通道或测试替身时，语种/纯音乐/显式 emo 等硬约束不会被绕过。
        candidates = [c for c in candidates if matches_filters(c, filters)]
        # 检索层已完成语义+标签召回；这里加入会话新鲜度后再统一排序。
        # 即使稍后因库存不足退化为保留旧歌，它们的新鲜度仍会是 0，排序自然靠后。
        candidates = rank_candidates(
            candidates,
            filters,
            recent_ids=recent,
            reference_features=reference_features,
        )
        candidates = [c for c in candidates if str(c["id"]) != reference_song_id]
    from app.recommendation_state import _genre_values
    explicit_genres = _genre_values(memory_query, None)
    allow_instrumental = bool(any(r['kind'] == 'genre' and r['target_key'] == '纯音乐' and r['value'] == 'prefer' for r in preferences)
        and not filters.want_instrumental and not explicit_genres and not any(w in memory_query for w in ('人声', '不要纯音乐', '不听纯音乐')))
    def memory_eligible(song):
        return matches_filters(song, filters) or (allow_instrumental and matches_filters(song, replace(filters, want_instrumental=True)))
    artist_ids = {str(s['id']) for s in find_artist_hits(artist)} if preferences and artist else None
    if allow_instrumental:
        extra_pool = list(find_eligible_songs(replace(filters, want_instrumental=True), exclude_ids=blocked_ids).values())
        if artist_ids is not None:
            extra_pool = [s for s in extra_pool if str(s['id']) in artist_ids]
        extras = rank_candidates(extra_pool, filters, recent_ids=recent, reference_features=reference_features)
        known_ids = {str(s['id']) for s in candidates}
        candidates.extend({**s, 'recall_routes': ['explicit_instrumental_preference']} for s in extras[:DEFAULT_PLAYLIST_POOL] if str(s['id']) not in known_ids)
    positives = [r for r in preferences if r['kind'] == 'reference' and r['value'] == 'like']
    if positives:
        # 明确正例可进入原语义窗口之外；只补真实且满足本次硬条件的作品。
        by_candidate = {str(c['id']): c for c in candidates}
        for row in positives:
            extra = find_songs_by_ids([row['target']['id']]).get(str(row['target']['id']))
            if extra and str(extra['id']) not in by_candidate and memory_eligible(extra) and (artist_ids is None or str(extra['id']) in artist_ids):
                ranked = rank_candidates([extra], filters, recent_ids=recent, reference_features=reference_features)
                by_candidate[str(extra['id'])] = {**ranked[0], 'recall_routes': ['reference_feedback']}
        candidates = list(by_candidate.values())
    # 快捷调整的排除是本轮硬约束，库存不足也不能偷偷把刚换掉的歌补回来。
    candidates = [{**c, **language_fields(c)} for c in candidates if memory_eligible(c) and (not title_term or title_term in c['title'].casefold())
                  and str(c["id"]) not in blocked_ids
                  and not artist_identity_keys(c.get("artist")).intersection(blocked_artist_keys)]
    if correction:
        candidates = [song for song in candidates if song.get('playability') != 'unavailable'
                      and reference_direction.matches_labels(song, correction)]
    # 品味只调整排序，不删除候选；艺人专场也保留单曲的主动反馈。
    # 近期容量和最终选择必须看到同一排序；合作署名会让容量依赖贪心顺序。
    # 空池直接走原来的无结果路径，不为零候选读取或初始化品味表。
    if session_id and candidates:
        candidates = (_apply_taste(candidates, session_id, query=memory_query, artist=artist, preferences=preferences)
                      if preferences else _apply_taste(candidates, session_id))
    if correction:
        candidates = [song for song in candidates if song['taste_score'] >= 0]
    if rain_plan:
        candidates = prioritize_rain_direction(candidates, rain_plan)
    if reference_song_id and not artist and not filters.genres:
        # 在品味重排之后分组，近期容量与最终贪心选择看到同一顺序；不改分数或召回窗口。
        candidates = prioritize_reference_candidates(candidates, filters, reference_song_id)
        if filters.reference_moods:
            candidates = narrow_regional_pop_reference(
                candidates, filters, {str(row['target']['id']) for row in positives})
    remembered_ids = set()
    if remembered:
        # 已保存方向仅在有足够资料的候选上优先，弱候选仍可供原流程补充。
        remembered_ids = {str(song['id']) for song in candidates if song.get('playability') != 'unavailable'
                          and song['taste_score'] >= 0 and reference_direction.matches_labels(song, remembered)}
    if comparison:
        # 对前批和本轮候选作一次共同读取；固定请求截止时刻，不跨模式混合新旧摘要。
        bundle = read_observations([*prior_ids, *(c['id'] for c in candidates)], cutoff=cutoff)
        comparison = prepare_observed_comparison(direction, snapshot, bundle)
        candidates = prioritize_candidates(candidates, comparison, observations=bundle['observations'])
        acoustic_candidates = [c for c in candidates if (c.get('relative_strength') or {}).get('promoted')]
        per_artist = MAX_PICKS if artist else MAX_PER_ARTIST
        if comparison['usable'] and artist_quota_capacity(acoustic_candidates, per_artist) >= target_count:
            candidates = acoustic_candidates
        elif not comparison['usable'] or any((c.get('relative_strength') or {}).get('energy') is None for c in candidates):
            # 新观察不够时只在既有合格池内作标签相对调整；未知标签不计作零分。
            current = find_songs_by_ids([*prior_ids, *(c['id'] for c in candidates)])
            tag_comparison = prepare_tag_comparison(direction, snapshot, current)
            tag_comparison['acoustic_baseline_observed'] = comparison['baseline_observed']
            tag_comparison['acoustic_baseline_usable'] = comparison['usable']
            tag_comparison['acoustic_supported_available'] = artist_quota_capacity(acoustic_candidates, per_artist)
            candidates = [c for c in candidates if str(c['id']) in current and matches_filters(current[str(c['id'])], filters)]
            candidates = prioritize_tag_candidates(candidates, tag_comparison, current)
            comparison = tag_comparison
        else:
            # 全部有可比数值但方向或配额不足，不能用相反标签推翻已知结果。
            candidates = acoustic_candidates
    if not filters.style_request and session_id and recent and (not artist or args.get("exclude_recent")):
        unseen = [c for c in candidates if c["id"] not in recent]
        # 数量必须按本轮目标及真实歌手配额计算；“有3条”不代表能选出3首，
        # 更不代表能满足8首。只放宽近期重复，不放宽上面已执行的任何硬排除。
        per_artist = MAX_PICKS if artist else MAX_PER_ARTIST
        capacity = artist_quota_capacity(unseen, per_artist)
        if capacity >= target_count:
            candidates = unseen
    if remembered_ids:
        candidates.sort(key=lambda song: str(song['id']) not in remembered_ids)
    if positives:
        positive_ids = {str(r['target']['id']) for r in positives}
        candidates.sort(key=lambda s: str(s['id']) not in positive_ids)
    if filters.style_request:
        candidates = [{**song, "style_match": evidence} for song in candidates
                      if (evidence := match_style(song, filters.style_request))]
        if filters.style_request["mode"] == "mood" and not any(
                song["style_match"]["kind"] != "mood_unknown" for song in candidates):
            candidates = []
        # 同档沿用综合排序。近期记录只在同档调序，近似候选不能抢明确匹配。
        candidates.sort(key=lambda song: (song["style_match"]["tier"],
            song["id"] in recent if not artist or args.get("exclude_recent") else False))
    if comparison:
        per_artist = MAX_PICKS if artist else MAX_PER_ARTIST
        available = artist_quota_capacity(candidates, per_artist)
        if not comparison['usable'] or available < target_count:
            summary = observed_summary([], comparison, target_count, available=available,
                                       batch_choice=bool(args.get('_relative_strength_batch_id')))
            return {'songs': [], 'count': 0, 'target_count': target_count,
                    'relative_strength': summary, 'message': summary['message'], 'queue_preserved': True}
    if not candidates:
        relative_result = {}
        if filters.reference_moods and not (filters.lang or filters.required_genres):
            return {"songs": [], "count": 0, "target_count": target_count,
                    "error": "reference_evidence_insufficient",
                    "message": "现有资料里，暂时没有同时有曲风和情绪线索支持的相近歌曲。"}
        if filters.lang:
            return {"songs": [], "count": 0, "target_count": target_count,
                    **relative_result,
                    "error": "language_evidence_insufficient",
                    "message": f"当前曲库没有平台明确标注为{filters.lang}且符合其他条件的歌曲，未用语种未知的歌曲补足。这不表示曲库一定没有此语种作品。"}
        if filters.style_request:
            return {"songs": [], "count": 0, "target_count": target_count,
                    "error": "genre_evidence_insufficient",
                    "message": selection_message(filters.style_request, [], target_count)}
        if filters.required_genres:
            return {"songs": [], "count": 0, "error": "genre_evidence_insufficient",
                    **relative_result,
                    "message": "当前曲库没有足够的已知风格标签支持这次请求。请在歌单同步中补全百科；这不表示曲库一定没有此类歌曲。"}
        return {"songs": [], "count": 0, "message": "歌库没有找到符合条件的歌曲", **relative_result}

    # 歌手限定场景：用户明确点名该歌手，同歌手限流应放宽（否则 3 首会被砍到 2 首）；
    # 普通语义推荐仍保持「同一歌手最多 2 首」防止专场。
    if artist:
        picks = validate_and_fill(
            [], candidates,
            min_picks=target_count,
            max_picks=target_count,
            max_per_artist=MAX_PICKS,
        )  # 歌手限定放宽同歌手限制，数量仍由 target_count 控制
    else:
        picks = validate_and_fill(
            [], candidates, min_picks=target_count, max_picks=target_count
        )  # 空 picks → 按目标数量从相似度顺序补足
    by_id = {c["id"]: c for c in candidates}
    # 推荐链路不再抓评论/歌词：背景查证是按轮次后台任务，不能挡住歌曲返回。
    # 旧知识模块仅保留兼容入口，不把用户评论当作作品背景的事实来源。
    knowledge_map = {}
    songs = []
    for position, pid in enumerate(picks, 1):
        c = by_id[pid]
        songs.append(
            {
                "id": pid,
                "title": c["title"],
                "artist": c["artist"],
                "album": c["album"],
                "genre": c.get("genre") or [],
                **{k: c[k] for k in ("genre_original", "genre_source", "platform_metadata", "genre_review_id", "genre_review_revision", "embedding_review_notice") if k in c},
                "mood": c.get("mood") or [],
                "scene": c.get("scene") or [],
                "language": c.get("language"),
                **{k: c.get(k) for k in ("language_source", "language_status")},
                # 纯音乐标识：前端『纯音乐』徽标的展示依赖，必须透传候选的真实值。
                # 回归背景：曾漏掉此字段，『来点纯音乐』响应里取不到 is_instrumental。
                "is_instrumental": bool(c.get("is_instrumental")),
                "reason": c["style_match"]["reason"] if filters.style_request else _fallback_reason(c, query),
                # 正式批次的介绍由后台节目作者完成；卡片不继续摆出旧的长模板冒充AI说明。
                "dj_intro": "" if session_id else (
                    c["style_match"]["reason"] if filters.style_request else build_dj_intro(c, query, position)),
                "evidence": build_evidence(c, filters),
                # 命中理由（确定性事实墙）：情绪/风格命中项 + 相似度，Task 8 素材包直接取材
                "known_why": c["style_match"]["reason"] if filters.style_request else build_known_why(c, filters),
                **({"style_match": c["style_match"]} if filters.style_request else {}),
                # 歌曲知识（评论/歌词提炼，缓存命中即取、失败为 None）：DJ 介绍的真实素材
                "knowledge": knowledge_map.get(pid),
                "retrieval_score": round(float(c.get("score") or 0.0), 4),
                "semantic_score": round(float(c.get("semantic_score", c.get("score") or 0.0)), 4),
                "semantic_source": c.get("semantic_source"),
                "tag_score": round(float(c.get("tag_score") or 0.0), 4),
                "taste_score": round(float(c.get("taste_score", 0.5)), 4),
                "transition_score": c.get("transition_score"),
                "freshness_score": round(float(c.get("freshness_score", 1.0)), 4),
                "acoustic_score": c.get("acoustic_score"),
                "audio_features": c.get("audio_features"),
                "taste_factor": round(float(c.get("taste_factor", 1.0)), 4),
                "final_score": round(float(c.get("final_score") or c.get("score") or 0.0), 4),
                "ranking_version": c.get("ranking_version"),
                "ranking_weights": c.get("ranking_weights") or {},
                "score_components": c.get("score_components") or {},
                "playability": c.get("playability", "unknown"),
                "recall_routes": list(c.get("recall_routes") or (["artist"] if artist else [])),
                "score_explanation": list(c.get("score_explanation") or []),
                **({'reference_style_priority': c['reference_style_priority']} if 'reference_style_priority' in c else {}),
                **({'activity_direction': dict(activity_direction)} if activity_direction else {}),
                **({'rain_music_direction': c['rain_music_direction']} if rain_plan else {}),
                **({'relative_strength': c['relative_strength']} if 'relative_strength' in c else {}),
            }
        )
    # strategy 来自 _merge_scene 的场景策略（可能为 None）：透传给 main.py/前端展示，
    # getattr 兜住 None，识别不出场景时返回 null 不影响调用方。
    # 文案调用次数保持不变，自由正文与可选单曲说明均由本次调用完成。
    # 本轮调整原有的 _defer_copy 和静默/简短偏好仍不增加模型调用。
    dj_opener = build_dj_opener(songs, query) if songs else ""
    presentation = preference_memory.expression(session_id, memory_query) if session_id else {}
    if (songs and not session_id and not opening_notice(songs) and not args.get("_defer_copy")
            and presentation.get('mode') != 'quiet' and presentation.get('length') != 'short'):
        from app import conversation, dj_program, song_background
        recent = dj_program.recent_programs(session_id, conversation.DB_PATH) if session_id else []
        backgrounds = {song['id']: song_background.peek_background(song) for song in songs[:2]}
        # 文案看到本轮原话，不能把模型改写的检索 query 当作听众真实处境。
        copy_request = preference_memory._request_query.get() or query
        authored_opener = author_song_intros(songs, copy_request, recent=recent, backgrounds=backgrounds)
        if authored_opener:
            dj_opener = authored_opener
        if not session_id:
            # 直接工具/MCP 调用没有后续批次创建，不把内部一次性暂存字段暴露给调用方。
            from app.voice_author import take_program
            draft = take_program(songs, copy_request)
            if draft:
                for song in songs:
                    song['round_intro'] = draft['text']
    style_priority = reference_priority_summary(songs, target_count)
    priority_notice = (style_priority or {}).get('message') or ''
    if priority_notice:
        dj_opener = (dj_opener + ' ' + priority_notice).strip()
    activity_summary = activity_selection_summary(songs)
    activity_notice = (activity_summary or {}).get('message') or ''
    if activity_notice:
        dj_opener = (dj_opener + ' ' + activity_notice).strip()
    rain_summary = rain_direction_summary(songs, target_count)
    rain_notice = (rain_summary or {}).get('message') or ''
    if rain_notice:
        dj_opener = (dj_opener + ' ' + rain_notice).strip()
    # 两类说明可在“运动时参考这首”同时出现，统一组合，避免后一个覆盖前一个。
    language_notice = (f"平台明确标注为{filters.lang}且符合条件的歌曲不足{target_count}首，未用语种未知的歌曲补足。"
                       if filters.lang and len(songs) < target_count else '')
    notices = [language_notice, selection_message(filters.style_request, songs, target_count),
               "已知风格标签支持的歌曲不足目标数量，未用未知风格歌曲补足。"
               if filters.required_genres and len(songs) < target_count else '', priority_notice, activity_notice, rain_notice]
    strength_summary = observed_summary(songs, comparison, target_count, available=len(songs)) if comparison else None
    if strength_summary:
        notices.append(strength_summary['message'])
    return {
        "songs": songs,
        "dj_opener": dj_opener,
        "count": len(songs),
        "target_count": target_count,
        "message": ' '.join(notice for notice in notices if notice),
        "reference_style_priority": style_priority,
        **({'reference_direction': {'label': reference_direction.describe(remembered),
                                   'matched': len(remembered_ids.intersection(picks))}} if remembered else {}),
        "activity_direction": activity_summary,
        **({'relative_strength': strength_summary} if strength_summary else {}),
        **({'rain_music_direction': rain_summary} if rain_summary else {}),
        "strategy": getattr(strategy, "key", None),
        "reference_song": (
            {
                "id": reference_metadata["id"],
                "title": reference_metadata["title"],
                "artist": reference_metadata["artist"],
            }
            if reference_metadata else None
        ),
        "acoustic_reference_available": bool(
            reference_song_id and reference_features
        ),
        "reference_style_hint": {**style_hint, 'genres': list(filters.reference_genres),
                                 'semantic_genres': list(filters.reference_semantic_genres or ())},
    }


# ---------- 注册表 ----------

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_library",
            "description": (
                "在用户的私人歌库里按语义检索歌曲（向量相似度 + 风格/情绪/语种/纯音乐硬过滤）。"
                "当用户描述心情、场景或风格想听歌时，先用它看看歌库里有什么可选的。"
                "返回候选歌曲列表，每首含 id/歌名/歌手/专辑/风格/情绪/场景/相似度。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "用户的语义描述，例如『深夜开车』『温柔一点』『走心的说唱』",
                    },
                    "genre": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "风格线索，例如说唱、爵士、emo rap。保留原话的只要、排除、任选或融合关系，是否允许近似由原话校验。",
                    },
                    "mood": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "情绪排序线索，例如伤感、内省、温柔、孤独。不要把情绪改写成曲风。",
                    },
                    "lang": {
                        "type": "string",
                        "enum": ["中文", "英文", "日文"],
                        "description": "语种过滤",
                    },
                    "instrumental": {
                        "type": "boolean",
                        "description": "true=只要纯音乐；false=排除纯音乐；不传默认排除纯音乐",
                    },
                    "scene": {
                        "type": "string",
                        "description": "用户的使用场景，如『工作专注』『深夜』『通勤』。无法确定可不填",
                    },
                    "energy": {
                        "type": "string",
                        "enum": ["low", "medium-low", "medium", "medium-high", "high"],
                        "description": "用户期待的整体能量；当前先进入会话状态，后续声学特征排序使用",
                    },
                    "exclude": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "要排除的风格，如『说唱』『金属』",
                    },
                    "count": {
                        "type": "integer",
                        "description": "返回数量上限（默认20，最大50）",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_artist_songs",
            "description": (
                "精确查找某位歌手在用户歌库里的全部歌曲（含别名）。"
                "返回空列表意味着歌库没有 TA 的任何作品——此时必须如实告诉用户"
                "『你的歌库没有 TA 的作品』，建议去网易云收藏后同步，严禁用相似风格的歌曲硬凑。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "artist": {"type": "string", "description": "歌手名，例如『周杰伦』『河南说唱之神』"},
                },
                "required": ["artist"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_song_profile",
            "description": (
                "查看歌库里某首歌的档案（风格/情绪/场景/语种/是否纯音乐）。"
                "当用户讨论某首歌（『X 是什么感觉』『介绍一下 X』）时使用。"
                "返回空列表说明歌库里没有这首歌，要如实告知，不要编造。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "歌名，例如『晴天』『Trauma』"},
                },
                "required": ["title"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "recommend_playlist",
            "description": (
                "生成一份最终播放歌单（3-8 首）：按条件检索 → 后端防幻觉校验 → 同歌手限流 → 每首生成 DJ 介绍。"
                "当用户明确要点歌/推荐时使用，这是唯一返回正式歌单的工具。"
                "返回的 songs 已校验：id 全部来自真实歌库、同一歌手最多 2 首、每首带 dj_intro/evidence。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "用户的语义描述，例如『深夜开车』『想听温柔一点的歌』『emo 说唱』",
                    },
                    "genre": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "风格过滤（命中任一即通过）：说唱/流行/民谣/电子/R&B/摇滚/爵士/灵魂乐/另类/独立/Lo-fi/蒸汽波/City Pop/ACG/国风/古典/金属/蓝调/纯音乐/emo",
                    },
                    "mood": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "情绪过滤（命中任一即通过）：伤感/治愈/温柔/孤独/释然/热烈/平静/夜晚/甜蜜/思念/迷茫/温暖/丧",
                    },
                    "lang": {
                        "type": "string",
                        "enum": ["中文", "英文", "日文"],
                        "description": "语种过滤",
                    },
                    "instrumental": {
                        "type": "boolean",
                        "description": "true=只要纯音乐；false=排除纯音乐；不传默认排除纯音乐",
                    },
                    "scene": {
                        "type": "string",
                        "description": "用户的使用场景，如『工作专注』『深夜』『通勤』。无法确定可不填",
                    },
                    "energy": {
                        "type": "string",
                        "enum": ["low", "medium-low", "medium", "medium-high", "high"],
                        "description": "用户期待的整体能量；用真实播放积累的声学特征参与排序",
                    },
                    "exclude": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "要排除的风格，如『说唱』『金属』",
                    },
                    "artist": {
                        "type": "string",
                        "description": "限定只推荐这位歌手的歌（用户点名『周杰伦的歌』时传『周杰伦』）",
                    },
                    "exclude_recent": {
                        "type": "boolean",
                        "description": "用户明确说『其他歌/不重样』时为 true：排除本会话已推荐歌曲",
                    },
                    "count": {
                        "type": "integer",
                        "description": "候选池大小（默认30，最大50）",
                    },
                    "target_count": {
                        "type": "integer",
                        "minimum": MIN_PICKS,
                        "maximum": MAX_PICKS,
                        "description": "最终播放歌曲数（3-8）；用户没指定时默认3",
                    },
                    "reference_song_id": {
                        "type": "string",
                        "description": "用户说『类似这首』时传当前正在播放的真实歌曲 ID",
                    },
                },
                "required": ["query"],
            },
        },
    },
]

# name -> 同步 handler。为什么不用 async：工具内部全是本地 SQLite + numpy，
# 没有 IO 等待，同步实现更简单、测试不需要事件循环。
HANDLERS = {
    "search_library": _search_library_handler,
    "get_artist_songs": _get_artist_songs_handler,
    "get_song_profile": _get_song_profile_handler,
    "recommend_playlist": _recommend_playlist_handler,
}


def execute_tool(name, args, session_id=None):
    """按名字分发工具调用；未知工具返回错误对象（不抛异常，LLM 也能看到）。

    session_id 是会话上下文（防重复用）：透传给 recommend_playlist 排除已听；
    测试可省略，省略时不排除。统一走关键字参数，handler 缺省 session_id=None，
    保证只关心检索的 handler（search_library 等）无需改动行为。
    """
    if name == 'remember_preference':
        from app.preference_proposal import execute
        return execute(args or {}, session_id)
    handler = HANDLERS.get(name)
    if handler is None:
        return {"error": f"未知工具: {name}"}
    return handler(args or {}, session_id=session_id)


def tool_schemas():
    """返回 DeepSeek tools 参数所需的 schema 列表。"""
    return TOOLS
