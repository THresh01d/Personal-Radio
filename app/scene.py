"""app/scene.py - 场景策略表（确定性：场景词 → 检索策略 + 开场白）

为什么确定性而不是交给 LLM：场景翻译成『要什么 mood/genre、排除什么』是
稳定规则，让 LLM 每次现译会飘；这里用多字 markers 精确匹配（避免单字误伤），
命中即得到一份可测试的检索策略。语义理解仍在 LLM（它决定是否传 scene 参数），
这里的策略表是『翻译』层，理解归 LLM、翻译归代码。

原则：markers 只放多字、确定的场景词；策略只引用真实标签（mood/genre），
不引用需要听觉依据的东西；opener 是活人感的确定性兜底开场。
"""

from dataclasses import dataclass
import re


@dataclass(frozen=True)
class SceneStrategy:
    key: str                    # 场景键名，如『工作专注』
    markers: tuple              # 触发词（多字，任一命中即触发）
    moods: tuple = ()           # 命中场景时补全的 mood 过滤（可空）
    genres: tuple = ()          # 命中场景时补全的 genre 过滤（可空）
    exclude_genres: tuple = ()  # 硬排除的风格（强人声类）
    opener: str = ""            # 确定性开场白（素材包注入 DJ 文案用）
    energy: str | None = None   # 场景默认仅是软目标，不表示测得节奏或真实运动适配性


def unnegated_terms(text, words):
    """只识别紧邻否定，避免把“不要安静/不是瑜伽”当正向方向。"""
    hits = []
    for word in words:
        for match in re.finditer(re.escape(word), text or ''):
            prefix = (text or '')[max(0, match.start()-12):match.start()]
            if not re.search(r'(?:不要|不想听|不想要|不听|不需要|不是|不想|不用|别|不)(?:太|很|那么|特别|再)?\s*$', prefix):
                hits.append((match.start(), word))
    return sorted(hits)


def valid_activity_direction(value):
    return (isinstance(value, dict) and value.get('mode') in ('active', 'gentle')
            and value.get('energy') in ('low', 'medium-low', 'medium', 'medium-high', 'high')
            and value.get('energy_source') in ('user_text', 'scene_default'))


def activity_from_query(query, previous=None):
    """用户原话中的活动与强度优先；没有请求依据时不从模型情绪推断运动。"""
    text = re.sub(r'《[^》]*》', '', query or '')
    previous = previous if valid_activity_direction(previous) else None
    active = unnegated_terms(text, ('运动', '跑步', '健身', '骑行', '锻炼', '跑操', '有氧', '力量训练'))
    active = [(pos, word) for pos, word in active if not (word == '运动' and text[pos+2:pos+3] in ('员', '会', '鞋'))]
    gentle = unnegated_terms(text, ('拉伸', '瑜伽', '散步', '运动后', '跑步后', '健身后'))
    if activity_rejected(text) and not active and not gentle:
        return None
    if not active and not gentle and not previous:
        return None
    gentle_default = bool(gentle and (not active or gentle[-1][0] >= active[-1][0]))
    choices = []
    for energy, words in (
        ('low', ('低能量', '舒缓', '安静', '低强度', '慢一点')),
        ('medium-low', ('温柔', '轻一点', '柔和')),
        ('high', ('高能量', '燃', '躁', '有劲', '强节奏', '冲刺')),
    ):
        choices.extend((pos, energy) for pos, _ in unnegated_terms(text, words))
    if choices:
        energy, source = max(choices, key=lambda pair: pair[0])[1], 'user_text'
    elif re.search(r'(?:不要|不想|别)(?:太|那么)?(?:燃|躁|高能量|强节奏)', text):
        energy, source = 'medium', 'user_text'
    elif active or gentle:
        energy, source = ('low' if gentle_default else 'medium-high'), 'scene_default'
    else:
        energy, source = previous['energy'], previous['energy_source']
    return {'mode': 'gentle' if energy in ('low', 'medium-low') else 'active',
            'energy': energy, 'energy_source': source}


def activity_rejected(query):
    """仅识别紧邻的退出活动方向；不是给情绪或歌曲新增排除规则。"""
    return bool(re.search(r'(?:不要|不是|不想|不适合|别)(?:再)?\s*(?:运动|跑步|健身|骑行|锻炼|拉伸|瑜伽)', query or ''))


ACTIVITY_STRATEGIES = {
    'active': SceneStrategy(key='运动', markers=(), energy='medium-high', opener='给这次活动一点向前的动力。'),
    'gentle': SceneStrategy(key='舒缓运动', markers=(), energy='low', opener='这轮跟着你想放缓的活动强度来。'),
}


def activity_selection_summary(songs):
    """分开说明标签线索和实际能量覆盖，不把粗能量冒充节拍或运动真值。"""
    if not songs:
        return None
    direction = songs[0].get('activity_direction')
    if not valid_activity_direction(direction) or any(s.get('activity_direction') != direction for s in songs):
        return None
    scene_matches = sum('运动' in (s.get('scene') or []) for s in songs)
    energy_observed = sum(s.get('acoustic_score') is not None for s in songs)
    if not scene_matches:
        notice = '这批候选暂无运动场景标签支持，按其余条件作了补充；活动强度是否合适仍需试听确认。'
    elif not energy_observed:
        notice = '这批候选主要依据场景标签和文本资料，暂缺可比的能量资料；节奏是否合适仍需试听确认。'
    else:
        notice = ''
    return {**direction, 'scene_tag_matches': scene_matches, 'energy_observed': energy_observed,
            'selected': len(songs), 'message': notice}


SCENE_STRATEGIES = (
    SceneStrategy(
        key="工作专注",
        markers=("写代码", "写作业", "加班", "学习", "工作", "专注", "赶工", "deadline"),
        moods=("平静",),
        genres=("Lo-fi", "纯音乐"),
        exclude_genres=("说唱", "金属", "摇滚"),
        opener="写代码的时候，声音得是背景，但不能抢戏。",
    ),
    SceneStrategy(
        key="深夜",
        markers=("深夜", "凌晨", "睡不着", "失眠", "夜晚"),
        moods=("夜晚", "孤独"),
        exclude_genres=("说唱", "金属"),
        opener="深夜了，声音得轻一点。",
    ),
)


def scene_from_query(query: str) -> SceneStrategy | None:
    """从用户消息文本确定性提取场景策略；识别不出返回 None。

    为什么在 LLM 结构化参数之外还要这条兜底：模型可能漏传 scene 参数，
    但场景词已经写在用户原话里，这里用词表兜住，保证『写代码』这类请求
    即使模型只传了 query 也能命中场景策略。
    """
    direction = activity_from_query(query)
    if direction:
        return ACTIVITY_STRATEGIES[direction['mode']]
    q = (query or "").lower()
    for s in SCENE_STRATEGIES:
        if any(m in q for m in s.markers):
            return s
    return None
