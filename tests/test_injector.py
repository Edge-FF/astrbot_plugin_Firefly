"""adapter.injector 的集成式单元测试 v0.3。"""

from __future__ import annotations

import logging
import tempfile
import unittest
from pathlib import Path
from unittest import IsolatedAsyncioTestCase

from astrbot.core.agent.message import TextPart
from astrbot.core.provider.entities import ProviderRequest

from astrbot_plugin_Firefly.adapter.injector import CognitiveShellInjector
from astrbot_plugin_Firefly.core.affect import AffectEngine
from astrbot_plugin_Firefly.core.assembly import ShellAssembly
from astrbot_plugin_Firefly.core.builder import ShellBuilder
from astrbot_plugin_Firefly.core.context_manager import ActiveContextManager
from astrbot_plugin_Firefly.core.registry import MaterialRegistry
from astrbot_plugin_Firefly.core.models import (
    RouteResult,
    RouteSignals,
    ShellConfig,
)
from astrbot_plugin_Firefly.core.state import StateStore

class _FakeEvent:
    def __init__(self, message_str: str = "", session_id: str = "s1") -> None:
        """构造假事件对象。

        Args:
            message_str: 用户消息文本。
            session_id: 会话唯一标识。
        """
        self.message_str = message_str
        self.unified_msg_origin = session_id


class _FakeRouter:
    async def route(self, user_msg, session_state, registry):
        """固定返回空路由结果的假路由器。"""
        return RouteResult(needed_ids=[], signals=RouteSignals(), source="keyword")


class _SignalRouter:
    """返回固定「用户情绪」信号的假路由器，用于验证信号跨钩子传递。"""

    def __init__(self, user_emotion: str) -> None:
        """记录要返回的用户情绪。

        Args:
            user_emotion: 本轮路由判定的用户情绪。
        """
        self._user_emotion = user_emotion

    async def route(self, user_msg, session_state, registry):
        """返回带用户情绪信号的路由结果。"""
        return RouteResult(
            needed_ids=[],
            signals=RouteSignals(user_emotion=self._user_emotion),
            source="llm",
        )


def _write_role(role_dir: Path) -> None:
    """在临时目录中写入一套最小可用的 role/ 资料。"""
    role_dir.mkdir(parents=True, exist_ok=True)
    for subdir in ["skills", "npc_profiles", "world_lore", "narratives"]:
        (role_dir / subdir).mkdir(parents=True, exist_ok=True)
    (role_dir / "persona_base.md").write_text(
        "---\nid: persona_base\ntitle: 基础人设\n---\n我是流萤。",
        encoding="utf-8",
    )
    (role_dir / "persona_narrative.md").write_text(
        "---\nid: persona_narrative\n---\n我曾被改造，但如今我属于自己。",
        encoding="utf-8",
    )
    (role_dir / "skills" / "battle.md").write_text(
        "---\nid: skill_battle\nkind: skill\nkeywords: [战斗]\n---\n战斗姿态说明。",
        encoding="utf-8",
    )


