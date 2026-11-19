"""歌手点名的描述词边界：避免把完整的场景句当作缺失歌手。

只用确定性结构和词表，不声称能识别所有人名；曲库零命中不是艺人身份的证据。
"""

import re

from app.retrieval import STYLE_KEYWORDS


def is_non_artist_description(text):
    """完整描述短语才放行，不能因名字含“说唱”就误伤河南说唱之神。"""
    bare = re.sub(r"(?:一点|一些|点儿|些|风格|类型|一点儿|的)$", "", text).strip().casefold()
    vocabulary = STYLE_KEYWORDS | {
        "emorap", "rap", "hip hop", "hiphop", "安静", "舒缓", "放松", "工作",
        "写代码", "专注", "中文", "国语", "英文", "英语", "日文", "日语",
        "有人声", "有人唱", "好听", "致郁", "抑郁", "低落", "悲伤",
        "我", "你", "他", "她", "自己", "大家", "喜欢", "收藏",
    }
    # 组合形容词也不是歌手；全串匹配使“河南说唱之神”不会因包含词表子串被排除。
    alternatives = "|".join(re.escape(word) for word in sorted(vocabulary, key=len, reverse=True))
    if re.fullmatch(rf"(?:{alternatives}|特别|非常|比较|有点|[\s、和又偏很更])+", bare):
        return True
    # 时间/目的/感受句是描述，不把“写代码时候听”送去精确歌手查询后直接拒绝。
    return bool(re.search(
        r"时候|时(?:听|适合)|用来|适合|让我|令人|想听|听起来|唱过|(?:想|喜欢|收藏|听过)的|风格|类型|听$",
        bare,
    ))
