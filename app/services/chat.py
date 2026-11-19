"""聊天业务编排：确定性直通、Agent、安全网及降级共用既有推荐工具。

不创建FastAPI应用；HTTP层负责参数校验与错误状态码。本模块的依赖可独立替换验证。
"""

import random
import re
import secrets
from app import conversation, listening, dj_program, chat_turns, reference_direction
from app.agent_loop import (
    _VIEWPOINTS, _artist_request_guard, _missing_artist_reply,
    build_system_prompt, degraded_answer, degraded_chat_answer, resolve_follow_up_query, run_agent,
    _CHAT_ONLY_REPLY,
)
from app.diagnostics import note
from app.safe_logging import log_failure
from app.intent import fast_reply, looks_like_song_request, direct_music_control, explicit_chat_only, trim_music_invitation
from app.llm_provider import request_chat
from app.recommendation_state import (_intent_to_args, resolve_recommendation, song_reference_mode,
                                      task_energy_adjustment, task_scene_declaration, task_scene_changed)
from app.retrieval import build_dj_opener
from app.tools import DEFAULT_TARGET_COUNT, execute_tool, resolve_target_count
from app.services.chat_policy import (
    _requests_unheard_tracks, _resolve_consent, _parse_events,
)
from app.services.chat_response import _finish_chat_response
from app.reply_policy import brief_sentences, clean_chat_reply
from app.relative_strength import direction_from_message, task_refresh_request

_looks_like_song_request = looks_like_song_request


def _new_session_id():
    """生成会话 ID：随机 12 位 hex，无需登录、无需服务端预创建。"""
    return secrets.token_hex(6)


def _acoustic_similarity_recommend(session_id, message, song_id):
    """“类似这首”直达声学召回；档案不足时诚实退回语义和标签。"""
    # 书名号内是作品名，不能把《某某民谣》里的“民谣”误当本次硬风格要求。
    # 原始消息仍交给提交层保存；参考对象以核实后的 ID 传递。
    direction = reference_direction.NAMED_REFERENCE.sub('参考作品', message)
    args, intent = resolve_recommendation(session_id, direction, {"query": direction, "reference_song_id": str(song_id)})
    args["exclude_recent"] = True
    result = execute_tool("recommend_playlist", args, session_id=session_id)
    songs = result.get("songs") or []
    reference = result.get("reference_song") or {}
    title = reference.get("title") or "当前这首歌"
    acoustic_matches = sum(s.get("transition_score") is not None for s in songs)
    if result.get("error") in ("reference_not_found", "reference_evidence_insufficient"):
        answer = result["message"]
    elif songs:
        if result.get("acoustic_reference_available") and acoustic_matches == len(songs):
            answer = f"我以《{title}》为参照，按已有声学特征接了一组不会重复当前歌曲的歌。"
        elif result.get("acoustic_reference_available") and acoustic_matches:
            answer = f"参考《{title}》，其中{acoustic_matches}首有可比声学特征，其余是符合当前条件的补充候选。"
        elif result.get("acoustic_reference_available"):
            answer = f"《{title}》的声学档案已有，但这批候选缺少可比特征，先保留符合当前条件的歌。"
        else:
            answer = f"我参考《{title}》的元数据与标签接了一组歌；暂无可用声学匹配。"
    else:
        answer = f"《{title}》附近暂时没有足够候选，我先不拿无关歌曲硬凑。"
    return _finish_chat_response(
        session_id, message, answer, songs,
        {
            "channel": "acoustic_similarity",
            "reference_song_id": str(song_id),
            "acoustic_reference_available": result.get("acoustic_reference_available", False),
            "acoustic_matches": acoustic_matches,
            "reference_style_hint": result.get("reference_style_hint"),
            "reference_direction": result.get("reference_direction"),
            "_recommendation_intent": intent,
        },
    )


