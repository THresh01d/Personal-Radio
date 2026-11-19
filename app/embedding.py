"""embedding.py - 模型加载与文本向量化公共逻辑

build_library.py（建库）与 main.py（查询）共用同一份模型加载代码，
保证「入库时如何向量化」与「查询时如何向量化」完全一致——否则相似度检索会失真。

设计要点：
1. 优先加载本地 BGE 模型目录（离线、快、不依赖网络），失败再回退在线下载；
2. 全局单例 + 锁：首次请求时懒加载，之后常驻内存复用；
3. BGE 官方推荐推理时做 L2 归一化，归一化后「余弦相似度 == 内积」，
   查询侧只需一次矩阵乘法即可算出全部相似度。
"""

import os
import hashlib
import threading
from pathlib import Path

from app.paths import load_project_env
from app.diagnostics import timed, stage

ROOT = Path(__file__).resolve().parent.parent
load_project_env()

# 跨机器顺序：用户显式路径 → 仓库可选 models/ 目录 → Hugging Face 下载。
# 不再把开发者电脑上的绝对路径写进代码，否则克隆者永远命中失败。
CONFIGURED_MODEL_PATH = os.getenv("BGE_MODEL_PATH", "").strip()
REPO_MODEL_PATH = ROOT / "models" / "BAAI" / "bge-small-zh-v1.5"
# 回退方案：本地缺失时从 HuggingFace 在线拉取
FALLBACK_MODEL_NAME = "BAAI/bge-small-zh-v1.5"

_model = None          # 模型单例缓存
_model_lock = threading.Lock()  # 保护首次加载，防止并发重复初始化
_model_identity = None
_model_identity_ref = None


def local_model_identity(path):
    """加载时指纹化本地文件，不在每首编码时重复读取权重，不保存机器绝对路径。"""
    digest = hashlib.sha256()
    for file in sorted(p for p in Path(path).rglob("*") if p.is_file()):
        digest.update(file.relative_to(path).as_posix().encode())
        content = hashlib.sha256()
        with file.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                content.update(chunk)
        digest.update(content.hexdigest().encode())
    return {"model_id": "local:" + Path(path).name, "files_sha256": digest.hexdigest()}


def get_model_identity():
    """只报告本进程实际加载记录；替身或旧单例无记录时诚实返回 unknown。"""
    if _model is None or _model_identity is None or _model_identity_ref is not _model:
        return {"model_id": "unknown", "files_sha256": None}
    return dict(_model_identity)


@timed("model_load")
def load_model():
    """懒加载并缓存模型单例（线程安全）。"""
    global _model, _model_identity, _model_identity_ref
    if _model is not None:
        return _model
    with _model_lock:
        if _model is not None:  # 双检锁：等锁期间可能已被别的线程加载
            return _model
        from sentence_transformers import SentenceTransformer
        configured = Path(CONFIGURED_MODEL_PATH).expanduser() if CONFIGURED_MODEL_PATH else None
        local_path = configured if configured and configured.exists() else REPO_MODEL_PATH
        if local_path.exists():
            print(f"加载本地模型: {local_path}")
            identity = local_model_identity(local_path)
            _model = SentenceTransformer(str(local_path))
            after = local_model_identity(local_path)
            _model_identity = identity if after == identity else {"model_id": identity["model_id"], "files_sha256": None}
        else:
            if configured:
                print(f"配置的 BGE_MODEL_PATH 不存在: {configured}")
            print(f"本地模型不存在，回退在线模型: {FALLBACK_MODEL_NAME}")
            _model = SentenceTransformer(FALLBACK_MODEL_NAME)
            # 在线名称不是已核对的本地权重指纹，不能伪造 files_sha256。
            _model_identity = {"model_id": FALLBACK_MODEL_NAME, "files_sha256": None}
        _model_identity_ref = _model
        return _model


def embed_texts(texts):
    """批量编码文本并做 L2 归一化，返回 (N, 512) float32 矩阵。

    normalize_embeddings=True 由 sentence-transformers 内部完成，
    与建库脚本保持一致，确保点积即余弦相似度。
    """
    model = load_model()
    with stage("embedding"):
        vectors = model.encode(list(texts), normalize_embeddings=True)
    return vectors
