"""app/retrieval.py - 检索与校验的纯函数层（无 HTTP、无模型、无数据库）

阶段 4 把「推荐质量里的确定性规则」从 main.py 提取成可独立测试的纯函数，
main.py（后端）、tests/test_constraints.py（单元测试）、
scripts/evaluate_topk.py（离线评测）三处复用同一份逻辑，
避免同一套规则被复制三份然后各改各的。

为什么这么做：
1. 硬过滤（纯音乐/emo/语种）和防幻觉校验是确定性约束，应该被测试钉死；
2. 纯函数无副作用，测试不需要起服务、不需要调用 DeepSeek（不花钱、不依赖外网）；
3. 评测脚本与线上检索走同一套规则，评测结果才对线上行为有说服力。

设计约定：候选字典（song）统一使用平铺 schema：
    id / title / artist / album / score / genre / mood / scene
    / is_instrumental / language / platform_metadata
语种硬条件还需ready状态的平台字段；仅有language或自报来源标记不构成证据。
"""

import hashlib
import re
from app.language_evidence import reported_language
from collections import Counter
from dataclasses import dataclass

# 推荐数量约束（后端保证的硬性边界）
MIN_PICKS = 3        # 至少推荐 3 首
MAX_PICKS = 8        # 最多推荐 8 首（长播放列表，场景需要可给更多）
MAX_PER_ARTIST = 2   # 同一歌手最多 2 首，避免推荐变成「某歌手专场」

# 档案系统的风格/情绪/场景固定词表（与 scripts/build_profiles.py 的 System prompt 一致）。
# 意图分类用它在「来几首 X」里区分风格词与歌手名（河南说唱之神 ≠ 说唱）；
# 精确集合匹配，不做子串匹配，避免把含风格词的人名误判成风格。
STYLE_KEYWORDS = frozenset({
    # genre 词表
    "说唱", "流行", "民谣", "电子", "r&b", "摇滚", "爵士", "灵魂乐",
    "另类", "独立", "lo-fi", "蒸汽波", "city pop", "acg", "国风",
    "古典", "金属", "蓝调", "纯音乐",
    # mood 词表
    "emo", "伤感", "治愈", "温柔", "孤独", "释然", "热烈", "平静",
    "夜晚", "甜蜜", "思念", "迷茫", "温暖", "丧",
    # scene 词表
    "通勤", "深夜", "雨天", "学习", "运动", "睡前", "开车", "派对", "独处", "旅行",
    # 用户高频口语/复合风格写法（与词表等价）
    "中文说唱", "emo rap", "轻音乐", "钢琴", "钢琴曲", "inst", "instrumental", "rnb",
})


@dataclass(frozen=True)
class Filters:
    """从用户消息解析出的硬过滤规则。

    genres / moods / scenes 是 Agent 工具层补充的结构化软信号：规则词表 parse_filters
    只认识 emo/纯音乐/语种这几个关键词，而用户表达是无限的（「来点丧的」
    「要治愈的」），语义理解交给 LLM 后，它把 genre/mood 以结构化参数传入，
    这里交给混合排序加分。默认空元组 = 没有该维度的加分信号。
    """

    want_instrumental: bool = False  # True=只要纯音乐；False=默认排除纯音乐
    want_emo: bool = False           # True=只要 genre 标了 emo 的歌
    lang: str | None = None          # 指定语种（中文/英文/日文），None 表示不限
    genres: tuple = ()               # 希望命中的风格（混合排序软信号）
    moods: tuple = ()                # 希望命中的情绪（混合排序软信号）
    scenes: tuple = ()               # 场景标签（混合排序软信号，不做硬过滤）
    exclude_genres: tuple = ()       # 硬排除的风格（如工作专注排除说唱/金属/摇滚）
    energy: str | None = None        # low 到 high，来自结构化意图的声学软信号
    required_genres: tuple = ()      # 用户明确风格，至少命中一项已有标签；未知不能凑数
    reference_genres: tuple = ()     # 参考单曲的可用风格线索，仅作软信号，不升级硬过滤
    reference_genre_source: str | None = None  # 区分人工、平台与原档案推断，不当作听感真值
    reference_semantic_genres: tuple | None = None  # 独立保留旧向量启用门槛；None兼容旧调用者
    reference_style_details: tuple = ()  # 平台明确子标签，与当前有效大类一致；不升级硬过滤
    reference_moods: tuple = ()  # 地域流行参考采用已有情绪线索；空值沿用其他参考路径
    style_request: dict | None = None  # 已核实的曲风范围、组合和证据门槛