def _artist_from_recommendation_state(session_id, message):
    """续播时从确定性状态读取歌手；聊天原文只保留为兼容兜底。"""
    _, intent = resolve_recommendation(
        session_id, message, {"query": message}
    )
    artists = intent.get("artists") or []
    if intent.get("continuation") and artists:
        return artists[0]
    return None


def _artist_direct_recommend(session_id, message, context_messages, artist):
    """点名歌手的明确点歌直通正式歌单，避免 Agent 只写口播却不出歌。"""
    continued_query = resolve_follow_up_query(context_messages)
    query = continued_query or message
    draft_args = {
        "query": query,
        "artist": artist,
        # 「再来几首」本身就是续播语义：即使没说「其他歌」，用户也期待
        # 下一批而不是固定排序的前三首重播。库存不足 3 首时工具会自动降级，
        # 因此不会让小歌库变成空结果。
        "exclude_recent": bool(continued_query) or _requests_unheard_tracks(message),
    }
    requested_count = resolve_target_count(message)
    if requested_count != DEFAULT_TARGET_COUNT:
        # “多来几首”会回溯上一轮 query；若不把 5 显式带上，旧 query 的默认 3
        # 会覆盖本轮数量意图。数量在这里钉死，避免依赖 Agent 临场理解。
        draft_args["target_count"] = requested_count
    args, intent = resolve_recommendation(
        session_id,
        message,
        draft_args,
    )
    result = execute_tool("recommend_playlist", args, session_id=session_id)
    songs = result.get("songs") or []
    if songs:
        answer = result.get("dj_opener") or _guard_playlist_reply(message, songs)
    else:
        answer = f"你的本地歌库未找到与「{artist}」匹配且符合本轮条件的作品，我先不拿其他歌手凑数。"
    return _finish_chat_response(session_id, message, answer, songs, {
        "channel": "artist_direct", "_recommendation_intent": intent,
    })


def _summarize_messages(early_messages):
    """把早期对话交给当前模型压缩成摘要。

    conversation.get_context 在超长对话时调用它；失败时返回占位摘要，
    不让会话因为摘要失败而卡死（代价只是多带几句早期原文）。
    """
    text = "\n".join(f"{m['role']}: {m['content']}" for m in early_messages)
    try:
        data = request_chat(
            [
            {
                "role": "system",
                "content": (
                    "把下面的电台对话压缩成 3-4 句中文摘要。"
                    "必须保留用户提过的人名、歌名、风格/情绪偏好，方便后续轮次引用。"
                    "只输出摘要正文，不要多余文字。"
                ),
            },
            {"role": "user", "content": text},
            ],
            temperature=0.3,
            max_tokens=300,
            timeout=30,
        )
        return data["choices"][0]["message"]["content"].strip() or "（早期对话）"
    except Exception as e:
        log_failure("conversation_summary_failed", e)
        return "（早期对话）"


def _guard_playlist_reply(message, songs):
    """守卫只写整轮短开场；逐首事实已经存在歌曲卡片与 dj_intro 中。"""
    if not songs:
        return "这次没有找到足够贴合的候选，我先不拿无关歌曲凑数。"
    return build_dj_opener(songs, message)


def _guard_playlist_args(message, messages=None):
    """安全网调用推荐工具时的参数：若消息明确点名歌手，必须注入 artist 限定，
    否则歌单会混入相似风格的其他歌手（『放几首keshi的歌』变成 keshi+xxxmiracle）。"""
    # 安全网也必须使用与 Agent 正常路径完全相同的续播解析。
    # 否则『来几首孙燕姿』→『再来几首』在 Agent 漏调工具时会退回空 query，
    # 让相似度检索混进其他歌手，恰好绕过了本应保护用户意图的守卫。
    context = messages or [{"role": "user", "content": message}]
    query = resolve_follow_up_query(context) or message
    args = {"query": query}
    guard = _artist_request_guard(context)
    if guard is not None:
        args["artist"] = guard[0]
    return args


