"""core.user_role（用户角色预设）的单元测试。

覆盖开发计划 §4.1/§4.2 的 P0 项：
- Store：默认初始化、覆盖优先级、落盘 round-trip、损坏回退、并发、原子性；
- Resolver / Service：回退链、空正文、未知 id、非法 mode、懒加载判空时序；
- Registry：`index_summary` 排除 `user_role`，`get`/`all_entries` 仍包含。
"""

from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest import IsolatedAsyncioTestCase

from astrbot_plugin_Firefly.core import consts
from astrbot_plugin_Firefly.core.materials.registry import MaterialRegistry
from astrbot_plugin_Firefly.core.materials.tier_rules import infer_tier_kind
from astrbot_plugin_Firefly.core.user_role.models import (
    MODE_CUSTOM,
    MODE_EXISTING,
    ORIGIN_CUSTOM,
    ORIGIN_EXISTING,
    ORIGIN_FALLBACK,
    ORIGIN_NONE,
    UserRoleSetting,
)
from astrbot_plugin_Firefly.core.user_role.resolver import UserRoleResolver
from astrbot_plugin_Firefly.core.user_role.service import UserRoleService
from astrbot_plugin_Firefly.core.user_role.store import UserRoleStore

_TRAILBLAZER = """---
id: trailblazer
title: 开拓者
kind: lore
---

他是星穹列车的无名客，我们约定过要再见面。
"""

_MY_ROLE = """---
id: my_role
title: 我的角色
kind: user_role
---

我是一个自写设定。
"""

_EMPTY_ROLE = """---
id: empty_role
title: 空角色
kind: user_role
---
"""


def _write(role_dir: Path, rel: str, text: str) -> None:
    """在角色目录下写入一个文件（自动创建父目录）。"""
    path = role_dir / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _make_registry(role_dir: Path) -> MaterialRegistry:
    """构建一个只含本测试资料的注册表。"""
    registry = MaterialRegistry(role_dir)
    report = registry.load()
    assert not report.warnings, report.warnings
    return registry


def _seed_role_dir(role_dir: Path) -> None:
    """写入测试用角色资料（开拓者 / 自定义 / 空角色）。"""
    _write(role_dir, "人物关系/开拓者.md", _TRAILBLAZER)
    _write(role_dir, "用户角色/我的角色.md", _MY_ROLE)
    _write(role_dir, "用户角色/空角色.md", _EMPTY_ROLE)


