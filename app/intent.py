"""app/intent.py - 确定性意图闸门（Agent 化重构后）

架构位置：语义理解（这是什么请求、要什么风格/情绪）已交给 Agent（LLM）。
本模块只保留两个 **100% 确定**的确定性意图判定，均「规则不确定 → 返回 None /
False，交给 Agent 裁决，禁止猜」：

1. fast_reply：纯社交寒暄（你好/谢谢/再见等）→ 模板回复，省一次 LLM 调用；
2. looks_like_song_request：用户消息明显在点歌/换歌（命中强点歌标记）。

为什么两处都放这里：main（HTTP 编排）与 agent_loop（Agent 大脑）都要判断
『这条消息是不是在点歌』——守卫层用它决定是否重写歌单，表达层用它决定是否
补反问。放独立模块避免 main/agent_loop 互相 import 成环，也让『点歌判定』有
唯一的真源、可被单测钉死。

注意：这里只放强标记与纯寒暄；情绪倾诉（我今天心情很差劲）可能是想听歌，
不是 100% 确定，必须交给 Agent 判断。
"""

import re
from app.rain_context import rain_short_request, rain_theme_command
from app.style_request import standalone_request

# 只放 100% 确定的纯社交寒暄。判断用『整句清理后 ∈ 集合』而不是子串，
# 保证「你好，想听周杰伦的歌」不会因为含「你好」就误入快速通道。
_PURE_CHITCHAT = {
    # 问候
    "你好", "您好", "嗨", "哈喽", "hello", "hi", "在吗", "在的",
    "你好呀", "你好啊", "您好呀", "哈喽你好", "hi你好", "hello你好",
    "早上好", "中午好", "下午好", "晚上好",
    # 感谢
    "谢谢", "感谢", "辛苦了", "谢谢啦", "谢谢你", "感谢感谢", "多谢", "多谢啦",
    # 告别 / 道晚安
    "晚安", "再见", "拜拜", "再见啦", "拜拜啦", "晚安好梦",
}


def _clean(message: str) -> str:
    """去掉空白与常见标点，只留正文，用于集合判断。"""
    return re.sub(r"[\s，。！？!?~～、…·,.]", "", (message or "").strip().lower())


# ---------- 歌单契约守卫：强点歌标记 ----------

# 明确点歌/换歌的强信号。只放「用户一定在要歌」的标记；
# 情绪倾诉（我今天心情很差劲）不含这些词，不会误触发。
RECOMMEND_MARKERS = (
    "想听", "再听", "来几首", "来点", "来一首", "来首", "推荐", "换一首",
    "换几首", "再来", "继续", "放点", "放几首", "点歌", "帮我找", "来几首",
)

# 带明确数量的点歌同样是强意图。「来5首/来五首」不包含固定短语“来几首”，
# 过去会被误当成方向不明确而要求用户再次确认。
_COUNTED_RECOMMEND = re.compile(
    r"(?:想听|再听|来|放|推荐|给我)\s*(?:上)?\s*"
    r"(?:\d{1,2}|[一二两三四五六七八九十]{1,3})\s*首"
)
# 完整的动作 + 作品对象也是明确点歌；锚定整句，不能把“听 X 的歌怎么样”当点播。
_PLAY_WORKS = re.compile(
    r"^\s*(?:放|播放|听)\s*.+的\s*(?:其他|别的|不一样的)?"
    r"(?:歌曲|作品|歌)\s*[。！？!?，,]*\s*$"
)


