"""本机受保护Node网关：只管理进程、端口、健康和HTTP失败边界。"""
import os
import secrets
import socket
import subprocess
import tempfile
import threading
import time
from pathlib import Path
import httpx
from fastapi import HTTPException
from app.diagnostics import PLAYBACK_NETWORK_REASONS, is_playback_platform_code, note, note_playback_failure, stage, timed
from app.paths import DATA_ROOT

ROOT = Path(__file__).resolve().parents[2]
GATEWAY_HOST = "127.0.0.1"
DEFAULT_GATEWAY_PORT = 3000

def _configured_gateway_port():
    """读取可选端口配置；非法值回退默认值，避免应用因配置拼写错误无法启动。"""
    try:
        port = int(os.getenv("NCM_GATEWAY_PORT", str(DEFAULT_GATEWAY_PORT)))
        return port if 1 <= port <= 65535 else DEFAULT_GATEWAY_PORT
    except (TypeError, ValueError):
        return DEFAULT_GATEWAY_PORT


GATEWAY_PORT = _configured_gateway_port()
GATEWAY_URL = f"http://{GATEWAY_HOST}:{GATEWAY_PORT}"
GATEWAY_DIR = ROOT / "gateway"

GATEWAY_READY_TIMEOUT = 30
HEALTHY_TTL = 10
# 回环连接不走系统代理；凭证只传本网关子进程，不写文件或响应。
_http = httpx.Client(timeout=20, trust_env=False)
_gateway_token = secrets.token_urlsafe(32)
_gateway_proc = None
_gateway_lock = threading.Lock()
_healthy_until = 0.0


class PlaybackV1Unavailable(HTTPException):
    """新版取址返回已确认可做兼容尝试的业务错误。"""

def _gateway_healthy():
    """只复用接受本进程凭证的网关，健康检查不再触发网易云外网请求。

    用独立短超时连接探测，避免健康检查占用长超时连接池。
    """
    try:
        with httpx.Client(timeout=2, trust_env=False) as c:
            r = c.get(f"{GATEWAY_URL}/__personal_radio_health",
                      headers={"X-Personal-Radio-Gateway": _gateway_token})
            return r.status_code == 200 and r.json().get("service") == "personal-radio-gateway-v1"
    except Exception:
        return False


def _port_is_available(port):
    """端口能否由本网关绑定；只探测本机，不终止或接管现有进程。"""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind((GATEWAY_HOST, port))
        return True
    except OSError:
        return False


def _find_available_gateway_port(start_port, attempts=50):
    """从配置端口之后寻找空闲端口，避免与其他本地应用直接冲突。"""
    for offset in range(1, attempts + 1):
        candidate = start_port + offset
        if candidate > 65535:
            candidate = 1024 + (candidate - 65536)
        if _port_is_available(candidate):
            return candidate
    raise RuntimeError("附近没有可用端口，无法启动网易云登录网关")


def _use_gateway_port(port):
    """原子更新 Python 侧的端口与地址，后续健康检查和请求共用同一入口。"""
    global GATEWAY_PORT, GATEWAY_URL, _healthy_until
    GATEWAY_PORT = int(port)
    GATEWAY_URL = f"http://{GATEWAY_HOST}:{GATEWAY_PORT}"
    _healthy_until = 0.0


@timed("gateway_ready")
def ensure_gateway():
    """所有直接入口共用可恢复提示，不能只保护经过 _gw 的调用。"""
    try:
        return _ensure_gateway()
    except Exception as exc:
        note("gateway_unavailable")
        raise HTTPException(503, "网易云登录网关暂时无法启动，请检查网关依赖或稍后重试") from exc


