"""app/feedback.py - 显式反馈与收听行为的统一画像（软调整、不封杀）

为什么需要：推荐是循环的——用户 👎 过一首，几轮后相似度还会把它顶上来，
因为模型只知道『语义像』不知道『你不喜欢』。本模块把每张推荐卡片上的
👍/👎 与保守的收听行为信号聚合成会话级画像，推荐时按画像调整候选排序
（详见 tools._apply_taste）。显式反馈权重大于播放器推断。

为什么软降权而不是硬排除：
- 点错、心境变化、歌被塞进不合适的歌单——『永不出现』的封杀在真实使用里
  误伤率高，而且体验很糟（用户没法反悔）；
- 软降权把被讨厌的歌排序后移，候选池不足时它仍是合法兜底，不会制造空结果；
- 加权/降权系数是设计决定，写进测试钉死（tests/test_feedback.py），改动
  必须是有意为之，不是调参时顺手改的。

匿名身份：无账号体系，前端 localStorage 持久化的 session_id 即身份。

为什么与对话记忆同库（conversation.db）而非 library.db：library.db 会被
build_library.py 清空重建，品味不能跟着丢；feedback 表建在 conversation.db
上，路径动态取 conversation.DB_PATH（不 import 时快照），这样测试 monkeypatch
conversation.DB_PATH 指向临时库时，feedback 自动跟随，不会污染真实库。
"""

import sqlite3
import time

from app import conversation  # 只用来取库文件位置（动态），避免与测试隔离脱节
from app.listening import BEHAVIOR_BOOST_FACTOR, BEHAVIOR_DEMOTE_FACTOR, get_behavior_signals


# ---------- 软降权/加权系数（设计契约，测试钉死） ----------

# 单曲负反馈系数只用于有界偏好通道，不直接乘整首候选总分，也不硬排除。
DEMOTE_SONG_FACTOR = 0.15
# 点赞是对单曲的明确表态；保持原加权强度，只收窄作用范围，避免连带同歌手作品。
BOOST_SONG_FACTOR = 1.2


def _now():
    """统一时间戳格式（本地时间）。"""
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _init_db(db_path):
    """建表（幂等）。主键 (session_id, song_id)：同一首歌多次表态只留最新一行。

    artist 保留为原反馈的署名元数据，以兼容已有记录和调用方；
    单曲表态不代表用户对整位歌手的意见，读取时不再由它推导歌手倾向。
    """
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS feedback (
                session_id TEXT NOT NULL,
                song_id TEXT NOT NULL,
                action TEXT NOT NULL,
                artist TEXT,
                created_at TEXT,
                PRIMARY KEY (session_id, song_id)
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


def record_feedback(session_id, song_id, action, artist=None, db_path=None):
    """记录一次对推荐歌曲的反馈；同一首歌重复表态 = 改判（upsert，不叠加）。

    action ∈ {like, dislike}，由上层（pydantic Literal / 前端按钮）限定；
    存储层只落库不解释业务，保证纯净可测。artist 可空（异常数据也容忍）。
    """
    db_path = db_path or conversation.DB_PATH
    _init_db(db_path)
    from app.scene_memory import owner_for
    session_id = owner_for(session_id, db_path)
    conn = sqlite3.connect(db_path)
    try:
        from app.preference_memory import supersede_song_feedback
        supersede_song_feedback(conn, session_id, song_id)
        conn.execute(
            "INSERT INTO feedback (session_id, song_id, action, artist, created_at)"
            " VALUES (?,?,?,?,?)"
            " ON CONFLICT(session_id, song_id) DO UPDATE SET"
            " action=excluded.action, artist=excluded.artist,"
            " created_at=excluded.created_at",
            (session_id, song_id, action, artist, _now()),
        )
        conn.commit()
    finally:
        conn.close()


def delete_feedback(session_id, song_id, db_path=None):
    """撤销一首歌的主动表态，并返回是否真的删除了记录。

    这是用户主动纠正长期画像的入口。这里删除的是用户明确要求撤销的事实，
    不会改动播放器行为、聊天记录或曲库。
    """
    db_path = db_path or conversation.DB_PATH
    _init_db(db_path)
    from app.scene_memory import owner_for
    session_id = owner_for(session_id, db_path)
    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.execute(
            "DELETE FROM feedback WHERE session_id=? AND song_id=?",
            (session_id, str(song_id)),
        )
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def get_taste(session_id, db_path=None):
    """读取当前身份的单曲反馈和行为信号；不从赞踩次数推断整位歌手。

    新旧记录都按所指单曲解释，不重写历史行。歌手集合保留为空，兼容旧内部
    调用方的字典结构；明确点名歌手的当次要求仍由任务过滤处理。
    """
    db_path = db_path or conversation.DB_PATH
    _init_db(db_path)
    from app.scene_memory import owner_for
    owner = owner_for(session_id, db_path)
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT song_id, action FROM feedback WHERE session_id=?",
            (owner,),
        ).fetchall()
    finally:
        conn.close()

    demote_songs = {str(song_id) for song_id, action in rows if action == "dislike"}
    boost_songs = {str(song_id) for song_id, action in rows if action == "like"}
    behavior = get_behavior_signals(session_id, db_path=db_path)
    return {
        "demote_song_ids": demote_songs,
        "boost_song_ids": boost_songs,
        "demote_artists": set(),
        "boost_artists": set(),
        "behavior_demote_song_ids": behavior["behavior_demote_song_ids"],
        "behavior_boost_song_ids": behavior["behavior_boost_song_ids"],
    }


def song_taste_factor(song_id, taste):
    """推荐和画像共享同一单曲规则，避免展示与实际排序分叉。

    保持单曲负反馈优先和原行为信号的合并方式；只把点赞范围从歌手收窄到单曲。
    兼容旧字典缺少新增集合，但不读取旧的歌手推断字段。
    """
    song_id = str(song_id)
    if song_id in taste.get("demote_song_ids", set()):
        return DEMOTE_SONG_FACTOR
    factor = BOOST_SONG_FACTOR if song_id in taste.get("boost_song_ids", set()) else 1.0
    if song_id in taste.get("behavior_demote_song_ids", set()):
        factor *= BEHAVIOR_DEMOTE_FACTOR
    elif song_id in taste.get("behavior_boost_song_ids", set()):
        factor *= BEHAVIOR_BOOST_FACTOR
    return factor
