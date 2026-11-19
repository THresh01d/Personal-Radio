"""推荐会话状态：把“上一轮想听什么”变成确定性、可持久化的数据。

聊天原文仍用于DJ理解语气；本模块保存成功推荐及用户明确切换后的当前任务约束。这样
“再来几首”不必赌 LLM 是否正确回忆上一轮，也为后续多路召回提供统一入口。
"""

import re
import uuid

from app import conversation
from app.rain_context import rain_listening_plan
from app.scene import activity_from_query, activity_rejected, unnegated_terms, scene_from_query, valid_activity_direction
from app.style_request import (EMO_RAP, GENRES, STYLE_TOKEN, canonical_style, families,
                               is_style_object, parse_style, unquoted)

MOODS = (
    "伤感", "治愈", "温柔", "孤独", "释然", "热烈", "平静", "夜晚",
    "甜蜜", "思念", "迷茫", "温暖", "丧",
)
SCENES = ("通勤", "深夜", "雨天", "学习", "运动", "睡前", "开车", "派对", "独处", "旅行")
INTENT_SCHEMA_VERSION = 2

# 只接受明确的音乐强度短句，不把“少说点”“心情安静了”等聊天变成调整操作。
_TASK_ENERGY_ADJUSTMENT = re.compile(
    r"(?:再|更|稍微|稍|换得|调得)?(?:安静|舒缓|温柔|柔和|轻|燃|躁|有劲|低能量|高能量)"
    r"(?:一?点|一些|些)(?:吧|就好|就行)?[。！!\s]*"
)
_TASK_SCENE_DECLARATION = re.compile(
    r"(?:我(?:现在)?(?:要|准备)(?:去|开始)?|接下来(?:我要|要|去|开始)?|现在开始|准备|改做|换到)"
    r"(?:跑步|运动|健身|骑行|拉伸|瑜伽|工作|学习|写代码|睡觉|通勤)(?:了)?[。！!\s]*"
)


def task_energy_adjustment(message):
    """短句只有已存在音乐任务时才可执行；无任务时由入口提出一次必要澄清。"""
    return bool(_TASK_ENERGY_ADJUSTMENT.fullmatch((message or '').strip()))


def _task_scene(message, artists=()):
    """用原话核对任务场景；模型自加场景、歌名和艺人名不产生换任务依据。"""
    text = re.sub(r'《[^》]*》|「[^」]*」|“[^”]*”', '', message or '')
    for artist in artists:
        text = text.replace(artist, '')
    strategy = scene_from_query(text)
    if strategy and (not strategy.markers or unnegated_terms(text, strategy.markers)):
        return strategy.key
    hits = unnegated_terms(text, SCENES)
    if hits:
        return '工作专注' if hits[-1][1] == '学习' else hits[-1][1]
    return '睡前' if '睡觉' in text else None


def task_scene_declaration(message):
    """明确活动切换只更新已有音乐任务，不授权创建歌单或长期情境习惯。"""
    if not _TASK_SCENE_DECLARATION.fullmatch((message or '').strip()):
        return None
    return _task_scene(message)


def _previous_task_scene(intent):
    direction = intent.get('activity')
    if valid_activity_direction(direction):
        return '舒缓运动' if direction['mode'] == 'gentle' else '运动'
    return _task_scene(' '.join(intent.get('scenes') or []) + ' ' + (intent.get('query') or ''),
                       intent.get('artists') or [])


def task_scene_changed(message, previous, artists=()):
    """同场景声明不应清掉任务；只有原话中的不同场景才触发退出。"""
    scene = _task_scene(message, artists)
    return bool(previous and scene and scene != _previous_task_scene(previous))

_SONG_REFERENCE_REQUEST = re.compile(
    r"(?P<song>(?:类似|像|接近)(?:这|当前|现在)?(?:一)?首)|"
    r"(?:保持|延续)(?:这个|现在的)?(?:节奏|感觉|声音|氛围)"
)


