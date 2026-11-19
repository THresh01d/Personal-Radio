"""聊天确认与事件归一化的纯规则，不访问HTTP、数据库或模型。"""

import re

_CONSENT = {
    "好", "好的", "行", "可以", "要", "嗯", "来", "好呀", "好啊",
    "可以啊", "行啊", "要吧", "嗯嗯", "好的呀", "行吧", "可以呀", "好嘞",
    # 英文短确认与中文口语确认同样是明确授权。只做整句匹配，避免普通长句误触发。
    "ok", "okay", "yes", "yep", "对", "对的", "没问题", "确认", "点头",
    "点头了", "我点头", "我点头了", "就这么来", "开始吧",
}

_OFFER_SIGNS = (
    "要不要", "需要我", "需要吗", "要吗", "挑几首", "来几首", "给你排", "放给你",
    "点个头",
)


def _requests_unheard_tracks(message: str) -> bool:
    """用户明确要求换一批时，给工具一个确定性的不重复信号。

    为什么不交给 Agent 猜：『其他歌』是可执行的硬约束。若只让模型写文案，
    它可能说“换一批”却仍返回上一轮的同三首，或干脆不返回歌曲卡片。
    """
    return any(word in (message or "") for word in ("其他", "别的", "不重样", "换一批", "没听过"))


def _clean_words(text):
    """去掉空白与常见标点只留正文（与 intent._clean 同口径，供集合判断）。"""
    return re.sub(r"[\s，。！？!?~～、…·,.]", "", (text or "").strip().lower())


def _assistant_offered(content):
    """DJ 上一条消息是否抛了『要不要再推荐几首』的询问。"""
    text = re.sub(r'「[^」]*」|“[^”]*”|《[^》]*》', '', content or '')
    return any(s in text for s in _OFFER_SIGNS)


def _resolve_consent(messages):
    """用户刚应允了 DJ 上一个反问 → 返回该按哪条用户原话推荐；否则 None。

    为什么这里要确定性直通而不是继续赌 LLM：『要歌不猜』把决定权交回用户后，
    用户在下一轮回「好/可以/要」，这是**已经点过头的点歌请求**（不是猜）。此时
    直接按用户原话方向出真实歌单既符合契约，也避免再次依赖 LLM 会否调工具。

    判定（两条都得满足，防止 DJ 只是闲聊后用户随口『好』也硬塞歌单）：
    1. 最后一条 user 是纯应允词；
    2. 倒数第二条 assistant 内容确实抛了推荐邀请。
    满足后取该 assistant 之前的最近一条 user 原话作为推荐方向（DJ 不代拟）。
    """
    if len(messages) < 2:
        return None
    if messages[-1].get("role") != "user":
        return None
    if _clean_words(messages[-1].get("content")) not in _CONSENT:
        return None
    prev = messages[-2]
    if prev.get("role") != "assistant" or not _assistant_offered(prev.get("content")):
        return None
    # 向上找方向：跳过 assistant，也跳过此前已经说过但未被旧逻辑识别的确认词。
    # 这让「换几首风格接近的 → ok → 对 → 我点头了」即使形成了重复反问，
    # 修复上线后的下一句确认仍会追溯到真正的方向，而不是拿「对」去检索。
    for m in reversed(messages[:-1]):
        if m.get("role") != "user" or not (m.get("content") or "").strip():
            continue
        content = m["content"].strip()
        if _clean_words(content) in _CONSENT:
            continue
        return content
    return None


def _parse_events(events):
    """从 Agent 事件流里抽出最终回答、歌单与统计。

    songs 只认 recommend_playlist 的 tool_result（唯一出正式歌单的工具）；
    answer 取最后一个 content 事件。
    """
    content = None
    songs = []
    stats = {}
    recommendation_intent = None
    for ev in events:
        t = ev.get("type")
        if t == "content":
            content = ev.get("content")
        elif t == "tool_result" and ev.get("name") == "recommend_playlist":
            songs = (ev.get("result") or {}).get("songs") or []
            recommendation_intent = ev.get("recommendation_intent")
        elif t == "done":
            stats = ev.get("stats") or {}
    if recommendation_intent is not None:
        stats = dict(stats)
        stats["_recommendation_intent"] = recommendation_intent
    return content or "我在呢……", songs, stats

