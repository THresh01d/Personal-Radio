"""app/agent_loop.py - Function Calling 主循环（Agent 大脑）

架构位置：main.py 只做 HTTP 编排；真正的『理解 + 决策 + 回复』在这里。

循环协议（事件格式）：
- {"type": "thinking", "round": n, "content": "..."}   每轮调用模型前
- {"type": "tool_call",   "round": n, "name", "arguments", "call_id"}
- {"type": "tool_result", "round": n, "name", "result"}
- {"type": "content", "content": "..."}                 最终回答
- {"type": "done", "stats": {...}}                     收尾统计

降级：模型异常时保留当前目的；明确点歌复用本地推荐，聊天仅说明能力受限。
"""

import json
import re
import time

from app.paths import load_project_env
from pathlib import Path

from app.retrieval import build_dj_opener, build_material_fallback, build_song_discussion, build_song_material
from app.scene import scene_from_query
from app.tools import TOOLS, execute_tool
from app.intent import looks_like_song_request, trim_music_invitation, colloquial_song_clause
from app.rain_context import rain_theme_request
from app.llm_provider import get_settings, request_chat
from app.recommendation_state import _intent_to_args, parse_intent, resolve_recommendation

ROOT = Path(__file__).resolve().parent.parent
load_project_env()

DEEPSEEK_TIMEOUT = 60          # 秒：模型思考 + 网络传输，放宽一些
MAX_ITERATIONS = 3             # 最多 3 轮，防 LLM 无限调工具跑飞

# 讨论文案消毒：以下信息我们没有可靠数据，LLM 一旦编造就整段替换为确定性档案介绍。
# 与 retrieval.pick_dj_intros 的越界词同一套口径，保证两处行为一致。
_UNSUPPORTED_AUDIO_TERMS = (
    "旋律", "鼓点", "节奏", "音色", "编曲", "贝斯", "吉他",
    "副歌", "高潮", "唱腔", "制作", "和声", "人声处理",
)
_YEAR_PATTERN = re.compile(r"(?:19|20)\d{2}")

# 表达视角池：每次请求随机注入一个，让 DJ 每次话术不一样。
# 每个视角只改语气、不改事实；素材包（事实墙）对任何视角都一样。
_VIEWPOINTS = {
    "深夜电台": "采用深夜电台的舒缓语气；这个表达视角不表示现在是夜晚，也不表示用户独处。",
    "老友闲谈": "像老友闲谈一样随意、亲切；不虚构认识多年的关系或共同往事。",
    "安静报歌单": "你话不多、不煽情：干脆利落地把歌单报出来，偶尔一句点睛，不啰嗦。",
    "挑剔乐评人": "你是懂行又挑剔的乐评人：眼光准、说得出门道，但不用术语砸人。",
}


