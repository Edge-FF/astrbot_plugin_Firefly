"""会话状态的通用更新辅助。

说明：心情更新已收敛到 `core.affect.AffectEngine`（事件驱动），因此本模块
不再包含任何心情逻辑，只保留与心情无关的纯状态维护工具（话题队列），
供 injector / proactive 等调用方复用。
"""

from __future__ import annotations

import re

from . import consts
from .models import SessionState

_PUNCT_RE = re.compile(r"[\s\u3000!?.,;:。，；：、！？…~～\x22'‘’“”（）()【】\[\]]+")


def extract_topic(user_text: str) -> str:
    """从用户消息中提取话题文本（去除标点，截断到最大长度）。

    Args:
        user_text: 用户消息原文。

    Returns:
        清理后的话题文本；无有效内容时返回空串。
    """
    cleaned = _PUNCT_RE.sub("", user_text).strip()
    if not cleaned:
        return ""
    return cleaned[: consts.MAX_TOPIC_LENGTH]


def update_recent_topics(state: SessionState, user_text: str, max_topics: int) -> None:
    """提取话题并插入最近话题队列头部（原地修改 state.recent_topics）。

    Args:
        state: 会话状态（原地修改）。
        user_text: 用户消息文本。
        max_topics: 话题队列上限。
    """
    topic = extract_topic(user_text)
    if not topic:
        return
    topics = [t for t in state.recent_topics if t != topic]
    topics.insert(0, topic)
    state.recent_topics = topics[: max(max_topics, 1)]
