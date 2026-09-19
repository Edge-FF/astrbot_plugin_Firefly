"""认知外壳组装器：分层注入 + strength 感知 + XML 块结构。

设计约束：
- Tier1 核心人格：不可裁剪，优先保障
- Tier2 关系阶段：不可裁剪
- State 动态状态：不可裁剪（<100 token）
- Active 激活条目：按 strength 降序，超预算时从尾部裁剪
"""

from __future__ import annotations

from collections.abc import Sequence

from .. import consts
from ..models import (
    ActivatedEntry,
    BuildResult,
    MaterialEntry,
    SessionState,
)

_SHELL_INTRO_HEAD = f"以下 <{consts.SHELL_BLOCK_TAG}> 描述「流萤」的认知外壳。"
# 无身份块时必须与历史文本逐字一致（test_behavior_anchor 的字节稳定性断言）
_SHELL_INTRO_STATIC = "静止设定定义你是谁；"
_SHELL_INTRO_TAIL = (
    "动态状态反映当前心境与关系阶段；"
    "激活上下文是当前场景相关的额外资料（弱激活条目仅为背景参考）。"
    "请始终以此为准，不要偏离人设。"
)


def _build_intro(has_user_profile: bool) -> str:
    """构造说明文本；仅在确实注入身份块时才解释 `<user_profile>`。

    若身份块不存在仍提及该标签，模型可能去找一个并不存在的块，反而产生歧义。

    Args:
        has_user_profile: 本次是否注入身份块。

    Returns:
        说明文本。
    """
    parts = [_SHELL_INTRO_HEAD, _SHELL_INTRO_STATIC]
    if has_user_profile:
        parts.append(f"<{consts.SHELL_USER_PROFILE_TAG}> 描述正在与你对话的人是谁；")
    parts.append(_SHELL_INTRO_TAIL)
    return "".join(parts)


class ShellBuilder:
    """认知外壳组装器（纯函数，无副作用，可单测）。"""

    def __init__(
        self,
        max_tokens: int = 1500,
        tier1_reserved: int = 830,
        user_profile_max_tokens: int = 600,
    ) -> None:
        """初始化组装器。

        Args:
            max_tokens: 默认总 token 预算。
            tier1_reserved: 常驻块（Tier1 人格 + 动态状态 + XML 包装）的预留
                token 预算；这部分不参与激活条目的分配，实际占用会随资料
                大小浮动。设为 0 则全部预算都给激活条目。
            user_profile_max_tokens: 用户身份块的长度上限（token），独立于总
                预算；身份块不可裁剪，但自身超限时按此值截断。至少为 1。
        """
        self._max_tokens = max_tokens
        self._tier1_reserved = max(tier1_reserved, 0)
        self._user_profile_max_tokens = max(user_profile_max_tokens, 1)

    def build(
        self,
        tier1_entries: Sequence[MaterialEntry],
        state: SessionState,
        active_entries: Sequence[tuple[ActivatedEntry, MaterialEntry]],
        max_tokens: int | None = None,
        user_profile: MaterialEntry | None = None,
    ) -> BuildResult:
        """分层组装：按预算分配注入内容。

        Args:
            tier1_entries: 核心人格条目（不可裁剪）
            state: 动态会话状态
            active_entries: 激活的 Tier3/4 条目及其 ActivatedEntry
            max_tokens: 总预算（缺省使用构造参数）
            user_profile: 用户身份条目（常驻、不可裁剪）；None 表示不注入

        Returns:
            组装结果（文本 + 激活条目裁剪信息 + 身份块截断标记）。
        """
        budget = max_tokens if max_tokens is not None else self._max_tokens

        static_block = self._build_tier1(tier1_entries)
        user_profile_block, profile_truncated = self._build_user_profile(user_profile)
        state_block = self._build_state(state)
        active_block, truncated = self._build_active(
            active_entries, budget, self._tier1_reserved
        )

        full_text = self._wrap(
            static_block, user_profile_block, state_block, active_block
        )
        estimated = self._estimate_tokens(full_text)

        over_budget = estimated > budget
        return BuildResult(full_text, tuple(truncated), over_budget, profile_truncated)

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

    def _build_user_profile(self, entry: MaterialEntry | None) -> tuple[str, bool]:
        """组装用户身份块（常驻、不可裁剪，但自身超限时截断）。

        Args:
            entry: 身份条目；None 或正文为空时不注入。

        Returns:
            (`<user_profile>` 块文本 或 空串, 是否发生截断)。
        """
        content = (entry.content or "").strip() if entry is not None else ""
        if not content:
            return "", False

        truncated = False
        # 与 _estimate_tokens 同口径：约 2 字符 ≈ 1 token
        limit = self._user_profile_max_tokens * 2
        if len(content) > limit:
            content = content[:limit].rstrip() + "…（身份设定已截断）"
            truncated = True

        inner = f'<entry id="{entry.id}" title="{entry.title}">\n{content}\n</entry>'
        block = (
            f"<{consts.SHELL_USER_PROFILE_TAG}>\n{inner}\n"
            f"</{consts.SHELL_USER_PROFILE_TAG}>"
        )
        return block, truncated

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
        tier1_reserved: int,
    ) -> tuple[str, list[str]]:
        """按 strength 降序组装激活条目，超预算时从尾部裁剪。

        Args:
            entries: 激活条目及其 ActivatedEntry。
            total_budget: 总 token 预算。
            tier1_reserved: 常驻块预留量，从总预算中扣除后再分配给激活条目。

        Returns:
            (`<active_context>` 块文本, 被裁剪的条目 ID 列表)。
        """
        if not entries:
            return "", []

        sorted_entries = sorted(entries, key=lambda x: -x[0].strength)
        max_active_budget = max(total_budget - tier1_reserved, 100)

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
        user_profile_block: str,
        state_block: str,
        active_block: str,
    ) -> str:
        """将各层块与说明文本包裹进 <cognitive_shell>。

        块序固定为 `static_core → user_profile → dynamic_state → active_context`：
        身份紧跟人格，保证模型先建立"我是谁、对面是谁"，再看当前状态与场景资料。

        Args:
            static_block: Tier1 块文本（可为空）。
            user_profile_block: 用户身份块文本（可为空）。
            state_block: 动态状态块文本（可为空）。
            active_block: 激活条目块文本（可为空）。

        Returns:
            最终注入的完整 XML 文本。
        """
        parts = [_build_intro(bool(user_profile_block))]
        if static_block:
            parts.append(static_block)
        if user_profile_block:
            parts.append(user_profile_block)
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
