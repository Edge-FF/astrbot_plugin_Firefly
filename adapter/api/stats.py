"""配置、注册表摘要与统计相关路由。

由 `adapter/debug_api.py` 按资源拆分而来；类以混入方式组合进 `DebugApi`，
共享 `DebugApi.__init__` 注入的同一组依赖。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .http import HttpHelpers, error, ok

if TYPE_CHECKING:
    pass


class StatsRoutes(HttpHelpers):
    """配置、注册表摘要与统计相关路由。"""

    async def _config_get(self, **kw) -> dict:
        """GET：返回当前插件运行配置。"""
        try:
            c = self._config_getter()
            return ok(
                {
                    "enabled": c.enabled,
                    "max_tokens": c.max_tokens,
                    "max_on_demand": c.max_on_demand,
                    "tier1_reserved": c.tier1_reserved,
                    "router_use_llm": c.router_use_llm,
                    "router_llm_timeout": c.router_llm_timeout,
                    "router_fallback_to_keyword": c.router_fallback_to_keyword,
                    "router_cache_enabled": c.router_cache_enabled,
                    "state_use_llm": c.state_use_llm,
                    "decay_hours": c.decay_hours,
                    "max_topics": c.max_topics,
                    "default_skill_ttl": c.default_skill_ttl,
                    "default_lore_ttl": c.default_lore_ttl,
                    "default_narrative_ttl": c.default_narrative_ttl,
                    "strength_decay_per_turn": c.strength_decay_per_turn,
                    "min_strength": c.min_strength,
                    "content_cache_max_entries": c.content_cache_max_entries,
                    "persist_state": c.persist_state,
                }
            )
        except Exception as e:
            return error(str(e))

    async def _registry_summary(self, **kw) -> dict:
        """GET：返回资料索引摘要与条目总数。"""
        try:
            return ok(
                {
                    "summary": self._registry.index_summary(),
                    "entry_count": self._registry.entry_count,
                }
            )
        except Exception as e:
            return error(str(e))

    async def _stats_get(self, **kw) -> dict:
        """GET：返回会话数、注入统计、路由来源比例等运行统计。"""
        try:
            all_states = await self._store.all()
            t_ses = len(all_states)
            records = self._recorder.get_recent(limit=10000)
            total = len(records)
            llm_r = sum(1 for r in records if r.route_source == "llm")
            succ = sum(1 for r in records if r.injected_successfully)
            ob = sum(1 for r in records if r.over_budget)
            avg_t = 0
            inj = [r for r in records if r.injected_successfully]
            if inj:
                avg_t = sum(r.token_estimate for r in inj) // len(inj)
            return ok(
                {
                    "total_sessions": t_ses,
                    "total_injections": total,
                    "llm_route_ratio": round(llm_r / max(total, 1), 2),
                    "success_rate": round(succ / max(total, 1), 2),
                    "over_budget_count": ob,
                    "avg_token_estimate": avg_t,
                    "session_ids": sorted(all_states.keys()),
                    "total_materials": self._registry.entry_count,
                }
            )
        except Exception as e:
            return error(str(e))

    def _register_stats_routes(self, reg, prefix: str) -> None:
        """注册配置、注册表摘要与统计相关路由。。

        Args:
            reg: AstrBot 的路由注册函数。
            prefix: 本插件的 API 路径前缀。
        """
        reg(f"{prefix}/config", self._config_get, ["GET"], "")
        reg(f"{prefix}/registry-summary", self._registry_summary, ["GET"], "")
        reg(f"{prefix}/stats", self._stats_get, ["GET"], "")
