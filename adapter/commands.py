"""/firefly 管理命令 v0.3（Mixin，装配在插件主类上）。"""

from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Any

from astrbot.api.event import AstrMessageEvent, MessageEventResult, filter
from astrbot.api.event.filter import PermissionType, permission_type

from ..core.user_role.models import (
    MODE_CUSTOM,
    MODE_EXISTING,
    UserRoleSetting,
    normalize_mode,
)

_ROLE_USAGE = (
    "用法：/firefly role [status] | set existing [id] | set custom <id> | clear\n"
    "  status        查看全局默认与本会话身份（默认动作）\n"
    "  set existing  会话身份设为某个现有人物（省略 id 表示内置默认开拓者）\n"
    "  set custom    会话身份设为某个自定义角色文档（id 必填）\n"
    "  clear         清除本会话覆盖，回落到全局默认"
)


def _role_label(setting: UserRoleSetting, resolved: Any = None) -> str:
    """把身份设置渲染为可读文本。

    Args:
        setting: 身份设置。
        resolved: 该设置的解析结果，可为 None（仅展示原始 id）。

    Returns:
        形如「自定义角色 · 我的角色（my_role）」的文本。
    """
    mode = "自定义角色" if normalize_mode(setting.mode) == MODE_CUSTOM else "现有角色"
    if resolved is not None and resolved.has_entry:
        return f"{mode} · {resolved.entry.title}（{resolved.pin_id}）"
    if normalize_mode(setting.mode) == MODE_EXISTING and not setting.role_id:
        return "现有角色 · 内置默认（当前不可用）"
    return f"{mode} · {setting.role_id or '未指定'}（不可用）"


