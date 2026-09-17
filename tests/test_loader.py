"""core.materials.parsers 与 core.materials.registry 的单元测试。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from astrbot_plugin_Firefly.core import consts
from astrbot_plugin_Firefly.core.materials.parsers import (
    coerce_int,
    coerce_str_list,
    parse_frontmatter,
    strip_html_comments,
)
from astrbot_plugin_Firefly.core.models import MaterialEntry
from astrbot_plugin_Firefly.core.materials.registry import MaterialRegistry


def _write(role_dir: Path, rel: str, text: str) -> None:
    """在 role 目录下写入一个文件（自动创建父目录）。

    Args:
        role_dir: role 目录路径。
        rel: 相对路径。
        text: 文件内容。
    """
    path = role_dir / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class TestParseFrontmatter(unittest.TestCase):
    def test_no_frontmatter(self):
        """验证无 front-matter 时原样返回。"""
        meta, body = parse_frontmatter("hello")
        self.assertEqual(meta, {})
        self.assertEqual(body, "hello")

    def test_scalars_and_list(self):
        """验证标量与列表字段的解析。"""
        text = """---
type: on_demand
title: 战斗技巧
priority: 80
keywords: ["战斗", "打架"]
enabled: true
---
正文内容
"""
        meta, body = parse_frontmatter(text)
        self.assertEqual(meta["type"], "on_demand")
        self.assertEqual(meta["priority"], 80)
        self.assertEqual(meta["keywords"], ["战斗", "打架"])
        self.assertTrue(meta["enabled"])
        self.assertEqual(body, "正文内容")

    def test_strip_html_comments(self):
        """验证 HTML 注释被清除。"""
        text = "<!-- 占位说明 -->\n正文内容\n<!-- 结尾注释 -->"
        self.assertEqual(strip_html_comments(text), "\n正文内容\n")

    def test_quoted_scalar_strips_quotes_and_unescapes(self):
        """验证成对引号被剥离：含 : / # / 方括号的值不再被误解析。"""
        text = '---\ntitle: "战斗: 技巧 #1"\nid: \'skill_a\'\n---\n正文。'
        meta, body = parse_frontmatter(text)
        self.assertEqual(meta["title"], "战斗: 技巧 #1")
        self.assertEqual(meta["id"], "skill_a")
        self.assertEqual(body, "正文。")

    def test_quoted_scalar_stays_string(self):
        """验证引号表示显式字符串：不再做数字/布尔推断。"""
        text = '---\npriority: "80"\nflag: "true"\n---\n正文。'
        meta, _ = parse_frontmatter(text)
        self.assertEqual(meta["priority"], "80")
        self.assertEqual(meta["flag"], "true")

    def test_unquoted_scalar_still_typed(self):
        """验证未加引号的值仍保持原有类型推断。"""
        text = "---\npriority: 80\nratio: 1.5\nflag: true\nnothing: null\n---\n正文。"
        meta, _ = parse_frontmatter(text)
        self.assertEqual(meta["priority"], 80)
        self.assertEqual(meta["ratio"], 1.5)
        self.assertIs(meta["flag"], True)
        self.assertIsNone(meta["nothing"])

    def test_escape_sequences_inside_quotes(self):
        """验证引号内的转义序列被还原。"""
        text = '---\ntitle: "a\\"b\\\\c\\nd"\n---\n正文。'
        meta, _ = parse_frontmatter(text)
        self.assertEqual(meta["title"], 'a"b\\c\nd')

    def test_list_quoted_items_with_comma(self):
        """验证列表元素支持引号，且引号内的逗号不参与切分。"""
        text = '---\nkeywords: ["a,b", \'c\', d]\n---\n正文。'
        meta, _ = parse_frontmatter(text)
        self.assertEqual(meta["keywords"], ["a,b", "c", "d"])

    def test_list_item_with_escaped_quote(self):
        """验证列表元素内的转义引号被还原且不提前闭合。"""
        text = '---\nkeywords: ["a\\"b", c]\n---\n正文。'
        meta, _ = parse_frontmatter(text)
        self.assertEqual(meta["keywords"], ['a"b', "c"])

    def test_hash_is_comment_only_at_line_start(self):
        """验证 `#` 仅在行首算注释，行内的 `#` 属于值本身。"""
        text = "---\n# 注释行\ntitle: a#b\n---\n正文。"
        meta, _ = parse_frontmatter(text)
        self.assertEqual(list(meta.keys()), ["title"])
        self.assertEqual(meta["title"], "a#b")

    def test_serializer_style_text_roundtrips(self):
        """验证序列化器风格的引号写法可无损读回（S4 往返前提）。"""
        text = '---\nid: "a: b"\ntags: ["x,y"]\ntier: 3\n---\n正文。'
        meta, _ = parse_frontmatter(text)
        self.assertEqual(meta["id"], "a: b")
        self.assertEqual(meta["tags"], ["x,y"])
        self.assertEqual(meta["tier"], 3)


