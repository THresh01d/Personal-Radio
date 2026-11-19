"""pages HTTP适配；应用保护由统一装配入口注册。"""

from fastapi import APIRouter, HTTPException
from pathlib import Path
from fastapi.responses import FileResponse

ROOT = Path(__file__).resolve().parents[2]
STATIC_DIR = ROOT / "static"
FRONTEND_DIST_DIR = STATIC_DIR / "dist"

router = APIRouter()


@router.get("/", include_in_schema=False)
def index():
    """首页：直接返回单页前端。

    加 no-cache 头：首页入口迭代频繁（资源文件名由Vite哈希管理），浏览器若缓存了
    旧 index.html 会让用户看不到新功能（曾出现『反馈按钮找不到』实际是缓存）。
    每次进站都重新校验，代价是一次 304/200，远小于踩缓存坑的排查成本。
    """
    frontend_index = FRONTEND_DIST_DIR / "index.html"
    page = frontend_index if frontend_index.is_file() else STATIC_DIR / "index.html"
    return FileResponse(page, headers={"Cache-Control": "no-cache"})
