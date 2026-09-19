"""用户角色预设领域模型。

本模块为纯数据：不依赖 astrbot、不访问 IO，可独立单测。

概念区分（详见 DESIGN_user_role.md §3）：
- 资料（MaterialEntry）是全局资产，行为由 tier/kind 决定；
- 身份设置（UserRoleSetting）是会话级选择，只引用资料 id；
- 解析结果（ResolvedUserRole）是两者结合后、可交给组装器渲染的产物。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .. import consts
from ..models import MaterialEntry

MODE_EXISTING = consts.USER_ROLE_MODE_EXISTING
MODE_CUSTOM = consts.USER_ROLE_MODE_CUSTOM
_VALID_MODES = frozenset({MODE_EXISTING, MODE_CUSTOM})

# 解析来源
ORIGIN_EXISTING = "existing"  # 命中现有人物
ORIGIN_CUSTOM = "custom"  # 命中自定义角色文档
ORIGIN_FALLBACK = "fallback"  # 自定义失效，回退到内置默认
ORIGIN_NONE = "none"  # 无有效身份


def normalize_mode(value: Any) -> str:
    """把任意输入规范为合法模式，非法值回退 `MODE_EXISTING`。

    设置来自手写 JSON 或 HTTP 入参，脏值不应让解析失败：无法识别时一律
    按"选现有人物"处理，最保守且不会凭空创造身份。

    Args:
        value: 原始模式值。

    Returns:
        `MODE_EXISTING` 或 `MODE_CUSTOM`。
    """
    text = str(value or "").strip().lower()
    return text if text in _VALID_MODES else MODE_EXISTING


@dataclass(frozen=True)
class UserRoleSetting:
    """一条身份设置。

    `role_id` 为空且 `mode == MODE_EXISTING` 时，表示"使用内置默认身份"，
    由解析器映射到 `consts.DEFAULT_USER_ROLE_ID`。
    """

    mode: str = MODE_EXISTING
    role_id: str = ""

    def to_dict(self) -> dict[str, str]:
        """序列化为可持久化字典。

        Returns:
            `{"mode": ..., "role_id": ...}`。
        """
        return {"mode": self.mode, "role_id": self.role_id}

    @classmethod
    def from_dict(cls, data: Any) -> UserRoleSetting:
        """从字典反序列化，缺失/脏值按默认设置处理。

        Args:
            data: `to_dict()` 产生的字典，可为 None 或非字典。

        Returns:
            还原后的设置（非法 mode 归一化为 `MODE_EXISTING`）。
        """
        if not isinstance(data, dict):
            return cls()
        return cls(
            mode=normalize_mode(data.get("mode")),
            role_id=str(data.get("role_id") or "").strip(),
        )


@dataclass(frozen=True)
class ResolvedUserRole:
    """解析后的可注入身份。

    `pin_id` 同时承担两个用途：命中的资料 id（用于取正文）与路由排除键
    （避免该条目被重复激活）。`entry` 为 None 表示无有效身份。
    """

    pin_id: str = ""
    entry: MaterialEntry | None = None
    origin: str = ORIGIN_NONE
    warning: str = ""

    @property
    def has_entry(self) -> bool:
        """是否存在可注入的身份正文。

        Returns:
            有有效条目时为 True。
        """
        return self.entry is not None
