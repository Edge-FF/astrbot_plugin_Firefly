"""core.config 的单元测试（P2-3）。

覆盖 `ShellConfig.from_dict` / `ProactiveConfig.from_dict` 的四类输入：正常值、
字符串形式的数字、非法脏值、缺失值，并锁定三个容易退化的契约：

1. 缺失（None / 未配置）静默使用默认值且**不产生告警**——否则每个未配置项
   都会刷告警；
2. 脏值回退为默认值并产生一条告警（原先这类脏值被静默吞掉）；
3. 字符串形式的布尔值一律映射为 True/False，**不会**回退到默认值。
"""

from __future__ import annotations

import unittest

from astrbot_plugin_Firefly.core.config import ProactiveConfig, ShellConfig


class TestShellConfigFromDict(unittest.TestCase):
    """ShellConfig 的解析、回退与告警。"""

    def test_missing_values_use_defaults_without_warnings(self) -> None:
        """缺失配置静默使用默认值。"""
        for payload in (None, {}):
            with self.subTest(payload=payload):
                warnings: list[str] = []
                cfg = ShellConfig.from_dict(payload, warnings)
                self.assertTrue(cfg.enabled)
                self.assertEqual(cfg.max_tokens, 1500)
                self.assertEqual(cfg.max_on_demand, 3)
                self.assertEqual(cfg.router_llm_timeout, 3.0)
                self.assertEqual(cfg.min_strength, 0.3)
                self.assertEqual(cfg.content_cache_max_entries, 50)
                self.assertEqual(warnings, [])

    def test_normal_values_are_read(self) -> None:
        """正常值逐字段读取，且不产生告警。"""
        warnings: list[str] = []
        cfg = ShellConfig.from_dict(
            {
                "enabled": False,
                "inject": {
                    "max_tokens": 900,
                    "max_on_demand": 7,
                    "enabled_sessions": "s1",
                },
                "router": {"use_llm": True, "llm_timeout_seconds": 4.5},
                "state": {"max_topics": 9},
                "active_context": {"min_strength": 0.5},
                "content_cache": {"max_entries": 9},
            },
            warnings,
        )
        self.assertFalse(cfg.enabled)
        self.assertEqual(cfg.max_tokens, 900)
        self.assertEqual(cfg.max_on_demand, 7)
        self.assertEqual(cfg.enabled_sessions, ("s1",))
        self.assertTrue(cfg.router_use_llm)
        self.assertEqual(cfg.router_llm_timeout, 4.5)
        self.assertEqual(cfg.max_topics, 9)
        self.assertEqual(cfg.min_strength, 0.5)
        self.assertEqual(cfg.content_cache_max_entries, 9)
        self.assertEqual(warnings, [])

    def test_string_numbers_are_converted(self) -> None:
        """字符串形式的数字可正常转换，不产生告警。"""
        warnings: list[str] = []
        cfg = ShellConfig.from_dict(
            {"inject": {"max_tokens": "900"}, "state": {"decay_hours": "12"}},
            warnings,
        )
        self.assertEqual(cfg.max_tokens, 900)
        self.assertEqual(cfg.decay_hours, 12.0)
        self.assertEqual(warnings, [])

    def test_dirty_values_fall_back_and_warn(self) -> None:
        """脏值回退为默认值并产生告警。"""
        warnings: list[str] = []
        cfg = ShellConfig.from_dict(
            {"inject": {"max_tokens": "abc"}, "state": {"decay_hours": "soon"}},
            warnings,
        )
        self.assertEqual(cfg.max_tokens, 1500)
        self.assertEqual(cfg.decay_hours, 0.0)
        self.assertEqual(len(warnings), 2, warnings)
        self.assertTrue(any("inject.max_tokens" in w for w in warnings), warnings)
        self.assertTrue(any("state.decay_hours" in w for w in warnings), warnings)

    def test_bool_string_never_falls_back(self) -> None:
        """字符串形式的布尔一律映射为 True/False，不使用默认值。"""
        warnings: list[str] = []
        # enabled 的默认值为 True：字符串 "no" 必须得到 False，才能证明
        # 走的是真值表而不是回退到默认值
        cfg = ShellConfig.from_dict({"enabled": "no"}, warnings)
        self.assertFalse(cfg.enabled)
        # 真值表内的字符串同样生效
        cfg2 = ShellConfig.from_dict({"router": {"use_llm": "on"}}, warnings)
        self.assertTrue(cfg2.router_use_llm)
        self.assertEqual(warnings, [])

    def test_non_bool_type_falls_back_and_warns(self) -> None:
        """既非布尔也非字符串的值回退到默认值并告警。"""
        warnings: list[str] = []
        cfg = ShellConfig.from_dict({"enabled": 1}, warnings)
        self.assertTrue(cfg.enabled)
        self.assertEqual(len(warnings), 1, warnings)
        self.assertIn("enabled", warnings[0])