def _apply_playlist_guard(message, events, session_id=None, messages=None):
    """歌单契约（安全网）：Agent 该出歌单却编了文案（没调 recommend_playlist）
    时，用真实歌单重写响应，杜绝幻觉歌名展示给用户。

    只在『消息明显在点歌』且『事件流里没有 recommend_playlist 结果』时触发；
    正常走了工具的请求不受影响。session_id 透传给推荐工具做防重复排除，
    与正常 Agent 路径保持一致（安全网补的歌单也不能刚推过又推一遍）。
    """
    _, songs, stats = _parse_events(events)
    if songs or not _looks_like_song_request(message):
        return None  # 不需要守卫

    # Agent 已确定性诚实拒绝（歌手 0 命中短路，steps 里有 guard 标记）：
    # 拒绝文案就是最终答复，绝不能再用相似歌单覆盖它。
    steps = list((stats or {}).get("steps") or [])
    if any(s.startswith("guard:artist_zero_hit") for s in steps):
        return None
    if (stats.get("_recommendation_intent") or {}).get("style_request"):
        # 风格工具已经返回真实空结果，无需重复检索并丢掉原来的条件。
        return None

    args, intent = resolve_recommendation(
        session_id,
        message,
        _guard_playlist_args(message, messages),
    )
    result = execute_tool("recommend_playlist", args, session_id=session_id)
    real_songs = result.get("songs") or []
    if not real_songs:
        # 工具 0 命中：模型手里没有任何真实歌单可写。
        # 如果它仍然输出了歌名，那必然是编造（工具没返回任何歌）。
        # 因此必须用确定性『没找到』覆盖，绝不保留模型原文。
        steps = list((stats or {}).get("steps") or [])
        steps.append("guard:no_match")
        stats = dict(stats or {})
        stats["steps"] = steps
        stats["picks"] = 0
        stats["_recommendation_intent"] = intent
        return {
            "answer": (
                f"我在你的歌库里没找到符合「{message}」的歌，所以不硬凑。"
                "你可以换个说法，或去网易云收藏后再点同步。"
            ),
            "songs": [],
            "stats": stats,
        }

    stats = dict(stats or {})
    steps = list(stats.get("steps") or [])
    steps.append("guard:recommend_playlist")
    stats["steps"] = steps
    stats["guard_songs"] = True
    stats["picks"] = len(real_songs)
    stats["_recommendation_intent"] = intent
    return {
        "answer": result.get("dj_opener") or _guard_playlist_reply(message, real_songs),
        "songs": real_songs,
        "stats": stats,
    }


def _consent_reply(direction, songs):
    """点头后的回复也只保留整轮短开场，不在聊天区复述卡片。"""
    return _guard_playlist_reply(direction, songs)


def _consent_empty(direction):
    """按用户原话方向没检索到可推荐的歌：确定性『不硬凑』文案。"""
    return (
        f"我按你说的「{direction}」又翻了遍歌库，暂时没挑到更贴的，所以先不硬凑。"
        "可以换个说法，或去网易云多收藏几首再点同步。"
    )


def _consent_recommend(session_id, user_message, direction):
    """点头直通：走完整 recommend_playlist 校验链出真实歌单并落库。

    复用 execute_tool 同一入口，防幻觉 ID / 同歌手限流 / 防重复 / 逐首 DJ 台词
    （author_song_intros 在 tools handler 尾部）全部与正常 Agent 路径一致。
    """
    args, intent = resolve_recommendation(
        session_id, direction, {"query": direction}
    )
    result = execute_tool("recommend_playlist", args, session_id=session_id)
    real = result.get("songs") or []
    answer = (result.get("dj_opener") or _consent_reply(direction, real)) if real else _consent_empty(direction)
    stats = {
        "picks": len(real), "channel": "consent", "steps": ["consent:recommend"],
        "_recommendation_intent": intent,
    }
    return _finish_chat_response(session_id, user_message, answer, real, stats)