def parse_filters(message: str) -> Filters:
    """从消息里解析硬过滤规则：要不要纯音乐、要不要 emo 风格、指定语种。

    为什么做成显式规则而不是交给 LLM：这些是确定性约束，
    在向量检索阶段就过滤掉，比让 LLM 事后挑选更可靠（LLM 看不到全部曲库）。
    emo 现在是 genre 标签（emotional rap 子流派）而不是情绪标签，
    用户明确提到「emo」就必须只推 genre 含 emo 的歌，不能靠伤感情绪推断。
    """
    msg = message.lower()
    # 明确要纯音乐：命中这些词才只看纯音乐；否则默认把纯音乐排除在外
    want_instrumental = any(k in msg for k in ("纯音乐", "轻音乐", "钢琴", "inst"))
    # emo 意图：前后不被拉丁字母夹住才匹配（避免把 emotional/demo 误判成 emo），
    # 中文里「好emo」「emo rap」都能命中
    want_emo = bool(re.search(r"(?<![a-zA-Z])emo(?![a-zA-Z])", msg))
    lang = None
    if any(k in msg for k in ("中文", "国语")):
        lang = "中文"
    elif any(k in msg for k in ("英文", "英语")):
        lang = "英文"
    elif any(k in msg for k in ("日文", "日语")):
        lang = "日文"
    energy = None
    if any(k in msg for k in ("高能量", "燃一点", "燃起来", "躁一点", "强节奏", "有劲")):
        energy = "high"
    elif any(k in msg for k in ("低能量", "安静", "舒缓", "睡前")):
        energy = "low"
    elif any(k in msg for k in ("温柔", "轻一点", "柔和")):
        energy = "medium-low"
    return Filters(
        want_instrumental=want_instrumental,
        want_emo=want_emo,
        lang=lang,
        energy=energy,
    )


def matches_filters(song: dict, filters: Filters) -> bool:
    """单首歌是否通过硬过滤规则。

    song 必须含 genre(list)/is_instrumental(bool)/language(str|None) 字段。
    缺少明确平台语种字段的歌会被指定语种请求排除，旧推断不再作为硬条件证据，
    默认（不要求纯音乐）时不会被排除——和「没跑建档脚本也能纯向量检索」的行为一致。
    """
    genre = song.get("genre") or []
    mood = song.get("mood") or []
    is_inst = bool(song.get("is_instrumental"))

    # 大小写归一：档案里存的是规范写法（R&B/City Pop/Lo-fi），而 query 兜底词表
    # 与意图识别给的是小写（r&b/city pop/lo-fi）。集合相交必须忽略大小写，
    # 否则『来几首欧美R&B』会把库里 1114 首 R&B 全滤掉、谎报『没找到』（曾踩过）。
    gl = {str(t).lower() for t in genre}
    ml = {str(t).lower() for t in mood}

    if filters.required_genres and not gl.intersection(str(g).casefold() for g in filters.required_genres):
        return False
    if filters.want_emo:
        # emo 是风格：必须 genre 明确标了 emo，不能凭丧/孤独情绪推断
        if "emo" not in gl:
            return False
    if filters.want_instrumental:
        if not is_inst:  # 只要纯音乐：非纯音乐（含无档案）一律排除
            return False
    elif is_inst:  # 默认排除纯音乐，避免推荐里混入无词纯音乐
        return False
    if filters.lang:
        if reported_language(song) != filters.lang:
            return False
    if filters.style_request:
        from app.style_request import match_style, positive_genres
        if filters.exclude_genres:
            gl.update(str(tag).casefold() for tag in positive_genres(song))
        if not match_style(song, filters.style_request):
            return False
    # 硬排除风格：命中任一即排除（场景策略用，如工作专注排除强人声）
    if filters.exclude_genres and gl.intersection(str(e).lower() for e in filters.exclude_genres):
        return False
    # genres / moods / scenes 是相关性信号，由混合排序加分，不在这里硬删除。
    # 因此尚未生成档案的歌曲仍能凭语义向量进入候选；明确 emo 仍由上面的
    # want_emo 保持硬过滤，语种/纯音乐/排除风格等确定性约束也不受影响。
    return True


