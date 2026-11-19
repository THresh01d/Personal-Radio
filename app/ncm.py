"""网易云公共入口：保留调用者使用的路由、生命周期与有限兼容函数。

具体适配在 netease/ 内维护；本文件不保存连接池、凭据路径或同步任务状态。
"""
from fastapi import APIRouter
from app.netease import auth, catalog, playback, sync
from app.netease.gateway import ensure_gateway, shutdown_gateway, _gateway_healthy, _gw
from app.netease.credentials import _load_credential

router = APIRouter()
for child in (auth.router, playback.router, catalog.router, sync.router):
    router.include_router(child)
