"""core.router.FallbackRouter 的单元测试（P1-5）。

`FallbackRouter` 承载「LLM 优先 / 失败降级关键词」这一策略。原实现内联在
`main.py` 的装配代码里，无法单测；本文件锁定其四种组合行为，避免迁移或
后续调整时改变降级语义。
"""

from __future__ import annotations

import unittest
from unittest import IsolatedAsyncioTestCase

from astrbot_plugin_Firefly.core.models import (
    RouteResult,
    RouteSignals,
    SessionState,
)
from astrbot_plugin_Firefly.core.routing.router import FallbackRouter

# 路由器替身不使用 registry，传 None 即可
_NO_REGISTRY = None


class _StubRouter:
    """返回固定路由结果的替身，并记录被调用次数。"""

    def __init__(self, result: RouteResult | None) -> None:
        """记录要返回的结果。

        Args:
            result: 每次 route() 的返回值；None 模拟 LLM 路由失败。
        """
        self._result = result
        self.calls = 0

    async def route(self, user_msg, session_state, registry) -> RouteResult | None:
        """记录调用并返回固定结果。"""
        self.calls += 1
        return self._result


def _route_result(needed_ids: tuple[str, ...], source: str) -> RouteResult:
    """构造用于断言对比的路由结果。

    Args:
        needed_ids: 命中的条目 ID。
        source: 路由来源标记。

    Returns:
        带空信号的 RouteResult。
    """
    return RouteResult(
        needed_ids=list(needed_ids), signals=RouteSignals(), source=source
    )


class TestFallbackRouter(IsolatedAsyncioTestCase):
    """四种组合：LLM 命中 / 失败降级 / 失败不降级 / 未启用 LLM。"""

    async def test_llm_result_is_used_and_keyword_is_not_called(self) -> None:
        """LLM 命中时直接采用其结果，不触发关键词兜底。"""
        llm = _StubRouter(_route_result(("a",), "llm"))
        keyword = _StubRouter(_route_result(("b",), "keyword"))
        router = FallbackRouter(llm, keyword, fallback_to_keyword=True)

        result = await router.route("x", SessionState(session_id="s1"), _NO_REGISTRY)

        self.assertEqual(result.needed_ids, ["a"])
        self.assertEqual(result.source, "llm")
        self.assertEqual(keyword.calls, 0, "LLM 成功时不应调用关键词路由")

    async def test_llm_failure_falls_back_to_keyword(self) -> None:
        """LLM 返回 None 且允许降级时，使用关键词路由结果。"""
        llm = _StubRouter(None)
        keyword = _StubRouter(_route_result(("b",), "keyword"))
        router = FallbackRouter(llm, keyword, fallback_to_keyword=True)

        result = await router.route("x", SessionState(session_id="s1"), _NO_REGISTRY)

        self.assertEqual(result.needed_ids, ["b"])
        self.assertEqual(keyword.calls, 1)

    async def test_llm_failure_without_fallback_returns_empty(self) -> None:
        """禁止降级时返回空结果，且不得调用关键词路由。"""
        llm = _StubRouter(None)
        keyword = _StubRouter(_route_result(("b",), "keyword"))
        router = FallbackRouter(llm, keyword, fallback_to_keyword=False)

        result = await router.route("x", SessionState(session_id="s1"), _NO_REGISTRY)

        self.assertEqual(result.needed_ids, [])
        self.assertEqual(result.source, "keyword")
        self.assertEqual(keyword.calls, 0, "禁止降级时不应调用关键词路由")

    async def test_missing_llm_uses_keyword_regardless_of_flag(self) -> None:
        """未提供 LLM 路由器时始终走关键词，与降级开关无关。"""
        keyword = _StubRouter(_route_result(("b",), "keyword"))
        router = FallbackRouter(None, keyword, fallback_to_keyword=False)

        result = await router.route("x", SessionState(session_id="s1"), _NO_REGISTRY)

        self.assertEqual(result.needed_ids, ["b"])
        self.assertEqual(keyword.calls, 1)


if __name__ == "__main__":
    unittest.main()
