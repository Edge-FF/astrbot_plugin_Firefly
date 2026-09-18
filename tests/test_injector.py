"""adapter.injector 的集成式单元测试 v0.3。"""

from __future__ import annotations

import logging
import tempfile
import unittest
from pathlib import Path
from unittest import IsolatedAsyncioTestCase, mock

from astrbot_plugin_Firefly.adapter.injector import CognitiveShellInjector
from astrbot_plugin_Firefly.core.cognition.affect import AffectEngine
from astrbot_plugin_Firefly.core.cognition.context_manager import ActiveContextManager
from astrbot_plugin_Firefly.core.cognition.state import StateStore
from astrbot_plugin_Firefly.core.config import ShellConfig
from astrbot_plugin_Firefly.core.materials.registry import MaterialRegistry
from astrbot_plugin_Firefly.core.models import (
    RouteResult,
    RouteSignals,
)
from astrbot_plugin_Firefly.core.shell.assembly import ShellAssembly
from astrbot_plugin_Firefly.core.shell.builder import ShellBuilder

from astrbot.core.agent.message import Message, TextPart
from astrbot.core.provider.entities import ProviderRequest


class _FakePlatformMeta:
    """最简平台元数据（仅提供事件判定所需的 name）。"""

    def __init__(self, name: str = "") -> None:
        """记录平台名。

        Args:
            name: 平台名。
        """
        self.name = name


class _FakeEvent:
    def __init__(
        self,
        message_str: str = "",
        session_id: str = "s1",
        platform_name: str = "",
        extras: dict | None = None,
    ) -> None:
        """构造假事件对象。

        Args:
            message_str: 用户消息文本。
            session_id: 会话唯一标识。
            platform_name: 平台名（任务事件为 "cron"）。
            extras: 事件附加数据（任务事件含 "cron_job"）。
        """
        self.message_str = message_str
        self.unified_msg_origin = session_id
        self.platform_meta = _FakePlatformMeta(platform_name)
        self._extras = extras or {}

    def get_extra(self, key: str, default=None):
        """读取事件附加数据（与 AstrMessageEvent 接口一致）。"""
        return self._extras.get(key, default)


class _BareEvent:
    """缺少 platform_meta / get_extra 的事件，用于健壮性验证。"""

    def __init__(self, message_str: str = "", session_id: str = "s1") -> None:
        """构造最简事件。

        Args:
            message_str: 用户消息文本。
            session_id: 会话唯一标识。
        """
        self.message_str = message_str
        self.unified_msg_origin = session_id


class _FakeRunContext:
    """最简 agent 运行上下文（仅提供 messages）。"""

    def __init__(self, messages=None) -> None:
        """记录消息数组。

        Args:
            messages: 消息数组；传 None 时使用空列表。
        """
        self.messages = [] if messages is None else messages


