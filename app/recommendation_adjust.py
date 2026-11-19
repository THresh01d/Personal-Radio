"""本轮调整，不写 feedback 或收听事实；只调用既有检索与硬规则校验。"""

from copy import deepcopy
import uuid

from app import conversation, dj_program, library
from app.recommendation_state import _intent_to_args, _with_contract
from app.retrieval import artist_quota_counts
from app.tools import execute_tool
from app.relative_strength import observed_summary

LABELS = {"replace": "只换这一首", "gentler": "更温柔一点", "energize": "更有劲一点",
          "other-artists": "换些歌手", "refresh": "保持方向，换一批"}


def adjust_recommendation(session_id, batch_id, operation, song_id, db_path):
    program = dj_program.read_program(batch_id, session_id, db_path, internal=True)
    if not program:
        raise LookupError("这轮推荐已不存在，请重新向 DJ 点歌")
    return adjust_program(session_id, batch_id, operation, song_id, db_path, program)


def adjust_program(session_id, batch_id, operation, song_id, db_path, program, *, selection=None, direction=None):
    previous = program["songs"]
    if not previous:
        raise ValueError("这一轮没有可以调整的歌曲")
    if operation == "replace" and song_id not in {s["id"] for s in previous}:
        raise ValueError("要替换的歌曲不属于这轮推荐")
    intent = deepcopy(program["intent"])
    current = conversation.get_recommendation_state(session_id, db_path=db_path) or {}
    # 历史批次可以重新调整，但属于新任务，不能复活原任务的临时意见。
    if not intent.get('_feedback_task_id') or intent.get('_feedback_task_id') != current.get('_feedback_task_id'):
        intent['_feedback_task_id'] = uuid.uuid4().hex
    # 旧批次缺少意图时只继承真实曲库边界，不从文案反推隐含要求。
    intent.setdefault("query", program["query"])
    intent.setdefault("instrumental", False)
    # 替换条件仅给新选中的一首；整批依然继承原任务和原参考曲。
    intent["target_count"] = len(previous)
    intent["continuation"] = True
    intent.pop('_replacement_direction', None)
    if operation == "gentler":
        intent.update(moods=["温柔", "平静"], energy="low")
    elif operation == "energize":
        intent.update(moods=["热烈"], energy="high")
    elif operation == "other-artists":
        intent["artists"] = []
    if operation in ('gentler', 'energize') and intent.get('activity'):
        intent['activity'].update(energy=intent['energy'], energy_source='user_text')
    intent = _with_contract(intent)
    selection_intent = {**intent, **(selection or {})}
    if direction:
        requested = set(direction.get('genres') or ())
        selection_intent['excluded_genres'] = list(dict.fromkeys([
            *(item for item in intent.get('excluded_genres', []) if item not in requested),
            *direction.get('exclude', []),
        ]))
    args = _intent_to_args(selection_intent, {})
    if direction:
        args['_replacement_direction'] = direction
    args.update(count=50, exclude_recent=True, _defer_copy=True,
                _exclude_song_ids=[s["id"] for s in previous])
    if operation in ('gentler', 'energize'):
        args['_relative_strength_direction'] = 'lower' if operation == 'gentler' else 'higher'
        args['_relative_strength_batch_id'] = batch_id
    if operation == "other-artists":
        args["_exclude_artists"] = list({s["artist"] for s in previous})
    if operation == "replace":
        args["_single_replacement"] = True
        counts = artist_quota_counts(s for s in previous if s["id"] != song_id)
        if not selection_intent.get("artists"):
            args["_exclude_artists"] = [a for a, n in counts.items() if n >= 2]
    result = execute_tool("recommend_playlist", args, session_id=session_id)
    next_songs = result.get("songs") or []
    # 即便未来工具实现变化，也不接受曲库外 ID 或重复的替换结果。
    known = library.find_songs_by_ids([s["id"] for s in next_songs])
    blocked = {s["id"] for s in previous}
    next_songs = [s for s in next_songs if s["id"] in known and s["id"] not in blocked]
    if result.get('relative_strength', {}).get('queue_preserved'):
        intent['_strength_result'] = result['relative_strength']
        return [], intent, LABELS[operation]
    if not next_songs:
        raise LookupError("曲库里暂时没有符合这次要求的新歌，先保留当前队列。")
    if operation == "replace":
        replacement = next_songs[0]
        if direction and selection_intent.get('reference_song_id'):
            from app import preference_memory
            ref = library.find_song_by_id(str(selection_intent['reference_song_id']))
            if ref:
                intent['_replacement_direction'] = {'reference_id': str(ref['id']),
                                                    'fingerprint': preference_memory._snapshot(ref)['fingerprint'],
                                                    'direction': direction}
        if selection_intent.get('reference_song_id') != intent.get('reference_song_id'):
            # 整批仍可继承原参考；另一首参考得到的新歌不冒充原参考的反馈对象。
            replacement = {**replacement, 'reference_song_id': None, 'reference_title': None}
        next_songs = [replacement if s["id"] == song_id else s for s in previous]
    if result.get('relative_strength'):
        # 统一出口展示最终校验后的覆盖；只作本次传递，不混入持久音乐意图。
        intent['_strength_result'] = observed_summary(next_songs, result['relative_strength'], len(previous), available=len(next_songs))
    message = LABELS[operation]
    return next_songs, intent, message
