"""主动消息执行器：后台循环 + 生成 + 发送 + 回写。

设计约束：
- 决策在 core.proactive（纯函数），本层只负责 IO 与框架调用。
- 后台循环与正常对话链路隔离：任何异常都不得外溢；单会话失败不影响其它会话。
- 不跨 LLM 调用持有 Store 锁（Store 自身的 get/set 是短操作）。
- 生成期间用户插话 → 丢弃本次结果；发送失败 → 不计数、不推进 last_proactive_at。
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import TYPE_CHECKING, Any

from ..core.affect import (
    EVENT_PROACTIVE_SENT,
    EVENT_PROACTIVE_UNANSWERED,
    EVENT_SILENCE,
    AffectEngine,
    AffectEvent,
)
from ..core.config import ProactiveConfig, ShellConfig
from ..core.models import ProactiveRecord, SessionState
from ..core.proactive import ProactivePolicy
from .proactive_prompt import build_intent_prompt

if TYPE_CHECKING:
    from ..core.assembly import ShellAssembly
    from ..core.state import StateStore


class ProactiveRunner:
    """情绪驱动的主动消息后台执行器。"""

    def __init__(
        self,
        *,
        store: StateStore,
        assembly: ShellAssembly,
        affect: AffectEngine,
        policy: ProactivePolicy,
        shell_config_getter: Callable[[], ShellConfig],
        llm_generate: Callable[[str, str], Awaitable[str]] | None,
        send_message: Callable[[str, str], Awaitable[None]],
        logger: Any,
        recorder: Any = None,
        registry_ready: Callable[[], bool] | None = None,
    ) -> None:
        """初始化执行器。

        Args:
            store: 会话状态仓库。
            assembly: 外壳组装器。
            affect: 情绪引擎。
            policy: 主动决策器。
            shell_config_getter: 外壳配置获取函数（取 token 预算）。
            llm_generate: async (system_prompt, user_prompt) -> str。
            send_message: async (session_id, text) -> None。
            logger: 日志记录器。
            recorder: 调试记录器（可为 None）。
            registry_ready: 资料是否就绪的检查函数（可为 None，表示总是就绪）。
        """
        self._store = store
        self._assembly = assembly
        self._affect = affect
        self._policy = policy
        self._shell_config_getter = shell_config_getter
        self._llm_generate = llm_generate
        self._send_message = send_message
        self._logger = logger
        self._recorder = recorder
        self._registry_ready = registry_ready

        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self._tick_running = False
        self._plugin_start = time.time()
        self._manual_triggering: set[str] = set()

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    @property
    def config(self) -> ProactiveConfig:
        """返回当前主动消息配置。"""
        return self._policy.config

    @property
    def is_running(self) -> bool:
        """后台循环是否在运行。"""
        return self._task is not None and not self._task.done()

    @property
    def plugin_start(self) -> float:
        """插件启动时间戳（供启动宽限判定）。"""
        return self._plugin_start

    def evaluate(self, state: SessionState, now: float) -> dict[str, Any]:
        """评估某会话当前的主动决策（不发送），供命令与调试面板复用。

        Args:
            state: 会话状态。
            now: 当前时间戳。

        Returns:
            含 allowed/reason/urge/threshold/intent/mood 的字典。
        """
        allowed, reason = self._policy.should_reach_out(
            state,
            now,
            self._plugin_start,
            registry_ready=self._is_registry_ready(),
            provider_ready=self._llm_generate is not None,
        )
        return {
            "session_id": state.session_id,
            "allowed": allowed,
            "reason": reason,
            "urge": round(self._policy.compute_urge(state, now), 2),
            "threshold": round(self._policy.threshold(state), 2),
            "intent": self._policy.pick_intent(state, now),
            "mood": state.mood,
            "unanswered_count": state.unanswered_count,
        }

    def _is_registry_ready(self) -> bool:
        """资料是否已加载（能力闸门 G1）。

        Returns:
            未提供检查函数时视为就绪。
        """
        if self._registry_ready is None:
            return True
        try:
            return bool(self._registry_ready())
        except Exception:
            return False

    async def start(self) -> None:
        """启动后台循环（幂等）。"""
        if self.is_running:
            return
        self._stop.clear()
        self._plugin_start = time.time()
        self._task = asyncio.create_task(self._loop(), name="firefly-proactive-loop")

    async def stop(self) -> None:
        """停止后台循环并等待退出。"""
        self._stop.set()
        task = self._task
        self._task = None
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    # ------------------------------------------------------------------
    # 主循环
    # ------------------------------------------------------------------

    async def _loop(self) -> None:
        """后台轮询循环。"""
        try:
            while not self._stop.is_set():
                interval = max(float(self.config.tick_interval_seconds), 10.0)
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=interval)
                    return  # 收到停止信号
                except asyncio.TimeoutError:
                    pass

                if self._tick_running:
                    continue
                self._tick_running = True
                try:
                    await self._tick()
                except Exception as exc:  # 单轮异常不得终止循环
                    self._logger.error(f"[主动消息] 轮询异常：{exc}", exc_info=True)
                finally:
                    self._tick_running = False
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._logger.error(f"[主动消息] 循环终止：{exc}", exc_info=True)

    async def _tick(self) -> None:
        """遍历所有会话，逐一判定是否发起主动消息。"""
        if not self.config.enabled:
            return
        shell_cfg = self._shell_config_getter()
        if not shell_cfg.enabled:
            # 插件主开关（enabled）关闭时，主动消息一并静默
            return

        states = await self._store.all()
        now = time.time()
        # 每次 tick 最多发送 N 条，其余留待下次评估，避免重启/跨天后集中轰炸
        sends_left = max(int(self.config.max_sends_per_tick), 1)
        for session_id, state in states.items():
            if sends_left <= 0:
                break
            try:
                sent = await self._maybe_send(session_id, state, now)
                if sent:
                    sends_left -= 1
            except Exception as exc:
                # 单会话失败隔离
                self._logger.error(
                    f"[主动消息] 会话 {session_id} 处理失败：{exc}", exc_info=True
                )

    # ------------------------------------------------------------------
    # 单个会话
    # ------------------------------------------------------------------

    async def _maybe_send(
        self, session_id: str, state: SessionState, now: float
    ) -> bool:
        """对单个会话执行一次主动决策与发送。

        Args:
            session_id: 会话唯一标识。
            state: 该会话当前状态。
            now: 当前时间戳。

        Returns:
            本轮是否实际发出了一条消息。
        """
        cfg = self.config
        if not cfg.enabled:
            return False
        if not cfg.is_session_enabled(session_id):
            return False

        # 长时间静默 → 她开始想念（自我运转；仅在尚未想念时写一次，避免反复落盘）
        if (
            cfg.silence_hours > 0
            and state.mood != "想念"
            and self._policy.idle_hours(state, now) >= cfg.silence_hours
        ):
            state = self._affect.apply(state, [AffectEvent(EVENT_SILENCE)], now)
            await self._store.set(session_id, state)

        registry_ready = self._is_registry_ready()
        provider_ready = self._llm_generate is not None

        allowed, reason = self._policy.should_reach_out(
            state,
            now,
            self._plugin_start,
            registry_ready=registry_ready,
            provider_ready=provider_ready,
        )
        if not allowed:
            self._record(
                session_id,
                allowed=False,
                reason=reason,
                intent="",
                urge=self._policy.compute_urge(state, now),
                threshold=self._policy.threshold(state),
                mood=state.mood,
            )
            return False

        intent = self._policy.pick_intent(state, now)
        sent, _ = await self._execute(
            session_id, state, now, intent=intent, reason_ok="ok"
        )
        return sent

    async def _execute(
        self,
        session_id: str,
        state: SessionState,
        now: float,
        *,
        intent: str,
        reason_ok: str,
    ) -> tuple[bool, str]:
        """组装外壳并执行生成/发送/回写/记录（决策之后的公共路径）。

        Args:
            session_id: 会话唯一标识。
            state: 决策时的会话状态。
            now: 当前时间戳。
            intent: 意图标识。
            reason_ok: 发送成功时用于记录的原因（"ok" 或 "manual"）。

        Returns:
            (是否发送成功, 结果说明)。
        """
        urge = self._policy.compute_urge(state, now)
        threshold = self._policy.threshold(state)

        shell_config = self._shell_config_getter()
        build_result = self._assembly.build(state, shell_config.max_tokens)
        if build_result.is_empty():
            self._record(
                session_id,
                allowed=True,
                reason="empty_shell",
                intent=intent,
                urge=urge,
                threshold=threshold,
                mood=state.mood,
            )
            return False, "外壳为空"

        user_prompt = build_intent_prompt(intent, state.recent_topics, now)
        before_user_at = state.last_user_at

        text = await self._generate(build_result.text, user_prompt)
        if not text:
            self._record(
                session_id,
                allowed=True,
                reason="generation_failed",
                intent=intent,
                urge=urge,
                threshold=threshold,
                mood=state.mood,
            )
            return False, "生成失败"

        # 生成期间用户插话 → 丢弃本次结果（比较捕获的标量，避免对象别名干扰）
        fresh = await self._store.get(session_id)
        if fresh.last_user_at != before_user_at:
            self._logger.info(
                f"[主动消息] {session_id} 生成期间用户已发言，丢弃本次主动消息。"
            )
            self._record(
                session_id,
                allowed=True,
                reason="superseded",
                intent=intent,
                urge=urge,
                threshold=threshold,
                mood=state.mood,
            )
            return False, "生成期间用户已发言，已丢弃"

        sent = await self._send(session_id, text)
        await self._after_send(session_id, state, now, sent)
        self._record(
            session_id,
            allowed=True,
            reason=reason_ok if sent else "send_failed",
            intent=intent,
            urge=urge,
            threshold=threshold,
            mood=state.mood,
            text=text,
            sent=sent,
        )
        return sent, ("已发送" if sent else "发送失败")

    async def _generate(self, system_prompt: str, user_prompt: str) -> str:
        """调用 LLM 生成主动消息文本，失败返回空串。

        Args:
            system_prompt: 认知外壳文本。
            user_prompt: 意图提示词。

        Returns:
            生成文本；失败时为空串。
        """
        if self._llm_generate is None:
            return ""
        try:
            return (await self._llm_generate(system_prompt, user_prompt) or "").strip()
        except Exception as exc:
            self._logger.error(f"[主动消息] 生成失败：{exc}", exc_info=True)
            return ""

    async def _send(self, session_id: str, text: str) -> bool:
        """发送主动消息。

        Args:
            session_id: 会话唯一标识。
            text: 消息文本。

        Returns:
            发送成功返回 True。
        """
        try:
            await self._send_message(session_id, text)
            return True
        except Exception as exc:
            self._logger.error(f"[主动消息] 发送失败：{exc}", exc_info=True)
            return False

    async def _after_send(
        self, session_id: str, state: SessionState, now: float, sent: bool
    ) -> None:
        """发送后的状态回写与情绪事件回灌。

        Args:
            session_id: 会话唯一标识。
            state: 发送前的会话状态。
            now: 当前时间戳。
            sent: 是否发送成功。
        """
        if not sent:
            return  # 未发出：不计数、不推进时间，保证下次可重试

        day = _day_key(now)
        fresh = await self._store.get(session_id)
        if fresh.last_proactive_at != state.last_proactive_at:
            # 期间已有其它主动消息写入，避免重复计数
            return

        merged = self._affect.apply(fresh, [AffectEvent(EVENT_PROACTIVE_SENT)], now)
        merged.last_proactive_at = now
        merged.last_message_at = now
        merged.unanswered_count = fresh.unanswered_count + 1
        merged.proactive_count_today = (
            fresh.proactive_count_today + 1 if fresh.proactive_day == day else 1
        )
        merged.proactive_day = day

        # 未回复累计到阈值时，回灌负面事件（她会在意）
        cfg = self.config
        if cfg.max_unanswered > 0 and merged.unanswered_count >= cfg.max_unanswered:
            merged = self._affect.apply(
                merged, [AffectEvent(EVENT_PROACTIVE_UNANSWERED)], now
            )

        await self._store.set(session_id, merged)

    # ------------------------------------------------------------------
    # 手动触发
    # ------------------------------------------------------------------

    async def trigger_now(self, session_id: str) -> tuple[bool, str]:
        """立即对指定会话尝试触发一次主动消息（忽略阈值与节奏闸门）。

        Args:
            session_id: 会话唯一标识。

        Returns:
            (是否成功发送, 说明)。
        """
        if session_id in self._manual_triggering:
            return False, "该会话正在触发中"
        cfg = self.config
        if not cfg.enabled:
            return False, "主动消息总开关未开启"
        if not cfg.is_session_enabled(session_id):
            return False, "该会话不在生效名单内"

        self._manual_triggering.add(session_id)
        try:
            state = await self._store.get(session_id)
            now = time.time()
            intent = self._policy.pick_intent(state, now)
            return await self._execute(
                session_id, state, now, intent=intent, reason_ok="manual"
            )
        except Exception as exc:
            self._logger.error(f"[主动消息] 手动触发失败：{exc}", exc_info=True)
            return False, f"触发异常：{exc}"
        finally:
            self._manual_triggering.discard(session_id)

    # ------------------------------------------------------------------
    # 观测
    # ------------------------------------------------------------------

    def _record(
        self,
        session_id: str,
        *,
        allowed: bool,
        reason: str,
        intent: str,
        urge: float,
        threshold: float,
        mood: str,
        text: str = "",
        sent: bool = False,
    ) -> None:
        """记录一次主动决策快照。

        Args:
            session_id: 会话唯一标识。
            allowed: 是否允许发起。
            reason: 原因。
            intent: 意图。
            urge: 冲动值。
            threshold: 阈值。
            mood: 决策时心情。
            text: 生成文本。
            sent: 是否发送成功。
        """
        if self._recorder is None:
            return
        try:
            self._recorder.record_proactive(
                ProactiveRecord(
                    record_id=str(uuid.uuid4())[:12],
                    session_id=session_id,
                    timestamp=time.time(),
                    allowed=allowed,
                    reason=reason,
                    intent=intent,
                    urge=urge,
                    threshold=threshold,
                    mood=mood,
                    text=text,
                    sent=sent,
                )
            )
        except Exception:
            pass


def _day_key(timestamp: float) -> str:
    """把时间戳格式化为日期键（YYYY-MM-DD）。

    Args:
        timestamp: 时间戳。

    Returns:
        日期字符串；转换失败时返回空串。
    """
    try:
        return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d")
    except (OSError, OverflowError, ValueError):
        return ""
