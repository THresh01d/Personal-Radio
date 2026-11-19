"""本进程聊天提交顺序：新需求开始后，旧请求不能再回写任务或创建推荐批次。"""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
import threading


@dataclass
class _SessionTurns:
    lock: object = field(default_factory=threading.RLock)
    active: int = 0
    latest: object = None


_sessions = {}
_sessions_lock = threading.Lock()
_current = ContextVar('chat_turn', default=None)


@contextmanager
def turn(session_id):
    # 只保存尚有在途请求的会话；最后一轮退出即删除，不形成持久画像或无限增长的缓存。
    with _sessions_lock:
        entry = _sessions.setdefault(session_id, _SessionTurns())
        entry.active += 1
    identity = object()
    with entry.lock:
        entry.latest = identity
    token = _current.set((entry, identity))
    try:
        yield
    finally:
        _current.reset(token)
        with _sessions_lock:
            entry.active -= 1
            if not entry.active:
                del _sessions[session_id]


@contextmanager
def commit_guard():
    """同会话的检查和落库共用锁；其他会话不被这次提交串行阻塞。"""
    current = _current.get()
    if current is None:
        # 兼容既有非聊天调用和纯函数测试；HTTP聊天由服务入口统一建立轮次。
        yield True
        return
    entry, identity = current
    with entry.lock:
        yield entry.latest is identity
