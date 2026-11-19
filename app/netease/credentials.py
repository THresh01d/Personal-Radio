"""Cookie清洗与安全凭据读写；保留旧凭据迁移的失败恢复语义。"""
import time
from pathlib import Path
from fastapi import HTTPException
from app.paths import DATA_ROOT
from app import credential_store
from app.netease import gateway

CREDENTIAL_FILE = DATA_ROOT / ".netease_credential.json"

_COOKIE_ATTR_TOKENS = {
    "max-age", "expires", "path", "domain", "secure",
    "httponly", "samesite", "priority", "partitioned",
}
def _clean_cookie(raw):
    """压缩网关返回的原始 cookie 串，返回干净可用的 Cookie 头值。

    为什么需要：/login/qr/check 等接口返回的 cookie 是多次 Set-Cookie 的原始
    拼接，同一键（MUSIC_R_T / MUSIC_A_T）重复出现，且排前面的往往是登录前的
    匿名值。整串原样发给网关时，解析器取第一个值 → 误判未登录 → 登录态显示
    不出来，播放/同步也被当匿名（30 秒试听的深层原因之一）。
    这里做两件事：
    1. 剥掉 Max-Age / Expires / Path 等属性行（不是 cookie）；
    2. 同名去重、取最后一次的值（真正的登录值在登录完成后才写入，位置靠后）。
    """
    if not raw:
        return raw
    cleaned = {}
    for part in raw.split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        name, _, value = part.partition("=")
        name = name.strip()
        value = value.strip()
        if not name or name.lower() in _COOKIE_ATTR_TOKENS:
            continue  # 属性行丢弃
        if not value:
            continue  # 空值 cookie 不影响登录态，丢弃保持请求头干净
        cleaned[name.lower()] = f"{name}={value}"  # 同名后者覆盖前者 = 取最后一次
    return "; ".join(cleaned.values())


def _load_credential():
    """读取系统凭据并安全兼容旧文件；迁移失败仍保留原登录。

    返回前对 cookie 做清洗：老版本可能存过未去重的原始拼接，清洗后自愈，
    用户无需重新扫码登录。
    """
    data = credential_store.load(CREDENTIAL_FILE)
    if data:
        data["cookie"] = _clean_cookie(data["cookie"])
    return data


def _save_credential(cookie, nickname="", uid=0):
    """Cookie 分段进入安全系统凭据库，文件只保存非密钥清单，不新写明文。

    入库前先清洗去重，保证之后所有请求（status/playurl/sync）用的都是
    干净、网关认可的 cookie，而不是含匿名旧值的原始拼接。
    """
    data = {
        "cookie": _clean_cookie(cookie),
        "nickname": nickname,
        "uid": uid,
        "saved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    try:
        credential_store.save(CREDENTIAL_FILE, data)
    except credential_store.CredentialUnavailable as exc:
        raise HTTPException(503, "安全凭据库暂不可用，原登录仍保留；请稍后重试") from exc


def _fetch_profile(cookie):
    """读取已登录账号资料；网关失败时仍允许扫码确认和旧凭据恢复流程处理。"""
    try:
        body = gateway._gw("/login/status", headers={"Cookie": cookie})
        profile = (body.get("data") or {}).get("profile") or {}
        return profile if isinstance(profile, dict) else {}
    except Exception:
        return {}


def clear_credential():
    # 路径只在凭据模块维护，退出登录与迁移读取始终操作同一份清单。
    credential_store.clear(CREDENTIAL_FILE)
