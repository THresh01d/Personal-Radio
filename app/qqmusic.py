"""QQ 音乐本机扫码、只读歌单和授权播放。平台凭据不进入网页。"""

import json
import re
import secrets
import time
from http.cookies import SimpleCookie
from threading import RLock
from urllib.parse import urlsplit, urlunsplit

import httpx
from fastapi import APIRouter, HTTPException, Query, Response

from app import credential_store
from app.netease import gateway
from app.paths import DATA_ROOT

router = APIRouter(prefix="/api/qq")
_state_file = DATA_ROOT / ".qqmusic" / "keyring.json"
_namespace = "qqmusic"
_lock = RLock()
_pending = None
_http = httpx.Client(timeout=20, trust_env=False)
_mid = re.compile(r"[A-Za-z0-9]{1,40}")
_id = re.compile(r"[1-9][0-9]{0,15}")
_qr_id = re.compile(r"[A-Za-z0-9_-]{24}")


def _object(value):
    return value if isinstance(value, dict) else {}


def _items(value):
    return value if isinstance(value, list) else []


def _identifier(value, pattern):
    return str(value) if isinstance(value, (int, str)) and pattern.fullmatch(str(value)) else ""


def _text(*values):
    return next((value.strip()[:250] for value in values if isinstance(value, str) and value.strip()), "")


def _count(value):
    return value if type(value) is int and 0 <= value <= 2**31 - 1 else None


def _available():
    return (gateway.GATEWAY_DIR / "node_modules/@yakult-green-tea/qq-music-api/package.json").is_file()


def _save(state):
    try:
        credential_store.save_secret_blob(_state_file, json.dumps(state), namespace=_namespace)
    except credential_store.CredentialUnavailable as exc:
        raise HTTPException(503, "本机安全凭据库暂不可用，请稍后重试") from exc


def _load(*, create=False):
    blob = credential_store.load_secret_blob(_state_file, namespace=_namespace)
    if blob:
        try:
            state = _object(json.loads(blob))
            if re.fullmatch(r"[A-Za-z0-9_-]{43}", _text(state.get("key"))):
                return state
        except ValueError:
            pass
    # 不能用新的密钥覆盖仍然存在、暂时无法解锁的旧会话。
    if _state_file.exists():
        raise HTTPException(503, "暂时无法读取 QQ 音乐安全凭据，请稍后重试")
    if create:
        state = {"key": secrets.token_urlsafe(32)}
        _save(state)
        return state
    return {}


def _request(route, state, *, request_timeout=None, **params):
    if not _available():
        raise HTTPException(503, "此版本的 QQ 音乐组件未安装")
    gateway.ensure_gateway()
    headers = {"X-Personal-Radio-Gateway": gateway._gateway_token, "X-Radio-QQ-Key": state["key"]}
    if state.get("token"):
        headers["Cookie"] = f"qqmusic_session={state['token']}"
    fetching_qr = route in ("/login/qr/key", "/login/qr/create")
    try:
        reply = _http.get(f"{gateway.GATEWAY_URL}/__qq{route}", params=params, headers=headers,
                          timeout=request_timeout if request_timeout is not None else 35 if fetching_qr else 20)
        if fetching_qr and reply.is_error:
            retry = reply.headers.get("Retry-After", "")
            seconds = int(retry) if re.fullmatch(r"[1-9][0-9]?", retry) else 0
            retry_headers = {"Retry-After": str(seconds)} if 1 <= seconds <= 60 else None
            if reply.status_code == 409:
                raise HTTPException(409, "上一张二维码正在确认，请稍后刷新")
            if reply.status_code == 429:
                wait = f"请等 {seconds} 秒再试" if retry_headers else "请稍后再试"
                raise HTTPException(429, f"QQ 音乐暂时限制了取码，{wait}", headers=retry_headers)
            step = "开始扫码登录" if route.endswith("/key") else "生成二维码"
            raise HTTPException(502, f"QQ 音乐暂时无法{step}，请稍后刷新", headers=retry_headers)
        if reply.status_code in (401, 403):
            raise HTTPException(409, "QQ 音乐登录已失效，请重新扫码")
        if reply.status_code == 429:
            raise HTTPException(429, "QQ 音乐请求过于频繁，请稍后再试")
        reply.raise_for_status()
        result = reply.json()
        if not isinstance(result, dict):
            raise ValueError("unexpected response shape")
        return result
    except httpx.TimeoutException as exc:
        if fetching_qr:
            raise HTTPException(504, "QQ 音乐二维码请求超时，请刷新重试") from exc
        raise HTTPException(502, "QQ 音乐暂时无法连接，请稍后重试") from exc
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(502, "QQ 音乐暂时无法连接，请稍后重试") from exc


def _logged_in_state():
    state = _load()
    if not state.get("token"):
        raise HTTPException(409, "请先登录 QQ 音乐")
    return state


def catalog_request(route, **params):
    # 匿名目录也使用同一个本机网关密钥，后续扫码可以继续沿用该会话组件。
    with _lock:
        state = _load(create=True)
    return _request(route, state, request_timeout=8, **params)


