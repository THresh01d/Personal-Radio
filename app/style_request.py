"""曲风请求的范围及单曲证据。文本相似度只负责召回和组内排序。"""

import re

from app.platform_metadata import DETAIL_LABELS, GENRE_ALIASES, SOURCE

GENRES = (
    "说唱", "流行", "民谣", "电子", "R&B", "摇滚", "爵士", "灵魂乐",
    "另类", "独立", "Lo-fi", "蒸汽波", "City Pop", "ACG", "国风", "古典",
    "金属", "蓝调", "纯音乐", "emo",
)
EMO_RAP = r"emo[-_\s]*(?:rap|说唱)"
_COMPOSITE = "emo rap"
_NAMES = {**GENRE_ALIASES, **{g.casefold(): g for g in GENRES}}
STYLE_TOKEN = (
    r"(?<![a-z])(?:" + EMO_RAP + "|" + "|".join(
        re.escape(g) for g in sorted(set(_NAMES) | set(DETAIL_LABELS), key=lambda name: (-len(name), name))
    ) + r")(?![a-z])"
)
_STYLE = re.compile(STYLE_TOKEN, re.IGNORECASE)
_QUOTED = re.compile(r'《[^》]*》|「[^」]*」|“[^”]*”|"[^"\n]*"')
_STRICT = r"(?<!不)(?<!不是)(?<!非)(?:只(?:要|听|想听|接受)|必须(?:要|听)?|一定要|严格限定(?:为)?|\bonly\b)"
_CONFIRMATION = (r"(?:(?:明确|确定|确实)\s*)?(?:是|属于|标注为)?\s*"
                 r"(?:中文|国语|华语|英文|日文)?\s*(?:(?:伤感|内省|温柔|安静|轻快|舒缓)的?\s*)?")
_FUSION = r"融合|结合|混合|跨界|兼有|fusion|crossover"
_OR = r"或者|或是|或|还是|\bor\b"
_NEAR = r"(?:接近|类似|相近|差不多|不必严格|不用严格)"
_STRICT_REFERENCE = re.compile(
    _STRICT + r"\s*" + _CONFIRMATION + r"(?:这个|这类|这种|同一)(?:曲风|风格|流派|类型)?"
    r"|(?:不要|别(?:给我|放)?|不接受)\s*(?:风格)?(?:接近|类似|相近|近似)(?:的|曲风|风格|[。！!]|$)",
    re.IGNORECASE,
)
_MOOD_NEIGHBORS = {"伤感": ("孤独", "丧", "迷茫", "内省", "思念"), "温柔": ("平静", "治愈")}


def canonical_style(label):
    label = label.strip().casefold()
    if re.fullmatch(EMO_RAP, label):
        return _COMPOSITE
    return _NAMES.get(label) or (DETAIL_LABELS[label][1] if label in DETAIL_LABELS else None)


def _terms(text):
    return list(dict.fromkeys(canonical_style(match[0]) for match in _STYLE.finditer(text)))


def families(style):
    if style == _COMPOSITE:
        return ("说唱", "emo")
    return (DETAIL_LABELS.get(style.casefold(), (style,))[0],)


def unquoted(text):
    return _QUOTED.sub(lambda match: " " * len(match[0]), text or "")


def is_style_object(text):
    """曲风短语可出现在「X 的歌」中，识别完整短语以免当成歌手名。"""
    if not _STYLE.search(text):
        return False
    modifiers = (r"接近|类似|相近|差不多|融合|结合|混合|跨界|兼有|伤感|内省|温柔|孤独|平静|治愈|"
                 r"舒缓|安静|轻快|华语|国语|中文|英语|英文|日文|日语|明确|确定|属于|"
                 r"风格|流派|一定要|只要|只能|必须|或者|以及|偏|的|和|与|或|是|[\s、，+＋]")
    return bool(re.fullmatch(r"(?:" + modifiers + r")*", _STYLE.sub("", text), re.IGNORECASE))


def standalone_request(text):
    """允许省略点歌动词的明确选择，保留引句和非音乐请求的闸门。"""
    text = text.strip().rstrip("。！!？? ")
    match = re.fullmatch(r"(?:我)?\s*" + _STRICT
                         + r"\s*(?:听|放)?\s*" + _CONFIRMATION + r"(.+?)"
                           r"(?:\s*的(?:歌|音乐)?|\s*歌曲|\s*风格)?", text, re.IGNORECASE)
    if match and _STYLE.fullmatch(match[1].strip()):
        return canonical_style(match[1]) != "纯音乐"
    match = re.fullmatch(r"(.+?)\s*(?:都行|都可以|都好)", text)
    if not match:
        return False
    options = re.split(_OR, match[1], flags=re.IGNORECASE)
    return len(options) > 1 and all(_STYLE.fullmatch(item.strip()) for item in options)


