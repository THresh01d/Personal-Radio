"""settings HTTP适配；应用保护由统一装配入口注册。"""

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from app import ncm, library, chatgpt_auth, credential_store
from app.library_db import library_summary
from app.llm_provider import ProviderUnavailable, public_settings, save_settings, test_connection
from app.diagnostics import snapshot, note, llm_failure_code
from app.safe_logging import log_failure
from app.api.schemas import LLMSettingsRequest, LLMConnectionRequest

router = APIRouter()


class ChatGPTAccountRequest(BaseModel):
    profile_id: str | None = Field(None, max_length=24)


def _account_action(action, *args):
    try:
        return action(*args)
    except (chatgpt_auth.ChatGPTAuthError, credential_store.CredentialUnavailable) as exc:
        raise HTTPException(422, str(exc)) from None
    except Exception as exc:
        log_failure("chatgpt_account_action_failed", exc)
        raise HTTPException(502, "ChatGPT 账号操作未完成，请检查网络后重试。") from None


@router.get("/api/settings/chatgpt")
def chatgpt_status():
    return JSONResponse(chatgpt_auth.status(), headers={"Cache-Control": "no-store"})


@router.post("/api/settings/chatgpt/start")
def chatgpt_start(req: ChatGPTAccountRequest):
    return JSONResponse(_account_action(chatgpt_auth.start, req.profile_id), headers={"Cache-Control": "no-store"})


@router.post("/api/settings/chatgpt/cancel")
def chatgpt_cancel():
    chatgpt_auth.cancel()
    return {"ok": True}


@router.get("/api/settings/chatgpt/models")
def chatgpt_models(profile_id: str | None = Query(None, max_length=24)):
    return JSONResponse(_account_action(chatgpt_auth.models, profile_id), headers={"Cache-Control": "no-store"})


@router.post("/api/settings/chatgpt/select")
def chatgpt_select(req: ChatGPTAccountRequest):
    _account_action(chatgpt_auth.select, req.profile_id)
    return {"ok": True}


@router.post("/api/settings/chatgpt/disconnect")
def chatgpt_disconnect(req: ChatGPTAccountRequest):
    return _account_action(chatgpt_auth.disconnect, req.profile_id)


@router.get("/api/settings/llm")
def get_llm_settings():
    """返回可公开的模型设置；永远不返回 API Key 明文。"""
    return public_settings()


@router.get("/api/setup/status")
def get_setup_status():
    """首次启动向导的单一状态源，不触发模型加载或网易云网络请求。"""
    credential = ncm._load_credential()
    return {
        "library": library_summary(library.DB_PATH),
        "netease": {
            "credential_present": bool(credential),
            "nickname": (credential or {}).get("nickname") or None,
        },
        "llm": public_settings(),
    }


@router.post("/api/settings/llm/test")
def test_llm_settings(req: LLMConnectionRequest):
    """用极小请求验证模型、地址与 Key，失败也不修改当前配置。"""
    try:
        kwargs = {"check_capabilities": True} if req.check_capabilities else {}
        if req.provider == "chatgpt":
            kwargs["chatgpt_profile_id"] = req.chatgpt_profile_id
        return test_connection(req.provider, req.model, req.base_url, req.api_key, **kwargs)
    except (ValueError, ProviderUnavailable) as exc:
        raise HTTPException(422, str(exc)) from exc
    except Exception as exc:
        code = llm_failure_code(exc)
        note(code)
        log_failure("llm_connection_failed", exc)
        # 连接测试没有执行推荐，不能把它的失败提示说成“已经完成本地降级”。
        detail = {"llm_timeout": "模型连接测试超时，请检查网络或稍后重试",
                  "llm_network": "模型网络暂不可用，请检查地址和网络",
                  "llm_http": "模型服务拒绝请求，请检查 Key、模型与权限",
                  "llm_invalid": "模型返回格式不可用，请检查兼容接口设置",
                  "chatgpt_usage_limit": "ChatGPT 套餐额度暂不可用，请前往 ChatGPT 设置查看用量",
                  }.get(code, "模型连接测试未完成，请检查 AI 设置")
        raise HTTPException(504 if code == "llm_timeout" else 502, detail) from exc


@router.post("/api/settings/llm")
def update_llm_settings(req: LLMSettingsRequest):
    """保存模型选择；Key 进入系统凭据库，SQLite 只记非敏感配置。"""
    try:
        kwargs = {"chatgpt_profile_id": req.chatgpt_profile_id} if req.provider == "chatgpt" else {}
        return save_settings(req.provider, req.model, req.base_url, req.api_key, **kwargs)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except ProviderUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc


@router.get("/api/diagnostics")
def request_diagnostics():
    """沿用本机同源访问保护，只读当前进程的有限诊断，不写 SQLite。"""
    return JSONResponse(snapshot(), headers={"Cache-Control": "no-store"})
