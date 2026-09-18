"""架构约束护栏测试（P0-3）。

用途：把 `PLAN_arch_refactor.md` 中的架构规则固化为可执行断言，
防止重构完成后约束被悄悄破坏。

覆盖规则：
1. `core/` 不得依赖 astrbot —— 领域层必须可脱离 AstrBot 独立运行与单测；
2. `core/` 的 `__init__.py` 只写 docstring，不得 re-export —— 避免制造
   隐式耦合节点（所有子包都必须被显式 import 到具体模块）；
3. `adapter/` 与 `main.py` 中接触 astrbot 内部 API（`astrbot.core.*`）的
   位置必须唯一（P1-2 建立 `adapter/astrbot_compat.py` 后生效）；
4. `core/` 内部模块级 import 不得成环。

实现说明：使用 AST 解析而非字符串匹配，避免注释与字符串中的
"astrbot" 造成误报。
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_CORE_DIR = _PLUGIN_ROOT / "core"
_ADAPTER_DIR = _PLUGIN_ROOT / "adapter"
_MAIN_FILE = _PLUGIN_ROOT / "main.py"

_ASTRBOT_ROOT = "astrbot"
_ASTRBOT_INTERNAL_PREFIX = "astrbot.core"
# 插件自身的 core 绝对路径前缀（用于把绝对导入归一化为 core 相对标识）
_CORE_ABS_PREFIX = "astrbot_plugin_Firefly.core"
# astrbot 内部 API 的唯一合法接触点（P1-2 建立后生效）
_COMPAT_MODULE = _ADAPTER_DIR / "astrbot_compat.py"


def _iter_py_files(root: Path) -> list[Path]:
    """递归收集 Python 文件（跳过 __pycache__）。

    Args:
        root: 起始目录。

    Returns:
        排序后的文件路径列表。
    """
    return sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)


def _absolute_imports(path: Path) -> set[str]:
    """提取文件中的绝对导入模块名（相对导入不计入）。

    Args:
        path: 待解析的 Python 文件。

    Returns:
        绝对导入的模块名集合，例如 ``astrbot.api.event``。
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module)
    return names


def _matches_module_prefix(module: str, prefix: str) -> bool:
    """判断模块名是否等于给定前缀或位于其子模块下。

    两个规则都要求「相等或子模块」语义：只写 ``startswith(f"{prefix}.")``
    会漏掉 ``from astrbot.core import db`` 这类精确包名导入。

    Args:
        module: 待判断的模块名。
        prefix: 目标前缀，如 ``astrbot`` 或 ``astrbot.core``。

    Returns:
        相等或以 ``prefix.`` 开头时返回 True。
    """
    return module == prefix or module.startswith(f"{prefix}.")


def _core_modules() -> dict[Path, tuple[str, list[str]]]:
    """列出 `core/` 下的模块标识与其所属包路径。

    Returns:
        ``{文件路径: (模块标识, 包路径片段)}``。
        ``core/materials/registry.py`` → ``("materials.registry", ["materials"])``；
        ``core/materials/__init__.py`` → ``("materials", ["materials"])``。
        标识相对于 `core/`，因此 `core/registry.py` 的标识为 ``"registry"``。
    """
    modules: dict[Path, tuple[str, list[str]]] = {}
    for path in _iter_py_files(_CORE_DIR):
        parts = list(path.relative_to(_CORE_DIR).with_suffix("").parts)
        is_package = parts[-1] == "__init__"
        if is_package:
            parts = parts[:-1]
        if not parts:
            continue
        package = parts if is_package else parts[:-1]
        modules[path] = (".".join(parts), package)
    return modules