def build_system_prompt(viewpoint=None):
    """构造 Agent 的系统提示：DJ 人格 + 表达规则 + 视角 + 工具边界 + 诚实规则。

    为什么规则写在 prompt 里而不是代码里：这些是『软行为』——表达重心、
    介绍深度、取材边界都是模型判断；硬校验（防幻觉 ID、同歌手限流、编造
    拦截）仍在工具与护栏层用代码钉死。prompt 只管表达分寸与『活人感』。
    """
    vp = _VIEWPOINTS.get(viewpoint, _VIEWPOINTS["老友闲谈"])
    return (
        "你是「私人电台」的电台 DJ，服务一位歌库里 5000 多首歌的用户。"
        "你的语气像懂 TA 的朋友：自然、温暖、不说教，全部用中文回复。\n\n"
        f"【本轮表达视角】{vp}\n\n"
        "你有 4 个工具查询 TA 的私人歌库：\n"
        "1. search_library：按语义检索候选歌曲（描述心情/场景/风格时先用它看看有什么）；\n"
        "2. get_artist_songs：精确查找某位歌手的歌（用户明确提到歌手时用）；\n"
        "3. get_song_profile：查看某首歌的档案（用户讨论某首歌『是什么感觉/介绍一下』时用）；\n"
        "4. recommend_playlist：生成最终播放歌单，**这是唯一返回正式歌单的工具**，"
        "返回 3-8 首已校验的歌曲，每首带标签、命中理由、评论/歌词素材。\n\n"
        "工具使用规则（必须遵守）：\n"
        "1. 点歌/推荐/换歌时调用 recommend_playlist 拿到真实歌单。工具会一次生成整轮短开场和逐首台词；"
        "调用后不要再另写一份逐首清单。**你回复里提到的每一首歌都必须来自本次调用的工具结果**；"
        "绝对禁止凭记忆、印象或训练数据编造歌名/歌手/专辑——歌库外或工具没返回的歌一律不写。\n"
        "2. 『换一首/再来/继续来/来点X』这类跟进请求同样是点歌：即使对话历史里有歌曲数据，"
        "也必须为当前请求重新调用 recommend_playlist，并带上新条件，不能直接沿用或编造历史里的歌。\n"
        "3. 歌手 0 命中（get_artist_songs 返回空列表）：必须如实告诉用户"
        "『你的歌库没有 TA 的作品』，建议去网易云收藏后同步，**严禁用相似歌曲硬凑**。\n"
        "4. 找不到符合条件的歌（检索返回空）：直接说明没有，**禁止编造**。\n"
        "5. 用户表达强烈的负面情绪但没有明确要求听歌时（如『我今天心情很差劲』"
        "『今天好烦』）：先自然回应，**不要硬塞歌单，也不固定询问是否需要歌**。"
        "用户明确想听歌时按音乐要求行动，不用情绪推测覆盖其方向。\n"
        "6. 对话有上下文时，引用前面聊到的内容（比如『换一首』指刚才的歌）。\n"
        "7. 聊偏好/口味、或你理解了用户想要的方向但暂时没打算出正式歌单时："
        "**不要口头罗列歌名当推荐**——没走 recommend_playlist 就等于没歌可播，"
        "会误导用户以为已经排队了。顺着用户聊，不在每轮结尾邀请听歌；"
        "只有影响本轮行动的关键歧义才简短问一次。用户转话题或要求先放歌时停止追问。\n"
        "8. 分享近况不等于同意长期保存或以后主动提及；沉默不表示认可，"
        "不得声称保存了当前工具不支持的记忆或诊断用户心理状态。\n\n"
        "9. 先回应本轮请求。用户只想聊天时，不自述‘唯一能做的是翻歌库’，不把话题都拉回音乐；"
        "明确询问能力或实际失败时才说明相应边界。歌曲场景/情绪标签不是用户处境，"
        "不得据此默认用户正在深夜独处、失眠或情绪低落。\n"
        "10. 用户说听得杂或什么都听，只能据此理解其类型广泛，不能推出私人回忆、性格或生活经历；"
        "不要用‘这类人一般都…’替用户下结论。举例须明确是假设，不能接着替用户编故事。"
        "每句话承接用户原话或已有话题，没有依据时简短回应或自然提问，不补无来由的转折。\n"
        "表达规则（这是你区别于模板机器的关键）：\n"
        "1. **每次开口都不一样**：不要用固定句式，不要每首歌都『这首…风格偏…情绪带点…』。\n"
        "2. **先判断这次聊什么**：用户情绪浓（低落/崩溃/开心到想分享）→ 先关心人再谈歌；"
        "点歌明确 → 按明确条件挑选，重点介绍歌；普通闲聊 → 自然接着聊。\n"
        "3. **推荐结果的最终回复只留 1-2 句整轮开场**：不编号、不复述歌名、不逐首解释；"
        "逐首内容已经由工具写入歌曲卡片和报幕。\n"
        "4. **只能谈论工具结果『material』里给出的内容**：可以基于标签做联想（氛围/画面感），"
        "但禁止编造歌名/歌手/年份/专辑等客观事实；也不描述旋律、鼓点、编曲等没有音频依据的"
        "听觉细节；评论与歌词主题只能转述 material 里的原话。\n"
        "5. 可以在结尾顺势抛一个相关话题（基于本次歌单/场景/素材），用户只想安静听歌时不抛。\n"
    )


def _last_user_content(messages):
    """取消息里最后一条 user 消息（降级兜底时作为推荐 query）。"""
    for m in reversed(messages):
        if m.get("role") == "user" and m.get("content"):
            return m["content"]
    return "随便听听"


# 口语引导词（可整体剥掉，露出真正要匹配的东西）。为什么组合匹配而不是单剥
# 『放』：『放几首keshi的歌』若只剥单个『放』，会剩『几首keshi』导致 0 命中
# 误拒绝；必须把『放几首/放点/想听几首』这类整体剥干净。两处共用同一份词表。
_ARTIST_LEAD_IN = re.compile(
    r"^(想听|我想听|我想|再听|多来|来几首|来点|来一首|来首|放几首|放点|播放|放|喜欢|听|来|"
    r"推荐|给我|我要|要|再)*"
)
_ARTIST_QTY = re.compile(r"^(?:(?:\d{1,2}|[一二两三四五六七八九十]{1,3})\s*首\s*|几首|几|一些|一点|点儿)")
# 跟进点歌中的人称指代。只接受带有「再来 / 继续」这类续播动作的整句，
# 避免把普通聊天里的「他」误当成上一位歌手。
_FOLLOW_UP_ARTIST = re.compile(
    r"^\s*(?:再来|多来|继续|再给我|再放|多放|给我再来).*(?:他|她|这位歌手|那个歌手)"
    r"\s*(?:的\s*(?:歌|歌曲|作品))?\s*[。！？!?，,]*\s*$"
)
# 只有续播动作和数量、没有任何新条件时，才继承上一轮的点歌条件。例如
# 「再来几首」应延续「emo rap、夜深了、声音轻一点」；「再来几首中文歌」
# 则是新条件，不能把上一轮的条件硬塞进去。
_BARE_FOLLOW_UP = re.compile(
    r"^\s*(?:再来|多来|继续|再给我|再放|多放|给我再来)"
    r"(?:几首|几|一些|一点|点儿|一首|首|点)?(?:歌|音乐)?\s*[。！？!?，,]*\s*$"
)


