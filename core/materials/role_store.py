"""role/ 目录的只读访问层：路径校验、目录树、单文件读取。

设计要点：

- **纯逻辑**：不 import astrbot / quart，可在任意 Python 3.10+ 环境单测。
- **文件系统是唯一事实来源**：registry 会静默丢弃"ID 冲突 / 读失败"的文件，
  这些恰恰是用户最需要看到并修复的，因此树不依赖 registry 索引构建。
- **status 是健康度标记**（按优先级择一）：
  `hidden` → `read_error` → `duplicate_id` → `empty_body` → `registered`。
  其中 `empty_body` 的判定是"剥离 front-matter 与 HTML 注释后正文为空"，
  与 registry 的告警口径一致（registry 仅对 tier≤2 告警，且条目仍留在索引中）。
- **不可管理的路径不入树**：名称不符合路径规则或为链接（符号链接 / 目录联接）的文件
  会被跳过并记入 warnings，以保证树中每个 path 都能被 `read_document` 读取。
"""

from __future__ import annotations

import hashlib
import os
import re
import stat
import tempfile
import unicodedata
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from .. import consts
from .parsers import (
    coerce_int,
    coerce_str_list,
    extract_frontmatter_lines,
    parse_frontmatter,
    strip_html_comments,
)
from .tier_rules import infer_tier_kind

# 路径深度上限（含文件名）
MAX_PATH_DEPTH = 8

# 单文件体积上限（序列化后的 UTF-8 字节数）
MAX_DOCUMENT_BYTES = 256 * 1024

# 资料 id 白名单（与 registry 的 id 语义一致：字母数字下划线连字符与 CJK）
_ID_RE = re.compile(r"^[A-Za-z0-9_\-\u4e00-\u9fff]{1,64}$")

# 序列化时已知键的固定顺序
_FRONTMATTER_KEY_ORDER = (
    consts.FM_KEY_TYPE,
    consts.FM_KEY_KIND,
    consts.FM_KEY_TITLE,
    consts.FM_KEY_ID,
    consts.FM_KEY_TAGS,
    consts.FM_KEY_KEYWORDS,
    consts.FM_KEY_PATTERNS,
    consts.FM_KEY_TIER,
    consts.FM_KEY_PRIORITY,
    consts.FM_KEY_DEFAULT_TTL,
)

# 路径段禁用字符：Windows 非法字符 + 控制字符（含 NUL、驱动/ADS 用的冒号）
_INVALID_SEGMENT_RE = re.compile(r'[\x00-\x1f<>:"/\\|?*]')

# 路径段长度上限
_MAX_SEGMENT_LENGTH = 64

# 触发加引号的字符（含引号本身，否则值会被解析器当成引号语法）
_QUOTE_TRIGGER_CHARS = (":", "#", "[", "]", '"', "'")

# 允许写入的扩展名
_MD_SUFFIX = ".md"

# Windows 保留设备名（含扩展名形式，如 CON.md）
_WINDOWS_RESERVED = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{i}" for i in range(1, 10)}
    | {f"LPT{i}" for i in range(1, 10)}
)

# front-matter 已知键（由序列化顺序派生，避免两处维护）
_KNOWN_FRONTMATTER_KEYS = frozenset(_FRONTMATTER_KEY_ORDER)


class RoleStoreError(Exception):
    """路径非法、文件不存在、读取失败或写入被守卫拒绝。"""


def _revision(raw: str) -> str:
    """内容版本号：sha256(文本)[:16]，用于乐观锁。

    注意 `read_document` 用文本模式读取，Windows 的 CRLF 会被归一为 LF，
    因此 rev 表达的是"换行无关的内容指纹"；`size` 则是磁盘上的实际字节数。
    两者基准不同是有意为之：跨平台编辑时 rev 不会因换行风格抖动，
    而 size 用于界面展示真实占用。
    """
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _escape_scalar(text: str) -> str:
    """转义引号内的特殊字符（与 parsers._ESCAPE_MAP 对称）。"""
    return (
        text.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\t", "\\t")
        .replace("\r", "\\r")
    )


