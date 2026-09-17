"""调试面板 Web API 的共享请求解析辅助。

原实现中 `DebugApi` 与 `RoleApi` 各自持有一份 `_get_query` / `_get_json`，
两者的空值与异常语义必须一致，分开维护迟早漂移，因此收敛为一个可混入的
`HttpHelpers`。

设计取舍：保留 `self._get_query` / `self._get_json` 的方法形态而非模块级
函数，原因有二：

1. 调用点无需改动，本次重构的风险面最小；
2. 现有测试通过给实例赋同名属性来打桩，而「继承来的方法 + 实例属性遮蔽」
   与原「类内定义 + 实例属性遮蔽」语义完全一致 —— 测试无需改写，覆盖
   不会因此降级（若改为模块级函数，那些打桩会静默失效）。

`quart` 保持惰性导入：导入本模块不应要求 Web 运行时依赖存在。
"""

from __future__ import annotations

from typing import Any


class HttpHelpers:
    """Web API 共用的查询参数与请求体解析。"""

    @staticmethod
    def _get_query(key: str) -> str | None:
        """读取请求查询参数。

        Args:
            key: 参数名。

        Returns:
            参数值；参数不存在时返回 None。
        """
        from quart import request

        return request.args.get(key)

    @staticmethod
    async def _get_json() -> dict[str, Any]:
        """读取请求体 JSON。

        Returns:
            解析后的字典；请求体为空或解析失败时返回空字典。
        """
        from quart import request

        try:
            return await request.get_json() or {}
        except Exception:
            return {}


def ok(data: Any = None) -> dict[str, Any]:
    """构造成功响应体。

    Args:
        data: 响应数据。

    Returns:
        形如 {"status": "ok", "data": ...} 的字典。
    """
    return {"status": "ok", "data": data}


def error(message: str) -> dict[str, Any]:
    """构造错误响应体。

    Args:
        message: 错误信息。

    Returns:
        形如 {"status": "error", "message": ...} 的字典。
    """
    return {"status": "error", "message": str(message)}
