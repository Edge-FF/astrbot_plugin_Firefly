"""主动消息决策层（纯逻辑）。

核心模型：冲动值越阈值。
    urge = 静默小时数 × 情绪倾向 + 未回复次数 × 未回复加成
    urge >= 阈值(心情) 且通过全部硬闸门 → 允许主动

本模块不依赖 astrbot、不访问 IO、不调用 LLM，可完整单测，
并可直接把判定结果序列化给调试面板（"为什么发/没发"）。
"""

from __future__ import annotations

from collections.abc import Callable

from ..cognition.affect import AffectEngine
from ..config import ProactiveConfig
from ..models import SessionState

UNANSWERED_BOOST = 1.5  # 每次未回复带来的冲动加值

# 意图常量
INTENT_MISS = "miss"
INTENT_SHARE = "share"
INTENT_SEEK_COMFORT = "seek_comfort"
INTENT_CHECK = "check"
INTENT_CONTINUE = "continue_topic"
INTENT_CARE = "care"


def _is_quiet_time(quiet_hours: str, hour: int) -> bool:
    """判断给定小时是否落在免打扰区间内。

    支持跨天区间（如 "23-6"）。配置非法时按「不打扰」处理，
    避免因配置错误而误阻断主动消息。

    Args:
        quiet_hours: 形如 "1-7" 或 "23-6" 的区间字符串。
        hour: 0~23 的小时数。

    Returns:
        处于免打扰时段返回 True。
    """
    try:
        start_str, end_str = quiet_hours.split("-")
        start_hour, end_hour = int(start_str), int(end_str)
    except (ValueError, AttributeError):
        return False

    if start_hour <= end_hour:
        return start_hour <= hour < end_hour
    return hour >= start_hour or hour < end_hour


def _last_contact(state: SessionState) -> float:
    """最近一次「真实接触」的时间戳：用户发言或她主动发出。

    刻意**不**包含 `updated_at`——那是状态自身的写入时间。任何内部状态更新
    （最典型的是长时间静默时写入「想念」）都会把它设为 now，从而把静默时长
    清零：结果是「越是想念，越推迟发起」——静默事件会把自己该触发的那一次
    拦在 `recent_contact` / `below_threshold` 上。实测 72 小时静默进入「想念」
    后 idle 立即归零。

    两个字段都缺失时返回 0，由调用方按「从未接触」处理（idle 视为 0，
    不会误判成无限久没接触）。

    Args:
        state: 会话状态。

    Returns:
        最近一次真实接触的时间戳；从未接触过时为 0。
    """
    return max(state.last_user_at, state.last_proactive_at)


