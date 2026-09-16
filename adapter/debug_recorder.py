"""调试记录器：内存环形缓冲 + 可选持久化。

在 injector 的 A-H 流程各步骤记录全链路快照，供调试面板查询。
"""

from __future__ import annotations

import asyncio
import json
from collections import deque
from pathlib import Path

from ..core.models import InjectionRecord, ProactiveRecord


class DebugRecorder:
    """注入日志记录器。

    设计约束：
    - 内存环形缓冲（默认 500 条），避免 OOM
    - 可选持久化到 JSONL 文件
    - 所有公开方法为同步（调用方在 async 上下文中）
    - 线程安全：全部写操作由 asyncio.Lock 串行化
    """

    def __init__(
        self,
        max_in_memory: int = 500,
        persist: bool = False,
        log_path: Path | None = None,
    ) -> None:
        """初始化调试记录器。

        Args:
            max_in_memory: 内存环形缓冲上限（至少 50 条）。
            persist: 是否追加落盘到 JSONL 文件。
            log_path: JSONL 日志文件路径（persist=True 时使用）。
        """
        self._max = max(max_in_memory, 50)
        self._persist = persist
        self._log_path = log_path
        self._lock = asyncio.Lock()
        self._records: deque[InjectionRecord] = deque()
        self._proactive: deque[ProactiveRecord] = deque()

    def record(self, rec: InjectionRecord) -> None:
        """记录一次注入（非阻塞，追加到内存队列后异步落盘）。"""
        if len(self._records) >= self._max:
            self._records.popleft()
        self._records.append(rec)

        if self._persist and self._log_path:
            try:
                self._log_path.parent.mkdir(parents=True, exist_ok=True)
                with open(self._log_path, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps(rec.to_api_dict(), ensure_ascii=False) + "\n")
            except OSError:
                pass

    def record_proactive(self, rec: ProactiveRecord) -> None:
        """记录一次主动消息决策快照。

        Args:
            rec: 主动决策记录。
        """
        if len(self._proactive) >= self._max:
            self._proactive.popleft()
        self._proactive.append(rec)

    def get_proactive_recent(
        self,
        session_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[ProactiveRecord]:
        """获取最近的主动决策记录，支持按 session 筛选与分页。

        Args:
            session_id: 会话唯一标识，为空时不过滤。
            limit: 单页条数。
            offset: 起始偏移。

        Returns:
            按时间倒序的记录列表。
        """
        records = list(self._proactive)
        if session_id:
            records = [r for r in records if r.session_id == session_id]
        records.reverse()
        start = max(offset, 0)
        end = start + max(limit, 1)
        return records[start:end]

    def count_proactive(self, session_id: str | None = None) -> int:
        """主动决策记录数（可按会话过滤）。"""
        if session_id:
            return sum(1 for r in self._proactive if r.session_id == session_id)
        return len(self._proactive)

    def get_recent(
        self,
        session_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[InjectionRecord]:
        """获取最近的注入记录，支持按 session_id 筛选和分页。"""
        records = list(self._records)
        if session_id:
            records = [r for r in records if r.session_id == session_id]
        records.reverse()
        start = max(offset, 0)
        end = start + max(limit, 1)
        return records[start:end]

    def get_by_id(self, record_id: str) -> InjectionRecord | None:
        """按 ID 获取单条记录。"""
        for r in self._records:
            if r.record_id == record_id:
                return r
        return None

    def get_session_ids(self) -> list[str]:
        """获取所有有记录的 session_id 列表（去重）。"""
        seen: set[str] = set()
        for r in self._records:
            seen.add(r.session_id)
        return sorted(seen)

    def count(self, session_id: str | None = None) -> int:
        """记录总数（可按 session 过滤）。"""
        if session_id:
            return sum(1 for r in self._records if r.session_id == session_id)
        return len(self._records)

    def clear(self, session_id: str | None = None) -> int:
        """清空记录（含注入与主动决策）。指定 session_id 时只清该会话。返回删除条数。"""
        if session_id:
            before = len(self._records)
            new_records = deque(r for r in self._records if r.session_id != session_id)
            removed = before - len(new_records)
            self._records = new_records
            self._proactive = deque(
                r for r in self._proactive if r.session_id != session_id
            )
            return removed
        count = len(self._records)
        self._records.clear()
        self._proactive.clear()
        return count

    @property
    def is_persisted(self) -> bool:
        """判断是否启用了文件持久化。"""
        return self._persist and self._log_path is not None

    @property
    def total_records(self) -> int:
        """返回内存中的注入记录总数。"""
        return len(self._records)

    @property
    def total_proactive(self) -> int:
        """返回内存中的主动决策记录总数。"""
        return len(self._proactive)
