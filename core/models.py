"""认知外壳数据模型。

核心类型：
- MaterialEntry: 统一资料条目，以 tier 字段区分层级（1-4）
- ActiveContext / ActivatedEntry: 激活上下文与惯性管理
- RouteResult / RouteSignals: LLM/关键词路由输出
- SessionState: 扩展的会话动态状态
- LoadReport / BuildResult / InjectionRecord: 结果与记录
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# ============================================================================
# 统一资料条目
# ============================================================================


@dataclass
class MaterialEntry:
    """统一资料条目，以 tier 字段区分层级。

    tier 语义：
        1 = 核心人格（persona_base / persona_narrative）
        3 = 技能/情境（skills/*.md / npc_profiles/*.md）
        4 = 世界观/原创（world_lore/*.md / narratives/*.md）
    （原 2 = 关系阶段档位，机制已移除）

    content 为 None 表示尚未加载，由 MaterialRegistry.fetch() 懒加载。
    """

    id: str
    title: str
    tier: int = 0
    kind: str = ""  # persona | skill | lore | narrative
    source_path: str = ""
    content: str | None = None
    trigger_keywords: tuple[str, ...] = ()
    trigger_patterns: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    default_ttl: int = 0
    priority: int = 50

    def is_empty(self) -> bool:
        """判断条目正文是否为空（用于过滤占位文件）。

        Returns:
            正文为空或全空白时为 True。
        """
        return not self.content or not self.content.strip()

    def is_loaded(self) -> bool:
        """判断条目内容是否已加载进内存。

        Returns:
            content 不为 None 即为已加载。
        """
        return self.content is not None


# ============================================================================
# 激活上下文
# ============================================================================


@dataclass
class ActivatedEntry:
    """激活上下文中的单条激活条目。"""

    entry_id: str
    remaining_ttl: int
    strength: float = 1.0  # 1.0 → 0.0
    activated_at_turn: int = 0


@dataclass
class ActiveContext:
    """当前会话的激活上下文，维护条目的生命周期。"""

    entries: list[ActivatedEntry] = field(default_factory=list)
    turn_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        """序列化为可持久化的字典（供状态存储/调试面板使用）。

        Returns:
            激活上下文的字典表示。
        """
        return {
            "entries": [
                {
                    "entry_id": e.entry_id,
                    "remaining_ttl": e.remaining_ttl,
                    "strength": e.strength,
                    "activated_at_turn": e.activated_at_turn,
                }
                for e in self.entries
            ],
            "turn_count": self.turn_count,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> ActiveContext:
        """从字典反序列化激活上下文。

        Args:
            data: to_dict() 产生的字典，为空或 None 时返回空上下文。

        Returns:
            还原后的 ActiveContext。
        """
        if not data:
            return cls()
        return cls(
            entries=[
                ActivatedEntry(
                    entry_id=str(e.get("entry_id", "")),
                    remaining_ttl=int(e.get("remaining_ttl", 0)),
                    strength=float(e.get("strength", 1.0)),
                    activated_at_turn=int(e.get("activated_at_turn", 0)),
                )
                for e in (data.get("entries") or [])
            ],
            turn_count=int(data.get("turn_count", 0)),
        )

    @property
    def active_ids(self) -> list[str]:
        """返回所有存活条目（remaining_ttl > 0）的 ID 列表。"""
        return [e.entry_id for e in self.entries if e.remaining_ttl > 0]

    @property
    def is_empty(self) -> bool:
        """判断激活上下文是否为空。"""
        return not self.entries


# ============================================================================
# 路由输出
# ============================================================================


@dataclass
class RouteSignals:
    """路由阶段检测到的信号（供 AffectEngine 消费）。

    注意：user_emotion 描述的是「用户」的情绪，它只是输入信号，
    绝不直接写入 SessionState.mood —— 她的心情由 AffectEngine 的事件反应决定。
    """

    user_emotion: str | None = None


@dataclass
class RouteResult:
    """路由阶段输出：本轮需要的条目 ID 列表 + 信号。"""

    needed_ids: list[str] = field(default_factory=list)
    signals: RouteSignals = field(default_factory=RouteSignals)
    source: str = "keyword"  # "llm" | "keyword"


# ============================================================================
# 动态会话状态
# ============================================================================


@dataclass
class SessionState:
    """单会话的动态状态（运行时维护）。

    说明：mood 表示「她自己的心情」（由 AffectEngine 反应得出），不是用户情绪；
    用户情绪仅在路由阶段作为信号存在（RouteSignals.user_emotion），不直接写入本字段。
    """

    session_id: str
    mood: str = "平静"
    mood_intensity: float = 0.4
    recent_topics: list[str] = field(default_factory=list)
    active_context: ActiveContext = field(default_factory=ActiveContext)
    last_message_at: float = 0.0
    updated_at: float = 0.0
    # ---- 主动消息相关 ----
    last_user_at: float = 0.0  # 用户最后发言时间
    last_proactive_at: float = 0.0  # 她最后主动发言时间
    unanswered_count: int = 0  # 连续未回复次数
    proactive_count_today: int = 0  # 当日主动次数
    proactive_day: str = ""  # 上述计数的日期锚点（YYYY-MM-DD）

    def to_dict(self) -> dict[str, Any]:
        """序列化为可持久化的字典（供 StateStore 落盘使用）。

        Returns:
            会话状态的字典表示。
        """
        return {
            "session_id": self.session_id,
            "mood": self.mood,
            "mood_intensity": self.mood_intensity,
            "recent_topics": list(self.recent_topics),
            "active_context": self.active_context.to_dict(),
            "last_message_at": self.last_message_at,
            "updated_at": self.updated_at,
            "last_user_at": self.last_user_at,
            "last_proactive_at": self.last_proactive_at,
            "unanswered_count": self.unanswered_count,
            "proactive_count_today": self.proactive_count_today,
            "proactive_day": self.proactive_day,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SessionState:
        """从字典反序列化会话状态。

        Args:
            data: to_dict() 产生的字典（旧版本缺少的字段按默认值补齐）。

        Returns:
            还原后的 SessionState。
        """
        return cls(
            session_id=str(data.get("session_id", "")),
            mood=str(data.get("mood", "平静")),
            mood_intensity=float(data.get("mood_intensity", 0.4)),
            recent_topics=[str(t) for t in data.get("recent_topics", [])],
            active_context=ActiveContext.from_dict(data.get("active_context")),
            last_message_at=float(data.get("last_message_at", 0.0)),
            updated_at=float(data.get("updated_at", 0.0)),
            last_user_at=float(data.get("last_user_at", 0.0)),
            last_proactive_at=float(data.get("last_proactive_at", 0.0)),
            unanswered_count=int(data.get("unanswered_count", 0)),
            proactive_count_today=int(data.get("proactive_count_today", 0)),
            proactive_day=str(data.get("proactive_day", "")),
        )

    def snapshot(self) -> SessionState:
        """返回一份独立快照，用于需要保留「变更前状态」的场景。

        复用 to_dict()/from_dict() 而非逐字段复制，使字段清单在全仓只有一处，
        避免新增字段时漏拷（历史上这里是第三份重复的字段列表）。

        Returns:
            与当前状态等值、但不共享任何可变对象的新实例。
        """
        return SessionState.from_dict(self.to_dict())

    def is_stale(self, now: float, decay_hours: float) -> bool:
        """判断状态是否已超时（超过 decay_hours 无互动则视为陈旧）。

        Args:
            now: 当前时间戳。
            decay_hours: 超时阈值（小时）。

        Returns:
            超时返回 True；从未互动（last_message_at 为 0）返回 False。
        """
        if not self.last_message_at:
            return False
        return now - self.last_message_at > decay_hours * 3600


# ============================================================================
# 组装与报告
# ============================================================================


@dataclass(frozen=True)
class BuildResult:
    """组装结果：最终注入文本 + 裁剪/超预算信息。"""

    text: str
    truncated: tuple[str, ...] = ()
    over_budget: bool = False

    def is_empty(self) -> bool:
        """判断组装结果是否为空（无有效注入文本）。"""
        return not self.text.strip()


@dataclass
class LoadReport:
    """一次加载/重载的结果：新条目列表 + 告警列表。"""

    entries: list[MaterialEntry] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def has_warnings(self) -> bool:
        """判断本次加载是否产生告警。"""
        return bool(self.warnings)

    def summary(self) -> str:
        """返回可读的加载摘要。"""
        tier_counts: dict[int, int] = {}
        for entry in self.entries:
            tier_counts[entry.tier] = tier_counts.get(entry.tier, 0) + 1

        lines = [f"资料条目总数：{len(self.entries)}"]
        for tier in sorted(tier_counts):
            lines.append(f"  Tier {tier}：{tier_counts[tier]} 条")
        for entry in self.entries:
            tags_str = "、".join(entry.tags[:3]) or "—"
            lines.append(
                f"  - [T{entry.tier}][{entry.kind}] {entry.id} "
                f"({entry.title}，标签：{tags_str})"
            )
        return "\n".join(lines)
