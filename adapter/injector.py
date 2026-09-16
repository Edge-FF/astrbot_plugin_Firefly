"""认知外壳注入适配器 v0.3：桥接 core 层与 AstrBot 钩子。

流程：
  on_llm_request:
    [A] 读取 SessionState
    [B] ContextRouter.route() → RouteResult
    [C] ActiveContextManager.merge() → ActiveContext
    [D] ShellAssembly.build() → 组装文本（复用层）
    [E] 注入 req.extra_user_content_parts

  on_llm_response:
    [F] AffectEngine 更新动态状态（事件驱动，含 P4 闭环）
    [G] ActiveContextManager.tick() → 衰减 TTL

说明：心情更新已从「直接采用用户情绪」改为「事件 → 反应」，
用户情绪只作为 RouteSignals.user_emotion 传入 AffectEngine。
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from astrbot.core.agent.message import TextPart

from ..core import consts
from ..core.affect import EVENT_REUNION, AffectEngine, AffectEvent
from ..core.models import (
    InjectionRecord,
    RouteResult,
    RouteSignals,
    SessionState,
    ShellConfig,
)
from ..core.updaters import update_recent_topics

if TYPE_CHECKING:
    from astrbot.api.event import AstrMessageEvent
    from astrbot.api.provider import LLMResponse, ProviderRequest

    from ..core.assembly import ShellAssembly
    from ..core.context_manager import ActiveContextManager
    from ..core.registry import MaterialRegistry
    from ..core.router import ContextRouter
    from ..core.state import StateStore
    from .debug_recorder import DebugRecorder


class CognitiveShellInjector:
    """对 LLM 请求注入认知外壳，并在 LLM 回复后更新动态状态。"""

    def __init__(
        self,
        registry: MaterialRegistry,
        store: StateStore,
        affect: AffectEngine,
        router: ContextRouter,
        context_manager: ActiveContextManager,
        assembly: ShellAssembly,
        config_getter: Callable[[], ShellConfig],
        logger: Any,
        debug_recorder: DebugRecorder | None = None,
    ) -> None:
        """初始化认知外壳注入器。

        Args:
            registry: 资料注册表。
            store: 会话状态仓库。
            affect: 情绪引擎。
            router: 上下文路由器。
            context_manager: 激活上下文管理器。
            assembly: 外壳组装器（复用层）。
            config_getter: 配置获取函数（每次调用返回最新配置）。
            logger: 日志记录器。
            debug_recorder: 调试记录器，可为 None。
        """
        self._registry = registry
        self._store = store
        self._affect = affect
        self._router = router
        self._ctx_manager = context_manager
        self._assembly = assembly
        self._config_getter = config_getter
        self._logger = logger
        self._debug_recorder = debug_recorder
        # 本轮路由信号：请求钩子写入，响应钩子消费（同一会话同轮次）
        self._pending_signals: dict[str, RouteSignals] = {}

    async def on_llm_request(
        self, event: AstrMessageEvent, req: ProviderRequest
    ) -> None:
        """LLM 请求钩子：执行注入流程，失败时降级跳过并记录日志。

        Args:
            event: 消息事件。
            req: 请求体（将认知外壳注入其中）。
        """
        try:
            await self._inject(event, req)
        except Exception as exc:
            self._logger.error(f"[认知外壳] 注入失败，本次跳过：{exc}", exc_info=True)

    async def on_llm_response(self, event: AstrMessageEvent, resp: LLMResponse) -> None:
        """LLM 响应钩子：更新动态状态，失败时记录日志。

        Args:
            event: 消息事件。
            resp: LLM 响应体。
        """
        try:
            await self._update_state(event, resp)
        except Exception as exc:
            self._logger.error(f"[认知外壳] 动态状态更新失败：{exc}", exc_info=True)

    async def _inject(self, event: AstrMessageEvent, req: ProviderRequest) -> None:
        """执行 A-E 注入主流程：读取状态→路由→合并→组装→注入。

        Args:
            event: 消息事件。
            req: 请求体（注入目标）。
        """
        config = self._config_getter()
        ts = time.time()

        skip_reason: str | None = None
        if not config.enabled:
            skip_reason = "disabled"
        session_id = getattr(event, "unified_msg_origin", None) or ""
        # 丢弃上一轮遗留的信号，避免本轮请求被跳过时误用旧信号
        self._pending_signals.pop(session_id, None)
        if skip_reason is None and not config.is_session_enabled(session_id):
            skip_reason = "session_filtered"
        if skip_reason is None and self._has_shell_block(req):
            skip_reason = "duplicate"
        if skip_reason is None and not self._registry.is_loaded:
            skip_reason = "empty_registry"

        if skip_reason is not None:
            self._record_skip(event, req, session_id, ts, skip_reason)
            return

        # [A] 读取 SessionState
        state = await self._store.get(session_id)
        state_before = SessionState(
            session_id=state.session_id,
            mood=state.mood,
            mood_intensity=state.mood_intensity,
            recent_topics=list(state.recent_topics),
            active_context=state.active_context,
        )

        # [B] ContextRouter.route()
        user_msg = getattr(event, "message_str", None) or req.prompt or ""
        route_result = await self._router.route(user_msg, state, self._registry)

        # [C] ActiveContextManager.merge()
        new_ctx = self._ctx_manager.merge(
            route_result, state.active_context, self._registry
        )
        state.active_context = new_ctx

        # [D] ShellAssembly.build()
        result = self._assembly.build(state, config.max_tokens)
        if result.is_empty():
            self._record_skip(event, req, session_id, ts, "empty_build")
            return

        # [E] 注入
        req.extra_user_content_parts.append(TextPart(text=result.text))

        if result.over_budget or result.truncated:
            self._logger.warning(
                f"[认知外壳] token 超预算：{result.over_budget}，"
                f"本次裁剪的激活条目：{list(result.truncated)}"
            )

        # 持久化
        await self._store.set(session_id, state)

        # 暂存本轮路由信号，供响应钩子更新状态时消费
        self._pending_signals[session_id] = route_result.signals

        # 记录注入
        self._record_injection(
            session_id=session_id,
            timestamp=ts,
            user_msg=user_msg,
            route_result=route_result,
            state_before=state_before,
            active_after=new_ctx,
            xml_text=result.text,
            truncated=result.truncated,
            over_budget=result.over_budget,
        )

    async def _update_state(self, event: AstrMessageEvent, resp: LLMResponse) -> None:
        """执行 F-G 状态更新流程：事件驱动更新心情并衰减激活上下文。

        Args:
            event: 消息事件。
            resp: LLM 响应体。
        """
        config = self._config_getter()
        if not config.enabled:
            return
        session_id = getattr(event, "unified_msg_origin", None) or ""
        if not config.is_session_enabled(session_id):
            return

        user_text = getattr(event, "message_str", None) or ""
        now = time.time()
        state = await self._store.get(session_id)

        # 消费路由信号（用户情绪）→ 事件；文本词典作为兜底事件源
        signals = self._pending_signals.pop(session_id, None) or RouteSignals()
        events = self._affect.events_from_turn(user_text, signals.user_emotion)

        # P4 闭环：用户回复了她先前的主动消息 → 正向事件 + 清零未回复
        if state.unanswered_count > 0:
            events.append(AffectEvent(EVENT_REUNION))

        new_state = self._affect.apply(
            state, events, now, stale_reset_hours=config.decay_hours
        )
        new_state.unanswered_count = 0
        new_state.last_user_at = now
        new_state.last_message_at = now

        update_recent_topics(new_state, user_text, config.max_topics)
        new_state.active_context = self._ctx_manager.tick(new_state.active_context)

        await self._store.set(session_id, new_state)

    @staticmethod
    def _has_shell_block(req: ProviderRequest) -> bool:
        """检测请求中是否已存在认知外壳块（防止重复注入）。

        Args:
            req: 请求体。

        Returns:
            已包含注入标记时返回 True。
        """
        for part in req.extra_user_content_parts:
            if isinstance(part, dict):
                text = part.get("text")
            else:
                text = getattr(part, "text", None)
            if isinstance(text, str) and consts.SHELL_INJECTION_MARK in text:
                return True
        return False

    # ------------------------------------------------------------------
    # 调试记录
    # ------------------------------------------------------------------

    def _record_injection(
        self,
        session_id: str,
        timestamp: float,
        user_msg: str,
        route_result: RouteResult,
        state_before: SessionState,
        active_after: Any,
        xml_text: str,
        truncated: tuple[str, ...],
        over_budget: bool,
    ) -> None:
        """记录一次成功注入的调试快照。

        Args:
            session_id: 会话唯一标识。
            timestamp: 注入时间戳。
            user_msg: 用户消息文本。
            route_result: 路由结果。
            state_before: 注入前的会话状态。
            active_after: 合并后的激活上下文。
            xml_text: 注入的 XML 文本。
            truncated: 被裁剪的条目 ID。
            over_budget: 是否超预算。
        """
        if self._debug_recorder is None:
            return
        rec = InjectionRecord.create(
            record_id=str(uuid.uuid4())[:12],
            session_id=session_id,
            timestamp=timestamp,
            user_msg=user_msg,
            route_result=route_result,
            mood_before=state_before.mood,
            active_before=state_before.active_context,
            active_after=active_after,
            xml_text=xml_text,
            truncated=truncated,
            over_budget=over_budget,
            injected=True,
        )
        self._debug_recorder.record(rec)

    def _record_skip(
        self,
        event: AstrMessageEvent,
        req: ProviderRequest,
        session_id: str,
        timestamp: float,
        reason: str,
    ) -> None:
        """记录一次被跳过的注入（未注入成功）。

        Args:
            event: 消息事件。
            req: 请求体。
            session_id: 会话唯一标识。
            timestamp: 跳过时间戳。
            reason: 跳过原因（如 disabled/session_filtered 等）。
        """
        if self._debug_recorder is None:
            return
        user_msg = getattr(event, "message_str", None) or req.prompt or ""
        rec = InjectionRecord(
            record_id=str(uuid.uuid4())[:12],
            session_id=session_id,
            timestamp=timestamp,
            user_msg=user_msg[:200],
            route_source="keyword",
            injected_successfully=False,
            skipped_reason=reason,
        )
        self._debug_recorder.record(rec)