def song_reference_mode(message):
    """只处理明确的当前歌曲指代；紧邻否定不能触发引用或继承旧参考。"""
    text = message or ""
    matches = list(_SONG_REFERENCE_REQUEST.finditer(text))
    if (any(re.search(r"(?:不要|不想听|不想要|不想|不听|不用|不再|不太|别|不是|不)(?:再)?\s*$", text[:m.start()]) for m in matches)
            or re.search(r"(?:不要|不用|不再|别)\s*(?:参考|沿用)(?:这首|当前|之前)?", text)):
        return "clear"
    if any(m.group("song") for m in matches):
        return "use"
    return "context_use" if matches else None

_CONTINUATION = re.compile(r"^\s*(?:再来|多来|继续|再听|多听|再放|多放|再给我|给我再来|换一批|其他|别的)")
_BARE_CONTINUATION = re.compile(
    r"^\s*(?:再来|多来|继续|再听|多听|再放|多放|再给我|给我再来|换一批)"
    r"(?:几首|几|一些|一点|点儿|一首|首|点)?(?:歌|音乐)?\s*[。！？!?，,]*\s*$"
)
_RESET_DIRECTION = re.compile(r"(?:换个|换一种|改成|不要之前|别按刚才|重新来).*(?:风格|方向|感觉|类型)?")
# 「换几首风格接近的」不是重置方向，也不是继续限定上一位歌手：用户要的是
# 上一批的相邻风格。继承情绪/场景等条件，但主动释放 artist，才能召回相似艺人。
_SIMILAR_STYLE_CONTINUATION = re.compile(
    r"^\s*(?:换|再来|来|推荐)(?:几首|一些|点)?(?:和|跟)?(?:刚才|之前|这些|这几首)?"
    r"(?:风格|感觉|调调|类型)?(?:相近|接近|类似|差不多)(?:的)?(?:歌|音乐)?\s*[。！？!?，,]*\s*$"
)


def _list(value):
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()] if str(value).strip() else []


def _mentioned(message, vocabulary):
    """固定词表；英文词要有字母边界，不能把 demo/emotional 当 emo。"""
    lower = (message or "").lower()
    result = []
    for item in vocabulary:
        if re.search(r"[a-z]", item.casefold()):
            matches = re.finditer(r"(?<![a-z])" + re.escape(item.casefold()) + r"(?![a-z])", lower)
            if any(not _negated_style(lower[:m.start()]) for m in matches):
                result.append(item)
        elif item in lower:
            result.append(item)
    return result


def _negated_style(prefix):
    """本轮新增英文别名不把紧邻的否定当正向偏好；复杂否定仍交语义层。"""
    return bool(re.search(r"(?:不要|不听|别|排除|不是)\s*$", prefix))


_RAP_ALIAS = re.compile(r"(?<![a-z])(?:" + EMO_RAP + r"|rap)(?![a-z])", re.IGNORECASE)

# 只接受紧邻否定词的固定风格及简单并列；纯音乐类型仍交给既有独立条件。
_STYLE_TOKEN = r"(?!纯音乐)" + STYLE_TOKEN
_NEGATIVE_STYLES = re.compile(
    r"(?<!不是)(?<!并非)(?:不要放|不要|不想听|不想要|不听|不放|别放|别听|别来|别|排除)\s*"
    r"(?P<styles>" + _STYLE_TOKEN + r"(?:\s*(?:以及|还有|和|及|或|、|/|，|,)\s*" + _STYLE_TOKEN + r")*)"
    r"(?=\s*(?:$|[，,。.!！?？;；]|的?(?:歌|音乐|风格|类型)|[了吧]))",
    re.IGNORECASE,
)
_STYLE_TOKENS = re.compile(_STYLE_TOKEN, re.IGNORECASE)


def _canonical_genre(value):
    if _RAP_ALIAS.fullmatch(value):
        return "emo" if value.casefold().startswith("emo") else "说唱"
    return canonical_style(value) or value


def genre_text_signals(message):
    """返回去除明确否定片段的文本与排除项；双重否定等复杂句不擅自推断。"""
    text = message or ""
    excluded, masked = [], list(text)
    for match in _NEGATIVE_STYLES.finditer(text):
        excluded.extend(_canonical_genre(m.group()) for m in _STYLE_TOKENS.finditer(match.group("styles")))
        masked[match.start():match.end()] = " " * (match.end() - match.start())
    return "".join(masked), list(dict.fromkeys(excluded))


