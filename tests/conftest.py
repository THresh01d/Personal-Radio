"""tests/conftest.py - 测试级全局中性化（本套件默认零外网、零真实 DeepSeek）

为什么这么做：本项目的既定约定是『测试绝不调用 DeepSeek / 外网』（各测试文件
docstring 均声明）。app.tools.author_song_intros 是 recommend handler 尾部的
逐首台词作者调用缝——生产走真实 DeepSeek；若测试里每次命中 handler 都真发一次
网络请求，套件会变慢、变脆（.env 在 import app 时已被加载，key 是真实存在的）。

这里用 autouse fixture 把它默认打成恒等桩（不覆写 dj_intro，保留模板）。
想测台词作者逻辑本身：直接 import app.voice_author 并传假 llm 即可，与本桩互不影响；
想验证 handler 真的接上了作者：在单个测试里 monkeypatch.setattr 覆盖本桩即可。
"""

from app.netease import credentials as ncm_credentials

import json
from pathlib import Path
import sqlite3

import numpy as np
import pytest

from app import library, tools
from app.library_db import ensure_library_schema
from db_isolation import DatabaseIsolation

ROOT = Path(__file__).resolve().parents[1]
_DB_REDIRECTED_CALLS = 0


@pytest.fixture(autouse=True)
def _isolated_credentials(tmp_path, monkeypatch):
    # 所有测试都不读取真实 Cookie，也不读写系统凭据；专门测试可局部覆盖这些缝。
    from app import credential_store
    monkeypatch.setattr(ncm_credentials, "CREDENTIAL_FILE", tmp_path / "credential.json")
    secrets = {}
    monkeypatch.setattr(credential_store, "_read_secret", lambda account: secrets.get(account))
    monkeypatch.setattr(credential_store, "_write_secret", lambda account, value: secrets.__setitem__(account, value))
    monkeypatch.setattr(credential_store, "_delete_secret", lambda account: secrets.pop(account, None))


@pytest.fixture(autouse=True)
def _isolated_library(tmp_path, monkeypatch):
    """每个测试使用可重复的最小曲库，不依赖开发者的私人 ``library.db``。

    过去部分测试会在仓库根目录直接读取真实曲库，因此开发机全部通过、干净
    副本却失败。这里保留几个测试会用到的已知实体，同时让显式传入 ``db_path``
    的检索测试继续使用它们自己的数据库。
    """
    db_path = tmp_path / "default-library.db"
    ensure_library_schema(db_path)
    songs = [
        ("1", "晴天", "周杰伦", "叶惠美", "流行", "温暖"),
        ("2", "夜曲", "周杰伦", "十一月的萧邦", "R&B", "夜晚"),
        ("3", "遇见", "孙燕姿", "The Moment", "流行", "温柔"),
        ("4", "半句再见", "孙燕姿", "半句再见", "流行", "伤感"),
        ("5", "Trauma", "未知名歌手", "测试专辑", "另类", "孤独"),
        ("6", "Bodies", "keshi", "测试专辑", "R&B", "平静"),
    ]
    unit_vector = np.zeros(512, dtype=np.float32)
    unit_vector[0] = 1.0
    with sqlite3.connect(db_path) as conn:
        for song_id, title, artist, album, genre, mood in songs:
            conn.execute(
                "INSERT INTO songs(song_id, title, artist, album, duration_seconds, aliases, search_text)"
                " VALUES (?, ?, ?, ?, 180, '', ?)",
                (song_id, title, artist, album, f"{title} {artist} {album}"),
            )
            conn.execute(
                "INSERT INTO song_embeddings(song_id, embedding) VALUES (?, ?)",
                (song_id, unit_vector.tobytes()),
            )
            conn.execute(
                "INSERT INTO song_profiles(song_id, genre, mood, scene, is_instrumental, language, source)"
                " VALUES (?, ?, ?, '[]', 0, '中文', 'test')",
                (
                    song_id,
                    json.dumps([genre], ensure_ascii=False),
                    json.dumps([mood], ensure_ascii=False),
                ),
            )

    monkeypatch.setattr(library, "DB_PATH", db_path)
    # 仅改模块全局变量覆盖不到早已捕获默认值的函数；连接边界统一指向合成库。
    isolation = DatabaseIsolation(ROOT, tmp_path, db_path, sqlite3.connect)
    monkeypatch.setattr(sqlite3, "connect", isolation.connect)
    library.invalidate_library_cache()
    yield isolation
    global _DB_REDIRECTED_CALLS
    _DB_REDIRECTED_CALLS += isolation.redirected_calls
    library.invalidate_library_cache()


def pytest_terminal_summary(terminalreporter):
    # 只输出次数，不记录路径、作品或SQL；用来确认旧默认路径确实被护栏接住。
    terminalreporter.write_line(f"测试数据库护栏：重定向 {_DB_REDIRECTED_CALLS} 次项目根目录默认连接")


@pytest.fixture(autouse=True)
def _no_voice_author_network(monkeypatch):
    # 桩签名须匹配 handler 调用 author_song_intros(songs, query)：就地改、返回 None
    monkeypatch.setattr(tools, "author_song_intros", lambda songs, message=None, **kw: None)
    # 普通 API 测试不能偷偷启动联网背景任务；后台行为另用注入资料的专门测试。
    from app import dj_program
    monkeypatch.setattr(dj_program, "schedule_program", lambda *args, **kwargs: None)
    from app import program_author, artist_background, interview_background
    monkeypatch.setattr(program_author, 'author_program', lambda *a, **kw: {'segments': [], 'copy_status': 'failed', 'copy_reasons': ['writer_unavailable']})
    monkeypatch.setattr(artist_background, 'get_artist_background', lambda *a, **kw: [])
    monkeypatch.setattr(interview_background, 'get_interview_excerpt', lambda *a, **kw: [])


@pytest.fixture(autouse=True)
def _no_wiki_network(request, monkeypatch):
    # 旧回归不触发新增百科网络；新模块的用例显式注入平台响应并验证接线。
    if request.module.__name__ != "test_platform_metadata":
        from app.netease import metadata
        monkeypatch.setattr(metadata, "fetch_wiki", lambda *args: {"code": 200, "data": {"blocks": []}})
        monkeypatch.setattr(metadata, "REQUEST_INTERVAL", 0)
