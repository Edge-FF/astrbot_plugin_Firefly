"""认知外壳数据模型。

核心类型：
- MaterialEntry: 统一资料条目，以 tier 字段区分层级（1-4）
- ActiveContext / ActivatedEntry: 激活上下文与惯性管理
- RouteResult / RouteSignals: LLM/关键词路由输出
- SessionState: 扩展的会话动态状态
- ShellConfig: 插件运行配置
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


# ============================================================================
# 插件运行配置
# ============================================================================


@dataclass(frozen=True)
class ShellConfig:
    """插件运行配置（v0.2 扩展版）。"""

    enabled: bool = True
    max_tokens: int = 1500
    max_on_demand: int = 3
    enabled_sessions: tuple[str, ...] = ()

    # 注入预算
    tier1_reserved: int = 600

    # 路由器
    router_use_llm: bool = True
    router_llm_timeout: float = 3.0
    router_fallback_to_keyword: bool = True
    router_cache_enabled: bool = True

    # 状态
    persist_state: bool = True
    state_use_llm: bool = False
    decay_hours: float = 0.0
    max_topics: int = 5

    # 激活上下文
    default_skill_ttl: int = 4
    default_lore_ttl: int = 2
    default_narrative_ttl: int = 6
    strength_decay_per_turn: float = 0.2
    min_strength: float = 0.3

    # 内容缓存
    content_cache_max_entries: int = 50

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> ShellConfig:
        """从插件配置字典构建 ShellConfig，缺失字段使用默认值。

        Args:
            data: _conf_schema.json 对应的配置字典，可为 None。

        Returns:
            填充好默认值的 ShellConfig。
        """
        data = data or {}
        inject = data.get("inject", {}) or {}
        state_cfg = data.get("state", {}) or {}
        router_cfg = data.get("router", {}) or {}
        active_cfg = data.get("active_context", {}) or {}
        cache_cfg = data.get("content_cache", {}) or {}

        def _int(value: Any, default: int) -> int:
            """安全转 int，失败时返回默认值。"""
            try:
                return int(value)
            except (TypeError, ValueError):
                return default

        def _float(value: Any, default: float) -> float:
            """安全转 float，失败时返回默认值。"""
            try:
                return float(value)
            except (TypeError, ValueError):
                return default

        def _bool(value: Any, default: bool) -> bool:
            """安全转 bool：布尔直用，字符串按常见真值解析，其余返回默认值。"""
            if isinstance(value, bool):
                return value
            if isinstance(value, str):
                return value.strip().lower() in ("1", "true", "yes", "on")
            return default

        enabled_sessions = inject.get("enabled_sessions", []) or []
        if isinstance(enabled_sessions, str):
            enabled_sessions = [enabled_sessions]

        return cls(
            enabled=_bool(data.get("enabled"), True),
            max_tokens=_int(inject.get("max_tokens"), 1500),
            max_on_demand=_int(inject.get("max_on_demand"), 3),
            enabled_sessions=tuple(str(s) for s in enabled_sessions if str(s).strip()),
            tier1_reserved=_int(inject.get("tier1_reserved"), 600),
            router_use_llm=_bool(router_cfg.get("use_llm"), False),
            router_llm_timeout=_float(router_cfg.get("llm_timeout_seconds"), 3.0),
            router_fallback_to_keyword=_bool(
                router_cfg.get("fallback_to_keyword"), True
            ),
            router_cache_enabled=_bool(router_cfg.get("cache_enabled"), True),
            persist_state=_bool(state_cfg.get("persist"), True),
            state_use_llm=_bool(state_cfg.get("use_llm"), False),
            decay_hours=_float(state_cfg.get("decay_hours"), 0.0),
            max_topics=_int(state_cfg.get("max_topics"), 5),
            default_skill_ttl=_int(active_cfg.get("default_skill_ttl"), 4),
            default_lore_ttl=_int(active_cfg.get("default_lore_ttl"), 2),
            default_narrative_ttl=_int(active_cfg.get("default_narrative_ttl"), 6),
            strength_decay_per_turn=_float(
                active_cfg.get("strength_decay_per_turn"), 0.2
            ),
            min_strength=_float(active_cfg.get("min_strength"), 0.3),
            content_cache_max_entries=_int(cache_cfg.get("max_entries"), 50),
        )

    def is_session_enabled(self, session_id: str) -> bool:
        """判断指定会话是否在启用名单内。

        Args:
            session_id: 会话唯一标识。

        Returns:
            启用名单为空时所有会话放行；否则仅在名单内返回 True。
        """
        if not self.enabled_sessions:
            return True
        return session_id in self.enabled_sessions

    def get_default_ttl(self, kind: str) -> int:
        """按资料类型获取默认激活轮数。

        Args:
            kind: 资料类型（skill/lore/narrative 等）。

        Returns:
            对应的默认 TTL；未知类型返回 1。
        """
        if kind == "skill":
            return self.default_skill_ttl
        if kind == "lore":
            return self.default_lore_ttl
        if kind == "narrative":
            return self.default_narrative_ttl
        return 1


@dataclass(frozen=True)
class ProactiveConfig:
    """主动消息运行配置（v1）。

    触发模型为「冲动值越阈值」，非随机区间；所有时间量均可用时间戳惰性计算。
    """

    enabled: bool = False
    tick_interval_seconds: float = 120.0
    min_contact_gap_minutes: int = 30
    min_proactive_interval_minutes: int = 60
    max_unanswered: int = 4
    max_per_day: int = 6
    max_sends_per_tick: int = 1
    silence_hours: float = 6.0
    quiet_hours: str = "1-7"
    startup_grace_seconds: float = 120.0
    sessions: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> ProactiveConfig:
        """从插件配置字典构建 ProactiveConfig，缺失字段使用默认值。

        Args:
            data: _conf_schema.json 对应的配置字典，可为 None。

        Returns:
            填充好默认值的 ProactiveConfig。
        """
        data = data or {}
        cfg = data.get("proactive", {}) or {}

        def _bool(value: Any, default: bool) -> bool:
            """安全转 bool。"""
            if isinstance(value, bool):
                return value
            if isinstance(value, str):
                return value.strip().lower() in ("1", "true", "yes", "on")
            return default

        def _int(value: Any, default: int) -> int:
            """安全转 int。"""
            try:
                return int(value)
            except (TypeError, ValueError):
                return default

        def _float(value: Any, default: float) -> float:
            """安全转 float。"""
            try:
                return float(value)
            except (TypeError, ValueError):
                return default

        sessions = cfg.get("sessions", []) or []
        if isinstance(sessions, str):
            sessions = [sessions]

        return cls(
            enabled=_bool(cfg.get("enabled"), False),
            tick_interval_seconds=max(
                _float(cfg.get("tick_interval_seconds"), 120.0), 10.0
            ),
            min_contact_gap_minutes=max(
                _int(cfg.get("min_contact_gap_minutes"), 30), 0
            ),
            min_proactive_interval_minutes=max(
                _int(cfg.get("min_proactive_interval_minutes"), 60), 0
            ),
            max_unanswered=max(_int(cfg.get("max_unanswered"), 4), 0),
            max_per_day=max(_int(cfg.get("max_per_day"), 6), 0),
            max_sends_per_tick=max(_int(cfg.get("max_sends_per_tick"), 1), 1),
            silence_hours=max(_float(cfg.get("silence_hours"), 6.0), 0.0),
            quiet_hours=str(cfg.get("quiet_hours") or "1-7"),
            startup_grace_seconds=max(
                _float(cfg.get("startup_grace_seconds"), 120.0), 0.0
            ),
            sessions=tuple(str(s) for s in sessions if str(s).strip()),
        )

    def is_session_enabled(self, session_id: str) -> bool:
        """判断会话是否在主动消息生效名单内。

        Args:
            session_id: 会话唯一标识。

        Returns:
            名单为空时全部放行；否则仅在名单内返回 True。
        """
        if not self.sessions:
            return True
        return session_id in self.sessions


# ============================================================================
# 调试记录 v0.2
# ============================================================================


@dataclass
class InjectionRecord:
    """单次注入的完整快照（供调试面板使用）。"""

    record_id: str
    session_id: str
    timestamp: float = 0.0

    # 输入
    user_msg: str = ""

    # 路由结果
    route_needed_ids: list[str] = field(default_factory=list)
    route_signals_emotion: str | None = None
    route_source: str = "keyword"

    # 注入前状态
    mood_before: str = ""
    active_context_ids_before: list[str] = field(default_factory=list)

    # 合并后的激活上下文
    active_context_after: list[dict[str, Any]] = field(default_factory=list)

    # 组装结果
    injection_xml: str = ""
    truncated_ids: list[str] = field(default_factory=list)
    over_budget: bool = False
    token_estimate: int = 0

    # 注入状态
    injected_successfully: bool = True
    skipped_reason: str | None = None

    @classmethod
    def create(
        cls,
        record_id: str,
        session_id: str,
        timestamp: float,
        user_msg: str,
        route_result: RouteResult,
        mood_before: str,
        active_before: ActiveContext,
        active_after: ActiveContext,
        xml_text: str,
        truncated: tuple[str, ...],
        over_budget: bool,
        injected: bool = True,
        skipped_reason: str | None = None,
    ) -> InjectionRecord:
        """从注入流程各阶段的数据构建完整快照记录。

        Args:
            record_id: 记录唯一标识。
            session_id: 会话唯一标识。
            timestamp: 注入时间戳。
            user_msg: 用户消息文本（截断到 200 字符）。
            route_result: 路由阶段的结果（含 needed_ids 与 signals）。
            mood_before: 注入前的心情。
            active_before: 注入前的激活上下文。
            active_after: 合并后的激活上下文。
            xml_text: 组装出的注入 XML（截断到 8000 字符）。
            truncated: 被裁剪的条目 ID 列表。
            over_budget: 是否超出 token 预算。
            injected: 是否成功注入。
            skipped_reason: 跳过的原因（未注入时非空）。

        Returns:
            填充完成的 InjectionRecord。
        """
        return cls(
            record_id=record_id,
            session_id=session_id,
            timestamp=timestamp,
            user_msg=user_msg[:200],
            route_needed_ids=list(route_result.needed_ids),
            route_signals_emotion=route_result.signals.user_emotion,
            route_source=route_result.source,
            mood_before=mood_before,
            active_context_ids_before=active_before.active_ids,
            active_context_after=[
                {
                    "entry_id": e.entry_id,
                    "remaining_ttl": e.remaining_ttl,
                    "strength": round(e.strength, 2),
                    "activated_at_turn": e.activated_at_turn,
                }
                for e in active_after.entries
            ],
            injection_xml=xml_text[:8000],
            truncated_ids=list(truncated),
            over_budget=over_budget,
            token_estimate=len(xml_text) // 2,
            injected_successfully=injected,
            skipped_reason=skipped_reason,
        )

    def to_api_dict(self) -> dict[str, Any]:
        """转换为调试面板可直接返回的字典格式。

        Returns:
            扁平化后的记录字典（含嵌套 signals）。
        """
        return {
            "record_id": self.record_id,
            "session_id": self.session_id,
            "timestamp": self.timestamp,
            "user_msg": self.user_msg,
            "route_needed_ids": self.route_needed_ids,
            "route_signals": {
                "user_emotion": self.route_signals_emotion,
            },
            "route_source": self.route_source,
            "mood_before": self.mood_before,
            "active_context_ids_before": self.active_context_ids_before,
            "active_context_after": self.active_context_after,
            "injection_xml": self.injection_xml,
            "truncated_ids": self.truncated_ids,
            "over_budget": self.over_budget,
            "token_estimate": self.token_estimate,
            "injected_successfully": self.injected_successfully,
            "skipped_reason": self.skipped_reason,
        }


@dataclass
class ProactiveRecord:
    """单次主动决策的快照（供调试面板查看「为什么发/没发」）。"""

    record_id: str
    session_id: str
    timestamp: float = 0.0
    allowed: bool = False  # 是否通过全部闸门且越阈值
    reason: str = ""  # 允许/拒绝的原因
    intent: str = ""  # 意图（miss/share/...）
    urge: float = 0.0  # 冲动值
    threshold: float = 0.0  # 当前阈值
    mood: str = ""  # 决策时的心情
    text: str = ""  # 实际生成并发送的文本（未发送为空）
    sent: bool = False
    error: str = ""

    def to_api_dict(self) -> dict[str, Any]:
        """转换为调试面板可直接返回的字典格式。

        Returns:
            扁平化后的记录字典。
        """
        return {
            "record_id": self.record_id,
            "session_id": self.session_id,
            "timestamp": self.timestamp,
            "allowed": self.allowed,
            "reason": self.reason,
            "intent": self.intent,
            "urge": round(self.urge, 2),
            "threshold": round(self.threshold, 2),
            "mood": self.mood,
            "text": self.text[:500],
            "sent": self.sent,
            "error": self.error[:300],
        }
