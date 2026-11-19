"""语音可用性只保留进程内状态，不落盘、不保存播报文字或上游异常正文。"""
import importlib.metadata
import math
from pathlib import Path
import re
import threading
import time

import aiohttp
import edge_tts
from edge_tts.exceptions import NoAudioReceived

LOADED_VERSION = getattr(edge_tts, "__version__", "unknown")
PIN_FILE = Path(__file__).resolve().parent.parent / "requirements.txt"
MESSAGES = {
    "tts_forbidden": "在线语音服务拒绝连接，请检查语音依赖更新。",
    "tts_network": "在线语音连接失败，请检查网络或证书。",
    "tts_timeout": "在线语音合成超时。",
    "tts_empty": "在线语音服务没有返回音频。",
    "tts_failed": "在线语音合成暂不可用。",
    "tts_cooldown": "在线语音暂时避让中，稍后随报幕重试。",
}


class TTSUnavailable(RuntimeError):
    def __init__(self, code="tts_cooldown", retry_after=60):
        self.code = code
        self.retry_after = retry_after
        super().__init__(MESSAGES[code])


class TTSCheckLimited(RuntimeError):
    def __init__(self, retry_after=30):
        self.retry_after = retry_after
        super().__init__("检测过于频繁，请稍后再试。")


class EmptyAudioError(RuntimeError):
    pass


def failure_code(exc):
    if isinstance(exc, TimeoutError):
        return "tts_timeout"
    if getattr(exc, "status", None) in (401, 403):
        return "tts_forbidden"
    if isinstance(exc, (NoAudioReceived, EmptyAudioError)):
        return "tts_empty"
    if isinstance(exc, aiohttp.ClientError):
        return "tts_network"
    return "tts_failed"


def _version(value):
    return value if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9.+_-]{1,40}", value) else "unknown"


def _installed_version():
    try:
        return _version(importlib.metadata.version("edge-tts"))
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


def _required_version():
    try:
        match = re.search(r"(?m)^edge-tts==([A-Za-z0-9.+_-]+)\s*$", PIN_FILE.read_text(encoding="utf-8-sig"))
        return _version(match[1]) if match else "unknown"
    except OSError:
        return "unknown"


class VoiceHealth:
    """两次连续上游失败避让60秒；恢复仅放行一次试探，旧并发结果不覆盖新状态。"""

    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.lock = threading.RLock()
        self.epoch = 0
        self.failures = 0
        self.blocked_until = 0.0
        self.trial = False
        self.last_failure = None
        self.check_started = None
        self.checking = False
        self.last_check = None

    def begin_attempt(self, manual=False):
        with self.lock:
            now = self.clock()
            if self.trial:
                raise TTSUnavailable(retry_after=max(1, math.ceil(self.blocked_until - now)))
            if not manual and now < self.blocked_until:
                raise TTSUnavailable(retry_after=math.ceil(self.blocked_until - now))
            if manual or self.blocked_until:
                self.epoch += 1
                self.trial = True
            return self.epoch

    def success(self, ticket):
        with self.lock:
            if ticket != self.epoch:
                return
            self.epoch += 1
            self.failures = 0
            self.blocked_until = 0.0
            self.trial = False
            self.last_failure = None

    def failure(self, ticket, code):
        with self.lock:
            if ticket != self.epoch:
                return
            self.failures += 1
            self.last_failure = code
            if self.failures >= 2 or (self.trial and self.blocked_until):
                self.blocked_until = self.clock() + 60
                self.epoch += 1
            self.trial = False

    def cancelled(self, ticket):
        with self.lock:
            if ticket == self.epoch:
                # 用户取消不能算上游故障，也不能永久占住恢复试探资格。
                self.trial = False

    def reserve_check(self):
        with self.lock:
            now = self.clock()
            remaining = 30 - (now - self.check_started) if self.check_started is not None else 0
            if self.checking or remaining > 0:
                raise TTSCheckLimited(max(1, math.ceil(remaining)))
            self.check_started = now
            self.checking = True

    def finish_check(self, report=None):
        with self.lock:
            self.checking = False
            if report is not None:
                self.last_check = dict(report)

    def snapshot(self):
        installed = _installed_version()
        required = _required_version()
        loaded = _version(LOADED_VERSION)
        with self.lock:
            return {
                "loaded_version": loaded, "installed_version": installed, "required_version": required,
                "restart_required": "unknown" not in (loaded, installed) and loaded != installed,
                "dependency_mismatch": "unknown" not in (installed, required) and installed != required,
                "cooldown_seconds": max(0, math.ceil(self.blocked_until - self.clock())),
                "last_failure_code": self.last_failure,
                "last_failure_message": MESSAGES.get(self.last_failure, ""),
                "checking": self.checking,
                "last_check": dict(self.last_check) if self.last_check else None,
            }