def _similarity_fallback(message, session_id, context_messages=None):
    """Agent 意外异常时整体降级：保留硬条件，走本地 recommend_playlist。

    与 Agent 模型异常共用本地文案和缺失歌手提示；有作品才出歌单，
    零命中诚实说明，不因异常丢失硬条件或拿其他作品补数。
    """
    if not _looks_like_song_request(message):
        return _finish_chat_response(session_id, message, degraded_chat_answer(), [],
                                     {"degraded": True, "channel": "chat_fallback"})
    draft_args = {"query": message}
    # 最外层异常也必须保住点名范围，不能因 Agent 自身出错而推荐其他歌手。
    guard = _artist_request_guard(context_messages or [{"role": "user", "content": message}])
    if guard is not None:
        draft_args["artist"] = guard[0]
    args, intent = resolve_recommendation(session_id, message, draft_args)
    result = execute_tool("recommend_playlist", args, session_id=session_id)
    songs = result.get("songs") or []
    answer = (_missing_artist_reply(guard[0]) if guard is not None and not guard[1]
              else degraded_answer(message, songs))
    return _finish_chat_response(
        session_id,
        message,
        answer,
        songs,
        {
            "degraded": True,
            "channel": "fallback",
            "_recommendation_intent": intent,
        },
    )


def _music_control_recommend(session_id, message, context_messages):
    """明确放歌短句直通；复用已有成功状态和裸续播，不引入新的任务继承规则。"""
    previous = conversation.get_recommendation_state(session_id)
    if previous:
        args, intent = resolve_recommendation(session_id, "再来几首", {"query": "再来几首"})
    else:
        # 只沿用明确的音乐表达，不把刚才倾诉的私人处境塞进选歌查询。
        direction = next((m["content"] for m in reversed(context_messages[:-1])
                          if m.get("role") == "user" and _looks_like_song_request(m.get("content"))
                          and not direct_music_control(m.get("content"))), "随便听听")
        args, intent = resolve_recommendation(session_id, direction, _guard_playlist_args(direction))
    args["exclude_recent"] = True
    result = execute_tool("recommend_playlist", args, session_id=session_id)
    songs = result.get("songs") or []
    answer = "好，接下来听歌。" if songs else result.get("message") or "当前没有找到符合条件的作品，先不硬凑。"
    return _finish_chat_response(session_id, message, answer, songs,
        {"channel": "music_control", "_recommendation_intent": intent})


def _adjust_task_energy(session_id, message):
    """音乐强度短句直接执行；用既有状态合并，不能猜测一轮不存在的音乐任务。"""
    if not conversation.get_recommendation_state(session_id):
        return _finish_chat_response(session_id, message, "你想调整哪一轮音乐？可以先告诉我场景或选一首歌。", [],
                                     {"channel": "task_missing"})
    args, intent = resolve_recommendation(session_id, message, {"query": message})
    # 只传本条明确短句的方向；比较基线由工具从当前会话读取，不信任模型提供的数值。
    args['_relative_strength_direction'] = direction_from_message(message)
    result = execute_tool("recommend_playlist", args, session_id=session_id)
    songs = result.get('songs') or []
    answer = "好，保留当前任务的其他条件，参考上一批挑选。" if songs else result.get('message') or "当前没有合适候选，先不硬凑。"
    return _finish_chat_response(session_id, message, answer, songs,
        {"channel": "task_adjustment", "_recommendation_intent": intent,
         "strength_direction": result.get('relative_strength')})


def _update_task_scene(session_id, message, scene):
    """场景声明不出歌；只有实际保存后才确认，失败不声称方向已更新。"""
    previous = conversation.get_recommendation_state(session_id)
    if not task_scene_changed(message, previous):
        return _finish_chat_response(session_id, message, "好，继续按当前音乐方向来。", [],
                                     {"channel": "task_context", "context_updated": False, "context_unchanged": True})
    _, intent = resolve_recommendation(session_id, message, {"query": message, "scene": scene})
    return _finish_chat_response(session_id, message, f"好，后续音乐按{scene}的方向来。", [],
        {"channel": "task_context", "_save_task_context": True, "_recommendation_intent": intent})


