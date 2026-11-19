"""雨天作为听歌语境；明确的音乐方向与歌名主题搜索分开处理。"""
import re


_RAIN = re.compile(r'雨天|下雨(?:天|了)?')
_SHORT = re.compile(r'\s*(?:雨天|下雨(?:天|了)?)[，,、\s]*'
                    r'(?:想要|想|要)?(?:温柔|柔和|轻柔)(?:一点|些|点)?(?:吧)?[。！!\s]*')
_THEME = re.compile(r'歌名|曲名|标题|名字|名叫|叫做|歌词|关于|主题|带雨|带有雨|含雨|包含雨')
_OTHER_CONTEXT = re.compile(r'工作|学习|写代码|运动|跑步|健身|深夜|睡前|参考|类似|像这首')


def rain_short_request(message):
    """只接受界面所提示的有限场景＋方向短句，不把天气陈述当执行意愿。"""
    return bool(_SHORT.fullmatch(message or ''))


def rain_theme_request(message):
    return bool(_THEME.search(message or '') and re.search(r'雨|rain', message or '', re.I))


def rain_theme_command(message):
    # “找歌名带雨的歌”也是明确选歌；讨论或拒绝这句话不构成命令。
    return bool(rain_theme_request(message) and re.fullmatch(
        r'\s*(?:请|麻烦)?(?:帮我)?(?:找|推荐|来|放|想听)[^。！？!?\n]+[。！!\s]*', message or ''))


def rain_title_term(message):
    """明确按歌名包含雨/Rain查找时，以真实元数据校验，不能只给语义近似结果。"""
    if re.search(r'(?:不要|别|不想|不用)[^，,。；;]*歌名', message or ''):
        return None
    match = re.search(r'(?:歌名|曲名|标题)(?:里|中)?(?:带有|包含|含有|带|含)[“"「]?'
                      r'(雨天|rain|雨)', message or '', re.I)
    return match.group(1).casefold() if match else None


def rain_listening_plan(message, *, artist=False, reference=False, other_strategy=False):
    """只在用户原话明确提供雨天语境和温柔方向时启用，不依赖模型自加标签。"""
    text = message or ''
    if artist or reference or other_strategy or _THEME.search(text) or _OTHER_CONTEXT.search(text):
        return None
    # 引用的数据可能是作品名或例句，不能据此建立本轮天气语境。
    if re.search(r'[《「“"]', text):
        return None
    if not _RAIN.search(text) or not re.search(r'温柔|柔和|轻柔', text):
        return None
    if re.search(r'(?:不要|不想|不是|别|不必|不用)[^，,。；;！？!?]*?(?:雨天|下雨|温柔|柔和|轻柔)', text):
        return None
    if re.search(r'不(?:太|很|那么)?\s*(?:温柔|柔和|轻柔)', text):
        return None
    query = re.sub(r'(?:适合|在)?(?:雨天|下雨(?:天|了)?)(?:时(?:候)?)?', ' ', text)
    query = re.sub(r'[，,、；;\s]+', ' ', query).strip()
    return {'context': '雨天', 'moods': ['温柔'], 'retrieval_query': query or '温柔的音乐'}


def prioritize_rain_direction(candidates, plan):
    """沿用原综合分的组内顺序；匹配方向先于仅有天气主题，不改变分数或歌曲名。"""
    result = []
    for song in candidates:
        protected = song.get('playability') == 'unavailable' or float(song.get('taste_score', .5)) < 0
        matched = bool(set(plan['moods']).intersection(song.get('mood') or []) and not protected)
        result.append({**song, 'rain_music_direction': {
            'context': '雨天', 'moods': list(plan['moods']), 'matched': matched}})
    return sorted(result, key=lambda s: s['rain_music_direction']['matched'], reverse=True)


def rain_direction_summary(songs, target_count=None):
    if not songs:
        return None
    records = [s.get('rain_music_direction') for s in songs]
    if not all(isinstance(r, dict) and r.get('context') == '雨天' and r.get('moods') == ['温柔'] for r in records):
        return None
    matched = sum(r.get('matched') is True for r in records)
    missing = len(songs) - matched
    message = (f'本轮{matched}首有温柔标签支持，另{missing}首的温柔程度暂缺资料确认。' if missing else '')
    if not missing and target_count and len(songs) < target_count:
        message = f'现有候选不足，这轮先给你{len(songs)}首有温柔标签支持的歌。'
    return {'context': '雨天', 'moods': ['温柔'], 'matched': matched, 'fallback': missing,
            'basis': 'existing_mood_tags_not_listening_truth', 'message': message}