class TestCoerceHelpers(unittest.TestCase):
    """front-matter 值的降级转换工具。"""

    def test_coerce_int_fallbacks(self) -> None:
        """可转换的值正常返回；脏值返回 fallback 并记录告警。"""
        warnings: list[str] = []
        self.assertEqual(coerce_int(3, 1), 3)
        self.assertEqual(coerce_int("3", 1), 3)
        self.assertEqual(coerce_int(3.7, 1), 3)
        self.assertEqual(coerce_int(None, 1), 1)
        self.assertEqual(coerce_int("abc", 1, warnings, "x.md 的 tier"), 1)
        self.assertEqual(coerce_int([1], 1, warnings, "x.md 的 tier"), 1)
        self.assertEqual(len(warnings), 2)
        self.assertTrue(all("不是整数" in w for w in warnings))

    def test_coerce_str_list(self) -> None:
        """标量视为单项列表，避免被 tuple() 拆成字符。"""
        self.assertEqual(coerce_str_list(None), [])
        self.assertEqual(coerce_str_list(""), [])
        self.assertEqual(coerce_str_list("战斗"), ["战斗"])
        self.assertEqual(coerce_str_list(["a", 0]), ["a", "0"])
        self.assertEqual(coerce_str_list(("x",)), ["x"])


