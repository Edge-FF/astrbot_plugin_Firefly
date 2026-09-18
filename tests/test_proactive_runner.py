"""adapter.proactive_runner（主动消息执行器）的集成式单元测试。"""

from __future__ import annotations

import logging
import math
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import IsolatedAsyncioTestCase

from astrbot_plugin_Firefly.adapter.debug_recorder import DebugRecorder
from astrbot_plugin_Firefly.adapter.proactive_runner import ProactiveRunner
from astrbot_plugin_Firefly.core.cognition.affect import AffectEngine
from astrbot_plugin_Firefly.core.cognition.state import StateStore
from astrbot_plugin_Firefly.core.config import ProactiveConfig, ShellConfig
from astrbot_plugin_Firefly.core.models import (
    BuildResult,
    SessionState,
)
from astrbot_plugin_Firefly.core.proactive.policy import ProactivePolicy

_HOUR = 3600.0


class _FakeAssembly:
    """固定返回外壳文本的假组装器。"""

    def __init__(
        self,
        text: str = "<cognitive_shell>\n<dynamic_state>\n心情：想念\n</dynamic_state>\n</cognitive_shell>",
    ) -> None:
        """记录要返回的外壳文本。

        Args:
            text: 外壳文本。
        """
        self._text = text

    def build(self, state: SessionState, max_tokens: int | None = None) -> BuildResult:
        """返回固定外壳。"""
        return BuildResult(text=self._text)


def _config(**kw) -> ProactiveConfig:
    """构造测试配置（默认放行全部节奏闸门）。"""
    base = {
        "enabled": True,
        "tick_interval_seconds": 60.0,
        "min_contact_gap_minutes": 0,
        "min_proactive_interval_minutes": 0,
        "max_unanswered": 0,
        "max_per_day": 0,
        "quiet_hours": "0-0",
        "startup_grace_seconds": 0.0,
    }
    base.update(kw)
    return ProactiveConfig(**base)


