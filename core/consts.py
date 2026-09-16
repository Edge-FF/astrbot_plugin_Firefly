"""认知外壳领域常量 v0.2。

统一存放资料格式、注入块结构、tier/kind 映射相关的常量。
"""

# ============================================================================
# front-matter 元数据键
# ============================================================================

FM_KEY_ID = "id"
FM_KEY_TITLE = "title"
FM_KEY_TYPE = "type"
FM_KEY_KIND = "kind"
FM_KEY_TIER = "tier"
FM_KEY_TAGS = "tags"
FM_KEY_KEYWORDS = "keywords"
FM_KEY_PATTERNS = "patterns"
FM_KEY_PRIORITY = "priority"
FM_KEY_DEFAULT_TTL = "default_ttl"

# ============================================================================
# 资料层级 (tier)
# ============================================================================

TIER_CORE_PERSONA = 1
# 2 原为关系阶段档位，机制已移除；保留作为"低层常驻档位"的加载阈值。
TIER_RELATIONSHIP = 2
TIER_SKILL_LORE = 3
TIER_WORLD_NARRATIVE = 4

# 资料种类 (kind)
KIND_PERSONA = "persona"
KIND_SKILL = "skill"
KIND_LORE = "lore"
KIND_NARRATIVE = "narrative"

# ============================================================================
# 目录 → tier/kind 自动推断规则
# (relative_path_pattern, tier, kind)
# ============================================================================

DIRECTORY_TIER_RULES: tuple[tuple[str, int, str], ...] = (
    # 英文目录与文件名（向后兼容）
    ("persona_base.md", TIER_CORE_PERSONA, KIND_PERSONA),
    ("persona_narrative.md", TIER_CORE_PERSONA, KIND_PERSONA),
    ("skills", TIER_SKILL_LORE, KIND_SKILL),
    ("npc_profiles", TIER_SKILL_LORE, KIND_LORE),
    ("world_lore", TIER_WORLD_NARRATIVE, KIND_LORE),
    ("narratives", TIER_WORLD_NARRATIVE, KIND_NARRATIVE),
    # 中文目录与文件名
    ("基础人设.md", TIER_CORE_PERSONA, KIND_PERSONA),
    ("故事", TIER_CORE_PERSONA, KIND_PERSONA),
    ("技能", TIER_SKILL_LORE, KIND_SKILL),
    ("人物关系", TIER_SKILL_LORE, KIND_LORE),
    ("世界知识", TIER_WORLD_NARRATIVE, KIND_LORE),
)

# ============================================================================
# 默认 TTL（按 kind）
# ============================================================================

DEFAULT_TTL_MAP: dict[str, int] = {
    KIND_SKILL: 4,
    KIND_LORE: 2,
    KIND_NARRATIVE: 6,
}

# ============================================================================
# 认知外壳注入块标签
# ============================================================================

SHELL_BLOCK_TAG = "cognitive_shell"
SHELL_STATIC_TAG = "static_core"
SHELL_STATE_TAG = "dynamic_state"
SHELL_ACTIVE_TAG = "active_context"
SHELL_ON_DEMAND_TAG = "shell_on_demand"

# 注入块识别标记
SHELL_INJECTION_MARK = f"<{SHELL_BLOCK_TAG}>"

# ============================================================================
# 动态状态的默认值与边界
# ============================================================================

DEFAULT_MOOD = "平静"

MAX_TOPIC_LENGTH = 24
DEFAULT_PRIORITY = 50

# ============================================================================
# 加载器忽略的文件名前缀
# ============================================================================

IGNORED_PREFIXES = ("_", ".")
