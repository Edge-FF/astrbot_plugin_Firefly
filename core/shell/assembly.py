"""认知外壳组装复用层。

把「取 Tier1 + 取激活条目 + 组装外壳」这段逻辑从 injector 中抽出，
供对话注入（injector）与主动消息（proactive runner）共用，避免重复实现。
"""

from __future__ import annotations

from .. import consts
from ..cognition.context_manager import ActiveContextManager
from ..materials.registry import MaterialRegistry
from ..models import ActivatedEntry, BuildResult, MaterialEntry, SessionState
from .builder import ShellBuilder


class ShellAssembly:
    """按会话状态组装认知外壳（无副作用，可单测）。"""

    def __init__(
        self,
        registry: MaterialRegistry,
        ctx_manager: ActiveContextManager,
        builder: ShellBuilder,
    ) -> None:
        """初始化组装器。

        Args:
            registry: 资料注册表。
            ctx_manager: 激活上下文管理器。
            builder: 外壳组装器。
        """
        self._registry = registry
        self._ctx_manager = ctx_manager
        self._builder = builder

    def build(self, state: SessionState, max_tokens: int | None = None) -> BuildResult:
        """组装指定会话当前的外壳文本。

        Args:
            state: 会话状态（提供心情、话题与激活上下文）。
            max_tokens: token 预算，缺省使用 builder 默认值。

        Returns:
            组装结果（文本 + 裁剪/超预算信息）。
        """
        return self._builder.build(
            tier1_entries=self._registry.get_tier(consts.TIER_CORE_PERSONA),
            state=state,
            active_entries=self.active_pairs(state),
            max_tokens=max_tokens,
        )

    def active_pairs(
        self, state: SessionState
    ) -> list[tuple[ActivatedEntry, MaterialEntry]]:
        """返回当前激活条目及其资料内容。

        Args:
            state: 会话状态。

        Returns:
            (ActivatedEntry, MaterialEntry) 列表；资料缺失的条目被丢弃。
        """
        pairs: list[tuple[ActivatedEntry, MaterialEntry]] = []
        for activated in self._ctx_manager.get_active_entries(state.active_context):
            entry = self._registry.get(activated.entry_id)
            if entry is not None:
                pairs.append((activated, entry))
        return pairs
