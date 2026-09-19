"""身份设置仓库：持久化全局默认与会话覆盖。

与 `StateStore`（动态认知状态）**物理隔离**：`/firefly reset` 清空动态状态时
不得带走用户身份设置（DESIGN_user_role.md §11.1）。

约定：
- 会话 id 为 unified_msg_origin；读为内存同步（注入热路径每轮调用），写为异步。
- 写盘采用临时文件 + `os.replace` 原子替换，全部可变操作由 asyncio.Lock 串行化。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from ..atomic_io import atomic_write_json
from .models import UserRoleSetting

_FILE_VERSION = 1


class UserRoleStore:
    """用户身份设置仓库（默认 + 会话覆盖）。"""

    def __init__(
        self,
        data_file: Path,
        persist: bool = True,
        default: UserRoleSetting | None = None,
        logger: Any = None,
    ) -> None:
        """初始化仓库。

        Args:
            data_file: 持久化文件路径（data/user_role.json）。
            persist: 是否启用落盘持久化。
            default: 初始全局默认；None 表示 `UserRoleSetting()`（内置默认身份）。
            logger: 可选的日志记录器；落盘失败时告警，避免静默丢数据。
        """
        self._data_file = Path(data_file)
        self._persist = persist
        self._logger = logger
        self._lock = asyncio.Lock()
        self._default: UserRoleSetting = default or UserRoleSetting()
        self._sessions: dict[str, UserRoleSetting] = {}

    # ------------------------------------------------------------------
    # 加载 / 落盘
    # ------------------------------------------------------------------

    async def load(self) -> list[str]:
        """从磁盘加载设置，返回告警列表。

        文件不存在视为"尚未设置"，静默使用默认值；文件损坏时回退默认并告警，
        绝不抛异常——身份设置损坏不应影响插件启动。

        Returns:
            告警列表（无文件时为空）。
        """
        warnings: list[str] = []
        if not self._data_file.is_file():
            return warnings
        try:
            raw = self._data_file.read_text(encoding="utf-8")
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise ValueError("设置文件根节点不是对象")
            self._default = UserRoleSetting.from_dict(data.get("default"))
            sessions = data.get("sessions")
            self._sessions = {}
            if isinstance(sessions, dict):
                for session_id, item in sessions.items():
                    if isinstance(item, dict):
                        self._sessions[str(session_id)] = UserRoleSetting.from_dict(
                            item
                        )
        except (OSError, ValueError, TypeError) as exc:
            warnings.append(f"用户身份设置加载失败，已回退默认：{exc}")
            self._default = UserRoleSetting()
            self._sessions = {}
        return warnings

    async def save(self) -> None:
        """原子写入磁盘（persist=False 时为空操作）。"""
        if not self._persist:
            return
        async with self._lock:
            payload = {
                "version": _FILE_VERSION,
                "default": self._default.to_dict(),
                "sessions": {
                    session_id: setting.to_dict()
                    for session_id, setting in self._sessions.items()
                },
            }
        try:
            atomic_write_json(self._data_file, payload)
        except OSError as exc:
            # 落盘失败必须可见：否则用户会以为设置已保存，实际已丢失
            if self._logger is not None:
                self._logger.warning(
                    f"[认知外壳] 用户身份设置落盘失败，本次未持久化：{exc}"
                )

    async def close(self) -> None:
        """插件终止时的最终落盘。"""
        if self._persist:
            await self.save()

    # ------------------------------------------------------------------
    # 读（同步，供注入热路径）
    # ------------------------------------------------------------------

    def default_setting(self) -> UserRoleSetting:
        """返回全局默认设置。

        Returns:
            全局默认身份设置。
        """
        return self._default

    def session_setting(self, session_id: str) -> UserRoleSetting | None:
        """返回指定会话的覆盖设置。

        Args:
            session_id: 会话唯一标识。

        Returns:
            会话覆盖；未设置时返回 None。
        """
        return self._sessions.get(session_id)

    def effective(self, session_id: str) -> UserRoleSetting:
        """返回指定会话实际生效的设置（会话覆盖优先，否则全局默认）。

        Args:
            session_id: 会话唯一标识。

        Returns:
            生效中的身份设置。
        """
        return self._sessions.get(session_id, self._default)

    def all_sessions(self) -> dict[str, UserRoleSetting]:
        """返回全部会话覆盖的副本。

        Returns:
            会话 id → 覆盖设置 的字典副本。
        """
        return dict(self._sessions)

    # ------------------------------------------------------------------
    # 写（异步，持久化）
    # ------------------------------------------------------------------

    async def set_session(self, session_id: str, setting: UserRoleSetting) -> None:
        """设置某会话的身份覆盖。

        Args:
            session_id: 会话唯一标识。
            setting: 要保存的设置。
        """
        async with self._lock:
            self._sessions[session_id] = setting
        if self._persist:
            await self.save()

    async def clear_session(self, session_id: str) -> None:
        """清除某会话的身份覆盖（回落到全局默认，幂等）。

        Args:
            session_id: 会话唯一标识。
        """
        async with self._lock:
            self._sessions.pop(session_id, None)
        if self._persist:
            await self.save()

    async def set_default(self, setting: UserRoleSetting) -> None:
        """设置全局默认身份。

        Args:
            setting: 新的全局默认设置。
        """
        async with self._lock:
            self._default = setting
        if self._persist:
            await self.save()
