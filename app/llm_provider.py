"""统一的 AI 模型 Provider 与本地设置。

Agent 只依赖这里的 OpenAI 兼容请求，不再知道背后是 DeepSeek、火山方舟、
自定义服务还是本地 Ollama。非密钥配置写入 settings.db；API Key 优先交给
操作系统凭据库（Windows Credential Manager 等），读取接口永不返回明文。
"""

import os
import json
import hashlib
import ipaddress
import re
import sqlite3
import threading
import time
from pathlib import Path
from urllib.parse import urlparse, urlunsplit

import httpx
from app import credential_store
from app.paths import DATA_ROOT
from app.diagnostics import timed, note, llm_failure_code


ROOT = Path(__file__).resolve().parent.parent
SETTINGS_DB = DATA_ROOT / "settings.db"
KEYRING_SERVICE = credential_store.SERVICE
_request_slots = threading.BoundedSemaphore(2)

PROVIDER_PRESETS = {
    "offline": {"model": "local-rules", "base_url": "", "needs_key": False},
    "deepseek": {
        "model": "deepseek-flash",
        "base_url": "https://api.deepseek.com",
        "needs_key": True,
    },
    "doubao": {
        "model": "doubao-seed-2-0-lite-260428",
        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
        "needs_key": True,
    },
    "openai_compatible": {"model": "", "base_url": "", "needs_key": True},
    "chatgpt": {"model": "", "base_url": "https://api.openai.com/v1", "needs_key": False},
    "ollama": {
        "model": "qwen3:4b",
        "base_url": "http://127.0.0.1:11434/v1",
        "needs_key": False,
    },
}

# 只替换已知旧预设，不重写用户手填的模型或方舟接入点。
_LEGACY_PRESETS = {
    "deepseek": {"deepseek-chat": "deepseek-flash", "deepseek-reasoner": "deepseek-flash"},
    "doubao": {"doubao-seed-2-0-lite-250428": "doubao-seed-2-0-lite-260428"},
}


def _current_model(provider, model):
    return _LEGACY_PRESETS.get(provider, {}).get(model, model)


class ProviderUnavailable(RuntimeError):
    """当前没有可用模型；上层捕获后进入本地确定性推荐。"""
    def __init__(self, message, code="llm_unconfigured"):
        super().__init__(message)
        self.code = code


class ProviderResponseInvalid(RuntimeError):
    """把上游 JSON 错误与用户配置 ValueError 分开，避免连接测试回显解析异常。"""


def _init_db(db_path=SETTINGS_DB):
    with sqlite3.connect(db_path, timeout=10) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS llm_settings (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                provider TEXT NOT NULL,
                model TEXT NOT NULL,
                base_url TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )


def _keyring_account(provider):
    return f"llm:{provider}"


def _read_secret(provider):
    """从系统凭据库取 Key；开发环境仍兼容根目录 .env。"""
    try:
        from app.credential_store import _backend
        keyring = _backend()

        secret = keyring.get_password(KEYRING_SERVICE, _keyring_account(provider))
        if secret:
            return secret
    except Exception:
        pass
    env_names = {
        "deepseek": "DEEPSEEK_API_KEY",
        "doubao": "DOUBAO_API_KEY",
        "openai_compatible": "OPENAI_COMPATIBLE_API_KEY",
    }
    return os.getenv(env_names.get(provider, ""), "")


def _write_secret(provider, api_key):
    """把 Key 写入操作系统凭据库，不写 SQLite、JSON 或前端存储。"""
    try:
        from app.credential_store import _backend
        keyring = _backend()

        keyring.set_password(KEYRING_SERVICE, _keyring_account(provider), api_key)
    except Exception as exc:
        raise ProviderUnavailable(
            "系统凭据库不可用，暂时不能安全保存 API Key；请安装 requirements.txt 后重试"
        ) from exc


def _default_settings():
    # 保持老用户 .env 零迁移可用；没有 Key 的新用户默认进入离线保底。
    provider = "deepseek" if os.getenv("DEEPSEEK_API_KEY") else "offline"
    preset = PROVIDER_PRESETS[provider]
    return {
        "provider": provider,
        "model": preset["model"],
        "base_url": preset["base_url"],
    }


