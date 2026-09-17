"""core.cognition.updaters（会话状态辅助）的单元测试。

心情演化已迁移到 core.cognition.affect（见 test_affect.py），
本文件只覆盖保留在此的纯状态工具：话题队列与话题提取。
"""

from __future__ import annotations

import unittest

from astrbot_plugin_Firefly.core.cognition.updaters import (
    extract_topic,
    update_recent_topics,
)
from astrbot_plugin_Firefly.core.models import SessionState


class TestTopicHelpers(unittest.TestCase):
    def test_extract_topic_strips_punctuation(self):
        """验证话题提取会去掉标点与空白。"""
        self.assertEqual(extract_topic("今天聊崩铁！！！"), "今天聊崩铁")
        self.assertEqual(extract_topic("   "), "")

    def test_update_recent_topics_dedupe_and_order(self):
        """验证话题去重且新话题置顶。"""
        state = SessionState(session_id="s1")
        update_recent_topics(state, "今天聊崩铁", 3)
        update_recent_topics(state, "今天聊崩铁", 3)
        self.assertEqual(len(state.recent_topics), 1)
        update_recent_topics(state, "明天聊别的", 3)
        self.assertEqual(state.recent_topics, ["明天聊别的", "今天聊崩铁"])

    def test_update_recent_topics_capped(self):
        """验证话题队列不超过上限。"""
        state = SessionState(session_id="s1")
        for i in range(5):
            update_recent_topics(state, f"话题{i}", 3)
        self.assertEqual(len(state.recent_topics), 3)

    def test_update_recent_topics_empty_noop(self):
        """验证空消息不改动话题。"""
        state = SessionState(session_id="s1")
        update_recent_topics(state, "", 3)
        self.assertEqual(state.recent_topics, [])


if __name__ == "__main__":
    unittest.main()
