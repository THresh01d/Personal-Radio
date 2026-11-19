"""桌面版和旧试用版共用的运行时打包逻辑。"""

import importlib.metadata as metadata
from pathlib import Path
import shutil
import sys

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name


ROOT_DEPENDENCIES = (
    "fastapi", "uvicorn", "sentence-transformers", "numpy", "python-dotenv",
    "httpx", "pydantic", "keyring", "edge-tts",
)
IGNORED = shutil.ignore_patterns("__pycache__", "*.pyc", ".git", ".env", "*.db", "*.db-wal", "*.db-shm")


def node_license_file(version):
    return Path(__file__).resolve().with_name(f"Node-{version}-LICENSE.txt")


def dependency_closure(roots):
    """只携带运行依赖 RECORD 文件，不复制开发者整个 site-packages。"""
    found, visited = {}, set()
    pending = [(name, frozenset()) for name in roots]
    while pending:
        name, extras = pending.pop()
        distribution = metadata.distribution(name)
        key = canonicalize_name(distribution.metadata["Name"])
        if (key, extras) in visited:
            continue
        visited.add((key, extras))
        found[key] = distribution
        for raw in distribution.requires or ():
            req = Requirement(raw)
            if req.marker is None or any(req.marker.evaluate({"extra": extra}) for extra in (set(extras) | {""})):
                pending.append((req.name, frozenset(req.extras)))
    return found


def copy_runtime(destination, distributions):
    base = Path(sys.base_prefix)
    destination.mkdir(parents=True)
    for name in ("python.exe", "pythonw.exe", "python3.dll", "python312.dll",
                 "vcruntime140.dll", "vcruntime140_1.dll", "LICENSE.txt"):
        source = base / name
        if source.is_file():
            shutil.copy2(source, destination / name)
    shutil.copytree(base / "DLLs", destination / "DLLs", ignore=IGNORED)
    shutil.copytree(base / "Lib", destination / "Lib", ignore=shutil.ignore_patterns(
        "site-packages", "__pycache__", "*.pyc", "test", "tests", "idlelib", "tkinter", "ensurepip"))
    site = destination / "Lib/site-packages"
    site.mkdir()
    copied = set()
    for name, dist in sorted(distributions.items()):
        print("依赖：", name, dist.version, flush=True)
        origin = Path(dist.locate_file("")).resolve()
        for relative in dist.files or ():
            # RECORD 的 ../Scripts 不是电台运行所需文件，禁止越界复制。
            if (relative.is_absolute() or ".." in relative.parts or relative.suffix == ".pyc"
                    or "__pycache__" in relative.parts or relative.parts[:2] == ("torch", "include")):
                continue
            source = Path(dist.locate_file(relative))
            if not source.is_file() or not source.resolve().is_relative_to(origin):
                continue
            target = site / relative
            if target in copied:
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            copied.add(target)