def artist_identity_names(credit: str | None) -> tuple[str, ...]:
    """只拆明确的合作署名，避免把英文全名、AC/DC或含&的组合拆成假艺人。"""
    parts = re.split(r"\s+/\s*|\s*/\s+|[、；]", credit or "")
    names = tuple(dict.fromkeys(" ".join(part.split()).casefold() for part in parts if part.strip()))
    # 缺失署名沿用同一个未知配额，不能因为没有名字就无限补足。
    return names or ("",)


def artist_identity_keys(credit: str | None) -> frozenset[str]:
    """配额和排除使用同一完整艺人身份集合；顺序只在读取首位署名时使用。"""
    return frozenset(artist_identity_names(credit))


def artist_quota_counts(songs) -> Counter:
    """合作曲为每位完整署名各占一次；同一署名重复列出时不重复扣配额。"""
    return Counter(name for song in songs for name in artist_identity_keys(song.get("artist")))


def artist_quota_capacity(candidates, max_per_artist=MAX_PER_ARTIST) -> int:
    """按实际选择顺序计算可选数量，与最终贪心选择保持一致，不虚报合作曲容量。"""
    return len(validate_and_fill([], candidates, min_picks=len(candidates),
                                 max_picks=len(candidates), max_per_artist=max_per_artist))


def validate_and_fill(
    picks,
    candidates,
    min_picks: int = MIN_PICKS,
    max_picks: int = MAX_PICKS,
    max_per_artist: int = MAX_PER_ARTIST,
) -> list:
    """后端防线：过滤幻觉 ID；收敛同歌手数量；不足按相似度补足。

    为什么必须先收敛同歌手、再补足：
    1. LLM 可能一口气给同歌手 3-4 首（它只看到 ID 和标签，看不到歌手分布），
       直接返回会让推荐变成「某歌手专场」，违反硬约束；
    2. 先收敛后补足，补足阶段才能基于「已占用的歌手配额」做正确决策——
       否则补足时可能再次选满同歌手。
    """
    cand_ids = [c["id"] for c in candidates]
    rank = {sid: i for i, sid in enumerate(cand_ids)}  # 候选越靠前相似度越高
    artist_of = {c["id"]: artist_identity_keys(c.get("artist")) for c in candidates}

    # 只保留候选内的 ID，并按相似度顺序去重排序（幻觉 ID 在这里被过滤）
    valid = sorted({p for p in (picks or []) if p in rank}, key=lambda x: rank[x])

    artist_count: Counter = Counter()
    result: list = []
    selected_ids = set()

    # 第一步：按相似度顺序收敛 picks，同歌手最多 max_per_artist 首
    for pid in valid:
        if len(result) >= max_picks:
            break
        if any(artist_count[name] >= max_per_artist for name in artist_of[pid]):
            continue
        result.append(pid)
        selected_ids.add(pid)
        artist_count.update(artist_of[pid])

    # 第二步：仍不足 min_picks 时，按相似度从候选池补足（候选已按相似度降序）
    if len(result) < min_picks:
        for c in candidates:
            if len(result) >= min_picks:
                break
            if c["id"] in selected_ids:
                continue
            if any(artist_count[name] >= max_per_artist for name in artist_of[c["id"]]):
                continue
            result.append(c["id"])
            selected_ids.add(c["id"])
            artist_count.update(artist_of[c["id"]])

    return result[:max_picks]


