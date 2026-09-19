"""用户角色应用服务：组合仓库与解析器，作为注入链路的唯一入口。

职责边界：
- 对内组合 `UserRoleStore`（持久化）与 `UserRoleResolver`（解析）；
- 对外提供读（默认/会话设置、解析身份、路由排除集）与写（set/clear）两类操作；
- 不生成注入文本（由 `ShellBuilder` 负责），不碰 HTTP（由 adapter 负责）。

`resolve` 为同步方法：仓库读为内存同步、解析为纯逻辑，二者都不 await。
保持同步可避免在注入热路径上引入无意义的协程调度。
"""

from __future__ import annotations

from typing import Any

from ..materials.registry import MaterialRegistry
from .models import (
    MODE_EXISTING,
    ORIGIN_CUSTOM,
    ORIGIN_EXISTING,
    ResolvedUserRole,
    UserRoleSetting,
    normalize_mode,
)
from .resolver import UserRoleResolver
from .store import UserRoleStore

# 只有"直接命中"才算合法身份；fallback/none 说明请求的身份本身不可用
_VALID_ORIGINS = (ORIGIN_EXISTING, ORIGIN_CUSTOM)


class UserRoleService:
    """身份设置的读取、解析与写入门面。"""

    def __init__(
        self,
        store: UserRoleStore,
        registry: MaterialRegistry,
        resolver: UserRoleResolver | None = None,
        logger: Any = None,
    ) -> None:
        """初始化服务。

        Args:
            store: 身份设置仓库。
            registry: 资料注册表（解析身份正文）。
            resolver: 可选的解析器；缺省按内置默认 id 构造。
            logger: 可选的日志记录器，透传给默认解析器。
        """
        self._store = store
        self._registry = registry
        self._resolver = resolver or UserRoleResolver(logger=logger)

    # ------------------------------------------------------------------
    # 读与解析
    # ------------------------------------------------------------------

    def setting(self, session_id: str) -> UserRoleSetting:
        """返回会话实际生效的设置（覆盖优先，否则默认）。

        Args:
            session_id: 会话唯一标识。

        Returns:
            生效中的身份设置。
        """
        return self._store.effective(session_id)

    def default_setting(self) -> UserRoleSetting:
        """返回全局默认设置。

        Returns:
            全局默认身份设置。
        """
        return self._store.default_setting()

    def session_setting(self, session_id: str) -> UserRoleSetting | None:
        """返回会话覆盖设置（用于区分"默认值"与"显式覆盖"）。

        Args:
            session_id: 会话唯一标识。

        Returns:
            会话覆盖；未设置时为 None。
        """
        return self._store.session_setting(session_id)

    def resolve_setting(self, setting: UserRoleSetting) -> ResolvedUserRole:
        """解析给定设置（不落盘），供写前校验与预览。

        Args:
            setting: 待解析的设置。

        Returns:
            解析结果；无效身份时 `entry` 为 None。
        """
        return self._resolver.resolve(setting, self._registry)

    def validate_setting(
        self, setting: UserRoleSetting
    ) -> tuple[ResolvedUserRole, str]:
        """校验设置能否作为身份写入（API 与命令共用的唯一判定）。

        判定依据是"直接命中"而非 `has_entry`：自定义失效时会回退到内置默认，
        只看 `has_entry` 会把不可用的请求误判为合法。唯一豁免是
        `existing + 空 id`——它是"使用内置默认身份"的哨兵。

        Args:
            setting: 待校验的设置。

        Returns:
            `(解析结果, 错误信息)`；可用时错误信息为空串。
        """
        resolved = self.resolve_setting(setting)
        is_default_sentinel = (
            normalize_mode(setting.mode) == MODE_EXISTING and not setting.role_id
        )
        if not is_default_sentinel and resolved.origin not in _VALID_ORIGINS:
            return resolved, resolved.warning or "该身份不可用（不存在或正文为空）"
        return resolved, ""

    def resolve(self, session_id: str) -> ResolvedUserRole:
        """解析会话当前身份。

        Args:
            session_id: 会话唯一标识。

        Returns:
            解析结果；无有效身份时 `entry` 为 None。
        """
        return self._resolver.resolve(self._store.effective(session_id), self._registry)

    @staticmethod
    def exclude_ids(resolved: ResolvedUserRole) -> frozenset[str]:
        """返回应排除出路由的条目 id 集合。

        pin 的条目已作为常驻身份注入，再被路由激活即重复注入。自定义身份条目
        本就不在路由池（kind 过滤），因此该集合主要对"pin 现有人物"生效。

        Args:
            resolved: 解析结果。

        Returns:
            含 `pin_id` 的单元素集合；无 pin 时为空集。
        """
        return frozenset({resolved.pin_id}) if resolved.pin_id else frozenset()

    def all_sessions(self) -> dict[str, UserRoleSetting]:
        """返回全部会话覆盖的副本（供删除守卫判断"正被 pin 的会话"）。

        Returns:
            会话 id → 覆盖设置 的字典副本。
        """
        return self._store.all_sessions()

    # ------------------------------------------------------------------
    # 写（持久化）
    # ------------------------------------------------------------------

    async def set_session(self, session_id: str, setting: UserRoleSetting) -> None:
        """设置会话级身份覆盖（调用方需先经 `resolve_setting` 校验）。

        Args:
            session_id: 会话唯一标识。
            setting: 新设置。
        """
        await self._store.set_session(session_id, setting)

    async def clear_session(self, session_id: str) -> None:
        """清除会话级覆盖（幂等）。

        Args:
            session_id: 会话唯一标识。
        """
        await self._store.clear_session(session_id)

    async def set_default(self, setting: UserRoleSetting) -> None:
        """设置全局默认身份。

        Args:
            setting: 新默认设置。
        """
        await self._store.set_default(setting)