class ProactivePolicy:
    """主动消息决策（纯函数集合）。"""

    def __init__(
        self,
        affect: AffectEngine,
        config: ProactiveConfig | Callable[[], ProactiveConfig],
        unanswered_boost: float = UNANSWERED_BOOST,
    ) -> None:
        """初始化决策器。

        Args:
            affect: 情绪引擎（提供心情档案）。
            config: 主动消息配置，或返回配置的可调用对象（用于实时读取）。
            unanswered_boost: 未回复冲量系数。
        """
        self._affect = affect
        self._config_getter = config if callable(config) else (lambda: config)
        self._boost = unanswered_boost

    @property
    def config(self) -> ProactiveConfig:
        """返回当前配置（每次调用实时解析，支持运行时切换开关）。"""
        return self._config_getter()

    # ------------------------------------------------------------------
    # 冲动值
    # ------------------------------------------------------------------

    def idle_hours(self, state: SessionState, now: float) -> float:
        """返回距上次接触的静默小时数（非负）。

        Args:
            state: 会话状态。
            now: 当前时间戳。

        Returns:
            静默小时数。
        """
        last_contact = _last_contact(state)
        return max(0.0, (now - last_contact) / 3600.0) if last_contact else 0.0

    def compute_urge(self, state: SessionState, now: float) -> float:
        """计算当前冲动值。

        Args:
            state: 会话状态。
            now: 当前时间戳。

        Returns:
            冲动值（非负）。
        """
        profile = self._affect.profile(state.mood)
        return (
            self.idle_hours(state, now) * profile.rate
            + state.unanswered_count * self._boost
        )

    def threshold(self, state: SessionState) -> float:
        """返回当前心情对应的触发阈值。

        Args:
            state: 会话状态。

        Returns:
            阈值。
        """
        return self._affect.profile(state.mood).threshold

    # ------------------------------------------------------------------
    # 闸门
    # ------------------------------------------------------------------

    def check_gates(
        self,
        state: SessionState,
        now: float,
        plugin_start: float,
        *,
        registry_ready: bool = True,
        provider_ready: bool = True,
        hour: int | None = None,
    ) -> tuple[bool, str]:
        """检查全部硬闸门。

        Args:
            state: 会话状态。
            now: 当前时间戳。
            plugin_start: 插件启动时间戳（用于启动宽限）。
            registry_ready: 资料是否已加载（能力闸门）。
            provider_ready: 是否有可用 LLM Provider（能力闸门）。
            hour: 当前小时数，缺省由 now 推导。

        Returns:
            (是否通过, 原因)。通过时原因为 "ok"。
        """
        cfg = self.config
        hour = hour if hour is not None else _local_hour(now)

        if not cfg.enabled:
            return False, "disabled"
        if not registry_ready:
            return False, "empty_registry"
        if not provider_ready:
            return False, "no_provider"
        if not cfg.is_session_enabled(state.session_id):
            return False, "session_filtered"
        if _is_quiet_time(cfg.quiet_hours, hour):
            return False, "quiet_hours"

        last_contact = _last_contact(state)
        if last_contact and (now - last_contact) < cfg.min_contact_gap_minutes * 60:
            return False, "recent_contact"
        if (
            state.last_proactive_at
            and (now - state.last_proactive_at)
            < cfg.min_proactive_interval_minutes * 60
        ):
            return False, "recent_proactive"
        if cfg.max_unanswered > 0 and state.unanswered_count >= cfg.max_unanswered:
            return False, "max_unanswered"
        if cfg.max_per_day > 0:
            # 按「今天」归一化（见 daily_count）：否则配额用尽后跨天仍会被拦下，
            # 而计数只在「发送成功后」重置（runner._after_send），被拦下就永远
            # 不会重置 → 该会话永久失效。系统非 24 小时运行时（跨午夜关机）
            # 正是触发场景。
            if daily_count(state, now) >= cfg.max_per_day:
                return False, "daily_limit"
        if plugin_start and (now - plugin_start) < cfg.startup_grace_seconds:
            return False, "startup_grace"

        return True, "ok"

    def should_reach_out(
        self,
        state: SessionState,
        now: float,
        plugin_start: float,
        *,
        registry_ready: bool = True,
        provider_ready: bool = True,
        hour: int | None = None,
    ) -> tuple[bool, str]:
        """综合判定是否应当主动发起。

        Args:
            state: 会话状态。
            now: 当前时间戳。
            plugin_start: 插件启动时间戳。
            registry_ready: 资料是否已加载。
            provider_ready: 是否有可用 LLM Provider。
            hour: 当前小时数（缺省由 now 推导）。

        Returns:
            (是否发起, 原因)。原因可能是闸门名或 "below_threshold"。
        """
        passed, reason = self.check_gates(
            state,
            now,
            plugin_start,
            registry_ready=registry_ready,
            provider_ready=provider_ready,
            hour=hour,
        )
        if not passed:
            return False, reason
        if self.compute_urge(state, now) < self.threshold(state):
            return False, "below_threshold"
        return True, "ok"

    # ------------------------------------------------------------------
    # 意图
    # ------------------------------------------------------------------

    def pick_intent(self, state: SessionState, now: float) -> str:
        """根据心情与状态选择主动消息的意图。

        Args:
            state: 会话状态。
            now: 当前时间戳。

        Returns:
            意图标识（见 INTENT_* 常量）。
        """
        if state.unanswered_count > 0:
            return INTENT_CHECK
        if state.mood == "想念":
            return INTENT_MISS
        if state.mood in ("委屈", "难过"):
            return INTENT_SEEK_COMFORT
        if state.mood in ("开心", "惊喜") and state.recent_topics:
            return INTENT_SHARE
        if state.recent_topics:
            return INTENT_CONTINUE
        return self._affect.profile(state.mood).default_intent or INTENT_CARE


def _local_hour(now: float) -> int:
    """从时间戳取得本地小时数。

    Args:
        now: 时间戳。

    Returns:
        0~23 的本地小时数；转换失败时返回 -1（必然不在免打扰区间）。
    """
    try:
        from datetime import datetime

        return datetime.fromtimestamp(now).hour
    except (OSError, OverflowError, ValueError):
        return -1


def day_key(timestamp: float) -> str:
    """把时间戳格式化为本地日期键（YYYY-MM-DD）。

    用于日配额判定：跨天意味着配额重置，因此"今天"必须与状态里记录的
    `proactive_day` 用同一套本地日期规则比较。

    Args:
        timestamp: 时间戳。

    Returns:
        日期字符串；转换失败时返回空串——空串与任何真实日期都不相等，
        因而会被判为"不是今天"（即配额重置），这是安全方向。
    """
    try:
        from datetime import datetime

        return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d")
    except (OSError, OverflowError, ValueError):
        return ""


def daily_count(state: SessionState, now: float) -> int:
    """返回该会话「今天」已发出的主动消息条数（跨天视为 0）。

    这是日配额的唯一语义来源，供闸门判定与面板展示共用。之所以抽成函数而不是
    在两处各写一遍条件：此前闸门只看计数、不看日期，而计数只在发送成功后重置，
    两者对"今天"的理解不一致，导致配额用尽后跨天永久失效。

    Args:
        state: 会话状态。
        now: 当前时间戳。

    Returns:
        今天已发出的条数；记录的日期不是今天时为 0。
    """
    if state.proactive_day == day_key(now):
        return state.proactive_count_today
    return 0
