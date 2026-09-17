"""adapter.astrbot_compat（AstrBot 内部 API 兼容层）的单元测试（P1-2）。

本模块是插件唯一接触 ``astrbot.core.*`` 的地方，因此测试锁定两件事：

1. 可用性标记与符号实际状态一致，且兼容层只做再导出、不引入替身类型；
2. 符号缺失时的诊断文本正确描述受影响的功能。

「缺失」场景通过临时改写可用性标记模拟，不重新导入模块，避免测试依赖
导入顺序。
"""

from __future__ import annotations

import unittest
from unittest import mock

from astrbot_plugin_Firefly.adapter import astrbot_compat


class TestSymbolAvailability(unittest.TestCase):
    """可用性标记与原始符号的一致性。"""

    def test_capability_flags_agree_with_symbols(self) -> None:
        """可用性标记必须与符号是否存在保持一致。"""
        self.assertEqual(
            astrbot_compat.INJECTION_API_AVAILABLE,
            astrbot_compat.Message is not None and astrbot_compat.TextPart is not None,
        )
        self.assertEqual(
            astrbot_compat.PROACTIVE_SEND_API_AVAILABLE,
            astrbot_compat.MessageChain is not None,
        )

    def test_required_symbols_available_in_current_env(self) -> None:
        """当前 AstrBot 环境下所需符号应全部可用，否则功能会静默降级。"""
        self.assertTrue(astrbot_compat.INJECTION_API_AVAILABLE)
        self.assertTrue(astrbot_compat.PROACTIVE_SEND_API_AVAILABLE)

    @unittest.skipUnless(
        astrbot_compat.INJECTION_API_AVAILABLE, "注入接口不可用，无法核对原始类型"
    )
    def test_symbols_are_the_astrbot_originals(self) -> None:
        """兼容层只做再导出，不得替换为替身类型（否则会与真实管线不兼容）。"""
        from astrbot.core.agent.message import Message, TextPart

        self.assertIs(astrbot_compat.Message, Message)
        self.assertIs(astrbot_compat.TextPart, TextPart)

    def test_missing_summary_is_empty_when_all_available(self) -> None:
        """全部可用时不产生诊断文本。"""
        self.assertEqual(astrbot_compat.missing_api_summary(), "")


class TestMissingApiSummary(unittest.TestCase):
    """缺失诊断文本必须点明受影响的功能。"""

    def test_injection_missing_is_described(self) -> None:
        """仅注入接口缺失时，只描述注入能力。"""
        with mock.patch.object(astrbot_compat, "INJECTION_API_AVAILABLE", False):
            summary = astrbot_compat.missing_api_summary()
        self.assertIn("认知外壳注入不可用", summary)
        self.assertNotIn("主动消息发送不可用", summary)

    def test_proactive_missing_is_described(self) -> None:
        """仅主动消息接口缺失时，只描述发送能力。"""
        with mock.patch.object(astrbot_compat, "PROACTIVE_SEND_API_AVAILABLE", False):
            summary = astrbot_compat.missing_api_summary()
        self.assertIn("主动消息发送不可用", summary)
        self.assertNotIn("认知外壳注入不可用", summary)

    def test_both_missing_are_reported_together(self) -> None:
        """两项都缺失时都应出现在同一条诊断中。"""
        with (
            mock.patch.object(astrbot_compat, "INJECTION_API_AVAILABLE", False),
            mock.patch.object(astrbot_compat, "PROACTIVE_SEND_API_AVAILABLE", False),
        ):
            summary = astrbot_compat.missing_api_summary()
        self.assertIn("认知外壳注入不可用", summary)
        self.assertIn("主动消息发送不可用", summary)


if __name__ == "__main__":
    unittest.main()
