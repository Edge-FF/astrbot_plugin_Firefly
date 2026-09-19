"""插件运行配置模型。

从 `core.models` 拆出：这里描述的是「插件如何运行」（开关、预算、阈值），
与「领域实体」（资料、会话状态、记录）是两类不同的东西，混在一个文件里
会让每次调参都触碰领域模型。

本模块为纯数据与纯解析：不依赖 astrbot、不访问 IO。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from . import consts
from .materials.parsers import coerce_bool, coerce_float, coerce_int


@dataclass(frozen=True)
class ShellConfig:
    """插件运行配置（v0.2 扩展版）。"""

    enabled: bool = True
    max_tokens: int = 1500
    max_on_demand: int = 3
    enabled_sessions: tuple[str, ...] = ()

    # 注入预算
    tier1_reserved: int = 830
    # 用户身份块长度上限（token）；独立于总预算，超限时截断身份块自身
    user_profile_max_tokens: int = 600

    # 路由器
    router_use_llm: bool = True
    router_llm_timeout: float = 3.0
    router_fallback_to_keyword: bool = True
    router_cache_enabled: bool = True

    # 状态
    persist_state: bool = True
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
    def from_dict(
        cls,
        data: dict[str, Any] | None,
        warnings: list[str] | None = None,
    ) -> ShellConfig:
        """从插件配置字典构建 ShellConfig，缺失字段使用默认值。

        缺失（None）静默使用默认值；存在但无法转换的脏值会回退为默认值，
        并在 `warnings` 中记录一条说明（原先这类脏值被静默吞掉）。

        Args:
            data: _conf_schema.json 对应的配置字典，可为 None。
            warnings: 告警收集列表，None 表示静默降级。

        Returns:
            填充好默认值的 ShellConfig。
        """
        data = data or {}
        inject = data.get("inject", {}) or {}
        state_cfg = data.get("state", {}) or {}
        router_cfg = data.get("router", {}) or {}
        active_cfg = data.get("active_context", {}) or {}
        cache_cfg = data.get("content_cache", {}) or {}
        user_role_cfg = data.get("user_role", {}) or {}

        enabled_sessions = inject.get("enabled_sessions", []) or []
        if isinstance(enabled_sessions, str):
            enabled_sessions = [enabled_sessions]

        return cls(
            enabled=coerce_bool(data.get("enabled"), True, warnings, "enabled"),
            max_tokens=coerce_int(
                inject.get("max_tokens"), 1500, warnings, "inject.max_tokens"
            ),
            max_on_demand=coerce_int(
                inject.get("max_on_demand"), 3, warnings, "inject.max_on_demand"
            ),
            enabled_sessions=tuple(str(s) for s in enabled_sessions if str(s).strip()),
            tier1_reserved=coerce_int(
                inject.get("tier1_reserved"), 830, warnings, "inject.tier1_reserved"
            ),
            # 下限 50：过小的上限会让身份块只剩截断标记，失去注入意义
            user_profile_max_tokens=max(
                coerce_int(
                    user_role_cfg.get("user_profile_max_tokens"),
                    600,
                    warnings,
                    "user_role.user_profile_max_tokens",
                ),
                50,
            ),
            router_use_llm=coerce_bool(
                router_cfg.get("use_llm"), False, warnings, "router.use_llm"
            ),
            router_llm_timeout=coerce_float(
                router_cfg.get("llm_timeout_seconds"),
                3.0,
                warnings,
                "router.llm_timeout_seconds",
            ),
            router_fallback_to_keyword=coerce_bool(
                router_cfg.get("fallback_to_keyword"),
                True,
                warnings,
                "router.fallback_to_keyword",
            ),
            router_cache_enabled=coerce_bool(
                router_cfg.get("cache_enabled"),
                True,
                warnings,
                "router.cache_enabled",
            ),
            persist_state=coerce_bool(
                state_cfg.get("persist"), True, warnings, "state.persist"
            ),
            decay_hours=coerce_float(
                state_cfg.get("decay_hours"), 0.0, warnings, "state.decay_hours"
            ),
            max_topics=coerce_int(
                state_cfg.get("max_topics"), 5, warnings, "state.max_topics"
            ),
            default_skill_ttl=coerce_int(
                active_cfg.get("default_skill_ttl"),
                4,
                warnings,
                "active_context.default_skill_ttl",
            ),
            default_lore_ttl=coerce_int(
                active_cfg.get("default_lore_ttl"),
                2,
                warnings,
                "active_context.default_lore_ttl",
            ),
            default_narrative_ttl=coerce_int(
                active_cfg.get("default_narrative_ttl"),
                6,
                warnings,
                "active_context.default_narrative_ttl",
            ),
            strength_decay_per_turn=coerce_float(
                active_cfg.get("strength_decay_per_turn"),
                0.2,
                warnings,
                "active_context.strength_decay_per_turn",
            ),
            min_strength=coerce_float(
                active_cfg.get("min_strength"),
                0.3,
                warnings,
                "active_context.min_strength",
            ),
            content_cache_max_entries=coerce_int(
                cache_cfg.get("max_entries"),
                50,
                warnings,
                "content_cache.max_entries",
            ),
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
        """按资料类型获取默认激活轮数（供资料加载时作为兜底 TTL）。

        按 `consts.KIND_*` 判定，未知类型回退到 `consts.DEFAULT_TTL_MAP.get(kind, 0)`，
        与注册表自身的回退语义保持一致（persona 等常驻类型因此为 0）。

        Args:
            kind: 资料类型（skill / lore / narrative 等）。

        Returns:
            对应的默认 TTL；无对应配置时回退为 `consts.DEFAULT_TTL_MAP` 的值。
        """
        if kind == consts.KIND_SKILL:
            return self.default_skill_ttl
        if kind == consts.KIND_LORE:
            return self.default_lore_ttl
        if kind == consts.KIND_NARRATIVE:
            return self.default_narrative_ttl
        return consts.DEFAULT_TTL_MAP.get(kind, 0)


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
    def from_dict(
        cls,
        data: dict[str, Any] | None,
        warnings: list[str] | None = None,
    ) -> ProactiveConfig:
        """从插件配置字典构建 ProactiveConfig，缺失字段使用默认值。

        缺失（None）静默使用默认值；存在但无法转换的脏值会回退为默认值并在
        `warnings` 中记录一条说明。数值项在回退后仍按各自的合法下限钳制。

        Args:
            data: _conf_schema.json 对应的配置字典，可为 None。
            warnings: 告警收集列表，None 表示静默降级。

        Returns:
            填充好默认值的 ProactiveConfig。
        """
        data = data or {}
        cfg = data.get("proactive", {}) or {}

        sessions = cfg.get("sessions", []) or []
        if isinstance(sessions, str):
            sessions = [sessions]

        return cls(
            enabled=coerce_bool(
                cfg.get("enabled"), False, warnings, "proactive.enabled"
            ),
            tick_interval_seconds=max(
                coerce_float(
                    cfg.get("tick_interval_seconds"),
                    120.0,
                    warnings,
                    "proactive.tick_interval_seconds",
                ),
                10.0,
            ),
            min_contact_gap_minutes=max(
                coerce_int(
                    cfg.get("min_contact_gap_minutes"),
                    30,
                    warnings,
                    "proactive.min_contact_gap_minutes",
                ),
                0,
            ),
            min_proactive_interval_minutes=max(
                coerce_int(
                    cfg.get("min_proactive_interval_minutes"),
                    60,
                    warnings,
                    "proactive.min_proactive_interval_minutes",
                ),
                0,
            ),
            max_unanswered=max(
                coerce_int(
                    cfg.get("max_unanswered"), 4, warnings, "proactive.max_unanswered"
                ),
                0,
            ),
            max_per_day=max(
                coerce_int(
                    cfg.get("max_per_day"), 6, warnings, "proactive.max_per_day"
                ),
                0,
            ),
            max_sends_per_tick=max(
                coerce_int(
                    cfg.get("max_sends_per_tick"),
                    1,
                    warnings,
                    "proactive.max_sends_per_tick",
                ),
                1,
            ),
            silence_hours=max(
                coerce_float(
                    cfg.get("silence_hours"), 6.0, warnings, "proactive.silence_hours"
                ),
                0.0,
            ),
            quiet_hours=str(cfg.get("quiet_hours") or "1-7"),
            startup_grace_seconds=max(
                coerce_float(
                    cfg.get("startup_grace_seconds"),
                    120.0,
                    warnings,
                    "proactive.startup_grace_seconds",
                ),
                0.0,
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