def _trusted_url(value, domains):
    if not isinstance(value, str) or not value or any(char.isspace() or char == "\\" for char in value):
        return None
    try:
        parts = urlsplit("https:" + value if value.startswith("//") else value)
        host = parts.hostname or ""
        if (parts.scheme not in ("http", "https") or parts.username or parts.password
                or parts.port is not None or parts.fragment or not parts.path.startswith("/")
                or not any(host == domain or host.endswith("." + domain) for domain in domains)):
            return None
        # 音源地址带临时签名，只通过 HTTPS 发往音乐 CDN。
        return urlunsplit(("https", host, parts.path, parts.query, ""))
    except ValueError:
        return None


def _image_url(value):
    return _trusted_url(value, ("gtimg.cn", "gtimg.com", "y.qq.com", "qlogo.cn"))


def _profile(data):
    user = _object(data.get("info"))
    return {"available": True, "logged_in": True,
            "nickname": _text(data.get("nickname"), data.get("nick"), user.get("nick"), user.get("nickname")) or "QQ 音乐用户",
            "avatar_url": _image_url(user.get("logo") or data.get("avatarUrl"))}


def _cancel_pending():
    global _pending
    previous, _pending = _pending, None
    if previous:
        try:
            _request("/login/qr/cancel", _load(), key=previous[1])
        except HTTPException:
            pass  # 本机已作废；上游扫码窗口依有效期结束。


@router.get("/status")
def status(response: Response):
    response.headers["Cache-Control"] = "no-store"
    if not _available():
        return {"available": False, "logged_in": False}
    with _lock:
        try:
            state = _load()
            if state.get("token"):
                raw = _request("/login/status", state)
                data = _object(_object(raw.get("data")).get("profile"))
                if data:
                    return _profile(data)
        except HTTPException as exc:
            if exc.status_code != 409:
                return {"available": True, "logged_in": False, "storage_warning": exc.detail}
        return {"available": True, "logged_in": False}


@router.post("/login/qr")
def create_qr(response: Response):
    global _pending
    response.headers["Cache-Control"] = "no-store"
    with _lock:
        if not _available():
            raise HTTPException(503, "此版本的 QQ 音乐组件未安装")
        state = _load(create=True)
        _cancel_pending()
        raw = _request("/login/qr/key", state, channel="qqmusic")
        upstream_key = _object(raw.get("data")).get("unikey")
        if not isinstance(upstream_key, str) or not upstream_key or len(upstream_key) > 300:
            raise HTTPException(502, "QQ 音乐暂时未返回二维码，请稍后重试")
        _pending = (secrets.token_urlsafe(18), upstream_key, time.monotonic() + 300)
        try:
            raw_image = _request("/login/qr/create", state, key=upstream_key)
            image = _object(raw_image.get("data")).get("qrimg")
            if (not isinstance(image, str) or len(image) > 300_000
                    or not re.fullmatch(r"data:image/(?:png|jpeg);base64,[A-Za-z0-9+/=]+", image)):
                raise HTTPException(502, "QQ 音乐未返回可用二维码，请刷新重试")
        except HTTPException:
            _cancel_pending()
            raise
        return {"id": _pending[0], "image": image}


def _matching_qr(qr_id):
    return _pending and secrets.compare_digest(_pending[0], qr_id)


@router.post("/login/qr/{qr_id}/check")
def check_qr(response: Response, qr_id: str):
    global _pending
    response.headers["Cache-Control"] = "no-store"
    if not _qr_id.fullmatch(qr_id):
        return {"state": "expired"}
    with _lock:
        if not _matching_qr(qr_id):
            return {"state": "expired"}
        if time.monotonic() >= _pending[2]:
            _cancel_pending()
            return {"state": "expired"}
        state = _load()
        raw = _request("/login/qr/check", state, key=_pending[1])
        code = raw.get("code")
        if code in (801, 802):
            return {"state": "waiting" if code == 801 else "scanned"}
        if code != 803:
            _cancel_pending()
            return {"state": "expired" if code == 800 else "failed"}
        cookie = SimpleCookie(_text(raw.get("cookie")))
        entry = cookie.get("qqmusic_session")
        if not entry or not re.fullmatch(r"[A-Za-z0-9_.~-]{16,200}", entry.value):
            _cancel_pending()
            raise HTTPException(502, "QQ 音乐未返回有效授权，请刷新二维码")
        updated = {**state, "token": entry.value}
        data = _object(_object(_request("/login/status", updated).get("data")).get("profile"))
        if not data:
            _request("/logout", updated)
            _pending = None
            return {"state": "failed"}
        _save(updated)
        _pending = None
        if state.get("token") and state["token"] != entry.value:
            try:
                _request("/logout", state)
            except HTTPException:
                pass  # 新授权已存好；旧会话按上游有效期回收。
        return {"state": "confirmed", **_profile(data)}


@router.delete("/login/qr/{qr_id}")
def cancel_qr(qr_id: str):
    with _lock:
        if _qr_id.fullmatch(qr_id) and _matching_qr(qr_id):
            _cancel_pending()
    return {"ok": True}


