"""资料层级推断规则。

tier/kind 的自动推断规则同时被「资料索引」（registry）与「资料写入」
（role_store）使用，因此独立成模块：它是双方共用的中立知识，不应寄生在
registry 中——否则写入侧会反向依赖索引侧。

本模块为纯逻辑：不依赖 astrbot、不访问 IO，可独立单测。
"""

from __future__ import annotations

from . import consts


def infer_tier_kind(rel_path: str) -> tuple[int, str]:
    """按目录路径自动推断 tier/kind。

    优先按目录名判定，其次按顶层文件名判定，避免子串误判：
    例如 narratives/persona_narrative.md 应归为 Tier4 动态叙事，
    而不是因文件名含 "persona_narrative" 而被误判为 Tier1 常驻人格。

    Args:
        rel_path: 相对 role/ 目录的路径（斜杠或反斜杠分隔均可）。

    Returns:
        (tier, kind) 二元组。
    """
    norm = rel_path.replace("\\", "/")
    segments = [s for s in norm.split("/") if s]
    top_dir = segments[0] if len(segments) > 1 else ""
    base_name = segments[-1] if segments else ""

    # 1) 目录名规则优先（如 skills/、narratives/）
    for pattern, tier, kind in consts.DIRECTORY_TIER_RULES:
        if "/" not in pattern and pattern == top_dir:
            return tier, kind

    # 2) 顶层文件名规则（如 persona_base.md → 常驻 Tier1）
    for pattern, tier, kind in consts.DIRECTORY_TIER_RULES:
        if pattern == base_name:
            return tier, kind

    # 3) 默认推断：顶级 .md → Tier1；子目录 → Tier3
    if len(segments) <= 1:
        return consts.TIER_CORE_PERSONA, consts.KIND_PERSONA
    return consts.TIER_SKILL_LORE, consts.KIND_LORE
