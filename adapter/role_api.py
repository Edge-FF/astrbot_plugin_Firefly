"""role/ 资料管理 Web API（只读 + 写路径）。

只做 HTTP 参数提取、线程池调度与响应封装：路径规则、序列化与文件访问全部委托
`core.role_store.RoleStore`；写/删后由本层触发 `registry.reload()` 并回传告警。

本层不 import astrbot 顶层模块（quart 在方法内懒导入，与 debug_api 一致）。
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..core import consts
from ..core.user_role.models import MODE_CUSTOM, normalize_mode
from .api.http import HttpHelpers, error, ok

if TYPE_CHECKING:
    from ..core.cognition.state import StateStore
    from ..core.materials.registry import MaterialRegistry
    from ..core.materials.role_store import RoleStore
    from ..core.user_role.store import UserRoleStore

# 全局默认身份的占位会话标记（删除守卫列出时使用，非真实会话 id）
_DEFAULT_PIN_MARKER = "（全局默认身份）"


class RoleApi(HttpHelpers):
    """资料管理面板的路由（只读 tree/file + 写路径 save/delete）。

    请求解析辅助由 `HttpHelpers` 提供，与 `DebugApi` 共用同一份实现。
    """

    def __init__(
        self,
        context,
        store: RoleStore,
        registry: MaterialRegistry,
        state_store: StateStore | None = None,
        logger: logging.Logger | None = None,
        user_role_store: UserRoleStore | None = None,
    ) -> None:
        """初始化。

        Args:
            context: AstrBot 插件上下文。
            store: role/ 访问层。
            registry: 资料注册表（写/删后重载，并附加运行时加载告警）。
            state_store: 会话状态仓库（用于"条目正被哪些会话使用"的删除守卫）。
            logger: 日志记录器。
            user_role_store: 身份设置仓库（删除守卫需识别"正被 pin 的身份"）。
        """
        self._ctx = context
        self._store = store
        self._registry = registry
        self._state_store = state_store
        self._logger = logger
        self._user_role_store = user_role_store
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
        reg(f"{P}/role/tree", self._role_tree, ["GET"], "")
        reg(f"{P}/role/file", self._role_file, ["GET"], "")
        reg(f"{P}/role/save", self._role_save, ["POST"], "")
        reg(f"{P}/role/rename", self._role_rename, ["POST"], "")
        reg(f"{P}/role/delete", self._role_delete, ["POST"], "")
        if self._logger is not None:
            self._logger.info(f"[认知外壳] 资料管理 API 已注册，API前缀={P}")

    # ------------------------------------------------------------------
    # 只读
    # ------------------------------------------------------------------

    async def _role_tree(self, **kw) -> dict:
        """GET：role/ 目录树与全部 .md 元数据。

        查询参数: include_hidden（1/true/yes 时包含 `_`/`.` 前缀文件）

        每个条目附带 `active_sessions`（正在激活该条目的会话数），供前端在删除前提示。
        """
        try:
            include_hidden = (
                self._get_query("include_hidden") or ""
            ).strip().lower() in (
                "1",
                "true",
                "yes",
            )
            tree = self._store.list_tree(include_hidden=include_hidden)
            usage = await self._usage_map()
            for entry in tree["entries"]:
                sessions = sorted(usage.get(entry["id"], []))
                entry["active_sessions"] = len(sessions)
                entry["active_session_ids"] = sessions
            tree["warnings"] = list(self._registry.warnings) + list(tree["warnings"])
            return ok(tree)
        except Exception as e:
            return error(str(e))

    async def _role_file(self, **kw) -> dict:
        """GET：读取单个 .md 文件。

        查询参数: path（相对 role/ 的路径）
        """
        try:
            return ok(self._store.read_document(self._get_query("path") or ""))
        except Exception as e:
            return error(str(e))

    # ------------------------------------------------------------------
    # 写路径
    # ------------------------------------------------------------------

    async def _role_save(self, **kw) -> dict:
        """POST：新建或覆盖资料文件。

        请求体: path, create, base_rev, frontmatter, body, raw
        """
        try:
            payload = await self._get_json()
            result = await asyncio.to_thread(
                self._store.save_document,
                str(payload.get("path") or ""),
                create=bool(payload.get("create")),
                base_rev=str(payload.get("base_rev") or ""),
                frontmatter=payload.get("frontmatter") or None,
                body=payload.get("body"),
                raw=payload.get("raw"),
            )
            reload_info = await asyncio.to_thread(self._reload_registry)
            self._audit("保存", result["path"], reload_info)
            return ok({**result, "reload": reload_info})
        except Exception as e:
            return error(str(e))

    async def _role_rename(self, **kw) -> dict:
        """POST：重命名 / 移动资料文件（内容不变，仅改路径）。

        请求体: path, base_rev, target
        """
        try:
            payload = await self._get_json()
            result = await asyncio.to_thread(
                self._store.rename_document,
                str(payload.get("path") or ""),
                base_rev=str(payload.get("base_rev") or ""),
                target=str(payload.get("target") or ""),
            )
            reload_info = await asyncio.to_thread(self._reload_registry)
            self._audit("重命名", result["path"], reload_info)
            return ok({**result, "reload": reload_info})
        except Exception as e:
            return error(str(e))

    async def _role_delete(self, **kw) -> dict:
        """POST：硬删除资料文件（不可恢复）。

        三重守卫：`base_rev` 匹配、`confirm_name` 逐字符相同、活跃会话二次确认。

        请求体: path, base_rev, confirm_name, confirm_active
        """
        try:
            payload = await self._get_json()
            path = str(payload.get("path") or "")

            session_ids = await self._active_session_ids(path)
            if session_ids and not payload.get("confirm_active"):
                listed = "、".join(session_ids[:5])
                return error(
                    f"该条目正被 {len(session_ids)} 个会话使用（{listed}），"
                    "需要再次确认后才能删除"
                )

            result = await asyncio.to_thread(
                self._store.delete_document,
                path,
                base_rev=str(payload.get("base_rev") or ""),
                confirm_name=str(payload.get("confirm_name") or ""),
            )
            reload_info = await asyncio.to_thread(self._reload_registry)
            self._audit("删除", result["path"], reload_info)
            return ok({**result, "deleted": True, "reload": reload_info})
        except Exception as e:
            return error(str(e))

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    async def _usage_map(self) -> dict[str, list[str]]:
        """条目 id → 正在使用它的会话 id 列表（激活上下文 + pin 身份）。

        删除守卫必须同时覆盖两类"正在使用"：active_context 中的激活条目，以及
        被用户身份预设 pin 的条目。否则删除他人身份文档时不会得到提示。
        全局默认身份不属于任何具体会话，以 `_DEFAULT_PIN_MARKER` 占位列出。
        """
        usage: dict[str, list[str]] = {}

        if self._state_store is not None:
            try:
                states = await self._state_store.all()
            except Exception:
                states = {}
            for session_id, state in states.items():
                for entry in state.active_context.entries:
                    usage.setdefault(entry.entry_id, []).append(session_id)

        if self._user_role_store is not None:
            try:
                for session_id, setting in self._user_role_store.all_sessions().items():
                    if setting.role_id:
                        usage.setdefault(setting.role_id, []).append(session_id)
                default_pin = self._default_pin_id()
                if default_pin:
                    usage.setdefault(default_pin, []).append(_DEFAULT_PIN_MARKER)
            except Exception:
                # 守卫是"锦上添花"，身份仓库异常不应阻断资料树读取
                pass

        return usage

    def _default_pin_id(self) -> str:
        """返回全局默认身份实际 pin 的条目 id。

        `existing` 模式下 role_id 为空表示"内置默认身份"，需映射到
        `consts.DEFAULT_USER_ROLE_ID`；`custom` 模式直接取 role_id。

        Returns:
            条目 id；无默认身份或读取失败时返回空串。
        """
        if self._user_role_store is None:
            return ""
        try:
            setting = self._user_role_store.default_setting()
        except Exception:
            return ""
        if normalize_mode(setting.mode) == MODE_CUSTOM:
            return setting.role_id
        return setting.role_id or consts.DEFAULT_USER_ROLE_ID

    async def _active_session_ids(self, path: str) -> list[str]:
        """查询某文件对应条目正被哪些会话使用（删除前的第三重守卫）。"""
        if self._state_store is None or not path:
            return []
        try:
            doc = await asyncio.to_thread(self._store.read_document, path)
        except Exception:
            return []
        entry_id = str(
            doc["frontmatter"].get("id") or Path(path).name.removesuffix(".md")
        )
        usage = await self._usage_map()
        return sorted(usage.get(entry_id, []))

    def _reload_registry(self) -> dict[str, Any]:
        """重载 registry 并汇总结果（失败时 registry 内部保留旧索引）。"""
        report = self._registry.reload()
        tier_counts: dict[str, int] = {}
        for entry in self._registry.all_entries():
            tier_counts[str(entry.tier)] = tier_counts.get(str(entry.tier), 0) + 1
        return {
            "total": self._registry.entry_count,
            "tier_counts": tier_counts,
            "warnings": list(report.warnings),
        }

    def _audit(self, action: str, path: str, reload_info: dict[str, Any]) -> None:
        """记录写操作审计日志（操作者由 Dashboard 会话鉴权保证，此处仅记录动作与路径）。"""
        if self._logger is None:
            return
        self._logger.info(
            f"[认知外壳] 资料{action}：{path}（重载后 {reload_info['total']} 条，"
            f"告警 {len(reload_info['warnings'])} 条）"
        )