def resolve_follow_up_query(messages):
    """纯续播请求返回最近一条完整用户点歌条件，否则返回 None。"""
    user_messages = [
        (m.get("content") or "").strip()
        for m in messages
        if m.get("role") == "user" and m.get("content")
    ]
    if len(user_messages) < 2 or not _BARE_FOLLOW_UP.match(user_messages[-1]):
        return None
    # 连续说了多次「再来」时，跳过这些空条件，追溯到最近一条实际点歌内容。
    for previous in reversed(user_messages[:-1]):
        if not _BARE_FOLLOW_UP.match(previous):
            return previous
    return None


def _artist_request_guard(messages):
    """返回 (artist, hits) 或 None；空 hits 保留明确点名，不能变成无约束推荐。

    完整“X 的歌”先排除场景/风格结构，零命中仍限定 X；回复只说本地未找到
    匹配作品，不断言 X 的艺人身份。裸名字没有这个语法证据，仍只接受曲库命中。
    这让离线兜底与在线工具共享边界，不依赖模型主动调用 get_artist_songs。
    """
    from app.library import find_artist_hits
    from app.artist_request import is_non_artist_description
    from app.style_request import is_style_object

    user_msg = (_last_user_content(messages) or "").strip()
    # 只为歌手提取规范化已识别的当前点歌分句；原话仍完整进入意图与条件解析。
    # 避免把“先不听，还是给我几首安静”误当成一整个人名。
    user_msg = colloquial_song_clause(user_msg) or user_msg
    if rain_theme_request(user_msg):
        # 明确在按名称/主题找作品，不把“关于雨天”当成一位缺失歌手。
        return None
    user_msg = re.sub(r"[。！？!?，,\s]+$", "", user_msg)

    def _clean(text):
        """剥引导词 + 残留量词，露出歌手名候选。"""
        out = _ARTIST_LEAD_IN.sub("", text).strip()
        out = _ARTIST_QTY.sub("", out).strip()
        out = re.sub(r"^(?:歌手|艺人|乐队|组合)\s*[:：]?\s*", "", out)
        # 「孙燕姿的其他歌」是明确点名，不是歌手名的一部分。这里仅剥点歌尾巴，
        # 让精确查找仍以「孙燕姿」为准，避免被误判成不存在的歌手。
        out = re.sub(r"的?(?:其他|别的|不一样的)?(?:歌|歌曲|作品)$", "", out).strip()
        return out

    def _style_or_multi(text):
        """该候选是场景/风格词，或含多人分隔符 → 不是单一歌手点名。"""
        from app.retrieval import STYLE_KEYWORDS
        if any(sep in text for sep in ("和", "与", "、", "/", ",", "&")):
            return True
        bare = re.sub(r"(一点|一些|点儿|些|风格|类型)$", "", text).strip()
        return bare.lower() in STYLE_KEYWORDS or is_style_object(bare)

    # 指代优先于“他的歌”结构；只跨越空续播，不跨越最近一次新的点歌方向。
    if _BARE_FOLLOW_UP.match(user_msg) or _FOLLOW_UP_ARTIST.match(user_msg):
        user_history = [m for m in messages if m.get("role") == "user" and m.get("content")]
        for previous in reversed(user_history[:-1]):
            previous_text = previous["content"].strip()
            if _BARE_FOLLOW_UP.match(previous_text) or _FOLLOW_UP_ARTIST.match(previous_text):
                continue
            previous_guard = _artist_request_guard([previous])
            if previous_guard is not None:
                return previous_guard
            if looks_like_song_request(previous_text):
                break
        return None

    name = None
    m = re.fullmatch(r"(.+?)的\s*(?:其他|别的|不一样的)?(?:歌曲|作品|歌)", user_msg)
    if m:
        # 剥掉引导前缀和残留量词，留下真正的歌手名。
        name = _clean(m.group(1))
    else:
        # 裸歌手名信号：剥完引导词后整句恰好是歌库歌手才收（带上下文就放行）。
        candidate = _clean(user_msg)
        candidate = re.sub(r"[。！？!?，,\s]+$", "", candidate).strip()
        if candidate and not _style_or_multi(candidate):
            # 试探式调用：只有真的命中歌库歌手才返回，0 命中当没说过（防误伤）
            hits = find_artist_hits(candidate)
            if hits:
                return (candidate, hits)

    if not name:
        return None
    if _style_or_multi(name):
        return None
    hits = find_artist_hits(name)
    if not hits and is_non_artist_description(name):
        return None
    return name, hits


def _missing_artist_reply(artist):
    """零命中只说明曲库查找结果，不以正则猜测艺人身份或平台全量库存。"""
    return (
        f"你的本地歌库未找到与「{artist}」匹配的歌手作品，我先不拿其他人的歌凑数。"
        "如果你指的是歌手，可以核对名字或收藏后同步；如果指的是场景或风格，请再描述一下。"
    )


