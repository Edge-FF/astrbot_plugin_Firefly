"""上下文路由 v0.2：LLM 语义路由 + 关键词兜底。

解决 P1（语义匹配僵化）：
- LLMRouter：微型 LLM 调用，约 150 token 输入，输出 JSON
- KeywordRouter：保留 v0.1 关键词/正则逻辑作为兜底
- 共享 ContextRouter 协议接口
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from collections.abc import Awaitable, Callable
from typing import Any

from .. import consts
from ..materials.registry import MaterialRegistry
from ..models import (
    MaterialEntry,
    RouteResult,
    RouteSignals,
    SessionState,
)

# LLM 路由的系统提示（固定，约 50 token）
_ROUTER_SYSTEM_PROMPT = (
    "你是角色扮演上下文路由器。根据对话片段，从「可用资料」中选出本轮需要的条目ID。"
    "只输出JSON，不要解释。"
    '格式：{"needed_ids": [...], "signals": {"user_emotion": "...|null"}}'
    "（user_emotion 指「用户」当前的情绪，不是角色的情绪；不确定填 null）"
)

# 关键词命中分数
_KEYWORD_HIT_SCORE = 10
_PATTERN_HIT_SCORE = 5


class ContextRouter:
    """路由协议接口（鸭子类型）。

    所有路由实现必须实现 async route() 方法。
    """

    async def route(
        self,
        user_msg: str,
        session_state: SessionState,
        registry: MaterialRegistry,
    ) -> RouteResult:
        """执行路由，返回本轮需要的条目 ID 与信号。

        Args:
            user_msg: 用户消息文本。
            session_state: 当前会话状态。
            registry: 资料注册表。

        Returns:
            路由结果（needed_ids 与 signals）。
        """
        raise NotImplementedError


class LLMRouter:
    """LLM 语义路由：用微型 LLM 调用替代关键词匹配。

    llm_generate 签名: async (system_prompt: str, user_prompt: str) -> str
    """

    def __init__(
        self,
        llm_generate: Callable[[str, str], Awaitable[str]] | None = None,
        timeout: float = 3.0,
        cache_enabled: bool = True,
        logger: Any = None,
    ) -> None:
        """初始化 LLM 路由。

        Args:
            llm_generate: async (system_prompt, user_prompt) -> str 生成函数。
            timeout: LLM 调用超时秒数。
            cache_enabled: 是否启用路由结果缓存。
            logger: 可选的日志记录器；路由失败时用于留痕。
        """
        self._llm_generate = llm_generate
        self._timeout = timeout
        self._cache_enabled = cache_enabled
        self._logger = logger
        self._cache: dict[str, tuple[float, RouteResult]] = {}

    async def route(
        self,
        user_msg: str,
        session_state: SessionState,
        registry: MaterialRegistry,
    ) -> RouteResult | None:
        """执行 LLM 路由，失败时返回 None（调用方降级到 KeywordRouter）。"""
        if self._llm_generate is None:
            return None

        user_input = self._build_prompt(user_msg, session_state, registry)
        cache_key = self._cache_key(user_msg, session_state)
        if self._cache_enabled:
            cached = self._get_cached(cache_key)
            if cached is not None:
                return cached

        try:
            raw = await asyncio.wait_for(
                self._llm_generate(_ROUTER_SYSTEM_PROMPT, user_input),
                timeout=self._timeout,
            )
            result = self._parse_response(raw, registry, self._logger)
            if self._cache_enabled:
                self._cache[cache_key] = (time.time(), result)
            return result
        except Exception as exc:
            # 任何失败（超时/上游异常）都降级到关键词路由，但必须留痕：
            # 否则「为什么这轮没命中资料」无从排查。
            # CancelledError 继承自 BaseException，不会被这里吞掉。
            if self._logger is not None:
                self._logger.debug(f"[认知外壳] LLM 路由失败，本次降级：{exc!r}")
            return None

    def _build_prompt(
        self,
        user_msg: str,
        session_state: SessionState,
        registry: MaterialRegistry,
    ) -> str:
        """构造严格控制在 ~200 token 的路由输入。"""
        summary = registry.index_summary()
        active_ids = session_state.active_context.active_ids
        active_str = "、".join(active_ids) if active_ids else "无"

        # 最近话题
        recent = "；".join(session_state.recent_topics[:2]) or "无最近话题"
        recent = recent[:100]

        # 用户消息截断
        msg = (user_msg or "").strip()[:80]

        return (
            f"可用资料：\n{summary}\n"
            f"当前激活：{active_str}\n"
            f"最近对话：{recent}\n"
            f"用户说：{msg}"
        )

    @staticmethod
    def _parse_response(
        raw: str, registry: MaterialRegistry, logger: Any = None
    ) -> RouteResult:
        """解析 LLM 返回的 JSON，过滤非法 ID。

        Args:
            raw: LLM 原始输出。
            registry: 资料注册表（用于过滤不存在的条目 ID）。
            logger: 可选的日志记录器；解析失败时用于留痕。

        Returns:
            解析出的路由结果；失败时返回空结果（source 仍标记为 llm）。
        """
        try:
            text = raw.strip()
            # 提取 JSON 块
            if "```" in text:
                start = text.find("{")
                end = text.rfind("}")
                if start != -1 and end != -1:
                    text = text[start : end + 1]

            data = json.loads(text)
            needed_ids = data.get("needed_ids", []) or []
            signals_raw = data.get("signals", {}) or {}

            valid_ids: list[str] = []
            all_ids = {e.id for e in registry.all_entries()}
            for rid in needed_ids:
                if rid in all_ids:
                    valid_ids.append(rid)

            signals = RouteSignals(
                user_emotion=signals_raw.get("user_emotion"),
            )
            return RouteResult(needed_ids=valid_ids, signals=signals, source="llm")
        except (json.JSONDecodeError, TypeError, AttributeError) as exc:
            # 必须留痕：否则无法区分「模型判定不需要资料」与「结果解析失败」
            if logger is not None:
                logger.debug(
                    f"[认知外壳] LLM 路由结果解析失败，本轮不激活条目：{exc!r}"
                )
            return RouteResult(source="llm")

    def _cache_key(self, user_msg: str, session_state: SessionState) -> str:
        """生成缓存键：用户消息前 50 字 + 激活 ID 哈希。"""
        msg_hash = hashlib.md5(user_msg[:50].encode()).hexdigest()[:8]
        active_hash = hashlib.md5(
            ",".join(session_state.active_context.active_ids).encode()
        ).hexdigest()[:8]
        return f"{msg_hash}_{active_hash}"

    def _get_cached(self, key: str) -> RouteResult | None:
        """查看缓存，过期（60 秒）后自动清除。"""
        if key not in self._cache:
            return None
        ts, result = self._cache[key]
        if time.time() - ts > 60:
            del self._cache[key]
            return None
        return result


class KeywordRouter:
    """关键词/正则兜底路由（保留 v0.1 核心逻辑）。

    信号字段全部返回 None；用户情绪由 AffectEngine 的文本事件兜底路径处理。
    """

    def __init__(self, max_entries: int = 5) -> None:
        """初始化关键词路由。

        Args:
            max_entries: 单轮最多返回的命中条目数。
        """
        self._max_entries = max_entries

    async def route(
        self,
        user_msg: str,
        session_state: SessionState,
        registry: MaterialRegistry,
    ) -> RouteResult:
        """关键词/正则兜底路由：只匹配 Tier3/4，按分数与优先级排序。"""
        query = (user_msg or "").strip()
        if not query:
            return RouteResult(source="keyword")

        # 只匹配 Tier3/4（Tier1/2 是常驻，不参与路由）
        candidates = [
            e for e in registry.all_entries() if e.tier >= consts.TIER_SKILL_LORE
        ]
        if not candidates:
            return RouteResult(source="keyword")

        scored: list[tuple[int, int, str, MaterialEntry]] = []
        for entry in candidates:
            score = self._score(entry, query)
            if score > 0:
                scored.append((score, entry.priority, entry.title, entry))

        scored.sort(key=lambda item: (-item[0], -item[1], item[2]))
        needed_ids = [entry.id for _, _, _, entry in scored[: self._max_entries]]
        return RouteResult(needed_ids=needed_ids, source="keyword")

    @classmethod
    def _score(cls, entry: MaterialEntry, query: str) -> int:
        """计算条目与查询文本的匹配得分。

        Args:
            entry: 待匹配的资料条目。
            query: 用户消息文本。

        Returns:
            关键词命中 +10，正则命中 +5 的累计得分。
        """
        score = 0
        for keyword in entry.trigger_keywords:
            if keyword and keyword.lower() in query.lower():
                score += _KEYWORD_HIT_SCORE
        for pattern in entry.trigger_patterns:
            try:
                if re.search(pattern, query, flags=re.IGNORECASE):
                    score += _PATTERN_HIT_SCORE
            except re.error:
                continue
        return score


class FallbackRouter:
    """组合路由：优先 LLM 语义路由，失败或未启用时降级关键词路由。

    降级语义：
    - 未提供 LLM 路由器 → 始终走关键词路由；
    - LLM 返回 None（未启用/超时/解析失败）且允许降级 → 走关键词路由；
    - LLM 返回 None 且禁止降级 → 返回空结果（本轮不激活任何条目）。

    本类只负责「组合与降级」这一策略，不关心具体匹配方式，因此可脱离
    AstrBot 与 IO 单测（原实现内联在插件装配文件中，无法单测）。
    """

    def __init__(
        self,
        llm: LLMRouter | None,
        keyword: KeywordRouter,
        fallback_to_keyword: bool = True,
    ) -> None:
        """初始化组合路由。

        Args:
            llm: LLM 语义路由器；为 None 表示未启用。
            keyword: 关键词兜底路由器。
            fallback_to_keyword: LLM 失败时是否降级到关键词路由。
        """
        self._llm = llm
        self._keyword = keyword
        self._fallback_to_keyword = fallback_to_keyword

    async def route(
        self,
        user_msg: str,
        session_state: SessionState,
        registry: MaterialRegistry,
    ) -> RouteResult:
        """执行路由：先 LLM，失败时按配置降级关键词。

        Args:
            user_msg: 用户消息文本。
            session_state: 当前会话状态。
            registry: 资料注册表。

        Returns:
            路由结果；LLM 失败且禁止降级时返回空结果。
        """
        if self._llm is not None:
            result = await self._llm.route(user_msg, session_state, registry)
            if result is not None:
                return result
        if self._llm is None or self._fallback_to_keyword:
            return await self._keyword.route(user_msg, session_state, registry)
        return RouteResult(source="keyword")
