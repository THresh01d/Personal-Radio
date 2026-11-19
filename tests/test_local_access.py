"""真实 ASGI 访问边界，不关闭生产保护，也不触碰私人配置。"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.local_access import LocalAccessMiddleware


def protected_app():
    app = FastAPI()
    app.add_middleware(LocalAccessMiddleware, token="test-token-for-local-only")

    @app.get("/api/private")
    def private():
        return {"ok": True}

    @app.post("/api/private")
    def mutate():
        return {"ok": True}

    @app.get("/")
    def page():
        return {"page": True}
    return app


def test_bootstrap_cookie_is_http_only_and_protected_endpoints_need_it():
    client = TestClient(protected_app(), base_url="http://127.0.0.1:8000", client=("127.0.0.1", 50000))
    assert client.get("/api/private").status_code == 401
    response = client.get("/api/local-access", headers={"Sec-Fetch-Site": "same-origin"})
    assert response.json() == {"ok": True} and response.headers["cache-control"] == "no-store"
    assert "HttpOnly" in response.headers["set-cookie"] and "SameSite=strict" in response.headers["set-cookie"]
    assert "personal_radio_access_8000" in response.headers["set-cookie"]
    assert client.get("/api/private").status_code == 200
    assert client.post("/api/private", headers={"Origin": "http://127.0.0.1:8000"}).status_code == 200
    assert client.get("/openapi.json").status_code == 200


@pytest.mark.parametrize("headers", [
    {"Host": "evil.example:8000"}, {"Host": "localhost.evil.example"},
    {"Host": "127.0.0.1@evil.example"}, {"Origin": "null"},
    {"Origin": "https://evil.example"}, {"Origin": "http://127.0.0.1:8001"},
    {"Origin": "http://localhost:8000"}, {"Sec-Fetch-Site": "same-site"},
    {"Sec-Fetch-Site": "cross-site"},
])
def test_cross_origin_and_rebinding_are_rejected_even_with_valid_cookie(headers):
    client = TestClient(protected_app(), base_url="http://127.0.0.1:8000", client=("127.0.0.1", 50000))
    client.get("/api/local-access")
    assert client.get("/api/local-access", headers=headers).status_code == 403
    assert client.post("/api/private", headers=headers).status_code == 403


def test_server_restart_invalidates_old_cookie_without_touching_identity():
    client = TestClient(protected_app(), base_url="http://127.0.0.1", client=("127.0.0.1", 50000))
    client.cookies.set("personal_radio_access_80", "old-cookie")
    assert client.get("/api/private").status_code == 401
    assert client.get("/", headers={"Sec-Fetch-Site": "none"}).status_code == 200
    client.get("/api/local-access")
    assert client.get("/api/private").status_code == 200
    headers = client.get("/").headers
    assert headers["content-security-policy"] == "frame-ancestors 'none'"
    assert headers["x-content-type-options"] == "nosniff"


def test_bootstrap_does_not_allow_unsafe_method_without_cookie():
    client = TestClient(protected_app(), base_url="http://127.0.0.1", client=("127.0.0.1", 50000))
    assert client.post("/api/local-access").status_code == 401


def test_remote_peer_cannot_spoof_local_host_or_forwarded_address():
    client = TestClient(protected_app(), base_url="http://127.0.0.1", client=("203.0.113.10", 50000))
    assert client.get("/api/local-access", headers={"X-Forwarded-For": "127.0.0.1"}).status_code == 403


def test_https_default_port_and_duplicate_origin():
    client = TestClient(protected_app(), base_url="https://localhost", client=("::1", 50000))
    response = client.get("/api/local-access", headers={"Origin": "https://localhost:443"})
    assert response.status_code == 200 and "Secure" in response.headers["set-cookie"]
    assert client.get("/api/private").status_code == 200
    assert client.get("/api/private", headers=[("Origin", "https://localhost"), ("Origin", "https://localhost")]).status_code == 403
