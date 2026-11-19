"""有限入场而非无限等待：繁忙快速拒绝，播放与轻量状态查询不共享队列。"""
import asyncio
import threading

from starlette.responses import JSONResponse

MAX_BODY_BYTES = 64 * 1024
BODY_TIMEOUT = 10
EXPENSIVE_PATHS = {"/api/chat", "/api/auto-queue", "/api/recommendations/adjust", "/api/settings/llm/test"}


class ResourceLimitsMiddleware:
    def __init__(self, app, limit=2):
        self.app = app
        self.slots = threading.BoundedSemaphore(limit)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not scope["path"].startswith("/api/"):
            await self.app(scope, receive, send)
            return
        expensive = scope["method"] == "POST" and scope["path"] in EXPENSIVE_PATHS
        if expensive and not self.slots.acquire(blocking=False):
            await JSONResponse({"detail": "DJ 正在处理其他请求，请稍后重试"}, status_code=429,
                               headers={"Retry-After": "2"})(scope, receive, send)
            return
        try:
            if scope["method"] in {"POST", "PUT", "PATCH"}:
                payload = bytearray()
                async def read_body():
                    while True:
                        message = await receive()
                        if message["type"] == "http.disconnect":
                            return False
                        payload.extend(message.get("body", b""))
                        if len(payload) > MAX_BODY_BYTES:
                            return 413
                        if not message.get("more_body"):
                            return True
                try:
                    outcome = await asyncio.wait_for(read_body(), BODY_TIMEOUT)
                except TimeoutError:
                    outcome = 408
                if outcome is False:
                    return
                if outcome in (408, 413):
                    detail = "请求内容过大，请缩短后重试" if outcome == 413 else "请求读取超时，请重试"
                    await JSONResponse({"detail": detail}, status_code=outcome)(scope, receive, send)
                    return
                consumed = False
                original_receive = receive
                async def limited_receive():
                    nonlocal consumed
                    if not consumed:
                        consumed = True
                        return {"type": "http.request", "body": bytes(payload), "more_body": False}
                    return await original_receive()
                receive = limited_receive
            await self.app(scope, receive, send)
        finally:
            if expensive:
                self.slots.release()
