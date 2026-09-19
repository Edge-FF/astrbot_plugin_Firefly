"""P1 注入链路测试：身份块渲染、路由排除与三条路径接入。

覆盖开发计划 §4.2 的 P1 项：
- ShellBuilder：`<user_profile>` 块存在性、顺序、不可裁剪但自身截断；
- ShellAssembly：身份注入 + pin 条目从激活对中剔除；
- Router：`exclude_ids` 与 `user_role` kind 均不参与路由；FallbackRouter 透传；
- Injector：对话路径注入身份块并把 pin 传给路由器；
- ShellConfig：`user_profile_max_tokens` 解析与下限钳制。
"""

from __future__ import annotations

import json
import logging
import tempfile
import unittest
from pathlib import Path
from unittest import IsolatedAsyncioTestCase

from astrbot.core.agent.message import TextPart
from astrbot.core.provider.entities import ProviderRequest
from astrbot_plugin_Firefly.adapter.injector import CognitiveShellInjector
from astrbot_plugin_Firefly.core.cognition.affect import AffectEngine
from astrbot_plugin_Firefly.core.cognition.context_manager import ActiveContextManager
from astrbot_plugin_Firefly.core.cognition.state import StateStore
from astrbot_plugin_Firefly.core.config import ShellConfig
from astrbot_plugin_Firefly.core.materials.registry import MaterialRegistry
from astrbot_plugin_Firefly.core.models import (
    ActivatedEntry,
    ActiveContext,
    MaterialEntry,
    RouteResult,
    RouteSignals,
    SessionState,
)
from astrbot_plugin_Firefly.core.routing.router import (
    FallbackRouter,
    KeywordRouter,
    LLMRouter,
)
from astrbot_plugin_Firefly.core.shell.assembly import ShellAssembly
from astrbot_plugin_Firefly.core.shell.builder import ShellBuilder
from astrbot_plugin_Firefly.core.user_role.models import MODE_CUSTOM, UserRoleSetting
from astrbot_plugin_Firefly.core.user_role.service import UserRoleService
from astrbot_plugin_Firefly.core.user_role.store import UserRoleStore

_TRAILBLAZER = """---
id: trailblazer
title: 开拓者
kind: lore
keywords: ["开拓者"]
---

他是星穹列车的无名客。
"""

_MY_ROLE = """---
id: my_role
title: 我的角色
kind: user_role
keywords: ["我的角色"]
---

我是一个自写设定。
"""

_BATTLE = """---
id: skill_battle
title: 战斗姿态
kind: skill
keywords: ["战斗"]
---

战斗姿态说明。
"""


def _write(role_dir: Path, rel: str, text: str) -> None:
    """写入一个角色资料文件（自动建父目录）。"""
    path = role_dir / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _seed(role_dir: Path) -> None:
    """写入测试用资料：现有人物 + 自定义身份 + 普通技能。"""
    _write(role_dir, "人物关系/开拓者.md", _TRAILBLAZER)
    _write(role_dir, "用户角色/我的角色.md", _MY_ROLE)
    _write(role_dir, "技能/战斗.md", _BATTLE)


def _registry(role_dir: Path) -> MaterialRegistry:
    """构建并加载注册表（断言无告警）。"""
    registry = MaterialRegistry(role_dir)
    report = registry.load()
    assert not report.warnings, report.warnings
    return registry


def _entry(**kwargs) -> MaterialEntry:
    """构造带默认值的 MaterialEntry。"""
    defaults = {
        "id": "test",
        "title": "Test",
        "tier": 1,
        "kind": "persona",
        "source_path": "<t>",
        "content": "默认内容",
    }
    defaults.update(kwargs)
    return MaterialEntry(**defaults)


