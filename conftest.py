"""conftest.py - 让 pytest 从项目根目录找到 app / scripts / eval 包。

pytest 默认把测试文件所在目录加进 sys.path，不一定会加项目根，
这里显式把根目录插进去，保证 `from app.xxx import ...` 在任意运行目录都可用。
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