def get_settings(db_path=SETTINGS_DB):
    _init_db(db_path)
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT provider, model, base_url, updated_at FROM llm_settings WHERE id=1"
        ).fetchone()
        binding = None
        if row and row[0] == "chatgpt" and conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='llm_chatgpt_binding'"
        ).fetchone():
            binding = conn.execute("SELECT profile_id FROM llm_chatgpt_binding WHERE id=1").fetchone()
    if not row:
        settings = _default_settings()
        settings["updated_at"] = None
        return settings
    settings = {"provider": row[0], "model": _current_model(row[0], row[1]), "base_url": row[2], "updated_at": row[3]}
    if row[0] == "chatgpt":
        settings["chatgpt_profile_id"] = binding[0] if binding else None
    return settings


def public_settings(db_path=SETTINGS_DB):
    settings = get_settings(db_path)
    provider = settings["provider"]
    preset = PROVIDER_PRESETS.get(provider, {})
    return {
        **settings,
        "mode": "offline" if provider == "offline" else "cloud" if provider != "ollama" else "local_model",
        "needs_api_key": bool(preset.get("needs_key", True)),
        "has_api_key": _provider_key_status(provider, settings["base_url"], preset, settings.get("chatgpt_profile_id")),
        "providers": [
            {
                "id": key,
                "default_model": value["model"],
                "default_base_url": value["base_url"],
                "needs_api_key": value["needs_key"],
                "has_saved_key": bool(_read_target_secret(key, value["base_url"]))
                if key in _LEGACY_PRESETS else False,
            }
            for key, value in PROVIDER_PRESETS.items()
        ],
    }


def _provider_key_status(provider, base_url, preset, profile_id=None):
    if provider == "chatgpt":
        from app.chatgpt_auth import connected
        return bool(profile_id and connected(profile_id))
    return bool(_read_target_secret(provider, base_url)) if preset.get("needs_key", True) else True


def _validate(provider, model, base_url):
    if provider not in PROVIDER_PRESETS:
        raise ValueError("不支持的模型服务商")
    model = _current_model(provider, (model or PROVIDER_PRESETS[provider]["model"]).strip())
    base_url = (base_url or PROVIDER_PRESETS[provider]["base_url"]).strip().rstrip("/")
    if provider != "offline" and not model:
        raise ValueError("请填写模型名称")
    if provider == "offline":
        return model, ""
    if provider == "chatgpt":
        if base_url != PROVIDER_PRESETS[provider]["base_url"]:
            raise ValueError("ChatGPT 套餐授权只能连接 OpenAI 官方 API")
        return model, base_url
    try:
        parsed = urlparse(base_url)
        port = parsed.port
        host = parsed.hostname or ""
        if (parsed.scheme not in {"http", "https"} or not host
                or parsed.username or parsed.password or parsed.query or parsed.fragment
                or re.search(r"[\s\\%]", parsed.netloc) or parsed.params):
            raise ValueError
        host = host.encode("idna").decode("ascii").lower()
        if not re.fullmatch(r"[a-z0-9.:-]+", host):
            raise ValueError
        netloc = f"[{host}]" if ":" in host else host
        if port is not None and port != (443 if parsed.scheme == "https" else 80):
            netloc += f":{port}"
        base_url = urlunsplit((parsed.scheme, netloc, parsed.path.rstrip("/"), "", ""))
    except (ValueError, UnicodeError):
        raise ValueError("API 地址必须是有效地址，不能包含账号、查询参数或片段") from None
    if provider in {"deepseek", "doubao"}:
        default = PROVIDER_PRESETS[provider]["base_url"]
        allowed = {default, default + "/chat/completions"}
        if provider == "deepseek":
            allowed |= {default + "/v1", default + "/v1/chat/completions"}
        if base_url not in allowed:
            raise ValueError("预设服务商只能使用官方 API 地址；其他服务请选择自定义兼容接口并填写独立 Key")
    loopback = host in {"127.0.0.1", "localhost", "::1"}
    if provider == "ollama" and not loopback:
        raise ValueError("Ollama 仅允许字面本机回环地址")
    if not loopback:
        if parsed.scheme != "https":
            raise ValueError("非本机 API 必须使用 HTTPS")
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            address = None
        if address is not None:
            if not address.is_global:
                raise ValueError("不允许非回环的内网或保留 IP")
        else:
            if "." not in host or host.endswith((".localhost", ".local", ".internal")):
                raise ValueError("远程 API 请使用公开 HTTPS 域名") from None
    return model, base_url


