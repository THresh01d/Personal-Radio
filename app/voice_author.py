"""DJ 自由文案作者：一次调用，自主组织节目；事实边界与写作风格分开。"""

import hashlib
import json
import re
import unicodedata

from app.llm_provider import request_chat
from app.reply_policy import unsupported_context, asserts_listener_state

AUTHOR_TIMEOUT = 20
PROGRAM_MAX = 300  # 与现有约一分钟的语音播放边界保持一致，不设凑字数下限。
LINE_MAX = 300
OPENER_MAX = 300
_HAN_YEAR_DIGITS = str.maketrans('〇零一二三四五六七八九', '00123456789')
_HAN_YEAR = re.compile(r'[一二][〇零一二三四五六七八九]{3}(?=\s*年)')

_SYSTEM_PROMPT = """你是私人电台的 DJ，写适合中文口播、与这次选歌有关的介绍。
根据听众本次原话、已选歌曲及可信素材，自行决定切入点、结构、重点和长短。
可以直接报歌、自然承接对话、围绕一首展开或串起几首；不用每首都讲，不必有开场或结尾。
允许联想、比喻与主观表达，让人听得出那是联想或看法；不把画面说成听众真实经历。
不用为求变化刻意换句式，不要复读近期已经说过的事实或空泛铺垫。没新信息就短一点。
事实边界：
- 当前作品的歌名、歌手、专辑以素材为准；档案标签属于推断线索，不是亲耳听音的证据。
- 不凭训练记忆补年份、创作故事、歌词、署名或编曲听感；有来源的背景才可使用。
- 来源短引如需引用，保留完整原句及其归属；专辑资料不能说成单曲创作史。
- 不断言听众的心理、处境、私人往事，也不声称已经替听众保存了偏好。
- 近期节目只是避免重复的参考，不是本次歌曲的事实或新的听众偏好。
输出 JSON 仅供程序取字段，字段内正文自由撰写：
{"text":"本轮实际播报的自由正文，最多300字", "opener":"可选的聊天回复", "lines":{"歌曲id":"可选的单曲说明"}, "source_ids":["正文确实使用的来源id"]}
opener、lines、source_ids 都可省略；不要求逐首覆盖。text 不必重复卡片，长度按内容决定。
"""


def _material_lines(songs):
    """只装入已有字段；标签和历史提炼明确标为线索，不将其升级为作品事实。"""
    rows = []
    for i, song in enumerate(songs, 1):
        parts = [f"{i}. id={song.get('id')} 歌名《{song.get('title') or ''}》 歌手:{song.get('artist') or ''}"]
        if song.get('album'):
            parts.append(f"专辑《{song['album']}》")
        for key, label in (('genre', '风格'), ('mood', '情绪'), ('scene', '场景')):
            if song.get(key):
                parts.append(label + ':' + '/'.join(song[key]) + '（档案线索）')
        if song.get('is_instrumental'):
            parts.append('纯音乐')
        if song.get('known_why'):
            parts.append('命中理由:' + song['known_why'])
        knowledge = song.get('knowledge') or {}
        for key, label in (('from_comments', '评论区'), ('from_lyrics', '歌词主题')):
            if knowledge.get(key):
                parts.append(label + ':' + knowledge[key] + '（旧提炼，不是创作史证据）')
        rows.append('；'.join(parts))
    return '\n'.join(rows)


def _call_deepseek(system, user, max_tokens):
    """沿用用户当前 Provider 和一次调用上限；异常不阻断推荐或触发重试付费。"""
    try:
        data = request_chat(
            [{'role': 'system', 'content': system}, {'role': 'user', 'content': user}],
            response_format={'type': 'json_object'}, temperature=0.95,
            max_tokens=max_tokens, timeout=AUTHOR_TIMEOUT,
        )
        value = json.loads(data['choices'][0]['message']['content'])
        return value if isinstance(value, dict) else None
    except Exception:
        return None


def _max_tokens(n):
    return min(2200, 350 + n * 180)


def copy_binding(songs):
    """绑定最终歌单与其素材；过滤、换歌或改顺序后不能沿用旧节目。"""
    keys = ('id', 'title', 'artist', 'album', 'genre', 'mood', 'scene', 'is_instrumental')
    value = [{key: song.get(key) for key in keys} for song in songs]
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def _normalize_years(text):
    # 平台简介会把年份逐字写成汉字。比较时统一写法，口播正文保留作者用词。
    return _HAN_YEAR.sub(lambda match: match[0].translate(_HAN_YEAR_DIGITS), unicodedata.normalize('NFKC', text))


