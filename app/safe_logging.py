"""诊断仅记固定事件、异常类型和状态，不依赖正则猜测所有可能的密钥。"""
import logging
import re


def log_failure(event, exc):
    from app.diagnostics import current_summary
    diagnostic = current_summary()
    status = getattr(getattr(exc, "response", None), "status_code", None)
    logging.getLogger("personal_radio").warning("%s type=%s status=%s request_id=%s", event, type(exc).__name__, status,
                                               diagnostic["request_id"] if diagnostic else "none")


class PrivateAccessFilter(logging.Filter):
    def filter(self, record):
        # Uvicorn 的访问日志含原始 query（扫码 key、报幕文字），只保留路由。
        if isinstance(record.args, tuple) and len(record.args) == 5:
            args = list(record.args)
            path = str(args[2]).split("?", 1)[0]
            path = re.sub(r"(/api/(?:sessions|recommendations)/)[^/]+", r"\1*", path)
            args[2] = path
            record.args = tuple(args)
        return True


def install_access_filter():
    logger = logging.getLogger("uvicorn.access")
    if not any(isinstance(item, PrivateAccessFilter) for item in logger.filters):
        logger.addFilter(PrivateAccessFilter())
