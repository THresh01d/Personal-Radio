"""模拟官方授权和 Responses SSE。无真实账号或网络推理。"""

import json
import time
from urllib.parse import parse_qs, urlsplit

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from app import chatgpt_auth as auth, chatgpt_responses as responses, llm_provider, program_author
from app.main import app

_author_program = program_author.author_program

@pytest.fixture
def local_auth(tmp_path, monkeypatch):
    monkeypatch.setattr(auth, "CREDENTIALS", tmp_path / "chatgpt.json")
    monkeypatch.setattr(auth, "HOST", tmp_path / "host.json")
    monkeypatch.setattr(auth, "_pending", None)
    yield
    auth.cancel()


def grant(**changes):
    return {"scope": auth.SCOPES, "access_token": "fixture-access", "refresh_token": "fixture-refresh",
            "token_type": "Bearer", "expires_in": 3600, **changes}


def test_browser_authorization_signature_refresh_and_account_binding(local_auth, monkeypatch, tmp_path):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key(), as_dict=True)
    jwk["kid"] = "fixture"
    calls = []
    context = {}

    def fake(method, url, **kwargs):
        calls.append(url)
        if url.endswith("jwks.json"):
            return httpx.Response(200, json={"keys": [jwk]})
        if url.endswith("/revoke"):
            assert kwargs["data"]["token"] == "rotated-refresh"
            return httpx.Response(200)
        if url.endswith("/models"):
            assert kwargs["headers"]["Authorization"] == "Bearer rotated-access"
            return httpx.Response(200, json={"models": [{"visibility": "list", "slug": "test-model"}]})
        data = kwargs["data"]
        if data["grant_type"] == "refresh_token":
            assert data["refresh_token"] == "fixture-refresh"
            return httpx.Response(200, json=grant(access_token="rotated-access", refresh_token="rotated-refresh"))
        assert data["client_id"] == "oaiapp_fixture"
        assert data["redirect_uri"] == context["redirect_uri"]
        assert data["code_verifier"] == auth._pending.verifier
        identity = {"sub": "synthetic-account", "aud": data["client_id"], "iss": auth.ISSUER,
                    "exp": time.time() + 600, "nonce": context["nonce"], "email": "test@example.invalid"}
        return httpx.Response(200, json=grant(id_token=jwt.encode(identity, key, "RS256", headers={"kid": "fixture"}),
                                               expires_in=1))

    monkeypatch.setattr(auth, "_http", fake)
    client = TestClient(app, base_url="http://127.0.0.1:8001", client=("127.0.0.1", 21000))
    assert client.get("/api/settings/chatgpt").status_code == 401
    client.get("/api/local-access")
    reply = client.post("/api/settings/chatgpt/start", json={})
    assert reply.status_code == 200
    url = urlsplit(reply.json()["authorize_url"])
    assert url.hostname == "auth.openai.com"
    context.update({key: value[0] for key, value in parse_qs(url.query).items()})
    assert context["client_id"] == "dynamic_agent_client"
    assert context["code_challenge_method"] == "S256"
    assert context["ext_agent_host_id"] == auth._host_id()
    callback = context["redirect_uri"]
    with httpx.Client(trust_env=False) as browser:
        assert browser.get(callback, params={"state": "wrong", "code": "fixture"}).status_code == 400
        assert not calls
        assert browser.get(callback, params={"state": context["state"], "code": "fixture",
                                             "client_id": "oaiapp_fixture"}).status_code == 200
    state = client.get("/api/settings/chatgpt").json()
    assert state["status"] == "connected"
    account = state["account_id"]
    assert b"fixture-access" not in auth.CREDENTIALS.read_bytes()
    assert client.get("/api/settings/chatgpt/models", params={"profile_id": account}).json() == [
        {"id": "test-model", "label": "test-model"}]
    assert auth.access_token(account) == "rotated-access"
    assert calls.count(auth.OAUTH + "/oauth/token") == 2
    db = tmp_path / "isolated-settings.db"
    saved = llm_provider.save_settings("chatgpt", "test-model", db_path=db, chatgpt_profile_id=account)
    assert saved["chatgpt_profile_id"] == account and saved["has_api_key"]
    assert client.post("/api/settings/chatgpt/disconnect", json={"profile_id": account}).json() == {"revoked": True}
    assert not llm_provider.public_settings(db)["has_api_key"]
    reauth = parse_qs(urlsplit(auth.start(account)["authorize_url"]).query)
    assert reauth["client_id"] == ["oaiapp_fixture"]
    assert "agent_name_hint" not in reauth
    auth.cancel()
    assert auth.status()["status"] == "cancelled"
    assert not auth._accept(auth._pending, {"state": reauth["state"], "code": ["late-code"]})