def call_deepseek(messages, tools=None):
    """调用用户当前选择的模型，返回 Agent 统一响应格式。

    返回格式（供 run_agent 消费）：
        {"content": str | None, "tool_calls": [{"id","name","arguments"}], "usage": {...}}

    OpenAI 兼容协议的 tool_calls.arguments 是 JSON 字符串，这里解析成 dict。
    函数名暂时保留以兼容既有测试和注入点；实现已经与 DeepSeek 解耦。
    异常直接上抛，由 run_agent 统一降级。
    """
    data = request_chat(
        messages,
        tools=tools,
        temperature=0.9,
        max_tokens=1500,
        timeout=DEEPSEEK_TIMEOUT,
    )
    msg = data["choices"][0]["message"]
    tool_calls = []
    for tc in msg.get("tool_calls") or []:
        fn = tc.get("function") or {}
        try:
            args = json.loads(fn.get("arguments") or "{}")
        except (ValueError, TypeError):
            args = {}
        tool_calls.append({"id": tc.get("id"), "name": fn.get("name"), "arguments": args})
    usage = data.get("usage") or {}
    return {
        "content": msg.get("content"),
        "tool_calls": tool_calls or None,
        "usage": {
            "prompt_tokens": usage.get("prompt_tokens", 0),
            "completion_tokens": usage.get("completion_tokens", 0),
            "total_tokens": usage.get("total_tokens", 0),
        },
    }


def degraded_answer(message, songs):
    """降级回答：用真实元数据完成离线推荐，不把主动离线说成系统故障。

    main.py 的异常兜底与 run_agent 内部的 LLM 降级共用同一份文案，
    保证两种降级路径输出一致。
    """
    offline = get_settings().get("provider") == "offline"
    if not songs:
        return "本地曲库里暂时没找到合适的歌，我先不拿无关歌曲凑数。"
    opener = build_dj_opener(songs, message)
    if offline:
        return f"{opener}（当前使用离线基础模式。）"
    return f"AI DJ 暂时没连上，{opener}（已切换本地降级模式。）"


def degraded_chat_answer():
    """聊天失败只说明能力受限，不借异常把倾诉改成音乐任务。"""
    if get_settings().get("provider") == "offline":
        return "当前使用离线基础模式，暂时无法展开聊天。"
    return "聊天服务暂时不可用，稍后可以再聊。"


def _max_iterations_reply():
    """跑满 max_iterations 仍没出最终回答时的安全兜底。"""
    return (
        "这个需求有点绕，我还没完全理顺。"
        "可以换个说法试试。"
    )


def _detect_fabrication(content, songs):
    """检测 DJ 文案是否编造客观事实（年份 / 音频细节 / 素材包外歌名）。

    返回 True 则触发兜底重写。为什么只检这三类：
    - 年份：客观事实，正则可钉死；
    - 音频词：需要听觉依据（旋律/鼓点/音色），我们没有音频分析；
    - 《》书名号歌名：模型编造歌名最常用的形态，比对素材包内真实歌名+专辑。
    歌手/专辑若伴随编造歌名必被书名号命中；单靠 prompt 取材约束拦剩余。
    """
    if _YEAR_PATTERN.search(content):
        return True
    if any(t in content for t in _UNSUPPORTED_AUDIO_TERMS):
        return True
    if songs:
        known = {s.get("title") for s in songs} | {
            s.get("album") for s in songs if s.get("album")
        }
        for t in re.findall(r"[《「]([^》」]{1,30})[》」]", content):
            if t not in known:
                return True
    return False


# ---------- 无工具轮次的口播书名号守卫（要歌不猜） ----------

# 句句都被删、连安全句子都留不下时的整段兜底（仍不含任何歌名）。
_ASK_ONLY_REPLY = (
    "我想顺着你说的感觉再往下走，但不想随便报几首还没排进歌单的歌名糊弄你。"
    "要不你点个头，我按你的话去歌库里认真挑几首、排成能直接播的歌单？"
)

# ---------- 要歌不猜（严格）：非点歌标记轮次禁止自动出歌单 ----------

# DJ 在用户没明确点歌（消息无强标记）时，即便模型想直接调 recommend_playlist 也被
# 拦下转成反问。为什么必须确定性拦截而不是靠 prompt：『猜方向直接排歌』正是用户最初
# 不满的行为——方向猜对是运气、猜错就是自作主张。有标记 = 用户已授权出歌单；无标记
# = 还没授权，一律先聊+反问，等用户点头（main 的 consent 直通）再真正出歌单。
_ASK_DIRECTION_MSG = (
    "我大概接住你说的方向了。不过挑歌这种事我想先跟你对一下，免得我自作主张排错调子"
    "——要不要我按你刚才说的，从你歌库里认真挑几首、排成能直接播的歌单？你点个头，我马上排。"
)

# 没有明确音乐动作时，护栏收束当前回复，不把每次拦截都变成推荐邀请。
_CHAT_ONLY_REPLY = "我在，先听你说。"


def _quoted_names(text):
    """取文案里《》/「」标注的名称（与 _detect_fabrication 同口径的正则）。"""
    return set(re.findall(r"[《「]([^》」]{1,30})[》」]", text or ""))