def parse_style(text, moods, *, excluded=()):
    """只由用户原话授权严格或近似；模型改写的 query 不能改变选择范围。"""
    text = unquoted(text)
    matches = list(_STYLE.finditer(text))
    styles = [style for style in _terms(text) if style != "纯音乐"]
    if not styles:
        return None
    # 「接近 emo rap 的说唱」的后一个词是大类锚点。
    if _COMPOSITE in styles and "说唱" in styles and not re.search(_OR, text, re.IGNORECASE):
        styles.remove("说唱")
    relation = "fusion" if len(styles) > 1 and re.search(_FUSION, text, re.IGNORECASE) else "any"
    strict = bool(_STRICT_REFERENCE.search(text)) or any(re.search(
        _STRICT + r"\s*(?:(?:听|放|来)\s*)?(?:[几一两三四五六七八十\d]+首\s*)?"
        + _CONFIRMATION + r"$", text[:match.start()], re.IGNORECASE
    ) for match in matches)
    optional = not strict and bool(re.search(_NEAR, text, re.IGNORECASE))
    if strict:
        mode = "strict"
    elif moods and not optional:
        mode = "mood"
    else:
        mode = "prefer"
    # 保留冲突供检索说明，不能删到空条件后退化成全库推荐。
    return {"styles": styles, "relation": relation, "mode": mode, "moods": list(moods),
            "near_allowed": optional, "excluded": list(excluded)}


def _labels(song):
    platform = song.get("platform_metadata") or {}
    raw = platform.get("raw_genres") if platform.get("status") == "ready" else None
    raw = tuple(label.strip() for label in raw if isinstance(label, str)) if isinstance(raw, list) else ()
    effective = set(song.get("genre") or ())
    if song.get("genre_source") == SOURCE and platform.get("status") != "ready":
        effective.clear()
    if song.get("genre_source") == "user_confirmed":
        # 生效的人工校正覆盖平台归类。平台细分只能在其大类仍生效时提供依据。
        raw = tuple(label for label in raw if (
            canonical_style(label) in effective
            or (label.casefold() in DETAIL_LABELS and set(families(canonical_style(label))) <= effective)
            or (canonical_style(label) == _COMPOSITE and {"emo", "说唱"} <= effective)
        ))
    return effective, raw


def _style_matches(style, effective, raw):
    labels = {canonical_style(label) for label in raw}
    if style == _COMPOSITE:
        return style in labels
    if style in GENRES:
        return style in effective or any(style in families(label) for label in labels if label)
    return style in labels


def positive_genres(song):
    """供风格排除使用，保留被有效单曲原文明确支持的大类。"""
    effective, raw = _labels(song)
    direct = [name for label in raw if (name := canonical_style(label))]
    return effective.union(direct, (g for label in direct for g in families(label)))


def _available_styles(request):
    excluded = {term.casefold() for term in request["excluded"]}
    remaining = [style for style in request["styles"]
                 if not (excluded & {tag.casefold() for tag in (style, *families(style))})]
    if request["relation"] == "fusion" and len(remaining) != len(request["styles"]):
        return []
    return remaining