def test_authorization_missing_scope_never_enables_plan():
    with pytest.raises(auth.ChatGPTAuthError, match="权限"):
        auth._tokens(grant(scope="openid profile"))


def sse(*events):
    return "".join(f"data: {json.dumps(item)}\n\n" for item in events)


def completed(*output):
    return {"type": "response.completed", "response": {"output": list(output)}}


def message(text):
    return {"type": "message", "content": [{"type": "output_text", "text": text}]}


def test_responses_runs_real_local_recommendation_and_program(local_auth, monkeypatch):
    import numpy as np
    from app import agent_loop, embedding, library

    requests = []
    def upstream(request):
        assert request.url == "https://api.openai.com/v1/responses"
        assert request.headers["Authorization"] == "Bearer fixture-access"
        body = json.loads(request.content)
        assert body["store"] is False and body["stream"] is True
        assert "max_output_tokens" not in body and "temperature" not in body
        requests.append(body)
        if body.get("tools"):
            assert body["tools"][0]["type"] == "namespace"
            response = completed({"type": "function_call", "namespace": "personal_radio", "call_id": "call_fixture",
                                  "name": "recommend_playlist", "arguments": '{"query":"想听温柔的歌"}'})
        else:
            assert body["text"]["format"] == {"type": "json_object"}
            response = completed(message('{"opening":{"text":"给这段夜色留一点轻声的陪伴，先听这轮的几首歌。","source_ids":[]},"segments":[]}'))
        return httpx.Response(200, text=sse(response), headers={"content-type": "text/event-stream"})

    real_client = httpx.Client
    monkeypatch.setattr(llm_provider.httpx, "Client", lambda **kw: real_client(transport=httpx.MockTransport(upstream), **kw))
    monkeypatch.setattr(auth, "access_token", lambda profile: "fixture-access")
    monkeypatch.setattr(llm_provider, "get_settings", lambda: {"provider": "chatgpt", "model": "test-model",
                        "base_url": auth.RESOURCE, "chatgpt_profile_id": "fixture"})
    fake = np.zeros((1, 512), dtype=np.float32)
    fake[0, 0] = 1
    monkeypatch.setattr(embedding, "embed_texts", lambda *_: fake)
    events = list(agent_loop.run_agent([{"role": "system", "content": "私人电台"},
                                      {"role": "user", "content": "想听温柔的歌"}], session_id="fixture"))
    songs = next(event["result"]["songs"] for event in events if event["type"] == "tool_result")
    assert songs and all(library.find_song_by_id(song["id"]) for song in songs)
    # 自动夹具禁用了后台写作。此处执行真实作者和稿件守卫。
    authored = _author_program(songs, "想听温柔的歌", length="short")
    assert any(item["position"] == 0 for item in authored["segments"]), authored
    assert requests[0]["input"][0]["role"] == "developer"
    assert not events[-1]["stats"]["degraded"]


def test_responses_tool_history_and_incomplete_stream():
    seen = []
    def upstream(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, text=sse({"type": "response.output_text.delta", "delta": "partial"},
                                            {"type": "response.incomplete"}))
    history = [{"role": "assistant", "tool_calls": [{"id": "call_fixture", "function": {"name": "search_library",
                "arguments": "{}"}}]}, {"role": "tool", "tool_call_id": "call_fixture", "content": "{}"}]
    with httpx.Client(transport=httpx.MockTransport(upstream)) as client:
        with pytest.raises(ValueError, match="未完成"):
            responses.request(client, "fixture", "test-model", history)
    assert seen[0]["input"][0]["namespace"] == "personal_radio"
    assert seen[0]["input"][1] == {"type": "function_call_output", "call_id": "call_fixture", "output": "{}"}


def test_responses_recognizes_chatgpt_plan_limit_before_stream_starts():
    upstream = httpx.MockTransport(lambda _: httpx.Response(
        429, json={"error": {"code": "subscription_sharing_usage_limit_exceeded", "message": "private detail"}}
    ))
    with httpx.Client(transport=upstream) as client:
        with pytest.raises(auth.ChatGPTAuthError, match="查看用量") as error:
            responses.request(client, "fixture", "test-model", [{"role": "user", "content": "测试"}])
    assert error.value.code == "chatgpt_usage_limit"
    assert "private detail" not in str(error.value)