def _guard_unqueued_picks(content, context_text):
    """无任何工具结果时，DJ 口播的书名号歌名必须是会话里出现过的，否则删句。

    为什么只查书名号：模型『口头配歌单』最常用的形态就是书名号列举（既可能编造
    库外歌名，也可能是把库内真实歌名当已排队来口播）；两种都违背『列出的每首歌都
    来自工具结果』的契约。书名号也是全文最可信的『指称一首歌』信号，错杀面最小。

    允许集 = 会话里出现过的歌名，两种形态都算：
    1. 带书名号提过的（DJ 上一轮已落库的歌单、用户自己《》标注的歌）——
       直接用 _quoted_names(context_text) 取；
    2. 用户口语裸提的（如「我更喜欢半句再见这种伤一点的」，正文无书名号）——
       只要 DJ 引用的书名号名作为连续子串出现在会话正文里即放行。为什么补这条：
       无书名号提歌名是用户常态（真正聊天谁会打《》），DJ 顺着用户原话自然衔接
       不该被判越权；越权口播（《比较爱》这种哪都没出现过的）两种都匹配不上。
       长度 ≥2 才作数，避免「喜欢下雨天」里的单字被误认成歌名《雨》。
    返回 (kept_or_None, dropped)：dropped=True 表示确实删了违规句；kept_or_None
    为删除后可保留句子，None 表示一句都没留下（调用方走 _ASK_ONLY_REPLY 整段兜底）。
    """
    context_text = context_text or ""
    allowed = set(_quoted_names(context_text))
    for name in re.findall(r"[《「]([^》」]{1,30})[》」]", content):
        if len(name) >= 2 and name in context_text:
            allowed.add(name)
    sentences = [s.strip() for s in re.split(r"(?<=[。！？!?])\s*|\n+", content) if s.strip()]
    if not sentences:
        return content, False
    keep = []
    for s in sentences:
        # 违规 ⇔ 这句口播了会话里没出现过的书名号名
        if _quoted_names(s) - allowed:
            continue
        keep.append(s)
    if len(keep) == len(sentences):
        return content, False  # 没有删任何句：纯聊天/纯讨论，原样放行
    return ("\n".join(keep) if keep else None), True


# 无工具轮口播假歌单的『编号列举』形态。实测模型会用「1. 歌名 — 歌手」这种
# 无书名号的列表逃过上面的《》检测——一样是『看似配好了、其实一首没落库』。
# 判定一条行是不是歌单条目：行首数字序号 + 名称 + 歌手分隔符（—/–/-）。
_SONG_ITEM_LINE = re.compile(
    r"(?:^|\n)\s*\d{1,3}\s*[.、)）]\s*[《「]?[^\n《》—–\-]{1,60}?[》」]?\s*[—–]\s*\S"
)
# 兜底：即使没带歌手分隔符，≥3 条连续编号列举也明显是歌单，不是闲聊
_LIST_ITEM_LINE = re.compile(
    r"(?:^|\n)\s*(?:\d{1,3}|[一二三四五六七八九十]{1,2})\s*[.、)）]\s*\S"
)


def _presents_fake_queue(content):
    """无工具内容是否在『以列举形式口播未落库歌单』。

    为什么整段换反问而不是只删条目句：无工具轮本就没有合法歌单，DJ 却端出一串
    编号歌单（哪怕名字全来自会话）都违背契约；保留条目前那些『我挑了三首』的
    引导句反而会留下『已配好』的假象。直接给确定性反问，最坏情况也只是多问一句。
    """
    text = content or ""
    if not text:
        return False
    if _SONG_ITEM_LINE.search(text):
        return True
    return len(_LIST_ITEM_LINE.findall(text)) >= 3


def _guard_no_tool_content(content, messages):
    """无工具轮次的内容安全出口（要歌不猜）。

    为什么单独一条出口：有工具结果的轮次，_detect_fabrication 会用本轮真实歌单做
    『已知集』拦书名号编造；但模型这一轮可能一个工具都没调（songs 空、profile 空），
    旧代码里书名号检测随之失效，于是出现『口头配了一组歌、其实一首都没落库』的裸奔。
    这里三道消毒在同一轮生效：
    0. 假歌单列举守卫 _presents_fake_queue：删掉未落库列表，不能残留已配好的假象；
    1. 书名号口播守卫 _guard_unqueued_picks：删会话里没出现过的《》口播句；
       仍保留安全回应，聊天不因删句而被追加音乐邀请；
    2. 年份/音频细节句级消毒：与有工具轮次同一套规则，防无工具时编客观事实。
    全删光 → 确定性 _ASK_ONLY_REPLY（不含任何歌名，最坏情况也诚实、也把决定权交回用户）。
    """
    music_requested = looks_like_song_request(_last_user_content(messages))
    if _presents_fake_queue(content):
        return _ASK_DIRECTION_MSG if music_requested else _CHAT_ONLY_REPLY
    context_text = "\n".join(m.get("content") or "" for m in messages)
    kept, dropped_picks = _guard_unqueued_picks(content, context_text)
    # 剩余句子再过一遍年份/音频细节（_sanitize_sentences(songs=[]) 只查这两类，
    # 不会误删合法书名号）；全被删时置空走整段兜底。
    if kept:
        cleaned = _sanitize_sentences(kept, [])
        if cleaned:
            kept = cleaned
        else:
            kept = None
    if kept is None:
        return _ASK_ONLY_REPLY if music_requested else _CHAT_ONLY_REPLY
    # 删掉编造仍保留安全回应；聊天不因护栏介入而被追加新的音乐邀请。
    return kept if music_requested else trim_music_invitation(kept) or _CHAT_ONLY_REPLY