# 短控制只接受整句，避免把“你为什么说先放歌”当作执行命令。
_DIRECT_MUSIC_CONTROL = re.compile(
    r"(?:(?:别(?:再)?聊了|不聊了|先不聊了|这次少说点|少说点|不用介绍了|不用说了)[，,\s]*)*"
    r"(?:先|直接)?(?:放歌|播放音乐|放音乐|听歌)(?:吧|就行|就好)?[。！!\s]*"
)
_CHAT_ONLY = re.compile(
    r"(?:先|暂时|现在)?(?:不想听|不听|别放|不要放|不用放)(?:任何)?(?:歌|音乐)"
    r"|(?:别|不要|不用)(?:再)?推荐(?:歌|音乐|了|[，,。！？!?\s]|$)"
    r"|(?:先|只想|只要|就想|只)(?:陪我)?聊(?:聊|两句|天)?"
    r"|(?:继续|接着)(?:陪我)?(?:聊|说|讲)"
    r"|(?:想听|听)(?:你|您)(?:说|聊|讲)"
    r"|(?:先|暂时)?不(?:要|用)?放歌"
)
_INVITATION = re.compile(
    r"(?:要不要|想不想|需要我|要不我|要不你点个头)[^。！？!?\n]*"
    r"(?:听歌|听点|放歌|放几首|多放几首|挑几首|排.*歌单|歌库|歌单)[^。！？!?\n]*[。！？!?]?"
)


def direct_music_control(message: str) -> bool:
    """明确停止聊天并听歌的短句，不改变任何持久表达设置。"""
    return bool(_DIRECT_MUSIC_CONTROL.fullmatch((message or "").strip()))


def _request_text(message):
    # 引号内的例句、歌名是数据，不当作当前操作；保留其余文本用于判断最后意图。
    text = re.sub(r'《[^》]*》|「[^」]*」|“[^”]*”|"[^"\n]*"',
                  lambda match: " " * len(match.group()), message or "")
    # 讨论命令和否定命令不是执行授权；按分句处理，保留后续明确改口。
    return re.sub(r'(?:^|(?<=[，,。；;！？!?]))\s*'
                  r'(?:为什么|为何|你(?:刚才|之前)?说|如果|假如|'
                  r'(?:请)?(?:不要|别|不用|不必)(?:再)?(?:给我|帮我|替我))'
                  r'[^，,。；;！？!?]*', '', text)


# “情绪说唱”本身已是音乐对象，不必额外说“的歌”。这里仅识别点歌授权，
# 不把这些修饰词映射成档案或排序条件；封闭修饰词和整句匹配避免吞入诗/例句/讨论。
_COLLOQUIAL_STYLE_OBJECT = (
    r'(?:(?:中文|国语|华语|英文|英语|日文|日语|韩语|情绪|伤感|温柔|治愈|舒缓|安静|'
    r'轻柔|轻快|重型|独立|另类|经典|轻|重|偏|比较|一点|有点|更)\s*(?:的)?\s*){0,6}'
    r'(?:说唱|嘻哈|民谣|流行|电子|摇滚|爵士|灵魂乐|蓝调|金属|古典|国风|'
    r'纯音乐|轻音乐|蒸汽波|R\s*&\s*B|Lo[-\s]?fi|City\s*Pop|ACG|'
    r'emo(?:\s*rap)?|rap|hip[-\s]?hop)(?:风格|类)?'
)


def music_style_label(text):
    """从短音乐方向中提取可直接播报的风格词。"""
    match = re.fullmatch(
        r'\s*(?:来点|来些|来几首|想听点?|给我几首)?\s*('
        + _COLLOQUIAL_STYLE_OBJECT + r')(?:的歌|吧)?[。！!\s]*',
        text or '', flags=re.IGNORECASE,
    )
    return match[1].strip() if match and len(match[1].strip()) <= 24 else ''


