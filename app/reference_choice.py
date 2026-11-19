"""单首修订后的参考关系确认；只用当前批次和可核对的曲库版本。"""

import re

from app import conversation, library, preference_memory as memory, reference_direction

_CHOICE_PREFIX = r'(?:记住?|选)'
_EXPLICIT_CHOICE = re.compile(_CHOICE_PREFIX)
_UNLIKE = re.compile(_CHOICE_PREFIX + r'?(?:原来(?:的)?|原先(?:的)?|旧歌)(?:这首)?(?:不像|不太像)')
_LIKE = re.compile(_CHOICE_PREFIX + r'?(?:换来(?:的)?|新(?:的)?|替补)(?:这首|歌)?(?:更像|很像|像)')
_CANCEL = re.compile(r'(?:先不记|先别记|不用记|都不记|取消这次记忆)')


def capture(reference_id, old_id, new_id):
    ids = [str(sid) for sid in (reference_id, old_id, new_id) if sid]
    if len(set(ids)) != 3:
        return None
    songs = library.find_songs_by_ids(ids)
    if not all(sid in songs for sid in ids):
        return None
    return {key: memory._snapshot(songs[sid]) for key, sid in zip(('reference', 'old', 'new'), ids)}


def current(session_id, batch_id):
    if not batch_id:
        return None
    pair = (conversation.get_recommendation_state(session_id) or {}).get('_replacement_pair') or {}
    keys = ('reference', 'old', 'new')
    if pair.get('batch_id') != batch_id or any(not isinstance(pair.get(key), dict) for key in keys):
        return None
    versions = capture(*(pair[key].get('id') for key in keys))
    if versions and all(versions[key]['fingerprint'] == pair[key].get('fingerprint') for key in keys):
        return versions
    return None


def _question(pair):
    ref, old, new = (pair[key]['title'] for key in ('reference', 'old', 'new'))
    return (f'要记住《{old}》不像《{ref}》，还是你试听后觉得《{new}》更像？'
            '可以回「记原来的不像」「记换来的更像」或「先不记」。')


def question(pair, ref):
    if pair and str(pair['reference']['id']) == str(ref['id']):
        return _question(pair)
    return None


def is_reply(text):
    return bool(_UNLIKE.fullmatch(text) or _LIKE.fullmatch(text) or _CANCEL.fullmatch(text))


def handle(session_id, text, batch_id):
    value = 'unlike' if _UNLIKE.fullmatch(text) else 'like' if _LIKE.fullmatch(text) else None
    cancel = _CANCEL.fullmatch(text)
    if value is None and not cancel:
        return None
    pair = current(session_id, batch_id)
    # 只接受紧接着明确保存请求的选择。普通换歌、点头或迟到的选择都不会授权写入。
    previous = conversation.get_context(session_id, recent_n=2)['messages'] if pair else []
    request = (reference_direction.SAVE_REFERENCE.fullmatch(previous[0]['content'].strip().rstrip('。！!'))
               if len(previous) == 2 and previous[0]['role'] == 'user' else None)
    if not (pair and request and reference_direction.USE_PREVIOUS.fullmatch(request['direction'].strip())
            and previous[1]['role'] == 'assistant' and previous[1]['content'] == _question(pair)):
        # 省略“记”的短答只在确认问题后有意义；其他时候仍可当作普通聊天。
        return ('刚才的参考关系已不在当前队列，请重新明确要记住哪首歌。'
                if value and _EXPLICIT_CHOICE.match(text) else None)
    if cancel:
        return '好，这次不记。'
    target, ref = pair['old' if value == 'unlike' else 'new'], pair['reference']
    memory.save(session_id, 'reference', str(target['id']), value, reference=str(ref['id']), source_text=text)
    relation = '不像' if value == 'unlike' else '更像'
    return f"已记住：《{target['title']}》{relation}《{ref['title']}》。只在以《{ref['title']}》为参考时用，可以在“DJ 记忆”撤销。"
