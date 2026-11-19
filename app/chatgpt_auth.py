"""ChatGPT 套餐的本机授权。回调在独立回环端口接收，业务 API 的同源保护保持原样。"""

import base64
import hashlib
import json
import re
import secrets
import threading
import time
import uuid
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
import jwt

from app.paths import DATA_ROOT
from app import credential_store

ISSUER = "https://auth.openai.com"
RESOURCE = "https://api.openai.com/v1"
OAUTH = ISSUER + "/api/accounts"
SCOPES = "openid profile email offline_access resource.invoke chatgpt.tokens.use.direct"
PLAN_SCOPES = {"resource.invoke", "chatgpt.tokens.use.direct", "offline_access"}
NAMESPACE = "chatgpt-plan"
ROOT = Path(__file__).resolve().parent.parent
CREDENTIALS = DATA_ROOT / ".chatgpt_credentials.json"
HOST = DATA_ROOT / ".chatgpt_host.json"
_lock = threading.RLock()


class ChatGPTAuthError(RuntimeError):
    """只向页面返回固定说明，避免外部授权响应或令牌进入日志。"""

    def __init__(self, message, code="llm_unconfigured"):
        super().__init__(message)
        self.code = code


@dataclass
class Attempt:
    state: str
    nonce: str
    verifier: str
    redirect_uri: str
    expires: float
    profile_id: str | None
    status: str = "pending"
    message: str = ""


_pending: Attempt | None = None


def _profiles():
    raw = credential_store.load_secret_blob(CREDENTIALS, namespace=NAMESPACE)
    if raw is None:
        if CREDENTIALS.exists():
            raise ChatGPTAuthError("暂时无法读取 ChatGPT 登录凭据，请检查系统凭据库。")
        return {"active": None, "profiles": {}}
    try:
        data = json.loads(raw)
        accounts = data["profiles"]
        if not isinstance(accounts, dict) or any(
            not isinstance(account, dict) or not isinstance(account.get("label"), str)
            for account in accounts.values()
        ) or (data["active"] is not None and data["active"] not in accounts):
            raise ValueError
    except (KeyError, TypeError, ValueError) as exc:
        raise ChatGPTAuthError("无法读取 ChatGPT 登录凭据，请检查系统凭据库。") from exc
    return data


def _save(data):
    credential_store.save_secret_blob(CREDENTIALS, json.dumps(data, ensure_ascii=False), namespace=NAMESPACE)


def _host_id():
    host_id = credential_store._file_data(HOST).get("host_id")
    if not host_id:
        host_id = "urn:uuid:" + str(uuid.uuid4())
        credential_store._atomic_metadata(HOST, {"host_id": host_id})
    return host_id


def _http(method, url, **kwargs):
    with httpx.Client(timeout=8, follow_redirects=False) as client:
        response = client.request(method, url, **kwargs)
        response.raise_for_status()
        return response


def _verified_identity(id_token, client_id, nonce):
    # 固定官方 JWKS，不信任 JWT 自带的密钥 URL 或算法。
    header = jwt.get_unverified_header(id_token)
    keys = _http("GET", ISSUER + "/.well-known/jwks.json").json()["keys"]
    matching = [key for key in keys if header.get("kid") and key.get("kid") == header["kid"]]
    if len(matching) != 1:
        raise ChatGPTAuthError("无法验证 ChatGPT 账号身份。")
    identity = jwt.decode(id_token, jwt.PyJWK.from_dict(matching[0], algorithm="RS256").key,
                          algorithms=["RS256"], audience=client_id, issuer=ISSUER,
                          options={"require": ["exp", "iss", "aud", "sub", "nonce"]})
    audience = identity["aud"]
    if (identity["nonce"] != nonce or not identity["sub"]
            or (isinstance(audience, list) and len(audience) > 1 and identity.get("azp") != client_id)
            or identity.get("azp", client_id) != client_id):
        raise ChatGPTAuthError("无法验证 ChatGPT 账号身份。")
    return identity


def _tokens(data, previous_scopes=""):
    scope = data.get("scope", previous_scopes)
    if not isinstance(scope, str) or not PLAN_SCOPES.issubset(scope.split()):
        raise ChatGPTAuthError("未获得 ChatGPT 套餐使用权限。请确认套餐资格和授权选项。")
    access, refresh, expires = (data.get(name) for name in ("access_token", "refresh_token", "expires_in"))
    if (not isinstance(access, str) or not access or not isinstance(refresh, str) or not refresh
            or type(expires) is not int or expires <= 0 or str(data.get("token_type", "")).lower() != "bearer"):
        raise ChatGPTAuthError("ChatGPT 返回的授权资料不完整，请重新登录。")
    return {"access_token": access, "refresh_token": refresh, "expires": time.time() + expires, "scope": scope}


