"""主动消息状态与手动触发相关路由。

由 `adapter/debug_api.py` 按资源拆分而来；类以混入方式组合进 `DebugApi`，
共享 `DebugApi.__init__` 注入的同一组依赖。
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from ..dto import proactive_to_api_dict
from .http import HttpHelpers, error, ok

if TYPE_CHECKING:
    pass


class ProactiveRoutes(HttpHelpers):
    """主动消息状态与手动触发相关路由。"""

    async def _proactive_status(self, **kw) -> dict:
        """GET：主动消息总览（配置 + 各会话当前决策状态）。"""
        try:
            if self._proactive is None:
                return ok({"available": False})
            now = time.time()
            states = await self._store.all()
            sessions = [self._proactive.evaluate(s, now) for s in states.values()]
            sessions.sort(key=lambda x: -x["urge"])
            cfg = self._proactive.config
            return ok(
                {
                    "available": True,
                    "running": self._proactive.is_running,
                    "config": {
                        "enabled": cfg.enabled,
                        "tick_interval_seconds": cfg.tick_interval_seconds,
                        "min_contact_gap_minutes": cfg.min_contact_gap_minutes,
                        "min_proactive_interval_minutes": cfg.min_proactive_interval_minutes,
                        "max_unanswered": cfg.max_unanswered,
                        "max_per_day": cfg.max_per_day,
                        "quiet_hours": cfg.quiet_hours,
                        "sessions": list(cfg.sessions),
                    },
                    "sessions": sessions,
                }
            )
        except Exception as e:
            return error(str(e))

    async def _proactive_decisions(self, **kw) -> dict:
        """GET：主动决策历史（含未发起的原因）。

        查询参数: session_id(可选), limit(默认 50), offset(默认 0)
        """
        try:
            if self._recorder is None:
                return ok({"records": [], "total": 0})
            sid = self._get_query("session_id")
            limit = int(self._get_query("limit") or 50)
            offset = int(self._get_query("offset") or 0)
            records = self._recorder.get_proactive_recent(sid, limit, offset)
            return ok(
                {
                    "records": [proactive_to_api_dict(r) for r in records],
                    "total": self._recorder.count_proactive(sid),
                }
            )
        except Exception as e:
            return error(str(e))

    async def _proactive_now(self, **kw) -> dict:
        """POST：立即手动触发一次主动消息。

        请求体: session_id
        """
        try:
            if self._proactive is None:
                return error("主动消息执行器不可用")
            body = await self._get_json()
            sid = str(body.get("session_id", "")).strip()
            if not sid:
                return error("缺少 session_id")
            sent, message = await self._proactive.trigger_now(sid)
            return ok({"sent": sent, "message": message})
        except Exception as e:
            return error(str(e))

    def _register_proactive_routes(self, reg, prefix: str) -> None:
        """注册主动消息状态与手动触发相关路由。。

        Args:
            reg: AstrBot 的路由注册函数。
            prefix: 本插件的 API 路径前缀。
        """
        reg(f"{prefix}/proactive", self._proactive_status, ["GET"], "")
        reg(f"{prefix}/proactive/decisions", self._proactive_decisions, ["GET"], "")
        reg(f"{prefix}/proactive/now", self._proactive_now, ["POST"], "")