def _target_account(provider, base_url):
    """自定义 Key 绑定最终请求地址；更换域名或路径不能借用其他目标的凭据。"""
    if provider == "openai_compatible":
        digest = hashlib.sha256(_chat_url(base_url).encode()).hexdigest()
        return f"openai_compatible:{digest}"
    return provider


def _read_target_secret(provider, base_url):
    # 不自动迁移没有目标归属的旧自定义 Key / 环境变量，用户需明确重新输入一次。
    return _read_secret(_target_account(provider, base_url))


def save_settings(provider, model="", base_url="", api_key=None, db_path=SETTINGS_DB, *, chatgpt_profile_id=None):
    model, base_url = _validate(provider, model, base_url)
    if not PROVIDER_PRESETS[provider]["needs_key"] and api_key:
        raise ValueError("这个模式不接收 API Key")
    if provider == "chatgpt" and not _provider_key_status(provider, base_url, PROVIDER_PRESETS[provider], chatgpt_profile_id):
        raise ValueError("请先连接 ChatGPT 账号")
    if api_key:
        _write_secret(_target_account(provider, base_url), api_key.strip())
    if PROVIDER_PRESETS[provider]["needs_key"] and not _read_target_secret(provider, base_url):
        raise ValueError("这个 API 地址需要独立的 API Key；地址变化后请重新填写")
    _init_db(db_path)
    updated_at = time.strftime("%Y-%m-%d %H:%M:%S")
    with sqlite3.connect(db_path, timeout=10) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO llm_settings(id, provider, model, base_url, updated_at)"
            " VALUES (1,?,?,?,?)",
            (provider, model, base_url, updated_at),
        )
        if provider == "chatgpt":
            conn.execute("CREATE TABLE IF NOT EXISTS llm_chatgpt_binding (id INTEGER PRIMARY KEY CHECK (id = 1), profile_id TEXT NOT NULL)")
            conn.execute("INSERT OR REPLACE INTO llm_chatgpt_binding VALUES (1, ?)", (chatgpt_profile_id,))
    return public_settings(db_path)


def _chat_url(base_url):
    return base_url if base_url.endswith("/chat/completions") else f"{base_url}/chat/completions"


@timed("llm")
def request_chat(
    messages,
    *,
    tools=None,
    response_format=None,
    temperature=0.8,
    max_tokens=1500,
    timeout=60,
    settings=None,
    api_key=None,
):
    """调用当前 OpenAI 兼容模型并返回原始响应 JSON。

    ``settings``/``api_key`` 仅供“测试连接”使用，不会隐式保存用户输入。
    """
    settings = dict(settings or get_settings())
    provider = settings["provider"]
    if provider == "offline":
        note("offline")
        raise ProviderUnavailable("当前使用离线基础模式", "offline")
    model, base_url = _validate(provider, settings.get("model"), settings.get("base_url"))
    if not PROVIDER_PRESETS[provider]["needs_key"] and api_key:
        raise ProviderUnavailable("这个模式不接收 API Key")
    key = (api_key or _read_target_secret(provider, base_url)) if PROVIDER_PRESETS[provider]["needs_key"] else None
    if PROVIDER_PRESETS[provider]["needs_key"] and not key:
        note("llm_unconfigured")
        raise ProviderUnavailable("当前模型尚未配置 API Key")
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if tools:
        payload.update({"tools": tools, "tool_choice": "auto"})
    if provider in {"deepseek", "doubao"}:
        # DJ 多轮工具消息只传事实结果，沿用非思考调用合同。
        payload["thinking"] = {"type": "disabled"}
    if response_format:
        payload["response_format"] = response_format
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    # 云模型尊重用户代理设置；只有本机 Ollama 明确绕过系统代理，避免回环请求
    # 被代理软件转发后出现难以理解的 502。
    # 网络槽位由实际工作线程持有；HTTP 客户端取消不能让尚未结束的请求无限增加。
    if not _request_slots.acquire(blocking=False):
        note("llm_busy")
        raise ProviderUnavailable("模型服务繁忙，暂时使用本地推荐", "llm_busy")
    try:
        with httpx.Client(timeout=timeout, trust_env=provider != "ollama", follow_redirects=False) as client:
            if provider == "chatgpt":
                from app import chatgpt_auth, chatgpt_responses
                try:
                    profile_id = settings.get("chatgpt_profile_id")
                    if not profile_id:
                        raise ProviderUnavailable("请先在 AI 设置中选择 ChatGPT 账号。")
                    return chatgpt_responses.request(client, chatgpt_auth.access_token(profile_id), model, messages, tools,
                                                    response_format, timeout=timeout)
                except chatgpt_auth.ChatGPTAuthError as exc:
                    raise ProviderUnavailable(str(exc), exc.code) from None
            response = client.post(_chat_url(base_url), json=payload, headers=headers)
            response.raise_for_status()
        try:
            data = response.json()
        except ValueError as exc:
            raise ProviderResponseInvalid("模型返回格式不可用") from exc
        if not isinstance(data, dict):
            raise ProviderResponseInvalid("模型返回格式不可用")
        return data
    except Exception as exc:
        note(llm_failure_code(exc))
        raise
    finally:
        _request_slots.release()