def status():
    with _lock:
        attempt = _pending
        if attempt and attempt.status in ("pending", "exchanging") and attempt.expires <= time.monotonic():
            attempt.status, attempt.message = "expired", "本次授权已过期，请重新登录。"
        try:
            data = _profiles()
        except ChatGPTAuthError as exc:
            return {"account_id": None, "profiles": [], "status": "failed", "message": str(exc)}
        active = data["active"]
        accounts = [{"id": key, "label": value["label"], "connected": bool(value.get("refresh_token"))}
                    for key, value in data["profiles"].items()]
        return {"account_id": active, "profiles": accounts,
                "status": attempt.status if attempt else "idle", "message": attempt.message if attempt else ""}


def connected(profile_id=None):
    with _lock:
        try:
            data = _profiles()
        except ChatGPTAuthError:
            return False
        return bool(data["profiles"].get(profile_id or data["active"], {}).get("refresh_token"))


class _CallbackServer(HTTPServer):
    allow_reuse_address = False

    def get_request(self):
        connection, address = super().get_request()
        connection.settimeout(3)
        return connection, address


class _Callback(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass  # 回调查询参数含授权码，不能进入访问日志。

    def do_GET(self):
        target = urlsplit(self.path)
        host = "127.0.0.1:" + str(self.server.server_port)
        if (target.path != "/auth/callback" or target.netloc or target.scheme
                or self.headers.get_all("Host") != [host]):
            self.send_error(404)
            return
        try:
            params = parse_qs(target.query, max_num_fields=24)
        except ValueError:
            self.send_error(400)
            return
        accepted = _accept(self.server.attempt, params)
        # 清除地址栏中的一次性授权码，并把结果留给受保护的设置面板读取。
        body = ("<!doctype html><meta charset=utf-8><title>私人电台</title>"
                "<p>可以关闭此页，回到私人电台查看授权结果。</p>"
                "<script>history.replaceState(null,'','/auth/finished')</script>").encode()
        self.send_response(200 if accepted else 400)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'none'; script-src 'unsafe-inline'; frame-ancestors 'none'")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _listen(server, attempt):
    with server:
        server.timeout = 0.5
        while attempt.status == "pending" and attempt.expires > time.monotonic():
            server.handle_request()


def start(profile_id=None):
    global _pending
    with _lock:
        if _pending and _pending.status in ("pending", "exchanging") and _pending.expires > time.monotonic():
            raise ChatGPTAuthError("已有 ChatGPT 登录正在进行，请完成或取消。")
        data = _profiles()
        previous = data["profiles"].get(profile_id)
        if profile_id is not None and previous is None:
            raise ChatGPTAuthError("未找到此 ChatGPT 账号。")
        host_id = _host_id()
        server = _CallbackServer(("127.0.0.1", 0), _Callback)
        attempt = Attempt(secrets.token_urlsafe(32), secrets.token_urlsafe(32), secrets.token_urlsafe(48),
                          f"http://127.0.0.1:{server.server_port}/auth/callback", time.monotonic() + 180, profile_id)
        params = {"client_id": previous["client_id"] if previous else "dynamic_agent_client",
                  "ext_agent_host_id": host_id, "redirect_uri": attempt.redirect_uri, "response_type": "code",
                  "scope": SCOPES, "state": attempt.state, "nonce": attempt.nonce,
                  "resource": RESOURCE, "code_challenge_method": "S256",
                  "code_challenge": base64.urlsafe_b64encode(hashlib.sha256(attempt.verifier.encode()).digest()).rstrip(b"=").decode()}
        if previous is None:
            params["agent_name_hint"] = "Personal-Radio"
        elif previous.get("email"):
            params["login_hint"] = previous["email"]
        _pending = server.attempt = attempt
        threading.Thread(target=_listen, args=(server, attempt), daemon=True).start()
        return {"authorize_url": OAUTH + "/authorize?" + urlencode(params)}


def cancel():
    with _lock:
        if _pending and _pending.status in ("pending", "exchanging"):
            _pending.status, _pending.message = "cancelled", "已取消本次授权。"


def _accept(attempt, params):
    def field(name):
        values = params.get(name, [])
        return values[0] if len(values) == 1 else ""

    with _lock:
        if (_pending is not attempt or attempt.status != "pending" or time.monotonic() >= attempt.expires
                or not secrets.compare_digest(field("state").encode(), attempt.state.encode())):
            return False
        if field("error"):
            attempt.status, attempt.message = "cancelled", "未完成 ChatGPT 授权。"
            return True
        attempt.status = "exchanging"
    try:
        with _lock:
            previous = _profiles()["profiles"].get(attempt.profile_id)
        if attempt.profile_id and previous is None:
            raise ChatGPTAuthError("该账号已移除，请重新登录。")
        issued_id = field("client_id")
        if previous and issued_id and issued_id != previous["client_id"]:
            raise ChatGPTAuthError("返回的 ChatGPT 应用身份与所选账号不一致。")
        client_id = previous["client_id"] if previous else issued_id
        if not re.fullmatch(r"[\w.-]{1,200}", client_id) or client_id == "dynamic_agent_client" or not field("code"):
            raise ChatGPTAuthError("ChatGPT 授权未返回完整资料，请重新登录。")
        grant = _http("POST", OAUTH + "/oauth/token", data={"grant_type": "authorization_code",
                      "client_id": client_id, "code": field("code"), "redirect_uri": attempt.redirect_uri,
                      "code_verifier": attempt.verifier, "resource": RESOURCE}).json()
        identity = _verified_identity(grant["id_token"], client_id, attempt.nonce)
        subject = identity["sub"]
        profile = {**_tokens(grant), "client_id": client_id, "subject": subject, "id_token": grant["id_token"]}
        email = identity.get("email")
        if isinstance(email, str):
            profile["email"] = email
        profile_id = hashlib.sha256(client_id.encode()).hexdigest()[:24]
        profile["label"] = f"{profile.get('email') or 'ChatGPT'} · {profile_id[:5]}"
        with _lock:
            if _pending is not attempt or attempt.status != "exchanging" or time.monotonic() >= attempt.expires:
                return True
            current = _profiles()
            existing = current["profiles"].get(profile_id)
            if existing and existing["subject"] != subject:
                raise ChatGPTAuthError("返回的 ChatGPT 账号与保存的账号不一致。")
            current["profiles"][profile_id] = profile
            current["active"] = profile_id
            _save(current)
            attempt.status, attempt.message = "connected", "ChatGPT 已授权，请选择模型。"
    except Exception as exc:
        with _lock:
            if attempt.status == "exchanging":
                attempt.status = "failed"
                attempt.message = str(exc) if isinstance(exc, ChatGPTAuthError) else "ChatGPT 授权未完成，请重试并检查账号资格。"
    return True


def select(profile_id):
    with _lock:
        data = _profiles()
        if not data["profiles"].get(profile_id, {}).get("refresh_token"):
            raise ChatGPTAuthError("请重新授权此 ChatGPT 账号。")
        cancel()
        data["active"] = profile_id
        _save(data)


def disconnect(profile_id):
    with _lock:
        cancel()
        data = _profiles()
        profile = data["profiles"].get(profile_id)
        if not profile:
            raise ChatGPTAuthError("未找到此 ChatGPT 账号。")
        refresh = profile.get("refresh_token")
        revoked = True
        if refresh:
            for wait in (0, 0.25):
                if wait:
                    time.sleep(wait)
                try:
                    _http("POST", OAUTH + "/oauth/revoke", data={"token": refresh, "token_type_hint": "refresh_token",
                                                               "client_id": profile["client_id"]})
                    revoked = True
                    break
                except httpx.HTTPStatusError as exc:
                    revoked = False
                    if exc.response.status_code < 500:
                        break
                except httpx.RequestError:
                    revoked = False
        for key in ("access_token", "refresh_token", "id_token", "expires", "scope"):
            profile.pop(key, None)
        if data["active"] == profile_id:
            data["active"] = None
        _save(data)
        return {"revoked": revoked}


def access_token(profile_id=None):
    # 同一应用进程串行刷新旋转令牌，整组凭据更新成功后才继续调用。
    with _lock:
        data = _profiles()
        profile = data["profiles"].get(profile_id or data["active"], {})
        if not profile.get("refresh_token"):
            raise ChatGPTAuthError("请先在 AI 设置中连接 ChatGPT。")
        if profile["expires"] <= time.time() + 60:
            try:
                grant = _http("POST", OAUTH + "/oauth/token", data={"grant_type": "refresh_token", "resource": RESOURCE,
                              "client_id": profile["client_id"], "refresh_token": profile["refresh_token"]}).json()
                profile.update(_tokens(grant, profile["scope"]))
                _save(data)
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code in (400, 401, 403):
                    raise ChatGPTAuthError("ChatGPT 授权已失效，请重新登录。") from None
                raise
        return profile["access_token"]


def models(profile_id=None):
    result = _http("GET", RESOURCE + "/models", headers={"Authorization": "Bearer " + access_token(profile_id)}).json()
    return [{"id": item["slug"], "label": item.get("display_name") or item["slug"]}
            for item in result.get("models", []) if item.get("visibility") == "list" and item.get("slug")]
