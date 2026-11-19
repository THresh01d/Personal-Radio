"""有限的本机诊断：只接受固定阶段/原因，不接收消息、URL、凭据或异常正文。"""
import asyncio
from collections import OrderedDict, deque
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
import inspect
import logging
import secrets
import threading
import time

import httpx
from starlette.responses import JSONResponse


STAGES = frozenset({"model_load", "embedding", "library_load", "retrieval", "llm",
                    "gateway_ready", "gateway", "gateway_legacy_playback", "tts", "tts_generate"})
NOTICES = {
    "offline": "当前使用离线基础模式，推荐仍来自本地曲库。",
    "llm_unconfigured": "AI 尚未配置，已使用本地规则完成相关步骤。",
    "llm_busy": "AI 服务繁忙，相关步骤已回到本地规则；可以稍后重试。",
    "chatgpt_usage_limit": "ChatGPT 套餐额度暂不可用，可在 ChatGPT 设置查看用量。",
    "llm_timeout": "AI 响应超时，相关步骤已回到本地规则；可以稍后重试。",
    "llm_network": "AI 网络暂不可用，相关步骤已回到本地规则。",
    "llm_http": "AI 服务拒绝或未完成请求，请在 AI 设置中测试连接。",
    "llm_invalid": "AI 返回格式不可用，相关步骤已使用本地规则。",
    "agent_fallback": "本轮 AI 流程未完成，已尝试本地推荐。",
    "gateway_unavailable": "网易云网关暂不可用，请稍后重试；本地曲库仍可浏览。",
    "gateway_timeout": "网易云响应超时，请稍后重试。",
    "gateway_http": "网易云请求未完成，请检查权限或稍后重试。",
    "tts_busy": "播报繁忙，直接放歌。",
    "tts_timeout": "播报超时，直接放歌。",
    "tts_failed": "播报暂不可用，直接放歌。",
    "tts_forbidden": "在线语音拒绝连接，请检查语音依赖更新。",
    "tts_network": "在线语音连接失败，将尝试本机语音。",
    "tts_empty": "在线语音没有返回音频，将尝试本机语音。",
    "tts_cooldown": "在线语音暂时避让，将尝试本机语音。",
    "request_busy": "请求繁忙，请稍后再试。",
    "request_timeout": "请求接收超时，请重试。",
    "request_cancelled": "请求等待已取消；不代表后台工作已经停止。",
    "request_invalid": "请求参数不正确。",
    "request_denied": "请求未通过本机访问检查，请刷新本机页面。",
    "request_failed": "请求暂未完成，请稍后重试。",
    "internal_error": "服务暂时未完成请求，请稍后重试。",
}
PLAYBACK_NETWORK_REASONS = frozenset({
    "connection_reset", "dns_failure", "connection_timeout", "connection_refused", "tls_error", "network_error",
})
_PLAYBACK_FAILURE_REASONS = PLAYBACK_NETWORK_REASONS | {
    "upstream_http", "platform_error", "gateway_error", "gateway_timeout", "gateway_connection", "invalid_response",
}
_current = ContextVar("radio_diagnostic", default=None)
_lock = threading.RLock()
_recent = deque(maxlen=100)
_active = OrderedDict()


class Trace:
    def __init__(self, category):
        self.request_id = secrets.token_hex(8)
        self.category = category
        self.started = time.monotonic()
        self.stages = {}
        self.running = {}
        self.codes = []
        self.playback_failure = None
        self.lock = threading.RLock()

    def snapshot(self, status=None):
        with self.lock:
            result = {"request_id": self.request_id, "category": self.category,
                    "status": status, "elapsed_ms": round((time.monotonic() - self.started) * 1000, 2),
                    "stages": {name: dict(value) for name, value in self.stages.items()},
                    "running_stages": sorted(self.running), "codes": list(self.codes)}
            if self.playback_failure is not None:
                result["playback_failure"] = dict(self.playback_failure)
            return result


def is_playback_platform_code(value):
    # 平台业务码可能为负数，和 HTTP 状态码不是同一套取值范围。
    return type(value) is int and -(2**31) <= value < 2**31


def note_playback_failure(reason, *, gateway_status=None, upstream_status=None, platform_code=None):
    """只保存取址故障的有限字段，原始响应或地址不得进入请求记录。"""
    trace = _current.get()
    if trace is None or reason not in _PLAYBACK_FAILURE_REASONS:
        return
    result = {"reason": reason}
    for field, value in (("gateway_status", gateway_status), ("upstream_status", upstream_status)):
        if type(value) is int and 100 <= value <= 599:
            result[field] = value
    if is_playback_platform_code(platform_code):
        result["platform_code"] = platform_code
    with trace.lock:
        trace.playback_failure = result