def _normalize_absolute_core(module: str, known: set[str]) -> str | None:
    """把绝对导入的插件路径归一化为 `core/` 相对的模块标识。

    Args:
        module: 绝对导入的模块名，如 ``astrbot_plugin_Firefly.core.materials``。
        known: 全部 `core/` 相对模块标识。

    Returns:
        归一化后的标识；``astrbot_plugin_Firefly.core`` 本身返回空串；
        不属于本插件 `core/` 或指向不存在的模块时返回 None。
    """
    if module == _CORE_ABS_PREFIX:
        return ""
    prefix = f"{_CORE_ABS_PREFIX}."
    if not module.startswith(prefix):
        return None
    rel = module[len(prefix) :]
    return rel if rel in known else None


def _core_dependency_graph() -> dict[str, set[str]]:
    """构建 `core/` 内部的模块级依赖图。

    相对导入与指向本插件 core 的绝对导入都会解析：P3 会重写 import 路径，
    若只认相对形式，由此产生的环会逃过检查。

    Returns:
        邻接表；键与值均为 `core/` 相对的模块标识。
    """
    modules = _core_modules()
    known = {module_id for module_id, _ in modules.values()}
    graph: dict[str, set[str]] = {}

    for path, (module_id, package) in modules.items():
        targets: set[str] = set()
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.level:
                    # 相对导入：level=1 指向当前包，每多一级上溯一层
                    depth = max(len(package) - (node.level - 1), 0)
                    stem = [*package[:depth], *([node.module] if node.module else [])]
                elif node.module:
                    rel = _normalize_absolute_core(node.module, known)
                    if rel is None:
                        continue
                    stem = rel.split(".") if rel else []
                else:
                    continue
                # `from pkg import submodule` 优先指向子模块本身
                for alias in node.names:
                    candidate = ".".join([*stem, alias.name])
                    if candidate in known:
                        targets.add(candidate)
                joined = ".".join(stem)
                if joined in known:
                    targets.add(joined)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    rel = _normalize_absolute_core(alias.name, known)
                    if rel:
                        targets.add(rel)
        graph[module_id] = targets

    return graph


def _find_cycle(graph: dict[str, set[str]]) -> list[str] | None:
    """在依赖图中查找环（深度优先）。

    Args:
        graph: 邻接表。

    Returns:
        环上的模块序列（首尾为同一模块）；无环时返回 None。
    """
    finished: set[str] = set()
    path: list[str] = []
    on_path: set[str] = set()

    def visit(node: str) -> list[str] | None:
        """访问单个节点，发现回边时返回环。"""
        on_path.add(node)
        path.append(node)
        for nxt in sorted(graph.get(node, ())):
            if nxt in on_path:
                return path[path.index(nxt) :] + [nxt]
            if nxt not in finished:
                found = visit(nxt)
                if found is not None:
                    return found
        path.pop()
        on_path.discard(node)
        finished.add(node)
        return None

    for node in sorted(graph):
        if node not in finished:
            found = visit(node)
            if found is not None:
                return found
    return None


class TestCoreLayerIsolation(unittest.TestCase):
    """规则 1 / 2：领域层的依赖与导出约束。"""

    def test_core_does_not_import_astrbot(self) -> None:
        """core/ 下的任何文件都不得 import astrbot。"""
        offenders: list[str] = []
        for path in _iter_py_files(_CORE_DIR):
            for module in _absolute_imports(path):
                if _matches_module_prefix(module, _ASTRBOT_ROOT):
                    offenders.append(f"{path.relative_to(_PLUGIN_ROOT)} → {module}")
        self.assertEqual(
            offenders,
            [],
            "core/ 是纯领域层，不得依赖 astrbot；请把 AstrBot 交互放到 adapter/："
            f"{offenders}",
        )

    def test_core_init_files_are_docstring_only(self) -> None:
        """core/ 的 __init__.py 不得 re-export，避免形成隐式耦合节点。"""
        offenders: list[str] = []
        for path in _iter_py_files(_CORE_DIR):
            if path.name != "__init__.py":
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            has_import = any(
                isinstance(node, (ast.Import, ast.ImportFrom)) for node in tree.body
            )
            if has_import:
                offenders.append(str(path.relative_to(_PLUGIN_ROOT)))
        self.assertEqual(
            offenders,
            [],
            "core/ 的 __init__.py 只应写 docstring；re-export 会隐藏真实依赖关系："
            f"{offenders}",
        )


