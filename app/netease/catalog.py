"""远端歌单目录、分页与真实元数据归一化；不写本地曲库。"""
from fastapi import APIRouter
from app.embedding_store import metadata_text
from app.netease import credentials, gateway

router = APIRouter(prefix="/api/ncm")

def _sync_context():
    """返回同步所需的 (凭据, uid, headers)，失败时返回中文错误。

    老版本凭据可能没有 uid；这里现场补齐并回写，用户不需要因为数据库升级
    再扫一次二维码。
    """
    gateway.ensure_gateway()
    cred = credentials._load_credential()
    if not cred:
        return None, "请先扫码登录网易云账号，再同步歌单"
    uid = cred.get("uid")
    if not uid:
        profile = credentials._fetch_profile(cred["cookie"])
        uid = profile.get("userId")
        if not uid:
            return None, "登录信息不完整，请重新扫码登录"
        credentials._save_credential(cred["cookie"], profile.get("nickname", ""), uid)
    return (cred, uid, {"Cookie": cred["cookie"]}), None


def _fetch_user_playlists(uid, headers):
    """分页读取当前账号的全部歌单，并按 ID 去重。"""
    limit = 200
    offset = 0
    by_id = {}
    for _ in range(50):  # 防御网关异常地一直返回同一页
        body = gateway._gw(
            "/user/playlist",
            params={"uid": uid, "limit": limit, "offset": offset},
            headers=headers,
            timeout=30,
        )
        batch = body.get("playlist") or []
        before = len(by_id)
        for playlist in batch:
            pid = str(playlist.get("id") or "")
            if pid:
                by_id[pid] = playlist
        if len(batch) < limit or len(by_id) == before:
            break
        offset += limit
    return list(by_id.values())


def _playlist_summary(playlist, owner_uid=None):
    creator = playlist.get("creator") or {}
    creator_id = str(creator.get("userId") or playlist.get("userId") or "")
    subscribed = bool(playlist.get("subscribed"))
    if owner_uid is not None and creator_id:
        owned_by_user = creator_id == str(owner_uid)
    else:
        # 老网关响应可能不带 creator；此时 subscribed 是更可靠的归属信号。
        owned_by_user = not subscribed
    return {
        "id": str(playlist.get("id") or ""),
        "name": (playlist.get("name") or "未命名歌单").strip(),
        "track_count": int(playlist.get("trackCount") or 0),
        # 收藏来的他人“喜欢的音乐”也可能带 specialType=5；只有账号自己
        # 未标记 subscribed 的那一张才是默认收藏，避免 liked 同步误选两张。
        "is_liked": bool(
            not subscribed and (
                playlist.get("specialType") == 5
                or playlist.get("name") == "我喜欢的音乐"
            )
        ),
        "creator_id": creator_id or None,
        "owned_by_user": owned_by_user,
        "subscribed": subscribed,
        "cover_url": playlist.get("coverImgUrl") or None,
        "remote_updated_at": int(
            playlist.get("trackUpdateTime") or playlist.get("updateTime") or 0
        ),
    }


@router.get("/playlists")
def ncm_playlists():
    """列出登录用户自己的歌单，供首次启动向导选择同步范围。"""
    context, error = _sync_context()
    if error:
        return {"ok": False, "error": error, "playlists": []}
    _, uid, headers = context
    playlists = [_playlist_summary(p, owner_uid=uid) for p in _fetch_user_playlists(uid, headers)]
    return {"ok": True, "playlists": playlists}


def _fetch_playlist_tracks(playlist_id, headers):
    """分页拉取一个歌单的全量曲目。"""
    tracks = []
    offset = 0
    while True:
        page = gateway._gw(
            "/playlist/track/all",
            params={"id": playlist_id, "limit": 1000, "offset": offset},
            headers=headers,
            timeout=60,
        )
        batch = page.get("songs") or []
        if not batch:
            break
        tracks.extend(batch)
        if len(batch) < 1000:
            break
        offset += 1000
    return tracks


def _select_playlists(playlists, req):
    """把同步请求解析成真实属于当前账号的歌单，拒绝任意外部 playlist id。"""
    if req.scope == "all":
        return playlists
    if req.scope == "liked":
        liked = [p for p in playlists if _playlist_summary(p)["is_liked"]]
        return liked[:1]
    requested = {str(pid) for pid in (req.playlist_ids or []) if str(pid)}
    return [p for p in playlists if str(p.get("id")) in requested]


def _normalise_track(track):
    """只提取可信元数据；返回 None 表示远端记录缺少歌名，不能入库。"""
    sid = str(track.get("id") or "")
    title = (track.get("name") or "").strip()
    if not sid or not title:
        return None
    artist = " / ".join(
        (item.get("name") or "").strip()
        for item in (track.get("ar") or [])
        if (item.get("name") or "").strip()
    )
    album = ((track.get("al") or {}).get("name") or "").strip()
    duration = int((track.get("dt") or 0) // 1000)
    search_text = metadata_text({"title": title, "artist": artist, "album": album})
    return {
        "song_id": sid, "title": title, "artist": artist, "album": album,
        "duration_seconds": duration, "search_text": search_text,
    }