class TestProactiveRunner(IsolatedAsyncioTestCase):
    def setUp(self):
        """准备临时状态仓库与记录器。"""
        self._tmp = TemporaryDirectory()
        self.store = StateStore(Path(self._tmp.name) / "state.json", persist=False)
        self.recorder = DebugRecorder()

    def tearDown(self):
        """清理临时目录。"""
        self._tmp.cleanup()

    def _make_runner(
        self, *, llm=None, sender=None, config=None, shell_enabled=True
    ) -> ProactiveRunner:
        """构建执行器。

        Args:
            llm: 生成函数（缺省返回固定文本）。
            sender: 发送函数（缺省成功）。
            config: 主动消息配置。
            shell_enabled: 外壳主开关（ShellConfig.enabled）。

        Returns:
            组装好的 ProactiveRunner。
        """

        async def default_llm(system_prompt: str, user_prompt: str) -> str:
            return "在的，我一直都在。"

        async def default_send(session_id: str, text: str) -> None:
            return None

        affect = AffectEngine()
        return ProactiveRunner(
            store=self.store,
            assembly=_FakeAssembly(),
            affect=affect,
            policy=ProactivePolicy(affect, config or _config()),
            shell_config_getter=lambda: ShellConfig(enabled=shell_enabled),
            llm_generate=llm or default_llm,
            send_message=sender or default_send,
            logger=logging.getLogger("test"),
            recorder=self.recorder,
        )

    async def test_trigger_now_sends_and_counts(self):
        """验证手动触发成功后会发送并正确回写状态。"""
        runner = self._make_runner()
        await self.store.set("s1", SessionState(session_id="s1"))
        sent, message = await runner.trigger_now("s1")
        self.assertTrue(sent, message)
        state = await self.store.get("s1")
        self.assertEqual(state.unanswered_count, 1)
        self.assertGreater(state.last_proactive_at, 0.0)
        self.assertEqual(state.proactive_count_today, 1)
        self.assertEqual(self.recorder.count_proactive(), 1)

    async def test_generation_failure_no_count(self):
        """验证生成失败时不发送、不计数。"""

        async def bad_llm(system_prompt: str, user_prompt: str) -> str:
            return ""

        runner = self._make_runner(llm=bad_llm)
        await self.store.set("s1", SessionState(session_id="s1"))
        sent, _ = await runner.trigger_now("s1")
        self.assertFalse(sent)
        state = await self.store.get("s1")
        self.assertEqual(state.unanswered_count, 0)
        self.assertEqual(state.last_proactive_at, 0.0)

    async def test_send_failure_no_count(self):
        """验证发送失败时不计数、不推进时间（可重试）。"""

        async def bad_send(session_id: str, text: str) -> None:
            raise RuntimeError("网络错误")

        runner = self._make_runner(sender=bad_send)
        await self.store.set("s1", SessionState(session_id="s1"))
        sent, _ = await runner.trigger_now("s1")
        self.assertFalse(sent)
        state = await self.store.get("s1")
        self.assertEqual(state.unanswered_count, 0)
        self.assertEqual(state.last_proactive_at, 0.0)

    async def test_superseded_by_user_message(self):
        """验证生成期间用户插话会丢弃本次主动消息。"""

        async def llm_with_user_talk(system_prompt: str, user_prompt: str) -> str:
            # 模拟生成期间用户发言：更新 last_user_at
            fresh = await self.store.get("s1")
            fresh.last_user_at = 999.0
            await self.store.set("s1", fresh)
            return "我来啦。"

        runner = self._make_runner(llm=llm_with_user_talk)
        await self.store.set("s1", SessionState(session_id="s1", last_user_at=1.0))
        sent, message = await runner.trigger_now("s1")
        self.assertFalse(sent, message)
        self.assertIn("丢弃", message)
        state = await self.store.get("s1")
        self.assertEqual(state.unanswered_count, 0)

    async def test_tick_skips_when_disabled(self):
        """验证总开关关闭时轮询不发消息。"""
        runner = self._make_runner(config=_config(enabled=False))
        await self.store.set(
            "s1",
            SessionState(
                session_id="s1", mood="想念", last_user_at=time.time() - 10 * _HOUR
            ),
        )
        await runner._tick()
        state = await self.store.get("s1")
        self.assertEqual(state.unanswered_count, 0)

    async def test_tick_sends_when_urge_passes(self):
        """验证冲动越阈值时轮询会主动发送。"""
        runner = self._make_runner()
        await self.store.set(
            "s1",
            SessionState(
                session_id="s1", mood="想念", last_user_at=time.time() - 10 * _HOUR
            ),
        )
        await runner._tick()
        state = await self.store.get("s1")
        self.assertEqual(state.unanswered_count, 1)
        self.assertGreater(state.last_proactive_at, 0.0)

    async def test_tick_isolates_session_failure(self):
        """验证单会话异常不影响其它会话。"""

        async def flaky_send(session_id: str, text: str) -> None:
            if session_id == "bad":
                raise RuntimeError("该会话发送失败")

        runner = self._make_runner(sender=flaky_send)
        past = time.time() - 10 * _HOUR
        await self.store.set(
            "bad", SessionState(session_id="bad", mood="想念", last_user_at=past)
        )
        await self.store.set(
            "ok", SessionState(session_id="ok", mood="想念", last_user_at=past)
        )
        await runner._tick()
        self.assertEqual((await self.store.get("ok")).unanswered_count, 1)
        self.assertEqual((await self.store.get("bad")).unanswered_count, 0)

    async def test_max_unanswered_blocks_tick(self):
        """验证达到未回复上限后不再主动。"""
        runner = self._make_runner(config=_config(max_unanswered=1))
        await self.store.set(
            "s1",
            SessionState(
                session_id="s1", mood="想念", last_user_at=time.time() - 10 * _HOUR
            ),
        )
        await runner._tick()
        self.assertEqual((await self.store.get("s1")).unanswered_count, 1)
        # 第二次应被上限拦截
        await runner._tick()
        state = await self.store.get("s1")
        self.assertEqual(state.unanswered_count, 1)
        self.assertFalse(math.isnan(state.last_proactive_at))

    async def test_evaluate_reports_reason(self):
        """验证 evaluate 会给出未发起的原因。"""
        runner = self._make_runner(config=_config(min_contact_gap_minutes=30))
        state = SessionState(session_id="s1", last_user_at=time.time())
        info = runner.evaluate(state, time.time())
        self.assertFalse(info["allowed"])
        self.assertEqual(info["reason"], "recent_contact")

    async def test_silence_drives_missing_mood(self):
        """验证长时间静默会让她的心情转为「想念」。"""
        runner = self._make_runner(config=_config(silence_hours=6.0))
        past = time.time() - 7 * _HOUR
        await self.store.set(
            "s1",
            SessionState(
                session_id="s1", mood="平静", last_user_at=past, updated_at=past
            ),
        )
        await runner._tick()
        state = await self.store.get("s1")
        self.assertEqual(state.mood, "想念")

    async def test_master_switch_stops_proactive(self):
        """验证插件主开关（ShellConfig.enabled=false）会静默主动消息。"""
        runner = self._make_runner(shell_enabled=False)
        await self.store.set(
            "s1",
            SessionState(
                session_id="s1", mood="想念", last_user_at=time.time() - 10 * _HOUR
            ),
        )
        await runner._tick()
        state = await self.store.get("s1")
        self.assertEqual(state.unanswered_count, 0)

    async def test_send_cap_per_tick(self):
        """验证单次 tick 最多发送 max_sends_per_tick 条。"""
        runner = self._make_runner(config=_config(max_sends_per_tick=1))
        past = time.time() - 10 * _HOUR
        await self.store.set(
            "a", SessionState(session_id="a", mood="想念", last_user_at=past)
        )
        await self.store.set(
            "b", SessionState(session_id="b", mood="想念", last_user_at=past)
        )
        await runner._tick()
        sent = sum(
            1 for s in (await self.store.all()).values() if s.unanswered_count > 0
        )
        self.assertEqual(sent, 1)


if __name__ == "__main__":
    unittest.main()
