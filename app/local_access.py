"""本机单用户入口边界；防跨站/重绑定，不把匿名会话 ID 当访问凭证。"""

import ipaddress
import secrets
from http.cookies import SimpleCookie
from urllib.parse import urlsplit

from starlette.responses import JSONResponse

COOKIE_NAME = "personal_radio_access"
BOOTSTRAP_PATH = "/api/local-access"


def local_authority(value, default_port=80):
    """只接受字面回环主机；端口参与同源比较，不信任转发头或任意域名。"""
    try:
        parsed = urlsplit("http://" + value)
        if parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
            return None
        if parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment:
            return None
        return parsed.hostname, parsed.port or default_port
    except ValueError:
        return None


class LocalAccessMiddleware:
    """纯 ASGI 边界避免流式/取消行为被 BaseHTTPMiddleware 改写。"""

    def __init__(self, app, token=None):
        self.app = app
        self.token = token or secrets.token_urlsafe(32)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        # Host 可伪造，客户端地址也必须是回环；不依赖反向代理的转发头。
        try:
            local_peer = ipaddress.ip_address(scope["client"][0]).is_loopback
        except (ValueError, TypeError, KeyError):
            local_peer = False
        headers = {k.decode("latin1").lower(): v.decode("latin1") for k, v in scope["headers"]}
        sensitive = {b"host", b"origin", b"sec-fetch-site"}
        boundary_headers = [k.lower() for k, _ in scope["headers"] if k.lower() in sensitive]
        unique_headers = len(boundary_headers) == len(set(boundary_headers))
        default_port = 443 if scope.get("scheme") == "https" else 80
        host = local_authority(headers.get("host", ""), default_port)
        origin = headers.get("origin")
        valid_origin = True
        if origin is not None:
            try:
                parsed = urlsplit(origin)
                valid_origin = (parsed.scheme == scope.get("scheme", "http")
                                and not parsed.path and not parsed.query and not parsed.fragment
                                and local_authority(parsed.netloc, default_port) == host)
            except ValueError:
                valid_origin = False
        site = headers.get("sec-fetch-site")
        # same-site 也可能来自本机另一端口，不能等价于 same-origin。
        if not local_peer or not unique_headers or not host or not valid_origin or site not in (None, "none", "same-origin"):
            await JSONResponse({"detail": "仅允许本机同源页面访问"}, status_code=403)(scope, receive, send)
            return
        path = scope["path"]
        cookie_name = f"{COOKIE_NAME}_{host[1]}"
        if path == BOOTSTRAP_PATH and scope["method"] == "GET":
            response = JSONResponse({"ok": True}, headers={"Cache-Control": "no-store"})
            response.set_cookie(cookie_name, self.token, httponly=True, samesite="strict",
                                secure=scope.get("scheme") == "https", path="/")
            await response(scope, receive, send)
            return
        if path.startswith("/api/") or path in {"/docs", "/redoc", "/openapi.json"}:
            cookie = SimpleCookie()
            try:
                cookie.load(headers.get("cookie", ""))
                supplied = cookie[cookie_name].value if cookie_name in cookie else ""
            except Exception:
                supplied = ""
            if not secrets.compare_digest(supplied.encode(), self.token.encode()):
                await JSONResponse({"detail": "本机访问凭证已失效，请刷新页面"}, status_code=401)(scope, receive, send)
                return
        # 不向脚本暴露 Cookie；frame-ancestors 防止攻击网站嵌入本机页面。
        async def secure_send(message):
            if message["type"] == "http.response.start":
                message.setdefault("headers", []).extend([
                    (b"x-content-type-options", b"nosniff"),
                    (b"content-security-policy", b"frame-ancestors 'none'"),
                    (b"referrer-policy", b"no-referrer"),
                ])
            await send(message)
        await self.app(scope, receive, secure_send)
