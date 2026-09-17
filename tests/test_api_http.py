"""adapter.api.http.HttpHelpers 的单元测试（P1-7）。

`_get_query` / `_get_json` 原先在 `DebugApi` 与 `RoleApi` 中各有一份实现，
行为必须一致。本文件直接覆盖共享实现本身，并锁定两处易退化的契约：

- 空请求体返回 `{}` 而不是 `None`；
- 解析异常被吞掉并降级为 `{}`，不向调用方抛出。
"""

from __future__ import annotations

import unittest
from typing import Any
from unittest import IsolatedAsyncioTestCase, mock

from astrbot_plugin_Firefly.adapter.api.http import HttpHelpers


class _FakeRequest:
    """模拟 quart.request 的最小替身。"""

    def __init__(self, args: dict[str, str] | None = None, payload: Any = None) -> None:
        """构造替身请求。

        Args:
            args: 查询参数映射。
            payload: get_json() 的返回载荷；为 Exception 实例时改抛该异常。
        """
        self.args = args or {}
        self._payload = payload

    async def get_json(self) -> Any:
        """返回预置载荷，载荷为异常实例时抛出该异常。"""
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class TestGetQuery(unittest.TestCase):
    """查询参数读取。"""

    def test_returns_value(self) -> None:
        """命中参数时返回其值。"""
        with mock.patch("quart.request", _FakeRequest(args={"k": "v"})):
            self.assertEqual(HttpHelpers._get_query("k"), "v")

    def test_returns_none_when_absent(self) -> None:
        """参数不存在时返回 None（调用方依赖该约定）。"""
        with mock.patch("quart.request", _FakeRequest(args={})):
            self.assertIsNone(HttpHelpers._get_query("missing"))


class TestGetJson(IsolatedAsyncioTestCase):
    """请求体解析与降级。"""

    async def test_parses_payload(self) -> None:
        """正常请求体原样返回。"""
        with mock.patch("quart.request", _FakeRequest(payload={"a": 1})):
            self.assertEqual(await HttpHelpers._get_json(), {"a": 1})

    async def test_empty_body_returns_empty_dict(self) -> None:
        """空请求体返回空字典，而不是 None。"""
        with mock.patch("quart.request", _FakeRequest(payload=None)):
            self.assertEqual(await HttpHelpers._get_json(), {})

    async def test_parse_failure_returns_empty_dict(self) -> None:
        """解析异常被降级为空字典，不向调用方抛出。"""
        with mock.patch("quart.request", _FakeRequest(payload=ValueError("bad"))):
            self.assertEqual(await HttpHelpers._get_json(), {})


if __name__ == "__main__":
    unittest.main()
