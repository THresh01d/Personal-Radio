"""推荐结果的唯一提交出口：消息、批次、意图和DJ节目。"""

import sqlite3
import re
from app import conversation, listening, dj_program, chat_turns
from app.diagnostics import current_summary
from app.ranking import reference_priority_summary
from app.scene import activity_selection_summary
from app.safe_logging import log_failure
from app.reply_policy import format_recommendation
from app.rain_context import rain_direction_summary
from app.relative_strength import capture_baseline
from app.style_request import selection_message


def _finish_chat_response(session_id, message, answer, songs, stats=None):
    """只有仍有效的聊天轮次才能追加历史、创建批次和更新当前任务。"""
    with chat_turns.commit_guard() as current:
        if not current:
            return {"answer": "这轮请求已被新的需求替代。", "songs": [], "session_id": session_id,
                    "batch_id": None, "dj_program": None,
                    "stats": {"picks": 0, "superseded": True, "channel": "superseded", "diagnostics": current_summary()}}
        return _commit_chat_response(session_id, message, answer, songs, stats)


def _commit_chat_response(session_id, message, answer, songs, stats=None):
    """统一完成一轮聊天：落消息、建推荐批次、组响应。

    这是一个重要的编排边界。项目里有歌手直通、点头直通、Agent、降级等多条
    返回路径；若每条路径各写一遍落库逻辑，新字段上线时很容易只改中一部分。
    所有分支从这里返回，推荐批次这个不变量才不会再次漏记。
    """
    songs = list(songs or [])
    stats = dict(stats or {})
    replacement_reply = answer if stats.get('replacement') else None
    replacement_pair = stats.pop('_replacement_pair', None)
    original_replacement_count = len(songs)
    selection_notices = []
    notices = {}
    recommendation_intent = stats.pop("_recommendation_intent", None)
    from app import preference_memory
    pref_query = (recommendation_intent or {}).get('query') or message
    preferences = preference_memory.relevant(session_id, pref_query, reference_id=(recommendation_intent or {}).get('reference_song_id') or '') if recommendation_intent or songs else []
    if preferences:
        original_count = len(songs)
        songs = [song for song in songs if not any(preference_memory.blocked(row, song) for row in preferences)]
        stats['preferences'] = [{'id': row['id'], 'kind': row['kind'], 'scene': row['scene'], 'value': row['value']} for row in preferences]
        if original_count != len(songs):
            answer = '已按你保存的持续排除或参考反例避开对应歌曲；符合条件的其他候选不足。'
            selection_notices.append(answer)
    from app.scene_memory import matching
    memory = matching(session_id, recommendation_intent, query=message) if recommendation_intent or songs else []
    if memory:
        blocked = {r['song_id'] for r in memory}
        original_count = len(songs)
        songs = [song for song in songs if str(song['id']) not in blocked]
        stats['scene_memory'] = {'scene': memory[0]['scene'], 'applied': len(memory)}
        if original_count != len(songs):
            answer = '已按你保存的场景偏好避开指定歌曲。' + ('符合条件的其他候选不足，先保留这些。' if songs else '暂时没有其他合适候选。')
            selection_notices.append(answer)
    if recommendation_intent:
        recommendation_intent = dict(recommendation_intent)
        strength = recommendation_intent.pop('_strength_result', None)
        if strength:
            stats['strength_direction'] = strength
        recommendation_intent = {key: value for key, value in recommendation_intent.items()
                                 if key not in ('_reply_presentation', '_strength_baseline', '_replacement_pair')}
        if not stats.get('replacement'):
            recommendation_intent.pop('_replacement_direction', None)
    # 候选生成途中也可能收到反馈；提交前再核对一次，不能把刚排除的歌曲补回来。
    if recommendation_intent and recommendation_intent.get('_feedback_task_id'):
        from app.task_feedback import active_feedback
        blocked = set(active_feedback(session_id, recommendation_intent['_feedback_task_id'])['song_ids'])
        remaining = [song for song in songs if str(song['id']) not in blocked]
        if len(remaining) != len(songs):
            songs = remaining
            answer = '已避开你标记为这次不合适的歌曲。' + ('本次符合条件的候选不足，先保留这些。' if songs else '暂时没有其他合适候选。')
            selection_notices.append(answer)
    if stats.get('replacement') and len(songs) != original_replacement_count:
        songs, recommendation_intent = [], None
        stats.pop('replacement')
        stats['queue_preserved'] = True
        answer = '这次替换与现有偏好冲突，先保留当前队列。'
    save_context = stats.pop("_save_task_context", False)
    queue_preserved = bool((stats.get('strength_direction') or {}).get('queue_preserved'))
    if queue_preserved:
        # 不是曲库零命中；不能让语种／风格的空结果兜底盖掉“未调整、保留队列”。
        songs = []
        answer = stats['strength_direction']['message']
        stats['queue_preserved'] = True
    if save_context and not songs and recommendation_intent:
        try:
            conversation.save_recommendation_state(session_id, recommendation_intent)
        except sqlite3.Error as error:
            log_failure("task_context_save_failed", error)
            stats['context_updated'] = False
            answer = "这次没能更新音乐方向，可以稍后再试。"
        else:
            stats['context_updated'] = True
    target = len(songs)
    language_notice = ""
    style_notice = ""
    style = (recommendation_intent or {}).get("style_request")
    if recommendation_intent:
        required = recommendation_intent.get("required_genres") or []
        from app.tools import resolve_target_count
        target = resolve_target_count(recommendation_intent.get("query") or message, recommendation_intent.get("target_count"))
        if required and not style and not songs and not queue_preserved:
            answer = "当前曲库没有足够的已知风格标签支持这次请求，先不硬凑。请在歌单同步中补全百科；这不表示曲库一定没有此类歌曲。"
        # 结构化意图是调试推荐是否“听懂了”的稳定视图；只含用户请求与规则字段，
        # 不含密钥、Cookie 或模型内部推理。
        stats["intent"] = recommendation_intent
        language = recommendation_intent.get('language')
        if language and not save_context and not queue_preserved:
            language_notice = (f"平台明确标注为{language}且符合条件的歌曲不足{target}首，未用语种未知的歌曲补足。"
                               if len(songs) < target else '')
            stats['language_evidence'] = {'required': language, 'basis': 'platform_reported',
                                          'selected': len(songs), 'target': target, 'message': language_notice}
            if not songs:
                # 零结果在Agent／离线降级也说明证据边界，不能误称曲库没有该语种。
                answer = f"当前曲库没有平台明确标注为{language}且符合其他条件的歌曲，未用语种未知的歌曲补足。这不表示曲库一定没有此语种作品。"
        if style and not save_context and not queue_preserved:
            style_notice = selection_message(style, songs, target)
            if not songs:
                answer = ((answer + " ") if language else "") + style_notice
                if style["mode"] == "strict":
                    # 有明确的方向也可能零命中。记住方向供继续寻找，不生成批次或更换播放队列。
                    try:
                        conversation.save_recommendation_state(session_id, recommendation_intent, db_path=conversation.DB_PATH)
                    except sqlite3.Error as error:
                        log_failure("task_context_save_failed", error)
                        answer += " 这次没能保存后续搜索方向，可以重新发一次条件。"
                    stats["queue_preserved"] = True

    if memory and not queue_preserved:
        memory_notice = f"本轮沿用你保存的{memory[0]['scene']}单曲排除；如需恢复，可在“DJ 记忆”撤销，或说“取消{memory[0]['scene']}偏好”。"
        answer += ' ' + memory_notice
        selection_notices.append(memory_notice)
    priority = reference_priority_summary(songs, target)
    if priority:
        stats['reference_style_priority'] = priority
        notice = priority['message']
        if notice and notice not in answer:
            answer += ' ' + notice
        elif priority['enabled'] and priority['matched'] and not notice:
            answer = '本轮先按参考歌曲的风格标签挑选。' + answer
    remembered = stats.get('reference_direction')
    if remembered and songs:
        label = remembered['label']
        notice = (f'这轮按你记下的「{label}」优先找。' if remembered['matched']
                  else f'你记下的方向是「{label}」，这轮曲库暂缺符合这个方向的候选。')
        selection_notices.append(notice)
        answer += ' ' + notice
    activity = activity_selection_summary(songs)
    if activity:
        stats['activity_direction'] = activity
        if activity['message'] and activity['message'] not in answer:
            answer += ' ' + activity['message']
    presentation_state = None
    rain = rain_direction_summary(songs, target)
    if rain:
        stats['rain_music_direction'] = rain
        if rain['message'] and rain['message'] not in answer:
            answer += ' ' + rain['message']
    if songs and recommendation_intent:
        previous = conversation.get_recommendation_state(session_id)
        notices = {
            'reference': {'message': (priority or {}).get('message') or ''},
            # 只比较面向用户的限制状态。支持标签由1首变2首但仍缺能量时，不重复同一句话；
            # 完整覆盖数仍保留在stats中，状态恢复或说明发生变化时会重新提示。
            'activity': {'message': (activity or {}).get('message') or ''},
            'required_genres': {'message': '已知风格标签支持的歌曲不足目标数量，未用未知风格歌曲补足。'
                                if recommendation_intent.get('required_genres') and len(songs) < target else ''},
            'style': {'message': style_notice},
            'strength': {'message': ''},
            'language': {'message': language_notice},
            'rain': {'message': (rain or {}).get('message') or ''},
        }
        if stats.get('strength_direction'):
            notices['strength'] = {'message': stats['strength_direction']['message']}
        elif stats.get('channel') == 'task_adjustment' and recommendation_intent.get('energy') in ('low', 'medium-low'):
            observed = sum(song.get('acoustic_score') is not None for song in songs)
            notice = ('这批暂无可比的能量资料，暂不能确认比上一批更安静。' if not observed
                      else '这轮参考已有片段的能量资料，尚不能保证比上一批更安静。')
            strength = {'message': notice, 'energy_observed': observed, 'selected': len(songs)}
            stats['strength_direction'] = strength
            notices['strength'] = {'message': notice}
        answer, presentation_state, stats['presentation'] = format_recommendation(
            answer, message, songs, recommendation_intent, previous, notices)
    if songs and (stats.get('strength_direction') or {}).get('basis') == 'relative_profile_tags':
        # 每一批标签尝试都要保留证据边界，不能被普通限制提示的去重吞掉。
        if '未经声学比较确认' not in answer:
            answer += ' 本轮仍依据档案标签尝试调整，未经声学比较确认。'
        selection_notices.append('本轮依据档案标签调整，未经声学比较确认。')
    if not songs and stats.get('strength_direction'):
        notice = stats['strength_direction']['message']
        if notice and notice not in answer:
            answer += ' ' + notice
    settings = preference_memory.expression(session_id, pref_query) if songs else {}
    if preferences:
        exclusions = [r for r in preferences if r['value'] == 'exclude']
        if exclusions and not songs and not queue_preserved:
            conflict = exclusions[0]
            example = (f"本次例外，来几首{conflict['label']}的歌" if conflict['kind'] == 'artist' else
                       f"本次例外，来几首{conflict['label']}歌曲" if conflict['kind'] == 'genre' else
                       f"本次例外，播放《{conflict['label']}》")
            answer += f' 本轮存在你保存的持续排除。可以在“DJ 记忆”解除，或明确说“{example}”。'
        if songs and (settings.get('mode') == 'quiet' or settings.get('length') == 'short'):
            # 留下真实边界提示，避免用简短偏好裁掉语种/相对强度等重要说明。
            answer = ('好，直接听歌。' if settings.get('mode') == 'quiet' else '好，这轮选了' + str(len(songs)) + '首。') + (' ' + language_notice if language_notice else '')
            for notice in ((priority or {}).get('message'), (activity or {}).get('message'), (rain or {}).get('message'), (stats.get('strength_direction') or {}).get('message')):
                if notice and notice not in answer:
                    answer += ' ' + notice
            if (stats.get('strength_direction') or {}).get('basis') == 'relative_profile_tags' and '未经声学比较确认' not in answer:
                answer += ' 本轮依据档案标签调整，未经声学比较确认。'
    stats["picks"] = len(songs)
    if stats.get('channel') not in ('scene_memory', 'memory_read'):
        claim_text = re.sub(r'《[^》]*》|“[^”]*”|「[^」]*」', '', answer or '')
        if re.search(r'记住了|已(?:经)?记住|已(?:经)?保存.*偏好|已(?:经)?撤销.*偏好|已(?:经)?忘记', claim_text):
            memory_notice = '本轮没有执行长期偏好的保存或撤销；请在“DJ 记忆”确认对象与范围。'
            answer = memory_notice + (f' 本轮选了{len(songs)}首歌。' if songs else '')
            selection_notices.append(memory_notice)
            for notice in (language_notice, (priority or {}).get('message'), (activity or {}).get('message'), (rain or {}).get('message'), (stats.get('strength_direction') or {}).get('message')):
                if notice and notice not in answer:
                    answer += ' ' + notice
    if songs and songs[0].get("ranking_version"):
        stats["ranking_version"] = songs[0]["ranking_version"]
    song_ids = [str(song["id"]) for song in songs]
    if songs and (recommendation_intent or {}).get('reference_song_id'):
        from app import library
        reference = library.find_song_by_id(str(recommendation_intent['reference_song_id']))
        if reference:
            songs = [{'reference_song_id': str(reference['id']), 'reference_title': reference['title'], **song} for song in songs]

    original_request = preference_memory._request_query.get()
    display_message = original_request if preference_memory._exception_query.get() and original_request else message
    # 换歌确认描述的是具体操作；短介绍、静默偏好或节目正文都不能抹掉位置和歌名。
    if stats.get('replacement'):
        answer = replacement_reply
    shared = bool(songs and preference_memory._dj_length.get() is not None)
    reply_notices = []
    if shared:
        repeated = (stats.get('presentation') or {}).get('repeated_notices', [])
        reply_notices = list(dict.fromkeys([
            *selection_notices,
            *(record['message'] for name, record in notices.items() if record['message'] and name not in repeated),
        ]))
        if stats.get('degraded'):
            reply_notices.append('本轮使用本地基础推荐。')
        if not stats.get('replacement'):
            answer = '好，直接听歌。' if settings.get('mode') == 'quiet' else f'已选好 {len(songs)} 首。'
    if style and songs and not stats.get('replacement'):
        details = [f"《{song['title']}》{song['style_match']['reason']}" for song in songs
                   if song.get('style_match', {}).get('kind') in ('near', 'mood_unknown')]
        if shared:
            reply_notices = list(dict.fromkeys(line for line in [*reply_notices, style_notice, *details] if line))
        else:
            additions = [line for line in (style_notice, *details) if line and line not in answer]
            if additions:
                answer += "\n\n" + "\n".join(additions)
    conversation.add_message(session_id, "user", display_message)
    message_id = conversation.add_message(
        session_id, "assistant", answer, related_song_ids=song_ids
    )
    batch_id = listening.record_recommendation_batch(
        session_id,
        display_message,
        song_ids,
        channel=stats.get("channel"),
        # 跟随 conversation.DB_PATH，测试替换临时库时不会污染真实数据。
        db_path=conversation.DB_PATH,
    )
    # 只有已经通过工具校验、真正返回给用户的歌单才能更新会话方向。
    # 空结果、闲聊和诚实拒绝都不能覆盖上一轮有效状态。
    if songs and recommendation_intent:
        saved_intent = dict(recommendation_intent)
        if replacement_pair and batch_id and stats.get('replacement'):
            saved_intent['_replacement_pair'] = {**replacement_pair, 'batch_id': batch_id}
        # 比较依据仅属于刚返回的当前批次；新场景声明、清空及空结果不伪造新前批。
        saved_intent['_strength_baseline'] = capture_baseline(songs)
        if presentation_state and presentation_state['shown']:
            saved_intent['_reply_presentation'] = presentation_state
        conversation.save_recommendation_state(
            session_id, saved_intent, db_path=conversation.DB_PATH
        )
    program = None
    if batch_id:
        confirmation_only = bool(stats.get('replacement'))
        program = dj_program.create_program(batch_id, session_id, songs, message,
                                             recommendation_intent, conversation.DB_PATH,
                                             **({'confirmation_only': True} if confirmation_only else {}),
                                             **({'message_id': message_id, 'notices': reply_notices} if shared else {}))
        if not confirmation_only:
            dj_program.schedule_program(batch_id, session_id, conversation.DB_PATH)
    stats["diagnostics"] = current_summary()
    return {
        "answer": answer,
        "songs": songs,
        "stats": stats,
        "session_id": session_id,
        "message_id": message_id,
        "batch_id": batch_id,
        "dj_program": program,
    }

