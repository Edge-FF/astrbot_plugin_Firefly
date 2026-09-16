"""core.affect（情绪引擎）的单元测试。

重点验证：用户信号 → 事件 → 她的心情（反应），而不是直接照抄用户情绪。
"""

from __future__ import annotations

import unittest

from astrbot_plugin_Firefly.core.affect import (
    BASELINE_INTENSITY,
    BASELINE_MOOD,
    EVENT_AFFECTION,
    EVENT_DISTRESS,
    EVENT_HUMOR,
    EVENT_REJECTION,
    AffectEngine,
    AffectEvent,
)
from astrbot_plugin_Firefly.core.models import SessionState

_HOUR = 3600.0


class TestAffectEvents(unittest.TestCase):
    def setUp(self):
        """初始化引擎。"""
        self.engine = AffectEngine()

    def _state(self, mood="平静", intensity=BASELINE_INTENSITY, updated_at=1000.0):
        """构造会话状态。"""
        return SessionState(
            session_id="s1", mood=mood, mood_intensity=intensity, updated_at=updated_at
        )

    def test_events_from_text(self):
        """验证文本能提取出正确的事件类型。"""
        kinds = {e.kind for e in self.engine.events_from_text("我好难过，想你了")}
        self.assertIn(EVENT_DISTRESS, kinds)
        self.assertIn(EVENT_AFFECTION, kinds)

    def test_events_from_text_empty(self):
        """验证空文本不产生事件。"""
        self.assertEqual(self.engine.events_from_text(""), [])

    def test_events_from_emotion_mapping(self):
        """验证用户情绪被映射为事件。"""
        self.assertEqual(
            self.engine.events_from_emotion("难过")[0].kind, EVENT_DISTRESS
        )
        self.assertEqual(self.engine.events_from_emotion(None), [])
        self.assertEqual(self.engine.events_from_emotion("未知情绪"), [])

    def test_events_from_turn_dedupe(self):
        """验证文本与信号产生的事件会去重。"""
        events = self.engine.events_from_turn("我很难过", "难过")
        self.assertEqual([e.kind for e in events], [EVENT_DISTRESS])

    def test_user_emotion_is_reacted_not_copied(self):
        """核心：用户难过时她变成「心疼」，而非照抄用户情绪。"""
        state = self.engine.apply(
            self._state(), [AffectEvent(EVENT_DISTRESS)], now=0.0
        )
        self.assertEqual(state.mood, "心疼")
        self.assertNotEqual(state.mood, "难过")

    def test_rejection_makes_her_wronged(self):
        """验证用户凶她时她变成「委屈」。"""
        state = self.engine.apply(
            self._state(), [AffectEvent(EVENT_REJECTION)], now=0.0
        )
        self.assertEqual(state.mood, "委屈")

    def test_affection_makes_her_happy(self):
        """验证用户亲昵时她变得开心。"""
        state = self.engine.apply(
            self._state(), [AffectEvent(EVENT_AFFECTION)], now=0.0
        )
        self.assertEqual(state.mood, "开心")

    def test_same_event_accumulates(self):
        """验证同类事件叠加会让强度更高（越提越牢）。"""
        state = self.engine.apply(
            self._state(mood="开心", intensity=0.5, updated_at=0.0),
            [AffectEvent(EVENT_AFFECTION)],
            now=0.0,
        )
        self.assertGreater(state.mood_intensity, 0.5)

    def test_weak_event_does_not_switch(self):
        """验证强度不足的事件不会切换心情。"""
        state = self.engine.apply(
            self._state(), [AffectEvent(EVENT_HUMOR, weight=0.5)], now=0.0
        )
        self.assertEqual(state.mood, BASELINE_MOOD)

    def test_decay_returns_to_baseline(self):
        """验证长时间无事件后心情衰减回基线。"""
        state = self.engine.apply(
            self._state(mood="开心", intensity=1.0, updated_at=1000.0),
            [],
            now=1000.0 + 24 * _HOUR,
        )
        self.assertEqual(state.mood, BASELINE_MOOD)
        self.assertEqual(state.mood_intensity, BASELINE_INTENSITY)

    def test_decay_partial(self):
        """验证短期内心情不会立刻回落。"""
        state = self.engine.apply(
            self._state(mood="开心", intensity=1.0, updated_at=1000.0),
            [],
            now=1000.0 + 1 * _HOUR,
        )
        self.assertEqual(state.mood, "开心")
        self.assertLess(state.mood_intensity, 1.0)

    def test_stale_reset(self):
        """验证超过陈旧阈值后硬性回落基线。"""
        state = SessionState(
            session_id="s1", mood="开心", mood_intensity=1.0, last_message_at=100.0
        )
        new = self.engine.apply(state, [], now=100.0 + 3 * _HOUR, stale_reset_hours=2.0)
        self.assertEqual(new.mood, BASELINE_MOOD)

    def test_unknown_mood_profile_fallback(self):
        """验证未知心情的档案回落到基线档案。"""
        profile = self.engine.profile("不存在的心情")
        self.assertEqual(profile.mood, BASELINE_MOOD)

    def test_known_moods(self):
        """验证档案表包含新增的非镜像心情。"""
        moods = self.engine.known_moods()
        self.assertIn("心疼", moods)
        self.assertIn("委屈", moods)


if __name__ == "__main__":
    unittest.main()
