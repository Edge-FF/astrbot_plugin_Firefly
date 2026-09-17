"""调试面板 Web API v2。

关键修正：插件名从 context.star_metadata.name 动态获取，
确保与 AstrBot Dashboard 构造的 URL 路径完全一致（大小写敏感匹配）。
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

from ..core import consts
from ..core.models import SessionState
from .api.http import HttpHelpers
from .dto import injection_to_api_dict, proactive_to_api_dict

if TYPE_CHECKING:
    from ..core.cognition.state import StateStore
    from ..core.materials.registry import MaterialRegistry
    from .debug_recorder import DebugRecorder


def ok(data: Any = None) -> dict[str, Any]:
    """构造成功响应体。

    Args:
        data: 响应数据。

    Returns:
        形如 {"status": "ok", "data": ...} 的字典。
    """
    return {"status": "ok", "data": data}


def error(message: str) -> dict[str, Any]:
    """构造错误响应体。

    Args:
        message: 错误信息。

    Returns:
        形如 {"status": "error", "message": ...} 的字典。
    """
    return {"status": "error", "message": str(message)}


class DebugApi(HttpHelpers):
    """调试面板 Web API 混入类。

    请求解析辅助（`_get_query` / `_get_json`）由 `HttpHelpers` 提供，
    与 `RoleApi` 共用同一份实现。
    """

    def __init__(
        self,
        context,
        registry: MaterialRegistry,
        store: StateStore,
        recorder: DebugRecorder,
        config_getter,
        router,
        ctx_manager,
        builder,
        logger: logging.Logger | None = None,
        proactive=None,
    ) -> None:
        """初始化调试面板 API。

        Args:
            context: AstrBot 插件上下文。
            registry: 资料注册表。
            store: 会话状态仓库。
            recorder: 调试记录器。
            config_getter: 配置获取函数。
            router: 上下文路由器。
            ctx_manager: 激活上下文管理器。
            builder: 注入文本组装器。
            logger: 日志记录器。
            proactive: 主动消息执行器（可为 None）。
        """
        self._ctx = context
        self._registry = registry
        self._store = store
        self._recorder = recorder
        self._config_getter = config_getter
        self._router = router
        self._ctx_manager = ctx_manager
        self._builder = builder
        self._logger = logger
        self._proactive = proactive

        self._plugin_name = "astrbot_plugin_Firefly"
        try:
            meta = getattr(context, "star_metadata", None)
            if meta and getattr(meta, "name", None):
                self._plugin_name = str(meta.name)
        except Exception:
            pass
        self._P = f"/{self._plugin_name}/page"

    def register_routes(self) -> None:
        """向 AstrBot 注册全部调试面板的 Web API 路由。"""
        if not hasattr(self._ctx, "register_web_api"):
            return
        reg = self._ctx.register_web_api
        P = self._P
        if self._logger is not None:
            self._logger.info(f"[认知外壳] 插件名={self._plugin_name}, API前缀={P}")

        reg(f"{P}/sessions", self._sessions_list, ["GET"], "")
        reg(f"{P}/sessions/detail", self._sessions_detail, ["GET"], "")
        reg(f"{P}/sessions/update", self._sessions_update, ["POST"], "")
        reg(f"{P}/sessions/activate", self._sessions_activate, ["POST"], "")
        reg(f"{P}/sessions/deactivate", self._sessions_deactivate, ["POST"], "")
        reg(f"{P}/sessions/reset", self._sessions_reset, ["POST"], "")
        reg(f"{P}/sessions/delete", self._sessions_delete, ["POST"], "")
        reg(f"{P}/injections", self._injections_list, ["GET"], "")
        reg(f"{P}/injections/detail", self._injections_detail, ["GET"], "")
        reg(f"{P}/injections/clear", self._injections_clear, ["POST"], "")
        reg(f"{P}/materials", self._materials_list, ["GET"], "")
        reg(f"{P}/materials/detail", self._materials_detail, ["GET"], "")
        reg(f"{P}/materials/reload", self._materials_reload, ["POST"], "")
        reg(f"{P}/route/test", self._route_test, ["POST"], "")
        reg(f"{P}/injection/preview", self._injection_preview, ["POST"], "")
        reg(f"{P}/config", self._config_get, ["GET"], "")
        reg(f"{P}/registry-summary", self._registry_summary, ["GET"], "")
        reg(f"{P}/stats", self._stats_get, ["GET"], "")
        reg(f"{P}/proactive", self._proactive_status, ["GET"], "")
        reg(f"{P}/proactive/decisions", self._proactive_decisions, ["GET"], "")
        reg(f"{P}/proactive/now", self._proactive_now, ["POST"], "")

    @staticmethod
    def _state_to_dict(s: SessionState) -> dict[str, Any]:
        """将会话状态转换为调试面板可读的字典。

        Args:
            s: 会话状态。

        Returns:
            含激活上下文详情的扁平字典。
        """
        ac = s.active_context
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
            "proactive_count_today": s.proactive_count_today,
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