class TestBuilderUserProfile(unittest.TestCase):
    def setUp(self) -> None:
        self.builder = ShellBuilder(max_tokens=3000, user_profile_max_tokens=600)
        self.state = SessionState(session_id="s1", mood="开心", recent_topics=["崩铁"])

    def _build(self, profile: MaterialEntry | None):
        return self.builder.build(
            tier1_entries=[_entry(id="persona_base", content="我是流萤。")],
            state=self.state,
            active_entries=[
                (
                    ActivatedEntry(entry_id="skill_battle", remaining_ttl=3),
                    _entry(
                        id="skill_battle", tier=3, kind="skill", content="战斗说明。"
                    ),
                )
            ],
            user_profile=profile,
        )

    def test_block_present_and_ordered(self):
        """身份块存在，且位于 static_core 与 dynamic_state 之间。"""
        result = self._build(_entry(id="my_role", title="我的角色", content="我是谁。"))
        text = result.text
        self.assertIn("<user_profile>", text)
        self.assertIn("我是谁。", text)
        # 用闭合标签比较块序，避免误匹配说明文本中的标签名
        self.assertLess(text.index("</static_core>"), text.index("</user_profile>"))
        self.assertLess(text.index("</user_profile>"), text.index("</dynamic_state>"))
        self.assertLess(text.index("</dynamic_state>"), text.index("</active_context>"))
        self.assertFalse(result.user_profile_truncated)

    def test_intro_mentions_user_profile(self):
        """说明文本解释 user_profile 的语义，避免模型误当流萤人格。"""
        text = self._build(_entry(id="my_role", content="设定。")).text
        self.assertIn("描述正在与你对话的人是谁", text)

    def test_absent_when_none_or_empty(self):
        """无身份或正文为空时不产生块。"""
        for profile in (None, _entry(id="empty", content="   ")):
            result = self._build(profile)
            self.assertNotIn("<user_profile>", result.text)
            self.assertFalse(result.user_profile_truncated)

    def test_truncation_flag_and_marker(self):
        """超过上限时截断自身并置标记，且不改动其它块。"""
        builder = ShellBuilder(max_tokens=3000, user_profile_max_tokens=5)
        result = builder.build(
            tier1_entries=[],
            state=SessionState(session_id="s1"),
            active_entries=[],
            user_profile=_entry(id="big", content="字" * 100),
        )
        self.assertTrue(result.user_profile_truncated)
        self.assertIn("…（身份设定已截断）", result.text)


class TestAssemblyUserProfile(IsolatedAsyncioTestCase):
    def _make(self, with_service: bool = True):
        self._tmp = tempfile.TemporaryDirectory()
        role_dir = Path(self._tmp.name)
        _seed(role_dir)
        registry = _registry(role_dir)
        ctx_manager = ActiveContextManager()
        builder = ShellBuilder(max_tokens=3000)
        user_role_store = UserRoleStore(Path(self._tmp.name) / "ur.json", persist=False)
        service = UserRoleService(user_role_store, registry)
        assembly = ShellAssembly(
            registry,
            ctx_manager,
            builder,
            service if with_service else None,
        )
        self.addCleanup(self._tmp.cleanup)
        return assembly, user_role_store

    async def test_identity_injected_for_pinned_session(self):
        """会话 pin 的身份被注入，且 pin 条目从激活对中剔除。"""
        assembly, store = self._make()
        await store.set_session("s1", UserRoleSetting(MODE_CUSTOM, "my_role"))
        state = SessionState(session_id="s1")
        state.active_context = ActiveContext(
            entries=[
                ActivatedEntry(entry_id="my_role", remaining_ttl=3),
                ActivatedEntry(entry_id="trailblazer", remaining_ttl=3),
            ],
            turn_count=1,
        )
        pairs = assembly.active_pairs(state, pin_id="my_role")
        self.assertNotIn("my_role", [entry.id for _, entry in pairs])
        self.assertIn("trailblazer", [entry.id for _, entry in pairs])

        result = assembly.build(state)
        self.assertIn("<user_profile>", result.text)
        self.assertIn("我是一个自写设定", result.text)

    async def test_no_service_means_no_profile(self):
        """未接入身份服务时不注入身份块（行为与旧版一致）。"""
        assembly, _ = self._make(with_service=False)
        result = assembly.build(SessionState(session_id="s1"))
        self.assertNotIn("<user_profile>", result.text)


