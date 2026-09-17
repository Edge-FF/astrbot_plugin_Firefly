"""调试记录的传输格式转换。

记录对象本身属于领域层（`core.records`）；「如何呈现在面板上」是传输层关切
—— 面板字段会随 UI 变动，不应因此回头修改领域模型。

本模块只做纯转换：不持有状态、不访问 IO、不依赖 astrbot。
"""

from __future__ import annotations

from typing import Any

from ..core.records import InjectionRecord, ProactiveRecord


def injection_to_api_dict(rec: InjectionRecord) -> dict[str, Any]:
    """把注入记录转换为调试面板可直接返回的字典。

    Args:
        rec: 注入记录。

    Returns:
        扁平化后的记录字典（含嵌套 signals）。
    """
    return {
        "record_id": rec.record_id,
        "session_id": rec.session_id,
        "timestamp": rec.timestamp,
        "user_msg": rec.user_msg,
        "route_needed_ids": rec.route_needed_ids,
        "route_signals": {
            "user_emotion": rec.route_signals_emotion,
        },
        "route_source": rec.route_source,
        "mood_before": rec.mood_before,
        "active_context_ids_before": rec.active_context_ids_before,
        "active_context_after": rec.active_context_after,
        "injection_xml": rec.injection_xml,
        "truncated_ids": rec.truncated_ids,
        "over_budget": rec.over_budget,
        "token_estimate": rec.token_estimate,
        "injection_source": rec.injection_source,
        "injected_successfully": rec.injected_successfully,
        "skipped_reason": rec.skipped_reason,
    }


def proactive_to_api_dict(rec: ProactiveRecord) -> dict[str, Any]:
    """把主动决策记录转换为调试面板可直接返回的字典。

    Args:
        rec: 主动决策记录。

    Returns:
        扁平化后的记录字典。
    """
    return {
        "record_id": rec.record_id,
        "session_id": rec.session_id,
        "timestamp": rec.timestamp,
        "allowed": rec.allowed,
        "reason": rec.reason,
        "intent": rec.intent,
        "urge": round(rec.urge, 2),
        "threshold": round(rec.threshold, 2),
        "mood": rec.mood,
        "text": rec.text[:500],
        "sent": rec.sent,
        "error": rec.error[:300],
    }