class TestCognitiveShellInjector(IsolatedAsyncioTestCase):
    def _make_injector(self, config: ShellConfig, router=None):
        """构建注入器及其状态仓库（使用临时 role/ 与 state 文件）。"""
        self._tmp = tempfile.TemporaryDirectory()
        role_dir = Path(self._tmp.name)
        _write_role(role_dir)

        registry = MaterialRegistry(role_dir)
        registry.load()

        ctx_manager = ActiveContextManager()
        builder = ShellBuilder(max_tokens=2000)
        store = StateStore(Path(self._tmp.name) / "state.json", persist=False)
        injector = CognitiveShellInjector(
            registry=registry,
            store=store,
            affect=AffectEngine(),
            router=router if router is not None else _FakeRouter(),
            context_manager=ctx_manager,
            assembly=ShellAssembly(registry, ctx_manager, builder),
            config_getter=lambda: config,
            logger=logging.getLogger("test"),
        )
        return injector, store

    def tearDown(self):
        """清理临时目录。"""
        if hasattr(self, "_tmp"):
            self._tmp.cleanup()

    async def test_inject_append_shell_block_v2(self):
        """验证正常注入：请求体中出现完整的认知外壳 XML 块。"""
        injector, _ = self._make_injector(ShellConfig())
        req = ProviderRequest()
        await injector.on_llm_request(_FakeEvent(message_str="战斗"), req)
        self.assertEqual(len(req.extra_user_content_parts), 1)
        part = req.extra_user_content_parts[0]
        self.assertIsInstance(part, TextPart)
        self.assertIn("<cognitive_shell>", part.text)
        self.assertIn("<static_core>", part.text)
        self.assertIn("<dynamic_state>", part.text)
        self.assertNotIn("<relationship_stage", part.text)

    async def test_no_double_injection(self):
        """验证重复请求不会重复注入。"""
        injector, _ = self._make_injector(ShellConfig())
        req = ProviderRequest()
        await injector.on_llm_request(_FakeEvent(message_str="战斗"), req)
        await injector.on_llm_request(_FakeEvent(message_str="战斗"), req)
        self.assertEqual(len(req.extra_user_content_parts), 1)

    async def test_disabled_config_no_inject(self):
        """验证配置禁用时不注入。"""
        injector, _ = self._make_injector(ShellConfig(enabled=False))
        req = ProviderRequest()
        await injector.on_llm_request(_FakeEvent(message_str="战斗"), req)
        self.assertEqual(req.extra_user_content_parts, [])

    async def test_session_filter(self):
        """验证会话白名单过滤：未在名单内的会话不注入。"""
        injector, _ = self._make_injector(ShellConfig(enabled_sessions=("other",)))
        req = ProviderRequest()
        await injector.on_llm_request(
            _FakeEvent(message_str="战斗", session_id="s1"), req
        )
        self.assertEqual(req.extra_user_content_parts, [])

    async def test_empty_registry_skips_inject(self):
        """验证资料库为空时不注入。"""
        self._tmp = tempfile.TemporaryDirectory()
        registry = MaterialRegistry(Path(self._tmp.name))
        registry.load()

        ctx_manager = ActiveContextManager()
        store = StateStore(Path(self._tmp.name) / "state.json", persist=False)
        injector = CognitiveShellInjector(
            registry=registry,
            store=store,
            affect=AffectEngine(),
            router=_FakeRouter(),
            context_manager=ctx_manager,
            assembly=ShellAssembly(registry, ctx_manager, ShellBuilder()),
            config_getter=lambda: ShellConfig(),
            logger=logging.getLogger("test"),
        )
        req = ProviderRequest()
        await injector.on_llm_request(_FakeEvent(message_str="战斗"), req)
        self.assertEqual(req.extra_user_content_parts, [])

    async def test_llm_response_updates_state(self):
        """验证 LLM 响应后状态被更新（她的心情是对用户情绪的反应）。"""
        injector, store = self._make_injector(ShellConfig())
        from astrbot.api.provider import LLMResponse

        real_resp = LLMResponse(role="assistant", completion_text="我在呢。")
        await injector.on_llm_response(
            _FakeEvent(message_str="我好难过", session_id="s1"), real_resp
        )
        state = await store.get("s1")
        # 用户难过 → 她心疼（反应，而非照抄用户情绪）
        self.assertEqual(state.mood, "心疼")
        self.assertTrue(state.recent_topics)
        self.assertGreater(state.last_user_at, 0.0)

    async def test_llm_route_signal_reaches_state_update(self):
        """验证请求钩子产出的「用户情绪」信号被响应钩子消费，不会丢失。"""
        injector, store = self._make_injector(
            ShellConfig(), router=_SignalRouter(user_emotion="惊喜")
        )
        from astrbot.api.provider import LLMResponse

        req = ProviderRequest()
        await injector.on_llm_request(
            _FakeEvent(message_str="没什么特别的话", session_id="s1"), req
        )
        real_resp = LLMResponse(role="assistant", completion_text="嗯嗯。")
        await injector.on_llm_response(
            _FakeEvent(message_str="没什么特别的话", session_id="s1"), real_resp
        )
        state = await store.get("s1")
        # 用户惊喜 → 她开心，说明信号确实传到了反应环节
        self.assertEqual(state.mood, "开心")

    async def test_reunion_clears_unanswered(self):
        """验证用户回复会清零未回复计数并回灌正向事件。"""
        injector, store = self._make_injector(ShellConfig())
        from astrbot.api.provider import LLMResponse

        pre = await store.get("s1")
        pre.unanswered_count = 3
        pre.mood = "委屈"
        pre.mood_intensity = 0.8
        await store.set("s1", pre)

        resp = LLMResponse(role="assistant", completion_text="你终于回我了。")
        await injector.on_llm_response(
            _FakeEvent(message_str="在的", session_id="s1"), resp
        )
        state = await store.get("s1")
        self.assertEqual(state.unanswered_count, 0)
        self.assertEqual(state.mood, "开心")

    async def test_active_context_persists_across_rounds(self):
        """验证激活上下文的惯性：注入后状态被保存。"""
        injector, store = self._make_injector(ShellConfig())
        req = ProviderRequest()
        await injector.on_llm_request(_FakeEvent(message_str="你好"), req)

        state = await store.get("s1")
        self.assertGreaterEqual(state.active_context.turn_count, 1)


if __name__ == "__main__":
    unittest.main()
