"""调试记录模型。

从 `core.models` 拆出：这些类型是「一次注入 / 一次主动决策」的事后快照，
供调试面板与日志使用，与运行时领域实体（资料、会话状态）的变更理由不同。

注意：面向面板的 DTO 转换不在这里，见 `adapter.dto` —— 面板字段会随 UI 变动，
属于传输层关切，不应反向影响领域模型。

本模块为纯数据：不依赖 astrbot、不访问 IO。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .models import ActiveContext, RouteResult

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

    # 记录来源：llm_request（普通对话） / task_agent_begin（任务唤醒）
    injection_source: str = "llm_request"

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
