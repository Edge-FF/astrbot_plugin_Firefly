"""身份解析器：把设置解析为可注入的身份。

纯逻辑：不读盘、不写盘、不依赖 astrbot，只依赖 `MaterialRegistry` 的检索接口，
可脱离 IO 单测（用一个内存注册表或临时目录构建真注册表均可）。

解析永不抛异常：任何无法定位/为空的身份都退化为 `ResolvedUserRole()`（无身份），
并附带告警文本。外壳注入链路的正确性依赖这一约定（DESIGN_user_role.md §6.2）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .. import consts
from ..models import MaterialEntry
from .models import (
    MODE_CUSTOM,
    ORIGIN_CUSTOM,
    ORIGIN_EXISTING,
    ORIGIN_FALLBACK,
    ORIGIN_NONE,
    ResolvedUserRole,
    UserRoleSetting,
    normalize_mode,
)

if TYPE_CHECKING:
    from ..materials.registry import MaterialRegistry


class UserRoleResolver:
    """把 `UserRoleSetting` + 资料注册表解析为 `ResolvedUserRole`。"""

    def __init__(
        self,
        default_id: str = consts.DEFAULT_USER_ROLE_ID,
        logger: Any = None,
    ) -> None:
        """初始化解析器。

        Args:
            default_id: 内置默认身份的条目 id。
            logger: 可选的日志记录器；解析失败时留痕。
        """
        self._default_id = default_id
        self._logger = logger

    def resolve(
        self,
        setting: UserRoleSetting,
        registry: MaterialRegistry,
    ) -> ResolvedUserRole:
        """解析设置，返回可注入身份。

        回退链：custom 失效 → 内置默认 → 无。每一级都带告警，不静默。

        Args:
            setting: 身份设置。
            registry: 资料注册表（`get` 会触发懒加载，判空前必须走它）。

        Returns:
            解析结果；无有效身份时 `entry` 为 None 且 `pin_id` 为空。
        """
        if normalize_mode(setting.mode) == MODE_CUSTOM:
            return self._resolve_custom(setting, registry)
        return self._resolve_existing(setting, registry)

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    def _resolve_custom(
        self, setting: UserRoleSetting, registry: MaterialRegistry
    ) -> ResolvedUserRole:
        """解析自定义角色；失效时回退内置默认。"""
        role_id = setting.role_id
        if not role_id:
            return self._fallback(registry, "自定义模式未指定角色 id，已回退内置身份")

        entry = self._safe_get(registry, role_id)
        if self._valid(entry):
            return ResolvedUserRole(
                pin_id=role_id,
                entry=entry,
                origin=ORIGIN_CUSTOM,
            )
        return self._fallback(
            registry,
            f"自定义角色 {role_id!r} 不存在或正文为空，已回退内置身份",
        )

    def _resolve_existing(
        self, setting: UserRoleSetting, registry: MaterialRegistry
    ) -> ResolvedUserRole:
        """解析现有人物；缺省 id 时使用内置默认。"""
        role_id = setting.role_id or self._default_id
        entry = self._safe_get(registry, role_id)
        if self._valid(entry):
            return ResolvedUserRole(
                pin_id=role_id,
                entry=entry,
                origin=ORIGIN_EXISTING,
            )
        return self._no_identity(f"身份 {role_id!r} 不存在或正文为空，本次不注入身份块")

    def _fallback(self, registry: MaterialRegistry, warning: str) -> ResolvedUserRole:
        """回退到内置默认身份。"""
        entry = self._safe_get(registry, self._default_id)
        if self._valid(entry):
            return ResolvedUserRole(
                pin_id=self._default_id,
                entry=entry,
                origin=ORIGIN_FALLBACK,
                warning=warning,
            )
        return self._no_identity(f"{warning}；内置默认身份也不可用")

    def _no_identity(self, warning: str) -> ResolvedUserRole:
        """构造无身份结果并留痕（正常降级路径，记 debug 即可）。"""
        if self._logger is not None:
            self._logger.debug(f"[用户角色] {warning}")
        return ResolvedUserRole(
            pin_id="",
            entry=None,
            origin=ORIGIN_NONE,
            warning=warning,
        )

    def _safe_get(
        self, registry: MaterialRegistry, entry_id: str
    ) -> MaterialEntry | None:
        """带降级保护的 `registry.get`：读取失败一律按"无此条目"处理。

        `get` 会触发懒加载读盘，而 `registry._read_file` 只捕获 `OSError`；
        非 UTF-8 文档抛出的 `UnicodeDecodeError` 属于 `ValueError`，会穿透它。
        解析契约要求永不抛异常（否则会连带中断整段外壳注入），故在此统一兜底。
        兜底只针对 `Exception`：`CancelledError` 继承 `BaseException`，不受影响。

        Args:
            registry: 资料注册表。
            entry_id: 待读取的条目 id。

        Returns:
            条目；不存在或读取失败时返回 None。
        """
        try:
            return registry.get(entry_id)
        except Exception as exc:
            if self._logger is not None:
                self._logger.debug(
                    f"[用户角色] 读取身份条目 {entry_id!r} 失败，按无效处理：{exc!r}"
                )
            return None

    @staticmethod
    def _valid(entry: MaterialEntry | None) -> bool:
        """判断条目是否为可用身份（存在且正文非空）。

        `registry.get` 已触发懒加载，因此这里的 `is_empty()` 判定的是真实正文，
        而不是 `content is None` 的未加载态。

        Args:
            entry: 待判定的条目。

        Returns:
            可注入时返回 True。
        """
        return entry is not None and not entry.is_empty()
