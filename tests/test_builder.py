"""core.shell.builder（认知外壳组装）的单元测试。"""

from __future__ import annotations

import unittest

from astrbot_plugin_Firefly.core.models import (
    ActivatedEntry,
    MaterialEntry,
    SessionState,
)
from astrbot_plugin_Firefly.core.shell.builder import ShellBuilder


def _entry(**kwargs) -> MaterialEntry:
    """构造带默认值的 MaterialEntry。

    Args:
        **kwargs: 覆盖默认字段的值。

    Returns:
        构造好的条目。
    """
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


class TestShellBuilder(unittest.TestCase):
    def setUp(self):
        """初始化组装器与测试状态。"""
        self.builder = ShellBuilder(max_tokens=3000)
        self.state = SessionState(
            session_id="s1",
            mood="开心",
            recent_topics=["崩铁"],
        )

    def test_build_all_layers(self):
        """验证组装结果包含各层块与激活条目。"""
        tier1 = [
            _entry(id="persona_base", content="我是流萤。", tier=1, kind="persona")
        ]
        active_ae = ActivatedEntry(entry_id="skill_x", remaining_ttl=3, strength=1.0)
        active_entry = _entry(id="skill_x", content="战斗说明。", tier=3, kind="skill")

        result = self.builder.build(
            tier1_entries=tier1,
            state=self.state,
            active_entries=[(active_ae, active_entry)],
        )
        text = result.text
        self.assertIn("<cognitive_shell>", text)
        self.assertIn("<static_core>", text)
        self.assertIn("<dynamic_state>", text)
        self.assertIn("<active_context>", text)
        self.assertIn("战斗说明", text)
        self.assertNotIn("<relationship_stage", text)

    def test_strength_marking(self):
        """验证弱激活条目带有背景参考标记。"""
        tier1 = [_entry(id="persona_base", content="我是流萤。", tier=1)]
        active_ae = ActivatedEntry(entry_id="skill_x", remaining_ttl=2, strength=0.3)
        active_entry = _entry(
            id="skill_x", content="弱激活内容。", tier=3, kind="skill"
        )

        result = self.builder.build(
            tier1_entries=tier1,
            state=self.state,
            active_entries=[(active_ae, active_entry)],
        )
        self.assertIn("背景参考，弱激活", result.text)

    def test_empty(self):
        """验证空输入仍产出外壳框架文本。"""
        result = self.builder.build(
            tier1_entries=[],
            state=self.state,
            active_entries=[],
        )
        self.assertFalse(result.is_empty())
        self.assertIn("cognitive_shell", result.text)

    def test_dynamic_state_shows_mood_and_topics(self):
        """验证动态状态块写入心情与最近话题。"""
        tier1 = [_entry(id="persona_base", content="核心人格。", tier=1)]
        result = self.builder.build(
            tier1_entries=tier1,
            state=self.state,
            active_entries=[],
        )
        self.assertIn("心情：开心", result.text)
        self.assertIn("崩铁", result.text)


class TestTier1Reserved(unittest.TestCase):
    """`tier1_reserved` 必须真实参与激活条目的预算分配（曾硬编码为 830）。"""

    def _truncated(self, reserved: int) -> tuple[str, ...]:
        """用给定预留量组装一条超长激活条目，返回被裁剪的条目 ID。

        Args:
            reserved: 常驻块预留量。

        Returns:
            被裁剪的条目 ID 元组。
        """
        builder = ShellBuilder(max_tokens=300, tier1_reserved=reserved)
        entry = _entry(id="skill_big", content="内" * 300, tier=3, kind="skill")
        active = ActivatedEntry(entry_id="skill_big", remaining_ttl=3, strength=1.0)
        result = builder.build(
            tier1_entries=[],
            state=SessionState(session_id="s1"),
            active_entries=[(active, entry)],
        )
        return result.truncated

    def test_default_reservation_matches_legacy_budget(self):
        """默认预留量为 830 —— 与接通前的硬编码一致，保证行为不变。"""
        self.assertEqual(ShellBuilder()._tier1_reserved, 830)

    def test_larger_reservation_truncates_active_entry(self):
        """预留量调大后，同样内容会被挤出预算并被裁剪。"""
        self.assertEqual(self._truncated(reserved=100), (), "预留量小时不应裁剪")
        self.assertEqual(
            self._truncated(reserved=830), ("skill_big",), "预留量大时应裁剪"
        )

    def test_zero_reservation_gives_all_budget_to_active(self):
        """预留量为 0 时全部预算交给激活条目，不触发裁剪。"""
        builder = ShellBuilder(max_tokens=300, tier1_reserved=0)
        entry = _entry(id="skill_small", content="短" * 20, tier=3, kind="skill")
        active = ActivatedEntry(entry_id="skill_small", remaining_ttl=3, strength=1.0)
        result = builder.build(
            tier1_entries=[],
            state=SessionState(session_id="s1"),
            active_entries=[(active, entry)],
        )
        self.assertIn("skill_small", result.text)
        self.assertEqual(result.truncated, ())

    def test_negative_reservation_is_clamped_to_zero(self):
        """负值被钳制为 0，不会把激活预算放大到超过总预算。"""
        self.assertEqual(ShellBuilder(tier1_reserved=-500)._tier1_reserved, 0)


if __name__ == "__main__":
    unittest.main()