class TestProactiveConfigFromDict(unittest.TestCase):
    """ProactiveConfig 的解析、钳制与告警。"""

    def test_missing_values_use_defaults_without_warnings(self) -> None:
        """缺失配置静默使用默认值。"""
        for payload in (None, {}):
            with self.subTest(payload=payload):
                warnings: list[str] = []
                cfg = ProactiveConfig.from_dict(payload, warnings)
                self.assertFalse(cfg.enabled)
                self.assertEqual(cfg.tick_interval_seconds, 120.0)
                self.assertEqual(cfg.quiet_hours, "1-7")
                self.assertEqual(cfg.sessions, ())
                self.assertEqual(warnings, [])

    def test_normal_values_are_read(self) -> None:
        """正常值逐字段读取，且不产生告警。"""
        warnings: list[str] = []
        cfg = ProactiveConfig.from_dict(
            {
                "proactive": {
                    "enabled": True,
                    "tick_interval_seconds": 300,
                    "max_per_day": 9,
                    "quiet_hours": "2-6",
                    "sessions": ["a", "b"],
                }
            },
            warnings,
        )
        self.assertTrue(cfg.enabled)
        self.assertEqual(cfg.tick_interval_seconds, 300.0)
        self.assertEqual(cfg.max_per_day, 9)
        self.assertEqual(cfg.quiet_hours, "2-6")
        self.assertEqual(cfg.sessions, ("a", "b"))
        self.assertEqual(warnings, [])

    def test_string_numbers_are_converted_and_clamped(self) -> None:
        """字符串数字可转换，且仍按合法下限钳制。"""
        warnings: list[str] = []
        cfg = ProactiveConfig.from_dict(
            {"proactive": {"tick_interval_seconds": "2", "max_sends_per_tick": 0}},
            warnings,
        )
        self.assertEqual(cfg.tick_interval_seconds, 10.0)
        self.assertEqual(cfg.max_sends_per_tick, 1)
        self.assertEqual(warnings, [])

    def test_dirty_values_fall_back_and_warn(self) -> None:
        """脏值回退为默认值并产生告警。"""
        warnings: list[str] = []
        cfg = ProactiveConfig.from_dict(
            {"proactive": {"tick_interval_seconds": "soon", "max_per_day": "many"}},
            warnings,
        )
        self.assertEqual(cfg.tick_interval_seconds, 120.0)
        self.assertEqual(cfg.max_per_day, 6)
        self.assertEqual(len(warnings), 2, warnings)
        self.assertTrue(any("tick_interval_seconds" in w for w in warnings), warnings)
        self.assertTrue(any("max_per_day" in w for w in warnings), warnings)

    def test_empty_quiet_hours_falls_back(self) -> None:
        """空字符串 quiet_hours 回落到默认值（沿用原有语义）。"""
        cfg = ProactiveConfig.from_dict({"proactive": {"quiet_hours": ""}})
        self.assertEqual(cfg.quiet_hours, "1-7")

    def test_single_session_string_is_normalized(self) -> None:
        """sessions 传字符串时规范为单元素元组。"""
        cfg = ProactiveConfig.from_dict({"proactive": {"sessions": "a"}})
        self.assertEqual(cfg.sessions, ("a",))


if __name__ == "__main__":
    unittest.main()
