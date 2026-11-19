"""在线目录适配：只归一化本机页面需要的字段，曲库写入仍由原同步入口处理。"""

import re
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Response

from app import qqmusic
from app.netease import credentials, gateway
from app.netease.auth import _avatar_url as netease_image

router = APIRouter(prefix="/api/online")
Platform = Literal["netease", "qq"]
SearchKind = Literal["song", "artist", "album"]
TrackKind = Literal["playlist", "artist", "album"]
PAGE_SIZE = 20
NETEASE_ID = re.compile(r"[1-9][0-9]{0,15}")
QQ_ID = re.compile(r"[A-Za-z0-9]{1,40}")


def obj(value):
    return value if isinstance(value, dict) else {}


def text(*values):
    return next((value.strip()[:250] for value in values if isinstance(value, str) and value.strip()), "")


def identifier(value, pattern):
    candidate = str(value) if type(value) in (str, int) else ""
    return candidate if pattern.fullmatch(candidate) else ""


def names(value):
    if not isinstance(value, list):
        return ""
    return " / ".join(filter(None, (text(obj(item).get("name"), obj(item).get("title")) for item in value)))


def netease_request(route, *, login=False, **params):
    credential = credentials._load_credential()
    cookie = (credential or {}).get("cookie")
    if login and not cookie:
        raise HTTPException(409, "登录网易云后即可查看首页推荐歌单")
    headers = {"Cookie": cookie} if cookie else {}
    if login:
        # 首页刷新应请求当前账号的新数据，避免被网关的两分钟缓存拦下。
        headers["X-Apicache-Bypass"] = "1"
    result = obj(gateway._gw(route, params=params, headers=headers or None, timeout=8))
    if result.get("code") in (301, 401):
        raise HTTPException(409, "网易云登录已失效，请重新扫码")
    if result.get("code") != 200:
        raise HTTPException(502, "网易云目录暂时不可用，请稍后重试")
    return result


def rows(value):
    if not isinstance(value, list):
        raise HTTPException(502, "音乐平台返回的数据暂时无法读取")
    return value


def netease_track(row):
    row = obj(row)
    sid = identifier(row.get("id"), NETEASE_ID)
    title = text(row.get("name"))
    if not sid or not title:
        return None
    album = obj(row.get("al") or row.get("album"))
    ms = row.get("dt", row.get("duration"))
    return {"id": sid, "title": title, "artist": names(row.get("ar") or row.get("artists")) or "未知歌手",
            "album": text(album.get("name")), "cover_url": netease_image(album.get("picUrl")),
            "duration_seconds": ms // 1000 if type(ms) is int and 0 < ms <= 2**31 - 1 else None,
            "platform": "netease", "online_catalog": True}


def qq_track(row):
    song = qqmusic._song(row)
    return {**song, "online_catalog": True} if song else None


def collection(row, platform, kind):
    row = obj(row)
    if platform == "netease":
        item_id = identifier(row.get("id"), NETEASE_ID)
        title = text(row.get("name"))
        artist = names(row.get("artists")) or text(obj(row.get("artist")).get("name")) if kind == "album" else ""
        cover = netease_image(row.get("picUrl") or row.get("img1v1Url") or row.get("coverImgUrl"))
    else:
        field = "singer" if kind == "artist" else "album"
        item_id = identifier(row.get("mid") or row.get(f"{field}MID") or row.get(f"{field}Mid") or row.get(f"{field}mid"), QQ_ID)
        title = text(row.get("title"), row.get("name"), row.get(f"{field}Name"), row.get(f"{field}name"))
        artist = names(row.get("singer")) or text(row.get("singerName"), row.get("singername")) if kind == "album" else ""
        prefix = "T001" if kind == "artist" else "T002"
        cover = qqmusic._image_url(row.get("pic")) or (
            f"https://y.gtimg.cn/music/photo_new/{prefix}R300x300M000{item_id}.jpg" if item_id else None
        )
    if item_id and title:
        return {"id": item_id, "title": title, "artist": artist, "cover_url": cover, "platform": platform, "kind": kind}


def unique_items(source, normalise):
    by_id = {}
    for row in source:
        if item := normalise(row):
            by_id.setdefault(item["id"], item)
    return list(by_id.values())