def colloquial_song_clause(message):
    # 模糊数量不等于意愿模糊；限定音乐对象，不能把几首诗或解释例句当成点歌。
    for clause in reversed(re.split(r'[，,。；;！？!?\n]', message)):
        match = re.fullmatch(
        r'\s*(?:还是|算了|现在|然后)?\s*(?:请|麻烦|能不能|可以)?\s*(?:给我|帮我|替我)\s*'
        r'(?:挑|选|找|推荐|放)?\s*'
        r'(?P<request>几首\s*(?:[^，,。；;！？!?\n]{0,40}(?:歌|歌曲|音乐|曲子|作品))?'
        r'|(?:一些|几支|几曲)\s*[^，,。；;！？!?\n]{0,40}(?:歌|歌曲|音乐|曲子|作品)'
        r'|(?:几首|一些|几支|几曲)\s*' + _COLLOQUIAL_STYLE_OBJECT + r')'
        r'(?:吧|好吗|可以吗)?\s*', _request_text(clause), flags=re.IGNORECASE)
        if match:
            # 引号仅遮蔽动作判断；提取时恢复原始作品/歌手文字，不能丢掉对象。
            return '给我' + clause[match.start('request'):match.end('request')]
    return None


def _positive_song_request(message):
    last_clause = re.split(r"[，,。；;！？!?]\s*(?=\S)", message)[-1]
    last_clause = re.sub(r"^\s*(?:还是|算了|现在|然后)\s*", "", last_clause)
    return (direct_music_control(message) or rain_short_request(message) or rain_theme_command(message)
            or standalone_request(last_clause)
            or bool(colloquial_song_clause(message))
            or any(k in message.lower() for k in RECOMMEND_MARKERS)
            or bool(_COUNTED_RECOMMEND.search(message)) or bool(_PLAY_WORKS.fullmatch(message)))


def explicit_chat_only(message: str) -> bool:
    """明确不听歌/先聊天优先；同句随后明确改口要歌时允许执行。"""
    text = _request_text(message)
    stops = list(_CHAT_ONLY.finditer(text))
    if not stops:
        return False
    following = text[stops[-1].end():]
    # 聊“为什么推荐”仍是讨论；只把后续新分句/明确改口中的动作当成重新要歌。
    if not re.match(r"\s*(?:[，,。；;！!]|但是|不过|但|还是|算了|然后|之后|现在)", following):
        return True
    remainder = re.sub(r"^(?:[，,。；;！!\s]|但是|不过|但|还是|算了|然后|之后|现在)+", "", following)
    return not (standalone_request(remainder) or bool(
        re.match(r"(?:我)?(?:想听|再听|来|放|播放|推荐|给我|帮我找|直接)", remainder)
        and _positive_song_request(remainder)))


def trim_music_invitation(content: str) -> str:
    """只移除主动推荐邀请，保留普通聊天问题和剩余回应，不强加反问。"""
    return _INVITATION.sub("", content or "").strip(" \n，,；;")


def looks_like_song_request(message: str) -> bool:
    """用户消息是否明显在点歌/换歌。

    为什么子串匹配而不是整句判等：口语点歌形态太多（想听/来几首/放点/换一首/
    再来…），整句等值会漏掉绝大多数；而这些词在普通闲聊里几乎不出现，子串误报
    面可接受。情绪倾诉（今天好烦、心里难受）不含这些词，不会误判成点歌。
    """
    m = _request_text(message).lower()
    return not explicit_chat_only(message) and _positive_song_request(m)


def fast_reply(message: str) -> str | None:
    """100% 确定的纯闲聊返回模板回复；不确定返回 None（交给 Agent）。

    判断逻辑：整句清理后必须恰好等于某个寒暄短语。任何夹带了其他内容的
    消息（「你好，推荐几首歌」）都不命中，交给 Agent 完整理解。
    """
    m = (message or "").strip()
    if not m:
        return None
    if _clean(m) not in _PURE_CHITCHAT:
        return None

    if any(k in m.lower() for k in ("你好", "您好", "嗨", "哈喽", "hello", "hi", "在吗", "在的",
                                    "早上好", "中午好", "下午好", "晚上好")):
        return "我在呢。"
    if any(k in m for k in ("谢谢", "感谢", "辛苦了", "多谢")):
        return "不客气。"
    return "晚安，做个好梦。"