def note(code):
    trace = _current.get()
    if trace is not None and code in NOTICES:
        with trace.lock:
            if code not in trace.codes:
                trace.codes.append(code)


def llm_failure_code(exc):
    code = getattr(exc, "code", None)
    if isinstance(code, str) and code in {"offline", "llm_unconfigured", "llm_busy", "chatgpt_usage_limit"}:
        return code
    if isinstance(exc, httpx.TimeoutException):
        return "llm_timeout"
    if isinstance(exc, httpx.HTTPStatusError):
        return "llm_http"
    if isinstance(exc, httpx.HTTPError):
        return "llm_network"
    return "llm_invalid"


@contextmanager
def stage(name):
    if name not in STAGES:
        raise ValueError("诊断阶段必须来自固定词表")
    trace = _current.get()
    if trace is None:
        yield
        return
    started = time.monotonic()
    with trace.lock:
        trace.running[name] = trace.running.get(name, 0) + 1
    try:
        yield
    finally:
        with trace.lock:
            trace.running[name] -= 1
            if not trace.running[name]:
                del trace.running[name]
            entry = trace.stages.setdefault(name, {"calls": 0, "elapsed_ms": 0})
            entry["calls"] += 1
            entry["elapsed_ms"] = round(entry["elapsed_ms"] + (time.monotonic() - started) * 1000, 2)


def timed(name):
    """保留函数签名与同步/异步语义，不为计时另起线程或改变取消边界。"""
    def decorate(fn):
        if inspect.iscoroutinefunction(fn):
            @wraps(fn)
            async def async_call(*args, **kwargs):
                with stage(name):
                    return await fn(*args, **kwargs)
            return async_call
        @wraps(fn)
        def sync_call(*args, **kwargs):
            with stage(name):
                return fn(*args, **kwargs)
        return sync_call
    return decorate


def current_summary():
    trace = _current.get()
    if trace is None:
        return None
    result = trace.snapshot()
    result["notices"] = [NOTICES[code] for code in result["codes"]]
    return result


def snapshot():
    with _lock:
        return {"capacity": 100, "active_capacity": 64,
                "recent": list(_recent), "active": [trace.snapshot() for trace in _active.values()],
                "scope": "仅当前进程内存；阶段可嵌套，不可相加当作总耗时；不记录消息、凭据或私人标识"}


def _category(path):
    # 不记录原路径，任意伪造路径、会话/歌曲 ID 和查询内容也不会进入诊断。
    if path in {"/api/chat", "/api/auto-queue", "/api/recommendations/adjust"}:
        return "recommendation"
    if path.startswith("/api/ncm/"):
        return "ncm"
    if path in {"/api/tts", "/api/tts/check"}:
        return "tts"
    if path.startswith("/api/settings/"):
        return "settings"
    return "api"


class DiagnosticsMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not scope["path"].startswith("/api/") or scope["path"] == "/api/hotkeys/events":
            return await self.app(scope, receive, send)
        trace = Trace(_category(scope["path"]))
        token = _current.set(trace)
        tracked = scope["path"] != "/api/diagnostics"
        if tracked:
            with _lock:
                _active[trace.request_id] = trace
                while len(_active) > 64:
                    _active.popitem(last=False)
        status = None
        started_response = False

        async def traced_send(message):
            nonlocal status, started_response
            if message["type"] == "http.response.start":
                status = message["status"]
                started_response = True
                if status >= 400 and not trace.codes:
                    note({401: "request_denied", 403: "request_denied", 408: "request_timeout",
                          413: "request_invalid", 422: "request_invalid", 429: "request_busy"}.get(status, "request_failed"))
                result = trace.snapshot(status)
                timings = [f'{name};dur={data["elapsed_ms"]}' for name, data in result["stages"].items()]
                timings.append(f'total;dur={result["elapsed_ms"]}')
                headers = list(message.get("headers", []))
                headers += [(b"x-request-id", trace.request_id.encode()),
                            (b"server-timing", ", ".join(timings).encode())]
                if trace.codes:
                    headers.append((b"x-failure-code", trace.codes[-1].encode()))
                message = {**message, "headers": headers}
            await send(message)
        try:
            await self.app(scope, receive, traced_send)
        except asyncio.CancelledError:
            note("request_cancelled")
            raise
        except Exception:
            note("internal_error")
            # 只输出固定代码，不让服务端默认异常页和 traceback 泄露上游内容。
            logging.getLogger("personal_radio").warning("internal_error request_id=%s", trace.request_id)
            if not started_response:
                await JSONResponse({"detail": NOTICES["internal_error"]}, status_code=500)(scope, receive, traced_send)
            else:
                raise
        finally:
            if tracked:
                with _lock:
                    _active.pop(trace.request_id, None)
                    _recent.append(trace.snapshot(status))
            _current.reset(token)
