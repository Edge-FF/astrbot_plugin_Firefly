"""core.proactive（主动消息决策层）的单元测试。"""

from __future__ import annotations

import unittest

from astrbot_plugin_Firefly.core.affect import AffectEngine
from astrbot_plugin_Firefly.core.config import ProactiveConfig
from astrbot_plugin_Firefly.core.models import SessionState
from astrbot_plugin_Firefly.core.proactive import (
    INTENT_CHECK,
    INTENT_MISS,
    INTENT_SHARE,
    ProactivePolicy,
)

_HOUR = 3600.0
_NOW = 1_700_000_000.0


def _config(**kw) -> ProactiveConfig:
    """构造测试用配置（默认全部启用且无时间限制）。"""
    base = dict(
        enabled=True,
        min_contact_gap_minutes=0,
        min_proactive_interval_minutes=0,
        max_unanswered=0,
        max_per_day=0,
        quiet_hours="0-0",
        startup_grace_seconds=0.0,
    )
    base.update(kw)
    return ProactiveConfig(**base)


class TestProactivePolicy(unittest.TestCase):
    def setUp(self):
        """初始化决策器。"""
        self.affect = AffectEngine()
        self.cfg = _config()
        self.policy = ProactivePolicy(self.affect, self.cfg)

    def _state(self, **kw) -> SessionState:
        """构造会话状态。"""
        base = dict(session_id="s1", mood="平静")
        base.update(kw)
        return SessionState(**base)

    # ---- 冲动值 ----

    def test_urge_zero_when_no_history(self):
        """验证从未接触时冲动值为 0。"""
        self.assertEqual(self.policy.compute_urge(self._state(), _NOW), 0.0)

    def test_urge_uses_mood_rate(self):
        """验证冲动值随心情倾向放大（想念更快）。"""
        state = self._state(mood="想念", last_user_at=_NOW - 3 * _HOUR)
        self.assertAlmostEqual(self.policy.compute_urge(state, _NOW), 6.0, places=3)

    def test_urge_unanswered_boost(self):
        """验证未回复会叠加冲动值。"""
        state = self._state(unanswered_count=2)
        self.assertGreater(self.policy.compute_urge(state, _NOW), 0.0)

    def test_threshold_by_mood(self):
        """验证阈值随心情变化（想念更低）。"""
        self.assertLess(
            self.policy.threshold(self._state(mood="想念")),
            self.policy.threshold(self._state(mood="平静")),
        )

    # ---- 闸门 ----

    def test_gate_disabled(self):
        """验证总开关关闭时阻断。"""
        policy = ProactivePolicy(self.affect, _config(enabled=False))
        self.assertEqual(policy.check_gates(self._state(), _NOW, 0.0)[1], "disabled")

    def test_gate_registry_and_provider(self):
        """验证资料未加载 / 无 Provider 时阻断。"""
        self.assertEqual(
            self.policy.check_gates(self._state(), _NOW, 0.0, registry_ready=False)[1],
            "empty_registry",
        )
        self.assertEqual(
            self.policy.check_gates(self._state(), _NOW, 0.0, provider_ready=False)[1],
            "no_provider",
        )

    def test_gate_session_filter(self):
        """验证会话白名单过滤。"""
        policy = ProactivePolicy(self.affect, _config(sessions=("other",)))
        self.assertEqual(
            policy.check_gates(self._state(), _NOW, 0.0)[1], "session_filtered"
        )

    def test_gate_quiet_hours(self):
        """验证免打扰时段阻断。"""
        policy = ProactivePolicy(self.affect, _config(quiet_hours="1-7"))
        self.assertEqual(
            policy.check_gates(self._state(), _NOW, 0.0, hour=3)[1], "quiet_hours"
        )
        self.assertEqual(
            policy.check_gates(self._state(), _NOW, 0.0, hour=12)[1], "ok"
        )

    def test_gate_quiet_hours_crossday(self):
        """验证跨天免打扰区间（23-6）。"""
        policy = ProactivePolicy(self.affect, _config(quiet_hours="23-6"))
        self.assertEqual(
            policy.check_gates(self._state(), _NOW, 0.0, hour=23)[1], "quiet_hours"
        )
        self.assertEqual(
            policy.check_gates(self._state(), _NOW, 0.0, hour=5)[1], "quiet_hours"
        )
        self.assertEqual(policy.check_gates(self._state(), _NOW, 0.0, hour=12)[1], "ok")

    def test_gate_recent_contact(self):
        """验证刚接触过会阻断。"""
        policy = ProactivePolicy(self.affect, _config(min_contact_gap_minutes=30))
        recent = self._state(last_user_at=_NOW - 60)
        self.assertEqual(policy.check_gates(recent, _NOW, 0.0)[1], "recent_contact")

    def test_gate_recent_proactive(self):
        """验证刚主动过会阻断。"""
        policy = ProactivePolicy(
            self.affect,
            _config(min_contact_gap_minutes=0, min_proactive_interval_minutes=60),
        )
        state = self._state(last_proactive_at=_NOW - 60)
        self.assertEqual(policy.check_gates(state, _NOW, 0.0)[1], "recent_proactive")

    def test_gate_max_unanswered_and_daily(self):
        """验证未回复上限与每日上限。"""
        policy = ProactivePolicy(
            self.affect, _config(max_unanswered=3, max_per_day=2)
        )
        self.assertEqual(
            policy.check_gates(self._state(unanswered_count=3), _NOW, 0.0)[1],
            "max_unanswered",
        )
        self.assertEqual(
            policy.check_gates(self._state(proactive_count_today=2), _NOW, 0.0)[1],
            "daily_limit",
        )

    def test_gate_startup_grace(self):
        """验证启动宽限期内不发。"""
        policy = ProactivePolicy(
            self.affect, _config(startup_grace_seconds=120.0)
        )
        self.assertEqual(
            policy.check_gates(self._state(), _NOW, _NOW - 10)[1], "startup_grace"
        )

    # ---- 综合判定 ----

    def test_below_threshold(self):
        """验证冲动值不足阈值时判定为 below_threshold。"""
        state = self._state(last_user_at=_NOW - 60)
        passed, reason = self.policy.should_reach_out(state, _NOW, 0.0)
        self.assertFalse(passed)
        self.assertEqual(reason, "below_threshold")

    def test_reach_out_when_urge_passes(self):
        """验证静默够久后允许主动。"""
        state = self._state(mood="想念", last_user_at=_NOW - 10 * _HOUR)
        passed, reason = self.policy.should_reach_out(state, _NOW, 0.0)
        self.assertTrue(passed)
        self.assertEqual(reason, "ok")

    # ---- 意图 ----

    def test_config_getter_is_live(self):
        """验证决策器使用实时配置（运行时切换开关后立即生效）。"""
        current = {"cfg": _config(enabled=False)}
        policy = ProactivePolicy(self.affect, lambda: current["cfg"])
        self.assertFalse(policy.config.enabled)
        current["cfg"] = _config(enabled=True)
        self.assertTrue(policy.config.enabled)

    def test_pick_intent_variants(self):
        """验证意图选择逻辑。"""
        self.assertEqual(
            self.policy.pick_intent(self._state(unanswered_count=1), _NOW), INTENT_CHECK
        )
        self.assertEqual(
            self.policy.pick_intent(self._state(mood="想念"), _NOW), INTENT_MISS
        )
        self.assertEqual(
            self.policy.pick_intent(
                self._state(mood="开心", recent_topics=["崩铁"]), _NOW
            ),
            INTENT_SHARE,
        )


if __name__ == "__main__":
    unittest.main()