@router.post("/logout")
def logout():
    with _lock:
        _cancel_pending()
        state = _load()
        if state.get("token"):
            # 清理网关中的会话和系统凭据中的票据，设备身份仍可供下一次正常扫码使用。
            _request("/logout", state)
            _save({"key": state["key"]})
    return {"ok": True}


def _playlist(row):
    row = _object(row)
    if str(row.get("dirId") or row.get("dirid")) == "201":
        return None  # 喜欢列表总由单独的只读入口加载，包括其中为空的情况。
    playlist_id = _identifier(row.get("tid") or row.get("dissid") or row.get("id"), _id)
    if not playlist_id or int(playlist_id) > 2**53 - 1:
        return None
    return {"id": playlist_id, "name": _text(row.get("dirName"), row.get("dissname"), row.get("name"), row.get("title")) or "未命名歌单",
            "track_count": _count(row.get("songNum", row.get("songnum", row.get("song_cnt")))),
            "cover_url": _image_url(row.get("picUrl") or row.get("coverUrl") or row.get("logo"))}


@router.get("/playlists")
def playlists(response: Response, offset: int = Query(0, ge=0, le=1_000_000)):
    response.headers["Cache-Control"] = "no-store"
    with _lock:
        raw = _request("/user/playlist", _logged_in_state(), favoriteOffset=offset)
    rows = {item["id"]: item for row in _items(raw.get("playlist")) if (item := _playlist(row))}
    if not offset:
        rows = {"liked": {"id": "liked", "name": "我喜欢", "track_count": None, "cover_url": None}, **rows}
    next_offset = _count(raw.get("nextFavoriteOffset"))
    return {"playlists": list(rows.values()), "more": raw.get("more") is True,
            "next_offset": next_offset if next_offset is not None and offset < next_offset <= 1_000_000 else None}


def _song(row):
    row = _object(row)
    song = _object(row.get("songInfo") or row.get("song_info") or row.get("musicData")) or row
    mid = _identifier(song.get("mid") or song.get("songmid"), _mid)
    title = _text(song.get("title"), song.get("name"), song.get("songname"))
    if not mid or not title:
        return None
    album = _object(song.get("album"))
    album_mid = _identifier(album.get("mid") or song.get("albummid"), _mid)
    artist = " / ".join(filter(None, (_text(_object(item).get("name")) for item in _items(song.get("singer")))))
    return {"id": f"qq:{mid}", "platform": "qq", "title": title, "artist": artist or "未知歌手",
            "album": _text(album.get("name"), album.get("title"), song.get("albumname")),
            "duration_seconds": _count(song.get("interval")),
            "cover_url": f"https://y.gtimg.cn/music/photo_new/T002R300x300M000{album_mid}.jpg" if album_mid else None}


@router.get("/playlists/{playlist_id}/tracks")
def tracks(response: Response, playlist_id: str, offset: int = Query(0, ge=0, le=1_000_000)):
    response.headers["Cache-Control"] = "no-store"
    params = {"offset": offset, "limit": 100}
    if playlist_id == "liked":
        route = "/user/liked-songs"
    elif _id.fullmatch(playlist_id) and int(playlist_id) <= 2**53 - 1:
        route = "/user/playlist-detail"
        params["tid"] = playlist_id
    else:
        raise HTTPException(404, "找不到这张 QQ 音乐歌单")
    with _lock:
        raw = _request(route, _logged_in_state(), **params)
    rows = _items(raw.get("songs"))
    songs = [song for row in rows if (song := _song(row))]
    more = raw.get("more") is True
    next_offset = offset + len(rows) if rows and offset + len(rows) <= 1_000_000 and more else None
    return {"songs": songs, "total": _count(raw.get("total")),
            "next_offset": next_offset, "incomplete": more and next_offset is None}


def _play_address(result, mid, kind):
    entry = _object(_object(_object(result.get("data")).get("playUrl")).get(mid))
    source = _object(entry.get("source"))
    if entry.get("error") or source.get("kind") != kind or bool(entry.get("preview")) != (kind == "preview"):
        return None
    return _trusted_url(entry.get("url"), ("stream.qqmusic.qq.com", "music.tc.qq.com"))


@router.get("/playurl/{mid}")
def playurl(response: Response, mid: str):
    response.headers["Cache-Control"] = "no-store"
    if not _mid.fullmatch(mid):
        raise HTTPException(404, "找不到这首 QQ 音乐歌曲")
    try:
        with _lock:
            state = _logged_in_state()
            result = _request(f"/getMusicPlay/{mid}", state, quality="128")
            url = _play_address(result, mid, "full")
            if url:
                return {"success": True, "url": url, "preview": False}
            attempt = _request(f"/getMusicPlay/{mid}", state, quality="128", preview=1)
    except HTTPException as exc:
        if exc.status_code == 409:
            return {"success": False, "need_login": True, "message": exc.detail}
        raise
    url = _play_address(attempt, mid, "preview")
    if url:
        return {"success": True, "url": url, "preview": True}
    return {"success": False, "message": "QQ 音乐未提供这首歌的播放权限或试听"}
