"""adapter.user_role_api 的接口契约测试（P2）。

覆盖：默认/会话视图、写前校验（非法 id / 空正文 / 缺 session / 非法 scope）、
reset 语义，以及候选列表对空正文与隐藏文件的处理。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any

from astrbot_plugin_Firefly.adapter.user_role_api import UserRoleApi
from astrbot_plugin_Firefly.core import consts
from astrbot_plugin_Firefly.core.materials.registry import MaterialRegistry
from astrbot_plugin_Firefly.core.materials.role_store import RoleStore
from astrbot_plugin_Firefly.core.user_role.models import (
    MODE_CUSTOM,
    MODE_EXISTING,
    UserRoleSetting,
)
from astrbot_plugin_Firefly.core.user_role.service import UserRoleService
from astrbot_plugin_Firefly.core.user_role.store import UserRoleStore

_TRAILBLAZER = """---
id: trailblazer
title: 开拓者
kind: lore
---

他是星穹列车的无名客。
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


def _write(root: Path, rel: str, text: str) -> None:
    """在 role 目录下写入文件（自动建父目录）。"""
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class TestUserRoleApi(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        """准备临时 role 目录与 UserRoleApi（context=None + 桩掉请求读取）。"""
        self._tmp = tempfile.TemporaryDirectory()
        base = Path(self._tmp.name)
        self.root = base / "role"
        self.root.mkdir()
        _write(self.root, "人物关系/开拓者.md", _TRAILBLAZER)
        _write(self.root, f"{consts.USER_ROLE_CUSTOM_DIR}/我的角色.md", _MY_ROLE)
        _write(self.root, f"{consts.USER_ROLE_CUSTOM_DIR}/空角色.md", _EMPTY_ROLE)
        # 隐藏文件：不应出现在候选中
        _write(self.root, f"{consts.USER_ROLE_CUSTOM_DIR}/_隐藏.md", _MY_ROLE)

        self.registry = MaterialRegistry(self.root)
        self.registry.load()
        self.role_store = RoleStore(self.root)
        self.user_role_store = UserRoleStore(base / "ur.json", persist=False)
        self.service = UserRoleService(self.user_role_store, self.registry)
        self.api = UserRoleApi(
            context=None,
            service=self.service,
            registry=self.registry,
            role_store=self.role_store,
        )
        self.api._get_query = lambda key: None

    def tearDown(self) -> None:
        """清理临时目录。"""
        self._tmp.cleanup()

    @staticmethod
    def _stub_query(mapping: dict[str, str]):
        """把查询参数读取替换为固定映射。"""
        return lambda key: mapping.get(key)

    @staticmethod
    def _stub_json(payload: dict[str, Any]):
        """把请求体读取替换为固定载荷。"""

        async def _fake_get_json() -> dict[str, Any]:
            return payload

        return _fake_get_json

    async def test_get_global_default(self) -> None:
        """全局视图：默认身份解析为内置开拓者，候选含空正文标记、不含隐藏文件。"""
        resp = await self.api._get()
        self.assertEqual(resp["status"], "ok")
        data = resp["data"]
        self.assertEqual(data["default"], {"mode": MODE_EXISTING, "role_id": ""})
        self.assertIsNone(data["session"])
        self.assertEqual(data["resolved"]["pin_id"], consts.DEFAULT_USER_ROLE_ID)
        self.assertTrue(data["resolved"]["exists"])
        self.assertIn("星穹列车的无名客", data["resolved"]["body"])

        existing_ids = [c["id"] for c in data["candidates"]["existing"]]
        custom = {c["id"]: c for c in data["candidates"]["custom"]}
        self.assertIn(consts.DEFAULT_USER_ROLE_ID, existing_ids)
        self.assertIn("my_role", custom)
        self.assertFalse(custom["my_role"]["empty"])
        self.assertTrue(custom["empty_role"]["empty"])
        self.assertNotIn("_隐藏", custom)

    async def test_get_session_override(self) -> None:
        """会话视图：显式覆盖优先，预览正文来自自定义文档。"""
        await self.user_role_store.set_session(
            "s1", UserRoleSetting(MODE_CUSTOM, "my_role")
        )
        self.api._get_query = self._stub_query({"session": "s1"})
        resp = await self.api._get()
        data = resp["data"]
        self.assertEqual(data["session"], {"mode": MODE_CUSTOM, "role_id": "my_role"})
        self.assertEqual(data["resolved"]["pin_id"], "my_role")
        self.assertIn("自写设定", data["resolved"]["body"])

    async def test_set_session_custom(self) -> None:
        """设置会话自定义身份成功并落库。"""
        self.api._get_json = self._stub_json(
            {
                "scope": "session",
                "session": "s1",
                "mode": "custom",
                "role_id": "my_role",
            }
        )
        resp = await self.api._set()
        self.assertEqual(resp["status"], "ok")
        setting = self.user_role_store.session_setting("s1")
        self.assertEqual(setting, UserRoleSetting(MODE_CUSTOM, "my_role"))

    async def test_set_rejects_unknown_id(self) -> None:
        """校验失败的 id 被拒绝，且不写入设置。"""
        self.api._get_json = self._stub_json(
            {"scope": "session", "session": "s1", "mode": "custom", "role_id": "nope"}
        )
        resp = await self.api._set()
        self.assertEqual(resp["status"], "error")
        self.assertIsNone(self.user_role_store.session_setting("s1"))

    async def test_set_rejects_empty_body(self) -> None:
        """空正文文档不可作为身份。"""
        self.api._get_json = self._stub_json(
            {
                "scope": "session",
                "session": "s1",
                "mode": "custom",
                "role_id": "empty_role",
            }
        )
        resp = await self.api._set()
        self.assertEqual(resp["status"], "error")
        self.assertIsNone(self.user_role_store.session_setting("s1"))

    async def test_set_default_sentinel_allowed(self) -> None:
        """existing + 空 id 是内置默认哨兵，允许写入。"""
        self.api._get_json = self._stub_json(
            {"scope": "default", "mode": "existing", "role_id": ""}
        )
        resp = await self.api._set()
        self.assertEqual(resp["status"], "ok")
        self.assertEqual(self.user_role_store.default_setting(), UserRoleSetting())

    async def test_set_session_requires_session(self) -> None:
        """scope=session 缺少 session 时拒绝。"""
        self.api._get_json = self._stub_json(
            {"scope": "session", "mode": "custom", "role_id": "my_role"}
        )
        resp = await self.api._set()
        self.assertEqual(resp["status"], "error")

    async def test_set_invalid_scope(self) -> None:
        """scope 非法时拒绝，避免静默写默认。"""
        self.api._get_json = self._stub_json(
            {"scope": "bogus", "mode": "custom", "role_id": "my_role"}
        )
        resp = await self.api._set()
        self.assertEqual(resp["status"], "error")

    async def test_reset_session_and_default(self) -> None:
        """reset 清除会话覆盖与重置默认。"""
        await self.user_role_store.set_session(
            "s1", UserRoleSetting(MODE_CUSTOM, "my_role")
        )
        self.api._get_json = self._stub_json({"scope": "session", "session": "s1"})
        resp = await self.api._reset()
        self.assertEqual(resp["status"], "ok")
        self.assertIsNone(self.user_role_store.session_setting("s1"))

        await self.user_role_store.set_default(UserRoleSetting(MODE_CUSTOM, "my_role"))
        self.api._get_json = self._stub_json({"scope": "default"})
        resp = await self.api._reset()
        self.assertEqual(resp["status"], "ok")
        self.assertEqual(self.user_role_store.default_setting(), UserRoleSetting())