def _refresh_task(session_id, message):
    """只有明确选择普通换歌才执行；不改变原任务，也不冒称完成相对调整。"""
    if not conversation.get_recommendation_state(session_id):
        return _finish_chat_response(session_id, message, '请先推荐一批歌曲，再选择保持方向换歌。', [],
                                     {'channel': 'task_missing'})
    args, intent = resolve_recommendation(session_id, '再来几首', {'query': '再来几首'})
    args['exclude_recent'] = True
    result = execute_tool('recommend_playlist', args, session_id=session_id)
    songs = result.get('songs') or []
    answer = ('好，保持原方向换一批；这次是普通换歌，不作相对强度调整。' if songs
              else result.get('message') or '当前没有符合原条件的新候选。')
    return _finish_chat_response(session_id, message, answer, songs,
        {'channel': 'task_refresh', '_recommendation_intent': intent})


def respond(message: str, session_id: str | None = None, current_song_id: str | None = None, *,
            current_batch_id=None, current_queue_song_ids=None):
    """一个公开聊天请求对应一轮提交资格；迟到结果不能覆盖较新的用户要求。"""
    session_id = session_id or _new_session_id()
    from app.preference_memory import exception_scope
    with chat_turns.turn(session_id), exception_scope(message, current_song_id):
        return _respond(message, session_id, current_song_id, current_batch_id, current_queue_song_ids)


