"""调试面板 Web API v2。

关键修正：插件名从 context.star_metadata.name 动态获取，
确保与 AstrBot Dashboard 构造的 URL 路径完全一致（大小写敏感匹配）。

本模块只保留组合壳与路由表；各资源的 handler 见 `adapter/api/`：
- `api/sessions.py`：会话与状态相关路由。
- `api/injections.py`：注入记录（日志）相关路由。
- `api/materials.py`：资料浏览与重载相关路由。
- `api/route.py`：路由测试与注入预览相关路由。
- `api/stats.py`：配置、注册表摘要与统计相关路由。
- `api/proactive.py`：主动消息状态与手动触发相关路由。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from .api.injections import InjectionRoutes
from .api.materials import MaterialRoutes
from .api.proactive import ProactiveRoutes
from .api.route import RouteRoutes
from .api.sessions import SessionRoutes
from .api.stats import StatsRoutes

if TYPE_CHECKING:
    from ..core.cognition.state import StateStore
    from ..core.materials.registry import MaterialRegistry
    from .debug_recorder import DebugRecorder


class DebugApi(
    SessionRoutes,
    InjectionRoutes,
    MaterialRoutes,
    RouteRoutes,
    StatsRoutes,
    ProactiveRoutes,
):
    """调试面板 Web API 组合类。

    各资源的 handler 与路由表分别在 `adapter/api/` 的同名模块中，
    本类只负责共享依赖的注入与路由注册的编排。
    """

    def __init__(
        self,
        context,
        registry: MaterialRegistry,
        store: StateStore,
        recorder: DebugRecorder,
        config_getter,
        router,
        ctx_manager,
        builder,
        logger: logging.Logger | None = None,
        proactive=None,
    ) -> None:
        """初始化调试面板 API。

        Args:
            context: AstrBot 插件上下文。
            registry: 资料注册表。
            store: 会话状态仓库。
            recorder: 调试记录器。
            config_getter: 配置获取函数。
            router: 上下文路由器。
            ctx_manager: 激活上下文管理器。
            builder: 注入文本组装器。
            logger: 日志记录器。
            proactive: 主动消息执行器（可为 None）。
        """
        self._ctx = context
        self._registry = registry
        self._store = store
        self._recorder = recorder
        self._config_getter = config_getter
        self._router = router
        self._ctx_manager = ctx_manager
        self._builder = builder
        self._logger = logger
        self._proactive = proactive

        self._plugin_name = "astrbot_plugin_Firefly"
        try:
            meta = getattr(context, "star_metadata", None)
            if meta and getattr(meta, "name", None):
                self._plugin_name = str(meta.name)
        except Exception:
            pass
        self._P = f"/{self._plugin_name}/page"

    def register_routes(self) -> None:
        """向 AstrBot 注册全部调试面板的 Web API 路由。"""
        if not hasattr(self._ctx, "register_web_api"):
            return
        reg = self._ctx.register_web_api
        prefix = self._P
        if self._logger is not None:
            self._logger.info(
                f"[认知外壳] 插件名={self._plugin_name}, API前缀={prefix}"
            )

        self._register_session_routes(reg, prefix)
        self._register_injection_routes(reg, prefix)
        self._register_material_routes(reg, prefix)
        self._register_route_routes(reg, prefix)
        self._register_stats_routes(reg, prefix)
        self._register_proactive_routes(reg, prefix)
