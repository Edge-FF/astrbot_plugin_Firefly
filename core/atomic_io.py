"""原子文件写入工具。

同目录临时文件 + `flush` + `fsync` + `os.replace`，避免进程崩溃或断电时留下半文件。
临时文件名以 `.` 开头且后缀非 `.md`，即使崩溃残留也不会被 role 资料树收录。

本模块为纯工具：只依赖标准库，不属于任何领域，可被不同持久化模块共用。
写入失败一律抛 `OSError`，由调用方按各自的日志语义告警（有的静默降级、有的告警）。
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

_JSON_OPTS: dict[str, Any] = {"ensure_ascii": False, "indent": 2}


def atomic_write_text(path: Path, text: str) -> None:
    """原子写入文本（UTF-8、`\\n` 换行）。

    Args:
        path: 目标文件路径；父目录不存在时自动创建。
        text: 要写入的完整文本。

    Raises:
        OSError: 写入或替换失败（原文件保持不变，临时文件被清理）。
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        newline="\n",
        dir=str(target.parent),
        prefix="." + target.name + ".",
        suffix=".tmp",
        delete=False,
    )
    temp_path = Path(handle.name)
    try:
        with handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, target)
    except BaseException:
        temp_path.unlink(missing_ok=True)
        raise


def atomic_write_json(path: Path, payload: Any) -> None:
    """原子写入 JSON（UTF-8、不转义非 ASCII、缩进 2）。

    Args:
        path: 目标文件路径。
        payload: 可 JSON 序列化的对象。

    Raises:
        OSError: 写入或替换失败。
    """
    atomic_write_text(path, json.dumps(payload, **_JSON_OPTS))