class FireflyCommandMixin:
    """/firefly 管理命令组。"""

    _core: Any = None  # FireflyCore 容器
    logger: logging.Logger  # 由主类 star.Star 在运行时注入

    @filter.command_group("firefly")
    def firefly(self) -> None:
        """流萤认知外壳管理命令组。"""

    @permission_type(PermissionType.ADMIN)
    @firefly.command("status")
    async def firefly_status(self, event: AstrMessageEvent) -> None:
        """[Admin] 查看认知外壳运行状态"""
        core = self._core
        cfg = core.config_getter()

        # 新注册表信息
        if core.registry.is_loaded:
            tier_counts: dict[int, int] = {}
            for entry in core.registry.all_entries():
                tier_counts[entry.tier] = tier_counts.get(entry.tier, 0) + 1
            tier_lines = [f"  Tier {t}：{c} 条" for t, c in sorted(tier_counts.items())]
            body = "\n".join(
                [
                    f"资料条目总数：{core.registry.entry_count}",
                    *tier_lines,
                ]
            )
        else:
            body = "资料尚未加载"

        warnings = core.registry.warnings
        warn_lines = "\n".join(f"  - {w}" for w in warnings) or "  （无）"
        sessions = await core.store.all()

        router_mode = "LLM + 关键词" if cfg.router_use_llm else "纯关键词"
        text = (
            "【流萤认知外壳 v0.2 · 状态】\n"
            f"注入总开关：{'开' if cfg.enabled else '关'}\n"
            f"路由模式：{router_mode}\n"
            f"Token 预算：{cfg.max_tokens}（常驻预留 {cfg.tier1_reserved}）\n"
            f"资料加载：\n{body}\n"
            f"最近加载告警：\n{warn_lines}\n"
            f"动态状态会话数：{len(sessions)}（/firefly state 查看详情）"
        )
        event.set_result(MessageEventResult().message(text))

    @permission_type(PermissionType.ADMIN)
    @firefly.command("reload")
    async def firefly_reload(self, event: AstrMessageEvent) -> None:
        """[Admin] 热重载 role/ 资料"""
        core = self._core
        report = core.registry.reload()
        lines = [
            "【认知外壳 v0.2 · 资料重载】",
            "完成。",
            report.summary(),
        ]
        if report.warnings:
            lines.append("告警：")
            lines.extend(f"  - {w}" for w in report.warnings)
        for w in report.warnings:
            self.logger.warning(f"[认知外壳] 重载告警：{w}")
        event.set_result(MessageEventResult().message("\n".join(lines)))

    @permission_type(PermissionType.ADMIN)
    @firefly.command("state")
    async def firefly_state(
        self, event: AstrMessageEvent, session_id: str | None = None
    ) -> None:
        """[Admin] 查看某会话的动态状态（缺省为当前会话）"""
        core = self._core
        target = (session_id or "").strip() or (event.unified_msg_origin or "")
        state = await core.store.get(target)
        updated = (
            datetime.fromtimestamp(state.updated_at).strftime("%Y-%m-%d %H:%M:%S")
            if state.updated_at
            else "从未更新"
        )

        active_ids = state.active_context.active_ids
        active_str = "、".join(active_ids) if active_ids else "无"
        turn = state.active_context.turn_count

        text = (
            f"【认知外壳 v0.2 · 动态状态】会话：{target or '（空）'}\n"
            f"心情：{state.mood}\n"
            f"最近话题：{'；'.join(state.recent_topics) or '暂无'}\n"
            f"激活条目（{turn} 轮）：{active_str}\n"
            f"最近互动时间：{updated}"
        )
        event.set_result(MessageEventResult().message(text))

    @permission_type(PermissionType.ADMIN)
    @firefly.command("reset")
    async def firefly_reset(self, event: AstrMessageEvent) -> None:
        """[Admin] 重置当前会话的动态状态"""
        core = self._core
        target = event.unified_msg_origin or ""
        await core.store.reset(target)
        event.set_result(
            MessageEventResult().message(
                f"【认知外壳 v0.2】已重置会话 {target or '（空）'} 的动态状态。"
            )
        )

    @permission_type(PermissionType.ADMIN)
    @firefly.command("proactive")
    async def firefly_proactive(
        self, event: AstrMessageEvent, action: str = "status", target: str = ""
    ) -> None:
        """[Admin] 主动消息：status | now [会话] | on | off"""
        core = self._core
        act = (action or "status").strip().lower()
        runner = core.proactive

        if act == "on":
            self._set_proactive_enabled(True)
            await runner.start()
            event.set_result(
                MessageEventResult().message(
                    "【主动消息】已启用（运行时生效；如需持久化请到插件配置中开启）。"
                )
            )
            return

        if act == "off":
            self._set_proactive_enabled(False)
            await runner.stop()
            event.set_result(
                MessageEventResult().message("【主动消息】已停用（运行时生效）。")
            )
            return

        if act == "now":
            sid = (target or "").strip() or (event.unified_msg_origin or "")
            if not sid:
                event.set_result(
                    MessageEventResult().message("【主动消息】无法确定会话。")
                )
                return
            sent, message = await runner.trigger_now(sid)
            event.set_result(
                MessageEventResult().message(
                    f"【主动消息】{'已发送' if sent else '未发送'}：{message}"
                )
            )
            return

        # 默认：status
        cfg = core.proactive_config_getter()
        if not cfg.enabled:
            event.set_result(
                MessageEventResult().message(
                    "【主动消息 · 状态】总开关：关\n"
                    "在插件配置的 proactive.enabled 中开启，或用 /firefly proactive on 临时启用。"
                )
            )
            return

        now = time.time()
        sessions = await core.store.all()
        lines: list[str] = []
        for sid, state in list(sessions.items())[:10]:
            info = runner.evaluate(state, now)
            lines.append(
                f"  - {sid[:32]}｜心情={info['mood']}｜冲动={info['urge']}/{info['threshold']}"
                f"｜意图={info['intent']}｜{'可发' if info['allowed'] else info['reason']}"
            )
        body = "\n".join(lines) or "  （暂无会话）"
        event.set_result(
            MessageEventResult().message(
                "【主动消息 · 状态】\n"
                f"总开关：{'开' if cfg.enabled else '关'}"
                f"｜运行中：{'是' if runner.is_running else '否'}\n"
                f"评估间隔：{cfg.tick_interval_seconds:.0f}s"
                f"｜未回复上限：{cfg.max_unanswered}"
                f"｜每日上限：{cfg.max_per_day}"
                f"｜免打扰：{cfg.quiet_hours}\n"
                f"各会话（冲动/阈值）：\n{body}"
            )
        )

    @permission_type(PermissionType.ADMIN)
    @firefly.command("role")
    async def firefly_role(
        self,
        event: AstrMessageEvent,
        action: str = "status",
        mode: str = "",
        role_id: str = "",
    ) -> None:
        """[Admin] 用户角色身份：status | set existing|custom [id] | clear"""
        core = self._core
        service = core.user_role_service
        target = event.unified_msg_origin or ""
        act = (action or "status").strip().lower()

        if act in ("", "status"):
            default = service.default_setting()
            override = service.session_setting(target)
            resolved = service.resolve(target)
            lines = [
                "【用户角色 · 状态】",
                f"全局默认：{_role_label(default, service.resolve_setting(default))}",
                "本会话："
                + (
                    "未覆盖（跟随默认）"
                    if override is None
                    else _role_label(override, service.resolve_setting(override))
                ),
                "实际注入："
                + (
                    f"{resolved.entry.title}（{resolved.pin_id}）"
                    if resolved.has_entry
                    else "无有效身份"
                ),
            ]
            if resolved.warning:
                lines.append(f"告警：{resolved.warning}")
            event.set_result(MessageEventResult().message("\n".join(lines)))
            return

        if act == "clear":
            await service.clear_session(target)
            event.set_result(
                MessageEventResult().message(
                    "【用户角色】已清除本会话的身份覆盖，回落到全局默认。"
                )
            )
            return

        if act == "set":
            requested_mode = (mode or "").strip().lower()
            if requested_mode not in (MODE_EXISTING, MODE_CUSTOM):
                event.set_result(MessageEventResult().message(_ROLE_USAGE))
                return
            rid = (role_id or "").strip()
            if requested_mode == MODE_CUSTOM and not rid:
                event.set_result(
                    MessageEventResult().message(
                        "【用户角色】自定义模式必须提供 role_id。\n" + _ROLE_USAGE
                    )
                )
                return
            setting = UserRoleSetting(mode=requested_mode, role_id=rid)
            resolved, invalid = service.validate_setting(setting)
            if invalid:
                event.set_result(
                    MessageEventResult().message(f"【用户角色】设置失败：{invalid}")
                )
                return
            await service.set_session(target, setting)
            event.set_result(
                MessageEventResult().message(
                    f"【用户角色】本会话身份已设为：{_role_label(setting, resolved)}"
                )
            )
            return

        event.set_result(MessageEventResult().message(_ROLE_USAGE))

    def _set_proactive_enabled(self, enabled: bool) -> None:
        """在运行时切换主动消息开关（不写入配置文件）。

        Args:
            enabled: 是否启用。
        """
        config_dict = getattr(self, "_config_dict", None)
        if not isinstance(config_dict, dict):
            return
        section = config_dict.setdefault("proactive", {})
        if isinstance(section, dict):
            section["enabled"] = enabled