def _sanitize_discussion(content, profile_songs, message):
    """讨论场景的最终文案消毒：LLM 编造事实（年份/音频细节/档案外歌名）→ 替换为确定性档案介绍。

    为什么只对讨论场景做：正式歌单的 DJ 介绍由 recommend_playlist 工具内的
    build_dj_intro 保证（确定性，不走 LLM 自由发挥）；而 get_song_profile 讨论
    路径是 LLM 基于档案自由回答，需要这道确定性防线——模型即便被 prompt 约束
    仍可能说出『2019 年的专辑』『旋律低沉』这类我们没有数据支撑的话。

    注意：这是『全部句子都越界』时的整段兜底；正常情况优先走 _sanitize_sentences
    句子级消毒，只在连一句合法内容都留不下时才退到这里。
    """
    if not profile_songs:
        return content
    # 统一用 _detect_fabrication 判定（含《》书名号歌名检测，比旧版年份/音频词更全）
    if not _detect_fabrication(content, profile_songs):
        return content
    try:
        return build_song_discussion(profile_songs[0], message)
    except Exception:
        # 兜底失败时宁可用原文也不让链路崩掉
        return content


def _sanitize_sentences(content, songs):
    """句子级消毒：只删除含编造点的句子，合法句子原样保留。

    为什么按句消毒而不是整段降级：LLM 讨论/介绍一首歌时提到『2003年的歌』
    『副歌那句歌词』几乎必然——这些单独看无法验证（song_profiles 无年份字段、
    歌词只提炼成主题不存原文）、可能是编造，但整段降级会把 95% 的真实内容
    （专辑、标签、歌词主题、推荐建议）一起连坐丢掉，用户看到的只剩机器模板，
    『LLM 唯一作者』名存实亡。这里按中文句号/换行切句，逐句跑 _detect_fabrication，
    只删越界句——编造风险被摘除，其余真实内容保留；全部越界时返回空串，
    由调用方走整段兜底（build_material_fallback / build_song_discussion）。
    """
    # 中文句号/感叹/问号后切分（保留标点在句尾），并按换行分段
    sentences = re.split(r"(?<=[。！？!?])\s*|\n+", content)
    parts = [s.strip() for s in sentences if s.strip()]
    clean = [s for s in parts if not _detect_fabrication(s, songs)]
    if len(clean) == len(parts):
        return content  # 没有句子被删：原样返回，保留原始段落/换行格式
    return "\n".join(clean)