class TestMaterialRegistry(unittest.TestCase):
    """MaterialRegistry 测试。"""

    def test_full_scan_with_tiers(self):
        """验证中文目录结构下 tier 的完整扫描。"""
        with tempfile.TemporaryDirectory() as tmp:
            role_dir = Path(tmp)
            _write(
                role_dir, "基础人设.md",
                "---\nid: persona_base\ntitle: 基础人设\n---\n我是流萤。",
            )
            _write(
                role_dir, "故事/自我叙事.md",
                "---\nid: persona_narrative\n---\n叙事人格。",
            )
            _write(
                role_dir, "技能/战斗.md",
                "---\nid: skill_battle\nkind: skill\nkeywords: [战斗]\npriority: 80\n---\n战斗说明。",
            )
            _write(
                role_dir, "人物关系/卡芙卡.md",
                "---\nid: kafka\ntitle: 卡芙卡\nkind: lore\ntags: [星核猎手]\n---\n卡芙卡资料。",
            )

            registry = MaterialRegistry(role_dir, cache_size=50)
            report = registry.load()

            t1 = registry.get_tier(1)
            self.assertEqual(len(t1), 2)

            t2 = registry.get_tier(2)
            self.assertEqual(len(t2), 0)

            t3 = registry.get_tier(3)
            self.assertEqual(len(t3), 2)

            self.assertEqual(registry.entry_count, 4)

    def test_lazy_loading(self):
        """验证 Tier3 内容为懒加载。"""
        with tempfile.TemporaryDirectory() as tmp:
            role_dir = Path(tmp)
            _write(
                role_dir, "技能/测试技能.md",
                "---\nid: test_skill\nkind: skill\n---\n懒加载测试内容。",
            )
            registry = MaterialRegistry(role_dir)
            report = registry.load()

            all_entries = registry.all_entries()
            skill = [e for e in all_entries if e.id == "test_skill"][0]
            self.assertIsNone(skill.content)

            fetched = registry.fetch(["test_skill"])
            self.assertEqual(fetched[0].content, "懒加载测试内容。")

    def test_tier_inference_chinese(self):
        """验证中文目录自动推断 tier/kind。"""
        with tempfile.TemporaryDirectory() as tmp:
            role_dir = Path(tmp)
            _write(role_dir, "基础人设.md", "---\n---\nTier1 内容。")
            _write(role_dir, "故事/自我叙事.md", "---\n---\nTier1 叙事人格内容。")
            _write(role_dir, "技能/skill_x.md", "---\n---\nTier3 内容。")
            _write(role_dir, "人物关系/npc_1.md", "---\n---\nTier3 内容。")

            registry = MaterialRegistry(role_dir)
            report = registry.load()
            self.assertFalse(report.has_warnings(), f"warnings: {report.warnings}")

            for entry in registry.all_entries():
                if entry.source_path.endswith("基础人设.md"):
                    self.assertEqual(entry.tier, 1)
                    self.assertEqual(entry.kind, "persona")
                elif "故事" in entry.source_path:
                    self.assertEqual(entry.tier, 1)
                    self.assertEqual(entry.kind, "persona")
                elif "技能" in entry.source_path or "人物关系" in entry.source_path:
                    self.assertEqual(entry.tier, 3)

    def test_frontmatter_overrides_auto_inference(self):
        """验证 front-matter 显式声明的 tier 覆盖自动推断。"""
        with tempfile.TemporaryDirectory() as tmp:
            role_dir = Path(tmp)
            _write(role_dir, "custom/special.md", "---\ntier: 4\nkind: lore\n---\n内容。")
            registry = MaterialRegistry(role_dir)
            report = registry.load()
            entry = [e for e in registry.all_entries() if e.id == "special"][0]
            self.assertEqual(entry.tier, 4)
            self.assertEqual(entry.kind, "lore")

    def test_lazy_load_failure_is_recorded_once(self):
        """P1-6 S4：懒加载读盘失败必须留痕，且不重复重试读盘。"""
        with tempfile.TemporaryDirectory() as tmp:
            role_dir = Path(tmp)
            _write(role_dir, "技能/skill_x.md", "---\n---\n技能内容。")

            registry = MaterialRegistry(role_dir)
            registry.load()
            # 索引建好后删除文件，制造懒加载读盘失败
            (role_dir / "技能" / "skill_x.md").unlink()

            entry = registry.get("skill_x")
            self.assertEqual(entry.content, "")
            self.assertTrue(
                any("读取失败" in warning for warning in registry.warnings),
                f"未记录懒加载失败：{registry.warnings}",
            )

            # 已降级为空内容，不应每次访问都重试读盘并重复告警
            warnings_after_first_access = len(registry.warnings)
            registry.get("skill_x")
            self.assertEqual(len(registry.warnings), warnings_after_first_access)

    def test_id_conflict_warning(self):
        """验证重复 ID 产生告警。"""
        with tempfile.TemporaryDirectory() as tmp:
            role_dir = Path(tmp)
            _write(role_dir, "技能/a.md", "---\nid: same_id\n---\n内容A。")
            _write(role_dir, "技能/b.md", "---\nid: same_id\n---\n内容B。")
            registry = MaterialRegistry(role_dir)
            report = registry.load()
            self.assertTrue(report.has_warnings())
            self.assertTrue(any("冲突" in w for w in report.warnings))

    def test_dirty_frontmatter_degrades_without_aborting_load(self) -> None:
        """脏 front-matter 只降级 + 告警，不得中断整次加载。"""
        with tempfile.TemporaryDirectory() as tmp:
            role_dir = Path(tmp)
            _write(role_dir, "技能/正常.md", "---\nid: ok_one\nkind: skill\n---\n正常内容。")
            _write(
                role_dir,
                "技能/脏值.md",
                "---\nid: dirty\ntier: abc\npriority: high\n"
                "default_ttl: [1, 2]\ntags: 战斗\nkeywords: [0]\n---\n脏值内容。",
            )

            registry = MaterialRegistry(role_dir)
            report = registry.load()

            self.assertEqual(registry.entry_count, 2)
            dirty = [e for e in registry.all_entries() if e.id == "dirty"][0]
            self.assertEqual(dirty.tier, consts.TIER_SKILL_LORE)
            self.assertEqual(dirty.priority, consts.DEFAULT_PRIORITY)
            self.assertEqual(dirty.default_ttl, consts.DEFAULT_TTL_MAP[consts.KIND_SKILL])
            self.assertEqual(dirty.tags, ("战斗",))
            self.assertEqual(dirty.trigger_keywords, ("0",))
            self.assertTrue(any("不是整数" in w for w in report.warnings))
            self.assertIn("ok_one", registry.index_summary())

    def test_scan_survives_unreadable_dir(self) -> None:
        """子目录不可读时告警并继续扫描其它文件。"""
        with tempfile.TemporaryDirectory() as tmp:
            role_dir = Path(tmp)
            _write(role_dir, "技能/好.md", "---\nid: good\n---\n内容。")
            _write(role_dir, "坏目录/x.md", "---\nid: bad\n---\n内容。")
            original = Path.iterdir

            def fake_iterdir(self: Path):
                if self.name == "坏目录":
                    raise PermissionError("denied")
                return original(self)

            with mock.patch.object(Path, "iterdir", fake_iterdir):
                registry = MaterialRegistry(role_dir)
                report = registry.load()

            self.assertEqual(registry.entry_count, 1)
            self.assertTrue(any("目录读取失败" in w for w in report.warnings))

    def test_index_summary(self):
        """验证索引摘要包含条目 ID 与标签。"""
        with tempfile.TemporaryDirectory() as tmp:
            role_dir = Path(tmp)
            _write(role_dir, "基础人设.md", "---\nid: persona_base\ntitle: 基础\ntags: [人格]\n---\n内容。")
            _write(role_dir, "技能/s1.md", "---\nid: s1\ntitle: 技能1\ntags: [战斗, 星核猎手]\n---\n内容。")
            registry = MaterialRegistry(role_dir)
            registry.load()
            summary = registry.index_summary()
            self.assertIn("persona_base", summary)
            self.assertIn("s1", summary)
            self.assertIn("标签", summary)

    def test_reload_keeps_on_failure(self):
        """验证重载遇到读取失败时保留上一份快照。"""
        with tempfile.TemporaryDirectory() as tmp:
            role_dir = Path(tmp)
            _write(role_dir, "基础人设.md", "---\n---\n旧内容。")
            registry = MaterialRegistry(role_dir)
            registry.load()
            self.assertEqual(registry.entry_count, 1)

            original = Path.iterdir

            def fake_iterdir(self: Path):
                if self == role_dir:
                    raise PermissionError("denied")
                return original(self)

            with mock.patch.object(Path, "iterdir", fake_iterdir):
                report = registry.reload()

            self.assertEqual(registry.entry_count, 1)
            self.assertTrue(any("保留上一份资料快照" in w for w in report.warnings))

    def test_reload_to_empty_dir_clears_index(self):
        """目录可读但已无 .md 时接受空索引（删掉最后一个资料后的正确状态）。"""
        with tempfile.TemporaryDirectory() as tmp:
            role_dir = Path(tmp)
            _write(role_dir, "基础人设.md", "---\n---\n旧内容。")
            registry = MaterialRegistry(role_dir)
            registry.load()
            self.assertEqual(registry.entry_count, 1)

            (role_dir / "基础人设.md").unlink()
            report = registry.reload()

            self.assertEqual(registry.entry_count, 0)
            self.assertFalse(report.has_warnings())

    def test_missing_role_dir(self):
        """验证目录缺失时安全返回告警。"""
        registry = MaterialRegistry(Path("no_such_dir_xyz"))
        report = registry.load()
        self.assertFalse(registry.is_loaded)
        self.assertTrue(report.has_warnings())

    def test_lru_cache_eviction(self):
        """验证 LRU 内容缓存不会无限增长。"""
        with tempfile.TemporaryDirectory() as tmp:
            role_dir = Path(tmp)
            for i in range(12):
                _write(
                    role_dir, f"技能/s{i}.md",
                    f"---\nid: s{i}\n---\n内容{i}。",
                )
            registry = MaterialRegistry(role_dir, cache_size=5)
            registry.load()
            all_ids = [f"s{i}" for i in range(12)]
            fetched = registry.fetch(all_ids)
            self.assertEqual(len(fetched), 12)
            self.assertLessEqual(len(registry._content_cache), 12)


if __name__ == "__main__":
    unittest.main()
