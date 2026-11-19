"""把 ChatGPT 套餐的 Responses 事件适配给已有 DJ 和曲库工具。"""

import json
import time

import httpx

from app import chatgpt_auth

_TOOL_NAMESPACE = "personal_radio"


def _check_error(error):
    code = error.get("code") if isinstance(error, dict) else None
    if code == "subscription_sharing_usage_limit_exceeded":
        raise chatgpt_auth.ChatGPTAuthError("ChatGPT 套餐额度已达上限，请到 ChatGPT 查看用量。", "chatgpt_usage_limit")
    if code == "subscription_sharing_usage_unavailable":
        raise chatgpt_auth.ChatGPTAuthError("暂时无法确认 ChatGPT 套餐额度，请稍后再试。", "chatgpt_usage_limit")
    if code == "subscription_sharing_user_not_eligible":
        raise chatgpt_auth.ChatGPTAuthError("这个 ChatGPT 账号或工作区暂不支持使用套餐额度。")


def _check_status(stream):
    if stream.is_success:
        return
    # 请求尚未开始推理时也可能以普通 HTTP JSON 返回套餐错误，日志不保留上游正文。
    body = bytearray()
    for chunk in stream.iter_bytes(chunk_size=4096):
        body.extend(chunk)
        if len(body) > 16384:
            break
    if len(body) <= 16384:
        try:
            data = json.loads(body)
        except ValueError:
            data = None
        if isinstance(data, dict):
            _check_error(data.get("error"))
    stream.raise_for_status()


def _input(messages):
    items = []
    for message in messages:
        role = message["role"]
        if role == "tool":
            items.append({"type": "function_call_output", "call_id": message["tool_call_id"],
                          "output": message["content"]})
            continue
        if message.get("content"):
            items.append({"role": "developer" if role == "system" else role, "content": message["content"]})
        for call in message.get("tool_calls") or ():
            function = call["function"]
            items.append({"type": "function_call", "call_id": call["id"], "name": function["name"],
                          "namespace": _TOOL_NAMESPACE, "arguments": function["arguments"]})
    return items


def _result(response):
    text, calls = [], []
    for item in response.get("output", []):
        if item.get("type") == "message":
            text.extend(block["text"] for block in item.get("content", []) if block.get("type") == "output_text")
        if item.get("type") == "function_call":
            if not item.get("call_id") or item.get("namespace") not in (None, _TOOL_NAMESPACE):
                raise ValueError("ChatGPT 工具调用返回无效")
            calls.append({"id": item["call_id"], "type": "function",
                          "function": {"name": item["name"], "arguments": item["arguments"]}})
    message = {"content": "".join(text) or None}
    if calls:
        message["tool_calls"] = calls
    if not text and not calls:
        raise ValueError("ChatGPT 没有返回可用的回复")
    usage = response.get("usage") or {}
    return {"choices": [{"message": message}], "usage": {"prompt_tokens": usage.get("input_tokens", 0),
            "completion_tokens": usage.get("output_tokens", 0), "total_tokens": usage.get("total_tokens", 0)}}


def request(client, token, model, messages, tools=None, response_format=None, *, timeout=60):
    payload = {"model": model, "input": _input(messages), "store": False, "stream": True}
    if tools:
        # 套餐通道要求自定义函数放进命名空间。白名单和参数校验仍由现有 Agent 执行。
        payload["tools"] = [{"type": "namespace", "name": _TOOL_NAMESPACE, "description": "私人电台的曲库工具",
                             "tools": [{"type": "function", **tool["function"], "strict": False} for tool in tools]}]
    if response_format:
        payload["text"] = {"format": response_format}
    deadline = time.monotonic() + timeout
    with client.stream("POST", chatgpt_auth.RESOURCE + "/responses", json=payload,
                       headers={"Authorization": "Bearer " + token, "Accept": "text/event-stream"}) as stream:
        _check_status(stream)
        # 只有 completed 中的完整输出才可被提交；中断的工具参数永不执行。
        lines = []
        size = 0
        for line in stream.iter_lines():
            if time.monotonic() >= deadline:
                raise httpx.ReadTimeout("ChatGPT 回复超时")
            if line.startswith("data:"):
                size += len(line)
                if size > 4 * 1024 * 1024:
                    raise ValueError("ChatGPT 回复超过长度限制")
                lines.append(line[5:].lstrip())
            elif not line and lines:
                data = "\n".join(lines)
                lines.clear()
                if data == "[DONE]":
                    break
                event = json.loads(data)
                kind = event.get("type")
                if kind == "response.completed":
                    return _result(event["response"])
                if kind in {"response.failed", "response.incomplete", "error"}:
                    error = (event.get("response") or {}).get("error") or event
                    _check_error(error)
                    raise ValueError("ChatGPT 未完成这次请求")
    raise ValueError("ChatGPT 回复在完成前中断")