def build_evidence(song: dict, filters: Filters) -> list:
    """为单首歌生成可解释证据列表（后端事实，禁止 LLM 编造）。

    证据来源只能是：
    1. 命中查询意图的确定性过滤项（emo / 语种 / 纯音乐）；
    2. 歌曲真实标签（风格 / 情绪 / 场景）；
    3. 向量召回相似度（score）。

    LLM 的 reason 是自然语言口吻，evidence 是硬数据，两者分开返回——
    前端展示「为什么推荐」时以 evidence 为准，避免 LLM 编造理由。
    """
    evidence = []
    genre = song.get("genre") or []
    mood = song.get("mood") or []
    scene = song.get("scene") or []
    lang_val = reported_language(song)

    # 命中查询意图的确定性过滤项
    if filters.want_emo and "emo" in genre:
        evidence.append("风格命中：emo")
    if filters.lang and lang_val == filters.lang:
        evidence.append(f"语种命中：{filters.lang}")
    if filters.want_instrumental and song.get("is_instrumental"):
        evidence.append("纯音乐命中：纯音乐")

    # 歌曲真实标签
    if genre:
        evidence.append("风格标签：" + " · ".join(genre))
    if mood:
        evidence.append("情绪标签：" + " · ".join(mood))
    if scene:
        evidence.append("场景标签：" + " · ".join(scene))

    # 只有浏览器真实播放并采样过才展示；没有数据时保持沉默，不用中性排序分
    # 冒充歌曲事实。beat_density 是瞬态密度，不宣称是精确 BPM。
    acoustic = song.get("audio_features") or {}
    if acoustic.get("energy_mean") is not None:
        evidence.append(
            "实播声学：能量 "
            f"{float(acoustic['energy_mean']):.2f} · "
            f"低频 {float(acoustic.get('low_ratio') or 0):.0%} · "
            f"明亮度 {float(acoustic.get('brightness') or 0):.0%}"
        )

    # 向量召回相似度（保留两位小数）
    score = song.get("score")
    if score is not None:
        evidence.append(f"向量召回相似度：{score:.2f}")

    return evidence


def _stable_variant(*values, size):
    """跨进程稳定选句式；不能用 Python hash（每次启动都会随机盐化）。"""
    raw = "|".join(str(value or "") for value in values).encode("utf-8")
    return int.from_bytes(hashlib.sha256(raw).digest()[:2], "big") % size


def build_dj_opener(songs, message):
    """为整轮歌单写一句短开场；不复述歌名，把逐首信息留给卡片与报幕。"""
    tags = []
    for song in songs:
        for field in ("mood", "genre", "scene"):
            for tag in song.get(field) or []:
                if tag and tag not in tags:
                    tags.append(tag)
    direction = "、".join(tags[:2]) or "这次的方向"
    count = len(songs)
    variants = (
        f"先不急着解释太多，这轮沿着{direction}往下听，{count}首已经排好。",
        f"你要的感觉我收到了：从{direction}切进去，这轮让歌自己往下说。",
        f"这一轮的线索是{direction}。我把{count}首接在一起，直接开听。",
        f"先把话留短一点——{direction}是这轮的底色，歌单已经接好。",
    )
    return variants[_stable_variant(message, direction, size=len(variants))]


