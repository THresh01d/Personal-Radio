"""真实播放产生的轻量声学特征。

这里不保存音频、歌词或频谱帧，只保存浏览器 Web Audio 对一首歌的统计摘要。
特征属于歌曲事实，因此放在 ``library.db``；用户是否喜欢它仍由 conversation.db
里的行为事件推断，两类数据不要混在一起。
"""

import math
import sqlite3
import time
from pathlib import Path


from app.paths import DATA_ROOT

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = DATA_ROOT / "library.db"
ALGORITHM_VERSION = "web-audio-v1"
MIN_SAMPLE_SECONDS = 12.0

FEATURE_FIELDS = (
    "energy_mean",
    "energy_peak",
    "low_ratio",
    "brightness",
    "dynamic_range",
    "beat_density",
)

# 相似推荐的可解释距离权重。beat_density 归一到 0-180 次/分钟；它是浏览器
# 检测到的低频瞬态密度，不冒充专业 BPM。
SIMILARITY_WEIGHTS = {
    "energy_mean": 0.30,
    "beat_density": 0.25,
    "low_ratio": 0.20,
    "brightness": 0.15,
    "dynamic_range": 0.10,
}


def _now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _clamp(value, low=0.0, high=1.0):
    number = float(value)
    # 必须先拒绝非有限数，否则 min/max 会把坏数据伪装成有效边界值并写库。
    if not math.isfinite(number):
        raise ValueError("声学数值必须是有限数")
    return max(low, min(high, number))


