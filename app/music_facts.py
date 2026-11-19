"""音乐平台返回的确定性事实处理。

LLM 适合补齐风格/情绪等“软信息”，但不能覆盖平台明确返回的硬事实。
当前唯一硬事实：网易云 /lyric/new 的 pureMusic=true，或其规范的
“纯音乐，请欣赏”歌词标记。这里不保存歌词，仅在响应内存中读取标记后丢弃。
"""

from copy import deepcopy

# 网易云接口中用于明确表示没有人声歌词的固定标记。
_PURE_MUSIC_MARKER = "纯音乐，请欣赏"


def is_pure_music_response(response: dict) -> bool:
    """判断网易云歌词响应是否明确证明该曲为纯音乐。

    优先相信 pureMusic 布尔字段；为兼容部分旧曲/接口缺字段的情形，
    只额外识别网易云自己的固定标记，不进行模糊歌词推测。
    """
    if response.get("pureMusic") is True:
        return True
    lyric = ((response.get("lrc") or {}).get("lyric") or "")
    return _PURE_MUSIC_MARKER in lyric


def apply_netease_pure_music(profile: dict, is_pure_music: bool) -> dict:
    """将网易云明确的纯音乐事实合并进歌曲档案。

    纯音乐事实优先级高于 LLM。为避免把 LLM 猜错的“emo”等继续作为
    纯音乐推荐的主标签，保留原标签但补上“纯音乐”；检索层通过
    is_instrumental 做硬过滤，普通推荐不会再混入此类歌曲。
    """
    result = deepcopy(profile)
    if not is_pure_music:
        return result

    result["is_instrumental"] = 1
    genre = list(result.get("genre") or [])
    if "纯音乐" not in genre:
        genre.append("纯音乐")
    result["genre"] = genre
    return result
