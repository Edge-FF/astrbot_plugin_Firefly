"""adapter.commands 的 `/firefly role` 命令冒烟测试（P4）。

命令装饰器不改变函数本体，因此可直接以桩 self 调用，验证参数分支与会话写入。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from astrbot_plugin_Firefly.adapter.commands import (
    FireflyCommandMixin,
    _role_label,
)
from astrbot_plugin_Firefly.core.materials.registry import MaterialRegistry
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
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class _FakeEvent:
    def __init__(self, umo: str = "s1") -> None:
        self.unified_msg_origin = umo
        self.result = None

    def set_result(self, result) -> None:
        self.result = result


class _Stub:
    """提供命令所需的 `_core` 容器。"""

    def __init__(self, service, store) -> None:
        self._core = SimpleNamespace(user_role_service=service, user_role_store=store)


class TestFireflyRoleCommand(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name) / "role"
        root.mkdir()
        _write(root, "人物关系/开拓者.md", _TRAILBLAZER)
        _write(root, "用户角色/我的角色.md", _MY_ROLE)
        _write(root, "用户角色/空角色.md", _EMPTY_ROLE)
        self.registry = MaterialRegistry(root)
        self.registry.load()
        self.store = UserRoleStore(Path(self._tmp.name) / "ur.json", persist=False)
        self.service = UserRoleService(self.store, self.registry)
        self.stub = _Stub(self.service, self.store)
        self.addCleanup(self._tmp.cleanup)

    async def _run(self, *args) -> _FakeEvent:
        event = _FakeEvent()
        await FireflyCommandMixin.firefly_role(self.stub, event, *args)
        self.assertIsNotNone(event.result, "命令必须回显结果")
        return event

    async def test_set_custom_writes_session(self):
        """set custom <id> 写入会话覆盖。"""
        await self._run("set", "custom", "my_role")
        self.assertEqual(
            self.store.session_setting("s1"), UserRoleSetting(MODE_CUSTOM, "my_role")
        )

    async def test_set_existing_default_sentinel(self):
        """set existing（省略 id）使用内置默认哨兵。"""
        await self._run("set", "existing")
        self.assertEqual(
            self.store.session_setting("s1"), UserRoleSetting(MODE_EXISTING, "")
        )

    async def test_set_rejects_invalid(self):
        """未知 id / 空正文 / 非法 mode 都不写入。"""
        await self._run("set", "custom", "nope")
        self.assertIsNone(self.store.session_setting("s1"))
        await self._run("set", "custom", "empty_role")
        self.assertIsNone(self.store.session_setting("s1"))
        await self._run("set", "bogus", "my_role")
        self.assertIsNone(self.store.session_setting("s1"))

    async def test_clear_removes_override(self):
        """clear 清除会话覆盖（幂等）。"""
        await self._run("set", "custom", "my_role")
        await self._run("clear")
        self.assertIsNone(self.store.session_setting("s1"))
        await self._run("clear")

    async def test_status_and_usage_do_not_raise(self):
        """status 缺省动作与未知动作都能安全回显。"""
        await self._run()
        await self._run("status")
        await self._run("unknown-action")

    def test_role_label_variants(self):
        """标签渲染覆盖命中/不可用/哨兵三种形态。"""
        resolved, _ = self.service.validate_setting(
            UserRoleSetting(MODE_CUSTOM, "my_role")
        )
        self.assertIn(
            "我的角色", _role_label(UserRoleSetting(MODE_CUSTOM, "my_role"), resolved)
        )
        sentinel = _role_label(UserRoleSetting(MODE_EXISTING, ""), None)
        self.assertIn("内置默认", sentinel)
        self.assertIn("不可用", _role_label(UserRoleSetting(MODE_CUSTOM, "nope"), None))


if __name__ == "__main__":
    unittest.main()
