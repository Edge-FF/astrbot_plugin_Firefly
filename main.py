"""流萤认知外壳插件入口。

职责：装配 core 与 adapter 组件、管理生命周期、转发事件钩子。
本文件保持"薄"，业务逻辑都在 core/ 与 adapter/ 中。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from astrbot.api import star
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.provider import LLMResponse, ProviderRequest

from .adapter.astrbot_compat import (
    PROACTIVE_SEND_API_AVAILABLE,
    MessageChain,
    missing_api_summary,
)
from .adapter.commands import FireflyCommandMixin
from .adapter.debug_recorder import DebugRecorder
from .adapter.injector import CognitiveShellInjector
from .adapter.proactive_runner import ProactiveRunner
from .core.cognition.affect import AffectEngine
from .core.cognition.context_manager import ActiveContextManager
from .core.cognition.state import StateStore
from .core.config import ProactiveConfig, ShellConfig
from .core.materials.registry import MaterialRegistry
from .core.proactive.policy import ProactivePolicy
from .core.routing.router import ContextRouter, FallbackRouter, KeywordRouter, LLMRouter
from .core.shell.assembly import ShellAssembly
from .core.shell.builder import ShellBuilder


@dataclass
class FireflyCore:
    """插件核心组件容器 v0.3。"""

    registry: MaterialRegistry
    store: StateStore
    affect: AffectEngine
    router: ContextRouter
    ctx_manager: ActiveContextManager
    builder: ShellBuilder
    assembly: ShellAssembly
    policy: ProactivePolicy
    config_getter: Callable[[], ShellConfig]
    proactive_config_getter: Callable[[], ProactiveConfig]
    proactive: ProactiveRunner
    recorder: DebugRecorder | None = None
    llm_generate: Callable | None = None


@star.register(
    "astrbot_plugin_Firefly",
    "项目维护者",
    "流萤认知外壳 v0.3：语义感知路由 + 激活惯性 + 事件驱动情绪 + 情绪驱动的主动消息。",
    "0.3.0",
)
class FireflyPlugin(FireflyCommandMixin, star.Star):
    """流萤认知外壳插件主类。"""

    def __init__(self, context: star.Context, config: dict) -> None:
        """初始化插件：装配核心组件并构建注入器与主动消息执行器。

        Args:
            context: AstrBot 插件上下文（Star 上下文）。
            config: 插件配置字典（来自 _conf_schema.json）。
        """
        super().__init__(context)
        self._config_dict: dict[str, Any] = config or {}
        # 配置解析告警的去重记录：按来源名保存上次已记录的告警
        self._config_warnings_logged: dict[str, tuple[str, ...]] = {}

        plugin_dir = Path(__file__).resolve().parent
        data_dir = star.StarTools.get_data_dir("astrbot_plugin_Firefly")
        self._role_dir = plugin_dir / "role"

        config_getter: Callable[[], ShellConfig] = self._build_config_getter()
        proactive_config_getter: Callable[[], ProactiveConfig] = (
            self._build_proactive_config_getter()
        )
        cfg = config_getter()

        registry = MaterialRegistry(
            self._role_dir,
            cache_size=cfg.content_cache_max_entries,
        )
        store = StateStore(
            data_dir / "cognitive_state.json",
            persist=cfg.persist_state,
            logger=self.logger,
        )

        affect = AffectEngine()

        llm_router: LLMRouter | None = None
        llm_generate_fn = None
        if cfg.router_use_llm:
            llm_generate_fn = self._make_llm_generate(context)
            llm_router = LLMRouter(
                llm_generate=llm_generate_fn,
                timeout=cfg.router_llm_timeout,
                cache_enabled=cfg.router_cache_enabled,
                logger=self.logger,
            )

        keyword_router = KeywordRouter(max_entries=cfg.max_on_demand)
        router: ContextRouter = FallbackRouter(
            llm=llm_router,
            keyword=keyword_router,
            fallback_to_keyword=cfg.router_fallback_to_keyword,
        )  # type: ignore[assignment]

        ctx_manager = ActiveContextManager(
            strength_decay=cfg.strength_decay_per_turn,
            min_strength=cfg.min_strength,
        )
        builder = ShellBuilder(max_tokens=cfg.max_tokens)
        assembly = ShellAssembly(registry, ctx_manager, builder)

        # 调试记录器
        recorder = DebugRecorder(
            max_in_memory=500,
            persist=False,
            log_path=data_dir / "injection_log.jsonl" if cfg.persist_state else None,
        )

        # 主动消息：决策（纯逻辑）+ 执行（IO）
        # 传入 getter 而非快照，使运行时开关切换（/firefly proactive on/off）能实时生效
        policy = ProactivePolicy(affect, proactive_config_getter)
        proactive = ProactiveRunner(
            store=store,
            assembly=assembly,
            affect=affect,
            policy=policy,
            shell_config_getter=config_getter,
            llm_generate=self._make_llm_generate(context),
            send_message=self._send_proactive_message,
            logger=self.logger,
            recorder=recorder,
            registry_ready=lambda: registry.is_loaded,
        )

        self._core = FireflyCore(
            registry=registry,
            store=store,
            affect=affect,
            router=router,
            ctx_manager=ctx_manager,
            builder=builder,
            assembly=assembly,
            policy=policy,
            config_getter=config_getter,
            proactive_config_getter=proactive_config_getter,
            proactive=proactive,
            recorder=recorder,
            llm_generate=llm_generate_fn,
        )

        self._injector = CognitiveShellInjector(
            registry=registry,
            store=store,
            affect=affect,
            router=router,
            context_manager=ctx_manager,
            assembly=assembly,
            config_getter=config_getter,
            logger=self.logger,
            debug_recorder=recorder,
        )

    async def initialize(self) -> None:
        """插件激活时调用：加载资料与动态状态，启动主动消息，并注册调试 API。"""
        missing = missing_api_summary()
        if missing:
            self.logger.warning(
                f"[认知外壳] 检测到 AstrBot 内部 API 缺失，相关功能将自动降级：{missing}"
            )

        report = self._core.registry.load()
        for warning in report.warnings:
            self.logger.warning(f"[认知外壳] 资料加载告警：{warning}")

        for warning in await self._core.store.load():
            self.logger.warning(f"[认知外壳] 动态状态告警：{warning}")

        tier_counts: dict[int, int] = {}
        for entry in self._core.registry.all_entries():
            tier_counts[entry.tier] = tier_counts.get(entry.tier, 0) + 1
        summary_parts = [f"T{t}={c}" for t, c in sorted(tier_counts.items())]
        self.logger.info(
            f"[认知外壳] 已就绪：{self._core.registry.entry_count} 条资料"
            f"（{', '.join(summary_parts)}）"
        )

        # 主动消息后台循环（仅在配置开启时启动）
        if self._core.proactive_config_getter().enabled:
            await self._core.proactive.start()
            self.logger.info("[主动消息] 后台评估循环已启动。")

        # 注册调试面板 API
        self._register_debug_api()
        # 注册资料管理 API（只读 tree/file + 写路径 save/delete）
        self._register_role_api()

    async def terminate(self) -> None:
        """插件停用时调用：停止主动消息并落盘保存动态状态。"""
        await self._core.proactive.stop()
        await self._core.store.close()
        self.logger.info("[认知外壳] 已停止，动态状态已保存。")

    @filter.on_llm_request()
    async def handle_llm_request(
        self, event: AstrMessageEvent, req: ProviderRequest
    ) -> None:
        """LLM 请求钩子：转发给注入器执行注入。"""
        await self._injector.on_llm_request(event, req)

    @filter.on_llm_response()
    async def handle_llm_response(
        self, event: AstrMessageEvent, resp: LLMResponse
    ) -> None:
        """LLM 响应钩子：转发给注入器更新动态状态。"""
        await self._injector.on_llm_response(event, resp)

    @filter.on_agent_begin()
    async def handle_agent_begin(
        self, event: AstrMessageEvent, run_context: Any
    ) -> None:
        """Agent 开始钩子：转发给注入器处理任务路径的外壳注入。

        任务（cron 唤醒）路径不经过消息流水线，因此不会触发 on_llm_request；
        本钩子在两条路径都会触发，由注入器自行判定是否属于任务路径。

        Args:
            event: 消息事件。
            run_context: agent 运行上下文（含最终消息数组）。
        """
        await self._injector.on_agent_begin(event, run_context)

    def _log_config_warnings(self, source: str, warnings: list[str]) -> None:
        """记录配置解析告警（同一来源的同一批告警只报一次）。

        getter 每轮注入都会被调用，若每次都记录会刷屏；配置被改动后告警内容
        随之变化，会再次记录，因此不会漏掉后续的配置问题。

        Args:
            source: 配置来源标识（shell / proactive）。
            warnings: 本次解析产生的告警。
        """
        current = tuple(warnings)
        if self._config_warnings_logged.get(source) == current:
            return
        self._config_warnings_logged[source] = current
        for message in warnings:
            self.logger.warning(f"[认知外壳] 插件配置项有问题（{source}）：{message}")

    def _build_config_getter(self) -> Callable[[], ShellConfig]:
        """构造外壳配置获取函数（每次调用返回最新配置）。

        Returns:
            读取当前配置字典并转换为 ShellConfig 的可调用对象。
        """

        def getter() -> ShellConfig:
            warnings: list[str] = []
            cfg = ShellConfig.from_dict(self._config_dict, warnings)
            self._log_config_warnings("shell", warnings)
            return cfg

        return getter

    def _build_proactive_config_getter(self) -> Callable[[], ProactiveConfig]:
        """构造主动消息配置获取函数（每次调用返回最新配置）。

        Returns:
            读取当前配置字典并转换为 ProactiveConfig 的可调用对象。
        """

        def getter() -> ProactiveConfig:
            warnings: list[str] = []
            cfg = ProactiveConfig.from_dict(self._config_dict, warnings)
            self._log_config_warnings("proactive", warnings)
            return cfg

        return getter

    def _make_llm_generate(self, context: star.Context):
        """构造 LLM 文本生成函数（供路由与主动消息复用）。

        Args:
            context: AstrBot 插件上下文，用于获取当前 LLM Provider。

        Returns:
            async (system_prompt, user_prompt) -> str 的生成函数；失败返回空串。
        """

        async def generate(system_prompt: str, user_prompt: str) -> str:
            """调用当前 Provider 生成文本，失败时静默返回空串。"""
            try:
                provider = context.get_using_provider()
                if provider is None:
                    raise RuntimeError("无可用的 LLM Provider")
                resp = await provider.text_chat(
                    prompt=user_prompt,
                    system_prompt=system_prompt,
                )
                return resp.completion_text or ""
            except Exception:
                return ""

        return generate

    async def _send_proactive_message(self, session_id: str, text: str) -> None:
        """把主动消息发送到指定会话。

        Args:
            session_id: 会话唯一标识（UMO）。
            text: 消息文本。

        Raises:
            RuntimeError: 当前 AstrBot 版本缺少消息链构造能力。
        """
        if not PROACTIVE_SEND_API_AVAILABLE:
            raise RuntimeError("当前 AstrBot 版本缺少 MessageChain，主动消息无法发送")
        await self.context.send_message(session_id, MessageChain().message(text))

    def _register_debug_api(self) -> None:
        """注册调试面板的 Web API 路由（当前 AstrBot 版本不支持时跳过）。"""
        if not hasattr(self.context, "register_web_api"):
            self.logger.info(
                "[认知外壳] 当前 AstrBot 版本不支持 Web API，调试面板不可用"
            )
            return

        from .adapter.debug_api import DebugApi

        debug_api = DebugApi(
            context=self.context,
            registry=self._core.registry,
            store=self._core.store,
            recorder=self._core.recorder,
            config_getter=self._core.config_getter,
            router=self._core.router,
            ctx_manager=self._core.ctx_manager,
            builder=self._core.builder,
            logger=self.logger,
            proactive=self._core.proactive,
        )
        debug_api.register_routes()
        self.logger.info(
            f"[认知外壳] 调试面板 API 已注册，路由数={len(self.context.registered_web_apis)}"
        )

    def _register_role_api(self) -> None:
        """注册资料管理 API（当前 AstrBot 版本不支持时跳过）。"""
        if not hasattr(self.context, "register_web_api"):
            return

        from .adapter.role_api import RoleApi
        from .core.materials.role_store import RoleStore

        role_api = RoleApi(
            context=self.context,
            store=RoleStore(self._role_dir),
            registry=self._core.registry,
            state_store=self._core.store,
            logger=self.logger,
        )
        role_api.register_routes()
        self.logger.info(
            f"[认知外壳] 资料管理 API 已注册（含写路径），路由数={len(self.context.registered_web_apis)}"
        )