def _genre_values(message, explicit):
    """只规范明确词义，不由 emo 推断中文/低能量/伤感，更不改歌曲档案。"""
    values = _list(explicit)
    if values:
        expanded = []
        for value in values:
            if _RAP_ALIAS.fullmatch(value):
                # emo rap 是复合风格，不是“emo 或任意说唱”；现有 genre 多值按或
                # 计分，不能把补词变成普通说唱满分。结构化显式多值仍保留原契约。
                expanded.append("emo" if value.casefold().startswith("emo") else "说唱")
            else:
                expanded.append(value)
        return list(dict.fromkeys(expanded))
    text, _ = genre_text_signals(message)
    found = set(_mentioned(text, GENRES))
    for match in _RAP_ALIAS.finditer(text):
        if not _negated_style(text[:match.start()]):
            found.add("emo" if match.group().casefold().startswith("emo") else "说唱")
    return [genre for genre in GENRES if genre in found]


def _language(message, args):
    if args.get("lang"):
        return args["lang"], True
    text = message or ""
    if any(word in text for word in ("中文", "国语", "华语")):
        return "中文", True
    if "英文" in text or "英语" in text:
        return "英文", True
    if "日文" in text or "日语" in text:
        return "日文", True
    return None, False


def _instrumental(message, args):
    if "instrumental" in args:
        return bool(args["instrumental"]), True
    lower = (message or "").lower()
    # 否定短语包含“纯音乐”字面，必须先判否定，不能被下面的正向子串抢先命中。
    if any(word in lower for word in ("不要纯音乐", "排除纯音乐", "有人声")):
        return False, True
    if any(word in lower for word in ("纯音乐", "轻音乐", "钢琴曲", "instrumental", "inst")):
        return True, True
    return False, False


def _energy(message, args):
    if args.get("energy"):
        return str(args["energy"]), True
    text = (message or "").lower()
    if any(word in text for word in ("低能量", "舒缓", "安静")):
        return "low", True
    if any(word in text for word in ("温柔", "轻一点", "柔和")):
        return "medium-low", True
    if any(word in text for word in ("高能量", "燃", "躁", "有劲", "强节奏")):
        return "high", True
    return None, False


def _activity_fields(message, previous=None):
    """只从当前用户词句或已确认的上轮条件恢复活动方向，不采纳模型自加的锁定。"""
    message = re.sub(r'《[^》]*》', '', message or '')
    base = previous or {}
    direction = activity_from_query(message, base.get('activity'))
    if direction is None:
        return None
    positive_moods = {word for _, word in unnegated_terms(message, MOODS)}
    denied_moods = {word for word in MOODS if word in message and word not in positive_moods}
    moods = [m for m in MOODS if m in positive_moods] or list(base.get('moods') or [])
    moods = [m for m in moods if m not in denied_moods]
    genres = _genre_values(message, None) or list(base.get('genres') or [])
    language, has_language = _language(message, {})
    if '华语' in message:
        language, has_language = '中文', True
    instrumental, has_instrumental = _instrumental(message, {})
    return {'activity': direction, 'moods': moods, 'genres': genres, 'scenes': ['运动'],
            'energy': direction['energy'],
            'language': language if has_language else base.get('language'),
            'instrumental': instrumental if has_instrumental else bool(base.get('instrumental'))}