class TestRouterExclusions(IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        _seed(Path(self._tmp.name))
        self.registry = _registry(Path(self._tmp.name))
        self.state = SessionState(session_id="s1")
        self.addCleanup(self._tmp.cleanup)

    async def test_keyword_router_excludes_pin(self):
        """pin 的条目即使命中关键词也不被激活。"""
        router = KeywordRouter()
        result = await router.route(
            "战斗 开拓者",
            self.state,
            self.registry,
            exclude_ids=frozenset({"trailblazer"}),
        )
        self.assertIn("skill_battle", result.needed_ids)
        self.assertNotIn("trailblazer", result.needed_ids)

    async def test_keyword_router_excludes_user_role_kind(self):
        """自定义身份文档（kind=user_role）永不参与路由。"""
        router = KeywordRouter()
        result = await router.route("我的角色", self.state, self.registry)
        self.assertNotIn("my_role", result.needed_ids)

    async def test_llm_parse_filters_exclude_and_kind(self):
        """LLM 返回的 pin 与身份文档 id 都被过滤，仅保留可路由条目。"""
        raw = json.dumps(
            {
                "needed_ids": ["trailblazer", "my_role", "skill_battle"],
                "signals": {},
            }
        )
        result = LLMRouter._parse_response(
            raw, self.registry, None, frozenset({"trailblazer"})
        )
        self.assertEqual(result.needed_ids, ["skill_battle"])

    async def test_fallback_router_passes_exclude_ids(self):
        """FallbackRouter 把排除集透传给被选中的实现。"""
        seen: dict[str, frozenset[str]] = {}

        class _CapturingRouter:
            async def route(
                self, user_msg, session_state, registry, exclude_ids=frozenset()
            ):
                seen["ids"] = exclude_ids
                return None

        class _Keyword:
            async def route(
                self, user_msg, session_state, registry, exclude_ids=frozenset()
            ):
                seen["ids"] = exclude_ids
                return RouteResult(source="keyword")

        router = FallbackRouter(
            llm=_CapturingRouter(), keyword=_Keyword(), fallback_to_keyword=True
        )
        await router.route(
            "x", self.state, self.registry, exclude_ids=frozenset({"trailblazer"})
        )
        self.assertEqual(seen["ids"], frozenset({"trailblazer"}))


class TestShellConfigUserProfile(unittest.TestCase):
    def test_default_and_clamp(self):
        """缺省 600；脏值回退；过小值被钳制到下限 50。"""
        self.assertEqual(ShellConfig.from_dict({}).user_profile_max_tokens, 600)
        self.assertEqual(
            ShellConfig.from_dict(
                {"user_role": {"user_profile_max_tokens": 10}}
            ).user_profile_max_tokens,
            50,
        )
        warnings: list[str] = []
        cfg = ShellConfig.from_dict(
            {"user_role": {"user_profile_max_tokens": "abc"}}, warnings
        )
        self.assertEqual(cfg.user_profile_max_tokens, 600)
        self.assertTrue(warnings)


class _FakePlatformMeta:
    def __init__(self, name: str = "") -> None:
        self.name = name


class _FakeEvent:
    def __init__(self, message_str: str = "", session_id: str = "s1") -> None:
        self.message_str = message_str
        self.unified_msg_origin = session_id
        self.platform_meta = _FakePlatformMeta("")
        self._extras: dict = {}

    def get_extra(self, key: str, default=None):
        return self._extras.get(key, default)


class _CapturingRouter:
    """记录注入器传入的排除集，供断言路由去重生效。"""

    def __init__(self) -> None:
        self.exclude_ids: frozenset[str] | None = None

    async def route(self, user_msg, session_state, registry, exclude_ids=frozenset()):
        self.exclude_ids = exclude_ids
        return RouteResult(needed_ids=[], signals=RouteSignals(), source="keyword")


class TestInjectorUserProfile(IsolatedAsyncioTestCase):
    def _make(self, with_service: bool = True):
        self._tmp = tempfile.TemporaryDirectory()
        role_dir = Path(self._tmp.name)
        _seed(role_dir)
        registry = _registry(role_dir)
        ctx_manager = ActiveContextManager()
        builder = ShellBuilder(max_tokens=3000)
        user_role_store = UserRoleStore(Path(self._tmp.name) / "ur.json", persist=False)
        service = UserRoleService(user_role_store, registry)
        assembly = ShellAssembly(
            registry, ctx_manager, builder, service if with_service else None
        )
        router = _CapturingRouter()
        injector = CognitiveShellInjector(
            registry=registry,
            store=StateStore(Path(self._tmp.name) / "state.json", persist=False),
            affect=AffectEngine(),
            router=router,
            context_manager=ctx_manager,
            assembly=assembly,
            config_getter=lambda: ShellConfig(),
            logger=logging.getLogger("test"),
            user_role_service=service if with_service else None,
        )
        self.addCleanup(self._tmp.cleanup)
        return injector, router

    async def test_inject_includes_default_profile_and_excludes_pin(self):
        """默认身份（内置开拓者）被注入，并把 pin 传给路由器排除。"""
        injector, router = self._make()
        req = ProviderRequest()
        await injector.on_llm_request(_FakeEvent(message_str="战斗"), req)
        self.assertEqual(len(req.extra_user_content_parts), 1)
        part = req.extra_user_content_parts[0]
        self.assertIsInstance(part, TextPart)
        self.assertIn("<user_profile>", part.text)
        self.assertIn("星穹列车的无名客", part.text)
        self.assertEqual(router.exclude_ids, frozenset({"trailblazer"}))

    async def test_inject_without_service_has_no_profile(self):
        """未接入身份服务时，外壳照常注入但不含身份块，排除集为空。"""
        injector, router = self._make(with_service=False)
        req = ProviderRequest()
        await injector.on_llm_request(_FakeEvent(message_str="战斗"), req)
        part = req.extra_user_content_parts[0]
        self.assertNotIn("<user_profile>", part.text)
        self.assertEqual(router.exclude_ids, frozenset())