def _needs_quote(text: str, *, in_list: bool = False) -> bool:
    """判断值是否需要加引号才能被解析器无损读回。

    除设计稿列出的 `:` `#` `[` `]` 与首尾空格外，还必须覆盖：
    引号本身（否则会被当成引号语法）、换行/制表符、逗号（列表元素分隔符），
    以及会被解析器强制类型推断的字符串（`3`、`true`、`null` 等）——
    否则字符串 `"3"` 往返后会变成整数 3。
    """
    if text == "" or text != text.strip():
        return True
    if any(char in text for char in _QUOTE_TRIGGER_CHARS):
        return True
    if in_list and "," in text:
        return True
    if "\n" in text or "\t" in text or "\r" in text:
        return True
    low = text.lower()
    if low in ("true", "false", "null", "none"):
        return True
    try:
        int(text)
        return True
    except ValueError:
        pass
    try:
        float(text)
        return True
    except ValueError:
        return False


def _format_scalar(value: Any) -> str:
    """把标量格式化为 front-matter 值文本。"""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value)
    return '"' + _escape_scalar(text) + '"' if _needs_quote(text) else text


def _format_list(values: Sequence[Any]) -> str:
    """把列表格式化为 `[a, b]`（元素一律为字符串语义）。"""
    items: list[str] = []
    for value in values:
        text = "true" if value is True else "false" if value is False else str(value)
        items.append(
            '"' + _escape_scalar(text) + '"'
            if _needs_quote(text, in_list=True)
            else text
        )
    return "[" + ", ".join(items) + "]"


def _format_value(value: Any) -> str:
    """按类型选择标量或列表格式。"""
    if isinstance(value, (list, tuple)):
        return _format_list(value)
    return _format_scalar(value)


def serialize_document(
    frontmatter: dict[str, Any],
    body: str,
    unknown_lines: Sequence[str] = (),
) -> str:
    """把 frontmatter + body 序列化为可被 `parse_frontmatter` 无损读回的文本。

    规则：已知键按固定顺序输出；空值（None/""/[]）不写；未知键按原行追加；
    正文两侧空白被裁剪；换行为 LF、编码 UTF-8。

    Args:
        frontmatter: 已知键 → 值。
        body: 正文（不含 front-matter）。
        unknown_lines: 需原样保留的未知键行。

    Returns:
        完整文件文本（以换行结尾）。
    """
    lines = ["---"]
    for key in _FRONTMATTER_KEY_ORDER:
        if key not in frontmatter:
            continue
        value = frontmatter[key]
        if value is None or value == "" or value == [] or value == ():
            continue
        lines.append(f"{key}: {_format_value(value)}")
    lines.extend(unknown_lines)
    lines.append("---")
    return "\n".join(lines) + "\n\n" + body.strip() + "\n"


