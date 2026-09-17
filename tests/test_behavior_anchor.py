"""注入行为锚点测试（P0-2）。

用途：固化「一次完整注入 + 一次状态更新 + 一次任务路径注入」的可观测输出，
作为 `PLAN_arch_refactor.md` 中 P2–P5 各「行为等价」重构阶段的唯一权威判据。

约定：
- 只断言确定性输出。时间戳（`timestamp` / `last_user_at` / `last_message_at` /
  `updated_at`）随时间变化，一律不断言。
- 本文件内的 role/ 资料夹具与期望文本均为**冻结快照**，故意不与
  `tests/test_injector.py` 共享：其它测试调整夹具时，本文件的锚点不得随之变化。
- 任何**有意**修改注入格式的提交，必须同步更新本文件的期望值，并在
  commit message 中注明「锚点已更新」，否则视为回归。
"""

from __future__ import annotations

import logging
import tempfile
import unittest
from pathlib import Path
from unittest import IsolatedAsyncioTestCase

from astrbot_plugin_Firefly.adapter.debug_recorder import DebugRecorder
from astrbot_plugin_Firefly.adapter.injector import CognitiveShellInjector
from astrbot_plugin_Firefly.core.affect import AffectEngine
from astrbot_plugin_Firefly.core.assembly import ShellAssembly
from astrbot_plugin_Firefly.core.builder import ShellBuilder
from astrbot_plugin_Firefly.core.config import ShellConfig
from astrbot_plugin_Firefly.core.context_manager import ActiveContextManager
from astrbot_plugin_Firefly.core.models import SessionState
from astrbot_plugin_Firefly.core.registry import MaterialRegistry
from astrbot_plugin_Firefly.core.router import KeywordRouter
from astrbot_plugin_Firefly.core.state import StateStore

from astrbot.api.provider import LLMResponse
from astrbot.core.agent.message import Message
from astrbot.core.provider.entities import ProviderRequest

SESSION_ID = "anchor:s1"
USER_MSG = "我有点难过，但我还能战斗"
# 任务路径的探针消息：刻意包含 skill_battle 的触发词「战斗」，
# 使「任务路径不经过路由」这一性质可被验证；若不含触发词，
# 「未出现激活块」的断言会退化为恒真（见锚点 C 的前置校验）。
TASK_USER_MSG = "提醒我战斗前取快递"
MAX_TOKENS = 2000

_SHELL_INTRO_TEXT = (
    "以下 <cognitive_shell> 描述「流萤」的认知外壳。"
    "静止设定定义你是谁；动态状态反映当前心境与关系阶段；"
    "激活上下文是当前场景相关的额外资料（弱激活条目仅为背景参考）。"
    "请始终以此为准，不要偏离人设。"
)

_STATIC_CORE_TEXT = (
    "<static_core>\n"
    '<entry id="persona_base" title="基础人设">\n'
    "我是流萤。\n"
    "</entry>\n"
    '<entry id="persona_narrative" title="persona_narrative">\n'
    "我曾被改造，但如今我属于自己。\n"
    "</entry>\n"
    "</static_core>"
)

_DYNAMIC_STATE_TEXT = "<dynamic_state>\n心情：平静\n最近话题：暂无\n</dynamic_state>"

_ACTIVE_CONTEXT_TEXT = (
    "<active_context>\n"
    '<entry kind="skill" id="skill_battle" strength="1.0" title="战斗姿态">\n'
    "战斗姿态说明。\n"
    "</entry>\n"
    "</active_context>"
)

# 锚点 A：普通对话路径（on_llm_request）注入的完整 XML
_GOLDEN_LLM_REQUEST_XML = (
    "<cognitive_shell>\n"
    f"{_SHELL_INTRO_TEXT}\n"
    f"{_STATIC_CORE_TEXT}\n"
    f"{_DYNAMIC_STATE_TEXT}\n"
    f"{_ACTIVE_CONTEXT_TEXT}\n"
    "</cognitive_shell>"
)

# 锚点 C：任务路径（on_agent_begin）注入的完整 XML（不经过路由，故无激活块）
_GOLDEN_TASK_XML = (
    "<cognitive_shell>\n"
    f"{_SHELL_INTRO_TEXT}\n"
    f"{_STATIC_CORE_TEXT}\n"
    f"{_DYNAMIC_STATE_TEXT}\n"
    "</cognitive_shell>"
)


