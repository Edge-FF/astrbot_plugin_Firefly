"""AstrBot 内部 API 兼容层。

本模块是插件中**唯一**直接 import ``astrbot.core.*`` 的地方。这些内部路径
不是 AstrBot 的稳定对外接口，集中在此处有两个目的：

1. 上游调整内部结构时，适配只需改动本文件；
2. 避免某个内部路径变动导致插件在 import 阶段整体加载失败——降级后
   人格注入、主动消息等其它能力仍可正常工作。

约定：
- 运行时必需的符号缺失时降级为 ``None``，调用方通过能力标记决定是否跳过
  对应功能（缺失的依赖关系由本模块描述，不泄漏到调用方）；
- 仅用于类型标注的符号缺失时降级为 ``Any``，不影响运行。
"""

from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# 外壳注入所需的原始符号（依赖 astrbot.core.agent.message）
# 缺失时：人格注入与任务路径注入都不可用。
# ---------------------------------------------------------------------------

INJECTION_API_AVAILABLE = True

try:
    from astrbot.core.agent.message import Message, TextPart
except ImportError:
    INJECTION_API_AVAILABLE = False
    Message = None
    TextPart = None

# ---------------------------------------------------------------------------
# 主动消息发送所需的原始符号（依赖 astrbot.core.message.message_event_result）
# 缺失时：主动消息无法构造发送链，只影响发送环节。
# ---------------------------------------------------------------------------

PROACTIVE_SEND_API_AVAILABLE = True

try:
    from astrbot.core.message.message_event_result import MessageChain
except ImportError:
    PROACTIVE_SEND_API_AVAILABLE = False
    MessageChain = None

# ---------------------------------------------------------------------------
# 仅用于类型标注的符号（依赖 astrbot.core.agent.run_context）
# 本模块的注解均为惰性求值（from __future__ import annotations），
# 因此缺失时降级为 Any 即可，不影响运行。
# ---------------------------------------------------------------------------

try:
    from astrbot.core.agent.run_context import ContextWrapper
except ImportError:
    ContextWrapper = Any


def missing_api_summary() -> str:
    """描述当前缺失的 AstrBot 内部符号及其影响（供启动日志使用）。

    Returns:
        缺失项的可读说明，以「；」分隔；全部可用时返回空串。
    """
    missing: list[str] = []
    if not INJECTION_API_AVAILABLE:
        missing.append(
            "astrbot.core.agent.message（Message/TextPart）→ 认知外壳注入不可用"
        )
    if not PROACTIVE_SEND_API_AVAILABLE:
        missing.append(
            "astrbot.core.message.message_event_result（MessageChain）"
            "→ 主动消息发送不可用"
        )
    return "；".join(missing)