class TestUserRoleStore(IsolatedAsyncioTestCase):
    async def test_default_when_file_absent(self):
        """无文件时返回内置默认设置，且不产生告警。"""
        with tempfile.TemporaryDirectory() as tmp:
            store = UserRoleStore(Path(tmp) / "user_role.json")
            warnings = await store.load()
            self.assertEqual(warnings, [])
            self.assertEqual(store.default_setting(), UserRoleSetting())
            self.assertIsNone(store.session_setting("s1"))
            self.assertEqual(store.effective("s1"), UserRoleSetting())

    async def test_session_override_and_persist_roundtrip(self):
        """会话覆盖优先于默认，且能完整落盘并读回。"""
        with tempfile.TemporaryDirectory() as tmp:
            data_file = Path(tmp) / "user_role.json"
            store = UserRoleStore(data_file)
            await store.set_default(UserRoleSetting(MODE_EXISTING, "trailblazer"))
            await store.set_session("s1", UserRoleSetting(MODE_CUSTOM, "my_role"))
            self.assertEqual(
                store.effective("s1"), UserRoleSetting(MODE_CUSTOM, "my_role")
            )
            # 未覆盖的会话回落到默认
            self.assertEqual(
                store.effective("s2"), UserRoleSetting(MODE_EXISTING, "trailblazer")
            )

            store2 = UserRoleStore(data_file)
            warnings = await store2.load()
            self.assertEqual(warnings, [])
            self.assertEqual(
                store2.effective("s1"), UserRoleSetting(MODE_CUSTOM, "my_role")
            )
            self.assertEqual(
                store2.default_setting(), UserRoleSetting(MODE_EXISTING, "trailblazer")
            )

    async def test_clear_session_is_idempotent(self):
        """清除会话覆盖后回落默认；重复清除不报错。"""
        with tempfile.TemporaryDirectory() as tmp:
            store = UserRoleStore(Path(tmp) / "user_role.json")
            await store.set_session("s1", UserRoleSetting(MODE_CUSTOM, "my_role"))
            await store.clear_session("s1")
            self.assertIsNone(store.session_setting("s1"))
            await store.clear_session("s1")
            self.assertEqual(store.effective("s1"), UserRoleSetting())

    async def test_persist_false_keeps_memory_only(self):
        """persist=False 时不写盘，但内存状态仍生效。"""
        with tempfile.TemporaryDirectory() as tmp:
            data_file = Path(tmp) / "user_role.json"
            store = UserRoleStore(data_file, persist=False)
            await store.set_session("s1", UserRoleSetting(MODE_CUSTOM, "my_role"))
            self.assertEqual(
                store.effective("s1"), UserRoleSetting(MODE_CUSTOM, "my_role")
            )
            self.assertFalse(data_file.exists())

    async def test_load_corrupt_falls_back(self):
        """损坏文件回退默认并告警，不抛异常。"""
        with tempfile.TemporaryDirectory() as tmp:
            data_file = Path(tmp) / "user_role.json"
            data_file.write_text("{ not json", encoding="utf-8")
            store = UserRoleStore(data_file)
            warnings = await store.load()
            self.assertEqual(len(warnings), 1)
            self.assertEqual(store.default_setting(), UserRoleSetting())
            self.assertEqual(store.all_sessions(), {})

    async def test_concurrent_set_yields_valid_json(self):
        """并发写入后文件仍是合法 JSON，且不含临时文件残留。"""
        with tempfile.TemporaryDirectory() as tmp:
            data_file = Path(tmp) / "user_role.json"
            store = UserRoleStore(data_file)
            await asyncio.gather(
                *(
                    store.set_session(
                        f"s{i}", UserRoleSetting(MODE_CUSTOM, f"role_{i}")
                    )
                    for i in range(10)
                )
            )
            payload = json.loads(data_file.read_text(encoding="utf-8"))
            self.assertEqual(len(payload["sessions"]), 10)
            leftovers = list(Path(tmp).glob("*.tmp"))
            self.assertEqual(leftovers, [])

    async def test_close_persists(self):
        """close() 触发最终落盘。"""
        with tempfile.TemporaryDirectory() as tmp:
            data_file = Path(tmp) / "user_role.json"
            store = UserRoleStore(data_file, persist=True)
            await store.set_session("s1", UserRoleSetting(MODE_CUSTOM, "my_role"))
            await store.close()

            store2 = UserRoleStore(data_file)
            await store2.load()
            self.assertEqual(
                store2.effective("s1"), UserRoleSetting(MODE_CUSTOM, "my_role")
            )


class TestUserRoleSettingModel(unittest.TestCase):
    def test_from_dict_normalizes(self):
        """反序列化归一化 mode 与 role_id。"""
        self.assertEqual(
            UserRoleSetting.from_dict({"mode": "CUSTOM", "role_id": " x "}),
            UserRoleSetting(MODE_CUSTOM, "x"),
        )
        self.assertEqual(
            UserRoleSetting.from_dict({"mode": "garbage"}), UserRoleSetting()
        )
        self.assertEqual(UserRoleSetting.from_dict(None), UserRoleSetting())

    def test_to_dict_roundtrip(self):
        """to_dict/from_dict 往返一致。"""
        setting = UserRoleSetting(MODE_CUSTOM, "my_role")
        self.assertEqual(UserRoleSetting.from_dict(setting.to_dict()), setting)


