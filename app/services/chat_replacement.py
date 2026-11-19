"""将明确的单曲换歌请求绑定到当前推荐批次。"""
import re

from app import conversation, dj_program, reference_choice, reference_direction
from app.recommendation_adjust import adjust_program

_NUMBERS = {value: index for index, value in enumerate('一二三四五六七八九十', 1)}
_TARGET = r'(?:第\s*(?P<number>[0-9]{1,2}|[一二三四五六七八九十])\s*首|(?P<current>当前这首|现在这首|这首)|《(?P<title>[^《》]+)》)'
_AFTER_TARGET = re.compile(_TARGET + r'(?P<comment>[^，,。；;！？!?《》]*?)[，,。；;\s]*(?:帮我|给我)?(?:换掉|换成|换一首|换首|换个|换掉它|换一下|换一换|换)(?P<direction>.*)')
_BEFORE_TARGET = re.compile(r'(?:帮我|给我)?(?:把)?(?:换掉|换一下|换)\s*' + _TARGET + r'(?P<direction>.*)')
_UNSUPPORTED = re.compile(r'(?:(?:第[^，,。]{1,12}首|这几首|上一首|下一首|《[^》]+》)[^。！？!?]{0,40}换|换(?:掉|成)?\s*(?:这几首|上一首|下一首))')


def handle(session_id, message, current_song_id, batch_id, queue_song_ids=None):
    text = message.strip().rstrip('。！!')
    if text.startswith(('如果', '假如', '不要', '别', '先不', '怎么', '为什么', '能不能别')):
        return None
    text = re.sub(r'^(?:请|帮我|给我|能不能)?\s*(?:把)?\s*', '', text)
    match = _AFTER_TARGET.fullmatch(text) or _BEFORE_TARGET.fullmatch(text)
    if not match:
        if not _UNSUPPORTED.match(text):
            return None
        return _failure('请说清要换当前推荐里的哪一首，比如「换掉第二首」。')
    if re.search(r'[？?]|(?:别|不要|不用)(?:再)?换', text):
        return _failure('请确认要换哪一首，再发一次单曲替换指令。')
    comment = match.groupdict().get('comment') or ''
    if re.search(r'不要|别|不用|不想|是否|(?:[和跟与及、/]|也|都)\s*第|[都也一起]\s*$', comment):
        return _failure('请确认要换哪一首，再发一次单曲替换指令。')
    if not batch_id:
        return _failure('请先打开一轮 DJ 推荐，再告诉我要换哪首。')
    program = dj_program.read_program(batch_id, session_id, conversation.DB_PATH, internal=True)
    songs = (program or {}).get('songs') or []
    if not songs:
        return _failure('这轮推荐已经找不到了，请重新点歌。')
    if queue_song_ids is not None:
        by_id = {str(song['id']): song for song in songs}
        if len(queue_song_ids) != len(songs) or set(queue_song_ids) != by_id.keys():
            return _failure('队列和这轮推荐对不上，先保留原队列。请核对当前队列后再试。')
        # 随机播放会改变可见编号。只接受同一推荐批次的完整排列，不接受客户端提供的歌曲资料。
        songs = [by_id[song_id] for song_id in queue_song_ids]
        program = {**program, 'songs': songs}
    if match['number']:
        if queue_song_ids is None:
            return _failure('没收到当前队列的顺序，先保留原队列。请刷新页面后再按编号换歌。')
        index = _NUMBERS.get(match['number']) or int(match['number'])
        targets = songs[index - 1:index] if index else []
    elif match['title']:
        targets = [song for song in songs if song['title'].casefold() == match['title'].strip().casefold()]
    else:
        targets = [song for song in songs if str(song['id']) == str(current_song_id)]
    if len(targets) != 1:
        return _failure('这轮推荐里没法确定你说的是哪一首。可以用这一轮的序号来换。')
    direction_text = match['direction'].strip('，,。；;：: ')
    if direction_text.startswith('它'):
        direction_text = direction_text[1:].strip('，,。；;：: ')
    if re.search(r'^(?:第.+首|《)|(?:都|一起)换|(?:和|跟|与|以及)\s*第.+首', direction_text):
        return _failure('请一次指定一首要换的歌。')
    named = reference_direction.NAMED_REFERENCE.search(direction_text)
    reference_id = None
    try:
        if named and not re.search(r'(?:不要|别|不用|不是)\s*$', direction_text[:named.start()]):
            reference_id = str(reference_direction.resolve_named(named['title'], named['artist'], current_song_id)['id'])
            direction_text = direction_text[:named.start()] + direction_text[named.end():]
        elif re.search(r'(?:类似|像|接近)(?:这首|当前这首)', direction_text):
            reference_id = str(current_song_id) if current_song_id else None
            if not reference_id:
                return _failure('请先选中你想用作参考的歌曲。')
        direction = reference_direction.parse(direction_text)
        # 对被换歌曲的描述不会重新当作正向条件；明确说偏某种曲风才避开它。
        if re.search(r'(?:偏|太)[^，,。]{0,8}说唱|说唱味太重', comment) and not {'说唱', 'emo'}.intersection(direction['genres']):
            direction['exclude'] = list(dict.fromkeys([*direction['exclude'], '说唱']))
        selection = reference_direction.selection(direction_text, reference_id=reference_id)
        if 'moods' in selection:
            selection['moods'] = direction['moods']
        if direction_text and not (reference_direction.describe(direction) or selection
                                   or re.fullmatch(r'(?:别的|新的|类似的|差不多的|其他的)(?:歌)?', direction_text)):
            return _failure('你想换成什么样的歌？可以说一种情绪或风格，比如「更温柔的华语流行」。')
        songs, intent, _ = adjust_program(session_id, batch_id, 'replace', targets[0]['id'], conversation.DB_PATH, program,
                                          selection=selection, direction=direction if reference_direction.describe(direction) else None)
    except (ValueError, LookupError) as error:
        return _failure(str(error))
    index = program['songs'].index(targets[0])
    effective_reference = selection.get('reference_song_id') or intent.get('reference_song_id')
    pair = reference_choice.capture(effective_reference, targets[0]['id'], songs[index]['id'])
    position = f'第 {index + 1} 首' if queue_song_ids is not None else ''
    answer = f"把{position}《{targets[0]['title']}》换成了《{songs[index]['title']}》。"
    return answer, songs, {'channel': 'chat_replacement', '_recommendation_intent': intent,
                           '_replacement_pair': pair,
                           'replacement': {'batch_id': batch_id, 'song_id': targets[0]['id']}}


def _failure(answer):
    return answer, [], {'channel': 'chat_replacement', 'queue_preserved': True}
