"""core.materials.role_store 的单元测试：路径边界、目录树、单文件读取。

对账用例（test_matches_registry_metadata）用于锁定"树元数据 == registry 索引"，
防止两侧推断规则漂移。
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from astrbot_plugin_Firefly.core.materials.registry import MaterialRegistry
from astrbot_plugin_Firefly.core.materials.role_store import (
    MAX_DOCUMENT_BYTES,
    MAX_PATH_DEPTH,
    RoleStore,
    RoleStoreError,
)


def _write(root: Path, rel: str, text: str) -> None:
    """在 role 目录下写入一个文件（自动创建父目录）。"""
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _make_dir_link(link: Path, target: Path) -> bool:
    """创建指向 target 的目录链接，成功返回 True。

    优先用 symlink（Linux/macOS 与开启开发者模式的 Windows）；
    失败时在 Windows 上退回目录联接（junction，无需管理员权限）。
    """
    try:
        os.symlink(target, link, target_is_directory=True)
        return True
    except (OSError, NotImplementedError):
        pass
    if os.name != "nt":
        return False
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(target)],
        capture_output=True,
        check=False,
    )
    return result.returncode == 0


class TestValidateRelPath(unittest.TestCase):
    """T2-2：路径校验与遍历防护。"""

    def setUp(self) -> None:
        """准备临时 role 目录。"""
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.store = RoleStore(self.root)

    def tearDown(self) -> None:
        """清理临时目录。"""
        self._tmp.cleanup()

    def test_accepts_legit_paths(self) -> None:
        """正常中英文路径（含 • 等 Unicode 标点）应通过。"""
        self.assertEqual(
            self.store.validate_rel_path("技能/讲笑话.md"), "技能/讲笑话.md"
        )
        self.assertEqual(
            self.store.validate_rel_path("世界知识/阮•梅.md"), "世界知识/阮•梅.md"
        )
        self.assertEqual(self.store.validate_rel_path("技能", is_dir=True), "技能")
        self.assertEqual(self.store.validate_rel_path("技能/", is_dir=True), "技能")

    def test_normalizes_separators_and_nfc(self) -> None:
        """反斜杠与 NFD 形式应被规范化。"""
        self.assertEqual(self.store.validate_rel_path("技能\\战斗.md"), "技能/战斗.md")
        self.assertEqual(self.store.validate_rel_path("技能/e\u0301.md"), "技能/é.md")

    def test_rejects_traversal_and_absolute(self) -> None:
        """路径遍历、绝对路径、盘符、UNC 应被拒绝。"""
        for bad in (
            "../config.json",
            "a/../../b.md",
            "..",
            ".",
            "/etc/passwd.md",
            "C:\\x.md",
            "C:/x.md",
            "//srv/share/a.md",
            "a//b.md",
            "a.md:stream",
        ):
            with self.assertRaises(RoleStoreError, msg=bad):
                self.store.validate_rel_path(bad)

    def test_rejects_bad_segments(self) -> None:
        """扩展名、保留名、首尾空白/点、超长段应被拒绝。"""
        for bad in (
            "技能/notes.txt",
            "技能/CON.md",
            "技能/NUL.md",
            "技能/com1.md",
            "技能/ 讲笑话.md",
            "技能/讲笑话 .md",
            "技能/讲笑话..md",
            "技能/.md",
            "技能/" + "a" * 65 + ".md",
            "",
            "   ",
        ):
            with self.assertRaises(RoleStoreError, msg=bad):
                self.store.validate_rel_path(bad)

    def test_rejects_over_deep_path(self) -> None:
        """超过深度上限的路径应被拒绝，恰好等于上限的应通过。"""
        deep = "/".join(f"d{i}" for i in range(MAX_PATH_DEPTH + 1)) + ".md"
        with self.assertRaises(RoleStoreError):
            self.store.validate_rel_path(deep)
        ok_path = "/".join(f"d{i}" for i in range(MAX_PATH_DEPTH - 1)) + "/x.md"
        self.assertEqual(self.store.validate_rel_path(ok_path), ok_path)

    def test_rejects_linked_parent(self) -> None:
        """父目录为链接指向 role/ 之外时应被拒绝（无法建立链接时跳过）。"""
        outside = Path(self._tmp.name).parent / f"firefly_outside_{id(self)}"
        outside.mkdir(exist_ok=True)
        link = self.root / "link"
        if not _make_dir_link(link, outside):
            self.skipTest("当前环境不支持创建目录链接")
        with self.assertRaises(RoleStoreError):
            self.store.validate_rel_path("link/a.md")


class TestListTree(unittest.TestCase):
    """T2-3 / T2-5 / T2-6：目录树列举、状态判定与边界。"""

    def setUp(self) -> None:
        """准备包含各类边界文件的临时 role 目录。"""
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        _write(
            self.root, "基础人设.md", "---\nid: persona_base\ntier: 1\n---\n常驻人格。"
        )
        _write(
            self.root,
            "技能/战斗.md",
            "---\nid: skill_battle\nkind: skill\nkeywords: [战斗]\npriority: 80\n---\n战斗说明。",
        )
        _write(self.root, "技能/空技能.md", "---\nid: skill_empty\nkind: skill\n---\n")
        _write(self.root, "技能/a_dup.md", "---\nid: dup_id\n---\n先加载。")
        _write(self.root, "技能/b_dup.md", "---\nid: dup_id\n---\n后加载。")
        _write(self.root, "世界知识/派系/x.md", "---\nid: faction_x\n---\n派系说明。")
        _write(self.root, "_草稿.md", "---\nid: draft\n---\n草稿内容。")
        self.store = RoleStore(self.root)

    def tearDown(self) -> None:
        """清理临时目录。"""
        self._tmp.cleanup()

    def test_lists_all_visible_files_with_status(self) -> None:
        """可见文件全部入树，并按健康度给出 status。"""
        tree = self.store.list_tree()
        by_path = {entry["path"]: entry for entry in tree["entries"]}
        self.assertEqual(
            set(by_path),
            {
                "世界知识/派系/x.md",
                "技能/a_dup.md",
                "技能/b_dup.md",
                "技能/空技能.md",
                "技能/战斗.md",
                "基础人设.md",
            },
        )
        self.assertEqual(by_path["技能/战斗.md"]["status"], "registered")
        self.assertEqual(by_path["技能/空技能.md"]["status"], "empty_body")
        self.assertEqual(by_path["技能/a_dup.md"]["status"], "registered")
        self.assertEqual(by_path["技能/b_dup.md"]["status"], "duplicate_id")
        self.assertTrue(any("冲突" in w for w in tree["warnings"]))

    def test_dirs_and_counts(self) -> None:
        """dirs 含根目录与各级子目录，counts 按 tier 统计全部条目。"""
        tree = self.store.list_tree()
        self.assertEqual(tree["dirs"][0], "")
        self.assertIn("技能", tree["dirs"])
        self.assertIn("世界知识/派系", tree["dirs"])
        self.assertEqual(sum(tree["counts"].values()), len(tree["entries"]))
        self.assertEqual(tree["counts"]["3"], 4)

    def test_hidden_files(self) -> None:
        """隐藏文件默认不列举，显式请求时标记为 hidden。"""
        self.assertNotIn(
            "_草稿.md", {e["path"] for e in self.store.list_tree()["entries"]}
        )
        tree = self.store.list_tree(include_hidden=True)
        hidden = [e for e in tree["entries"] if e["path"] == "_草稿.md"]
        self.assertEqual(len(hidden), 1)
        self.assertEqual(hidden[0]["status"], "hidden")
        self.assertEqual(hidden[0]["id"], "draft")

    def test_hidden_dir_is_skipped(self) -> None:
        """隐藏目录默认不进入，也不贡献 dirs。"""
        _write(self.root, "_drafts/secret.md", "---\nid: secret\n---\n内容。")
        tree = self.store.list_tree()
        self.assertNotIn("_drafts", tree["dirs"])
        self.assertNotIn("_drafts/secret.md", {e["path"] for e in tree["entries"]})

    def test_depth_boundary(self) -> None:
        """深度等于上限的目录可列举，超过上限的被跳过并告警。"""
        ok_rel = "/".join([*[f"d{i}" for i in range(MAX_PATH_DEPTH - 1)], "ok.md"])
        too_deep = "/".join([*[f"d{i}" for i in range(MAX_PATH_DEPTH)], "deep.md"])
        _write(self.root, ok_rel, "---\nid: ok_deep\n---\n内容。")
        _write(self.root, too_deep, "---\nid: too_deep\n---\n内容。")

        tree = self.store.list_tree()
        paths = {entry["path"] for entry in tree["entries"]}
        self.assertIn(ok_rel, paths)
        self.assertNotIn(too_deep, paths)
        self.assertTrue(any("深度" in w for w in tree["warnings"]))

    def test_matches_registry_metadata(self) -> None:
        """树的 registered 条目元数据必须与 MaterialRegistry 索引一致。

        额外注入脏值与零值字段，锁定两侧降级规则（含 `or` 回退语义）完全一致。
        """
        _write(
            self.root,
            "技能/脏值.md",
            "---\nid: dirty\ntier: abc\npriority: high\n"
            "default_ttl: [1]\ntags: 战斗\n---\n内容。",
        )
        _write(
            self.root,
            "技能/零值.md",
            "---\nid: zero\ntier: 0\npriority: 0\n---\n内容。",
        )

        tree = self.store.list_tree()
        registry = MaterialRegistry(self.root)
        registry.load()
        by_name = {
            Path(entry.source_path).name: entry for entry in registry.all_entries()
        }

        checked = 0
        for node in tree["entries"]:
            if node["status"] != "registered":
                continue
            target = by_name[node["name"]]
            self.assertEqual(node["id"], target.id, node["path"])
            self.assertEqual(node["title"], target.title, node["path"])
            self.assertEqual(node["tier"], target.tier, node["path"])
            self.assertEqual(node["kind"], target.kind, node["path"])
            self.assertEqual(node["priority"], target.priority, node["path"])
            self.assertEqual(node["default_ttl"], target.default_ttl, node["path"])
            self.assertEqual(tuple(node["tags"]), tuple(target.tags), node["path"])
            self.assertEqual(
                tuple(node["keywords"]), tuple(target.trigger_keywords), node["path"]
            )
            checked += 1
        self.assertGreaterEqual(checked, 3)

        dirty = [e for e in tree["entries"] if e["path"] == "技能/脏值.md"][0]
        self.assertEqual(dirty["tier"], 3)
        self.assertEqual(dirty["priority"], 50)
        self.assertEqual(dirty["tags"], ["战斗"])
        self.assertTrue(any("不是整数" in w for w in tree["warnings"]))


class TestWritePath(unittest.TestCase):
    """T3-1 ~ T3-6：写路径守卫与往返保真。"""

    def setUp(self) -> None:
        """准备临时 role 目录。"""
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.store = RoleStore(self.root)

    def tearDown(self) -> None:
        """清理临时目录。"""
        self._tmp.cleanup()

    def test_roundtrip_preserves_fields_and_unknown_keys(self) -> None:
        """T3-1：写入 → 读取 → 字段完全一致（含特殊字符与未知键）。"""
        _write(
            self.root,
            "技能/往返.md",
            "---\nid: skill_rt\nx_custom: keep me\n---\n原正文。",
        )
        frontmatter = {
            "type": "on_demand",
            "kind": "skill",
            "title": '含冒号: 与井号 # 与方括号 [x] 与引号 "q"',
            "id": "skill_rt",
            "tags": ["a,b", "c d", "普通"],
            "keywords": ["3", "true", "战斗"],
            "patterns": [],
            "tier": 3,
            "priority": 80,
            "default_ttl": 4,
            "leading": " 前后留空 ",
        }
        frontmatter.pop("leading")
        body = "## 机制\n她会尝试讲笑话。\n\n- 要点一\n- 要点二"

        result = self.store.save_document(
            "技能/往返.md",
            create=False,
            base_rev=self.store.read_document("技能/往返.md")["rev"],
            frontmatter=frontmatter,
            body=body,
        )

        doc = self.store.read_document("技能/往返.md")
        # 非空字段逐字段一致；空值（patterns: []）按约定不落盘
        expected = {
            key: value
            for key, value in frontmatter.items()
            if value not in (None, "", [], ())
        }
        self.assertEqual(doc["frontmatter"], expected)
        self.assertNotIn("patterns", doc["frontmatter"])
        self.assertEqual(doc["body"], body)
        self.assertEqual(doc["unknown_keys"], ["x_custom"])
        self.assertEqual(result["effective_tier"], 3)
        self.assertEqual(result["effective_kind"], "skill")

        # 再次保存同一内容：文本必须逐字节稳定（序列化幂等）
        second = self.store.save_document(
            "技能/往返.md",
            create=False,
            base_rev=doc["rev"],
            frontmatter=doc["frontmatter"],
            body=doc["body"],
        )
        self.assertEqual(second["rev"], doc["rev"])

        # 未知键行原样保留
        self.assertIn("x_custom: keep me", doc["raw"])

    def test_quoted_looking_strings_survive(self) -> None:
        """T3-1：数字/布尔样字符串加引号后仍读回字符串。"""
        self.store.save_document(
            "技能/引号.md",
            create=True,
            frontmatter={"id": "skill_q", "kind": "skill", "title": "3"},
            body="正文。",
        )
        doc = self.store.read_document("技能/引号.md")
        self.assertEqual(doc["frontmatter"]["title"], "3")
        self.assertIsInstance(doc["frontmatter"]["title"], str)
        self.assertIn('title: "3"', doc["raw"])

    def test_atomic_write_failure_keeps_original(self) -> None:
        """T3-2：写入失败时原文件不变，且不残留临时文件。"""
        _write(self.root, "技能/原.md", "---\nid: skill_keep\n---\n原始正文。")
        before = self.store.read_document("技能/原.md")

        with mock.patch(
            "astrbot_plugin_Firefly.core.materials.role_store.os.replace",
            side_effect=OSError("boom"),
        ):
            with self.assertRaises(RoleStoreError):
                self.store.save_document(
                    "技能/原.md",
                    create=False,
                    base_rev=before["rev"],
                    frontmatter={"id": "skill_keep"},
                    body="新正文。",
                )

        after = self.store.read_document("技能/原.md")
        self.assertEqual(after["rev"], before["rev"])
        self.assertEqual(after["body"], "原始正文。")
        leftovers = [
            p.name for p in (self.root / "技能").iterdir() if p.name != "原.md"
        ]
        self.assertEqual(leftovers, [])

    def test_rejects_stale_base_rev(self) -> None:
        """T3-3：base_rev 不符即拒绝。"""
        _write(self.root, "技能/锁.md", "---\nid: skill_lock\n---\n正文。")
        with self.assertRaises(RoleStoreError) as ctx:
            self.store.save_document(
                "技能/锁.md",
                create=False,
                base_rev="deadbeefdeadbeef",
                frontmatter={"id": "skill_lock"},
                body="新正文。",
            )
        self.assertIn("外部修改", str(ctx.exception))

    def test_rejects_create_on_existing_and_overwrite_on_missing(self) -> None:
        """T3-4：create=true 已存在拒绝；create=false 不存在拒绝。"""
        _write(self.root, "技能/已存在.md", "---\nid: skill_exists\n---\n正文。")
        with self.assertRaises(RoleStoreError):
            self.store.save_document(
                "技能/已存在.md",
                create=True,
                frontmatter={"id": "skill_new"},
                body="正文。",
            )
        with self.assertRaises(RoleStoreError):
            self.store.save_document(
                "技能/不存在.md",
                create=False,
                base_rev="x",
                frontmatter={"id": "skill_missing"},
                body="正文。",
            )

    def test_id_uniqueness_and_stem_derivation(self) -> None:
        """T3-5：ID 重复拒绝；留空按 stem 生成并同样校验。"""
        _write(self.root, "技能/一号.md", "---\nid: skill_one\n---\n正文。")
        with self.assertRaises(RoleStoreError) as ctx:
            self.store.save_document(
                "技能/二号.md",
                create=True,
                frontmatter={"id": "skill_one"},
                body="正文。",
            )
        self.assertIn("已被占用", str(ctx.exception))

        result = self.store.save_document(
            "技能/三号.md",
            create=True,
            frontmatter={},
            body="正文。",
        )
        self.assertEqual(result["id"], "三号")
        self.assertTrue((self.root / "技能" / "三号.md").is_file())

        with self.assertRaises(RoleStoreError) as ctx:
            self.store.save_document(
                "技能/四号.md",
                create=True,
                frontmatter={"id": "bad id!"},
                body="正文。",
            )
        self.assertIn("ID 不合法", str(ctx.exception))

    def test_rejects_empty_body_and_oversize_and_hidden_and_non_md(self) -> None:
        """T3-6：空正文 / 超大小 / 超深度 / 非 .md / 隐藏前缀全部拒绝。"""
        with self.assertRaises(RoleStoreError) as ctx:
            self.store.save_document(
                "技能/空.md",
                create=True,
                frontmatter={"id": "skill_empty"},
                body="   \n ",
            )
        self.assertIn("正文不能为空", str(ctx.exception))

        with self.assertRaises(RoleStoreError) as ctx:
            self.store.save_document(
                "技能/超大.md",
                create=True,
                frontmatter={"id": "skill_big"},
                body="中" * (MAX_DOCUMENT_BYTES // 3 + 100),
            )
        self.assertIn("KiB", str(ctx.exception))

        deep = "/".join([*[f"d{i}" for i in range(MAX_PATH_DEPTH)], "x.md"])
        for bad in (deep, "技能/非md.txt", "_隐藏.md", "技能/.隐藏.md"):
            with self.assertRaises(RoleStoreError, msg=bad):
                self.store.save_document(
                    bad, create=True, frontmatter={"id": "skill_x"}, body="正文。"
                )

    def test_rejects_unknown_keys_in_structured_payload(self) -> None:
        """结构化字段携带未知键时拒绝，避免静默写入非白名单键。"""
        with self.assertRaises(RoleStoreError) as ctx:
            self.store.save_document(
                "技能/未知.md",
                create=True,
                frontmatter={"id": "skill_u", "x_unknown": 1},
                body="正文。",
            )
        self.assertIn("未知键", str(ctx.exception))

    def test_raw_mode_reparses_server_side(self) -> None:
        """全文模式：服务端重新解析并规范化，未知键按 raw 保留。"""
        result = self.store.save_document(
            "技能/全文.md",
            create=True,
            raw='---\nid: skill_raw\nz_extra: "保留我"\ntitle: "a: b"\n---\n\n正文内容。\n',
        )
        doc = self.store.read_document("技能/全文.md")
        self.assertEqual(result["id"], "skill_raw")
        self.assertEqual(doc["frontmatter"]["title"], "a: b")
        self.assertEqual(doc["unknown_keys"], ["z_extra"])
        self.assertEqual(doc["body"], "正文内容。")
        self.assertIn('z_extra: "保留我"', doc["raw"])

    def test_raw_and_structured_are_mutually_exclusive(self) -> None:
        """raw 与结构化字段同时提交时拒绝。"""
        with self.assertRaises(RoleStoreError):
            self.store.save_document(
                "技能/冲突.md",
                create=True,
                frontmatter={"id": "skill_c"},
                body="正文。",
                raw="---\nid: skill_c\n---\n正文。",
            )

    def test_delete_guards(self) -> None:
        """T3-7（前两重）：rev 与 confirm_name 任一不符即拒绝。"""
        _write(self.root, "技能/删除.md", "---\nid: skill_del\n---\n正文。")
        doc = self.store.read_document("技能/删除.md")

        with self.assertRaises(RoleStoreError) as ctx:
            self.store.delete_document(
                "技能/删除.md", base_rev="deadbeefdeadbeef", confirm_name="删除.md"
            )
        self.assertIn("外部修改", str(ctx.exception))

        with self.assertRaises(RoleStoreError) as ctx:
            self.store.delete_document(
                "技能/删除.md", base_rev=doc["rev"], confirm_name="别的名字.md"
            )
        self.assertIn("确认名称", str(ctx.exception))

        _write(self.root, "隐藏/_不许删.md", "---\nid: skill_h\n---\n正文。")
        hidden = self.store.read_document("隐藏/_不许删.md")
        with self.assertRaises(RoleStoreError):
            self.store.delete_document(
                "隐藏/_不许删.md", base_rev=hidden["rev"], confirm_name="_不许删.md"
            )

        result = self.store.delete_document(
            "技能/删除.md", base_rev=doc["rev"], confirm_name="删除.md"
        )
        self.assertEqual(result["id"], "skill_del")
        self.assertFalse((self.root / "技能" / "删除.md").exists())

    def test_rename_keeps_content_and_rev(self) -> None:
        """重命名/移动：内容与 rev 不变，仅路径改变。"""
        _write(
            self.root, "技能/原名.md", "---\nid: skill_move\nkind: skill\n---\n正文。"
        )
        _write(
            self.root, "人物关系/占位.md", "---\nid: npc_keep\nkind: lore\n---\n占位。"
        )
        before = self.store.read_document("技能/原名.md")

        result = self.store.rename_document(
            "技能/原名.md", base_rev=before["rev"], target="人物关系/新名.md"
        )

        self.assertFalse((self.root / "技能" / "原名.md").exists())
        after = self.store.read_document("人物关系/新名.md")
        self.assertEqual(after["rev"], before["rev"])
        self.assertEqual(after["body"], "正文。")
        self.assertEqual(result["path"], "人物关系/新名.md")
        self.assertEqual(result["id"], "skill_move")

    def test_rename_guards(self) -> None:
        """重命名守卫：rev 不符 / 目标已存在 / 目标目录不存在 / 同路径 / 隐藏前缀。"""
        _write(self.root, "技能/a.md", "---\nid: skill_a\n---\n正文。")
        _write(self.root, "技能/b.md", "---\nid: skill_b\n---\n正文。")
        doc = self.store.read_document("技能/a.md")

        with self.assertRaises(RoleStoreError) as ctx:
            self.store.rename_document(
                "技能/a.md", base_rev="deadbeefdeadbeef", target="技能/c.md"
            )
        self.assertIn("外部修改", str(ctx.exception))

        with self.assertRaises(RoleStoreError) as ctx:
            self.store.rename_document(
                "技能/a.md", base_rev=doc["rev"], target="技能/b.md"
            )
        self.assertIn("已存在", str(ctx.exception))

        with self.assertRaises(RoleStoreError) as ctx:
            self.store.rename_document(
                "技能/a.md", base_rev=doc["rev"], target="没有这个目录/c.md"
            )
        self.assertIn("目录不存在", str(ctx.exception))

        with self.assertRaises(RoleStoreError):
            self.store.rename_document(
                "技能/a.md", base_rev=doc["rev"], target="技能/a.md"
            )

        with self.assertRaises(RoleStoreError) as ctx:
            self.store.rename_document(
                "技能/a.md", base_rev=doc["rev"], target="技能/_隐藏.md"
            )
        self.assertIn("隐藏前缀", str(ctx.exception))

        # 所有失败路径都不应动到原文件
        self.assertTrue((self.root / "技能" / "a.md").exists())

    def test_rename_revalidates_derived_id(self) -> None:
        """无显式 id 时改名会改派生 id，需重新做唯一性校验。"""
        _write(self.root, "技能/一号.md", "---\nkind: skill\n---\n正文。")
        _write(self.root, "技能/三号.md", "---\nkind: skill\n---\n正文。")
        _write(self.root, "人物关系/占位.md", "---\nkind: lore\n---\n正文。")
        doc = self.store.read_document("技能/一号.md")

        with self.assertRaises(RoleStoreError) as ctx:
            self.store.rename_document(
                "技能/一号.md", base_rev=doc["rev"], target="人物关系/三号.md"
            )
        self.assertIn("已被占用", str(ctx.exception))
        self.assertTrue((self.root / "技能" / "一号.md").exists())

        result = self.store.rename_document(
            "技能/一号.md", base_rev=doc["rev"], target="人物关系/二号.md"
        )
        self.assertEqual(result["id"], "二号")


class TestReadDocument(unittest.TestCase):
    """T2-4：单文件读取结构。"""

    def setUp(self) -> None:
        """准备临时 role 目录与样例文件。"""
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.body = "## 机制\n她会尝试讲笑话。"
        self.raw = f'---\nid: skill_humor\ntitle: "冷场: 讲笑话"\nx_custom: keep\n---\n{self.body}'
        _write(self.root, "技能/讲笑话.md", self.raw)
        self.store = RoleStore(self.root)

    def tearDown(self) -> None:
        """清理临时目录。"""
        self._tmp.cleanup()

    def test_read_structure(self) -> None:
        """返回路径、版本号、mtime、大小、已知键、未知键、正文与原文。"""
        doc = self.store.read_document("技能/讲笑话.md")
        self.assertEqual(doc["path"], "技能/讲笑话.md")
        self.assertEqual(len(doc["rev"]), 16)
        self.assertEqual(doc["size"], (self.root / "技能/讲笑话.md").stat().st_size)
        self.assertGreater(doc["mtime"], 0)
        self.assertEqual(
            doc["raw"], (self.root / "技能/讲笑话.md").read_text(encoding="utf-8")
        )
        self.assertEqual(doc["body"], self.body)
        self.assertEqual(
            doc["frontmatter"], {"id": "skill_humor", "title": "冷场: 讲笑话"}
        )
        self.assertEqual(doc["unknown_keys"], ["x_custom"])

    def test_rev_changes_with_content(self) -> None:
        """内容变更后版本号必须变化（乐观锁前提）。"""
        first = self.store.read_document("技能/讲笑话.md")["rev"]
        _write(self.root, "技能/讲笑话.md", self.raw + "\n追加。")
        self.assertNotEqual(self.store.read_document("技能/讲笑话.md")["rev"], first)

    def test_read_errors(self) -> None:
        """非法路径与不存在的文件都返回 RoleStoreError。"""
        for bad in ("../secret.md", "技能/不存在.md", "技能/notes.txt", ""):
            with self.assertRaises(RoleStoreError, msg=bad):
                self.store.read_document(bad)

    def test_hidden_file_is_readable(self) -> None:
        """隐藏前缀可读可展示（仅写路径拒绝）。"""
        _write(self.root, "_备注.md", "---\nid: note\n---\n备注内容。")
        self.assertEqual(
            self.store.read_document("_备注.md")["frontmatter"], {"id": "note"}
        )


if __name__ == "__main__":
    unittest.main()