def init_db(db_path=DB_PATH):
    """幂等创建表；现有 library.db 无需迁移脚本即可升级。"""
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS audio_features (
                song_id TEXT PRIMARY KEY,
                energy_mean REAL NOT NULL,
                energy_peak REAL NOT NULL,
                low_ratio REAL NOT NULL,
                brightness REAL NOT NULL,
                dynamic_range REAL NOT NULL,
                beat_density REAL NOT NULL,
                sample_seconds REAL NOT NULL,
                observation_count INTEGER NOT NULL,
                algorithm_version TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_audio_features_energy
                ON audio_features(energy_mean);
            """
        )


def _clean_summary(summary):
    seconds = _clamp(summary.get("sample_seconds", 0), 0, 24 * 60 * 60)
    if seconds < MIN_SAMPLE_SECONDS:
        raise ValueError(f"有效采样不足 {MIN_SAMPLE_SECONDS:g} 秒")
    return {
        "energy_mean": _clamp(summary.get("energy_mean", 0)),
        "energy_peak": _clamp(summary.get("energy_peak", 0)),
        "low_ratio": _clamp(summary.get("low_ratio", 0)),
        "brightness": _clamp(summary.get("brightness", 0)),
        "dynamic_range": _clamp(summary.get("dynamic_range", 0)),
        # beat_density 是每分钟瞬态次数，不是声称精准 BPM；给异常输入设合理上限。
        "beat_density": _clamp(summary.get("beat_density", 0), 0, 300),
        "sample_seconds": seconds,
        "algorithm_version": str(
            summary.get("algorithm_version") or ALGORITHM_VERSION
        )[:40],
    }


def record_audio_features(song_id, summary, db_path=DB_PATH):
    """写入一次播放摘要，多次观察按有效采样时长合并。

    song_id 必须真实存在于用户曲库，避免任意客户端把幻觉歌曲写进档案。
    算法版本不同时直接以新版本覆盖，防止不同标尺的数值被错误平均。
    """
    clean = _clean_summary(summary)
    init_db(db_path)
    with sqlite3.connect(db_path, timeout=10) as conn:
        conn.execute("BEGIN IMMEDIATE")
        exists = conn.execute(
            "SELECT 1 FROM songs WHERE song_id=?", (str(song_id),)
        ).fetchone()
        if not exists:
            raise LookupError("歌曲不在用户曲库中")
        old = conn.execute(
            "SELECT energy_mean, energy_peak, low_ratio, brightness,"
            " dynamic_range, beat_density, sample_seconds, observation_count,"
            " algorithm_version FROM audio_features WHERE song_id=?",
            (str(song_id),),
        ).fetchone()

        if not old or old[8] != clean["algorithm_version"]:
            values = [clean[field] for field in FEATURE_FIELDS]
            sample_seconds = clean["sample_seconds"]
            count = 1
        else:
            old_seconds = float(old[6])
            new_seconds = clean["sample_seconds"]
            total = old_seconds + new_seconds
            values = []
            for index, field in enumerate(FEATURE_FIELDS):
                if field == "energy_peak":
                    values.append(max(float(old[index]), clean[field]))
                else:
                    values.append(
                        (float(old[index]) * old_seconds + clean[field] * new_seconds)
                        / total
                    )
            sample_seconds = total
            count = int(old[7]) + 1

        conn.execute(
            """
            INSERT INTO audio_features (
                song_id, energy_mean, energy_peak, low_ratio, brightness,
                dynamic_range, beat_density, sample_seconds, observation_count,
                algorithm_version, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(song_id) DO UPDATE SET
                energy_mean=excluded.energy_mean,
                energy_peak=excluded.energy_peak,
                low_ratio=excluded.low_ratio,
                brightness=excluded.brightness,
                dynamic_range=excluded.dynamic_range,
                beat_density=excluded.beat_density,
                sample_seconds=excluded.sample_seconds,
                observation_count=excluded.observation_count,
                algorithm_version=excluded.algorithm_version,
                updated_at=excluded.updated_at
            """,
            (str(song_id), *values, sample_seconds, count,
             clean["algorithm_version"], _now()),
        )
    return get_audio_feature(song_id, db_path=db_path)


def get_audio_features(song_ids, db_path=DB_PATH):
    """批量读取 ``song_id -> 特征``；旧库没有表时自然返回空字典。"""
    ids = [str(song_id) for song_id in dict.fromkeys(song_ids) if song_id is not None]
    if not ids:
        return {}
    try:
        with sqlite3.connect(db_path) as conn:
            placeholders = ",".join("?" for _ in ids)
            rows = conn.execute(
                "SELECT song_id, energy_mean, energy_peak, low_ratio, brightness,"
                " dynamic_range, beat_density, sample_seconds, observation_count,"
                " algorithm_version, updated_at FROM audio_features"
                f" WHERE song_id IN ({placeholders})",
                ids,
            ).fetchall()
    except sqlite3.OperationalError:
        return {}
    keys = (
        "energy_mean", "energy_peak", "low_ratio", "brightness",
        "dynamic_range", "beat_density", "sample_seconds",
        "observation_count", "algorithm_version", "updated_at",
    )
    return {row[0]: dict(zip(keys, row[1:])) for row in rows}


def get_audio_feature(song_id, db_path=DB_PATH):
    return get_audio_features([song_id], db_path=db_path).get(str(song_id))


def attach_audio_features(songs, db_path=DB_PATH):
    """把最新声学事实挂到候选；每次推荐现查，避免内存歌库缓存过期。"""
    features = get_audio_features((song.get("id") for song in songs), db_path=db_path)
    result = []
    for raw in songs:
        song = dict(raw)
        song["audio_features"] = features.get(str(song.get("id")))
        result.append(song)
    return result


def acoustic_similarity(reference, candidate):
    """计算两份同版本声学档案的 0-1 相似度。"""
    if not reference or not candidate:
        return None
    if reference.get("algorithm_version") != candidate.get("algorithm_version"):
        return None
    distance = 0.0
    for field, weight in SIMILARITY_WEIGHTS.items():
        left = float(reference.get(field) or 0)
        right = float(candidate.get(field) or 0)
        if field == "beat_density":
            left, right = min(left, 180) / 180, min(right, 180) / 180
        distance += abs(left - right) * weight
    return max(0.0, min(1.0, 1.0 - distance))


def find_similar_features(song_id, *, limit=30, exclude_ids=None, eligible_ids=None, db_path=DB_PATH):
    """从合格已分析歌曲中找声学近邻；白名单先于截断，空集合不表示无限制。"""
    eligible = None if eligible_ids is None else {str(value) for value in eligible_ids}
    if eligible == set():
        return []
    reference = get_audio_feature(song_id, db_path=db_path)
    if not reference:
        return []
    excluded = {str(value) for value in (exclude_ids or [])}
    excluded.add(str(song_id))
    try:
        with sqlite3.connect(db_path) as conn:
            rows = conn.execute(
                "SELECT song_id, energy_mean, energy_peak, low_ratio, brightness,"
                " dynamic_range, beat_density, sample_seconds, observation_count,"
                " algorithm_version, updated_at FROM audio_features"
                " WHERE algorithm_version=?",
                (reference["algorithm_version"],),
            ).fetchall()
    except sqlite3.OperationalError:
        return []
    keys = (
        "energy_mean", "energy_peak", "low_ratio", "brightness",
        "dynamic_range", "beat_density", "sample_seconds",
        "observation_count", "algorithm_version", "updated_at",
    )
    result = []
    for row in rows:
        # 硬条件不合格作品不能先占满窗口，再由调用方删掉而丢失后续合格邻曲。
        if str(row[0]) in excluded or (eligible is not None and str(row[0]) not in eligible):
            continue
        features = dict(zip(keys, row[1:]))
        score = acoustic_similarity(reference, features)
        if score is not None:
            result.append({
                "id": str(row[0]),
                "acoustic_score": round(score, 4),
                "audio_features": features,
            })
    result.sort(key=lambda item: item["acoustic_score"], reverse=True)
    return result[:max(1, min(int(limit), 100))]


def get_coverage(db_path=DB_PATH, recent_limit=6):
    """返回声学学习覆盖率和最近形成的歌曲档案，供透明画像展示。"""
    init_db(db_path)
    with sqlite3.connect(db_path) as conn:
        total = int(conn.execute("SELECT COUNT(*) FROM songs").fetchone()[0])
        analyzed, seconds = conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(sample_seconds), 0) FROM audio_features"
        ).fetchone()
        rows = conn.execute(
            """
            SELECT a.song_id, s.title, s.artist, a.energy_mean, a.low_ratio,
                   a.brightness, a.beat_density, a.sample_seconds,
                   a.observation_count, a.updated_at
              FROM audio_features a
              JOIN songs s ON s.song_id=a.song_id
             ORDER BY a.updated_at DESC, a.song_id
             LIMIT ?
            """,
            (max(0, int(recent_limit)),),
        ).fetchall()
    return {
        "analyzed_songs": int(analyzed),
        "total_songs": total,
        "coverage_percent": round((int(analyzed) / total * 100) if total else 0, 2),
        "sample_hours": round(float(seconds) / 3600, 2),
        "recent_songs": [
            {
                "id": str(row[0]), "title": row[1], "artist": row[2],
                "energy_mean": row[3], "low_ratio": row[4],
                "brightness": row[5], "beat_density": row[6],
                "sample_seconds": row[7], "observation_count": row[8],
                "updated_at": row[9],
            }
            for row in rows
        ],
    }


def pending_song_ids(song_ids, db_path=DB_PATH):
    """在给定队列中返回尚无当前算法档案的真实曲库歌曲 ID。"""
    ids = [str(value) for value in dict.fromkeys(song_ids) if value is not None]
    if not ids:
        return []
    init_db(db_path)
    with sqlite3.connect(db_path) as conn:
        placeholders = ",".join("?" for _ in ids)
        valid = {
            row[0] for row in conn.execute(
                f"SELECT song_id FROM songs WHERE song_id IN ({placeholders})", ids
            )
        }
        analyzed = {
            row[0] for row in conn.execute(
                f"SELECT song_id FROM audio_features WHERE algorithm_version=?"
                f" AND song_id IN ({placeholders})",
                (ALGORITHM_VERSION, *ids),
            )
        }
    return [song_id for song_id in ids if song_id in valid and song_id not in analyzed]
