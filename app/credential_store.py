"""网易云凭据迁移：回读验证后才替换旧文件，绝不回退新明文存储。"""

import hashlib
import json
import os
import tempfile
import threading
import uuid
from pathlib import Path

from app.paths import DATA_ROOT, APP_ROOT

SERVICE = "Personal-Radio"
CREDENTIAL_IDENTITY = DATA_ROOT
if DATA_ROOT != APP_ROOT:
    # 桌面目录迁移后，凭据仍使用旧目录对应的 Windows 凭据库命名。
    CREDENTIAL_IDENTITY = Path(os.getenv("PERSONAL_RADIO_CREDENTIAL_IDENTITY") or DATA_ROOT).resolve()
    profile = hashlib.sha256(os.path.normcase(str(CREDENTIAL_IDENTITY)).encode()).hexdigest()[:16]
    SERVICE = f"Personal-Radio-Desktop-{profile}"
CHUNK_SIZE = 1000  # Windows 单条凭据有字节上限；Cookie 按小段保存，避免长登录态丢失。
MAX_CHUNKS = 64
_lock = threading.RLock()


class CredentialUnavailable(RuntimeError):
    pass


def _backend():
    import keyring
    # 不接受第三方明文文件后端，不能把“用了 keyring”当作已经加密。
    module = type(keyring.get_keyring()).__module__
    if not module.startswith(("keyring.backends.Windows", "keyring.backends.macOS",
                              "keyring.backends.SecretService", "keyring.backends.libsecret",
                              "keyring.backends.kwallet")):
        raise CredentialUnavailable("系统安全凭据库不可用")
    return keyring


def _read_secret(account):
    return _backend().get_password(SERVICE, account)


def _write_secret(account, value):
    _backend().set_password(SERVICE, account, value)


def _delete_secret(account):
    _backend().delete_password(SERVICE, account)


def _prefix(path, namespace="netease"):
    target = Path(path).resolve()
    if CREDENTIAL_IDENTITY != DATA_ROOT and target.is_relative_to(DATA_ROOT):
        target = CREDENTIAL_IDENTITY / target.relative_to(DATA_ROOT)
    return namespace + ":" + hashlib.sha256(str(target).encode()).hexdigest()[:24] + ":"


def _accounts(path, data, namespace="netease"):
    version = data.get("credential_version", "")
    count = data.get("credential_chunks", 0)
    if (not isinstance(version, str) or len(version) != 32
            or any(c not in "0123456789abcdef" for c in version)
            or not isinstance(count, int) or not 1 <= count <= MAX_CHUNKS):
        return []
    return [f"{_prefix(path, namespace)}{version}:{i}" for i in range(count)]


def _file_data(path):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (ValueError, OSError):
        return {}


def _atomic_metadata(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # 临时文件只含非密钥元数据；原子替换失败时旧文件仍可恢复。
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary and temporary.exists():
            temporary.unlink()


def _discard(accounts):
    for account in accounts:
        try:
            _delete_secret(account)
        except Exception:
            pass  # 遗留安全凭据不阻塞登录；文件清单已不会再引用它。


def save(path, data):
    metadata = {key: data.get(key) for key in ("nickname", "uid", "saved_at")}
    save_secret_blob(path, data.get("cookie"), namespace="netease", metadata=metadata)


def save_secret_blob(path, value, *, namespace, metadata=None):
    with _lock:
        if not isinstance(value, str) or not value or len(value) > CHUNK_SIZE * MAX_CHUNKS:
            raise CredentialUnavailable("登录凭据格式无效")
        old = _file_data(path)
        metadata = dict(metadata or {})
        metadata.update(storage="keyring", credential_version=uuid.uuid4().hex,
                        credential_chunks=(len(value) + CHUNK_SIZE - 1) // CHUNK_SIZE)
        accounts = _accounts(path, metadata, namespace)
        try:
            for i, account in enumerate(accounts):
                _write_secret(account, value[i * CHUNK_SIZE:(i + 1) * CHUNK_SIZE])
            restored = "".join(_read_secret(account) or "" for account in accounts)
            if restored != value:
                raise CredentialUnavailable("登录凭据回读验证失败")
            _atomic_metadata(path, metadata)
        except Exception as exc:
            _discard(accounts)
            raise CredentialUnavailable("系统凭据库暂不可用，原登录凭据已保留；请稍后重试") from exc
        _discard(_accounts(path, old, namespace))


def load_secret_blob(path, *, namespace):
    with _lock:
        data = _file_data(path)
        accounts = _accounts(path, data, namespace)
        if data.get("storage") != "keyring" or not accounts:
            return None
        try:
            chunks = [_read_secret(account) for account in accounts]
            return "".join(chunks) if all(isinstance(chunk, str) and chunk for chunk in chunks) else None
        except Exception:
            return None


def load(path):
    with _lock:
        data = _file_data(path)
        if isinstance(data.get("cookie"), str) and data["cookie"]:
            # 只有安全存储成功且回读一致后才去掉明文；任何失败均保留原文件。
            try:
                save(path, data)
                return {**data, "storage": "keyring"}
            except CredentialUnavailable:
                return {**data, "storage": "legacy-file"}
        accounts = _accounts(path, data)
        if data.get("storage") != "keyring" or not accounts:
            return None
        try:
            chunks = [_read_secret(account) for account in accounts]
            if not all(isinstance(chunk, str) and chunk for chunk in chunks):
                return None
            return {**data, "cookie": "".join(chunks)}
        except Exception:
            return None


def clear(path, *, namespace="netease"):
    with _lock:
        data = _file_data(path)
        try:
            for account in _accounts(path, data, namespace):
                if _read_secret(account):
                    _delete_secret(account)
        except Exception as exc:
            # 保留无明文的清单，用户重试退出时仍知道哪些分段需要删除。
            raise CredentialUnavailable("登录已退出，但系统凭据清理未完成，请重试") from exc
        Path(path).unlink(missing_ok=True)
