"""播放地址、可见封面与当前歌词HTTP适配，不保存音乐文件。"""
from functools import lru_cache
from fastapi import APIRouter, Response
from app.diagnostics import stage
from app.netease import credentials, gateway, lyrics

router = APIRouter(prefix="/api/ncm")

@router.get("/playurl/{song_id}")
def ncm_playurl(song_id: str):
    """使用当前账号获取原曲的完整播放地址。

    未登录 → 提示先登录（无 cookie 时绝大多数歌只能拿 30 秒试听，
    且会员歌完全拿不到，故明确引导登录）；
    已登录但拿不到 url（VIP/版权受限）→ 友好提示并给网易云跳转链接。
    """
    gateway.ensure_gateway()
    cred = credentials._load_credential()
    if not cred:
        return {
            "success": False,
            "need_login": True,
            "message": "请先扫码登录网易云账号，才能在线试听",
        }
    headers = {"Cookie": cred["cookie"]}
    try:
        body = gateway._gw("/song/url/v1", params={"id": song_id, "level": "standard"}, headers=headers)
    except gateway.PlaybackV1Unavailable:
        # 同一账号、同一首歌只做一次标准码率兼容取址，避免让播放器重发相同的新版请求。
        with stage("gateway_legacy_playback"):
            body = gateway._gw("/song/url", params={"id": song_id, "br": 128000}, headers=headers, timeout=5)
    if not isinstance(body, dict) or body.get("code") != 200:
        return {"success": False, "message": "获取播放地址失败，请稍后再试"}
    url = None
    rows = body.get("data")
    if isinstance(rows, list):
        for row in rows:
            if not isinstance(row, dict) or str(row.get("id")) != song_id:
                continue
            address = row.get("url")
            # 两条取址路径都只接受原曲完整音频，试听或单曲权限错误不能作为成功。
            if (row.get("code", 200) == 200 and row.get("freeTrialInfo") is None
                    and isinstance(address, str) and address.startswith(("http://", "https://"))):
                url = address
            break
    if not url:
        # 跳转链接：网易云网页版单曲页
        return {
            "success": False,
            "need_vip": True,
            "jump_url": f"https://music.163.com/#/song?id={song_id}",
            "message": "这首歌需要 VIP 或受版权保护，暂时无法在线试听，去网易云客户端听听吧",
        }
    # 网易云网关偶尔返回 http 播放地址。页面在 localhost 等安全上下文中
    # 加载远程 HTTP 音频会被 Chromium 当作混合内容拦截；同一 CDN 已验证支持 HTTPS，
    # 因此在后端统一升级协议，避免前端各处重复处理。
    if url.startswith("http://"):
        url = "https://" + url[len("http://"):]
    return {"success": True, "url": url}


@router.get("/song-cover/{song_id}")
@lru_cache(maxsize=2048)
def ncm_song_cover(song_id: str):
    """返回单曲的真实专辑封面地址。

    推荐接口只携带本地数据库中的事实字段，不为 UI 额外请求远程数据；封面由
    前端可见时按需加载，并在进程内缓存，避免反复打网易云详情接口。
    """
    body = gateway._gw("/song/detail", params={"ids": song_id})
    songs = body.get("songs") or []
    cover_url = ((songs[0].get("al") or {}).get("picUrl") if songs else None)
    if not cover_url:
        return {"success": False, "cover_url": None}
    if cover_url.startswith("http://"):
        cover_url = "https://" + cover_url[len("http://"):]
    separator = "&" if "?" in cover_url else "?"
    return {"success": True, "cover_url": f"{cover_url}{separator}param=300y300"}


@router.get("/lyrics/{song_id}")
def ncm_lyrics(song_id: str, response: Response):
    """播放时按需返回同步歌词，不保存 LRC 原文或解析结果。"""
    response.headers["Cache-Control"] = "no-store"
    cred = credentials._load_credential()
    headers = {"Cookie": cred["cookie"]} if cred and cred.get("cookie") else None
    body = gateway._gw("/lyric", params={"id": song_id}, headers=headers, timeout=10)
    raw_lyric = ((body.get("lrc") or {}).get("lyric") or "")
    pure_music = bool(body.get("pureMusic")) or "纯音乐，请欣赏" in raw_lyric
    if pure_music:
        return {"success": True, "instrumental": True, "lines": []}
    lines = lyrics._parse_lrc(raw_lyric)
    translated = lyrics._parse_lrc((body.get("tlyric") or {}).get("lyric") or "")
    return {
        "success": bool(lines),
        "instrumental": False,
        "lines": lyrics._merge_lyric_translation(lines, translated),
    }
