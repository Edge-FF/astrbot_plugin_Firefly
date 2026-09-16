"""激活上下文管理器 v0.2。

维护 ActivatedEntry 的生命周期：入队、续期、衰减、清除。
"""

from __future__ import annotations

from .models import ActivatedEntry, ActiveContext, RouteResult
from .registry import MaterialRegistry


class ActiveContextManager:
    """管理激活条目的生命周期。

    纯逻辑层，不依赖 LLM 或 IO，可在 core 层独立测试。
    """

    def __init__(
        self,
        strength_decay: float = 0.2,
        min_strength: float = 0.3,
    ) -> None:
        """初始化激活上下文管理器。

        Args:
            strength_decay: 每轮强度衰减量。
            min_strength: 强度下限（低于该值不再衰减）。
        """
        self._strength_decay = max(strength_decay, 0.0)
        self._min_strength = max(min_strength, 0.0)

    def merge(
        self,
        route_result: RouteResult,
        current: ActiveContext,
        registry: MaterialRegistry,
    ) -> ActiveContext:
        """合并路由结果到当前激活上下文。

        - 新触发条目入队
        - 已有条目续期
        - 保留惯性条目
        - 更新轮次计数
        """
        new_entries: list[ActivatedEntry] = []

        for entry_id in route_result.needed_ids:
            entry = registry.get(entry_id) or _lookup_meta(registry, entry_id)
            default_ttl = entry.default_ttl if entry else 1
            new_entries.append(
                ActivatedEntry(
                    entry_id=entry_id,
                    remaining_ttl=max(default_ttl, 1),
                    strength=1.0,
                    activated_at_turn=current.turn_count + 1,
                )
            )

        for existing in current.entries:
            if existing.entry_id not in route_result.needed_ids:
                if existing.remaining_ttl > 0:
                    new_entries.append(existing)

        return ActiveContext(
            entries=new_entries,
            turn_count=current.turn_count + 1,
        )

    def tick(self, ctx: ActiveContext) -> ActiveContext:
        """推进一轮：所有条目 TTL -= 1，strength 衰减，移除过期条目。"""
        survivors: list[ActivatedEntry] = []
        for entry in ctx.entries:
            entry.remaining_ttl -= 1
            if entry.remaining_ttl <= 0:
                continue
            entry.strength = max(
                self._min_strength, entry.strength - self._strength_decay
            )
            survivors.append(entry)
        return ActiveContext(
            entries=survivors,
            turn_count=ctx.turn_count,
        )

    def get_active_entries(self, ctx: ActiveContext) -> list[ActivatedEntry]:
        """返回按 strength 降序排列的活跃条目。"""
        return sorted(
            [e for e in ctx.entries if e.remaining_ttl > 0],
            key=lambda e: -e.strength,
        )


def _lookup_meta(registry: MaterialRegistry, entry_id: str):
    """从注册表查找条目元数据（不触发懒加载）。"""
    for entry in registry.all_entries():
        if entry.id == entry_id:
            return entry
    return None