def _ensure_gateway():
    """确保网关在运行：已监听则复用，否则拉起 node 并等待就绪。

    幂等 + 双检锁：并发请求同时进来时只有一个线程真正拉起子进程。
    网关重启后也能自动恢复（健康窗口过期后重新拉起）。
    """
    global _gateway_proc, _healthy_until
    now = time.monotonic()
    if now < _healthy_until:  # 窗口内已确认健康，不再探测
        return
    if _gateway_healthy():
        _healthy_until = time.monotonic() + HEALTHY_TTL
        return
    with _gateway_lock:
        if _gateway_healthy():
            _healthy_until = time.monotonic() + HEALTHY_TTL
            return
        # 端口已经被占用、但占用者未通过网易云网关健康检查：不能误杀或复用
        # 其他程序（例如 Mineradio），改用临近空闲端口继续启动。
        if (_gateway_proc is None or _gateway_proc.poll() is not None) and not _port_is_available(GATEWAY_PORT):
            occupied_port = GATEWAY_PORT
            fallback_port = _find_available_gateway_port(occupied_port)
            _use_gateway_port(fallback_port)
            print(f"[ncm] 端口 {occupied_port} 已被其他服务占用，自动切换到 {fallback_port}")
        if _gateway_proc is None or _gateway_proc.poll() is not None:
            # Windows 下用 CREATE_NO_WINDOW 避免弹黑色控制台窗口
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            # 日志写到系统临时目录，便于排障且不污染项目目录
            log_path = os.path.join(tempfile.gettempdir(), "ncm-gateway.log")
            child_env = os.environ.copy()
            child_env["NCM_GATEWAY_PORT"] = str(GATEWAY_PORT)
            child_env["PERSONAL_RADIO_GATEWAY_TOKEN"] = _gateway_token
            child_env["PERSONAL_RADIO_QQ_STATE_DIR"] = str(DATA_ROOT / ".qqmusic")
            with open(log_path, "ab") as log:
                _gateway_proc = subprocess.Popen(
                    ["node", "app.js"],
                    cwd=str(GATEWAY_DIR),
                    stdout=log,
                    stderr=log,
                    creationflags=flags,
                    env=child_env,
                )
            print(f"[ncm] 拉起网易云网关 pid={_gateway_proc.pid}，日志 {log_path}")
        # 轮询等待就绪
        deadline = time.monotonic() + GATEWAY_READY_TIMEOUT
        while time.monotonic() < deadline:
            if _gateway_healthy():
                _healthy_until = time.monotonic() + HEALTHY_TTL
                print("[ncm] 网易云网关已就绪")
                return
            time.sleep(0.4)
        note("gateway_unavailable")
        raise HTTPException(503, "网易云网关启动失败，请检查 gateway/node_modules 是否已安装")


def shutdown_gateway():
    """Python 退出时终止自己拉起的网关子进程（不碰复用的已有监听）。"""
    global _gateway_proc
    if _gateway_proc is not None and _gateway_proc.poll() is None:
        _gateway_proc.terminate()
        try:
            _gateway_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _gateway_proc.kill()
        print(f"[ncm] 已关闭网易云网关 pid={_gateway_proc.pid}")


def _note_playback_response_failure(response, body=None, *, reason="gateway_error"):
    if response is None:
        note_playback_failure(reason)
        return False
    header = response.headers.get("x-radio-ncm-upstream-status", "")
    upstream_status = int(header) if len(header) == 3 and header.isascii() and header.isdecimal() else None
    network_reason = response.headers.get("x-radio-ncm-upstream-error")
    platform_code = body.get("code") if isinstance(body, dict) else None
    if upstream_status is not None and 400 <= upstream_status <= 599:
        reason = "upstream_http"
    elif network_reason in PLAYBACK_NETWORK_REASONS:
        reason = network_reason
    elif (upstream_status is not None and 200 <= upstream_status < 300
          and is_playback_platform_code(platform_code)
          and platform_code not in (200, 502)):
        # 上游依赖自身也可能合成 code=502，来源不明时只保留确认过的状态。
        reason = "platform_error"
    note_playback_failure(
        reason, gateway_status=response.status_code, upstream_status=upstream_status,
        platform_code=platform_code if reason == "platform_error" else None,
    )
    return upstream_status == 200 and response.status_code == 400 and network_reason is None


