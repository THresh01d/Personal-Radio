"""推荐轮次的独立 DJ 节目：背景慢查询不阻塞选歌，事实不交给模型扩写。"""

import json
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from contextvars import ContextVar

from app import song_background
from app import artist_background, program_author, interview_background

_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="dj-background")
_slots = threading.BoundedSemaphore(10)
_active = set()
_lock = threading.Lock()
_bind_queue = ContextVar('bind_dj_queue', default=(False, False))


@contextmanager
def queue_binding_scope(enabled, *, allow_subset=False):
    token = _bind_queue.set((enabled, allow_subset))
    try:
        yield
    finally:
        _bind_queue.reset(token)


def _failed(batch_id, session_id, db_path, reason):
    with sqlite3.connect(db_path, timeout=5) as conn:
        row = conn.execute('SELECT payload FROM dj_programs WHERE batch_id=? AND session_id=?', (batch_id,session_id)).fetchone()
        if row:
            payload = json.loads(row[0])
            payload.update(copy_status='failed', copy_reasons=[reason])
            conn.execute("UPDATE dj_programs SET payload=?,status='fallback' WHERE batch_id=? AND session_id=?",
                         (json.dumps(payload,ensure_ascii=False),batch_id,session_id))


def _init(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS dj_programs (
        batch_id TEXT PRIMARY KEY, session_id TEXT NOT NULL, query TEXT NOT NULL,
        songs TEXT NOT NULL, intent TEXT NOT NULL, payload TEXT NOT NULL,
        status TEXT NOT NULL, updated_at REAL NOT NULL)""")


def compose_program(songs, query, backgrounds=None):
    """只拼入已校验字段原句；情绪表达是选歌角度，不冒充听音或创作动机。

    故事丰富度取决于证据，不凑时长。首版来源覆盖的是署名/发行资料而非采访故事，
    不把几个元数据字段包装成已查证的创作经历。
    """
    backgrounds = backgrounds or {}
    focus = next((s for s in songs if backgrounds.get(s["id"])), songs[0] if songs else None)
    facts = backgrounds.get(focus["id"], [])[:2] if focus else []
    if not focus:
        return {"text": "", "sources": [], "mode": "metadata", "max_seconds": 60}
    if facts:
        # 有资料的可能是第二首；介绍对象不能冒充队列接下来实际播放的第一首。
        text = f"本轮作品：{focus['artist']}《{focus['title']}》。"
        # 明确是来源资料的记载，不把引用存在说成事实绝对正确。
        text += "背景资料里，有这样一段记录："
        used = []
        for fact in facts:
            if len(text + fact["text"]) <= 235:
                text += fact["text"]
                used.append(fact)
        facts = used
        if not facts:
            # 极长标题挤不下完整事实时，回到元数据；不能挂来源却没念出依据。
            return compose_program(songs, query)
    else:
        text = f"{focus['artist']}，《{focus['title']}》。"
        if focus.get("album"):
            text += f"你曲库里的这个版本收录在《{focus['album']}》。"
    # 失败兜底只报真实作品，不强行填满开场、标签解释和结束语。
    if len(text) > 300:
        # 元数据本身也可能超长；不用截断歌名冒充完整名称，改请用户查看队列。
        text = "这一轮推荐已经准备好了，歌名、歌手和专辑都在队列里。先听音乐，感觉不合适时，我们再调整。"
        facts = []  # 最终文字不再包含来源事实，不能仍挂上“有来源资料”的状态。
    # 约一分钟是播放硬边界；文字上限优先保证每条事实完整，不按字符截断事实。
    return {"text": text, "sources": facts, "mode": "sourced" if facts else "metadata", "max_seconds": 60}


def recent_programs(session_id, db_path, exclude_batch=None):
    """仅本聊天最近三份已生成正文；不跨聊天、不建表、不当成实际已收听记录。"""
    from pathlib import Path
    path = Path(db_path)
    if not session_id or not path.is_file():
        return []
    try:
        with sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=.2) as conn:
            # 后台查证会更新 updated_at，但并不代表新写了一轮节目。
            rows = conn.execute('SELECT payload FROM dj_programs WHERE session_id=? AND batch_id!=? ORDER BY rowid DESC LIMIT 3',
                                (session_id, exclude_batch or '')).fetchall()
        result = []
        for row in reversed(rows):
            payload = json.loads(row[0])
            text = ('\n\n'.join(s['text'] for s in payload.get('segments', [])
                                if isinstance(s,dict) and isinstance(s.get('text'),str))
                    if payload.get('program_version') == program_author.VERSION else payload.get('text'))
            if isinstance(text, str) and 0 < len(text.strip()) <= 900:
                result.append(text.strip())
        return result
    except (sqlite3.Error, ValueError, TypeError, AttributeError):
        return []


def create_program(batch_id, session_id, songs, query, intent, db_path, *, message_id=None, notices=(),
                   confirmation_only=False):
    from app.voice_author import take_program
    authored = take_program(songs, query)
    if (intent or {}).get('reference_song_id'):
        from app import library
        reference = library.find_song_by_id(str(intent['reference_song_id']))
        if reference:
            intent = {**intent, '_memory_reference': {key: reference.get(key) for key in ('id', 'title', 'artist', 'album')}}
    from app.preference_memory import expression
    settings = expression(session_id, query)
    length = settings.get('length', 'normal')
    bind_queue, allow_subset = _bind_queue.get()
    bind_queue = bind_queue and not confirmation_only
    if confirmation_only:
        # 仍保存批次以支持下一次换歌和反馈，但单首修订不再生成整轮节目。
        payload = {'confirmation_only': True, 'text': '', 'sources': [], 'mode': 'metadata', 'max_seconds': 60,
                   'program_version': program_author.VERSION, 'segments': [], 'opening_priority': False}
    elif authored and not bind_queue and not (intent or {}).get('reference_song_id') and message_id is None and length == 'normal':
        payload = authored
    else:
        payload = {**compose_program(songs, query), 'program_version': program_author.VERSION,
                   'segments': [], 'copy_status': 'pending', 'copy_reasons': [], 'opening_priority': True,
                   'opening_status': 'pending', 'copy_length': length}
    if bind_queue:
        # 浏览器先把这批歌排成实际队列。写作只读取已确认的顺序。
        payload.update(queue_order_required=True, text='')
        if allow_subset:
            # 智能续播追加到已有队列前，会去掉其中已经存在的歌曲。
            payload['queue_subset_allowed'] = True
    if message_id is not None:
        payload.update(message_id=message_id, notices=list(notices))
    disabled = confirmation_only or settings.get('mode') == 'quiet'
    if disabled:
        payload.update(segments=[], copy_status='disabled', opening_status='disabled')
    with sqlite3.connect(db_path, timeout=5) as conn:
        _init(conn)
        conn.execute("INSERT OR IGNORE INTO dj_programs VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                     (batch_id, session_id, query, json.dumps(songs, ensure_ascii=False),
                      json.dumps(intent or {}, ensure_ascii=False), json.dumps(payload, ensure_ascii=False),
                      'ready' if disabled else 'pending', time.time()))
    from app.preference_memory import present_program
    return present_program({**payload, "batch_id": batch_id, "status": 'ready' if disabled else 'pending'}, session_id, query)


def read_program(batch_id, session_id, db_path, *, internal=False):
    with sqlite3.connect(db_path, timeout=5) as conn:
        _init(conn)
        row = conn.execute("SELECT query, songs, intent, payload, status FROM dj_programs WHERE batch_id=? AND session_id=?",
                           (batch_id, session_id)).fetchone()
    if not row:
        return None
    payload = {**json.loads(row[3]), "batch_id": batch_id, "status": row[4]}
    if internal:
        payload.update(query=row[0], songs=json.loads(row[1]), intent=json.loads(row[2]))
    if not internal:
        from app.preference_memory import present_program
        payload = present_program(payload, session_id, row[0])
    return payload


def bind_playback_order(batch_id, session_id, song_ids, db_path):
    """只接受已校验批次里的歌曲，并且只锁定一次，防止重试触发二次写稿。"""
    with sqlite3.connect(db_path, timeout=5) as conn:
        _init(conn)
        conn.execute('BEGIN IMMEDIATE')
        row = conn.execute('SELECT songs,payload FROM dj_programs WHERE batch_id=? AND session_id=?',
                           (batch_id, session_id)).fetchone()
        if not row:
            raise LookupError('这轮 DJ 节目已不存在')
        original, payload = map(json.loads, row)
        original_ids = [str(song['id']) for song in original]
        actual, original_set = set(song_ids), set(original_ids)
        if (not song_ids or len(actual) != len(song_ids) or not actual.issubset(original_set)
                or (not payload.get('queue_subset_allowed') and actual != original_set)):
            raise ValueError('播放队列与这轮推荐不一致')
        if not payload.get('queue_order_required'):
            raise ValueError('这轮节目已使用原推荐顺序写稿')
        bound = payload.get('playback_song_ids')
        if bound is not None:
            if bound != song_ids:
                raise ValueError('这轮节目已按确认的顺序安排')
            return
        payload['playback_song_ids'] = list(song_ids)
        conn.execute('UPDATE dj_programs SET payload=?,updated_at=? WHERE batch_id=? AND session_id=?',
                     (json.dumps(payload, ensure_ascii=False), time.time(), batch_id, session_id))


def project_chat_messages(session_id, messages, db_path):
    """刷新后用同一份开场还原对话，原回复可在节目未就绪时立即显示。"""
    ids = [message['id'] for message in messages if message['role'] == 'assistant']
    if not ids:
        return messages
    with sqlite3.connect(db_path, timeout=5) as conn:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='dj_programs'").fetchone():
            return messages
        placeholders = ','.join('?' for _ in ids)
        rows = conn.execute(
            f"SELECT payload FROM dj_programs WHERE session_id=? AND json_extract(payload,'$.message_id') IN ({placeholders})",
            (session_id, *ids),
        ).fetchall()
    programs = {program['message_id']: program for (payload,) in rows if (program := json.loads(payload))}
    for message in messages:
        if program := programs.get(message['id']):
            if program.get('opening_status') == 'ready' and program.get('segments'):
                message['content'] = program['text']
            message['notices'] = program.get('notices', [])
    return messages


def enrich_program(batch_id, session_id, db_path, background_path=None):
    program = read_program(batch_id, session_id, db_path, internal=True)
    if not program:
        return
    segmented = program.get('program_version') == program_author.VERSION
    if segmented:
        # 在网络和模型之前持久化唯一尝试。重启/重复GET不会为同一批重新付费写作。
        with sqlite3.connect(db_path, timeout=5) as conn:
            conn.execute('BEGIN IMMEDIATE')
            row = conn.execute('SELECT payload,status FROM dj_programs WHERE batch_id=? AND session_id=?', (batch_id,session_id)).fetchone()
            if not row or row[1] != 'pending':
                return
            current = json.loads(row[0])
            if current.get('queue_order_required'):
                order = current.get('playback_song_ids')
                if not order:
                    return
                catalog = {str(song['id']): song for song in program['songs']}
                program['songs'] = [catalog[sid] for sid in order]
                program.update(queue_order_required=True, playback_song_ids=order)
            if current.get('copy_status') == 'writing':
                current.update(copy_status='failed', copy_reasons=['writer_interrupted'])
                conn.execute("UPDATE dj_programs SET payload=?,status='fallback' WHERE batch_id=? AND session_id=?",
                             (json.dumps(current,ensure_ascii=False),batch_id,session_id))
                return
            current['copy_status'] = 'writing'
            conn.execute('UPDATE dj_programs SET payload=? WHERE batch_id=? AND session_id=?',
                         (json.dumps(current,ensure_ascii=False),batch_id,session_id))
    backgrounds = {}
    reference_id = program['intent'].get('reference_song_id')
    reference = None
    if segmented and reference_id:
        snapshot = program['intent'].get('_memory_reference') or {}
        reference = snapshot if str(snapshot.get('id')) == str(reference_id) else {'id': None}
    # 一轮最多查两首，找到有来源的作品后停止；不对整个私人曲库发联网任务。
    deadline = time.monotonic() + 20
    from app.style_request import opening_notice
    evidence_only = segmented and (reference_id or opening_notice(program["songs"]))
    for song in ([] if evidence_only else program["songs"][:2]):
        biography = artist_background.get_artist_background(song, db_path=background_path) if segmented else []
        facts = song_background.get_background(song, db_path=background_path)
        if biography or facts:
            backgrounds[song["id"]] = (biography + facts)[:3]
            if not segmented:
                break
        if time.monotonic() > deadline:
            break
    if segmented:
        if backgrounds and time.monotonic() < deadline:
            for song in program['songs'][:2]:
                available=backgrounds.get(song['id'],[])
                # 只沿一个已有作品引用尝试采访，不额外搜索/爬取整个艺人资料。
                if any((f.get('url') or '').split('/')[2].removeprefix('www.') in interview_background.MEDIA_HOSTS
                       for f in available if (f.get('url') or '').startswith('https://')):
                    excerpt=interview_background.get_interview_excerpt(song,available)
                    backgrounds[song['id']]=(excerpt+available)[:3]
                    break
        # 确认批次仍存在后才进入唯一模型调用；清空期间的资料结果不能恢复旧节目。
        if not read_program(batch_id, session_id, db_path, internal=True):
            return
        from app.preference_memory import expression
        settings = expression(session_id, program['query'])
        length = program.get('copy_length') or settings.get('length', 'normal')
        if settings.get('mode') == 'quiet':
            authored = {'segments': [], 'copy_status': 'disabled', 'copy_reasons': []}
        else:
            authored = program_author.author_program(program['songs'], program['query'], backgrounds,
                                                     recent_programs(session_id, db_path, exclude_batch=batch_id),
                                                     context={'direction': program['intent'].get('query') or program['query']},
                                                     length=length, **({'reference': reference} if reference else {}))
        segments = authored['segments']
        facts = list({f['id']:f for segment in segments for f in segment['sources']}.values())
        fallback = compose_program(program['songs'], program['query'], backgrounds)
        payload = {**fallback, **authored, 'program_version': program_author.VERSION,
                   'copy_source': authored.get('copy_source', 'ai' if segments else 'metadata'), 'opening_priority': True,
                   'opening_status': authored.get('opening_status') or ('ready' if any(s['position']==0 for s in segments) else 'missing' if segments else authored['copy_status'])}
        payload.update(copy_length=length)
        if program.get('queue_order_required'):
            payload.update(queue_order_required=True, playback_song_ids=program['playback_song_ids'])
            if program.get('queue_subset_allowed'):
                payload['queue_subset_allowed'] = True
        if 'message_id' in program:
            payload.update(message_id=program['message_id'], notices=program.get('notices', []))
        if segments:
            # text供尚未刷新的旧页面兼容：只给开场段，不能将三段串成>300字TTS，
            # 也不能把原定曲间的故事提前放到第一首前。新页面按segments播放。
            payload.update(text=next((s['text'] for s in segments if s['position']==0), ''), sources=facts,
                           mode='sourced' if facts else 'metadata')
        with sqlite3.connect(db_path, timeout=5) as conn:
            conn.execute("UPDATE dj_programs SET payload=?,status='ready',updated_at=? WHERE batch_id=? AND session_id=?",
                         (json.dumps(payload,ensure_ascii=False),time.time(),batch_id,session_id))
        return
    # 背景查询只预热下一次素材；不能把本轮已有的自由正文重新覆盖成固定句式，
    # 也不把未说出的资料挂为本轮引用。晚到资料不增加模型调用或插播。
    payload = ({key: value for key, value in program.items()
                if key not in ('batch_id', 'status', 'query', 'songs', 'intent')}
               if program.get('copy_source') == 'ai'
               else compose_program(program["songs"], program["query"], backgrounds))
    with sqlite3.connect(db_path, timeout=5) as conn:
        # 清空会话后的迟到工作只能 UPDATE，不重新创建幽灵节目。
        conn.execute("UPDATE dj_programs SET payload=?, status='ready', updated_at=? WHERE batch_id=? AND session_id=?",
                     (json.dumps(payload, ensure_ascii=False), time.time(), batch_id, session_id))


def schedule_program(batch_id, session_id, db_path):
    from app.preference_memory import expression
    current = read_program(batch_id, session_id, db_path, internal=True)
    if not current or current['status'] != 'pending' or (current.get('queue_order_required') and not current.get('playback_song_ids')):
        return
    settings = expression(session_id, current['query']) if current else {}
    if settings.get('mode') == 'quiet':
        return
    key = (str(db_path), batch_id)
    with _lock:
        if key in _active:
            return
        if not _slots.acquire(blocking=False):
            _failed(batch_id, session_id, db_path, 'program_busy')
            return
        _active.add(key)

    def work():
        try:
            enrich_program(batch_id, session_id, db_path)
        except Exception:
            try:
                _failed(batch_id, session_id, db_path, 'background_unavailable')
            except sqlite3.Error:
                pass
        finally:
            with _lock:
                _active.discard(key)
            _slots.release()
    try:
        _pool.submit(work)
    except RuntimeError:
        with _lock:
            _active.discard(key)
        _slots.release()
        # 执行器停止时任务尚未进入 work，那里负责的降级也必须在此补上。
        _failed(batch_id, session_id, db_path, 'program_unavailable')


def clear_programs(session_id, db_path):
    with sqlite3.connect(db_path, timeout=5) as conn:
        _init(conn)
        conn.execute("DELETE FROM dj_programs WHERE session_id=?", (session_id,))
