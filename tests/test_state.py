"""core.cognition.state（动态状态存储）的单元测试 v0.2。"""

from __future__ import annotations

import json
import logging
import tempfile
import unittest
from pathlib import Path
from unittest import IsolatedAsyncioTestCase

from astrbot_plugin_Firefly.core.cognition.state import StateStore
from astrbot_plugin_Firefly.core.models import (
    ActivatedEntry,
    ActiveContext,
    SessionState,
)


class TestStateStore(IsolatedAsyncioTestCase):
    async def test_get_default(self):
        """验证未存在会话返回默认状态。"""
        with tempfile.TemporaryDirectory() as tmp:
            store = StateStore(Path(tmp) / "state.json")
            state = await store.get("s1")
            self.assertEqual(state.session_id, "s1")
            self.assertEqual(state.mood, "平静")

    async def test_set_and_persist_roundtrip(self):
        """验证写入后能完整落盘并读回（含激活上下文轮数）。"""
        with tempfile.TemporaryDirectory() as tmp:
            data_file = Path(tmp) / "state.json"
            store = StateStore(data_file)
            s = SessionState(
                session_id="s1",
                mood="开心",
                recent_topics=["崩铁"],
                last_message_at=1.0,
                updated_at=2.0,
            )
            s.active_context.turn_count = 3
            await store.set("s1", s)
            self.assertTrue(data_file.is_file())

            store2 = StateStore(data_file)
            warnings = await store2.load()
            self.assertEqual(warnings, [])
            state = await store2.get("s1")
            self.assertEqual(state.mood, "开心")
            self.assertEqual(state.recent_topics, ["崩铁"])
            self.assertEqual(state.active_context.turn_count, 3)

    async def test_active_context_serialization(self):
        """验证激活上下文的序列化与反序列化。"""
        with tempfile.TemporaryDirectory() as tmp:
            data_file = Path(tmp) / "state.json"
            store = StateStore(data_file)
            s = SessionState(session_id="s1")
            s.active_context = ActiveContext(
                entries=[
                    ActivatedEntry(
                        entry_id="skill_x",
                        remaining_ttl=3,
                        strength=0.8,
                        activated_at_turn=1,
                    )
                ],
                turn_count=2,
            )
            await store.set("s1", s)

            store2 = StateStore(data_file)
            await store2.load()
            state = await store2.get("s1")
            self.assertEqual(state.active_context.turn_count, 2)
            self.assertEqual(len(state.active_context.entries), 1)
            self.assertEqual(state.active_context.entries[0].entry_id, "skill_x")

    async def test_load_ignores_legacy_keys(self):
        """验证旧状态文件中的关系/叙事遗留键被忽略，不崩溃。"""
        with tempfile.TemporaryDirectory() as tmp:
            data_file = Path(tmp) / "state.json"
            data_file.write_text(
                json.dumps(
                    {
                        "s1": {
                            "session_id": "s1",
                            "mood": "难过",
                            "relationship_stage": "friend",
                            "relationship": "朋友",
                            "relationship_note": "旧数据",
                            "active_context": {
                                "narrative_thread": "arc_01",
                                "entries": [],
                                "turn_count": 1,
                            },
                            "recent_topics": [],
                            "last_message_at": 1.0,
                            "updated_at": 2.0,
                        }
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            store = StateStore(data_file)
            warnings = await store.load()
            self.assertEqual(warnings, [])
            state = await store.get("s1")
            self.assertEqual(state.mood, "难过")
            self.assertFalse(hasattr(state, "relationship_stage"))
            self.assertFalse(hasattr(state.active_context, "narrative_thread"))

    async def test_reset(self):
        """验证重置后会话状态恢复默认。"""
        with tempfile.TemporaryDirectory() as tmp:
            store = StateStore(Path(tmp) / "state.json")
            await store.set("s1", SessionState(session_id="s1", mood="难过"))
            state = await store.reset("s1")
            self.assertEqual(state.mood, "平静")
            self.assertEqual(await store.get("s1"), state)

    async def test_all_and_session_isolation(self):
        """验证多会话状态相互隔离。"""
        with tempfile.TemporaryDirectory() as tmp:
            store = StateStore(Path(tmp) / "state.json", persist=False)
            await store.set("a", SessionState(session_id="a", mood="开心"))
            await store.set("b", SessionState(session_id="b", mood="难过"))
            self.assertEqual(set((await store.all()).keys()), {"a", "b"})
            self.assertEqual((await store.get("a")).mood, "开心")

    async def test_load_corrupt_file(self):
        """验证损坏的状态文件不会导致崩溃，返回告警并清空状态。"""
        with tempfile.TemporaryDirectory() as tmp:
            data_file = Path(tmp) / "state.json"
            data_file.write_text("{not json", encoding="utf-8")
            store = StateStore(data_file)
            warnings = await store.load()
            self.assertTrue(warnings)
            self.assertEqual(await store.all(), {})

    async def test_close_saves(self):
        """验证 close() 时执行最终落盘。"""
        with tempfile.TemporaryDirectory() as tmp:
            data_file = Path(tmp) / "state.json"
            store = StateStore(data_file)
            await store.set("s1", SessionState(session_id="s1"))
            await store.close()
            payload = json.loads(data_file.read_text(encoding="utf-8"))
            self.assertIn("s1", payload)

    async def test_persist_failure_is_logged(self):
        """P1-6 S1：落盘失败必须留下告警，不得静默丢数据。

        构造方式：让状态文件的父路径是一个普通文件，使 mkdir 抛 OSError。
        """
        with tempfile.TemporaryDirectory() as tmp:
            blocker = Path(tmp) / "blocker"
            blocker.write_text("not a directory", encoding="utf-8")
            logger = logging.getLogger("test_state_persist_failure")
            store = StateStore(blocker / "state.json", logger=logger)

            with self.assertLogs(logger, level="WARNING") as captured:
                await store.set("s1", SessionState(session_id="s1"))

            self.assertTrue(
                any("落盘失败" in message for message in captured.output),
                f"未记录落盘失败告警：{captured.output}",
            )

    async def test_persist_failure_without_logger_still_does_not_raise(self):
        """未注入 logger 时落盘失败仍不得抛异常（保持原有容错语义）。"""
        with tempfile.TemporaryDirectory() as tmp:
            blocker = Path(tmp) / "blocker"
            blocker.write_text("not a directory", encoding="utf-8")
            store = StateStore(blocker / "state.json")

            await store.set("s1", SessionState(session_id="s1"))

            self.assertEqual((await store.get("s1")).session_id, "s1")


class TestSessionStateSnapshot(unittest.TestCase):
    """P1-1：SessionState.snapshot() 的等价性与可变对象隔离。"""

    @staticmethod
    def _make_source() -> SessionState:
        """构造一份带有可变子对象的状态，用于验证快照隔离。

        Returns:
            含 recent_topics 与 active_context 的会话状态。
        """
        source = SessionState(
            session_id="s1",
            mood="委屈",
            mood_intensity=0.8,
            recent_topics=["取快递"],
            unanswered_count=2,
            last_user_at=111.0,
        )
        source.active_context = ActiveContext(
            entries=[
                ActivatedEntry(
                    entry_id="skill_x",
                    remaining_ttl=3,
                    strength=0.8,
                    activated_at_turn=1,
                )
            ],
            turn_count=2,
        )
        return source

    def test_snapshot_is_equal_to_source(self):
        """快照与源状态逐字段等值。"""
        source = self._make_source()
        self.assertEqual(source.snapshot(), source)

    def test_snapshot_does_not_share_mutable_objects(self):
        """修改源状态的可变子对象不得影响快照。

        浅拷贝（如 dataclasses.replace）会共享 recent_topics 与
        active_context，本条断言用于检出该退化。
        """
        source = self._make_source()
        snap = source.snapshot()

        source.recent_topics.append("新话题")
        source.active_context.entries.append(
            ActivatedEntry(entry_id="skill_y", remaining_ttl=1)
        )
        source.active_context.turn_count = 99

        self.assertEqual(snap.recent_topics, ["取快递"])
        self.assertEqual(len(snap.active_context.entries), 1)
        self.assertEqual(snap.active_context.entries[0].entry_id, "skill_x")
        self.assertEqual(snap.active_context.turn_count, 2)

    def test_snapshot_preserves_all_fields(self):
        """快照保留全部字段（防止序列化往返丢字段）。"""
        source = self._make_source()
        snap = source.snapshot()
        self.assertEqual(snap.session_id, "s1")
        self.assertEqual(snap.mood, "委屈")
        self.assertEqual(snap.mood_intensity, 0.8)
        self.assertEqual(snap.unanswered_count, 2)
        self.assertEqual(snap.last_user_at, 111.0)
        entry = snap.active_context.entries[0]
        self.assertEqual(entry.remaining_ttl, 3)
        self.assertEqual(round(entry.strength, 2), 0.8)
        self.assertEqual(entry.activated_at_turn, 1)


if __name__ == "__main__":
    unittest.main()