def _gw(path, params=None, headers=None, method="GET", timeout=20):
    """向网关发请求并解析 JSON；网关不可达时抛 HTTPException 给前端友好提示。

    timeout 按接口区分：扫码/播放这类轻接口用短超时，
    歌单拉取这类可能要翻页/等网易云响应的用长超时。
    """
    playback = path == "/song/url/v1"
    try:
        ensure_gateway()
    except Exception as e:
        note("gateway_unavailable")
        if playback:
            note_playback_failure("gateway_connection")
        # ensure_gateway 的启动/端口错误原本会变成无说明的 500；统一转换为
        # 可供前端直接展示的 503，用户不会再只看到永久加载动画。
        raise HTTPException(503, "网易云登录网关暂时无法启动，请稍后重试") from e
    resp = None
    try:
        protected_headers = {**(headers or {}), "X-Personal-Radio-Gateway": _gateway_token}
        with stage("gateway"):
            resp = _http.request(method, f"{GATEWAY_URL}{path}", params=params, headers=protected_headers, timeout=timeout)
        resp.raise_for_status()
        body = resp.json()
        if playback and (not isinstance(body, dict) or body.get("code") != 200):
            _note_playback_response_failure(resp, body, reason="invalid_response")
        return body
    except httpx.HTTPStatusError as e:
        note("gateway_http")
        # 网关会把网易云的 401/404 等业务限制包装成 HTTP 状态码；保留公开的
        # message（如“歌单已设为隐私”），但绝不把响应头、Cookie 或原始对象回传。
        body = None
        try:
            body = e.response.json()
        except ValueError:
            pass
        wait_for_playback = playback and _note_playback_response_failure(e.response, body)
        remote_message = (body.get("message") or body.get("msg")) if isinstance(body, dict) else None
        # 外部 message 也可能携带凭据，不直接回显；保留可识别的业务原因。
        retry_headers = None
        if isinstance(remote_message, str) and "隐私" in remote_message:
            detail = "网易云返回：该歌单已被创建者设置为隐私"
        elif isinstance(remote_message, str) and any(word in remote_message for word in ("登录", "login")):
            detail = "网易云登录状态已失效，请重新扫码"
        else:
            detail = "网易云请求失败，请稍后再试"
            if wait_for_playback:
                # 实际故障在快速重试时持续失败，约十余秒后同曲成功。
                retry_headers = {"Retry-After": "12"}
        # 隐私歌单不是重试能恢复的临时故障，用 403 让任务层关闭无效重试入口。
        status_code = 403 if isinstance(remote_message, str) and "隐私" in remote_message else 502
        restricted = isinstance(remote_message, str) and any(word in remote_message.lower() for word in (
            "登录", "login", "隐私", "版权", "copyright", "会员", "vip", "付费", "purchase",
            "权限", "授权", "地区", "无权", "试听", "forbidden", "unauthorized", "region",
        ))
        # 仅对已实测的新版 -460、且无明确权限限制尝试兼容取址，不扩大到一般 502。
        if wait_for_playback and isinstance(body, dict) and body.get("code") == -460 and not restricted:
            raise PlaybackV1Unavailable(status_code, detail, headers=retry_headers) from e
        raise HTTPException(status_code, detail, headers=retry_headers) from e
    except httpx.HTTPError as e:
        timed_out = isinstance(e, httpx.TimeoutException)
        note("gateway_timeout" if timed_out else "gateway_unavailable")
        if playback:
            note_playback_failure("gateway_timeout" if timed_out else "gateway_connection")
        # 网关可能在健康窗口内意外挂掉：清掉健康缓存，下次请求自动重启
        global _healthy_until
        _healthy_until = 0.0
        raise HTTPException(503, "网易云服务暂不可用，请稍后再试") from e
    except ValueError as exc:
        note("gateway_http")
        if playback:
            _note_playback_response_failure(resp, reason="invalid_response")
        raise HTTPException(502, "网易云返回格式暂不可用，请稍后再试") from exc
