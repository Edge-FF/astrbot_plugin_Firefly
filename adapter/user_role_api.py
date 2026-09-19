"""用户角色预设 Web API。

只做 HTTP 参数提取与响应封装，不直接读写文件：
- 设置读写委托 `core.user_role.service.UserRoleService`；
- 候选列表来自 `RoleStore.list_tree()`（文件系统为准）；
- 预览正文来自 `MaterialRegistry.get(id)`（与运行时注入同一来源）。

本层不 import astrbot 顶层模块（quart 在 `HttpHelpers` 内惰性导入）。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from ..core import consts
from ..core.user_role.models import UserRoleSetting, normalize_mode
from .api.http import HttpHelpers, error, ok

if TYPE_CHECKING:
    from ..core.materials.registry import MaterialRegistry
    from ..core.materials.role_store import RoleStore
    from ..core.user_role.models import ResolvedUserRole
    from ..core.user_role.service import UserRoleService

# 现有人物候选目录（内置角色，未 pin 时按需触发）
_EXISTING_DIR = "人物关系"
# 可作为身份候选项的文件状态：registered（正常）/ empty_body（可见但不可选）
_USABLE_STATUS = ("registered", "empty_body")


class UserRoleApi(HttpHelpers):
    """用户角色预设面板的路由（get / set / reset）。"""

    def __init__(
        self,
        context,
        service: UserRoleService,
        registry: MaterialRegistry,
        role_store: RoleStore,
        logger: logging.Logger | None = None,
    ) -> None:
        """初始化。

        Args:
            context: AstrBot 插件上下文。
            service: 身份应用服务（读写与校验）。
            registry: 资料注册表（取身份正文）。
            role_store: role/ 访问层（列候选）。
            logger: 日志记录器。
        """
        self._ctx = context
        self._service = service
        self._registry = registry
        self._role_store = role_store
        self._logger = logger
        self._plugin_name = "astrbot_plugin_Firefly"
        try:
            meta = getattr(context, "star_metadata", None)
            if meta and getattr(meta, "name", None):
                self._plugin_name = str(meta.name)
        except Exception:
            pass
        self._P = f"/{self._plugin_name}/page"

    def register_routes(self) -> None:
        """注册路由（当前 AstrBot 版本不支持 Web API 时静默跳过）。"""
        if not hasattr(self._ctx, "register_web_api"):
            return
        reg = self._ctx.register_web_api
        P = self._P
        reg(f"{P}/user_role/get", self._get, ["GET"], "")
        reg(f"{P}/user_role/set", self._set, ["POST"], "")
        reg(f"{P}/user_role/reset", self._reset, ["POST"], "")
        if self._logger is not None:
            self._logger.info(f"[认知外壳] 用户角色 API 已注册，API前缀={P}")

    # ------------------------------------------------------------------
    # 路由
    # ------------------------------------------------------------------

    async def _get(self, **kw) -> dict:
        """GET：读取默认/会话设置、解析预览与候选列表。

        查询参数: session（可选，会话 UMO；缺省为全局视图）
        """
        try:
            session_id = (self._get_query("session") or "").strip()
            default = self._service.default_setting()
            override = self._service.session_setting(session_id) if session_id else None
            resolved = (
                self._service.resolve(session_id)
                if session_id
                else self._service.resolve_setting(default)
            )
            return ok(
                {
                    "default": default.to_dict(),
                    "session": override.to_dict() if override else None,
                    "resolved": self._resolved_dict(resolved),
                    "candidates": self._candidates(),
                }
            )
        except Exception as e:
            return error(str(e))

    async def _set(self, **kw) -> dict:
        """POST：设置全局默认或会话身份（写前校验，无效即拒绝）。

        请求体: scope("default"|"session"), session?, mode, role_id
        """
        try:
            payload = await self._get_json()
            scope = str(payload.get("scope") or "default").strip().lower()
            if scope not in ("default", "session"):
                return error(f"scope 非法：{scope!r}（应为 default 或 session）")

            mode = normalize_mode(payload.get("mode"))
            role_id = str(payload.get("role_id") or "").strip()
            setting = UserRoleSetting(mode=mode, role_id=role_id)

            # 校验规则集中在 UserRoleService.validate_setting（API 与命令共用）
            resolved, invalid = self._service.validate_setting(setting)
            if invalid:
                return error(invalid)

            if scope == "session":
                session_id = str(payload.get("session") or "").strip()
                if not session_id:
                    return error("scope=session 时 session 不能为空")
                await self._service.set_session(session_id, setting)
            else:
                await self._service.set_default(setting)

            return ok(
                {
                    "scope": scope,
                    "setting": setting.to_dict(),
                    "resolved": self._resolved_dict(resolved),
                }
            )
        except Exception as e:
            return error(str(e))

    async def _reset(self, **kw) -> dict:
        """POST：清除会话覆盖或重置全局默认。

        请求体: scope("default"|"session"), session?
        """
        try:
            payload = await self._get_json()
            scope = str(payload.get("scope") or "default").strip().lower()
            if scope not in ("default", "session"):
                return error(f"scope 非法：{scope!r}（应为 default 或 session）")

            if scope == "session":
                session_id = str(payload.get("session") or "").strip()
                if not session_id:
                    return error("scope=session 时 session 不能为空")
                await self._service.clear_session(session_id)
            else:
                await self._service.set_default(UserRoleSetting())

            return ok(
                {
                    "scope": scope,
                    "default": self._service.default_setting().to_dict(),
                }
            )
        except Exception as e:
            return error(str(e))

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    def _resolved_dict(self, resolved: ResolvedUserRole) -> dict[str, Any]:
        """把解析结果转为前端可用的预览结构。

        Args:
            resolved: `ResolvedUserRole`。

        Returns:
            含 pin_id/origin/title/exists/body/warning 的字典。
        """
        entry = resolved.entry
        return {
            "pin_id": resolved.pin_id,
            "origin": resolved.origin,
            "title": entry.title if entry is not None else "",
            "exists": resolved.has_entry,
            "body": (entry.content or "") if entry is not None else "",
            "warning": resolved.warning,
        }

    def _candidates(self) -> dict[str, list[dict[str, Any]]]:
        """列出可扮演的现有人物与自定义角色候选。

        以文件系统为准（`list_tree`），仅保留可用的注册文件；空正文保留但标记
        `empty=True`（前端据此禁用选择），读失败/隐藏/ID 冲突的条目一律不列。

        Returns:
            `{"existing": [...], "custom": [...]}`。
        """
        tree = self._role_store.list_tree()
        existing: list[dict[str, Any]] = []
        custom: list[dict[str, Any]] = []
        for entry in tree.get("entries", []):
            status = entry.get("status")
            if status not in _USABLE_STATUS:
                continue
            rel_path = str(entry.get("path") or "")
            item = {
                "id": entry.get("id"),
                "title": entry.get("title") or entry.get("name"),
                "path": rel_path,
                "empty": status == "empty_body",
            }
            if rel_path.startswith(f"{_EXISTING_DIR}/"):
                existing.append(item)
            elif rel_path.startswith(f"{consts.USER_ROLE_CUSTOM_DIR}/"):
                custom.append(item)
        return {"existing": existing, "custom": custom}
