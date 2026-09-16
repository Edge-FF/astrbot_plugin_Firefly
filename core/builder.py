"""认知外壳组装器：分层注入 + strength 感知 + XML 块结构。

设计约束：
- Tier1 核心人格：不可裁剪，优先保障
- Tier2 关系阶段：不可裁剪
- State 动态状态：不可裁剪（<100 token）
- Active 激活条目：按 strength 降序，超预算时从尾部裁剪
"""

from __future__ import annotations

from collections.abc import Sequence

from . import consts
from .models import (
    ActivatedEntry,
    BuildResult,
    MaterialEntry,
    SessionState,
)

_SHELL_INTRO = (
    f"以下 <{consts.SHELL_BLOCK_TAG}> 描述「流萤」的认知外壳。"
    "静止设定定义你是谁；动态状态反映当前心境与关系阶段；"
    "激活上下文是当前场景相关的额外资料（弱激活条目仅为背景参考）。"
    "请始终以此为准，不要偏离人设。"
)


class ShellBuilder:
    """认知外壳组装器（纯函数，无副作用，可单测）。"""

    def __init__(self, max_tokens: int = 1500) -> None:
        """初始化组装器。

        Args:
            max_tokens: 默认总 token 预算。
        """
        self._max_tokens = max_tokens

    def build(
        self,
        tier1_entries: Sequence[MaterialEntry],
        state: SessionState,
        active_entries: Sequence[tuple[ActivatedEntry, MaterialEntry]],
        max_tokens: int | None = None,
    ) -> BuildResult:
        """分层组装：按预算分配注入内容。

        Args:
            tier1_entries: 核心人格条目（不可裁剪）
            state: 动态会话状态
            active_entries: 激活的 Tier3/4 条目及其 ActivatedEntry
            max_tokens: 总预算（缺省使用构造参数）
        """
        budget = max_tokens if max_tokens is not None else self._max_tokens

        static_block = self._build_tier1(tier1_entries)
        state_block = self._build_state(state)
        active_block, truncated = self._build_active(active_entries, budget)

        full_text = self._wrap(static_block, state_block, active_block)
        estimated = self._estimate_tokens(full_text)

        over_budget = estimated > budget
        return BuildResult(full_text, tuple(truncated), over_budget)

    @staticmethod
    def _build_tier1(entries: Sequence[MaterialEntry]) -> str:
        """组装 Tier1 核心人格块（不可裁剪）。

        Args:
            entries: 核心人格条目列表。

        Returns:
            `<static_core>` XML 块文本；无条目时返回空串。
        """
        if not entries:
            return ""
        inner = []
        for entry in entries:
            content = entry.content or ""
            inner.append(
                f'<entry id="{entry.id}" title="{entry.title}">\n{content}\n</entry>'
            )
        return (
            f"<{consts.SHELL_STATIC_TAG}>\n"
            + "\n".join(inner)
            + f"\n</{consts.SHELL_STATIC_TAG}>"
        )

    @staticmethod
    def _build_state(state: SessionState) -> str:
        """组装动态状态摘要块（不可裁剪）。

        Args:
            state: 会话状态。

        Returns:
            `<dynamic_state>` XML 块文本。
        """
        topics = "；".join(state.recent_topics) or "暂无"
        inner = f"心情：{state.mood}\n最近话题：{topics}"
        return f"<{consts.SHELL_STATE_TAG}>\n{inner}\n</{consts.SHELL_STATE_TAG}>"

    @staticmethod
    def _build_active(
        entries: Sequence[tuple[ActivatedEntry, MaterialEntry]],
        total_budget: int,
    ) -> tuple[str, list[str]]:
        """按 strength 降序组装激活条目，超预算时从尾部裁剪。"""
        if not entries:
            return "", []

        sorted_entries = sorted(entries, key=lambda x: -x[0].strength)
        max_active_budget = max(total_budget - 830, 100)

        truncated: list[str] = []
        items: list[str] = []
        used = 0

        for active, entry in sorted_entries:
            content = entry.content or ""
            strength_mark = ""
            if active.strength < 0.5:
                strength_mark = " （背景参考，弱激活）"

            item = (
                f'<entry kind="{entry.kind}" id="{entry.id}" '
                f'strength="{active.strength:.1f}" title="{entry.title}">\n'
                f"{content}{strength_mark}\n"
                f"</entry>"
            )
            est = len(item) // 2
            if used + est > max_active_budget:
                truncated.append(entry.id)
                continue
            items.append(item)
            used += est

        if not items:
            return "", truncated
        return (
            f"<{consts.SHELL_ACTIVE_TAG}>\n"
            + "\n".join(items)
            + f"\n</{consts.SHELL_ACTIVE_TAG}>"
        ), truncated

    def _wrap(
        self,
        static_block: str,
        state_block: str,
        active_block: str,
    ) -> str:
        """将各层块与说明文本包裹进 <cognitive_shell>。

        Args:
            static_block: Tier1 块文本（可为空）。
            state_block: 动态状态块文本（可为空）。
            active_block: 激活条目块文本（可为空）。

        Returns:
            最终注入的完整 XML 文本。
        """
        parts = [_SHELL_INTRO]
        if static_block:
            parts.append(static_block)
        if state_block:
            parts.append(state_block)
        if active_block:
            parts.append(active_block)
        return (
            f"<{consts.SHELL_BLOCK_TAG}>\n"
            + "\n".join(parts)
            + f"\n</{consts.SHELL_BLOCK_TAG}>"
        )

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        """粗略估算：中文约 1 token/字，英文约 4 字符/token，折中按 2 字符/token。"""
        return max(len(text) // 2, 1)