def run_agent(messages, *, tools=TOOLS, llm_client=None, max_iterations=MAX_ITERATIONS,
              session_id=None):
    """手写 Function Calling 主循环（生成器，yield 事件，最后一个事件是 done）。

    参数：
        messages: OpenAI 格式消息列表（含 system + 历史 + 当前 user）；
                  循环内会原地追加 assistant(tool_calls) 与 tool 结果消息；
        tools:    工具 schema 列表（默认全量注册表）；
        llm_client: 可注入的模型调用函数（测试传假模型，默认走 DeepSeek）；
        max_iterations: 最大轮数，防止 LLM 无限调工具；
        session_id: 会话上下文（透传给工具做防重复排除），无会话时省略。
    """
    if llm_client is None:
        llm_client = call_deepseek
    start = time.monotonic()
    stats = {
        "iterations": 0,
        "tool_calls": 0,
        "total_tokens": 0,
        "elapsed_ms": 0,
        "degraded": False,
        "steps": [],
    }
    # 讨论场景：get_song_profile 返回的真实档案，用于最终文案消毒
    profile_songs = []
    # 推荐场景：recommend_playlist 返回的真实歌单，用于编造拦截与兜底②重写
    playlist_songs = []

    for i in range(max_iterations):
        stats["iterations"] = i + 1
        yield {"type": "thinking", "round": i + 1, "content": f"第 {i+1} 轮：分析用户意图"}

        try:
            resp = llm_client(messages, tools=tools)
        except Exception as e:  # 网络/限流/无Key也必须保留当前聊或听歌的目的。
            from app.diagnostics import note, llm_failure_code
            reason_code = llm_failure_code(e)
            note(reason_code)
            from app.safe_logging import log_failure
            log_failure("llm_fallback", e)
            if not looks_like_song_request(_last_user_content(messages)):
                stats["degraded"] = True
                stats["degradation_code"] = reason_code
                stats["steps"].append("degraded:chat")
                yield {"type": "content", "content": degraded_chat_answer()}
                break
            user_msg = resolve_follow_up_query(messages) or _last_user_content(messages)
            guard = _artist_request_guard(messages)
            fallback_args = {"query": user_msg}
            if guard is not None:
                fallback_args["artist"] = guard[0]
            fallback_args, fallback_intent = resolve_recommendation(
                session_id, _last_user_content(messages), fallback_args
            )
            yield {
                "type": "tool_call", "round": i + 1, "name": "recommend_playlist",
                "arguments": fallback_args, "call_id": "degraded",
            }
            result = execute_tool("recommend_playlist", fallback_args, session_id=session_id)
            yield {
                "type": "tool_result", "round": i + 1, "name": "recommend_playlist",
                "result": result, "recommendation_intent": fallback_intent,
            }
            stats["tool_calls"] += 1
            stats["steps"].append("degraded:recommend_playlist")
            stats["degraded"] = True
            stats["degradation_code"] = reason_code
            answer = (_missing_artist_reply(guard[0]) if guard is not None and not guard[1]
                      else degraded_answer(user_msg, result.get("songs") or []))
            yield {"type": "content", "content": answer}
            break

        usage = resp.get("usage") or {}
        stats["total_tokens"] += usage.get("total_tokens") or 0

        tool_calls = resp.get("tool_calls") or []
        if tool_calls:
            # 一次响应可能带多个工具调用：assistant 消息只追加一条，
            # 后面跟每个调用的 tool 结果消息（OpenAI 多工具约定）。
            assistant_msg = {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": tc.get("id") or f"call_{j}",
                        "type": "function",
                        "function": {
                            "name": tc["name"],
                            "arguments": json.dumps(tc.get("arguments") or {}, ensure_ascii=False),
                        },
                    }
                    for j, tc in enumerate(tool_calls)
                ],
            }
            messages.append(assistant_msg)
            short_circuited = False  # 守卫短路标志：置位后终止整个循环
            for j, tc in enumerate(tool_calls):
                name = tc["name"]
                args = tc.get("arguments") or {}
                call_id = tc.get("id") or f"call_{j}"
                # 模型看得到历史，但不能把「再来几首」这种空条件原样交给检索。
                # 对纯续播强制继承上一轮条件；带有新条件的续播不会命中该规则。
                if name == "recommend_playlist":
                    follow_up_query = resolve_follow_up_query(messages)
                    if follow_up_query:
                        args = dict(args)
                        args["query"] = follow_up_query
                # 守卫 0.5（要歌不猜·严格）：当前 user 消息不带强点歌标记（想听/来几首…）
                # 时，模型即便主动调 recommend_playlist 也属于『猜方向』——用户裁定不猜，
                # 一律拦下并简短回应（不执行工具、不出歌单）；已有邀请的点头仍由consent直通。
                if name == "recommend_playlist" and not looks_like_song_request(_last_user_content(messages)):
                    stats["steps"].append("guard:chat_no_recommendation")
                    yield {"type": "content", "content": _CHAT_ONLY_REPLY}
                    short_circuited = True
                    break
                stats["tool_calls"] += 1
                stats["steps"].append(f"tool:{name}")
                # 守卫 0：明确点名必须保留范围，零命中也不能放行去推荐其他歌手。
                if name in ("recommend_playlist", "search_library"):
                    args = {key: value for key, value in args.items() if not key.startswith("_")}
                    guard = _artist_request_guard(messages)
                    if guard is not None:
                        artist, hits = guard
                        if not hits:
                            refuse = _missing_artist_reply(artist)
                            stats["steps"].append(f"guard:artist_zero_hit:{artist}")
                            yield {"type": "content", "content": refuse}
                            short_circuited = True
                            break
                        # 歌手存在：把 artist 限定注入工具参数（覆盖模型可能漏传的情况）
                        args = dict(args)
                        args["artist"] = artist
                        stats["steps"].append(f"guard:artist_filter:{artist}")
                    if name == "search_library":
                        intent, _ = parse_intent(_last_user_content(messages), args)
                        if intent.get("style_request") and looks_like_song_request(_last_user_content(messages)):
                            args = _intent_to_args(intent, args)

                recommendation_intent = None
                if name == "recommend_playlist":
                    # 最终工具参数先经过持久化会话状态：续播继承未修改条件，
                    # 并强制排除最近推荐；普通新请求不继承旧方向。
                    args, recommendation_intent = resolve_recommendation(
                        session_id,
                        _last_user_content(messages),
                        args,
                    )
                # tool_call 事件必须暴露真正执行的最终参数，而不是守卫注入前的草稿。
                yield {"type": "tool_call", "round": i + 1, "name": name, "arguments": args, "call_id": call_id}
                result = execute_tool(name, args, session_id=session_id)
                # 素材包注入：recommend_playlist 结果回填前，把 songs 组装成
                # 『事实墙』文本（build_song_material），LLM 下一轮只能取材于此。
                # 场景 opener 来自场景策略（scene_from_query），评论/歌词来自 knowledge。
                if name == "recommend_playlist":
                    # 推荐场景：记录真实歌单（供最终文案的编造拦截与兜底②重写），
                    # 同时把素材包文本塞进 result——LLM 取材的唯一事实墙。
                    playlist_songs = (result or {}).get("songs") or []
                    # 素材包要求每首歌有歌名+歌手（事实墙必须能引用真实字段）；
                    # 缺字段的残缺结果（测试 mock 或异常数据）跳过注入，不让回填崩掉。
                    # 生产路径 _recommend_playlist_handler 恒返回完整字段，守卫只为防御。
                    if playlist_songs and all(
                        s.get("title") and s.get("artist") for s in playlist_songs
                    ):
                        query = args.get("query") or _last_user_content(messages)
                        strategy = scene_from_query(query)
                        result = dict(result)
                        result["material"] = build_song_material(
                            playlist_songs, query, strategy.opener if strategy else None
                        )
                tool_result_event = {
                    "type": "tool_result", "round": i + 1, "name": name, "result": result,
                }
                if recommendation_intent is not None:
                    tool_result_event["recommendation_intent"] = recommendation_intent
                yield tool_result_event

                if name == 'remember_preference':
                    yield {'type': 'content', 'content': result.get('message') or '偏好没有确认保存。'}
                    short_circuited = True
                    break

                # recommend_playlist 已在一次文案调用中同时生成整轮 opener 与逐首台词。
                # 这里直接收束，避免再让 Agent 写一遍编号歌单，造成用户看到/听到两层套话。
                if name == "recommend_playlist":
                    if playlist_songs:
                        opener = (result or {}).get("dj_opener") or build_dj_opener(
                            playlist_songs, args.get("query") or _last_user_content(messages)
                        )
                    else:
                        opener = "这次本地曲库里没有足够贴合的候选，我先不拿无关歌曲凑数。"
                    stats["steps"].append("copy:single_pass")
                    yield {"type": "content", "content": opener}
                    short_circuited = True
                    break

                # 守卫 1：歌手 0 命中 → 确定性短路诚实拒绝。
                # 为什么不依赖 prompt：模型可能无视『必须拒绝』继续调 recommend_playlist 硬凑，
                # 硬凑的歌词单比『没满足需求』更伤信任。这里在工具层直接终止循环。
                if name == "get_artist_songs" and not (result.get("songs")):
                    artist = (args.get("artist") or "").strip() or "这位歌手"
                    refuse = (
                        f"你的歌库没有找到「{artist}」的作品，所以我不想用无关歌曲凑数。"
                        "你可以在网易云把 TA 的歌收藏后点「同步歌单」，下次我就能从你的歌库里推荐给你。"
                    )
                    stats["steps"].append(f"guard:artist_zero_hit:{artist}")
                    yield {"type": "content", "content": refuse}
                    short_circuited = True
                    break

                # 讨论场景：记录真实档案，供最终文案消毒
                if name == "get_song_profile" and result.get("songs"):
                    profile_songs = result["songs"]

                # 工具结果回填：LLM 下一轮能看到它刚调的工具返回了什么
                messages.append(
                    {"role": "tool", "tool_call_id": call_id, "content": json.dumps(result, ensure_ascii=False)}
                )
            if short_circuited:
                break  # 守卫已给出确定性答复，跳出外层循环
            continue

        content = resp.get("content")
        if content:
            if profile_songs or playlist_songs:
                # 守卫 2：文案消毒（本轮有真实工具结果）——讨论路径拦编造（年份/音频/
                # 书名号），推荐路径拦素材包外编造并兜底重写（LLM 唯一作者，护栏只拦不写）。
                if _detect_fabrication(content, profile_songs or playlist_songs):
                    # 优先句子级消毒：只删含编造点的句子，合法句子保留（不整段连坐）。
                    # 全部句子都越界（cleaned 为空）才退到整段兜底②，保证最坏情况
                    # 输出也是素材包驱动的真实介绍，绝不留素材包外内容。
                    cleaned = _sanitize_sentences(content, profile_songs or playlist_songs)
                    if cleaned:
                        content = cleaned
                    elif playlist_songs:
                        content = build_material_fallback(
                            playlist_songs, _last_user_content(messages)
                        )
                    else:
                        content = _sanitize_discussion(content, profile_songs, _last_user_content(messages))
            else:
                # 守卫 3：本轮一个工具都没调（songs/profile 全空）→ 口播书名号歌名
                # 一律按『要歌不猜』消毒，杜绝『口头配了歌单却一首没落库』裸奔。
                content = _guard_no_tool_content(content, messages)
            yield {"type": "content", "content": content}
            break
        # 既无工具调用也无文本（异常返回）：给兜底文案，避免空回复
        yield {"type": "content", "content": "我还在摸索怎么帮你，换个说法试试？"}
        break
    else:
        # for 循环跑满 max_iterations 还没 break：安全退出，不让循环失控
        yield {"type": "content", "content": _max_iterations_reply()}

    stats["elapsed_ms"] = int((time.monotonic() - start) * 1000)
    yield {"type": "done", "stats": stats}