def _atomic_write(path: Path, text: str) -> None:
    """同目录临时文件 + os.replace 原子替换，避免出现半文件。

    临时文件名以 `.` 开头且后缀非 `.md`，即使进程崩溃残留也不会进入资料树。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        newline="\n",
        dir=str(path.parent),
        prefix="." + path.name + ".",
        suffix=".tmp",
        delete=False,
    )
    temp_path = Path(handle.name)
    try:
        with handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    except BaseException:
        temp_path.unlink(missing_ok=True)
        raise


def _has_hidden_segment(rel_posix: str) -> bool:
    """任一路径段以 `_`/`.` 开头即视为隐藏（与 registry 的忽略规则一致）。"""
    return any(
        segment.startswith(consts.IGNORED_PREFIXES)
        for segment in rel_posix.split("/")
        if segment
    )


def _is_link(path: Path) -> bool:
    """判断路径是否为符号链接或 Windows 目录联接（junction）。

    Windows 上 `Path.is_symlink()` 对 junction 返回 False，但 junction 同样可以
    把路径指向 role/ 之外，因此必须额外检查重解析点属性。
    """
    try:
        if path.is_symlink():
            return True
        attributes = getattr(path.lstat(), "st_file_attributes", 0)
    except OSError:
        return False
    return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))


class RoleStore:
    """role/ 目录的只读访问层。"""

    def __init__(self, root: Path) -> None:
        """初始化。

        Args:
            root: role/ 资料根目录。
        """
        self._root = Path(root)

    # ------------------------------------------------------------------
    # 路径校验
    # ------------------------------------------------------------------

    def validate_rel_path(self, rel: str, *, is_dir: bool = False) -> str:
        """校验并规范化 role/ 内的相对路径。

        校验内容：绝对路径 / 盘符 / UNC、空段与 `..`、段级禁用字符（NFC 规范化后）、
        段长与首尾空白/点、深度上限、仅 `.md`（文件），以及父目录非链接与
        resolve() 后的根目录包含性（防符号链接 / Windows 目录联接逃逸）。

        注意：`_`/`.` 前缀在这里是允许的（面板可读可展示），写路径的隐藏前缀拒绝
        由保存接口自行叠加。

        Args:
            rel: 相对 role/ 的路径，允许使用 `/` 或 `\\` 分隔。
            is_dir: 目标是否为目录；为 False 时要求最后一段是 `.md`。

        Returns:
            规范化后的 posix 相对路径。

        Raises:
            RoleStoreError: 路径为空或违反上述任一规则。
        """
        if not isinstance(rel, str) or not rel.strip():
            raise RoleStoreError("路径不能为空")

        raw = rel.strip().replace("\\", "/")
        if raw.startswith("/") or ":" in raw:
            raise RoleStoreError("不允许绝对路径、盘符或 UNC 路径")

        parts = raw.split("/")
        if is_dir and parts and parts[-1] == "":
            parts = parts[:-1]
        if not parts or any(part == "" for part in parts):
            raise RoleStoreError("路径包含空段")
        if len(parts) > MAX_PATH_DEPTH:
            raise RoleStoreError(f"路径深度超过 {MAX_PATH_DEPTH} 层")

        segments = [
            self._validate_segment(
                part, allow_md=not is_dir and index == len(parts) - 1
            )
            for index, part in enumerate(parts)
        ]

        for index in range(len(segments) - 1):
            if _is_link(self._root.joinpath(*segments[: index + 1])):
                raise RoleStoreError("路径包含符号链接目录")
        if _is_link(self._root.joinpath(*segments)):
            raise RoleStoreError("目标本身是链接，不受支持")
        try:
            self._root.joinpath(*segments).resolve(strict=False).relative_to(
                self._root.resolve()
            )
        except ValueError as exc:
            raise RoleStoreError("路径越出 role/ 目录") from exc

        return "/".join(segments)

    @staticmethod
    def _validate_segment(segment: str, *, allow_md: bool) -> str:
        """校验单个路径段（NFC 规范化 + 禁用字符 + 保留名），返回规范化后的段。

        这里用"禁用字符"而不是窄白名单：role/ 里存在含 `•` 等 Unicode 标点的真实
        文件名，窄白名单会把它们误判为非法并导致文件在面板中消失。路径安全实际依赖
        的是 `/ \\ : ..`、控制字符、保留名、长度与首尾空白/点这些约束。

        目录段允许出现 `.`（不要求"目录无扩展名"）：目录与文件由文件系统类型区分，
        文件段必须且只能以 `.md` 结尾，两者不会混淆。
        """
        normalized = unicodedata.normalize("NFC", segment)
        stem = normalized
        if allow_md:
            if not normalized.endswith(_MD_SUFFIX) or normalized == _MD_SUFFIX:
                raise RoleStoreError("仅允许 .md 文件")
            stem = normalized[: -len(_MD_SUFFIX)]
        if stem in {".", ".."}:
            raise RoleStoreError(f"路径段非法：{segment!r}")
        if not stem or stem != stem.strip() or stem.endswith("."):
            raise RoleStoreError(f"路径段首尾不能是空白或点：{segment!r}")
        if len(stem) > _MAX_SEGMENT_LENGTH:
            raise RoleStoreError(f"路径段超过 {_MAX_SEGMENT_LENGTH} 字符：{segment!r}")
        if _INVALID_SEGMENT_RE.search(stem):
            raise RoleStoreError(f"路径段含非法字符：{segment!r}")
        if stem.split(".")[0].upper() in _WINDOWS_RESERVED:
            raise RoleStoreError(f"路径段是系统保留名：{segment!r}")
        return normalized

    # ------------------------------------------------------------------
    # 单文件读取
    # ------------------------------------------------------------------

    def read_document(self, rel: str) -> dict[str, Any]:
        """读取单个资料文件。

        Args:
            rel: 相对 role/ 的文件路径。

        Returns:
            含 path / rev / mtime / size / frontmatter / unknown_keys / body / raw 的字典。
            `frontmatter` 只含已知键，未知键名由 `unknown_keys` 给出（保存时需原样保留）。
            文本以换行归一方式读取：`raw` 与 `rev` 基于 LF 化文本，`size` 为磁盘字节数。

        Raises:
            RoleStoreError: 路径非法、文件不存在或不是合法 UTF-8 文本。
        """
        rel_posix = self.validate_rel_path(rel, is_dir=False)
        path = self._root.joinpath(*rel_posix.split("/"))
        if not path.is_file():
            raise RoleStoreError(f"文件不存在：{rel_posix}")

        try:
            raw = path.read_text(encoding="utf-8")
            stat = path.stat()
        except (OSError, UnicodeDecodeError) as exc:
            raise RoleStoreError(f"读取失败：{rel_posix}") from exc

        meta, body = parse_frontmatter(raw)
        return {
            "path": rel_posix,
            "rev": _revision(raw),
            "mtime": stat.st_mtime,
            "size": stat.st_size,
            "frontmatter": {
                key: value
                for key, value in meta.items()
                if key in _KNOWN_FRONTMATTER_KEYS
            },
            "unknown_keys": sorted(
                key for key in meta if key not in _KNOWN_FRONTMATTER_KEYS
            ),
            "body": body,
            "raw": raw,
        }

    # ------------------------------------------------------------------
    # 写路径
    # ------------------------------------------------------------------

    def save_document(
        self,
        rel: str,
        *,
        create: bool,
        base_rev: str = "",
        frontmatter: dict[str, Any] | None = None,
        body: str | None = None,
        raw: str | None = None,
    ) -> dict[str, Any]:
        """新建或覆盖写入资料文件（服务端权威：重新解析、规范化、原子落盘）。

        守卫顺序：路径校验 → 隐藏前缀 → 目标存在性 / 乐观锁 → 正文与体积 →
        ID 生成与唯一性 → 序列化 → 原子写。

        未知键的处理：全文模式取 `raw` 中的原行；结构化模式取磁盘原文件中的原行，
        因此不会因为用结构化表单保存而丢掉未知键。

        Args:
            rel: 相对 role/ 的文件路径。
            create: True 表示新建（目标不得存在）；False 表示覆盖（需 base_rev 匹配）。
            base_rev: 覆盖时的内容版本号（乐观锁）。
            frontmatter: 结构化已知键（与 raw 互斥）。
            body: 结构化正文（与 raw 互斥）。
            raw: 完整文件文本（全文模式）。

        Returns:
            含 path / rev / mtime / size / id / effective_tier / effective_kind / created 的字典。

        Raises:
            RoleStoreError: 任一守卫不满足；失败时不触碰原文件。
        """
        rel_posix = self.validate_rel_path(rel, is_dir=False)
        if _has_hidden_segment(rel_posix):
            raise RoleStoreError("隐藏前缀（_ / .）不允许写入")
        if raw is not None and (frontmatter or body is not None):
            raise RoleStoreError("raw 与结构化字段不能同时提交")

        path = self._root.joinpath(*rel_posix.split("/"))
        exists = path.is_file()
        existing: dict[str, Any] | None = None
        if exists:
            if create:
                raise RoleStoreError(f"文件已存在：{rel_posix}")
            existing = self.read_document(rel_posix)
            if base_rev != existing["rev"]:
                raise RoleStoreError("文件已被外部修改，请刷新后重试")
        elif not create:
            raise RoleStoreError(f"文件不存在：{rel_posix}")

        if raw is not None:
            parsed, body_text = parse_frontmatter(raw)
            unknown_source = raw
        else:
            parsed = dict(frontmatter or {})
            body_text = body or ""
            unknown_source = existing["raw"] if existing else ""
            # 未知键只允许经全文模式进来；结构化提交带未知键视为前端错误
            unknown_keys = sorted(
                key for key in parsed if key not in _KNOWN_FRONTMATTER_KEYS
            )
            if unknown_keys:
                raise RoleStoreError(
                    "结构化字段含未知键："
                    + "、".join(unknown_keys)
                    + "（请改用全文模式编辑）"
                )
        unknown_lines = [
            line
            for key, lines in extract_frontmatter_lines(unknown_source).items()
            if key not in _KNOWN_FRONTMATTER_KEYS
            for line in lines
        ]

        written = {key: parsed[key] for key in _FRONTMATTER_KEY_ORDER if key in parsed}

        body_text = body_text.strip()
        if not body_text:
            raise RoleStoreError("正文不能为空")

        auto_tier, auto_kind = infer_tier_kind(rel_posix)
        entry_id = str(written.get(consts.FM_KEY_ID) or path.stem)
        if not _ID_RE.fullmatch(entry_id):
            raise RoleStoreError(
                f"ID 不合法：{entry_id}"
                "（仅允许字母、数字、下划线、连字符与中文，长度 1-64）"
            )
        self._assert_id_available(entry_id, rel_posix)

        text = serialize_document(written, body_text, unknown_lines)
        size = len(text.encode("utf-8"))
        if size > MAX_DOCUMENT_BYTES:
            raise RoleStoreError(f"文件超过 {MAX_DOCUMENT_BYTES // 1024} KiB 上限")

        try:
            _atomic_write(path, text)
        except OSError as exc:
            raise RoleStoreError(f"写入失败：{rel_posix}") from exc
        doc = self.read_document(rel_posix)
        return {
            "path": doc["path"],
            "rev": doc["rev"],
            "mtime": doc["mtime"],
            "size": doc["size"],
            "id": entry_id,
            "effective_tier": coerce_int(
                written.get(consts.FM_KEY_TIER) or auto_tier, auto_tier
            ),
            "effective_kind": str(written.get(consts.FM_KEY_KIND) or auto_kind),
            "created": not exists,
        }

    def delete_document(
        self, rel: str, *, base_rev: str, confirm_name: str
    ) -> dict[str, Any]:
        """硬删除单个资料文件（不可恢复）。

        前两重守卫在此实现：`base_rev` 必须匹配磁盘现值、`confirm_name` 必须与文件名
        逐字符相同。第三重"活跃会话确认"依赖会话状态，由 adapter 层叠加。

        Args:
            rel: 相对 role/ 的文件路径。
            base_rev: 内容版本号（乐观锁）。
            confirm_name: 用户输入的文件名（含扩展名）。

        Returns:
            含 path / name / id 的字典。

        Raises:
            RoleStoreError: 路径非法、隐藏前缀、文件不存在或守卫不满足。
        """
        rel_posix = self.validate_rel_path(rel, is_dir=False)
        if _has_hidden_segment(rel_posix):
            raise RoleStoreError("隐藏前缀（_ / .）不允许删除")
        path = self._root.joinpath(*rel_posix.split("/"))
        doc = self.read_document(rel_posix)
        if base_rev != doc["rev"]:
            raise RoleStoreError("文件已被外部修改，请刷新后重试")
        if confirm_name != path.name:
            raise RoleStoreError("确认名称与文件名不一致")

        entry_id = str(doc["frontmatter"].get(consts.FM_KEY_ID) or path.stem)
        try:
            path.unlink()
        except OSError as exc:
            raise RoleStoreError(f"删除失败：{rel_posix}") from exc
        return {"path": rel_posix, "name": path.name, "id": entry_id}

    def rename_document(
        self, rel: str, *, base_rev: str, target: str
    ) -> dict[str, Any]:
        """重命名 / 移动资料文件（内容与 rev 不变，仅改路径）。

        守卫：源路径合法且存在、`base_rev` 匹配、目标路径合法且不存在、目标父目录
        必须已存在（不自动建目录）、目标 `id` 唯一。文件内容原样搬运，因此不做
        序列化、也不改变内容 rev。

        Args:
            rel: 当前相对路径。
            base_rev: 源文件内容版本号（乐观锁）。
            target: 目标相对路径（含新文件名）。

        Returns:
            含 path / name / id / rev / mtime / size 的字典。

        Raises:
            RoleStoreError: 任一守卫不满足；失败时不触碰原文件。
        """
        source_rel = self.validate_rel_path(rel, is_dir=False)
        target_rel = self.validate_rel_path(target, is_dir=False)
        if _has_hidden_segment(source_rel):
            raise RoleStoreError("隐藏前缀（_ / .）不允许修改")
        if _has_hidden_segment(target_rel):
            raise RoleStoreError("隐藏前缀（_ / .）不允许写入")
        if source_rel == target_rel:
            raise RoleStoreError("目标路径与当前路径相同")

        source = self._root.joinpath(*source_rel.split("/"))
        target_path = self._root.joinpath(*target_rel.split("/"))
        doc = self.read_document(source_rel)
        if base_rev != doc["rev"]:
            raise RoleStoreError("文件已被外部修改，请刷新后重试")
        if target_path.exists():
            raise RoleStoreError(f"目标已存在：{target_rel}")
        if not target_path.parent.is_dir():
            parent = target_rel.rsplit("/", 1)[0] if "/" in target_rel else "role/"
            raise RoleStoreError(f"目标目录不存在：{parent}")

        # 内容不变，但未显式声明 id 时有效 id 会随文件名变化，需重新校验
        new_id = str(doc["frontmatter"].get(consts.FM_KEY_ID) or target_path.stem)
        if not _ID_RE.fullmatch(new_id):
            raise RoleStoreError(
                f"ID 不合法：{new_id}"
                "（仅允许字母、数字、下划线、连字符与中文，长度 1-64）"
            )
        self._assert_id_available(new_id, source_rel)

        try:
            os.replace(source, target_path)
        except OSError as exc:
            raise RoleStoreError(f"重命名失败：{source_rel} → {target_rel}") from exc

        moved = self.read_document(target_rel)
        return {
            "path": moved["path"],
            "name": target_path.name,
            "id": new_id,
            "rev": moved["rev"],
            "mtime": moved["mtime"],
            "size": moved["size"],
        }

    def _assert_id_available(self, entry_id: str, own_path: str) -> None:
        """确保 id 未被其它文件占用（排除自身路径）。

        以文件系统为准（而非 registry 索引）：未收录的重复 id 文件同样会阻止写入，
        避免保存后出现"先加载者胜"的静默丢弃。
        """
        for node in self.list_tree()["entries"]:
            if node["path"] == own_path:
                continue
            if node["id"] == entry_id:
                raise RoleStoreError(f"ID 已被占用：{entry_id}（{node['path']}）")

    # ------------------------------------------------------------------
    # 目录树
    # ------------------------------------------------------------------

    def list_tree(self, include_hidden: bool = False) -> dict[str, Any]:
        """以文件系统为准列举 role/ 下的全部 `.md` 文件及其元数据。

        扫描顺序与 registry 一致（每层按名称排序、目录优先递归、忽略隐藏前缀），
        因此 ID 冲突时"保留先加载者"的判定与运行时一致。

        Args:
            include_hidden: 是否包含 `_`/`.` 前缀的文件与目录（标记为 hidden）。

        Returns:
            含 dirs / entries / warnings / counts 的字典。
        """
        dirs: list[str] = [""]
        listed: list[dict[str, Any]] = []
        warnings: list[str] = []
        seen_ids: dict[str, str] = {}

        def build_entry(
            path: Path, rel_posix: str, depth: int
        ) -> dict[str, Any] | None:
            """构造单个文件的树节点；不可管理时返回 None 并记入 warnings。"""
            if depth > MAX_PATH_DEPTH:
                warnings.append(
                    f"{rel_posix}：路径深度超过 {MAX_PATH_DEPTH} 层，已跳过"
                )
                return None
            try:
                self.validate_rel_path(rel_posix, is_dir=False)
            except RoleStoreError as exc:
                warnings.append(f"{rel_posix}：{exc}，已跳过管理")
                return None

            hidden = _has_hidden_segment(rel_posix)
            auto_tier, auto_kind = infer_tier_kind(rel_posix)
            entry: dict[str, Any] = {
                "path": rel_posix,
                "name": path.name,
                "dir": rel_posix.rsplit("/", 1)[0] if "/" in rel_posix else "",
                "size": 0,
                "mtime": 0.0,
                "rev": "",
                "id": path.stem,
                "title": path.stem,
                "tier": auto_tier,
                "kind": auto_kind,
                "type": None,
                "priority": consts.DEFAULT_PRIORITY,
                "default_ttl": consts.DEFAULT_TTL_MAP.get(auto_kind, 0),
                "tags": [],
                "keywords": [],
                "patterns": [],
                "status": "read_error",
            }

            try:
                raw = path.read_text(encoding="utf-8")
                stat = path.stat()
            except (OSError, UnicodeDecodeError):
                warnings.append(f"读取失败：{rel_posix}")
                return entry

            meta, body = parse_frontmatter(raw)
            kind = str(meta.get(consts.FM_KEY_KIND) or auto_kind)
            entry.update(
                {
                    "size": stat.st_size,
                    "mtime": stat.st_mtime,
                    "rev": _revision(raw),
                    "id": str(meta.get(consts.FM_KEY_ID) or path.stem),
                    "title": str(meta.get(consts.FM_KEY_TITLE) or path.stem),
                    "tier": coerce_int(
                        meta.get(consts.FM_KEY_TIER) or auto_tier,
                        auto_tier,
                        warnings,
                        f"{rel_posix} 的 {consts.FM_KEY_TIER}",
                    ),
                    "kind": kind,
                    "type": meta.get(consts.FM_KEY_TYPE),
                    "priority": coerce_int(
                        meta.get(consts.FM_KEY_PRIORITY) or consts.DEFAULT_PRIORITY,
                        consts.DEFAULT_PRIORITY,
                        warnings,
                        f"{rel_posix} 的 {consts.FM_KEY_PRIORITY}",
                    ),
                    "default_ttl": coerce_int(
                        meta.get(consts.FM_KEY_DEFAULT_TTL)
                        or consts.DEFAULT_TTL_MAP.get(kind, 0),
                        consts.DEFAULT_TTL_MAP.get(kind, 0),
                        warnings,
                        f"{rel_posix} 的 {consts.FM_KEY_DEFAULT_TTL}",
                    ),
                    "tags": coerce_str_list(meta.get(consts.FM_KEY_TAGS)),
                    "keywords": coerce_str_list(meta.get(consts.FM_KEY_KEYWORDS)),
                    "patterns": coerce_str_list(meta.get(consts.FM_KEY_PATTERNS)),
                }
            )

            if hidden:
                entry["status"] = "hidden"
            elif not strip_html_comments(body).strip():
                entry["status"] = "empty_body"
            elif entry["id"] in seen_ids:
                entry["status"] = "duplicate_id"
                warnings.append(
                    f"资料 ID 冲突：{entry['id']}（{rel_posix}），"
                    f"保留先加载的版本（{seen_ids[entry['id']]}）"
                )
            else:
                entry["status"] = "registered"
            if not hidden:
                seen_ids.setdefault(entry["id"], rel_posix)
            return entry

        def walk(directory: Path, rel_dir: str, depth: int) -> None:
            try:
                children = sorted(directory.iterdir())
            except OSError:
                warnings.append(f"目录读取失败：{rel_dir or '.'}")
                return
            for child in children:
                rel_posix = f"{rel_dir}/{child.name}" if rel_dir else child.name
                if (
                    child.name.startswith(consts.IGNORED_PREFIXES)
                    and not include_hidden
                ):
                    continue
                if _is_link(child):
                    warnings.append(f"{rel_posix}：符号链接/目录联接不受支持，已跳过")
                    continue
                if child.is_dir():
                    if depth + 1 > MAX_PATH_DEPTH:
                        warnings.append(
                            f"{rel_posix}：目录深度超过 {MAX_PATH_DEPTH} 层，已跳过"
                        )
                        continue
                    dirs.append(rel_posix)
                    walk(child, rel_posix, depth + 1)
                elif child.suffix == _MD_SUFFIX:
                    entry = build_entry(child, rel_posix, depth + 1)
                    if entry is not None:
                        listed.append(entry)

        walk(self._root, "", 0)

        counts: dict[str, int] = {}
        for entry in listed:
            tier_key = str(entry["tier"])
            counts[tier_key] = counts.get(tier_key, 0) + 1
        return {
            "dirs": dirs,
            "entries": listed,
            "warnings": warnings,
            "counts": counts,
        }
