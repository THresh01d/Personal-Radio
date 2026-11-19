"""有限但完整的偏好短句入口；复杂表达澄清，不将模型推测当成保存成功。"""
import re

from app import conversation, preference_memory as memory, reference_choice, reference_direction
from app.scene_memory_dialogue import _song, _SCENE

_PREF = re.compile(rf'(?:请)?(?:帮我)?(?:记住[，,：:]?)?(?P<future>以后|平时|一直|通常)?(?:(?P<scene>{_SCENE})(?:的时候|时)?[，,]?)?(?:我)?(?:更|比较|最)?(?P<action>喜欢|不喜欢|多推荐|少推荐|不要推荐|不再推荐|别推荐|不听)(?P<target>.+?)(?:的歌|的歌曲|吧|了)?[。！!]*')
_RELATION = re.compile(r'(?:记住[，,：:]?)?(?:以|找类似)《(?P<reference>[^》]+)》(?:为参考时|的歌时)[，,]?《(?P<song>[^》]+)》(?P<value>很像|像|不像|不太像)[。！!]*')
_EXPRESSION = (
    (r'(?:以后|记住[，,]?)?(?:介绍|讲解)(?:请)?(?:简短|短|少说)(?:一?点|一些|些)?', 'length', 'short'),
    (r'(?:以后|记住[，,]?)(?:介绍|讲解)(?:正常|详细)(?:一?点|一些|些)?', 'length', 'normal'),
    (r'(?:以后|记住[，,]?)(?:不用介绍|不要介绍|直接放歌|直接听歌)', 'mode', 'quiet'),
    (r'(?:以后|记住[，,]?)(?:恢复介绍|保留介绍|需要介绍)', 'mode', 'intro'),
)


def _save_reply(session_id, kind, target, value, scene, message, reference=''):
    identifier = memory.save(session_id, kind, target, value, scene=scene, reference=reference, source_text=message)
    row = next(r for r in memory.records(session_id) if r['id'] == identifier)
    relation = f"以《{row['target']['reference']['title']}》找类似歌曲时，" if kind == 'reference' else ''
    limit = '当前队列不变；可在“DJ 记忆”修改或撤销。'
    return f"已记住：{relation}{row['scene'] or '一般偏好'}，{row['label']} → {row['value_label']}。{limit}"


def handle(session_id, message, current_song_id=None, *, current_batch_id=None):
    text = message.strip().rstrip('。！!')
    choice_reply = reference_choice.handle(session_id, text, current_batch_id)
    if choice_reply is not None:
        return choice_reply
    saved = reference_direction.SAVE_REFERENCE.fullmatch(text)
    if saved:
        if re.search(r'[？?]|(?:不要|别|不用)记', text):
            return '要保存的话，请明确说以后拿这首歌当参考时想优先找的方向。'
        if not re.match(r'(?:就|请)?(?:按|沿用|优先|多找|多推|找|推|选|挑|避开|不要)', saved['direction']):
            raise ValueError('请说清以后拿这首作参考时，要优先找什么情绪或风格。')
        direction = reference_direction.parse(saved['direction'])
        recent = {}
        use_previous = reference_direction.USE_PREVIOUS.fullmatch(saved['direction'].strip())
        pair = None
        if use_previous:
            task = conversation.get_recommendation_state(session_id) or {}
            recent = task.get('_replacement_direction') or {}
            pair = reference_choice.current(session_id, current_batch_id)
        ref = reference_direction.resolve_named(saved['title'], saved['artist'],
                                                recent.get('reference_id') or (pair or {}).get('reference', {}).get('id') or current_song_id)
        if recent:
            direction = (recent.get('direction') if recent.get('reference_id') == str(ref['id'])
                         and recent.get('fingerprint') == memory._snapshot(ref)['fingerprint'] else None)
        label = reference_direction.describe(direction or {})
        if not label:
            if use_previous and (question := reference_choice.question(pair, ref)):
                return question
            raise ValueError('请说清以后拿这首作参考时，要优先找什么情绪或风格。')
        memory.save(session_id, 'reference_direction', str(ref['id']), 'prefer', source_text=message, direction=direction)
        return f"已记住。以后以《{ref['title']}》为参考，我会优先按「{label}」找。可以在“DJ 记忆”撤销。"
    for pattern, target, value in _EXPRESSION:
        if re.fullmatch(pattern, text):
            # 无“以后”的少说点仍是当次控制，不升级为长期习惯。
            if not text.startswith(('以后', '记住')):
                return None
            return _save_reply(session_id, 'expression', target, value, '', message)
    relation = _RELATION.fullmatch(text)
    if relation:
        return _save_reply(session_id, 'reference', relation['song'], 'unlike' if relation['value'] in ('不像', '不太像') else 'like', '', message, relation['reference'])
    match = _PREF.fullmatch(text)
    if match:
        scene = match['scene'] or ''
        action = match['action']
        # 临时诉求与泛泛“喜欢”不直接伪装为永久命令；明确习惯陈述可保存。
        if not (match['future'] or scene or text.startswith('记住')):
            return None
        value = 'exclude' if action in ('不要推荐', '不再推荐', '别推荐', '不听') else 'reduce' if action in ('不喜欢', '少推荐') else 'prefer'
        target = match['target'].strip()
        if target in ('这位歌手', '这个歌手', '这位艺人'):
            from app import library
            from app.retrieval import artist_identity_keys
            song = library.find_song_by_id(current_song_id) if current_song_id else None
            if not song or len(artist_identity_keys(song.get('artist'))) != 1:
                raise ValueError('请明确一位歌手的完整名称；合作署名不能猜指的是谁。')
            target = song['artist']
        if target.startswith(('这首', '当前这首', '《')):
            if scene and value == 'exclude':
                # 已有场景单曲入口继续兼容；底层写事务会互斥同对象的新记录。
                return None
            song = _song(target, current_song_id)
            if not song:
                raise ValueError('请选中这首歌，或提供曲库中唯一的完整歌名。')
            return _save_reply(session_id, 'song', song['id'], value, scene, message)
        from app.recommendation_state import GENRES, _canonical_genre
        canonical = _canonical_genre(target)
        if canonical in GENRES:
            return _save_reply(session_id, 'genre', canonical, value, scene, message)
        return _save_reply(session_id, 'artist', target, value, scene, message)
    revoke = re.fullmatch(r'(?:取消|撤销|忘掉)(?:关于)?(?P<target>.+?)(?:的)?(?:长期)?偏好', text)
    if revoke:
        target = revoke['target'].strip().strip('《》')
        rows = [r for r in memory.records(session_id) if r['label'] == target]
        if len(rows) == 1:
            if memory.revoke(session_id, rows[0]['id']):
                return f"已撤销关于{rows[0]['label']}的偏好；后续不再使用。"
            return '这项偏好已经变化，请重新读取后核对。'
        if len(rows) > 1:
            return '存在多条不同场景的相关偏好，请在“DJ 记忆”选择具体记录撤销。'
    return None