def parse_intent(message, args=None):
    """把工具参数和确定性词表合并成结构化意图，并标记本轮显式字段。

    工具参数来自 Agent 的语义理解，固定词表负责在降级/直通路径补齐显式条件。
    ``provided`` 只在合并时使用，不入库；它让“没提语种”和“明确不限语种”可区分。
    """
    args = dict(args or {})
    artist = (args.get("artist") or "").strip()
    if artist and is_style_object(artist) and not re.search(r"(?:歌手|艺人|乐队|组合)\s*[:：]?\s*" + re.escape(artist),
                                                          message or "", re.IGNORECASE):
        artist = ""
    style_text = unquoted((message or "").replace(artist, "") if artist else message)
    positive_style, text_excluded = genre_text_signals(style_text)
    excluded = list(dict.fromkeys([*(_canonical_genre(g) for g in _list(args.get("exclude"))), *text_excluded]))
    blocked = {g.casefold() for g in excluded}
    explicit_genres = [g for g in _genre_values(message, args.get("genre")) if g.casefold() not in blocked]
    explicit_moods = _list(args.get("mood")) or _mentioned(message, MOODS)
    explicit_scenes = _list(args.get("scene")) or _mentioned(message, SCENES)
    if activity_rejected(message) and not activity_from_query(message):
        explicit_scenes = [s for s in explicit_scenes if s != '运动']
    language, has_language = _language(message, args)
    instrumental, has_instrumental = _instrumental(message, args)
    energy, has_energy = _energy(message, args)
    reference_rejected = song_reference_mode(message) == "clear"
    requested_moods = list(dict.fromkeys(word for _, word in unnegated_terms(positive_style, (*MOODS, "内省"))))
    style_request = parse_style(positive_style, requested_moods, excluded=text_excluded)
    # 兼容旧接口中的平铺字段。新曲风约束由 style_request 整体执行。
    required = [] if style_request else [g for g in _genre_values(style_text, None)
                                         if g != "纯音乐" and g.casefold() not in blocked]
    if style_request:
        if artist and artist.casefold() not in (message or "").casefold():
            artist = ""
        # 旧软标签仍可表达模型建议。复合曲风只占一个软标签维度，范围由独立契约保证。
        requested = ["emo" if style == "emo rap" else families(style)[0] for style in style_request["styles"]]
        if not args.get("genre"):
            explicit_genres = list(dict.fromkeys(g for g in requested if g.casefold() not in blocked))
        # 模型可以补充情绪用于组内排序，硬放宽及可执行的情绪方向由原话决定。
        if requested_moods:
            explicit_moods = requested_moods
        excluded = text_excluded
        language, has_language = _language(style_text, {})
        instrumental, has_instrumental = _instrumental(style_text, {})
    intent = {
        "query": (args.get("query") or message or "随便听听").strip(),
        "artists": [artist] if artist else [],
        "required_genres": required,
        "genres": explicit_genres,
        "moods": explicit_moods,
        "scenes": explicit_scenes,
        "language": language,
        "instrumental": instrumental,
        "energy": energy,
        "excluded_genres": excluded,
        "target_count": args.get("target_count"),
        "reference_song_id": None if reference_rejected else (str(args.get("reference_song_id") or "").strip() or None),
        "continuation": False,
        **({"style_request": style_request} if style_request else {}),
    }
    provided = {
        "artists": bool(artist),
        "required_genres": bool(required or style_request),
        "style_request": bool(style_request),
        "genres": bool(explicit_genres),
        "moods": bool(explicit_moods),
        "scenes": bool(explicit_scenes),
        "language": has_language,
        "instrumental": has_instrumental,
        "energy": has_energy,
        "excluded_genres": "exclude" in args or bool(text_excluded),
        "target_count": "target_count" in args,
        "reference_song_id": "reference_song_id" in args or reference_rejected,
    }
    activity = _activity_fields(style_text)
    if activity:
        intent.update(activity)
        # 模型可辅助表达，但不能把“运动”改写成深夜伤感，继而靠字面查询重新引偏。
        intent['query'] = (message or '').strip()
        for field in ('moods', 'genres', 'scenes', 'language', 'instrumental', 'energy', 'activity'):
            provided[field] = True
    elif rain_listening_plan(message, artist=bool(artist), reference=bool(intent['reference_song_id']),
                             other_strategy=bool(scene_from_query(message))):
        # 使用原话建立雨天语境；不能让模型改写的雨主题查询覆盖明确的温柔方向。
        intent.update(query=message.strip(), moods=['温柔'], scenes=['雨天'], energy=_energy(message, {})[0] or energy)
        for field in ('moods', 'scenes', 'energy'):
            provided[field] = True
    return _with_contract(intent), provided