def _respond(message: str, session_id: str | None = None, current_song_id: str | None = None,
             current_batch_id=None, current_queue_song_ids=None):
    """核心接口：快速通道 + Agent 主循环 + 对话记忆落库。

    返回 {answer, songs, stats, session_id}：
    - answer：电台式回答（快速通道模板 / Agent 文案 / 降级文案）；
    - songs：正式歌单（来自 recommend_playlist 工具结果，已校验）或空；
    - stats：Agent 过程统计（iterations/tool_calls/tokens/耗时/degraded）；
    - session_id：本会话 ID（客户端保存，下次请求带回恢复上下文）。
    """
    message = (message or "").strip()
    if not message:
        message = "随便来首歌"
    session_id = session_id or _new_session_id()

    if re.match(r'^本次例外[，,:：\s]', message):
        from app.preference_proposal import memory_exchange
        bare = re.sub(r'^本次例外[，,:：\s]+', '', message)
        if memory_exchange(bare) or not _looks_like_song_request(bare):
            return _finish_chat_response(session_id, message, '本次例外只用于这一轮点歌，不会改变长期偏好；请同时明确要播放的歌曲或歌手。', [], {'channel':'memory_exception'})
        # 路由使用去掉前缀的请求，例外授权仍来自respond上下文中的原始用户消息。
        message = bare

    # 记忆操作在历史摘要和模型调用之前执行；只有本轮原话可授权保存或撤销。
    from app.scene_memory_dialogue import handle as handle_scene_memory
    from app.preference_proposal import eligible as preference_proposal_eligible
    from app import reference_choice
    propose_memory = preference_proposal_eligible(message)
    import sqlite3
    with chat_turns.commit_guard() as current:
        if not current:
            return _finish_chat_response(session_id, message, '', [], {})
        try:
            from app.preference_dialogue import handle as handle_preference
            memory_reply = handle_preference(session_id, message, current_song_id, current_batch_id=current_batch_id)
            if memory_reply is None:
                memory_reply = handle_scene_memory(session_id, message, current_song_id)
                if propose_memory and memory_reply and '没有识别出' in memory_reply:
                    memory_reply = None
        except ValueError as error:
            memory_reply = f'这次偏好没有保存：{error}'
        except sqlite3.Error as error:
            log_failure('scene_memory_write_failed', error)
            memory_reply = '这次记忆操作没有完成，请稍后重试，并在“DJ 记忆”核对；未确认保存或撤销成功。'
        if memory_reply is not None:
            memory_stats = {'channel': 'scene_memory'}
            text = message.rstrip('。！!')
            if reference_direction.SAVE_REFERENCE.fullmatch(text) or reference_choice.is_reply(text):
                memory_stats['queue_preserved'] = True
            return _finish_chat_response(session_id, message, memory_reply, [], memory_stats)

    # “这首”只认客户端当前选定的 ID，库内身份仍由工具核验，不假称服务端知道播放状态。
    # 参考歌曲尚无声学档案时，工具内部仍走原有语义/标签路径并明确标记降级。
    if not explicit_chat_only(message):
        from app.services.chat_replacement import handle as replace_from_chat
        replacement = replace_from_chat(session_id, message, current_song_id, current_batch_id, current_queue_song_ids)
        if replacement:
            return _finish_chat_response(session_id, message, *replacement)
    named_reference = reference_direction.NAMED_REFERENCE.search(message)
    if named_reference and _looks_like_song_request(message) and not explicit_chat_only(message):
        prefix = message[:named_reference.start()]
        if not re.search(r'(?:不要|不想|别|不是|不用|不必)[^，,。；;]*$', prefix):
            try:
                ref = reference_direction.resolve_named(named_reference['title'], named_reference['artist'], current_song_id)
            except ValueError as error:
                return _finish_chat_response(session_id, message, str(error), [], {'channel':'reference_missing'})
            return _acoustic_similarity_recommend(session_id, message, str(ref['id']))
    reference_mode = song_reference_mode(message)
    if not explicit_chat_only(message) and (reference_mode == "use" or (reference_mode == "context_use" and current_song_id)):
        if not (current_song_id or "").strip():
            return _finish_chat_response(session_id, message, "请先在播放器选一首歌，再告诉我“类似这首”。", [], {"channel": "reference_missing"})
        return _acoustic_similarity_recommend(
            session_id, message, current_song_id
        )

    # ① 快速通道：100% 确定的纯闲聊模板，零成本、不调 Agent。
    #    规则不确定时 fast_reply 返回 None，必须交给 Agent，禁止猜。
    if message.rstrip('？?。') in ('你记住了什么', '你还记得我的偏好吗', '我的偏好是什么', '查看我的偏好'):
        from app import preference_memory, scene_memory
        from app.feedback import get_taste
        rows = preference_memory.records(session_id)
        legacy = scene_memory.records(session_id)
        taste = get_taste(session_id)
        lines = [f"{r['scene'] or '一般偏好'}：{r['label']}，{r['value_label']}" + ('（资料变化，已暂停）' if not r['effective'] else '') for r in rows]
        lines.extend(f"{r['scene']}：避开《{r['song']['title']}》" + ('（资料变化，已暂停）' if not r['effective'] else '') for r in legacy)
        explicit_count = len(taste['boost_song_ids']) + len(taste['demote_song_ids'])
        answer = ('当前记录：\n' + '\n'.join(lines[:12]) if lines else '当前没有这类长期偏好记录。')
        if explicit_count:
            answer += f'\n另有{explicit_count}首单曲喜欢或减少推荐记录。'
        answer += '\n完整对象、范围和撤销入口可以在“DJ 记忆”查看。'
        return _finish_chat_response(session_id, message, answer, [], {'channel':'memory_read'})
    quick = fast_reply(message)
    if quick is not None:
        return _finish_chat_response(
            session_id, message, quick, [], {"channel": "fast_path"}
        )

    # 相对调整与显式普通换歌只用当前任务，不为读取长历史而触发付费摘要。
    if task_refresh_request(message):
        return _refresh_task(session_id, message)
    if task_energy_adjustment(message):
        return _adjust_task_energy(session_id, message)

    # ② 读历史（最近 10 轮原文 + 早期摘要），与系统提示拼成完整 messages
    ctx = conversation.get_context(session_id, summarizer=_summarize_messages, for_model=True)
    request_context = [*ctx["messages"], {"role": "user", "content": message}]

    if propose_memory:
        from app.preference_proposal import TOOL
        try:
            events = list(run_agent([{'role':'system','content': '把本条明确持续偏好转换为remember_preference提案。只认本条原话，不执行推荐，不从历史补对象或条件；不确定就澄清。'}, {'role':'user','content':message}], tools=[TOOL], session_id=session_id))
        except Exception as error:
            log_failure('memory_proposal_failed', error)
            events = []
        results = [ev.get('result') or {} for ev in events if ev.get('type') == 'tool_result' and ev.get('name') == 'remember_preference']
        reply = results[-1].get('message') if results else '这句话暂未形成可核验的保存操作；请在“DJ 记忆”选择对象和范围，或改用明确短句。'
        return _finish_chat_response(session_id, message, reply, [], {'channel':'scene_memory'})

    if direct_music_control(message):
        return _music_control_recommend(session_id, message, request_context)
    scene = task_scene_declaration(message)
    if scene and conversation.get_recommendation_state(session_id):
        return _update_task_scene(session_id, message, scene)

    # ②.1 点名歌手且明确要歌时，直接走正式歌单工具。歌手范围和“其他歌”
    # 都是可确定执行的约束，不应让 Agent 有机会只写一段“给你换一批”的口播。
    artist_guard = _artist_request_guard(request_context)
    if artist_guard is None:
        state_artist = _artist_from_recommendation_state(session_id, message)
        if state_artist:
            # 命中与否交给 recommend_playlist 的精确歌手查询；这里的非空哨兵只表示
            # “状态明确限定了歌手”，不能因最近聊天被摘要而退回无约束向量检索。
            artist_guard = (state_artist, [{"source": "conversation_state"}])
    if artist_guard is not None and _looks_like_song_request(message):
        return _artist_direct_recommend(
            session_id, message, request_context, artist_guard[0]
        )

    # ②.2 用户点头直通：DJ 上轮抛了『要不要按方向再挑几首』、用户回纯应允词（好/可以/要）
    # → 等同于点歌，按用户原话方向直接出真实歌单（不再赌 LLM 会不会调工具）。见 _resolve_consent。
    consent_query = _resolve_consent([*ctx["messages"], {"role": "user", "content": message}])
    if consent_query is not None:
        return _consent_recommend(session_id, message, consent_query)

    # 每次请求随机选一个表达视角注入（活人感：话术每次不一样）
    system = build_system_prompt(viewpoint=random.choice(list(_VIEWPOINTS)))
    from app.preference_memory import expression
    short_chat = not _looks_like_song_request(message) and expression(session_id, message).get('length') == 'short'
    if short_chat:
        system += '\n本轮聊天按简短档回答，用一到两句直接回应。'
    if ctx.get("summary"):
        system += "\n\n（更早对话摘要）\n" + ctx["summary"]
    system += ('\n长期偏好的保存和撤销由后端单独执行；你没有写入记忆的工具，禁止声称已经保存、更新或忘记。'
               '旧聊天与摘要中的偏好不代表当前有效记录，不得据此恢复已经撤销的场景排除。'
               '支持的明确偏好可以在DJ记忆管理，也可用“以后少推荐说唱”“工作时我更喜欢纯音乐”等明确短句；复杂说法不能假称保存。')
    messages = (
        [{"role": "system", "content": system}]
        + ctx["messages"]
        + [{"role": "user", "content": message}]
    )

    # ③ 跑 Agent 主循环；意外异常整体降级，不把 500 抛给用户
    try:
        events = list(run_agent(messages, session_id=session_id))
    except Exception as e:
        note("agent_fallback")
        log_failure("agent_fallback", e)
        return _similarity_fallback(message, session_id, request_context)

    answer, songs, stats = _parse_events(events)

    # ③.5 歌单契约守卫：Agent 没走 recommend_playlist 却编了歌单文案 → 用真实歌单重写
    guarded = _apply_playlist_guard(
        message, events, session_id=session_id, messages=messages
    )
    if guarded is not None:
        answer, songs, stats = guarded["answer"], guarded["songs"], guarded["stats"]

    if not _looks_like_song_request(message):
        stats = dict(stats or {})
        if songs:
            # 编排出口同样守住聊天目的；替换/未来Agent适配器不能借工具结果绕过当前意愿。
            answer, songs = _CHAT_ONLY_REPLY, []
            stats.pop("_recommendation_intent", None)
            stats["steps"] = [*stats.get("steps", []), "guard:chat_no_recommendation"]
        answer = clean_chat_reply(trim_music_invitation(answer), message) or _CHAT_ONLY_REPLY
        if short_chat:
            answer = brief_sentences(answer) or (answer[:95].rstrip() + '…')

    # ④ 统一落库并返回；任何新增返回路径都应经过这个出口。
    return _finish_chat_response(session_id, message, answer, songs, stats)


