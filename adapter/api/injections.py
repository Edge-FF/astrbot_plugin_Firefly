"""注入记录（日志）相关路由。

由 `adapter/debug_api.py` 按资源拆分而来；类以混入方式组合进 `DebugApi`，
共享 `DebugApi.__init__` 注入的同一组依赖。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..dto import injection_to_api_dict
from .http import HttpHelpers, error, ok

if TYPE_CHECKING:
    pass


class InjectionRoutes(HttpHelpers):
    """注入记录（日志）相关路由。"""

    async def _injections_list(self, **kw) -> dict:
        """GET：分页列出注入日志记录（可按会话筛选）。

        查询参数: session_id, limit, offset
        """
        try:
            sid = self._get_query("session_id")
            limit = int(self._get_query("limit") or 50)
            offset = int(self._get_query("offset") or 0)
            records = self._recorder.get_recent(
                session_id=sid or None, limit=limit, offset=offset
            )
            items = []
            for r in records:
                d = injection_to_api_dict(r)
                d.pop("injection_xml", None)
                items.append(d)
            return ok(
                {
                    "items": items,
                    "total": self._recorder.count(sid or None),
                    "session_ids": self._recorder.get_session_ids(),
                }
            )
        except Exception as e:
            return error(str(e))

    async def _injections_detail(self, **kw) -> dict:
        """GET：查看单条注入记录的完整内容。

        查询参数: record_id
        """
        try:
            rid = self._get_query("record_id")
            if not rid:
                return error("缺少 record_id")
            r = self._recorder.get_by_id(rid)
            if r is None:
                return error(f"记录不存在：{rid}")
            return ok(injection_to_api_dict(r))
        except Exception as e:
            return error(str(e))

    async def _injections_clear(self, **kw) -> dict:
        """POST：清空注入日志，不改动任何会话状态。

        与 sessions/reset 的区别：本接口只清理调试记录，适合面板的「清空日志」。

        请求体: session_id（可选，留空表示清空全部会话的记录）
        """
        try:
            body = await self._get_json()
            sid = str(body.get("session_id", "") or "")
            removed = self._recorder.clear(sid or None)
            return ok({"removed": removed, "session_id": sid})
        except Exception as e:
            return error(str(e))

    def _register_injection_routes(self, reg, prefix: str) -> None:
        """注册注入记录（日志）相关路由。。

        Args:
            reg: AstrBot 的路由注册函数。
            prefix: 本插件的 API 路径前缀。
        """
        reg(f"{prefix}/injections", self._injections_list, ["GET"], "")
        reg(f"{prefix}/injections/detail", self._injections_detail, ["GET"], "")
        reg(f"{prefix}/injections/clear", self._injections_clear, ["POST"], "")
