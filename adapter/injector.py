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

说明：
  - 心情更新已从「直接采用用户情绪」改为「事件 → 反应」，
    用户情绪只作为 RouteSignals.user_emotion 传入 AffectEngine。
  - 任务路径（AstrBot cron 唤醒）不是「用户说话」：其响应到达时
    完全跳过状态更新，避免把任务说明当作对话内容污染情绪状态。
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from ..core import consts
from ..core.cognition.affect import EVENT_REUNION, AffectEngine, AffectEvent
from ..core.cognition.updaters import update_recent_topics
from ..core.config import ShellConfig
from ..core.models import (
    BuildResult,
    RouteResult,
    RouteSignals,
    SessionState,
)
from ..core.records import InjectionRecord
from . import astrbot_compat

if TYPE_CHECKING:
    from astrbot.api.event import AstrMessageEvent
    from astrbot.api.provider import LLMResponse, ProviderRequest

    from ..core.cognition.context_manager import ActiveContextManager
    from ..core.cognition.state import StateStore
    from ..core.materials.registry import MaterialRegistry
    from ..core.routing.router import ContextRouter
    from ..core.shell.assembly import ShellAssembly
    from ..core.user_role.models import ResolvedUserRole
    from ..core.user_role.service import UserRoleService
    from .astrbot_compat import ContextWrapper
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
        user_role_service: UserRoleService | None = None,
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
            user_role_service: 用户身份服务；None 表示不做身份解析与路由排除。
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
        self._user_role_service = user_role_service
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

    async def on_agent_begin(
        self, event: AstrMessageEvent, run_context: ContextWrapper[Any]
    ) -> None:
        """Agent 开始钩子：任务路径下把外壳注入 system 消息。

        普通对话由 ``on_llm_request`` 负责注入，本钩子只处理任务（cron 唤醒）
        路径，两条路径互斥，从结构上避免重复注入。

        Args:
            event: 消息事件。
            run_context: agent 运行上下文（含最终消息数组 messages）。
        """
        try:
            await self._inject_for_task(event, run_context)
        except Exception as exc:
            self._logger.error(
                f"[认知外壳] 任务路径注入失败，本次跳过：{exc}", exc_info=True
            )

    async def _inject_for_task(
        self, event: AstrMessageEvent, run_context: ContextWrapper[Any]
    ) -> None:
        """任务路径注入主流程：判定 → 闸门 → 组装 → 注入 system 消息。

        注入位置为最前面的连续 system 消息（依据 P1-0 实测：仅该位置能在
        各类上下文压缩策略下存活，且权威更高）。

        Args:
            event: 消息事件。
            run_context: agent 运行上下文。
        """
        # 仅处理任务路径；普通对话不走此分支（与 on_llm_request 互斥）
        if not self._is_task_event(event):
            return

        config = self._config_getter()
        session_id = getattr(event, "unified_msg_origin", None) or ""

        messages = getattr(run_context, "messages", None)
        if not isinstance(messages, list) or not messages:
            self._record_agent_begin(session_id, None, "no_messages")
            return

        skip_reason = self._check_gates(
            session_id, config, already_injected=self._has_shell_in_messages(messages)
        )
        if skip_reason is not None:
            self._record_agent_begin(session_id, None, skip_reason)
            return

        state = await self._store.get(session_id)
        resolved = self._resolve_user_role(session_id)
        result = self._build_shell(state, config, resolved)
        if result.is_empty():
            self._record_agent_begin(session_id, None, "empty_build")
            return

        target = self._last_leading_system_message(messages)
        if target is None:
            # 兜底：无 system 消息时插入一条，仍保持 system 位置（压缩下可存活）
            messages.insert(
                0, astrbot_compat.Message(role="system", content=result.text)
            )
        else:
            target.content = self._append_text(target.content, result.text)

        self._record_agent_begin(session_id, result, None)

    async def _inject(self, event: AstrMessageEvent, req: ProviderRequest) -> None:
        """执行 A-E 注入主流程：读取状态→路由→合并→组装→注入。

        Args:
            event: 消息事件。
            req: 请求体（注入目标）。
        """
        config = self._config_getter()
        ts = time.time()
        session_id = getattr(event, "unified_msg_origin", None) or ""

        # 丢弃上一轮遗留的信号，避免本轮请求被跳过时误用旧信号
        self._pending_signals.pop(session_id, None)

        skip_reason = self._check_gates(
            session_id, config, already_injected=self._has_shell_block(req)
        )
        if skip_reason is not None:
            self._record_skip(event, req, session_id, ts, skip_reason)
            return

        # [A] 读取 SessionState（快照用于记录注入前的状态，必须与后续变更隔离）
        state = await self._store.get(session_id)
        state_before = state.snapshot()

        # [A2] 解析用户身份：同一结果既用于路由排除，也用于身份块注入
        resolved = self._resolve_user_role(session_id)

        # [B] ContextRouter.route()
        user_msg = getattr(event, "message_str", None) or req.prompt or ""
        route_result = await self._router.route(
            user_msg,
            state,
            self._registry,
            exclude_ids=self._exclude_ids(resolved),
        )

        # [C] ActiveContextManager.merge()
        new_ctx = self._ctx_manager.merge(
            route_result, state.active_context, self._registry
        )
        state.active_context = new_ctx

        # [D] 组装外壳（复用层）
        result = self._build_shell(state, config, resolved)
        if result.is_empty():
            self._record_skip(event, req, session_id, ts, "empty_build")
            return

        # [E] 注入
        req.extra_user_content_parts.append(astrbot_compat.TextPart(text=result.text))

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

        任务路径（cron 唤醒）不是「用户说话」，因此完全跳过状态更新：
        不回灌情绪、不触发 reunion、不清零未回复、不更新话题与时间戳。
        详见 PLAN_task_shell.md 决策 D2。

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

        # 任务事件：收尾侧不做任何状态写入（避免把 note 当成用户消息）
        if self._is_task_event(event):
            self._record_task_event_skip(session_id)
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

    def _check_gates(
        self,
        session_id: str,
        config: ShellConfig,
        *,
        already_injected: bool = False,
    ) -> str | None:
        """统一的能力/开关闸门检查（两条路径共用）。

        顺序即优先级：AstrBot 接口可用性 → 总开关 → 会话白名单
        → 重复注入 → 资料已加载。

        Args:
            session_id: 会话唯一标识。
            config: 当前外壳配置。
            already_injected: 本次目标中是否已存在外壳。

        Returns:
            跳过原因；全部通过时返回 None。
        """
        if not astrbot_compat.INJECTION_API_AVAILABLE:
            return "astrbot_api_unavailable"
        if not config.enabled:
            return "disabled"
        if not config.is_session_enabled(session_id):
            return "session_filtered"
        if already_injected:
            return "duplicate"
        if not self._registry.is_loaded:
            return "empty_registry"
        return None

    def _resolve_user_role(self, session_id: str) -> ResolvedUserRole | None:
        """解析会话的用户身份；未配置身份服务时返回 None（等价"无身份"）。

        `UserRoleService.resolve` 已保证不抛异常（读取失败降级为无身份），
        因此这里只需在发生降级时留痕，不再额外吞异常。

        Args:
            session_id: 会话唯一标识。

        Returns:
            解析结果；无服务时 None。
        """
        if self._user_role_service is None:
            return None
        resolved = self._user_role_service.resolve(session_id)
        if resolved.warning:
            self._logger.debug(f"[认知外壳] 用户身份降级：{resolved.warning}")
        return resolved

    @staticmethod
    def _exclude_ids(resolved: ResolvedUserRole | None) -> frozenset[str]:
        """把已 pin 的身份条目转成路由排除集。

        Args:
            resolved: 身份解析结果，可为 None。

        Returns:
            含 pin_id 的单元素集合；无 pin 时为空集。
        """
        if resolved is None or not resolved.pin_id:
            return frozenset()
        return frozenset({resolved.pin_id})

    def _build_shell(
        self,
        state: SessionState,
        config: ShellConfig,
        resolved: ResolvedUserRole | None = None,
    ) -> BuildResult:
        """组装外壳并在超预算/身份截断时告警（两条路径共用）。

        Args:
            state: 会话状态。
            config: 当前外壳配置（提供 token 预算）。
            resolved: 已解析的用户身份；透传给组装层，避免重复解析。

        Returns:
            组装结果；调用方需自行判断 ``is_empty()``。
        """
        result = self._assembly.build(state, config.max_tokens, resolved=resolved)
        if result.over_budget or result.truncated:
            self._logger.warning(
                f"[认知外壳] token 超预算：{result.over_budget}，"
                f"本次裁剪的激活条目：{list(result.truncated)}"
            )
        if result.user_profile_truncated:
            self._logger.warning("[认知外壳] 用户身份块超过上限，本次已截断。")
        return result

    @staticmethod
    def _has_shell_in_messages(messages: list[Any]) -> bool:
        """检测消息数组中是否已存在外壳标记（防止重复注入）。

        Args:
            messages: 消息数组。

        Returns:
            已包含注入标记时返回 True。
        """
        for message in messages:
            content = getattr(message, "content", None)
            if isinstance(content, str):
                if consts.SHELL_INJECTION_MARK in content:
                    return True
            elif isinstance(content, list):
                for part in content:
                    text = (
                        part.get("text")
                        if isinstance(part, dict)
                        else getattr(part, "text", None)
                    )
                    if isinstance(text, str) and consts.SHELL_INJECTION_MARK in text:
                        return True
        return False

    @staticmethod
    def _last_leading_system_message(messages: list[Any]) -> Any | None:
        """返回最前面的连续 system 消息中的最后一条。

        与 AstrBot 的 ``_split_system_rest`` 语义一致：只有开头的连续 system
        消息在上下文压缩时会被保留，因此外壳注入此处可确保存活。

        Args:
            messages: 消息数组。

        Returns:
            目标 system 消息；不存在时返回 None。
        """
        target = None
        for message in messages:
            if getattr(message, "role", None) != "system":
                break
            target = message
        return target

    @staticmethod
    def _append_text(content: Any, text: str) -> Any:
        """把文本追加到消息 content 末尾，兼容 str / list / 其它。

        Args:
            content: 原 content。
            text: 要追加的文本。

        Returns:
            追加后的 content。
        """
        if isinstance(content, str):
            return f"{content}\n\n{text}" if content else text
        if isinstance(content, list):
            return [*content, astrbot_compat.TextPart(text=text)]
        return text

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

    @staticmethod
    def _is_task_event(event: AstrMessageEvent) -> bool:
        """判断本次事件是否来自 AstrBot 任务（cron 唤醒）路径。

        采用双保险信号，任一命中即判定为任务事件：
          1. 主信号：事件平台名落在 ``TASK_EVENT_PLATFORM_NAMES``
          2. 兜底信号：事件 extras 中存在 ``TASK_EVENT_EXTRA_KEY``

        健壮性约定：任何属性缺失或访问异常，一律判定为「非任务事件」，
        从而保守回退到普通对话行为，绝不误伤正常链路。

        Args:
            event: 消息事件。

        Returns:
            来自任务路径返回 True；否则返回 False。
        """
        try:
            platform_meta = getattr(event, "platform_meta", None)
            name = getattr(platform_meta, "name", None)
            if isinstance(name, str) and name in consts.TASK_EVENT_PLATFORM_NAMES:
                return True

            get_extra = getattr(event, "get_extra", None)
            if callable(get_extra) and get_extra(consts.TASK_EVENT_EXTRA_KEY):
                return True
        except Exception:
            return False
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

    def _record_task_event_skip(self, session_id: str) -> None:
        """记录一次因任务事件而跳过的状态更新（可观测，避免静默）。

        Args:
            session_id: 会话唯一标识。
        """
        self._logger.debug(f"[认知外壳] 会话 {session_id} 为任务事件，跳过状态更新。")
        if self._debug_recorder is None:
            return
        rec = InjectionRecord(
            record_id=str(uuid.uuid4())[:12],
            session_id=session_id,
            timestamp=time.time(),
            user_msg="",
            route_source="keyword",
            injected_successfully=False,
            skipped_reason="task_event_no_state_update",
        )
        self._debug_recorder.record(rec)

    def _record_agent_begin(
        self, session_id: str, result: BuildResult | None, reason: str | None
    ) -> None:
        """记录一次任务路径的注入或跳过（可观测，避免静默）。

        Args:
            session_id: 会话唯一标识。
            result: 组装结果（成功时提供）。
            reason: 跳过原因（跳过时提供）；为 None 表示注入成功。
        """
        if reason is not None:
            self._logger.debug(
                f"[认知外壳] 任务路径跳过注入：{reason}（会话 {session_id}）"
            )
        if self._debug_recorder is None:
            return
        xml_text = result.text if result is not None else ""
        rec = InjectionRecord(
            record_id=str(uuid.uuid4())[:12],
            session_id=session_id,
            timestamp=time.time(),
            user_msg="",
            route_source="keyword",
            injection_source="task_agent_begin",
            injection_xml=xml_text[:8000],
            token_estimate=len(xml_text) // 2,
            injected_successfully=reason is None,
            skipped_reason=reason,
        )
        self._debug_recorder.record(rec)