def homepage_playlists(blocks):
    for raw in blocks:
        block = obj(raw)
        if not text(block.get("blockCode")).startswith("HOMEPAGE_BLOCK_PLAYLIST_RCMD"):
            continue
        for raw_creative in rows(block.get("creatives") or []):
            creative = obj(raw_creative)
            for raw_resource in rows(creative.get("resources") or []):
                resource = obj(raw_resource)
                if resource.get("valid") is False or not (
                    resource.get("resourceType") in ("list", "playlist")
                    or text(resource.get("action")).startswith(("orpheus://playlist?", "orpheus://playlist/"))
                ):
                    continue
                ui = obj(resource.get("uiElement"))
                yield {"id": resource.get("resourceId"), "name": obj(ui.get("mainTitle")).get("title"),
                       "picUrl": obj(ui.get("image")).get("imageUrl")}


@router.get("/netease/recommendations")
def recommendations(response: Response):
    response.headers["Cache-Control"] = "private, no-store"
    # 只读网易云首页的推荐歌单卡片，避免把每日推荐、广告或歌曲卡片标成同一来源。
    result = netease_request("/homepage/block/page", login=True, refresh="true")
    blocks = rows(obj(result.get("data")).get("blocks"))
    playlists = unique_items(homepage_playlists(blocks), lambda row: collection(row, "netease", "playlist"))
    return {"playlists": playlists[:40]}


def search_netease(query, kind, page):
    section, key, count_key = {"song": (1, "songs", "songCount"), "artist": (100, "artists", "artistCount"),
                               "album": (10, "albums", "albumCount")}[kind]
    body = netease_request("/cloudsearch", keywords=query, type=section, limit=PAGE_SIZE, offset=(page - 1) * PAGE_SIZE)
    result = obj(body.get("result"))
    total = result.get(count_key)
    candidates = rows(result.get(key, [] if total in (0, None) else None))[:PAGE_SIZE]
    return candidates, len(candidates) == PAGE_SIZE and (not isinstance(total, int) or total > page * PAGE_SIZE)


@router.get("/{platform}/search")
def search(response: Response, platform: Platform, q: str = Query(min_length=1, max_length=60),
           kind: SearchKind = "song", page: int = Query(1, ge=1, le=50)):
    response.headers["Cache-Control"] = "private, no-store"
    q = q.strip()
    if not q:
        raise HTTPException(422, "请输入搜索关键词")
    if platform == "netease":
        candidates, more = search_netease(q, kind, page)
    else:
        result = qqmusic.catalog_request("/search", keyword=q, type="singer" if kind == "artist" else kind,
                                        page=page, limit=PAGE_SIZE)
        candidates, more = rows(result.get("items"))[:PAGE_SIZE], result.get("more") is True
    if kind == "song":
        normalise = netease_track if platform == "netease" else qq_track
        items = [{**song, "kind": "song"} for song in unique_items(candidates, normalise)]
    else:
        items = unique_items(candidates, lambda row: collection(row, platform, kind))
    return {"items": items, "has_more": bool(candidates) and more and page < 50}


def collection_tracks(platform, kind, item_id, page):
    offset = (page - 1) * PAGE_SIZE
    if platform == "netease":
        if kind == "album":
            songs = rows(netease_request("/album", id=item_id).get("songs"))
            return songs[offset:offset + PAGE_SIZE], len(songs) > offset + PAGE_SIZE
        route = "/artist/songs" if kind == "artist" else "/playlist/track/all"
        result = netease_request(route, id=item_id, limit=PAGE_SIZE, offset=offset)
        songs = rows(result.get("songs"))[:PAGE_SIZE]
        return songs, result.get("more", len(songs) == PAGE_SIZE) is True
    if kind == "artist":
        result = qqmusic.catalog_request("/singer/songs", mid=item_id, limit=PAGE_SIZE, offset=offset)
        songs = rows(result.get("songs"))[:PAGE_SIZE]
        return songs, result.get("more") is True
    result = obj(qqmusic.catalog_request("/getAlbumInfo", albummid=item_id).get("response"))
    if result.get("code") != 0:
        raise HTTPException(502, "QQ 音乐暂时无法读取这张专辑")
    songs = rows(obj(result.get("data")).get("list"))
    return songs[offset:offset + PAGE_SIZE], len(songs) > offset + PAGE_SIZE


@router.get("/{platform}/{kind}/{item_id}/tracks")
def tracks(response: Response, platform: Platform, kind: TrackKind, item_id: str,
           page: int = Query(1, ge=1, le=500)):
    response.headers["Cache-Control"] = "private, no-store"
    pattern = NETEASE_ID if platform == "netease" else QQ_ID
    if not identifier(item_id, pattern) or (platform == "qq" and kind == "playlist"):
        raise HTTPException(404, "找不到这个在线目录")
    candidates, more = collection_tracks(platform, kind, item_id, page)
    songs = unique_items(candidates, netease_track if platform == "netease" else qq_track)
    return {"songs": songs, "has_more": bool(candidates) and more and page < 500}
