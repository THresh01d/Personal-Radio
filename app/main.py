"""FastAPI装配入口：统一保护、生命周期与路由注册；不包含推荐业务。"""

import threading
from app.paths import DATA_ROOT, load_project_env
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app import library, ncm, qqmusic
from app.api import ROUTERS
from app.api.hotkeys import router as hotkey_router
from app.api.pages import FRONTEND_DIST_DIR
from app.diagnostics import DiagnosticsMiddleware
from app.library_db import ensure_library_schema
from app.hotkeys import RadioHotkeys
from app.local_access import LocalAccessMiddleware
from app.profile_review_api import router as profile_review_router
from app.resource_limits import ResourceLimitsMiddleware
from app.safe_logging import install_access_filter, log_failure

load_project_env()


async def safe_validation_error(request, exc):
    # Pydantic 默认回显 input，模型设置中的 Key/Cookie 可能因此出现在响应里。
    return JSONResponse({"detail": "请求参数格式不正确，请检查后重试",
                         "fields": [list(error["loc"]) for error in exc.errors()]}, status_code=422)


def _start_ncm_gateway():
    """启动时在后台拉起网易云 node 网关，不阻塞 uvicorn 就绪。

    网关端口 3000 若已被本网关占用则自动复用；首次请求 /api/ncm/* 前
    也会在端点内再兜底调用 ensure_gateway()，保证无论顺序都可用。
    """

    # 空白克隆第一次启动时先创建空曲库；这是纯本地轻量操作，不加载 BGE，
    # 因此不会拖慢服务就绪，也不会要求用户先运行开发用 CSV 脚本。
    ensure_library_schema(library.DB_PATH)

    def _boot():
        try:
            ncm.ensure_gateway()
        except Exception as e:
            log_failure("gateway_start_failed", e)

    threading.Thread(target=_boot, daemon=True).start()


def _stop_ncm_gateway():
    """退出时关闭自己拉起的网关子进程（不碰复用的已有监听）。"""
    ncm.shutdown_gateway()


def create_app():
    """只装配对象；真正的启动/关闭仍由ASGI生命周期触发。"""
    application = FastAPI(title="私人电台 Personal Radio")
    application.state.hotkeys = RadioHotkeys(DATA_ROOT / ".cache" / "windows-hotkeys.json")
    install_access_filter()
    # 顺序属于访问保护与诊断契约，不随路由拆分变化。
    application.add_middleware(ResourceLimitsMiddleware)
    application.add_middleware(LocalAccessMiddleware)
    application.add_middleware(DiagnosticsMiddleware)
    application.add_exception_handler(RequestValidationError, safe_validation_error)
    if FRONTEND_DIST_DIR.is_dir():
        application.mount("/dist", StaticFiles(directory=FRONTEND_DIST_DIR), name="frontend-dist")
    application.include_router(ncm.router)
    application.include_router(qqmusic.router)
    application.include_router(profile_review_router)
    application.include_router(hotkey_router)
    for router in ROUTERS:
        application.include_router(router)
    application.router.add_event_handler("startup", _start_ncm_gateway)
    application.router.add_event_handler("shutdown", _stop_ncm_gateway)
    application.router.add_event_handler("shutdown", application.state.hotkeys.stop)
    return application

app = create_app()
