"""模型 Provider 配置与离线保底测试。"""

import sqlite3

import pytest

from app import llm_provider


def test_new_user_without_key_defaults_to_offline(tmp_path, monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setattr(llm_provider, "_read_secret", lambda provider: "")

    settings = llm_provider.public_settings(tmp_path / "settings.db")

    assert settings["provider"] == "offline"
    assert settings["mode"] == "offline"
    assert settings["has_api_key"] is True


def test_legacy_env_key_keeps_deepseek_as_default(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-from-env")
    monkeypatch.setattr(
        llm_provider,
        "_read_secret",
        lambda provider: "sk-from-env" if provider == "deepseek" else "",
    )

    settings = llm_provider.public_settings(tmp_path / "settings.db")

    assert settings["provider"] == "deepseek"
    assert settings["model"] == "deepseek-flash"
    assert settings["has_api_key"] is True


def test_save_cloud_settings_never_writes_api_key_to_sqlite(tmp_path, monkeypatch):
    db_path = tmp_path / "settings.db"
    secrets = {}
    monkeypatch.setattr(
        llm_provider, "_write_secret", lambda provider, value: secrets.__setitem__(provider, value)
    )
    monkeypatch.setattr(
        llm_provider, "_read_secret", lambda provider: secrets.get(provider, "")
    )

    result = llm_provider.save_settings(
        "deepseek", "deepseek-chat", "https://api.deepseek.com", "sk-secret", db_path
    )

    assert result["provider"] == "deepseek"
    assert result["has_api_key"] is True
    raw = db_path.read_bytes()
    assert b"sk-secret" not in raw
    with sqlite3.connect(db_path) as conn:
        row = conn.execute("SELECT provider, model, base_url FROM llm_settings").fetchone()
    assert row == ("deepseek", "deepseek-flash", "https://api.deepseek.com")


def test_offline_provider_refuses_network_call(tmp_path, monkeypatch):
    monkeypatch.setattr(llm_provider, "SETTINGS_DB", tmp_path / "settings.db")
    llm_provider.save_settings("offline", db_path=tmp_path / "settings.db")

    with pytest.raises(llm_provider.ProviderUnavailable, match="离线"):
        llm_provider.request_chat(
            [{"role": "user", "content": "hello"}],
            settings={"provider": "offline", "model": "local-rules", "base_url": ""},
        )


def test_custom_provider_rejects_invalid_base_url():
    with pytest.raises(ValueError, match="API 地址"):
        llm_provider.test_connection(
            "openai_compatible", "some-model", "not-a-url", "sk-test"
        )


@pytest.mark.parametrize("provider,url", [
    ("deepseek", "https://evil.example"), ("doubao", "https://evil.example"),
    ("deepseek", "https://api.deepseek.com.evil.example"),
    ("openai_compatible", "http://api.example.com/v1"),
    ("openai_compatible", "https://user:password@api.example.com/v1"),
    ("openai_compatible", "https://api.example.com/v1?token=secret"),
    ("openai_compatible", "https://api.example.com/v1#fragment"),
    ("openai_compatible", "https://192.168.1.1/v1"),
    ("openai_compatible", "https://169.254.169.254/v1"),
    ("ollama", "https://remote.example.com/v1"),
])
def test_unsafe_targets_rejected_before_credentials_or_network(provider, url, monkeypatch):
    monkeypatch.setattr(llm_provider, "_read_secret", lambda *_: pytest.fail("不应读取 Key"))
    monkeypatch.setattr(llm_provider.httpx, "Client", lambda **_: pytest.fail("不应联网"))
    with pytest.raises(ValueError):
        llm_provider.test_connection(provider, "model", url)


def test_custom_key_is_bound_to_canonical_target_and_never_reuses_legacy(tmp_path, monkeypatch):
    secrets = {"openai_compatible": "legacy-unbound"}
    monkeypatch.setattr(llm_provider, "_read_secret", lambda key: secrets.get(key, ""))
    monkeypatch.setattr(llm_provider, "_write_secret", lambda key, value: secrets.__setitem__(key, value))
    path = tmp_path / "settings.db"
    a = "https://api.example.com/v1"
    with pytest.raises(ValueError, match="独立"):
        llm_provider.save_settings("openai_compatible", "model", a, db_path=path)
    llm_provider.save_settings("openai_compatible", "model", a, "new-bound-key", path)
    assert llm_provider._read_target_secret("openai_compatible", a) == "new-bound-key"
    before = llm_provider.get_settings(path)
    for changed in ("https://other.example.com/v1", "https://api.example.com/v2"):
        with pytest.raises(ValueError, match="独立"):
            llm_provider.save_settings("openai_compatible", "model", changed, db_path=path)
        assert llm_provider.get_settings(path) == before
    _, canonical = llm_provider._validate("openai_compatible", "model", "https://API.EXAMPLE.COM:443/v1/")
    assert canonical == a
    assert llm_provider._target_account("openai_compatible", a + "/chat/completions") == llm_provider._target_account("openai_compatible", a)
    assert b"new-bound-key" not in path.read_bytes()


def test_request_to_other_target_never_forwards_existing_key(monkeypatch):
    bound = llm_provider._target_account("openai_compatible", "https://api.example.com/v1")
    monkeypatch.setattr(llm_provider, "_read_secret", lambda key: "saved-secret" if key == bound else "")
    monkeypatch.setattr(llm_provider.httpx, "Client", lambda **_: pytest.fail("不应联网"))
    with pytest.raises(llm_provider.ProviderUnavailable):
        llm_provider.test_connection("openai_compatible", "model", "https://other.example.com/v1")


def test_valid_request_keeps_redirects_disabled_and_sends_only_bound_key(monkeypatch):
    calls = []
    monkeypatch.setattr(llm_provider, "_read_secret", lambda key: "test-key" if key == "deepseek" else "")
    class Client:
        def __init__(self, **kwargs):
            assert kwargs["follow_redirects"] is False
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def post(self, url, **kwargs):
            calls.append((url, kwargs["headers"]["Authorization"]))
            import httpx
            return httpx.Response(200, request=httpx.Request("POST", url), json={"choices": [{"message": {"content": "OK"}}]})
    monkeypatch.setattr(llm_provider.httpx, "Client", Client)
    assert llm_provider.test_connection("deepseek")["ok"]
    assert calls == [("https://api.deepseek.com/chat/completions", "Bearer test-key")]


def test_llm_capacity_is_bounded_and_error_releases_real_worker_slot(monkeypatch):
    import threading
    slots = threading.BoundedSemaphore(1)
    monkeypatch.setattr(llm_provider, "_request_slots", slots)
    monkeypatch.setattr(llm_provider, "_read_secret", lambda *_: "synthetic-test-key")
    slots.acquire()
    monkeypatch.setattr(llm_provider.httpx, "Client", lambda **_: pytest.fail("繁忙时不能创建网络请求"))
    with pytest.raises(llm_provider.ProviderUnavailable, match="繁忙"):
        llm_provider.test_connection("deepseek")
    slots.release()
    class Failing:
        def __init__(self, **kwargs):
            pass
        def __enter__(self):
            raise RuntimeError("synthetic-external-error")
        def __exit__(self, *args):
            pass
    monkeypatch.setattr(llm_provider.httpx, "Client", Failing)
    with pytest.raises(RuntimeError):
        llm_provider.test_connection("deepseek")
    assert slots.acquire(blocking=False)
    slots.release()


@pytest.mark.parametrize("provider,old,expected", [
    ("deepseek", "deepseek-chat", "deepseek-flash"),
    ("doubao", "doubao-seed-2-0-lite-250428", "doubao-seed-2-0-lite-260428"),
    ("openai_compatible", "test-model", "test-model"),
])
def test_capabilities_probe_uses_current_model_and_exposes_partial_failure(provider, old, expected, monkeypatch):
    import json
    import httpx
    payloads = []

    def upstream(request):
        payload = json.loads(request.content)
        payloads.append(payload)
        assert payload["model"] == expected
        if provider != "openai_compatible":
            assert payload["thinking"] == {"type": "disabled"}
        else:
            assert "thinking" not in payload
        if "tools" in payload:
            message = {"tool_calls": [{"function": {"name": "probe_library",
                                                    "arguments": '{"title":"Radio Test"}'}}]}
        elif "response_format" in payload:
            if provider == "openai_compatible":
                return httpx.Response(400)
            message = {"content": '{"opening":{"text":"测试开场。","source_ids":[]},"segments":[]}'}
        else:
            message = {"content": "OK"}
        return httpx.Response(200, json={"choices": [{"message": message}]})

    real_client = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kw: real_client(transport=httpx.MockTransport(upstream), **kw))
    base_url = "https://example.test/v1" if provider == "openai_compatible" else ""
    result = llm_provider.test_connection(provider, old, base_url, "test-key", check_capabilities=True)
    assert len(payloads) == 3
    assert result["capabilities"] == {"chat": "passed", "tools": "passed",
                                      "program_json": "unsupported" if provider == "openai_compatible" else "passed"}
    assert result["ok"] is (provider != "openai_compatible")