def match_style(song, request):
    """返回本次可展示的单曲证据。缺失或相反的证据返回 None。"""
    styles = _available_styles(request)
    if not styles:
        return None
    effective, raw = _labels(song)
    platform = song.get("platform_metadata") or {}
    human = song.get("genre_source") == "user_confirmed"
    platform_genre = song.get("genre_source") == SOURCE and platform.get("status") == "ready"
    direct = {style for style in styles if _style_matches(style, effective if (human or platform_genre) else set(), raw)}
    human_direction = effective.copy() if human else set()
    if {"emo", "说唱"} <= human_direction:
        # 审核是一首歌的一次完整人工校正，词表里的 emo 指说唱子流派。
        human_direction.add(_COMPOSITE)
        if _COMPOSITE in styles:
            direct.add(_COMPOSITE)
    topic = "＋".join(styles) if request["relation"] == "fusion" else "或".join(styles)

    if request["relation"] == "fusion":
        # 同首歌上的独立大类标签不足以支持「融合」这个事实。
        if not any(re.search(_FUSION, label, re.IGNORECASE)
                   and set(styles) <= set(_terms(label)) for label in raw):
            return None
        direct = set(styles)
    if direct:
        basis = "人工确认曲风" if direct <= human_direction else "平台单曲标注"
        label = topic if request["relation"] == "fusion" else "、".join(s for s in styles if s in direct)
        match = {"tier": 0, "kind": "exact", "reason": f"{basis}支持「{label}」"}
    elif request["mode"] == "strict":
        return None
    elif inferred := [style for style in styles if style in effective and style != _COMPOSITE]:
        # 档案仍有检索价值，但来源不足以通过严格确认，也不冒充平台标签。
        match = {"tier": 1, "kind": "inferred", "reason": f"单曲档案线索支持「{'、'.join(inferred)}」"}
    else:
        compatible = []
        if request["mode"] != "mood":
            for style in styles:
                if style == _COMPOSITE and "说唱" in positive_genres(song):
                    moods = list(request["moods"]) or ["伤感", "内省"]
                    cues, source, _ = _mood_cues(song, moods)
                    if "emo" in effective:
                        compatible.append("单曲资料有说唱、emo 线索")
                    elif cues:
                        compatible.append(f"有单曲说唱依据，{source}有{'、'.join(cues)}线索")
                elif any(re.fullmatch(r"(.+?)(?:元素|风味|影响|倾向|[-\s]+influenced)", label, re.IGNORECASE)
                         and canonical_style(re.sub(r"(?:元素|风味|影响|倾向|[-\s]+influenced)$", "", label, flags=re.IGNORECASE)) == style
                         for label in raw):
                    compatible.append(f"平台单曲标注有「{style}」元素")
                elif any(re.search(_FUSION, label, re.IGNORECASE) and style in _terms(label) for label in raw):
                    compatible.append(f"平台单曲标注记录了包含「{style}」的融合方向")
        if not compatible:
            return None
        return {"tier": 2, "kind": "near", "reason": "风格接近｜" + "，".join(compatible)}

    if request["moods"]:
        cues, source, tier = _mood_cues(song, request["moods"])
        if cues:
            match.update(tier=tier if request["mode"] == "mood" else match["tier"],
                         reason=f"{match['reason']}，{source}有{'、'.join(cues)}线索")
        elif request["mode"] == "mood":
            match.update(tier=3, kind="mood_unknown", reason=match["reason"] + "，所需情绪待确认")
    return match


def _mood_cues(song, requested):
    platform = song.get("platform_metadata") or {}
    reported = set([*(platform.get("raw_moods") or ()), *(platform.get("mood") or ())]) if platform.get("status") == "ready" else set()
    known = reported.union(song.get("mood") or ())
    for tier, options in enumerate((requested, [neighbor for mood in requested for neighbor in _MOOD_NEIGHBORS.get(mood, ())])):
        cues = list(dict.fromkeys(cue for cue in options if cue in known))
        if cues:
            source = "平台单曲情绪标注" if set(cues) <= reported else "单曲情绪资料"
            return cues, source, tier
    return [], "", 3


def selection_message(request, songs, target):
    """统一工具、聊天与节目对曲风不足和补充内容的解释。"""
    if not request:
        return ""
    styles = _available_styles(request)
    if not styles:
        return "这轮的曲风要求和排除条件冲突。请选一个曲风，或明确取消对应的排除。"
    topic = ("与" if request["relation"] == "fusion" else "或").join(styles)
    matches = [song["style_match"] for song in songs if song.get("style_match")]
    if songs and not matches:
        return ""
    if not songs:
        if request["mode"] == "mood":
            return f"现有单曲资料不足以支持这个曲风和情绪组合，先不硬凑。想放宽情绪要求，可以发「来几首{topic}」。"
        if request["relation"] == "fusion":
            return f"现有资料不足以确认同一首歌融合了{topic}。如果两种风格任选其一也行，可以发「{styles[0]}或{styles[1]}都行」。"
        if request["mode"] == "strict":
            return f"在当前条件下，暂找不到有明确单曲依据的「{topic}」，先保留当前队列。想试近似风格，可以发「再来几首接近{topic}的歌」。"
        return "当前条件下，暂没有足够的已知风格标签或单曲线索，先不硬凑。曲库里可能有此类作品。可以试试其他曲风，或给一首参考曲。"
    notes = []
    if any(match["kind"] == "near" for match in matches):
        notes.append("这轮含风格接近的作品，逐首依据见下方。")
    if any(match["kind"] == "mood_unknown" for match in matches):
        notes.append("部分作品的曲风有依据，情绪待确认。")
    if len(songs) < target:
        notes.append("现有单曲资料只支持这几首。")
    return "".join(notes)


def opening_notice(songs):
    kinds = {song.get("style_match", {}).get("kind") for song in songs}
    clauses = []
    if "near" in kinds:
        evidence = next(song["style_match"]["reason"].removeprefix("风格接近｜")
                        for song in songs if song.get("style_match", {}).get("kind") == "near")
        clauses.append(f"这轮也选了风格接近的作品。比如，{evidence}。")
    if "mood_unknown" in kinds:
        clauses.append("部分作品的情绪线索还不够明确。")
    return "".join(clauses)