def build_dj_intro(song, message, position):
    """用真实元数据/标签生成短而有差异的逐首兜底台词（禁止编造）。

    前端已经会报歌手与播放次序，因此兜底不再反复说“第一首/第二首”和用户
    原话，也不使用“回应你的频率”一类万能套话。稳定散列负责在多套结构间选句式，
    每句至少落到真实歌名、歌手、专辑或标签上。
    """
    title = song.get("title") or ""
    artist = song.get("artist") or ""
    album = song.get("album") or ""
    genre = song.get("genre") or []
    mood = song.get("mood") or []
    scene = song.get("scene") or []

    tags = []
    if genre:
        tags.extend(genre[:2])
    if mood:
        tags.extend(tag for tag in mood[:2] if tag not in tags)
    tag_part = "、".join(tags[:3])
    scene_part = scene[0] if scene else "现在"
    fact = tag_part or (album if album else "曲库里与描述最接近的一首")
    album_tail = f"，专辑是《{album}》" if album else ""
    variants = (
        f"{scene_part}的时候，我会留下{artist}的《{title}》；它在曲库里的关键词是{fact}。",
        f"《{title}》先从{fact}这条线进来。{artist}的这首歌{album_tail or '，先听歌本身'}。",
        f"想把{scene_part}留得更{fact}一点，可以听{artist}的《{title}》{album_tail}。",
        f"{artist}的《{title}》不多铺垫：{fact}，这是现有档案里最明确的方向。",
    )
    return variants[_stable_variant(song.get("id"), title, position, size=len(variants))]


def pick_dj_intros(dj_intros, picks, candidates, message, original_picks=None):
    """为每个 pick 取 dj_intro；LLM 缺失、越界或播放顺序错位时用真实标签兜底。

    LLM 的 dj_intro 会引用它自己决定的播放位置（如「第二首」「最后收尾」），但
    validate_and_fill 为防幻觉、补足数量和限制同歌手，可能按相似度重排最终 picks。
    若某歌曲的最终位置与 LLM 原计划不同，原文的位置叙事必然错位，必须按最终位置
    重新生成介绍；位置未变的歌仍可保留 LLM 的自然文案。

    此外，LLM 即便被 prompt 约束，仍可能越界写出「旋律、鼓点、编曲」等没有音频分析
    支撑的细节。检测到这些词也会走只引用真实元数据/标签的 build_dj_intro。
    """
    # 当前项目只拥有歌名、歌手、专辑与标签；以下词需要音频/歌词/可靠乐评才可确认。
    unsupported_audio_terms = (
        "旋律", "鼓点", "节奏", "音色", "编曲", "贝斯", "吉他",
        "副歌", "高潮", "唱腔", "制作", "和声", "人声处理",
    )
    by_id = {c["id"]: c for c in candidates}
    # 未传原始顺序时向后兼容：默认认为顺序未变。
    original_positions = {pid: index for index, pid in enumerate(original_picks or picks, 1)}
    result = {}
    for position, pid in enumerate(picks, 1):
        dj = None
        position_changed = original_positions.get(pid) != position
        if isinstance(dj_intros, dict) and not position_changed:
            raw = dj_intros.get(pid)
            if isinstance(raw, str) and raw.strip():
                candidate_intro = raw.strip()
                # 有未验证音频细节就视为无效，走确定性且诚实的兜底介绍。
                if not any(term in candidate_intro for term in unsupported_audio_terms):
                    dj = candidate_intro
        # 位置变化 / 文案缺失 / 文案越界：按最终播放位置生成安全介绍。
        result[pid] = dj or build_dj_intro(by_id[pid], message, position)
    return result


