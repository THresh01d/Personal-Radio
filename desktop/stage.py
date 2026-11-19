"""从公开源码和锁定依赖准备 Electron 的只读运行文件。"""

import argparse
from pathlib import Path
import shutil
import struct
import subprocess
import sys


ROOT = Path(__file__).resolve().parent.parent
# 保持直接运行 desktop/stage.py 的入口，从仓库内加载共用代码。
sys.path.insert(0, str(ROOT / "scripts" / "packaging"))
from radio_runtime import ROOT_DEPENDENCIES, copy_runtime, dependency_closure, node_license_file

GATEWAY_FILES = (
    "app.js", "security.js", "qq-music.js", "safe-logging.js", "playback-diagnostics.js", "package.json", "package-lock.json",
)
BLOCKED_NAMES = {
    ".env", ".git", ".netease_credential.json", ".qqmusic", ".chatgpt_credentials.json", ".chatgpt_host.json",
    "my_favorites_full.csv",
}
IGNORE = shutil.ignore_patterns(*BLOCKED_NAMES, "__pycache__", "*.pyc", "*.db", "*.db-*", "*.sqlite", "*.sqlite3")
MODEL_FILES = {
    "1_Pooling/config.json", "config_sentence_transformers.json", "config.json", "model.safetensors",
    "modules.json", "README.md", "sentence_bert_config.json", "special_tokens_map.json",
    "tokenizer_config.json", "tokenizer.json", "vocab.txt",
}


def stage(model):
    if sys.platform != "win32" or struct.calcsize("P") != 8 or sys.version_info[:2] != (3, 12):
        raise RuntimeError("请在 Windows x64 使用 Python 3.12 构建。")
    if (ROOT/"frontend/src/components/scene/musicRain/rainform").exists():
        raise RuntimeError("请从已清理第三方雨幕的公开源码构建安装包。")
    staging = ROOT/"desktop/.stage/radio-runtime"
    if staging.exists():
        raise FileExistsError("运行文件暂存目录已存在，请在新构建目录运行。")
    entry = ROOT/"static/dist/index.html"
    if not entry.is_file():
        raise FileNotFoundError("请先构建 Vue 前端。")
    if not (ROOT/"gateway/node_modules/NeteaseCloudMusicApi/package.json").is_file():
        raise FileNotFoundError("请先用 npm ci 安装网易云网关的锁定依赖。")
    if not (ROOT/"gateway/node_modules/@yakult-green-tea/qq-music-api/package.json").is_file():
        raise FileNotFoundError("请先用 npm ci 安装 QQ 音乐网关的锁定依赖。")
    model = model.resolve()
    for filename in MODEL_FILES:
        if not (model/filename).is_file():
            raise FileNotFoundError(f"BGE 模型缺少 {filename}")
    node = shutil.which("node")
    if not node:
        raise FileNotFoundError("构建电脑缺少 Node.js。")
    node_version = subprocess.check_output([node, "--version"], text=True).strip()
    node_license = node_license_file(node_version)
    if not node_license.is_file():
        raise FileNotFoundError("请使用 Node.js 24.16.0 x64 以及仓库附带的该版本许可证。")
    # JWT 签名校验是桌面版的额外运行依赖。
    distributions = dependency_closure((*ROOT_DEPENDENCIES, "PyJWT", "cryptography"))
    application = staging/"application"
    application.mkdir(parents=True)
    shutil.copytree(ROOT/"app", application/"app", ignore=IGNORE)
    shutil.copytree(ROOT/"static/dist", application/"static/dist", ignore=IGNORE)
    shutil.copyfile(ROOT/"requirements.txt", application/"requirements.txt")
    shutil.copyfile(ROOT/"desktop/server.py", application/"desktop-server.py")
    shutil.copyfile(ROOT/"desktop/PUBLIC_LICENSE.txt", application/"LICENSE")
    for filename in ("vue", "three"):
        license_file = ROOT/"frontend/node_modules"/filename/"LICENSE"
        shutil.copyfile(license_file, application/(filename + "-LICENSE.txt"))
    gateway = application/"gateway"
    gateway.mkdir()
    for filename in GATEWAY_FILES:
        shutil.copyfile(ROOT/"gateway"/filename, gateway/filename)
    shutil.copytree(ROOT/"gateway/node_modules", gateway/"node_modules", ignore=IGNORE)
    bundled_model = application/"models/BAAI/bge-small-zh-v1.5"
    for filename in sorted(MODEL_FILES):
        target = bundled_model/filename
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(model/filename, target)
    shutil.copyfile(ROOT/"desktop/BGE-LICENSE.txt", bundled_model/"LICENSE")
    (staging/"node").mkdir()
    shutil.copyfile(node, staging/"node/node.exe")
    shutil.copyfile(node_license, staging/"node/LICENSE.txt")
    copy_runtime(staging/"python", distributions)
    for filename in staging.rglob("*"):
        if filename.is_file() and (filename.name in BLOCKED_NAMES
                or filename.suffix in {".db", ".db-wal", ".db-shm", ".sqlite", ".sqlite3"}):
            raise RuntimeError(f"发现禁止打包的文件：{filename.relative_to(staging)}")
    print(f"运行文件已准备好：{staging}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", type=Path, required=True)
    stage(parser.parse_args().model)