def _with_contract(intent):
    """生成稳定的 v2 意图视图，同时保留旧版平铺字段供现有调用方兼容。"""
    result = dict(intent)
    result["schema_version"] = INTENT_SCHEMA_VERSION
    result["hard_filters"] = {
        "required_genres": list(result.get("required_genres") or []),
        "artists": list(result.get("artists") or []),
        "language": result.get("language"),
        # 系统默认排除纯音乐，因此 False 也是实际执行的硬约束，不是假装“未设置”。
        "instrumental": bool(result.get("instrumental")),
        "excluded_genres": list(result.get("excluded_genres") or []),
    }
    result["soft_preferences"] = {
        "genres": list(result.get("genres") or []),
        "moods": list(result.get("moods") or []),
        "scenes": list(result.get("scenes") or []),
        "energy": result.get("energy"),
    }
    result["reference"] = {
        "song_id": result.get("reference_song_id"),
    }
    result["selection"] = {
        "target_count": result.get("target_count"),
        "continuation": bool(result.get("continuation")),
    }
    if result.get("style_request"):
        result["style_request"] = {**result["style_request"], "excluded": result["hard_filters"]["excluded_genres"]}
        result["selection"]["style_request"] = dict(result["style_request"])
    return result


def _intent_to_args(intent, original_args):
    """把状态还原成现有 recommend_playlist 工具参数，保持旧接口兼容。"""
    resolved = dict(original_args or {})
    resolved["query"] = intent["query"]
    resolved["_required_genres"] = list(intent.get("required_genres") or [])
    resolved.pop("_style_request", None)
    if intent.get("style_request"):
        resolved["_style_request"] = dict(intent["style_request"])
    mappings = (
        ("artists", "artist"), ("genres", "genre"), ("moods", "mood"),
        ("scenes", "scene"), ("language", "lang"),
        ("energy", "energy"), ("excluded_genres", "exclude"),
        ("target_count", "target_count"),
        ("reference_song_id", "reference_song_id"),
    )
    for state_key, arg_key in mappings:
        value = intent.get(state_key)
        if not value:
            resolved.pop(arg_key, None)
        elif state_key == "artists":
            resolved[arg_key] = value[0]
        elif state_key == "scenes":
            resolved[arg_key] = value[0] if len(value) == 1 else "、".join(value)
        else:
            resolved[arg_key] = value
    if intent.get("instrumental"):
        resolved["instrumental"] = True
    elif "instrumental" in resolved:
        resolved["instrumental"] = False
    if intent.get('activity'):
        resolved['_activity'] = dict(intent['activity'])
    else:
        resolved.pop('_activity', None)
    resolved["_feedback_task_id"] = intent.get("_feedback_task_id", "")
    return resolved


