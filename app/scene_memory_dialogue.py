"""首轮支持的记忆短句：核对当前原话、场景与真实作品，不让模型直接写库。"""

import json
import re
import sqlite3

from app import conversation, library, scene_memory
from app.task_feedback import record_task_feedback

_SCENE = r'睡前|工作专注|工作|学习|通勤|深夜|雨天|运动|开车|派对|独处|旅行'
_SAVE = re.compile(rf'(?:请)?(?:帮我)?(?:记住[，,：:]?)?(?:以后|每次)?(?P<scene>{_SCENE})(?:的时候|时)?[，,]?(?:我)?(?:以后)?(?:都)?(?:不要|别|不再|不)(?:再)?(?:推荐|播放|放|听)(?P<song>.+?)(?:了|吧)?[。！!]*')
_REVOKE = re.compile(rf'(?:请)?(?:取消|撤销|删除|忘掉)(?:刚才)?(?:那条|这条|该条|那个|这个)?(?P<scene>{_SCENE})?(?:的)?(?:偏好|记忆|排除|限制)(?:[：:，,]?(?P<song>.+?))?[。！!]*')
_TEMPORARY = re.compile(r'(?:今天|这次)(?:我)?(?:不想听|不要放|不要推荐|别放)(?P<song>.+?)[。！!]*')


def _song(text, current_song_id):
    value = text.strip().rstrip('。！!').strip()
    if value in ('这首', '这首歌', '当前这首', '当前这首歌'):
        return library.find_song_by_id(current_song_id) if current_song_id else None
    value = value.strip('《》“”「」')
    hits = [s for s in library.find_songs_by_title(value) if s['title'].casefold() == value.casefold()]
    return hits[0] if len(hits) == 1 else None


def handle(session_id, message, current_song_id=None):
    """返回确定性回复或None；只有当前用户明确提交的命令会触发写入。"""
    match = _SAVE.fullmatch(message)
    if match:
        scene = match['scene']
        scene = '工作专注' if scene in ('学习', '工作') else scene
        song = _song(match['song'], current_song_id)
        if not song:
            return '请先在播放器选中要设置的歌曲，或写出曲库中唯一的完整歌名；重名版本请先选中再说“这首”。'
        scene_memory.save(session_id, scene, song, message)
        return f"记住了：以后明确按{scene}选歌时避开《{song['title']}》（{song['artist']}）；其他场景不受影响。可以在“DJ 记忆”查看或撤销。当前队列保持原样。"
    match = _REVOKE.fullmatch(message)
    if match:
        rows = scene_memory.records(session_id)
        scene = match['scene']
        scene = '工作专注' if scene in ('学习', '工作') else scene
        if scene:
            rows = [r for r in rows if r['scene'] == scene]
        if match['song']:
            song = _song(match['song'], current_song_id)
            rows = [r for r in rows if song and r['song_id'] == str(song['id'])]
        if not rows:
            return '没有找到对应的有效场景偏好；可以在“DJ 记忆”核对。'
        if len(rows) != 1:
            return '这里有多条场景偏好，请到“DJ 记忆”选择要撤销的那一条，或补上场景和完整歌名。'
        if not scene_memory.revoke(session_id, rows[0]['id']):
            return '这条偏好已被其他操作修改，请在“DJ 记忆”重新核对；没有撤销较新的判断。'
        return f"已撤销《{rows[0]['song']['title']}》的{rows[0]['scene']}排除；之后不再使用这条判断。"
    match = _TEMPORARY.fullmatch(message)
    if match:
        song = _song(match['song'], current_song_id)
        if not song:
            return '请先选中这首歌，或提供唯一的完整歌名；这次意见不会保存为长期偏好。'
        with sqlite3.connect(conversation.DB_PATH) as conn:
            if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='dj_programs'").fetchone():
                return '请先发起一轮选歌，再标记这次不合适；未保存长期偏好。'
            batches = conn.execute('SELECT batch_id,songs FROM dj_programs WHERE session_id=? ORDER BY updated_at DESC', (session_id,)).fetchall()
        for batch_id, raw in batches:
            if str(song['id']) in {str(s['id']) for s in json.loads(raw)}:
                try:
                    record_task_feedback(session_id, batch_id, str(song['id']))
                except ValueError:
                    continue
                return f"好，这次选歌后续避开《{song['title']}》；换任务后失效，长期偏好和当前队列不变。"
        return '这首歌不在当前任务的推荐中，未保存意见；可从当前任务的歌曲菜单选择“这次不合适”。'
    if re.match(r'^(?:请)?(?:帮我)?(?:记住|保存.*偏好|(?:以后|每次).*(?:不要|别|不再).*(?:推荐|播放|放|听)|取消.*(?:偏好|记忆)|撤销.*(?:偏好|记忆))', message):
        return '这次没有识别出可直接执行的明确偏好，暂未保存。可以在“DJ 记忆”选择类型和对象，或说“工作时我更喜欢纯音乐”“以后睡前不要推荐这首歌”等明确短句。'
    return None
