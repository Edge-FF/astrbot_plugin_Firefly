"""会话与状态相关路由。

由 `adapter/debug_api.py` 按资源拆分而来；类以混入方式组合进 `DebugApi`，
共享 `DebugApi.__init__` 注入的同一组依赖。
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from ...core.models import SessionState
from ...core.proactive.policy import daily_count
from .http import HttpHelpers, error, ok

if TYPE_CHECKING:
    pass


class SessionRoutes(HttpHelpers):
    """会话与状态相关路由。"""

    async def _sessions_list(self, **kw) -> dict:
        """GET：列出全部会话状态概览（按更新时间倒序）。"""
        try:
            all_states = await self._store.all()
            sessions = []
            for sid, s in all_states.items():
                sessions.append(
                    {
                        "session_id": sid,
                        "mood": s.mood,
                        "active_count": len(s.active_context.active_ids),
                        "turn_count": s.active_context.turn_count,
                        "last_updated": s.updated_at,
                        "recent_topics": s.recent_topics,
                    }
                )
            sessions.sort(key=lambda x: -x["last_updated"])
            return ok({"sessions": sessions, "total": len(sessions)})
        except Exception as e:
            return error(str(e))

    async def _sessions_detail(self, **kw) -> dict:
        """GET：查看单个会话的完整状态详情。

        查询参数: session_id
        """
        try:
            sid = self._get_query("session_id")
            if not sid:
                return error("缺少 session_id")
            return ok(self._state_to_dict(await self._store.get(sid)))
        except Exception as e:
            return error(str(e))

    async def _sessions_update(self, **kw) -> dict:
        """POST：手动修改会话状态（心情）。

        请求体: session_id 及可选的 mood
        """
        try:
            body = await self._get_json()
            sid = str(body.get("session_id", ""))
            if not sid:
                return error("缺少 session_id")
            s = await self._store.get(sid)
            if "mood" in body:
                s.mood = str(body["mood"])
            s.updated_at = time.time()
            await self._store.set(sid, s)
            return ok(self._state_to_dict(s))
        except Exception as e:
            return error(str(e))

    async def _sessions_activate(self, **kw) -> dict:
        """POST：手动激活一个资料条目（设置 TTL 与强度）。

        请求体: session_id, entry_id, 可选 ttl/strength
        """
        try:
            body = await self._get_json()
            sid = str(body.get("session_id", ""))
            eid = str(body.get("entry_id", ""))
            if not sid or not eid:
                return error("缺少 session_id 或 entry_id")
            ttl = int(body.get("ttl", 0)) or 3
            strength = float(body.get("strength", 1.0))
            s = await self._store.get(sid)
            from ..core.models import ActivatedEntry

            existing = [e for e in s.active_context.entries if e.entry_id == eid]
            if existing:
                existing[0].remaining_ttl = ttl
                existing[0].strength = strength
            else:
                s.active_context.entries.append(
                    ActivatedEntry(
                        entry_id=eid,
                        remaining_ttl=ttl,
                        strength=strength,
                        activated_at_turn=s.active_context.turn_count + 1,
                    )
                )
            s.updated_at = time.time()
            await self._store.set(sid, s)
            return ok(self._state_to_dict(s))
        except Exception as e:
            return error(str(e))

    async def _sessions_deactivate(self, **kw) -> dict:
        """POST：手动取消激活一个资料条目。

        请求体: session_id, entry_id
        """
        try:
            body = await self._get_json()
            sid = str(body.get("session_id", ""))
            eid = str(body.get("entry_id", ""))
            if not sid or not eid:
                return error("缺少 session_id 或 entry_id")
            s = await self._store.get(sid)
            s.active_context.entries = [
                e for e in s.active_context.entries if e.entry_id != eid
            ]
            s.updated_at = time.time()
            await self._store.set(sid, s)
            return ok(self._state_to_dict(s))
        except Exception as e:
            return error(str(e))

    async def _sessions_reset(self, **kw) -> dict:
        """POST：重置会话状态并清空其调试记录。

        请求体: session_id
        """
        try:
            body = await self._get_json()
            sid = str(body.get("session_id", ""))
            if not sid:
                return error("缺少 session_id")
            ns = await self._store.reset(sid)
            self._recorder.clear(sid)
            return ok(self._state_to_dict(ns))
        except Exception as e:
            return error(str(e))

    async def _sessions_delete(self, **kw) -> dict:
        """POST：删除会话状态及其调试记录。

        请求体: session_id
        """
        try:
            body = await self._get_json()
            sid = str(body.get("session_id", ""))
            if not sid:
                return error("缺少 session_id")
            await self._store.reset(sid)
            self._recorder.clear(sid)
            return ok({"deleted": sid})
        except Exception as e:
            return error(str(e))

    @staticmethod
    def _state_to_dict(s: SessionState) -> dict[str, Any]:
        """将会话状态转换为调试面板可读的字典。

        Args:
            s: 会话状态。

        Returns:
            含激活上下文详情的扁平字典。
        """
        ac = s.active_context
        now = time.time()
        return {
            "session_id": s.session_id,
            "mood": s.mood,
            "mood_intensity": round(s.mood_intensity, 2),
            "recent_topics": s.recent_topics,
            "last_message_at": s.last_message_at,
            "updated_at": s.updated_at,
            "last_user_at": s.last_user_at,
            "last_proactive_at": s.last_proactive_at,
            "unanswered_count": s.unanswered_count,
            # 按「今天」归一化后展示：与 ProactivePolicy.check_gates 的判定同源
            # （daily_count），避免面板显示早已跨天的陈旧计数。
            "proactive_count_today": daily_count(s, now),
            "proactive_day": s.proactive_day,
            "active_context": {
                "turn_count": ac.turn_count,
                "active_ids": ac.active_ids,
                "entries": [
                    {
                        "entry_id": e.entry_id,
                        "remaining_ttl": e.remaining_ttl,
                        "strength": round(e.strength, 2),
                        "activated_at_turn": e.activated_at_turn,
                    }
                    for e in ac.entries
                    if e.remaining_ttl > 0
                ],
            },
        }

    def _register_session_routes(self, reg, prefix: str) -> None:
        """注册会话与状态相关路由。。

        Args:
            reg: AstrBot 的路由注册函数。
            prefix: 本插件的 API 路径前缀。
        """
        reg(f"{prefix}/sessions", self._sessions_list, ["GET"], "")
        reg(f"{prefix}/sessions/detail", self._sessions_detail, ["GET"], "")
        reg(f"{prefix}/sessions/update", self._sessions_update, ["POST"], "")
        reg(f"{prefix}/sessions/activate", self._sessions_activate, ["POST"], "")
        reg(f"{prefix}/sessions/deactivate", self._sessions_deactivate, ["POST"], "")
        reg(f"{prefix}/sessions/reset", self._sessions_reset, ["POST"], "")
        reg(f"{prefix}/sessions/delete", self._sessions_delete, ["POST"], "")
