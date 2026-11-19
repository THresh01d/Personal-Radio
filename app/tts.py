"""app/tts.py - 电台语音播报：文本 -> 中文语音 mp3（edge-tts，微软晓晓）

为什么单独一层：
- 电台的「开口」= 文本 -> mp3。用 edge-tts 走微软神经音（免费、无需 API key），
  与主链路（检索/防幻觉）完全解耦：合成挂了最多是不说话，绝不影响推荐本身；
- 磁盘缓存同一句话不重复合成：edge-tts 有频率限制，缓存既省时间也防触发限流。

约束：
- 只被 /api/tts 调用，不碰 Agent/检索链路；不保存任何歌词，只转发文本；
- MAX_TEXT_LEN 限制单句长度：控制合成延迟与请求体积（一句报幕足够）。
"""

import asyncio
import hashlib
import logging
import os
import re
import tempfile
import threading
import time
from pathlib import Path

import edge_tts
from app.paths import DATA_ROOT
from app.diagnostics import timed, stage
from app.tts_health import VoiceHealth, TTSUnavailable, TTSCheckLimited, EmptyAudioError, failure_code, MESSAGES

ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = DATA_ROOT / "tts_cache"
VOICE = "zh-CN-XiaoxiaoNeural"  # 晓晓：自然中文女声，有电台感
DJ_VOICES = frozenset((
    VOICE,
    "zh-CN-XiaoyiNeural",
    "zh-CN-YunxiNeural",
    "zh-CN-YunyangNeural",
    "zh-CN-YunjianNeural",
    "zh-CN-YunxiaNeural",
))

MAX_TEXT_LEN = 300  # 单次播报文本上限（字）
SYNTHESIS_TIMEOUT = 8  # 先于浏览器十五秒加载上限结束，留出响应传输和媒体解码时间。
MAX_AUDIO_BYTES = 5 * 1024 * 1024
MAX_CACHE_BYTES = 64 * 1024 * 1024
MAX_CACHE_FILES = 128
CACHE_TTL = 7 * 24 * 3600
_slots = threading.BoundedSemaphore(2)
_cache_lock = threading.RLock()
_inflight = set()
health = VoiceHealth()


class TTSBusy(RuntimeError):
    """没有空闲合成槽位时立即降级，不创建无限排队任务。"""


def _cache_path(text: str, voice: str) -> Path:
    """缓存文件名 = hash(voice + text)：稳定、不泄露明文，同句必同文件。"""
    key = hashlib.sha1(f"{voice}\n{text}".encode("utf-8")).hexdigest()
    return CACHE_DIR / f"{key}.mp3"


def _prune_cache(reserved_bytes=0, reserved_files=0):
    """只清理本目录中本服务生成的哈希 MP3，不递归、不跟随链接、不碰未知文件。"""
    records = []
    removed = 0
    now = time.time()
    # 只识别我们带前缀的崩溃残留，留一小时避免误删另一进程的在途文件。
    for temporary in CACHE_DIR.glob("pr-tts-*.tmp"):
        if temporary.is_symlink() or not temporary.is_file():
            continue
        try:
            if now - temporary.stat().st_mtime > 3600:
                temporary.unlink()
                removed += 1
        except FileNotFoundError:
            pass
    for path in CACHE_DIR.glob("*.mp3"):
        if path.is_symlink() or not path.is_file() or not re.fullmatch(r"[0-9a-f]{40}\.mp3", path.name):
            continue
        try:
            stat = path.stat()
            if now - stat.st_mtime > CACHE_TTL or stat.st_size > MAX_AUDIO_BYTES:
                path.unlink()
                removed += 1
            else:
                records.append((stat.st_mtime, stat.st_size, path))
        except FileNotFoundError:
            pass
    total = sum(size for _, size, _ in records)
    records.sort()
    while records and (len(records) + reserved_files > MAX_CACHE_FILES or total + reserved_bytes > MAX_CACHE_BYTES):
        _, size, path = records.pop(0)
        path.unlink(missing_ok=True)
        total -= size
        removed += 1
    if removed:
        logging.getLogger("personal_radio").info("tts_cache_pruned files=%s", removed)


async def _generate(text, voice):
    data = bytearray()
    comm = edge_tts.Communicate(text, voice=voice)
    async for chunk in comm.stream():
        if chunk["type"] == "audio":
            data.extend(chunk["data"])
            if len(data) > MAX_AUDIO_BYTES:
                raise RuntimeError("语音音频超过上限")
    if not data:
        raise EmptyAudioError("语音合成返回空音频")
    return bytes(data)


async def _network_audio(text, voice, *, manual=False):
    ticket = health.begin_attempt(manual=manual)
    try:
        with stage("tts_generate"):
            data = await asyncio.wait_for(_generate(text, voice), SYNTHESIS_TIMEOUT)
    except asyncio.CancelledError:
        health.cancelled(ticket)
        raise
    except Exception as exc:
        health.failure(ticket, failure_code(exc))
        raise
    health.success(ticket)
    return data


async def check_online():
    """只在用户点击检测时合成固定短句，绕过缓存，不播放、不保存测试音频。"""
    if not _slots.acquire(blocking=False):
        raise TTSBusy("语音服务繁忙")
    reserved = False
    report = None
    started = time.monotonic()
    try:
        health.reserve_check()
        reserved = True
        try:
            audio = await _network_audio("私人电台，语音连接测试。", VOICE, manual=True)
            report = {"ok": True, "code": "ok", "message": "在线合成成功；实际出声请用试听确认。", "audio_bytes": len(audio)}
        except TTSUnavailable:
            raise
        except Exception as exc:
            code = failure_code(exc)
            report = {"ok": False, "code": code, "message": MESSAGES[code], "audio_bytes": 0}
        report.update(elapsed_ms=round((time.monotonic() - started) * 1000), checked_at=int(time.time()))
    finally:
        if reserved:
            health.finish_check(report)
        _slots.release()
    return health.snapshot()


@timed("tts")
async def synthesize(text: str, voice: str = VOICE) -> bytes:
    text = (text or "").strip()[:MAX_TEXT_LEN]
    if not text:
        raise ValueError("播报文本为空")
    path = _cache_path(text, voice)
    with _cache_lock:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        _prune_cache()
        if path.exists() and not path.is_symlink():
            data = path.read_bytes()
            if data:
                os.utime(path, None)  # 按近期使用淘汰，而不是只看生成时间。
                return data
        if path in _inflight or not _slots.acquire(blocking=False):
            raise TTSBusy("语音服务繁忙")
        _inflight.add(path)
    temporary = None
    try:
        data = await _network_audio(text, voice)
        with _cache_lock:
            _prune_cache(len(data), 1)
            with tempfile.NamedTemporaryFile(dir=CACHE_DIR, prefix="pr-tts-", suffix=".tmp", delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(data)
            os.replace(temporary, path)
        return data
    finally:
        # 超时、外部取消、上游异常和文件失败都释放槽位，失败不留半截 MP3。
        try:
            if temporary and temporary.exists():
                temporary.unlink()
        finally:
            with _cache_lock:
                _inflight.discard(path)
                _slots.release()