def resolve_recommendation(session_id, message, args=None, db_path=None):
    """解析本轮参数；续播时从持久化状态继承未被本轮覆盖的字段。"""
    current, provided = parse_intent(message, args)
    previous = conversation.get_recommendation_state(session_id, db_path=db_path) if session_id else None
    if previous:
        # 表达去重属于会话显示元数据，不能被模型/推荐契约当作选歌依据继承。
        previous = {key: value for key, value in previous.items()
                    if key not in ('_reply_presentation', '_strength_baseline')}
    if previous and "required_genres" not in previous:
        # 旧会话没有来源字段，只从保留的方向文本恢复明确词，不把软标签直接升级。
        previous = dict(previous)
        legacy_text = previous.get("query") or ""
        for name in previous.get("artists") or []:
            legacy_text = legacy_text.replace(name, "")
        previous["required_genres"] = [g for g in _genre_values(legacy_text, None) if g != "纯音乐"]
    similar_style = bool(previous and _SIMILAR_STYLE_CONTINUATION.match(message or ""))
    energy_adjustment = bool(previous and task_energy_adjustment(message))
    if energy_adjustment:
        # 纯强度短句没有授权模型顺带改语种、风格、歌手或参考，其他字段来自有效任务。
        current, provided = parse_intent(message, {"query": message})
    changed_scene = task_scene_changed(message, previous, [args['artist']] if (args or {}).get('artist') else [])
    continuing = bool(previous and (
        energy_adjustment or similar_style
        or (_CONTINUATION.search(message or "") and not _RESET_DIRECTION.search(message or ""))
    ) and not changed_scene)

    if continuing:
        merged = dict(previous)
        resets_old_exclusion = bool(provided["genres"] and {
            g.casefold() for g in current["genres"]
        }.intersection(g.casefold() for g in previous.get("excluded_genres", [])))
        if similar_style:
            # “风格接近”继承的是音乐方向，不是歌手硬过滤；保留 artist 会退化成
            # “再来几首 Drake”，无法真正换到相似艺人。
            merged["artists"] = []
        for field, was_provided in provided.items():
            if was_provided:
                merged[field] = current[field]
        old_style, new_style = previous.get("style_request"), current.get("style_request")
        if (old_style and new_style and old_style["mode"] == "strict"
                and set(old_style["styles"]) == set(new_style["styles"])
                and old_style["relation"] == new_style["relation"]
                and not new_style["near_allowed"]):
            merged["style_request"] = {**new_style, "mode": "strict"}
        if old_style and not new_style:
            positive_moods = [word for _, word in unnegated_terms(message, (*MOODS, "内省"))]
            denied_moods = {word for word in (*MOODS, "内省") if word in message and word not in positive_moods}
            if positive_moods or denied_moods:
                moods = positive_moods or [mood for mood in old_style["moods"] if mood not in denied_moods]
                mode = old_style["mode"] if old_style["mode"] == "strict" else ("mood" if moods else "prefer")
                merged["style_request"] = {**old_style, "mode": mode, "moods": list(dict.fromkeys(moods))}
        if provided["genres"] and not provided["excluded_genres"]:
            # 本轮明确改要某风格时解除同名的旧排除；本轮明确排除仍有更高优先级。
            requested = {g.casefold() for g in current["genres"]}
            merged["excluded_genres"] = [g for g in merged.get("excluded_genres", []) if g.casefold() not in requested]
        if energy_adjustment:
            # 当前强度已结构化保存；只保留最新一条短句，防重复调整无限增长查询原文。
            context = '；'.join(part for part in (previous.get('query') or '').split('；')
                               if not task_energy_adjustment(part))
            merged['query'] = f"{context}；{current['query']}".strip('；')
        elif provided["reference_song_id"] and current["reference_song_id"]:
            # 新参考由本轮显式绑定，旧否定/指代原文不能再次让工具丢失或误用它。
            merged["query"] = current["query"]
        elif resets_old_exclusion:
            # 其他继承条件已有结构化字段；旧否定原文不能再次进入工具兜底推翻本轮改口。
            merged["query"] = current["query"]
        elif _BARE_CONTINUATION.match(message or ""):
            merged["query"] = previous.get("query") or current["query"]
        else:
            merged["query"] = f"{previous.get('query') or ''}；{current['query']}".strip("；")
        merged["continuation"] = True
        current = merged
    else:
        current["continuation"] = False

    # 裸续播中的模型猜测也不能覆盖上轮活动方向；用户新场景则退出本轮运动默认。
    text_scenes = _mentioned(message, SCENES)
    previous_activity = previous if continuing and previous.get('activity') and not any(s != '运动' for s in text_scenes) else None
    activity = _activity_fields((message or '').replace((args or {}).get('artist') or '\0', ''), previous_activity)
    if activity:
        current.update(activity)
        if activity_from_query(message):
            current['query'] = message.strip()
    elif current.pop('activity', None):
        # 清除旧活动原文，否则工具层从拼接query又会把刚退出的运动方向重新激活。
        current['query'] = message.strip()
        current['scenes'] = [s for s in current.get('scenes') or [] if s != '运动']
        if not provided.get('energy'):
            current['energy'] = None

    # 仅表达“不要”时可能继承上一轮正向风格，不能让旧条件与新的硬排除冲突。
    blocked = {g.casefold() for g in current.get("excluded_genres", [])}
    current["genres"] = [g for g in current.get("genres", []) if g.casefold() not in blocked]
    current["required_genres"] = [g for g in current.get("required_genres", []) if g.casefold() not in blocked]
    # 沿用既有任务边界；新任务即使方向相同也不能复活旧的临时排除。
    current['_feedback_task_id'] = (previous.get('_feedback_task_id') if continuing else None) or uuid.uuid4().hex
    current = _with_contract(current)
    resolved = _intent_to_args(current, args or {})
    if continuing:
        resolved["exclude_recent"] = True
        if current.get("style_request"):
            resolved["_style_follow_up"] = True
    return resolved, current