def continue_queue(song_id: str, session_id: str | None = None, count: int = 3):
    """队列耗尽后生成一段声学衔接；不向聊天历史塞入伪对话。"""
    target = max(1, min(count, 5))
    # 自动续播仍属于当前任务；沿用临时排除，但不由此改写原音乐条件。
    task = conversation.get_recommendation_state(session_id) if session_id else None
    task_id = (task or {}).get('_feedback_task_id', '')
    from app.scene_memory import matching
    memory_blocked = {r['song_id'] for r in matching(session_id, task)}
    result = execute_tool(
        "recommend_playlist",
        {
            **(_intent_to_args(task, {}) if (task or {}).get('style_request') else {}),
            "query": "延续当前歌曲的声音与节奏",
            "_feedback_task_id": task_id,
            "_exclude_song_ids": list(memory_blocked),
            "reference_song_id": song_id,
            "target_count": target,
            "count": 30,
            "exclude_recent": True,
            **({"_style_follow_up": True} if (task or {}).get("style_request") else {}),
        },
        session_id=session_id,
    )
    songs = result.get("songs") or []
    # 排播仍在原任务场景中；候选生成期间新增的排除也应在提交前生效。
    from app import preference_memory
    preferences = preference_memory.relevant(session_id, (task or {}).get('query') or '', reference_id=song_id)
    songs = [song for song in songs if not any(preference_memory.blocked(row, song) for row in preferences)]
    from app import library
    reference = library.find_song_by_id(song_id)
    if reference:
        songs = [{**song, 'reference_song_id': str(reference['id']), 'reference_title': reference['title']} for song in songs]
    memory_blocked.update(r['song_id'] for r in matching(session_id, task))
    songs = [song for song in songs if str(song['id']) not in memory_blocked]
    if session_id and task_id:
        from app.task_feedback import active_feedback
        blocked = set(active_feedback(session_id, task_id)['song_ids'])
        songs = [song for song in songs if str(song['id']) not in blocked]
    batch_id = None
    if session_id and songs:
        batch_id = listening.record_recommendation_batch(
            session_id,
            "自动排播：延续当前歌曲",
            [song["id"] for song in songs],
            channel="auto_queue",
            db_path=conversation.DB_PATH,
        )
    program = None
    if batch_id:
        program = dj_program.create_program(batch_id, session_id, songs,
            "延续当前歌曲", {**(task or {}), "_feedback_task_id": task_id, **({'reference_song_id': song_id} if reference else {})}, conversation.DB_PATH)
        dj_program.schedule_program(batch_id, session_id, conversation.DB_PATH)
    return {
        "songs": songs,
        "batch_id": batch_id,
        "dj_program": program,
        "reference_song": result.get("reference_song"),
        "acoustic_reference_available": result.get("acoustic_reference_available", False),
        "message": result.get("message") or "",
        "reference_style_priority": result.get("reference_style_priority"),
    }
