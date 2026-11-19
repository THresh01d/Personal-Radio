"""安装后的可写数据目录与只读程序文件分开。"""

import os
from pathlib import Path

from dotenv import load_dotenv

APP_ROOT = Path(__file__).resolve().parent.parent
_data_override = os.getenv("PERSONAL_RADIO_DATA_DIR")
DATA_ROOT = Path(_data_override).expanduser().resolve() if _data_override else APP_ROOT
if _data_override:
    DATA_ROOT.mkdir(parents=True, exist_ok=True)


def load_project_env():
    # 桌面版只采用用户在应用内保存的配置，开发版保留 .env。
    if not _data_override:
        load_dotenv(APP_ROOT / ".env")
