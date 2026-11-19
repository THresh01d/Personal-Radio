"""声学观察基础层；不打开数据库、不初始化正式服务、不消费旧摘要。

调用方必须显式连接并开启事务，生产接线时使用 BEGIN IMMEDIATE。
观察身份只约束提交与作品绑定，不证明播放、信号充分性或音乐理解。
"""

from contextlib import contextmanager
import hashlib
import json
import math
import re
import time
import uuid


LEASE_SECONDS = 20 * 60
MAX_LEASES = 256
MAX_OBSERVATIONS = 20_000
MAX_SONG_BINDINGS = 20_000
MAX_PAYLOAD_BYTES = 4096
FEATURE_ALGORITHM = "web-audio-v1"
CAPTURE_CONTRACT = "media-clock-v1"
CONFIGURATIONS = {
    "foreground": "fft2048-s078-bands-v1",
    "background": "fft2048-s072-bands-v1",
}
FIELDS = ("energy_mean", "energy_peak", "low_ratio", "brightness", "dynamic_range", "beat_density", "sample_seconds")
BINDING_FIELDS = ("library_generation", "song_id", "song_revision", "metadata_sha256",
                  "feature_algorithm", "capture_contract", "measurement_mode", "capture_configuration")
EPSILON = 1e-6


class ObservationError(ValueError):
    """格式错误；错误文本不携带客户端载荷。"""


class LeaseUnavailable(ObservationError):
    """未发放、过期、取消或作品绑定失效。"""


class ObservationConflict(ObservationError):
    """同编号不能更换原包。"""


class ObservationCapacity(ObservationError):
    """有限容量耗尽，不自动删私人证据。"""


def _invalid():
    raise ObservationError("声学观察格式无效")


def _identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{32}", value):
        _invalid()
    return value


def _timestamp(value):
    value = time.time() if value is None else value
    if type(value) not in (int, float):
        _invalid()
    try:
        value = float(value)
    except OverflowError:
        _invalid()
    if not math.isfinite(value) or not 0 <= value <= 253402299599:
        _invalid()
    return float(value)


@contextmanager
def _atomic(conn):
    if not conn.in_transaction:
        raise RuntimeError("声学观察写入须由调用方先开启事务")
    # 保存点也保护捕获异常后继续提交外层事务的调用者；不隐式提交其他模块的写入。
    name = "audio_observation_" + uuid.uuid4().hex
    conn.execute(f"SAVEPOINT {name}")
    try:
        yield
    except BaseException:
        conn.execute(f"ROLLBACK TO {name}")
        conn.execute(f"RELEASE {name}")
        raise
    else:
        conn.execute(f"RELEASE {name}")


def ensure_schema(conn):
    """仅在调用者显式事务创建新命名空间，不迁移/写入旧声学表。"""
    with _atomic(conn):
        for statement in (
            "CREATE TABLE IF NOT EXISTS audio_observation_state (singleton INTEGER PRIMARY KEY CHECK(singleton=1), generation TEXT NOT NULL)",
            "CREATE TABLE IF NOT EXISTS audio_observation_songs (song_id TEXT PRIMARY KEY, revision TEXT NOT NULL, metadata_sha256 TEXT NOT NULL)",
            """CREATE TABLE IF NOT EXISTS audio_observation_leases (
                observation_id TEXT PRIMARY KEY, library_generation TEXT NOT NULL, song_id TEXT NOT NULL,
                song_revision TEXT NOT NULL, metadata_sha256 TEXT NOT NULL, feature_algorithm TEXT NOT NULL,
                capture_contract TEXT NOT NULL, measurement_mode TEXT NOT NULL, capture_configuration TEXT NOT NULL,
                issued_at REAL NOT NULL, expires_at REAL NOT NULL, payload_sha256 TEXT, accepted_at REAL)""",
            "CREATE INDEX IF NOT EXISTS idx_audio_observation_leases_expiry ON audio_observation_leases(expires_at)",
            """CREATE TABLE IF NOT EXISTS audio_observations (
                observation_id TEXT PRIMARY KEY, library_generation TEXT NOT NULL, song_id TEXT NOT NULL,
                song_revision TEXT NOT NULL, metadata_sha256 TEXT NOT NULL, feature_algorithm TEXT NOT NULL,
                capture_contract TEXT NOT NULL, measurement_mode TEXT NOT NULL, capture_configuration TEXT NOT NULL,
                payload_sha256 TEXT NOT NULL, payload_json TEXT NOT NULL, received_at REAL NOT NULL)""",
            "CREATE INDEX IF NOT EXISTS idx_audio_observations_song ON audio_observations(song_id)",
        ):
            conn.execute(statement)
        conn.execute("INSERT OR IGNORE INTO audio_observation_state VALUES (1,?)", (uuid.uuid4().hex,))


def _exists(conn):
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='audio_observation_state'").fetchone() is not None