def _write_frozen_role(role_dir: Path) -> None:
    """写入冻结的 role/ 资料夹具（改动本函数即代表锚点变更）。

    Args:
        role_dir: role/ 目录路径。
    """
    (role_dir / "skills").mkdir(parents=True, exist_ok=True)
    (role_dir / "persona_base.md").write_text(
        "---\nid: persona_base\ntitle: 基础人设\n---\n我是流萤。",
        encoding="utf-8",
    )
    (role_dir / "persona_narrative.md").write_text(
        "---\nid: persona_narrative\n---\n我曾被改造，但如今我属于自己。",
        encoding="utf-8",
    )
    (role_dir / "skills" / "battle.md").write_text(
        "---\nid: skill_battle\nkind: skill\ntitle: 战斗姿态\nkeywords: [战斗]\n"
        "---\n战斗姿态说明。",
        encoding="utf-8",
    )


class _FakePlatformMeta:
    """最简平台元数据（仅提供任务判定所需的 name）。"""

    def __init__(self, name: str = "") -> None:
        """记录平台名。

        Args:
            name: 平台名。
        """
        self.name = name


class _FakeEvent:
    """最简消息事件（仅提供注入流程读取的字段）。"""

    def __init__(
        self,
        message_str: str = "",
        session_id: str = SESSION_ID,
        platform_name: str = "",
    ) -> None:
        """构造假事件对象。

        Args:
            message_str: 用户消息文本。
            session_id: 会话唯一标识。
            platform_name: 平台名（任务事件为 "cron"）。
        """
        self.message_str = message_str
        self.unified_msg_origin = session_id
        self.platform_meta = _FakePlatformMeta(platform_name)


class _FakeRunContext:
    """最简 agent 运行上下文（仅提供 messages）。"""

    def __init__(self, messages: list | None = None) -> None:
        """记录消息数组。

        Args:
            messages: 消息数组；None 时使用空列表。
        """
        self.messages = [] if messages is None else messages


