"""按需歌词的纯解析规则；不获取或持久化歌词。"""
import re

_LRC_TIMESTAMP = re.compile(r"\[(?:(\d+):)?(\d{1,2}):(\d{1,2}(?:\.\d{1,3})?)\]")


def _parse_lrc(raw):
    """把 LRC 原文解析成前端可同步的时间轴；调用方用完原文即丢弃。

    同一行可能带多个时间戳，元数据行没有合法时间戳会自然跳过。文本长度与
    总行数都设上限，避免异常响应把大块内容带进播放器状态。
    """
    parsed = []
    for raw_line in str(raw or "").splitlines():
        matches = list(_LRC_TIMESTAMP.finditer(raw_line))
        if not matches:
            continue
        text = _LRC_TIMESTAMP.sub("", raw_line).strip()
        if not text:
            continue
        text = text[:240]
        for match in matches:
            hours = int(match.group(1) or 0)
            minutes = int(match.group(2))
            seconds = float(match.group(3))
            parsed.append({
                "time": round(hours * 3600 + minutes * 60 + seconds, 3),
                "text": text,
            })
    parsed.sort(key=lambda line: line["time"])
    # 精度不同的重复时间戳只保留最后一条，避免舞台连续闪过同一句。
    unique = {}
    for line in parsed[:1500]:
        unique[round(line["time"], 2)] = line
    return sorted(unique.values(), key=lambda line: line["time"])


def _merge_lyric_translation(lines, translated):
    """按 0.15 秒容差合并翻译，避免不同 LRC 精度导致翻译错位。"""
    translated_by_time = {round(line["time"], 2): line["text"] for line in translated}
    for line in lines:
        key = round(line["time"], 2)
        translation = translated_by_time.get(key)
        if translation is None:
            candidates = (
                (abs(other_time - key), text)
                for other_time, text in translated_by_time.items()
                if abs(other_time - key) <= 0.15
            )
            translation = min(candidates, default=(999, None))[1]
        if translation and translation != line["text"]:
            line["translation"] = translation
    return lines
