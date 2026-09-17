"""core.materials.tier_rules（tier/kind 推断规则）的单元测试（P1-3）。

该规则同时被 registry（索引侧）与 role_store（写入侧）使用，因此这里是它的
直接契约测试：锁定完整规则表与优先级，防止两侧行为各自漂移。

测试表覆盖：目录规则、文件名规则、目录优先于文件名、多级路径、Windows
分隔符、以及无规则命中时的默认推断。
"""

from __future__ import annotations

import unittest

from astrbot_plugin_Firefly.core import consts
from astrbot_plugin_Firefly.core.materials.tier_rules import infer_tier_kind


class TestDirectoryRules(unittest.TestCase):
    """目录名规则及其优先级。"""

    def test_directory_rules_are_frozen(self) -> None:
        """完整目录规则表（中英文）逐项锁定。"""
        cases = [
            ("skills/battle.md", consts.TIER_SKILL_LORE, consts.KIND_SKILL),
            ("npc_profiles/kafka.md", consts.TIER_SKILL_LORE, consts.KIND_LORE),
            ("world_lore/aeon.md", consts.TIER_WORLD_NARRATIVE, consts.KIND_LORE),
            ("narratives/arc.md", consts.TIER_WORLD_NARRATIVE, consts.KIND_NARRATIVE),
            ("技能/机甲.md", consts.TIER_SKILL_LORE, consts.KIND_SKILL),
            ("人物关系/三月七.md", consts.TIER_SKILL_LORE, consts.KIND_LORE),
            ("世界知识/星神.md", consts.TIER_WORLD_NARRATIVE, consts.KIND_LORE),
            ("故事/自我叙事.md", consts.TIER_CORE_PERSONA, consts.KIND_PERSONA),
        ]
        for rel, tier, kind in cases:
            with self.subTest(rel=rel):
                self.assertEqual(infer_tier_kind(rel), (tier, kind))

    def test_directory_rule_wins_over_filename_rule(self) -> None:
        """narratives/ 下的 persona_narrative.md 必须归 Tier4。

        这是规则表里唯一的「目录名与文件名冲突」场景：若文件名规则先判定，
        它会被误判成 Tier1 常驻人格，因此优先级必须锁定。
        """
        self.assertEqual(
            infer_tier_kind("narratives/persona_narrative.md"),
            (consts.TIER_WORLD_NARRATIVE, consts.KIND_NARRATIVE),
        )

    def test_nested_paths_use_top_level_segment(self) -> None:
        """多级子目录按顶层目录判定。"""
        self.assertEqual(
            infer_tier_kind("世界知识/派系/存护命途/星际和平公司.md"),
            (consts.TIER_WORLD_NARRATIVE, consts.KIND_LORE),
        )

    def test_backslash_separator_is_supported(self) -> None:
        """Windows 风格路径分隔符同样生效。"""
        self.assertEqual(
            infer_tier_kind("技能\\机甲.md"),
            (consts.TIER_SKILL_LORE, consts.KIND_SKILL),
        )


class TestFilenameRules(unittest.TestCase):
    """文件名规则（仅当目录名未命中时生效）。"""

    def test_filename_rules_are_frozen(self) -> None:
        """完整文件名规则表逐项锁定。"""
        cases = [
            ("persona_base.md", consts.TIER_CORE_PERSONA, consts.KIND_PERSONA),
            ("persona_narrative.md", consts.TIER_CORE_PERSONA, consts.KIND_PERSONA),
            ("基础人设.md", consts.TIER_CORE_PERSONA, consts.KIND_PERSONA),
        ]
        for rel, tier, kind in cases:
            with self.subTest(rel=rel):
                self.assertEqual(infer_tier_kind(rel), (tier, kind))


class TestDefaultInference(unittest.TestCase):
    """无规则命中时的默认推断。"""

    def test_root_markdown_defaults_to_core_persona(self) -> None:
        """role/ 根目录下的其它 .md 默认为常驻 Tier1。"""
        self.assertEqual(
            infer_tier_kind("随便写的.md"),
            (consts.TIER_CORE_PERSONA, consts.KIND_PERSONA),
        )

    def test_unknown_subdirectory_defaults_to_skill_lore(self) -> None:
        """未知子目录默认为按需 Tier3。"""
        self.assertEqual(
            infer_tier_kind("自定义目录/内容.md"),
            (consts.TIER_SKILL_LORE, consts.KIND_LORE),
        )

    def test_empty_path_falls_back_to_core_persona(self) -> None:
        """空路径作为边界输入，回落到默认推断且不抛异常。"""
        self.assertEqual(
            infer_tier_kind(""),
            (consts.TIER_CORE_PERSONA, consts.KIND_PERSONA),
        )


if __name__ == "__main__":
    unittest.main()
