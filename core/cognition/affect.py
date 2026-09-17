"""情绪引擎：事件驱动的心情演化。

设计要点：
- 心情是「她的反应」，不是用户情绪的回声：先把用户信号转成事件，再由反应表决定她变成什么心情。
- 时间维度用半衰期惰性求值：只存 mood + mood_intensity + updated_at，用时现场算，不用定时器，重启安全。
- 纯逻辑层：不依赖 astrbot，不依赖 IO，可独立单测。
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from ..models import SessionState

# ============================================================================
# 常量
# ============================================================================

BASELINE_MOOD = "平静"
BASELINE_INTENSITY = 0.4
FLOOR_INTENSITY = 0.15  # 强度低于该值时回落基线
SWITCH_MIN_INTENSITY = 0.4  # 事件强度低于该值不引起心情切换
ACCUMULATE_FACTOR = 0.5  # 同类事件叠加系数
MAX_INTENSITY = 1.0

# 事件类型
EVENT_AFFECTION = "affection"  # 用户亲昵
EVENT_PRAISE = "praise"  # 用户夸奖
EVENT_HUMOR = "humor"  # 用户玩梗/开心
EVENT_DISTRESS = "distress"  # 用户倾诉/低落
EVENT_REJECTION = "rejection"  # 用户凶她/冷淡
EVENT_REUNION = "reunion"  # 久别或回复她的主动消息
EVENT_SILENCE = "silence"  # 长时间静默（由时间驱动，非文本）
EVENT_PROACTIVE_SENT = "proactive_sent"  # 她主动发了消息
EVENT_PROACTIVE_UNANSWERED = "proactive_unanswered"  # 她主动了但用户没回


def _clamp(value: float, low: float = 0.0, high: float = MAX_INTENSITY) -> float:
    """把强度夹到合法区间。

    Args:
        value: 原始强度。
        low: 下界。
        high: 上界。

    Returns:
        夹取后的强度。
    """
    return max(low, min(high, value))


# ============================================================================
# 数据模型
# ============================================================================


@dataclass(frozen=True)
class AffectEvent:
    """一次影响心情的事件。"""

    kind: str
    weight: float = 1.0


@dataclass(frozen=True)
class MoodProfile:
    """单个心情的档案：主动倾向、阈值、半衰期、默认意图。"""

    mood: str
    rate: float = 1.0
    threshold: float = 4.0
    half_life_hours: float = 6.0
    default_intent: str = "care"


# 心情档案表（人设数字化：可整体替换/配置）
MOOD_PROFILES: dict[str, MoodProfile] = {
    "平静": MoodProfile(
        "平静", rate=1.0, threshold=4.0, half_life_hours=6.0, default_intent="care"
    ),
    "开心": MoodProfile(
        "开心", rate=1.4, threshold=3.5, half_life_hours=4.0, default_intent="share"
    ),
    "惊喜": MoodProfile(
        "惊喜", rate=1.6, threshold=3.0, half_life_hours=2.0, default_intent="share"
    ),
    "想念": MoodProfile(
        "想念", rate=2.0, threshold=2.5, half_life_hours=12.0, default_intent="miss"
    ),
    "委屈": MoodProfile(
        "委屈",
        rate=1.6,
        threshold=3.0,
        half_life_hours=10.0,
        default_intent="seek_comfort",
    ),
    "心疼": MoodProfile(
        "心疼", rate=1.5, threshold=3.0, half_life_hours=8.0, default_intent="care"
    ),
    "难过": MoodProfile(
        "难过",
        rate=0.8,
        threshold=5.0,
        half_life_hours=6.0,
        default_intent="seek_comfort",
    ),
    "疲惫": MoodProfile(
        "疲惫", rate=0.6, threshold=6.0, half_life_hours=5.0, default_intent="care"
    ),
    "生气": MoodProfile(
        "生气", rate=0.2, threshold=8.0, half_life_hours=3.0, default_intent="care"
    ),
}

# 事件 → (她的目标心情, 基础强度)
_REACTIONS: dict[str, tuple[str, float]] = {
    EVENT_AFFECTION: ("开心", 0.8),
    EVENT_PRAISE: ("开心", 0.7),
    EVENT_HUMOR: ("开心", 0.5),
    EVENT_DISTRESS: ("心疼", 0.8),
    EVENT_REJECTION: ("委屈", 0.8),
    EVENT_REUNION: ("开心", 0.6),
    EVENT_SILENCE: ("想念", 0.5),
    EVENT_PROACTIVE_UNANSWERED: ("委屈", 0.5),
    EVENT_PROACTIVE_SENT: ("", 0.0),
}

# 用户文本 → 事件（LLM 路由关闭时的兜底信号源）
_TEXT_EVENT_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        EVENT_AFFECTION,
        ("想你", "想你了", "思念", "喜欢你", "爱你", "抱抱", "亲亲", "最爱"),
    ),
    (EVENT_PRAISE, ("厉害", "好棒", "真棒", "可爱", "好喜欢", "优秀")),
    (EVENT_HUMOR, ("哈哈", "笑死", "233", "hhh", "好笑")),
    (EVENT_DISTRESS, ("难过", "伤心", "委屈", "好累", "压力", "焦虑", "害怕", "崩溃")),
    (EVENT_REJECTION, ("讨厌", "别烦", "烦人", "不理你", "不想理", "闭嘴")),
)

# LLM 路由判定的「用户情绪」→ 事件
_EMOTION_TO_EVENTS: dict[str, tuple[str, ...]] = {
    "开心": (EVENT_HUMOR,),
    "惊喜": (EVENT_HUMOR,),
    "想念": (EVENT_AFFECTION,),
    "难过": (EVENT_DISTRESS,),
    "疲惫": (EVENT_DISTRESS,),
    "生气": (EVENT_REJECTION,),
}


# ============================================================================
# 引擎
# ============================================================================


class AffectEngine:
    """事件驱动的情绪引擎（纯逻辑）。

    用法：
        engine.apply(state, [AffectEvent("distress")], now)
    """

    def __init__(
        self,
        profiles: dict[str, MoodProfile] | None = None,
        baseline: str = BASELINE_MOOD,
    ) -> None:
        """初始化情绪引擎。

        Args:
            profiles: 心情档案表，缺省使用内置表。
            baseline: 基线心情（无事件时回落目标）。
        """
        self._profiles = dict(profiles or MOOD_PROFILES)
        self._baseline = baseline

    def profile(self, mood: str) -> MoodProfile:
        """获取心情档案，未知心情回落到基线档案。

        Args:
            mood: 心情名。

        Returns:
            对应的心情档案。
        """
        if mood in self._profiles:
            return self._profiles[mood]
        return self._profiles.get(self._baseline, MoodProfile(self._baseline))

    def decay(
        self, mood: str, intensity: float, elapsed_hours: float
    ) -> tuple[str, float]:
        """按半衰期衰减强度；低于下界时回落基线。

        Args:
            mood: 当前心情。
            intensity: 当前强度。
            elapsed_hours: 距上次更新的小时数（负数按 0 处理）。

        Returns:
            (衰减后的心情, 衰减后的强度)。
        """
        hours = max(0.0, elapsed_hours)
        prof = self.profile(mood)
        if hours > 0 and prof.half_life_hours > 0:
            intensity = intensity * (0.5 ** (hours / prof.half_life_hours))
        if mood != self._baseline and intensity < FLOOR_INTENSITY:
            return self._baseline, BASELINE_INTENSITY
        return mood, intensity

    def apply(
        self,
        state: SessionState,
        events: list[AffectEvent],
        now: float,
        stale_reset_hours: float = 0.0,
    ) -> SessionState:
        """应用事件并推进时间，返回新的会话状态（不修改原对象）。

        Args:
            state: 当前会话状态。
            events: 本轮事件列表（可为空）。
            now: 当前时间戳。
            stale_reset_hours: 陈旧阈值（小时）。超过该时长无互动时，
                心情先硬性回落基线（对应配置 state.decay_hours）；0 表示不启用。

        Returns:
            更新了 mood / mood_intensity / updated_at 的新状态。
        """
        if stale_reset_hours > 0 and state.is_stale(now, stale_reset_hours):
            mood, intensity = self._baseline, BASELINE_INTENSITY
        else:
            elapsed_hours = (
                max(0.0, (now - state.updated_at) / 3600.0) if state.updated_at else 0.0
            )
            mood, intensity = self.decay(
                state.mood, state.mood_intensity, elapsed_hours
            )

        for event in events:
            target, gain = _REACTIONS.get(event.kind, ("", 0.0))
            if not target:
                continue
            strength = _clamp(gain * max(event.weight, 0.0))
            if strength <= 0.0:
                continue
            if target == mood:
                # 同类事件叠加，越提越牢
                intensity = _clamp(intensity + strength * ACCUMULATE_FACTOR)
            elif strength >= SWITCH_MIN_INTENSITY:
                mood, intensity = target, strength

        return replace(state, mood=mood, mood_intensity=intensity, updated_at=now)

    # ------------------------------------------------------------------
    # 信号 → 事件
    # ------------------------------------------------------------------

    @staticmethod
    def events_from_text(user_text: str) -> list[AffectEvent]:
        """从用户文本提取事件（关键词兜底，非情绪分类器）。

        Args:
            user_text: 用户消息文本。

        Returns:
            命中的事件列表（可能为空，可能多个）。
        """
        if not user_text:
            return []
        events: list[AffectEvent] = []
        for kind, keywords in _TEXT_EVENT_KEYWORDS:
            if any(keyword in user_text for keyword in keywords):
                events.append(AffectEvent(kind))
        return events

    @staticmethod
    def events_from_emotion(user_emotion: str | None) -> list[AffectEvent]:
        """把 LLM 路由判定的「用户情绪」映射为事件。

        Args:
            user_emotion: 用户情绪标签，可为 None。

        Returns:
            对应的事件列表。
        """
        if not user_emotion:
            return []
        return [AffectEvent(kind) for kind in _EMOTION_TO_EVENTS.get(user_emotion, ())]

    def events_from_turn(
        self, user_text: str, user_emotion: str | None
    ) -> list[AffectEvent]:
        """汇总一轮对话的事件：文本词典 + 路由信号（去重）。

        Args:
            user_text: 用户消息文本。
            user_emotion: 路由判定的用户情绪。

        Returns:
            去重后的事件列表。
        """
        merged: dict[str, AffectEvent] = {}
        for event in self.events_from_text(user_text) + self.events_from_emotion(
            user_emotion
        ):
            merged.setdefault(event.kind, event)
        return list(merged.values())

    def is_known_mood(self, mood: str) -> bool:
        """判断心情是否在档案表内。

        Args:
            mood: 心情名。

        Returns:
            在档案表内返回 True。
        """
        return mood in self._profiles

    def known_moods(self) -> tuple[str, ...]:
        """返回全部已知心情名（供 UI 下拉框使用）。"""
        return tuple(self._profiles.keys())