def build_song_discussion(song: dict, message: str) -> str:
    """基于真实档案自然介绍一首歌（song_discussion 意图，确定性、禁编造）。

    与 build_dj_intro 的区别：讨论是「介绍一首歌」而不是「推荐并说明播放位置」，
    所以不说「放在第几首」，只说它真实的气质/情绪/场景/语种/纯音乐属性。
    只允许引用真实字段：歌名/歌手/专辑/genre/mood/scene/language/is_instrumental；
    没有档案时诚实说明标签缺失，绝不编造歌词/编曲/制作/年份等未提供的事实。
    """
    title = song.get("title") or ""
    artist = song.get("artist") or ""
    album = song.get("album") or ""
    genre = song.get("genre") or []
    mood = song.get("mood") or []
    scene = song.get("scene") or []
    is_inst = bool(song.get("is_instrumental"))
    lang = reported_language(song)

    album_part = f"，收在专辑《{album}》里" if album else ""
    facts = []
    if is_inst:
        facts.append("是一首纯音乐")
    if genre:
        facts.append("气质偏" + "、".join(genre))
    if mood:
        facts.append("情绪带点" + "、".join(mood))
    if scene:
        facts.append(f"适合{scene[0]}的时候听")
    if lang and lang != "其他":
        facts.append(f"平台标注的语种为{lang}")

    if facts:
        fact_part = "，".join(facts)
    else:
        fact_part = "我这边档案里还没有它的风格标签，不敢乱猜"

    return (
        f"你问的《{title}》是{artist}的作品{album_part}。"
        f"{fact_part}。想听的话，跟我说一声就能直接播。"
    )


# 评测集约束键白名单：matches_constraints 不认识任何其他键，拼错直接报错
SUPPORTED_CONSTRAINT_KEYS = {
    "instrumental", "language",
    "genre_all", "genre_any", "genre_not_any",
    "mood_all", "mood_any", "mood_not_any",
    "scene_all", "scene_any", "scene_not_any",
    "artist_not_any",
    "min_results", "negative",
}


def matches_constraints(song: dict, constraints: dict) -> bool:
    """判断单首歌是否满足评测集的约束字典（硬约束判定）。

    constraints 可选键：
        instrumental: bool          纯音乐要求（true 必须纯音乐，false 必须非纯音乐）
        language: str               语种精确匹配
        genre_all: [str]            风格需包含全部
        genre_any: [str]            风格需包含至少一个
        genre_not_any: [str]        风格不得包含任一
        mood_all / mood_any / mood_not_any     同上，作用于情绪
        scene_all / scene_any / scene_not_any  同上，作用于场景
        artist_not_any: [str]       歌手不得包含任一子串
    未出现的键视为无约束；min_results / negative 是评测元信息，不在此判定。
    """
    for key in constraints:
        if key not in SUPPORTED_CONSTRAINT_KEYS:
            raise ValueError(f"未知约束键: {key}")

    if "instrumental" in constraints:
        if bool(song.get("is_instrumental")) != bool(constraints["instrumental"]):
            return False
    if "language" in constraints:
        if reported_language(song) != constraints["language"]:
            return False

    genre = set(song.get("genre") or [])
    mood = set(song.get("mood") or [])
    scene = set(song.get("scene") or [])

    for prefix, tagset in (("genre", genre), ("mood", mood), ("scene", scene)):
        if f"{prefix}_all" in constraints:
            if not set(constraints[f"{prefix}_all"]).issubset(tagset):
                return False
        if f"{prefix}_any" in constraints:
            if not tagset.intersection(constraints[f"{prefix}_any"]):
                return False
        if f"{prefix}_not_any" in constraints:
            if tagset.intersection(constraints[f"{prefix}_not_any"]):
                return False

    artist = song.get("artist") or ""
    for name in constraints.get("artist_not_any", []):
        if name in artist:
            return False
    return True


def count_satisfying(candidates: list, constraints: dict) -> int:
    """候选池里满足约束的歌有多少首——TopK 评测里『硬约束覆盖』的口径。"""
    return sum(1 for c in candidates if matches_constraints(c, constraints))


