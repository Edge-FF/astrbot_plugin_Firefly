"""core.routing.router 的单元测试（KeywordRouter + LLMRouter）。"""

from __future__ import annotations

import asyncio
import logging
import unittest
from pathlib import Path
from typing import Any

from astrbot_plugin_Firefly.core.materials.registry import MaterialRegistry
from astrbot_plugin_Firefly.core.models import (
    MaterialEntry,
    SessionState,
)
from astrbot_plugin_Firefly.core.routing.router import KeywordRouter, LLMRouter


def _write_entry(role_dir: Path, rel_path: str, frontmatter: str, body: str) -> None:
    """写入带 front-matter 的资料文件。

    Args:
        role_dir: role 目录路径。
        rel_path: 相对路径。
        frontmatter: front-matter 内容。
        body: 正文内容。
    """
    path = role_dir / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\n{frontmatter}\n---\n{body}",
        encoding="utf-8",
    )


def _run(coro):
    """同步运行 async 协程（适配不同事件循环环境）。"""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor() as pool:
        fut = pool.submit(asyncio.run, coro)
        return fut.result()


class TestKeywordRouter(unittest.TestCase):
    def setUp(self):
        """构造关键词路由与临时资料库。"""
        self.router = KeywordRouter(max_entries=3)
        import tempfile

        self._tmp = tempfile.TemporaryDirectory()
        role_dir = Path(self._tmp.name)
        _write_entry(
            role_dir, "persona_base.md", "tier: 1\nkind: persona", "核心人格内容"
        )
        _write_entry(
            role_dir,
            "skills/battle.md",
            "tier: 3\nkind: skill\nkeywords: [战斗, 打架]\npriority: 80",
            "战斗说明",
        )
        _write_entry(
            role_dir,
            "skills/cooking.md",
            "tier: 3\nkind: skill\nkeywords: [甜点, 烘焙]\npriority: 50",
            "烹饪技能",
        )
        self.registry = MaterialRegistry(role_dir)
        report = self.registry.load()
        self.assertGreater(len(report.entries), 0, f"load failed: {report.warnings}")

    def tearDown(self):
        """清理临时目录。"""
        if hasattr(self, "_tmp"):
            self._tmp.cleanup()

    def test_keyword_match(self):
        """验证关键词命中条目。"""
        result = _run(
            self.router.route(
                "我们来战斗吧", SessionState(session_id="s1"), self.registry
            )
        )
        self.assertIn("battle", result.needed_ids)
        self.assertEqual(result.source, "keyword")

    def test_no_match(self):
        """验证无命中时返回空列表。"""
        result = _run(
            self.router.route(
                "今天天气不错", SessionState(session_id="s1"), self.registry
            )
        )
        self.assertEqual(result.needed_ids, [])

    def test_empty_text(self):
        """验证空文本不触发路由。"""
        result = _run(
            self.router.route("", SessionState(session_id="s1"), self.registry)
        )
        self.assertEqual(result.needed_ids, [])

    def test_sort_by_priority(self):
        """验证命中条目按优先级排序。"""
        result = _run(
            self.router.route(
                "战斗和甜点都很棒", SessionState(session_id="s1"), self.registry
            )
        )
        self.assertEqual(result.needed_ids[0], "battle")

    def test_tier1_not_routed(self):
        """验证 Tier1/2 常驻资料不参与路由。"""
        result = _run(
            self.router.route(
                "核心人格内容", SessionState(session_id="s1"), self.registry
            )
        )
        self.assertNotIn("persona_base", result.needed_ids)


class TestLLMRouter(unittest.TestCase):
    def setUp(self):
        """构造禁用 LLM 的路由器。"""
        self.router = LLMRouter(
            llm_generate=None,
            timeout=3.0,
            cache_enabled=False,
        )

    def test_no_llm_generate_returns_none(self):
        """验证无生成函数时返回 None（触发降级）。"""
        result = _run(
            self.router.route(
                "测试",
                SessionState(session_id="s1"),
                _make_empty_registry(),
            )
        )
        self.assertIsNone(result)

    def test_parse_valid_json(self):
        """验证合法 JSON 的解析。"""
        raw = '{"needed_ids": ["a", "b"], "signals": {"user_emotion": "开心"}}'
        registry = _make_fake_registry(["a", "b"])
        result = LLMRouter._parse_response(raw, registry)
        self.assertEqual(result.needed_ids, ["a", "b"])
        self.assertEqual(result.signals.user_emotion, "开心")
        self.assertEqual(result.source, "llm")

    def test_parse_filters_invalid_ids(self):
        """验证非法 ID 被过滤。"""
        raw = '{"needed_ids": ["a", "invalid_id"]}'
        registry = _make_fake_registry(["a"])
        result = LLMRouter._parse_response(raw, registry)
        self.assertEqual(result.needed_ids, ["a"])

    def test_parse_invalid_json_returns_empty(self):
        """验证非法 JSON 返回空结果（安全降级）。"""
        result = LLMRouter._parse_response("not json", _make_fake_registry([]))
        self.assertEqual(result.needed_ids, [])

    def test_parse_failure_is_logged(self):
        """P1-6 S3：解析失败必须留痕，以区分「模型没选」与「解析失败」。"""
        logger = logging.getLogger("test_llm_router_parse_failure")

        with self.assertLogs(logger, level="DEBUG") as captured:
            result = LLMRouter._parse_response(
                "not json", _make_fake_registry([]), logger
            )

        self.assertEqual(result.needed_ids, [])
        self.assertEqual(result.source, "llm")
        self.assertTrue(
            any("解析失败" in message for message in captured.output),
            f"未记录解析失败：{captured.output}",
        )

    def test_generate_failure_is_logged_and_returns_none(self):
        """P1-6 S2：LLM 调用异常必须留痕，且保持返回 None 的降级契约。"""
        logger = logging.getLogger("test_llm_router_failure")

        async def _boom(system_prompt: str, user_prompt: str) -> str:
            """模拟上游 LLM 不可用。"""
            raise RuntimeError("上游不可用")

        router = LLMRouter(
            llm_generate=_boom,
            timeout=3.0,
            cache_enabled=False,
            logger=logger,
        )

        with self.assertLogs(logger, level="DEBUG") as captured:
            result = _run(
                router.route(
                    "测试", SessionState(session_id="s1"), _make_empty_registry()
                )
            )

        self.assertIsNone(result)
        self.assertTrue(
            any("LLM 路由失败" in message for message in captured.output),
            f"未记录路由失败：{captured.output}",
        )


def _make_empty_registry() -> MaterialRegistry:
    """构造空资料库。"""
    import tempfile

    d = tempfile.TemporaryDirectory()
    r = MaterialRegistry(Path(d.name))
    r.load()
    return r


def _make_fake_registry(ids: list[str]) -> Any:
    """构造假 registry（仅实现 all_entries）。

    Args:
        ids: 条目 ID 列表。

    Returns:
        假的注册表对象。
    """

    class _FakeRegistry:
        def all_entries(self):
            """返回构造的条目列表。"""
            return [
                MaterialEntry(
                    id=eid,
                    title=eid,
                    tier=3,
                    kind="skill",
                    source_path="<t>",
                    content="test",
                )
                for eid in ids
            ]

    return _FakeRegistry()


if __name__ == "__main__":
    unittest.main()