class TestCoreDependencyGraph(unittest.TestCase):
    """规则 4：领域层内部依赖必须无环。"""

    def test_core_module_dependencies_are_acyclic(self) -> None:
        """core/ 内部模块级 import 不得成环。"""
        graph = _core_dependency_graph()
        cycle = _find_cycle(graph)
        self.assertIsNone(
            cycle,
            f"core/ 内部依赖出现环，请调整模块职责划分：{' → '.join(cycle or [])}",
        )


def _dataclass_field_names(path: Path, class_name: str) -> list[str]:
    """取出指定 dataclass 的字段名（按声明顺序）。

    Args:
        path: 源码文件路径。
        class_name: 目标类名。

    Returns:
        字段名列表；未找到该类时返回空列表。
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            return [
                t.target.id
                for t in node.body
                if isinstance(t, ast.AnnAssign) and isinstance(t.target, ast.Name)
            ]
    return []


def _container_attribute_reads(paths: list[Path]) -> set[str]:
    """收集所有形如 `self._core.X` / `core.X` 的属性读取名。

    用 AST 判定访问链，避免文档字符串里出现同名字样被误判为「已读取」。

    Args:
        paths: 待扫描的源码文件。

    Returns:
        被读取过的属性名集合。
    """
    reads: set[str] = set()
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Attribute):
                continue
            value = node.value
            if isinstance(value, ast.Name) and value.id == "core":
                reads.add(node.attr)
            elif isinstance(value, ast.Attribute) and value.attr == "_core":
                reads.add(node.attr)
    return reads


class TestCoreContainerHasNoDeadFields(unittest.TestCase):
    """规则 5：装配容器只登记会被读取的组件。"""

    def test_every_core_field_is_read_in_production_code(self) -> None:
        """FireflyCore 的每个字段都必须至少被生产代码读取一次。

        否则容器会退化成「看起来有依赖、实际没人用」的清单，
        与 §3.3.1 批评的死配置属于同一类问题。
        """
        fields = _dataclass_field_names(_MAIN_FILE, "FireflyCore")
        self.assertTrue(fields, "未找到 FireflyCore 的字段定义")

        reads = _container_attribute_reads([*_iter_py_files(_ADAPTER_DIR), _MAIN_FILE])
        dead = [name for name in fields if name not in reads]
        self.assertEqual(
            dead,
            [],
            "FireflyCore 存在无人读取的死字段（接入使用或从容器移除）："
            f"{dead}；已读取字段={sorted(reads)}",
        )


class TestAstrBotInternalApiIsolation(unittest.TestCase):
    """规则 3：AstrBot 内部 API 只能有一个接触点。"""

    def test_only_compat_module_touches_astrbot_internal_api(self) -> None:
        """adapter/ 与 main.py 中只有 astrbot_compat.py 可 import astrbot.core.*。"""
        if not _COMPAT_MODULE.is_file():
            self.skipTest("adapter/astrbot_compat.py 尚未建立（P1-2 完成后生效）")

        offenders: list[str] = []
        for path in [*_iter_py_files(_ADAPTER_DIR), _MAIN_FILE]:
            if path == _COMPAT_MODULE:
                continue
            for module in _absolute_imports(path):
                if _matches_module_prefix(module, _ASTRBOT_INTERNAL_PREFIX):
                    offenders.append(f"{path.relative_to(_PLUGIN_ROOT)} → {module}")
        self.assertEqual(
            offenders,
            [],
            "astrbot 内部 API（astrbot.core.*）只允许在 adapter/astrbot_compat.py "
            f"中接触，以便上游变更时集中适配：{offenders}",
        )


if __name__ == "__main__":
    unittest.main()