def _lock(conn):
    # 调用方应使用 BEGIN IMMEDIATE；即使传入普通事务，也在检查容量/状态前取得写锁。
    # 锁升级失败直接交还调用方，不在此重试或建立第二条连接。
    conn.execute("UPDATE audio_observation_state SET generation=generation WHERE singleton=1")


def _song_hash(conn, song_id):
    row = conn.execute("SELECT title,artist,album,duration_seconds FROM songs WHERE song_id=?", (song_id,)).fetchone()
    if row is None:
        raise LookupError("歌曲不在当前曲库中")
    metadata = [row[0] or "", row[1] or "", row[2] or "", row[3] or 0]
    if any(not isinstance(value, str) for value in metadata[:3]) or type(metadata[3]) is not int or metadata[3] < 0:
        _invalid()
    return hashlib.sha256(json.dumps(metadata, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def prepare_observation(conn, song_id, measurement_mode, *, now=None):
    """发放新观察租约；未知作品、容量不足或格式错误不留下部分绑定/租约。"""
    timestamp = _timestamp(now)
    if (not isinstance(song_id, str) or not song_id or len(song_id) > 128
            or not isinstance(measurement_mode, str) or measurement_mode not in CONFIGURATIONS):
        _invalid()
    with _atomic(conn):
        metadata = _song_hash(conn, song_id)
        ensure_schema(conn)
        _lock(conn)
        # 取得写锁后再核对一次，防止普通事务升级过程误用旧作品快照。
        metadata = _song_hash(conn, song_id)
        conn.execute("DELETE FROM audio_observation_leases WHERE expires_at<=?", (timestamp,))
        if (conn.execute("SELECT COUNT(*) FROM audio_observation_leases").fetchone()[0] >= MAX_LEASES
                or conn.execute("SELECT COUNT(*) FROM audio_observations").fetchone()[0] >= MAX_OBSERVATIONS):
            raise ObservationCapacity("声学观察容量已满，暂时停止新分析保存")
        row = conn.execute("SELECT revision,metadata_sha256 FROM audio_observation_songs WHERE song_id=?", (song_id,)).fetchone()
        if row is None and conn.execute("SELECT COUNT(*) FROM audio_observation_songs").fetchone()[0] >= MAX_SONG_BINDINGS:
            raise ObservationCapacity("声学观察容量已满，暂时停止新分析保存")
        revision = row[0] if row and row[1] == metadata else uuid.uuid4().hex
        conn.execute("""INSERT INTO audio_observation_songs VALUES (?,?,?) ON CONFLICT(song_id)
            DO UPDATE SET revision=excluded.revision, metadata_sha256=excluded.metadata_sha256""", (song_id, revision, metadata))
        binding = {
            "library_generation": conn.execute("SELECT generation FROM audio_observation_state WHERE singleton=1").fetchone()[0],
            "song_id": song_id, "song_revision": revision, "metadata_sha256": metadata,
            "feature_algorithm": FEATURE_ALGORITHM, "capture_contract": CAPTURE_CONTRACT,
            "measurement_mode": measurement_mode, "capture_configuration": CONFIGURATIONS[measurement_mode],
        }
        observation_id = uuid.uuid4().hex
        expires = timestamp + LEASE_SECONDS
        conn.execute("INSERT INTO audio_observation_leases VALUES (?,?,?,?,?,?,?,?,?,?,?,NULL,NULL)",
                     (observation_id, *(binding[key] for key in BINDING_FIELDS), timestamp, expires))
        return {"observation_id": observation_id, "binding": binding, "expires_at": expires}


def canonical_payload(observation_id, payload):
    """规范化完整语义包；不先夹值，不以标签/格式声明冒充音源真实性。"""
    _identifier(observation_id)
    if not isinstance(payload, dict) or set(payload) != {*BINDING_FIELDS, "summary", "source_extent", "confidence"}:
        _invalid()
    for key in BINDING_FIELDS:
        if not isinstance(payload[key], str):
            _invalid()
    _identifier(payload["library_generation"])
    _identifier(payload["song_revision"])
    if (not payload["song_id"] or len(payload["song_id"]) > 128
            or not re.fullmatch(r"[0-9a-f]{64}", payload["metadata_sha256"])
            or payload["feature_algorithm"] != FEATURE_ALGORITHM or payload["capture_contract"] != CAPTURE_CONTRACT
            or payload["measurement_mode"] not in CONFIGURATIONS
            or payload["capture_configuration"] != CONFIGURATIONS[payload["measurement_mode"]]
            or payload["source_extent"] != "unknown" or payload["confidence"] != "uncalibrated"):
        _invalid()
    raw = payload["summary"]
    if not isinstance(raw, dict) or set(raw) != set(FIELDS):
        _invalid()
    summary = {}
    for field in FIELDS:
        value = raw[field]
        if type(value) not in (int, float):
            _invalid()
        try:
            value = float(value)
        except OverflowError:
            _invalid()
        if not math.isfinite(value):
            _invalid()
        low, high = (12, 86400) if field == "sample_seconds" else (0, 300) if field == "beat_density" else (0, 1)
        tolerance = 0 if field == "sample_seconds" else EPSILON
        if value < low - tolerance or value > high + tolerance:
            _invalid()
        # 1与1.0、正负零允许语义一致；保留其他有限原值用于冲突判断，不静默夹值。
        summary[field] = float(value) if value else 0.0
    if (summary["energy_peak"] <= 0 or summary["energy_mean"] > summary["energy_peak"] + EPSILON
            or summary["low_ratio"] + summary["brightness"] > 1 + EPSILON):
        _invalid()
    canonical = {**payload, "summary": summary, "observation_id": observation_id}
    encoded = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    if len(encoded.encode()) > MAX_PAYLOAD_BYTES:
        _invalid()
    return encoded, hashlib.sha256(encoded.encode()).hexdigest()


def submit_observation(conn, observation_id, payload, *, now=None):
    """同租约同包只接受一次；记录与已接受哈希一起成功或一起回滚。"""
    timestamp = _timestamp(now)
    encoded, fingerprint = canonical_payload(observation_id, payload)
    with _atomic(conn):
        if not _exists(conn):
            raise LeaseUnavailable("声学观察租约不存在或已失效")
        _lock(conn)
        row = conn.execute("""SELECT library_generation,song_id,song_revision,metadata_sha256,feature_algorithm,
            capture_contract,measurement_mode,capture_configuration,expires_at,payload_sha256,issued_at
            FROM audio_observation_leases WHERE observation_id=?""", (observation_id,)).fetchone()
        if row is None or timestamp < row[10] or timestamp >= row[8]:
            raise LeaseUnavailable("声学观察租约不存在或已失效")
        binding = dict(zip(BINDING_FIELDS, row[:8]))
        generation = conn.execute("SELECT generation FROM audio_observation_state WHERE singleton=1").fetchone()[0]
        current = conn.execute("SELECT revision,metadata_sha256 FROM audio_observation_songs WHERE song_id=?", (row[1],)).fetchone()
        try:
            metadata = _song_hash(conn, row[1])
        except LookupError as exc:
            raise LeaseUnavailable("声学观察作品绑定已失效") from exc
        if generation != row[0] or current is None or tuple(current) != (row[2], row[3]) or metadata != row[3]:
            raise LeaseUnavailable("声学观察作品绑定已失效")
        if row[9] is not None:
            if row[9] != fingerprint:
                raise ObservationConflict("同一声学观察编号不能更换内容")
            return {"observation_id": observation_id, "status": "duplicate"}
        if any(payload[key] != binding[key] for key in BINDING_FIELDS):
            raise ObservationConflict("声学观察与已发放绑定不一致")
        if conn.execute("SELECT COUNT(*) FROM audio_observations").fetchone()[0] >= MAX_OBSERVATIONS:
            raise ObservationCapacity("声学观察容量已满，暂时停止新分析保存")
        conn.execute("INSERT INTO audio_observations VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                     (observation_id, *(binding[key] for key in BINDING_FIELDS), fingerprint, encoded, timestamp))
        conn.execute("UPDATE audio_observation_leases SET payload_sha256=?,accepted_at=? WHERE observation_id=?",
                     (fingerprint, timestamp, observation_id))
        return {"observation_id": observation_id, "status": "accepted"}


def cancel_observation(conn, observation_id):
    """只取消尚未接受的租约；停止等待不能撤销已经保存的观察。"""
    _identifier(observation_id)
    with _atomic(conn):
        if not _exists(conn):
            return False
        _lock(conn)
        return conn.execute("DELETE FROM audio_observation_leases WHERE observation_id=? AND payload_sha256 IS NULL",
                            (observation_id,)).rowcount == 1


def invalidate_song(conn, song_id):
    """元数据写事务显式调用；即使A→B→A，也不能沿用原修订的租约。"""
    if not isinstance(song_id, str) or not song_id or len(song_id) > 128:
        _invalid()
    with _atomic(conn):
        if not _exists(conn):
            return
        _lock(conn)
        conn.execute("DELETE FROM audio_observation_leases WHERE song_id=?", (song_id,))
        conn.execute("DELETE FROM audio_observation_songs WHERE song_id=?", (song_id,))


def invalidate_library(conn):
    """随显式replace失效已有观察；未用过新协议的库不因此创建新表。"""
    with _atomic(conn):
        if _exists(conn):
            rotate_generation(conn)


def rotate_generation(conn):
    """显式重置时仅清本新命名空间，由调用方与歌曲replace一起提交。"""
    with _atomic(conn):
        ensure_schema(conn)
        _lock(conn)
        generation = uuid.uuid4().hex
        conn.execute("UPDATE audio_observation_state SET generation=? WHERE singleton=1", (generation,))
        for table in ("audio_observation_leases", "audio_observations", "audio_observation_songs"):
            conn.execute(f"DELETE FROM {table}")
        return generation
