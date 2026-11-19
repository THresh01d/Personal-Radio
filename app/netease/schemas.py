"""平台HTTP请求模型：与网关进程和数据库操作无关。"""
from typing import Literal
from pydantic import BaseModel

class LoginSaveRequest(BaseModel):
    """手动保存登录凭据的请求体（check 之外的后备入口）。"""
    cookie: str
    nickname: str = ""
    uid: int = 0


class SyncRequest(BaseModel):
    """用户选择的同步范围。

    默认仍同步「我喜欢的音乐」，兼容现有前端；新引导页可以传 selected/all。
    replace=true 用于用户明确要求重建个人曲库，默认合并以免误删已有档案。
    """

    scope: Literal["liked", "selected", "all", "metadata"] = "liked"
    playlist_ids: list[str] | None = None
    replace: bool = False
