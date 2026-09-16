"""动态状态存储 v0.2：按会话隔离，原子落盘。

支持 v0.2 扩展的 SessionState 字段序列化（ActiveContext 等）。
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from pathlib import Path

from .models import SessionState

_JSON_OPTS = {"ensure_ascii": False, "indent": 2}


class StateStore:
    """会话动态状态仓库。

    - key 为会话 UMO（unified_msg_origin），缺省时使用空字符串兜底。
    - 写入采用临时文件 + os.replace 原子替换。
    - 全部可变操作由 asyncio.Lock 串行化。
    """

    def __init__(self, data_file: Path, persist: bool = True) -> None:
        """初始化状态仓库。

        Args:
            data_file: 状态持久化文件路径。
            persist: 是否启用落盘持久化。
        """
        self._data_file = Path(data_file)
        self._persist = persist
        self._lock = asyncio.Lock()
        self._states: dict[str, SessionState] = {}

    async def load(self) -> list[str]:
        """从磁盘加载已有状态，返回告警列表。"""
        warnings: list[str] = []
        if not self._data_file.is_file():
            return warnings
        try:
            raw = self._data_file.read_text(encoding="utf-8")
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise ValueError("状态文件根节点不是对象")
            for session_id, item in data.items():
                if isinstance(item, dict):
                    self._states[str(session_id)] = SessionState.from_dict(item)
        except (OSError, ValueError, TypeError) as exc:
            warnings.append(f"动态状态加载失败，将使用空状态：{exc}")
            self._states.clear()
        return warnings

    async def get(self, session_id: str) -> SessionState:
        """获取指定会话的状态，不存在时返回默认状态。

        Args:
            session_id: 会话唯一标识。

        Returns:
            会话状态（默认状态或已存在的状态）。
        """
        async with self._lock:
            return self._states.get(session_id, SessionState(session_id=session_id))

    async def set(self, session_id: str, state: SessionState) -> None:
        """保存指定会话的状态，可选地立即落盘。

        Args:
            session_id: 会话唯一标识。
            state: 要保存的会话状态。
        """
        async with self._lock:
            self._states[session_id] = state
        if self._persist:
            await self.save()

    async def reset(self, session_id: str) -> SessionState:
        """重置指定会话的状态为默认值。

        Args:
            session_id: 会话唯一标识。

        Returns:
            重置后的默认会话状态。
        """
        async with self._lock:
            self._states.pop(session_id, None)
        if self._persist:
            await self.save()
        return SessionState(session_id=session_id)

    async def all(self) -> dict[str, SessionState]:
        """返回全部会话状态的副本（按会话 ID 索引）。"""
        async with self._lock:
            return dict(self._states)

    async def save(self) -> None:
        """原子写入磁盘。"""
        if not self._persist:
            return
        async with self._lock:
            payload = {
                session_id: state.to_dict()
                for session_id, state in self._states.items()
            }
        try:
            self._data_file.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp_path = tempfile.mkstemp(
                dir=str(self._data_file.parent),
                prefix=self._data_file.name,
                suffix=".tmp",
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    json.dump(payload, fh, **_JSON_OPTS)
                os.replace(tmp_path, self._data_file)
            finally:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
        except OSError:
            pass

    async def close(self) -> None:
        """插件终止时的最终落盘。"""
        if self._persist:
            await self.save()
