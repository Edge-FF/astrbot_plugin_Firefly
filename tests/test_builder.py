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
    defaults = dict(
        id="test", title="Test", tier=1, kind="persona",
        source_path="<t>", content="默认内容",
    )
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
        tier1 = [_entry(id="persona_base", content="我是流萤。", tier=1, kind="persona")]
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
        active_entry = _entry(id="skill_x", content="弱激活内容。", tier=3, kind="skill")

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


if __name__ == "__main__":
    unittest.main()
