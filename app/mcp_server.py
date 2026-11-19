"""app/mcp_server.py - 把项目能力暴露为标准 MCP 协议（fastmcp 3.x）

为什么做这个：
- 项目主入口是 FastAPI（HTTP），但对外展示 MCP 生态能力是集成亮点；
- fastmcp 把已有的确定性能力（检索、推荐、单曲档案+歌曲知识）包装成标准
  MCP Server，任何 MCP 客户端（Claude Desktop、Cursor、自己写的客户端）
  都能直接调用，不需要懂我们的 HTTP API 和 SQLite 结构。

为什么复用 execute_tool / find_song_by_id / get_song_knowledge 而不重写：
- execute_tool 是工具层唯一出口，已经内置防幻觉校验、同歌手限流、防重复等
  硬约束。MCP 层只做「协议翻译」：声明参数 → 组装 args → 透传结果，绝不
  重复实现业务逻辑，保证 Web 与 MCP 两条路行为一致。
- 资源 song://{id} 是只读数据源（单曲档案 + 评论/歌词提炼主题），符合 MCP
  「资源 = 眼」的语义，且歌词原文不落库的红线在 song_knowledge 内部已经守住。

注意：MCP 是无状态的（每次调用独立），recommend_playlist 不传 session_id——
会话内防重复是 Web 会话级概念，MCP 客户端之间不共享『最近听过什么』。

运行方式：
- stdio（标准传输，Claude Desktop 等客户端直接接）：
      python -m app.mcp_server
- HTTP（方便 curl / 浏览器调试端点）：
      python -m app.mcp_server --http   默认 127.0.0.1:8931
"""

import asyncio
import json
import sys

from fastmcp import FastMCP

from app.library import find_song_by_id
from app.song_knowledge import get_song_knowledge
from app.tools import execute_tool

# 全局单例：mcp 对象持有工具/资源注册表，import 即注册，供测试与入口复用
mcp = FastMCP(
    name="personal-radio",
    instructions=(
        "个人歌库的 AI 电台能力：search_library 按语义检索歌曲，"
        "recommend_playlist 生成校验过的歌单，song://{id} 资源读取单曲档案与歌曲知识。"
        "所有返回的歌曲 id 都来自真实歌库。"
    ),
)


# ---------- 工具：手（执行动作） ----------

@mcp.tool()
def search_library(
    query: str,
    genre: list[str] | None = None,
    mood: list[str] | None = None,
    lang: str | None = None,
    instrumental: bool | None = None,
    scene: str | None = None,
    exclude: list[str] | None = None,
    count: int = 20,
) -> dict:
    """在用户的私人歌库里按语义检索歌曲（向量相似度 + 风格/情绪/语种/纯音乐硬过滤）。

    返回候选歌曲列表，每首含 id/歌名/歌手/专辑/风格/情绪/场景/相似度。
    """
    return execute_tool(
        "search_library",
        {
            "query": query,
            "genre": genre or [],
            "mood": mood or [],
            "lang": lang,
            "instrumental": instrumental,
            "scene": scene,
            "exclude": exclude or [],
            "count": count,
        },
    )


@mcp.tool()
def recommend_playlist(
    query: str,
    genre: list[str] | None = None,
    mood: list[str] | None = None,
    lang: str | None = None,
    instrumental: bool | None = None,
    scene: str | None = None,
    exclude: list[str] | None = None,
    artist: str | None = None,
    count: int = 30,
    target_count: int | None = None,
) -> dict:
    """生成一份最终播放歌单（3-8 首）。

    已后端校验：id 全部来自真实候选池、同一歌手最多 2 首、每首带 DJ 介绍与证据。
    传 artist 时歌单只来自这位歌手（0 命中返回空并说明）。
    """
    return execute_tool(
        "recommend_playlist",
        {
            "query": query,
            "genre": genre or [],
            "mood": mood or [],
            "lang": lang,
            "instrumental": instrumental,
            "scene": scene,
            "exclude": exclude or [],
            "artist": artist,
            "count": count,
            "target_count": target_count,
        },
        # MCP 无状态：不传 session_id，防重复是 Web 会话级概念
    )


# ---------- 资源：眼（只读数据源） ----------

@mcp.resource("song://{song_id}")
def song_resource(song_id: str) -> str:
    """读取单曲档案 + 歌曲知识（评论/歌词提炼主题），只读数据源。

    返回 JSON：{"song": {...档案}, "knowledge": {"from_comments", "from_lyrics"}}。
    找不到时返回 {"error": "..."}，客户端据此诚实说明，不编造。
    """
    song = find_song_by_id(song_id)
    if song is None:
        return json.dumps({"error": f"歌库没有找到歌曲 {song_id}"}, ensure_ascii=False)
    # get_song_knowledge 返回 None 表示知识抓取/提炼失败（有 SQLite 缓存，命中零成本）
    knowledge = get_song_knowledge(song_id)
    return json.dumps(
        {
            "song": {
                "id": song["id"],
                "title": song["title"],
                "artist": song["artist"],
                "album": song["album"],
                "genre": song.get("genre") or [],
                "mood": song.get("mood") or [],
                "scene": song.get("scene") or [],
                "is_instrumental": bool(song.get("is_instrumental")),
                "language": song.get("language"),
            },
            "knowledge": knowledge,
        },
        ensure_ascii=False,
    )


def main():
    """MCP Server 入口：stdio 为默认标准传输；--http 启动 HTTP 端点便于调试。"""
    if "--http" in sys.argv:
        # HTTP 传输：访问 http://127.0.0.1:8931/mcp 查看端点，curl 可调
        asyncio.run(mcp.run_http_async(host="127.0.0.1", port=8931))
    else:
        mcp.run()


if __name__ == "__main__":
    main()