def _valid_text(value, songs, message, sources=(), *, song_context=False, allow_reported_state=False):
    """有限事实检查，不用字数下限、标签原词或句式骨架代替文案质量判断。"""
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > PROGRAM_MAX:
        return None
    text = value.strip()
    if not allow_reported_state and asserts_listener_state(text):
        return None
    # 条件或比喻允许画面联想；直接宣称当前时间/处境仍用原有有限守卫检查。
    if (not song_context or '你' in text) and unsupported_context(
            text, message, songs, allow_reported_state=allow_reported_state):
        return None
    if re.search(r'记住了|已(?:经)?保存.*偏好|已(?:经)?撤销.*偏好', text):
        return None
    known = {str(song.get(key)) for song in songs for key in ('title', 'album') if song.get(key)}
    source_text = '\n'.join(fact['text'] for fact in sources)
    if any(name not in known and f'《{name}》' not in source_text for name in re.findall(r'《([^》]+)》', text)):
        return None
    fact_text = _normalize_years(re.sub(r'《[^》]*》', '', text))
    year_pattern = r'(?<!\d)(?:19|20)\d{2}(?!\d)'
    source_years = set(re.findall(year_pattern, _normalize_years(source_text)))
    if any(year not in source_years for year in re.findall(year_pattern, fact_text)):
        return None
    # 缺少可追溯素材时不放行具体听觉/创作断言；这不是任意中文事实核验器。
    terms = ('旋律', '鼓点', '音色', '编曲', '贝斯', '吉他', '副歌', '高潮', '唱腔',
             '制作人', '作词', '作曲', '和声', '人声处理', '创作于', '发行于', '写于')
    if any(term in text and term not in source_text and term.removesuffix('于') not in source_text
           and not any(term in name for name in known) for term in terms):
        return None
    for song in songs:
        title, album = song.get('title'), song.get('album')
        if title and album:
            for wrong in re.findall(re.escape(f'《{title}》') + r'[^。！？!?\n]{0,12}(?:收录在|出自|来自)(?:专辑)?《([^》]+)》', text):
                if wrong != album:
                    return None
    return text


def _guard_lines(raw, songs, message='', sources=()):
    if not isinstance(raw, dict):
        return {}
    return {song['id']: text for song in songs
            if (text := _valid_text(raw.get(song['id']), songs, message, sources, song_context=True))}


def _guard_opener(value, songs, message=''):
    return _valid_text(value, songs, message)


def author_song_intros(songs, message, llm=None, *, recent=(), backgrounds=None):
    """兼容原开场返回值；把绑定后的自由正文暂存在首曲，批次创建时消费并移除。"""
    if not songs:
        return None
    backgrounds = backgrounds or {}
    facts = [dict(fact, song_id=song['id']) for song in songs[:2]
             for fact in backgrounds.get(song['id'], [])[:2]][:3]
    # 元数据、来源、近期正文是数据，不能作为新系统指令；不发送整段私人聊天历史。
    user = json.dumps({'listener_request': message or '随便听听', 'songs': _material_lines(songs),
                       'sources': facts, 'recent_programs': list(recent)[-3:]}, ensure_ascii=False)
    caller = llm or (lambda system, prompt: _call_deepseek(system, prompt, _max_tokens(len(songs))))
    try:
        raw = caller(_SYSTEM_PROMPT, user)
    except Exception:
        return None
    if not isinstance(raw, dict):
        return None
    requested = raw.get('source_ids') or []
    if not isinstance(requested, list) or any(not isinstance(key, str) for key in requested):
        requested = []
    by_id = {fact['id']: fact for fact in facts}
    used = [by_id[key] for key in dict.fromkeys(requested)
            if key in by_id and isinstance(raw.get('text'), str) and by_id[key]['text'] in raw['text']]
    text = (_valid_text(raw.get('text'), songs, message, used)
            if all(key in by_id and by_id[key] in used for key in requested) else None)
    lines = raw.get('lines') if isinstance(raw.get('lines'), dict) else raw
    keep = _guard_lines(lines, songs, message, used)
    for position, song in enumerate(songs, 1):
        if song['id'] in keep:
            song['dj_intro'] = keep[song['id']]
            song['reason'] = keep[song['id']]
        elif text:
            # 模型选择不逐首介绍时，不替它补一长段模板；卡片仍保留确定性选歌依据。
            song['dj_intro'] = ''
        elif keep:
            from app.retrieval import build_dj_intro
            song['dj_intro'] = build_dj_intro(song, message, position)
    if text:
        binding = copy_binding(songs)
        for song in songs:
            song['_dj_binding'] = binding
        songs[0]['_dj_copy'] = {'binding': binding, 'text': text,
                                'sources': used, 'copy_source': 'ai'}
    return _valid_text(raw.get('opener'), songs, message, used) or text


def take_program(songs, message):
    """程序内部传递的文案只使用一次；不把暂存字段送进 API、历史曲目或后台任务。"""
    packets = [song.pop('_dj_copy', None) for song in songs]
    bindings = [song.pop('_dj_binding', None) for song in songs]
    packet = next((value for value in packets if isinstance(value, dict)), None)
    if not packet or packet.get('binding') != copy_binding(songs):
        if any(bindings):
            # 最终排除可能连首曲也删掉；剩余单曲文案亦可能提到已删作品，须同时退化。
            from app.retrieval import build_dj_intro
            for position, song in enumerate(songs, 1):
                song['dj_intro'] = build_dj_intro(song, message, position)
                song['reason'] = '按本次条件从曲库选出。'
        return None
    from app.song_background import _valid_cached_facts
    sources = packet.get('sources') or []
    if not _valid_cached_facts(sources) or any(fact['text'] not in packet.get('text', '') for fact in sources):
        return None
    text = _valid_text(packet.get('text'), songs, message, sources)
    if not text:
        return None
    return {'text': text, 'sources': sources, 'mode': 'sourced' if sources else 'metadata',
            'max_seconds': 60, 'copy_source': 'ai'}
