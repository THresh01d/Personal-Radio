"""扫码登录、状态与退出HTTP适配。"""
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Query
from app import credential_store
from app.netease import credentials, gateway
from app.netease.schemas import LoginSaveRequest

router = APIRouter(prefix="/api/ncm")


def _avatar_url(value):
    # 老资料可能返回 HTTP。只对网易云图片域名升级为 HTTPS，再交给界面加载。
    if not isinstance(value, str) or not value or any(char.isspace() or char == "\\" for char in value):
        return None
    try:
        url = urlsplit(value)
        host = url.hostname or ""
        if url.scheme not in {"http", "https"} or url.netloc.lower() != host:
            return None
        if not host.endswith((".music.126.net", ".music.163.com")):
            return None
    except ValueError:
        return None
    return url._replace(scheme="https", fragment="").geturl()


@router.get("/login/qr")
def ncm_login_qr():
    """第一步：向网关要 unikey 并生成二维码。

    前端拿到 qrimg（base64 data URL）直接 <img> 展示即可，
    同时记下 key 用于轮询 /login/check。
    """
    body = gateway._gw("/login/qr/key")
    if body.get("code") != 200 or not body.get("data", {}).get("unikey"):
        raise HTTPException(502, "获取登录二维码失败，请稍后再试")
    key = body["data"]["unikey"]
    qr = gateway._gw("/login/qr/create", params={"key": key, "qrimg": "true"})
    data = (qr.get("data") or {})
    return {"key": key, "qr_url": data.get("qrurl", ""), "qrimg": data.get("qrimg", "")}


@router.get("/login/check")
def ncm_login_check(key: str = Query(..., description="扫码 key")):
    """第二步：前端轮询扫码状态。

    网关状态码：801=等待扫码，802=已扫待确认，803=确认成功，800=二维码过期。
    803 时网关在响应体里回传登录 cookie，这里保存凭据并返回可展示的账号资料。
    """
    body = gateway._gw("/login/qr/check", params={"key": key})
    code = body.get("code")
    if code == 803:
        cookie = body.get("cookie", "")
        if not cookie:  # 理论上不会发生；保险起见让前端继续轮询
            return {"status": "waiting"}
        profile = credentials._fetch_profile(cookie)
        nickname = profile.get("nickname", "")
        credentials._save_credential(cookie, nickname, profile.get("userId", 0))
        return {"status": "confirmed", "nickname": nickname, "avatar_url": _avatar_url(profile.get("avatarUrl"))}
    if code == 802:
        return {"status": "scanned"}
    if code == 801:
        return {"status": "waiting"}
    # 800（过期）或其他异常一律视为过期
    return {"status": "expired"}


@router.post("/login/save")
def ncm_login_save(req: LoginSaveRequest):
    """前端把完整 cookie 回传保存。校验非空，避免写入空凭据。"""
    if not req.cookie:
        raise HTTPException(400, "cookie 不能为空")
    credentials._save_credential(req.cookie, req.nickname, req.uid)
    return {"ok": True}


@router.get("/status")
def ncm_status():
    """登录状态：有凭据且仍有效则返回昵称、uid 和头像，否则未登录。

    前端每次加载页面时调用，决定显示「扫码登录」还是用户昵称。
    """
    cred = credentials._load_credential()
    if not cred:
        # 空白安装打开电台时，只读本机状态。扫码时再准备网易云网关。
        return {"logged_in": False, "nickname": None, "uid": None}
    gateway.ensure_gateway()
    profile = credentials._fetch_profile(cred["cookie"])
    if profile:
        return {
            "logged_in": True,
            "nickname": profile.get("nickname") or cred.get("nickname"),
            "uid": profile.get("userId"),
            "avatar_url": _avatar_url(profile.get("avatarUrl")),
            "credential_storage": cred.get("storage"),
            "storage_warning": "旧登录凭据尚未安全迁移，系统凭据库恢复后将自动重试" if cred.get("storage") == "legacy-file" else None,
        }
    return {"logged_in": False, "nickname": None, "uid": None}


@router.post("/logout")
def ncm_logout():
    """退出登录：尽力在网关侧失效 cookie，再删除本地凭据文件。幂等可重入。

    先调网关 /logout 让网易云侧登录态失效（best-effort，失败不阻塞），
    再删本地 .netease_credential.json，前端随即回到未登录态。
    """
    cred = credentials._load_credential()
    if cred and cred.get("cookie"):
        try:
            gateway._gw("/logout", method="POST", headers={"Cookie": cred["cookie"]})
        except Exception:
            pass  # 网关注销失败不影响本地清除
    try:
        credentials.clear_credential()
    except credential_store.CredentialUnavailable as exc:
        raise HTTPException(503, "平台登录已退出，但本机凭据清理未完成，请重试") from exc
    return {"ok": True}