def build_known_why(song, filters):
    """生成『为什么选这首歌』的命中理由（确定性，只引用真实过滤项与相似度）。"""
    reasons = []
    if filters.moods and set(song.get("mood") or []).intersection(filters.moods):
        reasons.append("情绪标签命中「" + "/".join(filters.moods) + "」")
    if filters.genres and set(song.get("genre") or []).intersection(filters.genres):
        reasons.append("风格标签命中「" + "/".join(filters.genres) + "」")
    if filters.reference_genres:
        hits = {str(g).casefold() for g in song.get("genre") or []}.intersection(
            str(g).casefold() for g in filters.reference_genres)
        if hits:
            reasons.append("与参考档案的软风格线索重合「" + "/".join(sorted(hits)) + "」")
    score = song.get("score")
    if score is not None:
        label = "与参考歌曲文本档案相似度" if song.get("semantic_source") == "reference_embedding" else "与你的描述相似度"
        reasons.append(f"{label} {float(score):.2f}")
    return "；".join(reasons) if reasons else "检索结果里最接近你的描述"


def build_material_fallback(songs, query):
    """编造拦截后的兜底介绍（素材包驱动、变体句式、避免模板腔）。

    为什么变体而不是统一句式：连兜底文案都每首同一个模板，用户一眼出戏。
    这里用 position 取模从 3 套句式里轮换，相邻歌曲结构不同，同时保证
    内容 100% 来自真实字段（歌名/歌手/标签/命中理由）。

    为什么每套句式都以《书名号歌名》开头：agent_loop 的 _detect_fabrication
    会把《》书名号内容当作歌名嫌疑比对素材包，这里兜底文案直接以真实歌名
    起句，既让「起句结构」按 position 明显区分（甲/乙/丙前缀不同），又保证
    任何一句被复检时书名号内容都在素材包 known 集合内、绝不误伤自己。
    """

    def _tag_phrase(s):
        parts = []
        if s.get("genre"):
            parts.append("风格偏" + "、".join(s["genre"]))
        if s.get("mood"):
            parts.append("情绪带点" + "、".join(s["mood"]))
        return "，".join(parts) if parts else "气质和你要的很接近"

    templates = (
        lambda s, q: f"《{s['title']}》是{s['artist']}的作品，{_tag_phrase(s)}，正好对上你说的「{q}」。",
        lambda s, q: f"《{s['title']}》——{_tag_phrase(s)}，我放进来的原因是：{s.get('known_why') or '最接近你的描述'}。",
        lambda s, q: f"《{s['title']}》——{s['artist']}的歌，{_tag_phrase(s)}，放在这份歌单里刚刚好。",
    )
    return "\n".join(
        templates[i % len(templates)](s, query) for i, s in enumerate(songs)
    )


def build_song_material(songs, query, scene_opener=None):
    """把推荐歌曲的所有真实素材组装成素材包文本（LLM 唯一可取材的事实墙）。

    对应 spec 5.5.1：LLM 只能在素材包里取材——素材包没有的字段，LLM 无米之炊，
    从源头杜绝『无关歌曲的东西』。评论/歌词/命中理由都是确定性注入的真实内容。
    """
    lines = []
    if scene_opener:
        lines.append(f"【场景开场】{scene_opener}")
    lines.append("以下是你这次推荐的歌曲及所有已知事实（你只能谈论这些内容，禁止编造其他事实）：")
    for i, s in enumerate(songs, 1):
        head = f"{i}. 《{s['title']}》 - {s['artist']}"
        if s.get("album"):
            head += f"（专辑《{s['album']}》）"
        lines.append(head)
        tags = []
        if s.get("genre"):
            tags.append("风格:" + "/".join(s["genre"]))
        if s.get("mood"):
            tags.append("情绪:" + "/".join(s["mood"]))
        if s.get("scene"):
            tags.append("场景:" + "/".join(s["scene"]))
        if tags:
            lines.append("   标签：" + "；".join(tags))
        if s.get("known_why"):
            lines.append(f"   为什么选它：{s['known_why']}")
        k = s.get("knowledge")
        if k:
            if k.get("from_comments"):
                lines.append(f"   评论区的评价：{k['from_comments']}")
            if k.get("from_lyrics"):
                lines.append(f"   歌词主题：{k['from_lyrics']}")
    return "\n".join(lines)
