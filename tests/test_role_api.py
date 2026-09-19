"""adapter.role_api 写路径与守卫的测试（T3-7 ~ T3-9）。

这些用例覆盖只能在 HTTP 层验证的部分：活跃会话第三重守卫、写后 registry 传播、
以及删除后残留激活项的运行时容错。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any

from astrbot_plugin_Firefly.adapter.role_api import RoleApi
from astrbot_plugin_Firefly.core.cognition.state import StateStore
from astrbot_plugin_Firefly.core.materials.registry import MaterialRegistry
from astrbot_plugin_Firefly.core.materials.role_store import RoleStore
from astrbot_plugin_Firefly.core.models import ActivatedEntry
from astrbot_plugin_Firefly.core.user_role.models import (
    MODE_CUSTOM,
    MODE_EXISTING,
    UserRoleSetting,
)
from astrbot_plugin_Firefly.core.user_role.store import UserRoleStore


def _write(root: Path, rel: str, text: str) -> None:
    """在 role 目录下写入一个文件（自动创建父目录）。"""
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class TestRoleApiGuards(unittest.IsolatedAsyncioTestCase):
    """写路径的第三重守卫与 registry 传播。"""

    def setUp(self) -> None:
        """准备临时 role 目录、registry、会话状态与 RoleApi。"""
        self._tmp = tempfile.TemporaryDirectory()
        base = Path(self._tmp.name)
        self.root = base / "role"
        self.root.mkdir()
        _write(
            self.root,
            "技能/战斗.md",
            "---\nid: skill_battle\nkind: skill\ntitle: 战斗\n---\n战斗说明。",
        )
        _write(
            self.root,
            "人物关系/三月七.md",
            "---\nid: npc_march7\nkind: lore\n---\n三月七资料。",
        )
        self.store = RoleStore(self.root)
        self.registry = MaterialRegistry(self.root)
        self.registry.load()
        self.state = StateStore(base / "state.json", persist=False)
        self.api = RoleApi(
            context=None,
            store=self.store,
            registry=self.registry,
            state_store=self.state,
        )
        # 直接调用处理器时没有 quart 请求上下文，桩掉查询参数读取
        self.api._get_query = lambda key: None

    def tearDown(self) -> None:
        """清理临时目录。"""
        self._tmp.cleanup()

    @staticmethod
    def _stub_json(payload: dict[str, Any]):
        """把请求体读取替换为固定载荷。"""

        async def _fake_get_json() -> dict[str, Any]:
            return payload

        return _fake_get_json

    async def _activate(self, entry_id: str, session_id: str = "sess1") -> None:
        """在会话状态中激活一个条目。"""
        state = await self.state.get(session_id)
        state.active_context.entries.append(
            ActivatedEntry(entry_id=entry_id, remaining_ttl=3)
        )
        await self.state.set(session_id, state)

    async def test_tree_reports_active_sessions(self) -> None:
        """role/tree 需回传每条目的活跃会话数与会话 id。"""
        await self._activate("skill_battle", "sessA")
        await self._activate("skill_battle", "sessB")
        resp = await self.api._role_tree()
        self.assertEqual(resp["status"], "ok")
        entry = [
            item for item in resp["data"]["entries"] if item["id"] == "skill_battle"
        ][0]
        self.assertEqual(entry["active_sessions"], 2)
        self.assertEqual(entry["active_session_ids"], ["sessA", "sessB"])

    async def test_rename_route_propagates(self) -> None:
        """S6：role/rename 改路径后 registry 立即指向新位置。"""
        doc = self.store.read_document("技能/战斗.md")
        self.api._get_json = self._stub_json(
            {
                "path": "技能/战斗.md",
                "base_rev": doc["rev"],
                "target": "人物关系/战斗.md",
            }
        )
        resp = await self.api._role_rename()
        self.assertEqual(resp["status"], "ok")
        self.assertEqual(resp["data"]["path"], "人物关系/战斗.md")
        self.assertEqual(resp["data"]["reload"]["total"], 2)

        entry = self.registry.get("skill_battle")
        self.assertIsNotNone(entry)
        self.assertTrue(entry.source_path.endswith("战斗.md"))
        self.assertIn("人物关系", entry.source_path)
        self.assertFalse((self.root / "技能" / "战斗.md").exists())

    async def test_delete_requires_active_confirmation(self) -> None:
        """T3-7：条目被会话激活时必须二次确认，否则拒绝删除。"""
        await self._activate("skill_battle")
        doc = self.store.read_document("技能/战斗.md")
        target = self.root / "技能" / "战斗.md"

        self.api._get_json = self._stub_json(
            {"path": "技能/战斗.md", "base_rev": doc["rev"], "confirm_name": "战斗.md"}
        )
        resp = await self.api._role_delete()
        self.assertEqual(resp["status"], "error")
        self.assertIn("正被 1 个会话使用", resp["message"])
        self.assertTrue(target.is_file())

        self.api._get_json = self._stub_json(
            {
                "path": "技能/战斗.md",
                "base_rev": doc["rev"],
                "confirm_name": "战斗.md",
                "confirm_active": True,
            }
        )
        resp = await self.api._role_delete()
        self.assertEqual(resp["status"], "ok")
        self.assertTrue(resp["data"]["deleted"])
        self.assertFalse(target.is_file())

    async def test_delete_rejects_bad_confirm_name(self) -> None:
        """T3-7：confirm_name 与文件名不一致时拒绝。"""
        doc = self.store.read_document("技能/战斗.md")
        self.api._get_json = self._stub_json(
            {"path": "技能/战斗.md", "base_rev": doc["rev"], "confirm_name": "战斗.txt"}
        )
        resp = await self.api._role_delete()
        self.assertEqual(resp["status"], "error")
        self.assertIn("确认名称", resp["message"])
        self.assertTrue((self.root / "技能" / "战斗.md").is_file())

    async def test_save_propagates_to_registry(self) -> None:
        """T3-8：保存后 registry 立即收录，并回传 reload 统计。"""
        self.api._get_json = self._stub_json(
            {
                "path": "技能/新技能.md",
                "create": True,
                "frontmatter": {"id": "skill_new", "kind": "skill", "title": "新技能"},
                "body": "新内容。",
            }
        )
        resp = await self.api._role_save()
        self.assertEqual(resp["status"], "ok")
        self.assertEqual(resp["data"]["id"], "skill_new")
        self.assertEqual(resp["data"]["created"], True)
        reload_info = resp["data"]["reload"]
        self.assertEqual(reload_info["total"], 3)
        self.assertEqual(reload_info["tier_counts"]["3"], 3)
        self.assertIn("warnings", reload_info)

        entry = self.registry.get("skill_new")
        self.assertIsNotNone(entry)
        self.assertEqual(entry.title, "新技能")
        self.assertEqual(entry.content, "新内容。")

    async def test_overwrite_then_delete_reflects_in_registry(self) -> None:
        """T3-8：覆盖与删除都要立即反映到 registry。"""
        doc = self.store.read_document("技能/战斗.md")
        self.api._get_json = self._stub_json(
            {
                "path": "技能/战斗.md",
                "create": False,
                "base_rev": doc["rev"],
                "frontmatter": {
                    "id": "skill_battle",
                    "kind": "skill",
                    "title": "战斗（改）",
                },
                "body": "改后的说明。",
            }
        )
        resp = await self.api._role_save()
        self.assertEqual(resp["status"], "ok")
        self.assertEqual(resp["data"]["created"], False)
        self.assertEqual(self.registry.get("skill_battle").title, "战斗（改）")

        revised = self.store.read_document("技能/战斗.md")
        self.api._get_json = self._stub_json(
            {
                "path": "技能/战斗.md",
                "base_rev": revised["rev"],
                "confirm_name": "战斗.md",
            }
        )
        resp = await self.api._role_delete()
        self.assertEqual(resp["status"], "ok")
        self.assertEqual(resp["data"]["reload"]["total"], 1)
        self.assertIsNone(self.registry.get("skill_battle"))

    async def test_dangling_active_entry_is_tolerated(self) -> None:
        """T3-9：删除后被激活的条目缺失，运行时不抛异常。"""
        await self._activate("skill_battle")
        doc = self.store.read_document("技能/战斗.md")
        self.api._get_json = self._stub_json(
            {
                "path": "技能/战斗.md",
                "base_rev": doc["rev"],
                "confirm_name": "战斗.md",
                "confirm_active": True,
            }
        )
        resp = await self.api._role_delete()
        self.assertEqual(resp["status"], "ok")

        self.assertIsNone(self.registry.get("skill_battle"))
        self.assertEqual(self.registry.fetch(["skill_battle"]), [])
        state = await self.state.get("sess1")
        self.assertEqual(state.active_context.entries[0].entry_id, "skill_battle")

    async def test_errors_are_returned_not_raised(self) -> None:
        """非法路径 / 陈旧 rev 都返回 {status:"error"} 而不是抛异常。"""
        self.api._get_json = self._stub_json(
            {"path": "../evil.md", "create": True, "frontmatter": {}, "body": "正文。"}
        )
        resp = await self.api._role_save()
        self.assertEqual(resp["status"], "error")

        self.api._get_json = self._stub_json(
            {
                "path": "技能/战斗.md",
                "create": False,
                "base_rev": "deadbeefdeadbeef",
                "frontmatter": {"id": "skill_battle"},
                "body": "正文。",
            }
        )
        resp = await self.api._role_save()
        self.assertEqual(resp["status"], "error")
        self.assertIn("外部修改", resp["message"])


class TestDeleteGuardIncludesPinned(unittest.IsolatedAsyncioTestCase):
    """P2-2：删除守卫必须识别"正被 pin 为身份"的条目。

    仅统计 `active_context` 会漏掉身份预设：用户把自己的角色文档设为身份后，
    删除它不会得到提示，可能连同正在使用的设定一起删除。
    """

    def setUp(self) -> None:
        """准备 role 目录、仓库与带身份仓库的 RoleApi。"""
        self._tmp = tempfile.TemporaryDirectory()
        base = Path(self._tmp.name)
        self.root = base / "role"
        self.root.mkdir()
        _write(
            self.root,
            "人物关系/三月七.md",
            "---\nid: npc_march7\nkind: lore\n---\n三月七资料。",
        )
        _write(
            self.root,
            "人物关系/丹恒.md",
            "---\nid: npc_danheng\nkind: lore\n---\n丹恒资料。",
        )
        self.registry = MaterialRegistry(self.root)
        self.registry.load()
        self.user_role_store = UserRoleStore(base / "ur.json", persist=False)
        self.api = RoleApi(
            context=None,
            store=RoleStore(self.root),
            registry=self.registry,
            state_store=StateStore(base / "state.json", persist=False),
            user_role_store=self.user_role_store,
        )
        self.api._get_query = lambda key: None

    def tearDown(self) -> None:
        """清理临时目录。"""
        self._tmp.cleanup()

    async def test_pinned_session_triggers_guard(self) -> None:
        """会话把某文档 pin 为身份时，删除守卫应列出该会话。"""
        await self.user_role_store.set_session(
            "sess_pin", UserRoleSetting(MODE_CUSTOM, "npc_march7")
        )
        ids = await self.api._active_session_ids("人物关系/三月七.md")
        self.assertIn("sess_pin", ids)

    async def test_global_default_triggers_guard(self) -> None:
        """全局默认身份指向该文档时，也应触发守卫（以占位标记列出）。"""
        await self.user_role_store.set_default(
            UserRoleSetting(MODE_EXISTING, "npc_march7")
        )
        ids = await self.api._active_session_ids("人物关系/三月七.md")
        self.assertIn("（全局默认身份）", ids)

    async def test_unrelated_document_not_flagged(self) -> None:
        """未被 pin 也无激活的其它文档不应误报。"""
        await self.user_role_store.set_session(
            "sess_pin", UserRoleSetting(MODE_CUSTOM, "npc_march7")
        )
        ids = await self.api._active_session_ids("人物关系/丹恒.md")
        self.assertEqual(ids, [])


if __name__ == "__main__":
    unittest.main()