class TestUserRoleResolver(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        role_dir = Path(self._tmp.name)
        _seed_role_dir(role_dir)
        self.registry = _make_registry(role_dir)
        self.resolver = UserRoleResolver()
        self.addCleanup(self._tmp.cleanup)

    def test_existing_default_id(self):
        """existing + 空 id → 命中内置默认身份。"""
        resolved = self.resolver.resolve(UserRoleSetting(), self.registry)
        self.assertEqual(resolved.pin_id, consts.DEFAULT_USER_ROLE_ID)
        self.assertEqual(resolved.origin, ORIGIN_EXISTING)
        self.assertTrue(resolved.has_entry)
        self.assertEqual(resolved.warning, "")

    def test_existing_explicit_id(self):
        """existing + 显式 id → 命中该人物。"""
        resolved = self.resolver.resolve(
            UserRoleSetting(MODE_EXISTING, "trailblazer"), self.registry
        )
        self.assertEqual(resolved.pin_id, "trailblazer")
        self.assertEqual(resolved.origin, ORIGIN_EXISTING)

    def test_custom_hit(self):
        """custom 命中自定义文档。"""
        resolved = self.resolver.resolve(
            UserRoleSetting(MODE_CUSTOM, "my_role"), self.registry
        )
        self.assertEqual(resolved.pin_id, "my_role")
        self.assertEqual(resolved.origin, ORIGIN_CUSTOM)
        self.assertTrue(resolved.has_entry)

    def test_lazy_loaded_content_not_mistaken_as_empty(self):
        """Tier3 懒加载：解析必须先 get 触发加载，再判空。

        这是实现红线：`registry.get` 返回前 content 为 None，直接判空会把
        有效身份误判为无效。
        """
        entry = self.registry.all_entries()
        my_role = next(e for e in entry if e.id == "my_role")
        self.assertIsNone(my_role.content)  # 加载前确为未加载态
        resolved = self.resolver.resolve(
            UserRoleSetting(MODE_CUSTOM, "my_role"), self.registry
        )
        self.assertTrue(resolved.has_entry)
        self.assertTrue(resolved.entry.content)

    def test_unreadable_document_degrades_without_raising(self):
        """运行期被替换为非 UTF-8 的文档，读取失败必须降级而非抛异常。

        `registry._read_file` 只捕获 OSError，UnicodeDecodeError 属于 ValueError，
        会穿透它；解析器必须在 `_safe_get` 兜底，否则会中断整段外壳注入。
        """
        role_file = Path(self._tmp.name) / "用户角色" / "我的角色.md"
        role_file.write_bytes(b"\xff\xfe\x00bad")
        resolved = self.resolver.resolve(
            UserRoleSetting(MODE_CUSTOM, "my_role"), self.registry
        )
        # 自定义读取失败 → 回退内置默认（开拓者文档仍可读）
        self.assertEqual(resolved.origin, ORIGIN_FALLBACK)
        self.assertNotEqual(resolved.warning, "")

    def test_custom_empty_body_falls_back(self):
        """custom 指向空正文 → 回退内置默认并告警。"""
        resolved = self.resolver.resolve(
            UserRoleSetting(MODE_CUSTOM, "empty_role"), self.registry
        )
        self.assertEqual(resolved.origin, ORIGIN_FALLBACK)
        self.assertEqual(resolved.pin_id, consts.DEFAULT_USER_ROLE_ID)
        self.assertIn("empty_role", resolved.warning)

    def test_custom_unknown_id_falls_back(self):
        """custom 指向不存在的 id → 回退内置默认。"""
        resolved = self.resolver.resolve(
            UserRoleSetting(MODE_CUSTOM, "nope"), self.registry
        )
        self.assertEqual(resolved.origin, ORIGIN_FALLBACK)
        self.assertEqual(resolved.pin_id, consts.DEFAULT_USER_ROLE_ID)

    def test_custom_without_id_falls_back(self):
        """custom 未指定 id → 回退内置默认。"""
        resolved = self.resolver.resolve(
            UserRoleSetting(MODE_CUSTOM, ""), self.registry
        )
        self.assertEqual(resolved.origin, ORIGIN_FALLBACK)

    def test_existing_unknown_id_has_no_identity(self):
        """existing 指向不存在的 id → 无身份（不回退到默认）。"""
        resolved = self.resolver.resolve(
            UserRoleSetting(MODE_EXISTING, "nope"), self.registry
        )
        self.assertEqual(resolved.origin, ORIGIN_NONE)
        self.assertEqual(resolved.pin_id, "")
        self.assertIsNone(resolved.entry)
        self.assertNotEqual(resolved.warning, "")

    def test_invalid_mode_normalized_to_existing(self):
        """非法 mode 归一化为 existing。"""
        resolved = self.resolver.resolve(
            UserRoleSetting("garbage", "trailblazer"), self.registry
        )
        self.assertEqual(resolved.origin, ORIGIN_EXISTING)

    def test_missing_default_doc_degrades(self):
        """内置默认文档也不可用时，返回无身份而非抛异常。"""
        with tempfile.TemporaryDirectory() as tmp:
            role_dir = Path(tmp)
            _write(role_dir, "用户角色/我的角色.md", _MY_ROLE)
            registry = _make_registry(role_dir)
            resolver = UserRoleResolver()
            resolved = resolver.resolve(UserRoleSetting(), registry)
            self.assertEqual(resolved.origin, ORIGIN_NONE)
            self.assertFalse(resolved.has_entry)


class TestUserRoleService(IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        role_dir = Path(self._tmp.name)
        _seed_role_dir(role_dir)
        self.registry = _make_registry(role_dir)
        self.store = UserRoleStore(Path(self._tmp.name) / "user_role.json")
        self.service = UserRoleService(self.store, self.registry)
        self.addCleanup(self._tmp.cleanup)

    async def test_session_scoped_settings(self):
        """身份按会话隔离：A 的覆盖不影响 B。"""
        await self.store.set_session("a", UserRoleSetting(MODE_CUSTOM, "my_role"))
        self.assertEqual(self.service.setting("a").role_id, "my_role")
        self.assertEqual(self.service.setting("b").role_id, "")
        self.assertEqual(self.service.resolve("b").pin_id, "trailblazer")

    async def test_exclude_ids(self):
        """pin 存在时排除该 id；无 pin 时为空集。"""
        resolved = self.service.resolve("s1")
        self.assertEqual(self.service.exclude_ids(resolved), {"trailblazer"})

        with tempfile.TemporaryDirectory() as tmp:
            role_dir = Path(tmp)
            _write(role_dir, "用户角色/我的角色.md", _MY_ROLE)
            registry = _make_registry(role_dir)
            service = UserRoleService(UserRoleStore(Path(tmp) / "s.json"), registry)
            empty = service.resolve("s1")
            self.assertEqual(service.exclude_ids(empty), frozenset())

    async def test_validate_setting_rules(self):
        """写前校验集中在服务层：只有"直接命中"或默认哨兵才合法。"""
        resolved, err = self.service.validate_setting(
            UserRoleSetting(MODE_CUSTOM, "my_role")
        )
        self.assertEqual(err, "")
        self.assertEqual(resolved.origin, ORIGIN_CUSTOM)

        _, err = self.service.validate_setting(UserRoleSetting(MODE_EXISTING, ""))
        self.assertEqual(err, "", "existing + 空 id 是内置默认哨兵，应放行")

        _, err = self.service.validate_setting(UserRoleSetting(MODE_CUSTOM, "nope"))
        self.assertNotEqual(err, "", "未知 id 会回退默认，但不得视为合法")

        _, err = self.service.validate_setting(
            UserRoleSetting(MODE_CUSTOM, "empty_role")
        )
        self.assertNotEqual(err, "", "空正文身份不得写入")

    async def test_all_sessions_copy(self):
        """all_sessions 返回副本，外部修改不影响内部。"""
        await self.store.set_session("a", UserRoleSetting(MODE_CUSTOM, "my_role"))
        snapshot = self.service.all_sessions()
        snapshot.clear()
        self.assertEqual(len(self.service.all_sessions()), 1)


class TestRegistryUserRoleFilter(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        role_dir = Path(self._tmp.name)
        _seed_role_dir(role_dir)
        self.registry = _make_registry(role_dir)
        self.addCleanup(self._tmp.cleanup)

    def test_dir_rule_infers_user_role_kind(self):
        """目录规则把 `用户角色/` 推断为 user_role。"""
        self.assertEqual(
            infer_tier_kind("用户角色/我的角色.md"),
            (consts.TIER_SKILL_LORE, consts.KIND_USER_ROLE),
        )

    def test_index_summary_excludes_user_role(self):
        """路由摘要不含身份文档，但普通人物的条目仍在。"""
        summary = self.registry.index_summary()
        self.assertNotIn("my_role", summary)
        self.assertNotIn("empty_role", summary)
        self.assertIn("trailblazer", summary)

    def test_all_entries_and_get_still_include_user_role(self):
        """索引与检索仍包含身份文档（解析与编辑器需要）。"""
        ids = {e.id for e in self.registry.all_entries()}
        self.assertIn("my_role", ids)
        self.assertEqual(self.registry.get("my_role").kind, consts.KIND_USER_ROLE)
