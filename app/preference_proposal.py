"""现有Agent的受约束偏好提案；只有当前用户原话可授权，工具不能重放历史。"""
import re
import sqlite3

from app import chat_turns, library, preference_memory as memory
from app.recommendation_state import SCENES

TOOL = {'type': 'function', 'function': {
    'name': 'remember_preference',
    'description': '仅保存本条用户明确表达的持续音乐或DJ表达偏好；普通闲聊、临时要求和历史原话不可保存。必须严格引用本条的对象、条件和表态，不得猜测；不确定时澄清。',
    'parameters': {'type': 'object', 'additionalProperties': False,
        'properties': {'kind': {'type': 'string', 'enum': list(memory.KINDS)},
            'target': {'type': 'string', 'description': '当前用户明确提到的完整对象；歌曲可用真实ID或唯一完整歌名'},
            'value': {'type': 'string', 'enum': [*memory.VALUES, 'like', 'unlike', 'short', 'normal', 'quiet', 'intro']},
            'scene': {'type': 'string', 'description': '仅保留用户明确说出的场景；无条件时留空'},
            'reference': {'type': 'string', 'description': '关系反馈双方必须在本条原话中明确指出'}},
        'required': ['kind', 'target', 'value']}}}


def eligible(message):
    text = message or ''
    if not re.match(r'^(?:我|请|帮我|记住|以后|平时|每次|通常|长期|工作|学习|睡前)', text):
        return False
    if any(word in text for word in ('今天', '这次', '本次', '假如', '假设', '不要记', '别记', '歌词', '他说', '她说', '如果',
        '不保存', '不要保存', '别保存', '不用保存', '不想保存', '只是聊', '引用', '转述', '举个例子')):
        return False
    stable = any(word in text for word in ('以后', '平时', '通常', '长期', '每次', '一直', '记住'))
    opinion = re.search(r'喜欢|偏爱|偏好|多推|少推|少听|不听|别.*推|不要.*推|介绍', text)
    scenes = {memory.canonical_scene(scene) for scene in SCENES if scene in text}
    if any(word in text for word in ('工作', '写代码')):
        scenes.add('工作专注')
    return bool(stable and opinion and len(scenes) <= 1)


def memory_exchange(message):
    """只识别历史管理语句用于上下文隔离；不解析对象、不执行存储。"""
    from app.preference_dialogue import _PREF, _RELATION, _EXPRESSION
    from app.scene_memory_dialogue import _SAVE, _REVOKE
    from app import reference_choice, reference_direction
    text = (message or '').strip().rstrip('。！!')
    pref = _PREF.fullmatch(text)
    return bool(eligible(text) or _SAVE.fullmatch(text) or _REVOKE.fullmatch(text) or _RELATION.fullmatch(text)
        or reference_direction.SAVE_REFERENCE.fullmatch(text) or reference_choice.is_reply(text)
        or (pref and (pref['future'] or pref['scene'] or text.startswith('记住')))
        or (text.startswith(('以后','记住')) and any(re.fullmatch(pattern,text) for pattern,_,_ in _EXPRESSION))
        or re.fullmatch(r'(?:取消|撤销|忘掉).+偏好',text))


def execute(args, session_id=None):
    raw = memory._request_query.get() or ''
    if not session_id or not eligible(raw) or set(args).difference({'kind','target','value','scene','reference'}):
        return {'ok': False, 'message': '当前原话没有明确授权这项长期偏好，未保存；请明确对象和范围。'}
    try:
        kind, target, value = args['kind'], args['target'], args['value']
        if kind in ('song', 'artist') and target in ('这首', '这首歌', '这位歌手'):
            selected = library.find_song_by_id(memory._selected_song.get()) if memory._selected_song.get() else None
            if not selected:
                raise ValueError('请先选定歌曲，再使用当前作品的指代。')
            if kind == 'song':
                target = selected['id']
            else:
                from app.retrieval import artist_identity_keys
                if len(artist_identity_keys(selected.get('artist'))) != 1:
                    raise ValueError('合作署名不能猜指的是哪位歌手。')
                target = selected['artist']
        key, snapshot = memory.resolve_target(kind, target, args.get('reference') or '')
        scene = memory.canonical_scene(args.get('scene') or '')
        observed_scene = memory.context_scene(raw)
        if scene != observed_scene:
            raise ValueError('场景条件必须与本条原话一致，不能省略或补充。')
        label = snapshot.get('title') or snapshot.get('label') or ''
        if kind in ('song','artist'):
            pointed = memory._selected_song.get()
            selected = library.find_song_by_id(pointed) if pointed else None
            implicit = bool(selected and ((kind == 'song' and str(selected['id']) == str(snapshot['id']) and '这首' in raw)
                or (kind == 'artist' and selected['artist'].casefold() == label.casefold() and '这位歌手' in raw)))
            if label.casefold() not in raw.casefold() and not implicit:
                raise ValueError('对象未在本条原话中明确出现。')
        elif kind == 'genre':
            # 完整词边界拒绝把“太空爵士”等未知子风格自动扩大成“爵士”。
            pattern = rf'(?:喜欢|偏爱|偏好|推荐|多推|少推|少听|不听)(?:点|些|一些|一点|比较|更|听)?\s*{re.escape(label)}(?:音乐|风格|的歌|歌曲|歌|一点|一些)?(?:[，。！!\s]|$)'
            if not re.search(pattern, raw, re.I):
                raise ValueError('风格对象需要在原话中明确、完整出现。')
        elif kind == 'reference':
            if label not in raw or snapshot['reference']['title'] not in raw:
                raise ValueError('必须明确参考曲和对照曲两方。')
        elif kind == 'expression' and '介绍' not in raw:
            raise ValueError('没有明确要求改变DJ介绍。')
        if kind == 'expression':
            expected = ('quiet' if re.search('不用介绍|不要介绍|直接听|直接放', raw) else 'short' if re.search('简短|少说|短一点', raw)
                        else 'intro' if re.search('恢复介绍|保留介绍', raw) else 'normal' if re.search('正常|详细', raw) else None)
        elif kind == 'reference':
            expected = 'unlike' if '不像' in raw or '不太像' in raw else 'like' if '像' in raw else None
        else:
            expected = ('exclude' if re.search(r'不要.*推荐|别.*推|不再.*推荐|不听', raw) else
                'reduce' if re.search(r'少推|少听|不喜欢|不太喜欢', raw) else
                'prefer' if re.search(r'喜欢|偏爱|偏好|多推', raw) else None)
        if value != expected:
            raise ValueError('表态方向与本条原话不一致，未猜测保存。')
        with chat_turns.commit_guard() as current:
            if not current:
                return {'ok': False, 'message': '这轮已被新的要求替代，未保存。'}
            identifier = memory.save(session_id, kind, target, value, scene=scene, reference=args.get('reference') or '', source_text=raw)
        return {'ok': True, 'id': identifier, 'message': f'已记住：{scene or "一般偏好"}，{label} → {memory.LABELS[value]}。可在“DJ 记忆”修改或撤销。'}
    except (KeyError, ValueError, TypeError) as error:
        return {'ok': False, 'message': f'这项提案未保存：{error}'}
    except sqlite3.Error:
        return {'ok': False, 'message': '偏好写入未确认，请稍后在“DJ 记忆”重新读取核对。'}
