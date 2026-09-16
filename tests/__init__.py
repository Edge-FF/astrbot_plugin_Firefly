"""测试包初始化。

把插件所在目录（data/plugins）与 AstrBot 项目根目录加入 sys.path：
- 插件以命名空间包 `astrbot_plugin_Firefly` 导入（与 AstrBot 的加载方式一致）；
- adapter 层测试需要 import astrbot，由项目根目录提供。
纯 core 层测试不依赖 astrbot，可在任意 Python 3.10+ 环境运行。
"""

from __future__ import annotations

import sys
from pathlib import Path

_PLUGINS_ROOT = Path(__file__).resolve().parents[2]  # tests -> 插件目录 -> data/plugins
_PROJECT_ROOT = Path(__file__).resolve().parents[4]  # ... -> data -> 项目根目录

for _path in (_PLUGINS_ROOT, _PROJECT_ROOT):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))
