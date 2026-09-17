"""资料浏览与重载相关路由。

由 `adapter/debug_api.py` 按资源拆分而来；类以混入方式组合进 `DebugApi`，
共享 `DebugApi.__init__` 注入的同一组依赖。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .http import HttpHelpers, error, ok

if TYPE_CHECKING:
    pass


class MaterialRoutes(HttpHelpers):
    """资料浏览与重载相关路由。"""

    async def _materials_list(self, **kw) -> dict:
        """GET：浏览全部资料条目，支持按 tier/kind/关键词过滤。

        查询参数: tier, kind, search
        """
        try:
            tf = self._get_query("tier")
            kf = self._get_query("kind")
            search = (self._get_query("search") or "").strip().lower()
            entries = self._registry.all_entries()
            if tf:
                entries = [e for e in entries if str(e.tier) == str(tf)]
            if kf:
                entries = [e for e in entries if e.kind == kf]
            if search:
                entries = [
                    e
                    for e in entries
                    if search in e.id.lower()
                    or search in e.title.lower()
                    or any(search in t.lower() for t in e.tags)
                    or (e.content and search in e.content.lower())
                ]
            items = [
                {
                    "id": e.id,
                    "title": e.title,
                    "tier": e.tier,
                    "kind": e.kind,
                    "source_path": e.source_path,
                    "tags": list(e.tags),
                    "trigger_keywords": list(e.trigger_keywords),
                    "default_ttl": e.default_ttl,
                    "priority": e.priority,
                    "is_loaded": e.is_loaded(),
                    "content_preview": (e.content or "")[:200]
                    if e.is_loaded()
                    else None,
                }
                for e in sorted(entries, key=lambda x: (x.tier, -x.priority, x.id))
            ]
            return ok({"items": items, "total": len(items)})
        except Exception as e:
            return error(str(e))

    async def _materials_detail(self, **kw) -> dict:
        """GET：查看单个资料条目的完整内容。

        查询参数: entry_id
        """
        try:
            eid = self._get_query("entry_id")
            if not eid:
                return error("缺少 entry_id")
            e = self._registry.get(eid)
            if e is None:
                return error(f"条目不存在：{eid}")
            return ok(
                {
                    "id": e.id,
                    "title": e.title,
                    "tier": e.tier,
                    "kind": e.kind,
                    "source_path": e.source_path,
                    "content": e.content,
                    "tags": list(e.tags),
                    "trigger_keywords": list(e.trigger_keywords),
                    "trigger_patterns": list(e.trigger_patterns),
                    "default_ttl": e.default_ttl,
                    "priority": e.priority,
                }
            )
        except Exception as e:
            return error(str(e))

    async def _materials_reload(self, **kw) -> dict:
        """POST：热重载 role/ 资料并返回加载统计。"""
        try:
            r = self._registry.reload()
            tc: dict[int, int] = {}
            for e in r.entries:
                tc[e.tier] = tc.get(e.tier, 0) + 1
            return ok(
                {"total": len(r.entries), "tier_counts": tc, "warnings": r.warnings}
            )
        except Exception as e:
            return error(str(e))

    def _register_material_routes(self, reg, prefix: str) -> None:
        """注册资料浏览与重载相关路由。。

        Args:
            reg: AstrBot 的路由注册函数。
            prefix: 本插件的 API 路径前缀。
        """
        reg(f"{prefix}/materials", self._materials_list, ["GET"], "")
        reg(f"{prefix}/materials/detail", self._materials_detail, ["GET"], "")
        reg(f"{prefix}/materials/reload", self._materials_reload, ["POST"], "")
