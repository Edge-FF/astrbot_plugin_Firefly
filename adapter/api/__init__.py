"""调试面板 Web API 子包。

按资源拆分路由处理器（见 `PLAN_arch_refactor.md` 的 P4）：
本阶段仅提供共享的请求解析辅助 `http`，其余模块在 P4 迁入。

本包的 `__init__.py` 只写 docstring，不做 re-export —— 与 `core` 包同一约定，
避免制造隐式耦合节点。
"""