def test_connection(provider, model="", base_url="", api_key=None, *, check_capabilities=False, chatgpt_profile_id=None):
    model, base_url = _validate(provider, model, base_url)
    if provider == "offline":
        return {"ok": True, "message": "离线基础模式无需连接云模型"}
    def probe(messages, **kwargs):
        data = request_chat(messages, temperature=0, timeout=8, max_tokens=128,
                            settings={"provider": provider, "model": model, "base_url": base_url,
                                      "chatgpt_profile_id": chatgpt_profile_id},
                            api_key=api_key, **kwargs)
        try:
            message = data["choices"][0]["message"]
            if isinstance(message, dict):
                return message
        except (IndexError, KeyError, TypeError):
            pass
        raise ProviderResponseInvalid("模型没有返回有效的对话消息")

    message = probe([{"role": "user", "content": "只回复 OK"}])
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        raise ProviderResponseInvalid("模型没有返回可见文本")
    if not check_capabilities:
        return {"ok": True, "message": "连接成功"}

    def check(run, validate):
        try:
            return "passed" if validate(run()) else "invalid"
        except httpx.HTTPStatusError as exc:
            return "unsupported" if exc.response.status_code in (400, 404, 422) else llm_failure_code(exc)
        except Exception as exc:
            return llm_failure_code(exc)

    tool = {"type": "function", "function": {"name": "probe_library", "description": "返回合成测试曲目的编号",
            "parameters": {"type": "object", "properties": {"title": {"type": "string"}}, "required": ["title"]}}}
    tool_prompt = [{"role": "user", "content": "请调用 probe_library，title 填入 Radio Test，勿直接回答。"}]

    def is_tool_reply(message):
        for call in message.get("tool_calls") or []:
            fn = call["function"]
            if fn["name"] == "probe_library" and json.loads(fn["arguments"]) == {"title": "Radio Test"}:
                return True
        return False

    json_prompt = [{"role": "user", "content":
                    '只输出 JSON 对象 {"opening":{"text":"现在开始播放测试歌单。","source_ids":[]},"segments":[]}。'}]

    def is_json_reply(message):
        result = json.loads(message.get("content") or "")
        if not isinstance(result, dict) or result.get("segments") != []:
            return False
        opening = result.get("opening")
        return (isinstance(opening, dict) and isinstance(opening.get("text"), str)
                and bool(opening["text"].strip()) and opening.get("source_ids") == [])

    tool_status = check(lambda: probe(tool_prompt, tools=[tool]), is_tool_reply)
    capabilities = {"chat": "passed", "tools": tool_status,
                    "program_json": "chatgpt_usage_limit" if tool_status == "chatgpt_usage_limit" else
                    check(lambda: probe(json_prompt, response_format={"type": "json_object"}), is_json_reply)}
    complete = all(status == "passed" for status in capabilities.values())
    return {"ok": complete, "message": "对话、工具调用和结构化稿件均通过" if complete else "能对话，但部分 DJ 能力未通过",
            "capabilities": capabilities}
