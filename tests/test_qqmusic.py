"""本机 QQ 音乐路由回归。使用隔离凭据和合成上游响应。"""

import httpx
import pytest
from fastapi.testclient import TestClient

from app import qqmusic
from app.main import create_app


@pytest.fixture
def radio(monkeypatch, tmp_path):
    monkeypatch.setattr(qqmusic, "_available", lambda: True)
    monkeypatch.setattr(qqmusic, "_pending", None)
    monkeypatch.setattr(qqmusic, "_state_file", tmp_path / ".qqmusic" / "keyring.json")
    # 不进入 lifespan，避免启动平台网关。请求仍经过真实的本机访问保护。
    return TestClient(create_app(), base_url="http://localhost", client=("127.0.0.1", 50000))


def test_qr_requires_local_auth_and_keeps_ticket_out_of_response(radio, monkeypatch):
    calls = []
    sample = "test-ticket-for-qq-session"

    def upstream(path, state, **kwargs):
        calls.append((path, kwargs))
        if path == "/login/qr/key":
            assert kwargs == {"channel": "qqmusic"}
            return {"data": {"unikey": "not-sent-to-renderer"}}
        if path == "/login/qr/create":
            return {"data": {"qrimg": "data:image/png;base64,AAAA"}}
        if path == "/login/qr/check":
            return {"code": 803, "cookie": f"qqmusic_session={sample}"}
        assert state["token"] == sample
        if path == "/login/status":
            return {"data": {"profile": {"info": {"nick": "合成听众", "logo": "//q1.qlogo.cn/avatar.png"}, "musicid": "1234"}}}
        return {"code": 200}

    monkeypatch.setattr(qqmusic, "_request", upstream)
    assert radio.get("/api/qq/status").status_code == 401
    assert radio.get("/api/local-access").status_code == 200
    assert radio.post("/api/qq/login/qr", headers={"Origin": "https://example.com"}).status_code == 403
    qr = radio.post("/api/qq/login/qr")
    assert qr.status_code == 200 and qr.headers["cache-control"] == "no-store"
    assert "not-sent-to-renderer" not in qr.text
    checked = radio.post(f"/api/qq/login/qr/{qr.json()['id']}/check")
    assert checked.json() == {"state": "confirmed", "available": True, "logged_in": True,
                              "nickname": "合成听众", "avatar_url": "https://q1.qlogo.cn/avatar.png"}
    assert sample not in checked.text and sample not in qqmusic._state_file.read_text(encoding="utf-8")
    assert radio.get("/api/qq/status").json()["logged_in"]
    assert radio.post("/api/qq/logout").json() == {"ok": True}
    assert not radio.get("/api/qq/status").json()["logged_in"]
    assert radio.post(f"/api/qq/login/qr/{qr.json()['id']}/check").json() == {"state": "expired"}


def test_account_playlist_paging_and_song_id_namespace(radio, monkeypatch):
    def upstream(path, state, **kwargs):
        assert state["token"] == "synthetic-ticket"
        if path == "/user/playlist":
            assert kwargs == {"favoriteOffset": 0}
            return {"playlist": [{"tid": 123, "dirName": "私人歌单", "songNum": 101},
                                 {"dirId": 201, "tid": 9}], "more": True, "nextFavoriteOffset": 20}
        assert (path, kwargs) in (("/user/playlist-detail", {"offset": 0, "limit": 100, "tid": "123"}),
                                  ("/user/liked-songs", {"offset": 0, "limit": 100}))
        return {"songs": [{"songInfo": {"mid": "123", "name": "合成歌曲", "singer": [{"name": "歌手"}], "interval": 180}},
                          {"mid": "/invalid", "name": "忽略"}], "total": 101, "more": True}

    monkeypatch.setattr(qqmusic, "_load", lambda: {"key": "placeholder", "token": "synthetic-ticket"})
    monkeypatch.setattr(qqmusic, "_request", upstream)
    radio.get("/api/local-access")
    result = radio.get("/api/qq/playlists").json()
    assert [playlist["id"] for playlist in result["playlists"]] == ["liked", "123"]
    assert result["next_offset"] == 20
    for playlist in ("liked", "123"):
        result = radio.get(f"/api/qq/playlists/{playlist}/tracks").json()
        assert result["next_offset"] == 2
        assert result["songs"][0]["id"] == "qq:123"
        assert len(result["songs"]) == 1
    assert radio.get("/api/qq/playlists/injected/tracks").status_code == 404


@pytest.mark.parametrize("status,delay", [(502, "15"), (429, "30")])
def test_qr_errors_preserve_safe_retry_interval(radio, monkeypatch, status, delay):
    def upstream(url, *, params, headers, timeout):
        assert url.endswith("/__qq/login/qr/key")
        assert timeout == 35
        return httpx.Response(status, headers={"Retry-After": delay}, json={"message": "未清洗的上游错误"})

    monkeypatch.setattr(qqmusic.gateway, "ensure_gateway", lambda: None)
    monkeypatch.setattr(qqmusic._http, "get", upstream)
    radio.get("/api/local-access")
    response = radio.post("/api/qq/login/qr")
    assert response.status_code == status
    assert response.headers["Retry-After"] == delay
    assert "未清洗的上游错误" not in response.text


@pytest.mark.parametrize("mode,expected,calls", [("full", False, 1), ("preview", True, 2), ("bad-host", None, 2)])
def test_playback_uses_only_classified_qq_audio(radio, monkeypatch, mode, expected, calls):
    seen = []

    def upstream(path, _state, **kwargs):
        assert path == "/getMusicPlay/abc" and kwargs["quality"] == "128"
        preview = kwargs.get("preview") == 1
        seen.append(preview)
        if (mode == "full" and not preview) or (mode == "preview" and preview):
            entry = {"url": "http://isure.stream.qqmusic.qq.com/track.m4a?signature=demo", "error": False,
                     "preview": preview, "source": {"kind": mode}}
        else:
            entry = {"url": "https://not-stream.qqmusic.qq.com.example.com/a", "error": False,
                     "source": {"kind": "full"}}
        return {"data": {"playUrl": {"abc": entry}}}

    radio.get("/api/local-access")
    assert radio.get("/api/qq/playurl/abc").json()["need_login"] is True
    monkeypatch.setattr(qqmusic, "_load", lambda: {"key": "placeholder", "token": "synthetic-ticket"})
    monkeypatch.setattr(qqmusic, "_request", upstream)
    result = radio.get("/api/qq/playurl/abc").json()
    assert len(seen) == calls
    assert result.get("preview") is expected
    assert result["success"] is (expected is not None)
    if expected is not None:
        assert result["url"].startswith("https://isure.stream.qqmusic.qq.com/")
