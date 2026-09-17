"""adapter.debug_api 注入日志清空接口的单元测试。

重点覆盖 injections/clear 与 sessions/reset 的职责差异：
前者只清调试记录，不改动任何会话状态。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any

from astrbot_plugin_Firefly.adapter.debug_api import DebugApi
from astrbot_plugin_Firefly.adapter.debug_recorder import DebugRecorder
from astrbot_plugin_Firefly.core.models import InjectionRecord, SessionState
from astrbot_plugin_Firefly.core.state import StateStore


def _make_api(
    recorder: DebugRecorder, store: StateStore, body: dict[str, Any]
) -> DebugApi:
    """构造只注入被测依赖的 DebugApi，并桩掉请求体读取。

    Args:
        recorder: 调试记录器。
        store: 会话状态仓库。
        body: 替身请求体。

    Returns:
        可直接调用内部处理函数的 DebugApi 实例。
    """
    api = DebugApi(
        context=None,
        registry=None,
        store=store,
        recorder=recorder,
        config_getter=None,
        router=None,
        ctx_manager=None,
        builder=None,
    )

    async def _fake_get_json() -> dict[str, Any]:
        return body

    api._get_json = _fake_get_json
    return api


class TestInjectionsClear(unittest.IsolatedAsyncioTestCase):
    """injections/clear 的行为边界。"""

    def setUp(self) -> None:
        """准备临时状态文件与记录器。"""
        self._tmp = tempfile.TemporaryDirectory()
        self.recorder = DebugRecorder()
        self.store = StateStore(Path(self._tmp.name) / "state.json", persist=False)

    def tearDown(self) -> None:
        """清理临时目录。"""
        self._tmp.cleanup()

    async def test_clear_all_sessions(self) -> None:
        """session_id 留空时清空全部会话的记录。"""
        self.recorder.record(InjectionRecord(record_id="r1", session_id="a"))
        self.recorder.record(InjectionRecord(record_id="r2", session_id="b"))
        api = _make_api(self.recorder, self.store, {"session_id": ""})

        resp = await api._injections_clear()

        self.assertEqual(resp["status"], "ok")
        self.assertEqual(resp["data"]["removed"], 2)
        self.assertEqual(self.recorder.count(), 0)

    async def test_clear_one_session_keeps_others(self) -> None:
        """指定 session_id 时只清该会话，其它会话记录保留。"""
        self.recorder.record(InjectionRecord(record_id="r1", session_id="a"))
        self.recorder.record(InjectionRecord(record_id="r2", session_id="b"))
        api = _make_api(self.recorder, self.store, {"session_id": "a"})

        resp = await api._injections_clear()

        self.assertEqual(resp["data"]["removed"], 1)
        self.assertEqual(self.recorder.count(), 1)
        self.assertEqual(self.recorder.get_recent()[0].session_id, "b")

    async def test_clear_keeps_session_state(self) -> None:
        """清空日志不得改动会话状态（与 sessions/reset 的关键区别）。"""
        await self.store.set("a", SessionState(session_id="a", mood="开心"))
        self.recorder.record(InjectionRecord(record_id="r1", session_id="a"))
        api = _make_api(self.recorder, self.store, {"session_id": "a"})

        await api._injections_clear()

        state = await self.store.get("a")
        self.assertEqual(state.mood, "开心")
        self.assertEqual(self.recorder.count(), 0)

    async def test_clear_missing_body_is_treated_as_clear_all(self) -> None:
        """请求体缺失时按清空全部处理，不报错。"""
        self.recorder.record(InjectionRecord(record_id="r1", session_id="a"))
        api = _make_api(self.recorder, self.store, {})

        resp = await api._injections_clear()

        self.assertEqual(resp["status"], "ok")
        self.assertEqual(resp["data"]["removed"], 1)
        self.assertEqual(self.recorder.count(), 0)
