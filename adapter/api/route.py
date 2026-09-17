"""路由测试与注入预览相关路由。

由 `adapter/debug_api.py` 按资源拆分而来；类以混入方式组合进 `DebugApi`，
共享 `DebugApi.__init__` 注入的同一组依赖。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ...core import consts
from ...core.models import SessionState
from .http import HttpHelpers, error, ok

if TYPE_CHECKING:
    pass


class RouteRoutes(HttpHelpers):
    """路由测试与注入预览相关路由。"""

    async def _route_test(self, **kw) -> dict:
        """POST：测试路由——给定文本运行一次路由并返回命中的资料详情。

        请求体: text, 可选 session_id
        """
        try:
            body = await self._get_json()
            text = str(body.get("text", "")).strip()
            sid = str(body.get("session_id", "")).strip()
            if not text:
                return error("缺少测试文本")
            s = (
                await self._store.get(sid)
                if sid
                else None or SessionState(session_id=sid or "test")
            )
            rr = await self._router.route(text, s, self._registry)
            detail = []
            for eid in rr.needed_ids:
                e = self._registry.get(eid)
                detail.append(
                    {
                        "id": eid,
                        "title": e.title if e else "?",
                        "kind": e.kind if e else "?",
                        "tier": e.tier if e else 0,
                        "priority": e.priority if e else 0,
                        "content_preview": (e.content or "")[:300] if e else "",
                    }
                )
            all_ids = {e.id for e in self._registry.all_entries()}
            return ok(
                {
                    "needed_ids": rr.needed_ids,
                    "signals": {"user_emotion": rr.signals.user_emotion},
                    "source": rr.source,
                    "detail": detail,
                    "not_matched": sorted(all_ids - set(rr.needed_ids))[:50],
                }
            )
        except Exception as e:
            return error(str(e))

    async def _injection_preview(self, **kw) -> dict:
        """POST：预览——对给定文本模拟完整注入流程并返回组装结果。

        请求体: text, 可选 session_id
        """
        try:
            body = await self._get_json()
            text = str(body.get("text", "")).strip()
            sid = str(body.get("session_id", "")).strip()
            cfg = self._config_getter()
            s = (
                await self._store.get(sid)
                if sid
                else None or SessionState(session_id=sid or "test")
            )
            rr = await self._router.route(text, s, self._registry)
            nctx = self._ctx_manager.merge(rr, s.active_context, self._registry)
            t1 = self._registry.get_tier(consts.TIER_CORE_PERSONA)
            aes = [
                (ae, entry)
                for ae in self._ctx_manager.get_active_entries(nctx)
                if (entry := self._registry.get(ae.entry_id))
            ]
            result = self._builder.build(
                tier1_entries=t1,
                state=s,
                active_entries=aes,
                max_tokens=cfg.max_tokens,
            )
            return ok(
                {
                    "injection_xml": result.text,
                    "token_estimate": len(result.text) // 2,
                    "over_budget": result.over_budget,
                    "truncated_ids": list(result.truncated),
                    "budget_breakdown": {
                        "total_budget": cfg.max_tokens,
                        "tier1_reserved": cfg.tier1_reserved,
                        "estimated_total": len(result.text) // 2,
                        "active_entries": [
                            {
                                "entry_id": ae.entry_id,
                                "kind": entry.kind if entry else "?",
                                "strength": round(ae.strength, 2),
                                "remaining_ttl": ae.remaining_ttl,
                                "token_est": (len(entry.content or "") // 2)
                                if entry
                                else 0,
                            }
                            for ae, entry in aes
                        ],
                    },
                    "route_needed_ids": rr.needed_ids,
                    "active_context_after": [
                        {
                            "entry_id": e.entry_id,
                            "remaining_ttl": e.remaining_ttl,
                            "strength": round(e.strength, 2),
                        }
                        for e in nctx.entries
                    ],
                }
            )
        except Exception as e:
            return error(str(e))

    def _register_route_routes(self, reg, prefix: str) -> None:
        """注册路由测试与注入预览相关路由。。

        Args:
            reg: AstrBot 的路由注册函数。
            prefix: 本插件的 API 路径前缀。
        """
        reg(f"{prefix}/route/test", self._route_test, ["POST"], "")
        reg(f"{prefix}/injection/preview", self._injection_preview, ["POST"], "")
