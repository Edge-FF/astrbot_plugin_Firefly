"""认知外壳组装复用层。

把「取 Tier1 + 取激活条目 + 组装外壳」这段逻辑从 injector 中抽出，
供对话注入（injector）与主动消息（proactive runner）共用，避免重复实现。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .. import consts
from ..cognition.context_manager import ActiveContextManager
from ..materials.registry import MaterialRegistry
from ..models import ActivatedEntry, BuildResult, MaterialEntry, SessionState
from .builder import ShellBuilder

if TYPE_CHECKING:
    from ..user_role.models import ResolvedUserRole
    from ..user_role.service import UserRoleService


class ShellAssembly:
    """按会话状态组装认知外壳（无副作用，可单测）。

    用户身份解析由本层负责（对话注入与主动消息共用同一路径），因此三条注入
    路径的身份块必然一致；未注入 `user_role_service` 时等价于"无身份"。
    """

    def __init__(
        self,
        registry: MaterialRegistry,
        ctx_manager: ActiveContextManager,
        builder: ShellBuilder,
        user_role_service: UserRoleService | None = None,
    ) -> None:
        """初始化组装器。

        Args:
            registry: 资料注册表。
            ctx_manager: 激活上下文管理器。
            builder: 外壳组装器。
            user_role_service: 身份服务；None 表示不做身份注入（如纯单测）。
        """
        self._registry = registry
        self._ctx_manager = ctx_manager
        self._builder = builder
        self._user_role_service = user_role_service

    def build(
        self,
        state: SessionState,
        max_tokens: int | None = None,
        resolved: ResolvedUserRole | None = None,
    ) -> BuildResult:
        """组装指定会话当前的外壳文本。

        Args:
            state: 会话状态（提供会话 id、心情、话题与激活上下文）。
            max_tokens: token 预算，缺省使用 builder 默认值。
            resolved: 已解析的身份；调用方若已解析（如需同时计算路由排除集）
                可直接传入以避免重复解析；None 时本层自行解析。

        Returns:
            组装结果（文本 + 裁剪/超预算/身份截断信息）。
        """
        if resolved is None and self._user_role_service is not None:
            resolved = self._user_role_service.resolve(state.session_id)
        pin_id = resolved.pin_id if resolved is not None else ""

        return self._builder.build(
            tier1_entries=self._registry.get_tier(consts.TIER_CORE_PERSONA),
            state=state,
            active_entries=self.active_pairs(state, pin_id=pin_id),
            max_tokens=max_tokens,
            user_profile=resolved.entry if resolved is not None else None,
        )

    def active_pairs(
        self, state: SessionState, pin_id: str = ""
    ) -> list[tuple[ActivatedEntry, MaterialEntry]]:
        """返回当前激活条目及其资料内容。

        Args:
            state: 会话状态。
            pin_id: 已由身份块常驻注入的条目 id；此处再次剔除，防御历史
                残留的激活项造成同一内容重复注入。空串表示不过滤。

        Returns:
            (ActivatedEntry, MaterialEntry) 列表；资料缺失或已被 pin 的条目被丢弃。
        """
        pairs: list[tuple[ActivatedEntry, MaterialEntry]] = []
        for activated in self._ctx_manager.get_active_entries(state.active_context):
            if pin_id and activated.entry_id == pin_id:
                continue
            entry = self._registry.get(activated.entry_id)
            if entry is not None:
                pairs.append((activated, entry))
        return pairs