def _mk_messages():
    """构造模拟 cron 唤醒的消息数组（system + 历史 + 当前指令）。"""
    return [
        Message(role="system", content="CRON_PROMPT + Persona"),
        Message(role="user", content="历史1"),
        Message(role="assistant", content="回复1"),
        Message(role="user", content="scheduled task instruction"),
    ]


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

    async def _seed_state(self, store, **kw) -> None:
        """写入一份预设的会话状态，便于断言「未被改动」。

        Args:
            store: 状态仓库。
            **kw: 需要覆盖的状态字段。
        """
        state = await store.get("s1")
        state.mood = kw.get("mood", "委屈")
        state.mood_intensity = kw.get("mood_intensity", 0.8)
        state.recent_topics = kw.get("recent_topics", ["取快递"])
        state.unanswered_count = kw.get("unanswered_count", 2)
        state.last_user_at = kw.get("last_user_at", 111.0)
        state.last_message_at = kw.get("last_message_at", 111.0)
        await store.set("s1", state)

    async def test_task_event_primary_signal_skips_state_update(self):
        """T0-1：任务事件（主信号：平台名 cron）不更新任何状态字段。"""
        injector, store = self._make_injector(ShellConfig())
        from astrbot.api.provider import LLMResponse

        await self._seed_state(store)
        resp = LLMResponse(role="assistant", completion_text="记得取快递。")
        await injector.on_llm_response(
            _FakeEvent(
                message_str="提醒他取快递", session_id="s1", platform_name="cron"
            ),
            resp,
        )

        state = await store.get("s1")
        self.assertEqual(state.mood, "委屈")
        self.assertEqual(state.mood_intensity, 0.8)
        self.assertEqual(state.recent_topics, ["取快递"])
        self.assertEqual(state.unanswered_count, 2)
        self.assertEqual(state.last_user_at, 111.0)
        self.assertEqual(state.last_message_at, 111.0)

    async def test_task_event_fallback_signal_skips_state_update(self):
        """T0-2：仅兜底信号（extras 含 cron_job）命中时同样跳过状态更新。"""
        injector, store = self._make_injector(ShellConfig())
        from astrbot.api.provider import LLMResponse

        await self._seed_state(store)
        resp = LLMResponse(role="assistant", completion_text="记得取快递。")
        await injector.on_llm_response(
            _FakeEvent(
                message_str="提醒他取快递",
                session_id="s1",
                extras={"cron_job": {"note": "提醒取快递"}},
            ),
            resp,
        )

        state = await store.get("s1")
        self.assertEqual(state.mood, "委屈")
        self.assertEqual(state.unanswered_count, 2)

    async def test_task_event_does_not_trigger_reunion(self):
        """T0-3：任务事件不得被误判为「用户回复」，不触发 reunion、不清零计数。"""
        injector, store = self._make_injector(ShellConfig())
        from astrbot.api.provider import LLMResponse

        await self._seed_state(store, mood="委屈", unanswered_count=3)
        resp = LLMResponse(role="assistant", completion_text="记得取快递。")
        await injector.on_llm_response(
            _FakeEvent(
                message_str="提醒他取快递", session_id="s1", platform_name="cron"
            ),
            resp,
        )

        state = await store.get("s1")
        # 若被误判为 reunion，心情会转为「开心」且计数清零
        self.assertEqual(state.mood, "委屈")
        self.assertEqual(state.unanswered_count, 3)

    async def test_event_without_task_attributes_treated_as_normal(self):
        """T0-5：事件缺少 platform_meta / get_extra 时按普通事件处理，不抛异常。"""
        injector, store = self._make_injector(ShellConfig())
        from astrbot.api.provider import LLMResponse

        resp = LLMResponse(role="assistant", completion_text="我在呢。")
        await injector.on_llm_response(
            _BareEvent(message_str="我好难过", session_id="s1"), resp
        )

        state = await store.get("s1")
        # 普通路径照常更新（保守回退，不误伤正常链路）
        self.assertEqual(state.mood, "心疼")
        self.assertGreater(state.last_user_at, 0.0)

    async def test_task_event_skip_is_recorded(self):
        """验证任务事件跳过状态更新会留下可观测记录。"""
        from astrbot_plugin_Firefly.adapter.debug_recorder import DebugRecorder

        injector, store = self._make_injector(ShellConfig())
        recorder = DebugRecorder()
        injector._debug_recorder = recorder
        from astrbot.api.provider import LLMResponse

        await self._seed_state(store)
        resp = LLMResponse(role="assistant", completion_text="记得取快递。")
        await injector.on_llm_response(
            _FakeEvent(
                message_str="提醒他取快递", session_id="s1", platform_name="cron"
            ),
            resp,
        )

        records = recorder.get_recent("s1", limit=10)
        reasons = [r.skipped_reason for r in records if not r.injected_successfully]
        self.assertIn("task_event_no_state_update", reasons)

    async def test_task_agent_begin_injects_shell_into_system(self):
        """T1-1：任务事件下把外壳注入 system 消息，其余消息不变。"""
        injector, _ = self._make_injector(ShellConfig())
        messages = _mk_messages()

        await injector.on_agent_begin(
            _FakeEvent(
                message_str="提醒他取快递", session_id="s1", platform_name="cron"
            ),
            _FakeRunContext(messages),
        )

        self.assertEqual(messages[0].role, "system")
        self.assertIn("<cognitive_shell>", messages[0].content)
        self.assertIn("<static_core>", messages[0].content)
        self.assertIn("<dynamic_state>", messages[0].content)
        # 其余消息保持原样，数组长度不变（T1-9）
        self.assertEqual(messages[1].content, "历史1")
        self.assertEqual(messages[3].content, "scheduled task instruction")
        self.assertEqual(len(messages), 4)

    async def test_agent_begin_ignores_normal_events(self):
        """T1-5：普通对话事件不触发注入（防止与 on_llm_request 双注入）。"""
        injector, _ = self._make_injector(ShellConfig())
        messages = _mk_messages()
        before = [m.content for m in messages]

        await injector.on_agent_begin(
            _FakeEvent(message_str="你好", session_id="s1"),
            _FakeRunContext(messages),
        )

        self.assertEqual([m.content for m in messages], before)
        self.assertNotIn("<cognitive_shell>", messages[0].content)

    async def test_task_agent_begin_respects_disabled(self):
        """T1-2：插件禁用时不注入。"""
        injector, _ = self._make_injector(ShellConfig(enabled=False))
        messages = _mk_messages()

        await injector.on_agent_begin(
            _FakeEvent(session_id="s1", platform_name="cron"),
            _FakeRunContext(messages),
        )

        self.assertNotIn("<cognitive_shell>", messages[0].content)

    async def test_task_agent_begin_respects_session_filter(self):
        """T1-3：会话不在白名单时不注入。"""
        injector, _ = self._make_injector(ShellConfig(enabled_sessions=("other",)))
        messages = _mk_messages()

        await injector.on_agent_begin(
            _FakeEvent(session_id="s1", platform_name="cron"),
            _FakeRunContext(messages),
        )

        self.assertNotIn("<cognitive_shell>", messages[0].content)

    async def test_task_agent_begin_skips_empty_registry(self):
        """T1-4：资料未加载时不注入。"""
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
        messages = _mk_messages()

        await injector.on_agent_begin(
            _FakeEvent(session_id="s1", platform_name="cron"),
            _FakeRunContext(messages),
        )

        self.assertNotIn("<cognitive_shell>", messages[0].content)

    async def test_task_agent_begin_skips_when_already_injected(self):
        """T1-6：已存在外壳标记时跳过（保险丝）。"""
        injector, _ = self._make_injector(ShellConfig())
        messages = [
            Message(
                role="system", content="SYS <cognitive_shell>已有</cognitive_shell>"
            )
        ]
        before = messages[0].content

        await injector.on_agent_begin(
            _FakeEvent(session_id="s1", platform_name="cron"),
            _FakeRunContext(messages),
        )

        self.assertEqual(messages[0].content, before)

    async def test_task_agent_begin_handles_missing_messages(self):
        """T1-7：messages 缺失或类型异常时不抛异常、不改动。"""
        injector, _ = self._make_injector(ShellConfig())

        # run_context 没有 messages 属性
        class _NoMessages:
            pass

        await injector.on_agent_begin(
            _FakeEvent(session_id="s1", platform_name="cron"), _NoMessages()
        )
        # messages 不是列表
        await injector.on_agent_begin(
            _FakeEvent(session_id="s1", platform_name="cron"),
            _FakeRunContext("not-a-list"),
        )
        # 空列表
        await injector.on_agent_begin(
            _FakeEvent(session_id="s1", platform_name="cron"), _FakeRunContext([])
        )

    async def test_task_agent_begin_inserts_system_when_absent(self):
        """T1-8：无 system 消息时插入一条，仍保持 system 位置。"""
        injector, _ = self._make_injector(ShellConfig())
        messages = [Message(role="user", content="instruction")]

        await injector.on_agent_begin(
            _FakeEvent(session_id="s1", platform_name="cron"),
            _FakeRunContext(messages),
        )

        self.assertEqual(messages[0].role, "system")
        self.assertIn("<cognitive_shell>", messages[0].content)
        self.assertEqual(len(messages), 2)

    async def test_task_agent_begin_records_source(self):
        """T1-9：任务路径注入留下可观测记录，来源标记正确。"""
        from astrbot_plugin_Firefly.adapter.debug_recorder import DebugRecorder

        injector, _ = self._make_injector(ShellConfig())
        recorder = DebugRecorder()
        injector._debug_recorder = recorder
        messages = _mk_messages()

        await injector.on_agent_begin(
            _FakeEvent(session_id="s1", platform_name="cron"),
            _FakeRunContext(messages),
        )

        records = recorder.get_recent("s1", limit=10)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].injection_source, "task_agent_begin")
        self.assertTrue(records[0].injected_successfully)

    async def test_astrbot_api_unavailable_skips_and_is_recorded(self):
        """P1-2：AstrBot 内部 API 缺失时走能力闸门跳过，并留下可观测记录。"""
        from astrbot_plugin_Firefly.adapter import astrbot_compat
        from astrbot_plugin_Firefly.adapter.debug_recorder import DebugRecorder

        injector, _ = self._make_injector(ShellConfig())
        recorder = DebugRecorder()
        injector._debug_recorder = recorder
        req = ProviderRequest()

        with mock.patch.object(astrbot_compat, "INJECTION_API_AVAILABLE", False):
            await injector.on_llm_request(_FakeEvent(message_str="战斗"), req)

        self.assertEqual(req.extra_user_content_parts, [])
        reasons = [
            r.skipped_reason
            for r in recorder.get_recent("s1", limit=10)
            if not r.injected_successfully
        ]
        self.assertIn("astrbot_api_unavailable", reasons)

    async def test_active_context_persists_across_rounds(self):
        """验证激活上下文的惯性：注入后状态被保存。"""
        injector, store = self._make_injector(ShellConfig())
        req = ProviderRequest()
        await injector.on_llm_request(_FakeEvent(message_str="你好"), req)

        state = await store.get("s1")
        self.assertGreaterEqual(state.active_context.turn_count, 1)


if __name__ == "__main__":
    unittest.main()
