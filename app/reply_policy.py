"""本轮表达边界：歌曲标签不代替用户处境，重复资料提示只在当前任务去重。"""
import hashlib
import json
import re

from app.intent import direct_music_control


_CONTEXTS = (
    ('深夜', '凌晨', '夜晚', '今晚'), ('独处', '一个人', '孤身'),
    ('雨天', '下雨', '阴天'), ('失眠', '睡不着'),
    ('工作', '写代码', '学习'), ('跑步', '运动', '健身'),
)
_USER_STATE = re.compile(r'你(?:现在|此刻|最近|今天|今晚)?(?:正|又|很|有点|已经|正在)?'
                         r'(?P<state>难过|孤独|低落|焦虑|疲惫|累|失眠|伤心)|你的(?:心情|情绪|压力|孤独)')
_REPORTED_STATE = re.compile(
    r'(?:^|[，。！？!?；;\n])\s*(?:我)?(?:现在|此刻|最近|今天|今晚)?(?:我)?'
    r'(?:有点儿?|很|挺|太|真的|好|特别)?(难过|孤独|低落|焦虑|疲惫|累|失眠|伤心)'
    r'(?:了|得不行)?(?=$|[，。！？!?；;\n]|[， ]*想听)'
)


def _request_text(message, songs):
    text = re.sub(r'《[^》]*》|「[^」]*」|“[^”]*”|"[^"\n]*"', '', message or '')
    for song in songs:
        for field in ('title', 'artist', 'album'):
            value = song.get(field)
            if value:
                text = text.replace(str(value), '')
    # 拒绝/询问的场景词不是当前处境的证据。
    text = re.sub(r'(?:不要|不是|不想|为什么|别说|别提)[^，。！？!?；;\n]*', '', text)
    return text


def unsupported_context(text, message, songs=(), *, allow_reported_state=False):
    """有限的场景/处境检查，不能冒充任意中文事实核验。"""
    request = _request_text(message, songs)
    if asserts_listener_state(text, request if allow_reported_state else ''):
        # 请求伤感歌曲不等于自述伤感；这一层优先拒绝替用户定义心理状态。
        return True
    for clause in re.split(r'[，。！？!?；;\n]', text or ''):
        # 明确的想象画面可自由表达，但含听众指向时仍检查，不能借比喻宣称其处境。
        if '你' not in clause and any(word in clause for word in ('像是', '仿佛', '不妨想象', '假如', '如果')):
            continue
        # 明确归属歌曲/资料或条件性的适用描述可以保留，但不放行同句对“你”的断言。
        described = any(word in clause for word in ('档案', '标签', '歌词', '评论区', '适合', '的时候',
                                                     '歌曲', '歌手', '专辑', '作品', '这首', '这张'))
        if described and '你' not in clause:
            continue
        for words in _CONTEXTS:
            if any(word in clause for word in words) and not any(word in request for word in words):
                return True
    return False


def asserts_listener_state(text, report=''):
    """逐首介绍可有条件性的适用描述，但不能替听众定义心理状态。"""
    reported = set(_REPORTED_STATE.findall(report))
    return any(match.group('state') not in reported for match in _USER_STATE.finditer(text or ''))


def brief_music_reply(message):
    return direct_music_control(message) and any(word in message for word in
        ('少说点', '不用介绍了', '不用说了'))


def clean_chat_reply(answer, message):
    """只清理未被问及的能力自述/拉回歌库，不裁掉普通聊天问题和诚实失败说明。"""
    if any(word in message for word in ('你能', '你会', '能力', '功能', '做什么', '限制')):
        return answer
    chunks = re.findall(r'[^。！？!?\n]+[。！？!?]?|\n', answer or '')
    kept = []
    removed_inference = False
    for chunk in chunks:
        # 有限兜底：拦截从泛听口味直接概括私人经历的整段，连带例子不残留。
        # 不尝试用关键词验证所有人生经历；明确引用或讨论这类说法时保留讨论。
        if (re.search(r'(?:啥都听|什么都听|听得(?:很)?杂|听歌很杂)的人.*(?:一般|共同点|都|每首)', chunk)
                and not any(word in message for word in ('这句话', '这种说法', '为什么', '怎么看'))):
            removed_inference = True
            continue
        if removed_inference and (chunk.strip().startswith(('比如', '例如', '某首', '不是不喜欢')) or not chunk.strip()):
            continue
        if any(word in chunk for word in ('唯一能干', '只能翻你的歌库', '除了翻歌库', '最近听的、没听的、想听')):
            continue
        kept.append(chunk)
    return ''.join(kept).strip() or '好，先聊聊。'


def task_key(intent):
    # 查询原文、模型补充和播放批次不构成新任务；明确新需求仍由continuation判断重置。
    fields = ('artists', 'genres', 'moods', 'scenes', 'language', 'instrumental',
              'energy', 'excluded_genres', 'target_count', 'reference_song_id', 'activity')
    payload = {key: intent.get(key) for key in fields}
    if intent.get('style_request'):
        payload['style_request'] = intent['style_request']
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def brief_sentences(text, limit=96):
    """保留开头至多两句完整的话，避免在作品名或事实中间截断。"""
    text = text.strip()
    end = 0
    for match in list(re.finditer(r'.+?(?:[。！？!?]+[”’」』）】]*|\n+|$)', text, re.DOTALL))[:2]:
        if match.end() > limit:
            break
        end = match.end()
    return text[:end].rstrip()


def format_recommendation(answer, message, songs, intent, previous, notices):
    """只改整轮回答；所有限制保留在stats，歌曲、分数、次序完全不动。"""
    previous = previous or {}
    previous_presentation = previous.get('_reply_presentation') or {}
    key = task_key(intent)
    same = bool(intent.get('continuation') and previous_presentation.get('task_key') == key)
    shown = dict(previous_presentation.get('shown') or {}) if same else {}
    repeated = []
    # 工具开场可能已带说明，先统一拆出，避免整段护栏或去重吞掉新的限制。
    for record in notices.values():
        if record['message']:
            answer = answer.replace(record['message'], '').strip()
    artists = intent.get('artists') or []
    fallback = (f"好，选了{len(songs)}首{artists[0]}的作品。" if artists
                else f"好，这轮选了{len(songs)}首。")
    if not artists:
        tags = list(dict.fromkeys(tag for song in songs for field in ('genre', 'mood')
                                  for tag in song.get(field) or []))[:2]
        if tags:
            fallback = f"好，这轮选了{len(songs)}首，档案标签有{'、'.join(tags)}。"
    # 最终出口用本条用户原话校验，不能让模型自己改写的query变成处境证据。
    # 裸续播可以退回简短中性开场；不为文案自由度放松事实边界。
    guarded = unsupported_context(answer, message, songs)
    if guarded:
        suffix = '（当前使用离线基础模式。）' if '当前使用离线基础模式' in answer else ''
        if '已切换本地降级模式' in answer:
            suffix = '（AI服务暂不可用，已切换本地降级模式。）'
        answer = fallback + suffix
    if brief_music_reply(message):
        answer = '好，直接听歌。'
    answer = answer.strip() or fallback
    for name, record in notices.items():
        if record['message']:
            if shown.get(name) == record:
                repeated.append(name)
            else:
                answer += ' ' + record['message']
            shown[name] = record
        else:
            # 资料恢复后再缺失必须重新提示，不能把旧的“已经说过”永久沿用。
            shown.pop(name, None)
    return answer, {'task_key': key, 'shown': shown}, {
        'brief': brief_music_reply(message), 'guarded_context': guarded, 'repeated_notices': repeated}