class TestInjectionBehaviorAnchor(IsolatedAsyncioTestCase):
    """注入链路的行为锚点：输出文本与状态演化必须逐字符稳定。"""

    async def asyncSetUp(self) -> None:
        """准备冻结夹具：临时 role/ 目录、确定性路由器与状态仓库。"""
        self._tmp = tempfile.TemporaryDirectory()
        role_dir = Path(self._tmp.name) / "role"
        _write_frozen_role(role_dir)

        registry = MaterialRegistry(role_dir)
        registry.load()
        self._registry = registry

        ctx_manager = ActiveContextManager()
        self._store = StateStore(Path(self._tmp.name) / "state.json", persist=False)
        self._recorder = DebugRecorder()
        self._injector = CognitiveShellInjector(
            registry=registry,
            store=self._store,
            affect=AffectEngine(),
            # 使用关键词路由器而非 LLM 路由器，保证路由结果完全确定
            router=KeywordRouter(max_entries=3),
            context_manager=ctx_manager,
            assembly=ShellAssembly(
                registry, ctx_manager, ShellBuilder(max_tokens=MAX_TOKENS)
            ),
            config_getter=lambda: ShellConfig(max_tokens=MAX_TOKENS),
            logger=logging.getLogger("behavior_anchor"),
            debug_recorder=self._recorder,
        )

    def tearDown(self) -> None:
        """清理临时目录。"""
        self._tmp.cleanup()

    async def _seed_initial_state(self) -> None:
        """写入冻结的初始会话状态。

        `updated_at` 必须为 0.0：AffectEngine 依据它计算衰减时长，
        非零值会让结果依赖真实时间，破坏锚点确定性。
        """
        await self._store.set(
            SESSION_ID,
            SessionState(
                session_id=SESSION_ID,
                mood="平静",
                mood_intensity=0.4,
                recent_topics=[],
                updated_at=0.0,
            ),
        )

    async def test_llm_request_injection_is_byte_stable(self) -> None:
        """锚点 A：普通对话注入的 XML 文本与结构化记录逐字段稳定。"""
        await self._seed_initial_state()
        req = ProviderRequest()

        await self._injector.on_llm_request(_FakeEvent(message_str=USER_MSG), req)

        self.assertEqual(len(req.extra_user_content_parts), 1)
        self.assertEqual(req.extra_user_content_parts[0].text, _GOLDEN_LLM_REQUEST_XML)

        records = self._recorder.get_recent(SESSION_ID, limit=10)
        self.assertEqual(len(records), 1)
        rec = records[0]
        self.assertTrue(rec.injected_successfully)
        self.assertIsNone(rec.skipped_reason)
        self.assertEqual(rec.injection_source, "llm_request")
        self.assertEqual(rec.route_source, "keyword")
        self.assertEqual(rec.route_needed_ids, ["skill_battle"])
        self.assertIsNone(rec.route_signals_emotion)
        self.assertEqual(rec.mood_before, "平静")
        self.assertEqual(rec.active_context_ids_before, [])
        self.assertEqual(rec.injection_xml, _GOLDEN_LLM_REQUEST_XML)
        self.assertEqual(rec.token_estimate, len(_GOLDEN_LLM_REQUEST_XML) // 2)
        self.assertEqual(rec.truncated_ids, [])
        self.assertFalse(rec.over_budget)
        self.assertEqual(
            rec.active_context_after,
            [
                {
                    "entry_id": "skill_battle",
                    "remaining_ttl": 4,
                    "strength": 1.0,
                    "activated_at_turn": 1,
                }
            ],
        )

    async def test_state_update_is_byte_stable(self) -> None:
        """锚点 B：响应钩子后的心情、话题与激活惯性稳定。

        场景含两个信号：用户文本「难过」触发心疼反应；关键词「战斗」
        激活 skill_battle。同时验证激活条目在下一轮 TTL 与强度的衰减。
        """
        await self._seed_initial_state()
        event = _FakeEvent(message_str=USER_MSG)
        req = ProviderRequest()

        await self._injector.on_llm_request(event, req)
        await self._injector.on_llm_response(
            event, LLMResponse(role="assistant", completion_text="我在。")
        )

        state = await self._store.get(SESSION_ID)
        # 用户难过 → 她心疼（反应而非复读）；强度取自 _REACTIONS 的基础值
        self.assertEqual(state.mood, "心疼")
        self.assertAlmostEqual(state.mood_intensity, 0.8, places=6)
        self.assertEqual(state.recent_topics, ["我有点难过但我还能战斗"])
        self.assertEqual(state.unanswered_count, 0)
        self.assertEqual(state.active_context.turn_count, 1)
        self.assertEqual(len(state.active_context.entries), 1)

        entry = state.active_context.entries[0]
        self.assertEqual(entry.entry_id, "skill_battle")
        # 初始 TTL 取自 consts.DEFAULT_TTL_MAP["skill"]；tick 后减 1
        self.assertEqual(entry.remaining_ttl, 3)
        # strength 由 1.0 衰减一个 strength_decay（0.2），取整比较以规避浮点尾差
        self.assertEqual(round(entry.strength, 2), 0.8)
        self.assertEqual(entry.activated_at_turn, 1)

    async def test_task_path_injection_is_byte_stable(self) -> None:
        """锚点 C：任务路径注入 system 消息的内容稳定，且不触发路由。

        任务路径不经过 ContextRouter，因此注入文本中不得出现激活上下文块。
        为使该性质可验证，消息刻意包含触发词并先做前置校验：否则「未出现
        激活块」在路由返回空结果时同样成立，断言将失去检出能力。
        """
        await self._seed_initial_state()
        # 前置校验：确认该消息确实能触发路由，保证下面的断言不是恒真
        probe = await KeywordRouter(max_entries=3).route(
            TASK_USER_MSG, SessionState(session_id=SESSION_ID), self._registry
        )
        self.assertEqual(probe.needed_ids, ["skill_battle"])

        messages = [
            Message(role="system", content="CRON_PROMPT + Persona"),
            Message(role="user", content="历史1"),
            Message(role="assistant", content="回复1"),
        ]

        await self._injector.on_agent_begin(
            _FakeEvent(message_str=TASK_USER_MSG, platform_name="cron"),
            _FakeRunContext(messages),
        )

        self.assertEqual(
            messages[0].content, "CRON_PROMPT + Persona\n\n" + _GOLDEN_TASK_XML
        )
        self.assertNotIn("<active_context>", messages[0].content)
        self.assertEqual(messages[1].content, "历史1")
        self.assertEqual(messages[2].content, "回复1")
        self.assertEqual(len(messages), 3)

        records = self._recorder.get_recent(SESSION_ID, limit=10)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].injection_source, "task_agent_begin")
        self.assertTrue(records[0].injected_successfully)
        self.assertEqual(records[0].injection_xml, _GOLDEN_TASK_XML)


if __name__ == "__main__":
    unittest.main()
