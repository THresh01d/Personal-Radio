"""测试专用SQLite连接护栏，覆盖函数在定义时捕获的正式库默认路径。"""

import os
from pathlib import Path
import re
from urllib.parse import parse_qs, unquote, urlsplit


class DatabaseIsolation:
    def __init__(self, root, directory, library_database, connector):
        self.root = Path(root).resolve()
        self.directory = Path(directory).resolve()
        self.library_database = Path(library_database).resolve()
        if self.library_database.parent != self.directory:
            raise AssertionError("合成曲库必须在当前测试目录中")
        self.connector = connector
        self.redirected_calls = 0

    def target(self, database, *, uri=False):
        """只映射项目根目录数据库；不打开、读取或复制原数据库。"""
        if not isinstance(database, (str, bytes, os.PathLike)):
            return database, False
        text = os.fsdecode(database)
        if text in ("", ":memory:"):
            return database, False
        is_uri = text.startswith("file:")
        query = ""
        if is_uri:
            if not uri:
                raise AssertionError("测试SQLite URI必须显式声明uri=True")
            parsed = urlsplit(text)
            if parsed.netloc not in ("", "localhost"):
                raise AssertionError("测试不得访问远程SQLite URI")
            query = parsed.query
            if parse_qs(query).get("mode") == ["memory"]:
                return database, False
            text = unquote(parsed.path)
            if os.name == "nt" and re.match(r"^/[a-zA-Z]:[/\\]", text):
                text = text[1:]
            if text == ":memory:":
                return database, False
        # 先检查默认路径自身的父目录，再解析文件；否则正式库是文件链接时会漏过。
        path = Path(os.path.abspath(text))
        if path.parent.resolve() != self.root:
            path = path.resolve()
        if path.parent.resolve() != self.root or path.suffix.lower() not in (".db", ".sqlite", ".sqlite3"):
            return database, False
        target = self.library_database if path.name.lower() == "library.db" else self.directory / ("default-" + path.name)
        # 防止后续改动把数据库路径拼回正式目录；这里只操作当前测试目录中的文件。
        if target.parent.resolve() != self.directory:
            raise AssertionError("测试数据库不能离开当前测试目录")
        if is_uri:
            return target.as_uri() + ("?" + query if query else ""), True
        return target, True

    def connect(self, database, *args, **kwargs):
        uri = kwargs.get("uri", args[6] if len(args) > 6 else False)
        target, redirected = self.target(database, uri=uri)
        if redirected:
            self.redirected_calls += 1
        return self.connector(target, *args, **kwargs)
