"""资料解析工具：front-matter 解析、HTML 注释清除、分节。

解析实现为最小可用版本（无第三方依赖），仅支持 `key: value` 与 `key: [a, b]` 形式。
解析边界：

- `#` 仅在行首视为注释，行内的 `#` 属于值本身。
- 标量若被成对引号包裹，视为"显式字符串"：剥离引号并反转义后不再做类型推断；
  这样 `role_store` 的规范化序列化器写出的文本才能被无损读回。
- 列表元素支持引号，逗号切分是引号感知的（被引号包裹的逗号不切分）。

够用且保证 core 层可在任意 Python 3.10+ 环境运行。
"""

from __future__ import annotations

import re
from typing import Any

_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_HEADING_RE = re.compile(r"^##\s+(.+)$", re.MULTILINE)

# 引号内的转义序列；未列出的形式按字面保留
_ESCAPE_RE = re.compile(r"\\(.)")
_ESCAPE_MAP = {
    "\\": "\\",
    '"': '"',
    "'": "'",
    "n": "\n",
    "t": "\t",
    "r": "\r",
}


# 字符串形式的布尔真值（与插件原有配置解析保持一致，勿随意增减）
_TRUE_WORDS = ("1", "true", "yes", "on")


def strip_html_comments(text: str) -> str:
    """去除 HTML 注释（占位文件中用于标明作用的说明会被去掉）。"""
    return _HTML_COMMENT_RE.sub("", text)


def coerce_int(
    value: Any,
    fallback: int,
    warnings: list[str] | None = None,
    field: str = "",
) -> int:
    """把 front-matter 值转为 int，失败时降级为 fallback。

    front-matter 是手写文本，脏值（`tier: abc`、`priority: high`）不应中断整次加载：
    无法转换时返回 fallback，并在提供 warnings 时记录一条告警。

    Args:
        value: 原始值，可为 None / 数字 / 字符串 / 列表。
        fallback: 转换失败时的回退值。
        warnings: 告警收集列表，None 表示静默降级。
        field: 告警中显示的字段标识。

    Returns:
        转换后的整数，或 fallback。
    """
    if value is None:
        return fallback
    try:
        return int(value)
    except (TypeError, ValueError):
        if warnings is not None:
            warnings.append(f"字段 {field} 不是整数（{value!r}），已回退为 {fallback}")
        return fallback


def coerce_float(
    value: Any,
    fallback: float,
    warnings: list[str] | None = None,
    field: str = "",
) -> float:
    """把配置/元数据值转为 float，失败时降级为 fallback。

    与 `coerce_int` 同一套约定：缺失（None）静默回退，脏值回退并告警。

    Args:
        value: 原始值，可为 None / 数字 / 字符串。
        fallback: 转换失败时的回退值。
        warnings: 告警收集列表，None 表示静默降级。
        field: 告警中显示的字段标识。

    Returns:
        转换后的浮点数，或 fallback。
    """
    if value is None:
        return fallback
    try:
        return float(value)
    except (TypeError, ValueError):
        if warnings is not None:
            warnings.append(f"字段 {field} 不是数字（{value!r}），已回退为 {fallback}")
        return fallback


def coerce_bool(
    value: Any,
    fallback: bool,
    warnings: list[str] | None = None,
    field: str = "",
) -> bool:
    """把配置/元数据值转为 bool，无法判断时降级为 fallback。

    语义（与插件原有的配置解析保持一致，勿随意更改）：
    - 布尔值直接返回；
    - 字符串一律映射为 True/False（真值表：1/true/yes/on），**不会**回退到
      fallback —— 因此 `"maybe"` 得到 False 而不是默认值；
    - 缺失（None）静默回退；
    - 其它类型回退并告警。

    Args:
        value: 原始值。
        fallback: 无法判断时的回退值。
        warnings: 告警收集列表，None 表示静默降级。
        field: 告警中显示的字段标识。

    Returns:
        转换后的布尔值，或 fallback。
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in _TRUE_WORDS
    if value is None:
        return fallback
    if warnings is not None:
        warnings.append(f"字段 {field} 不是布尔值（{value!r}），已回退为 {fallback}")
    return fallback


def coerce_str_list(value: Any) -> list[str]:
    """把 front-matter 值规范为字符串列表。

    None / 空值 → []；标量 → [标量]；列表 → 逐项转字符串。
    否则 `tags: 战斗` 会被 `tuple("战斗")` 拆成 ("战", "斗")，
    `tags: [0]` 会在 join 时抛 TypeError。

    Args:
        value: 原始值。

    Returns:
        字符串列表（可能为空）。
    """
    if not value:
        return []
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value]
    return [str(value)]


def _unescape(text: str) -> str:
    """还原引号内的转义序列，未知序列按字面保留。"""
    return _ESCAPE_RE.sub(
        lambda m: _ESCAPE_MAP.get(m.group(1), "\\" + m.group(1)),
        text,
    )


def _unquote_scalar(raw: str) -> str | None:
    """成对引号包裹时返回剥离引号并反转义的内容，否则返回 None。"""
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "\"'":
        return _unescape(raw[1:-1])
    return None


def _split_list_items(inner: str) -> list[str]:
    """按逗号切分列表内容，忽略引号内的逗号。"""
    items: list[str] = []
    buffer: list[str] = []
    quote: str | None = None
    escaped = False
    for char in inner:
        if quote:
            buffer.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
        elif char in "\"'":
            quote = char
            buffer.append(char)
        elif char == ",":
            items.append("".join(buffer))
            buffer = []
        else:
            buffer.append(char)
    items.append("".join(buffer))
    return items


def _parse_list_item(item: str) -> str:
    """解析单个列表元素：剥离成对引号；列表元素一律保持字符串。"""
    item = item.strip()
    unquoted = _unquote_scalar(item)
    return item if unquoted is None else unquoted


def _parse_value(raw: str) -> Any:
    """解析 front-matter 的值：列表 / 布尔 / 整数 / 浮点 / 字符串。"""
    raw = raw.strip()
    if raw.startswith("[") and raw.endswith("]"):
        inner = raw[1:-1].strip()
        if not inner:
            return []
        return [
            value
            for value in (_parse_list_item(item) for item in _split_list_items(inner))
            if value
        ]
    unquoted = _unquote_scalar(raw)
    if unquoted is not None:
        return unquoted
    low = raw.lower()
    if low in ("true", "false"):
        return low == "true"
    if low in ("null", "none"):
        return None
    try:
        return int(raw)
    except ValueError:
        pass
    try:
        return float(raw)
    except ValueError:
        return raw


def parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """解析文本开头的 `---` front-matter 块，返回 (元数据, 正文)。

    没有 front-matter 时返回 ({}, 原文)。
    以下情况跳过该行：空行、以 `#` 开头（仅行首算注释）、不含 `:` 的行。
    """
    lines = text.lstrip("\ufeff").splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text.strip()

    meta: dict[str, Any] = {}
    end_index: int | None = None
    for i in range(1, len(lines)):
        line = lines[i]
        stripped = line.strip()
        if stripped == "---":
            end_index = i
            break
        if not stripped or stripped.startswith("#") or ":" not in line:
            continue
        key, _, value = line.partition(":")
        meta[key.strip()] = _parse_value(value.strip())

    if end_index is None:
        return meta, text.strip()
    body = "\n".join(lines[end_index + 1 :]).strip()
    return meta, body


def extract_frontmatter_lines(text: str) -> dict[str, list[str]]:
    """按原样提取 front-matter 的每一行，按键分组（保留原始书写形式）。

    与 `parse_frontmatter` 使用同一套行规则：空行、以 `#` 开头（仅行首）、不含 `:`
    的行都会被跳过。用于序列化时"未知键按原行保留"。

    Args:
        text: 原始文件内容。

    Returns:
        键 → 原始行列表（同一键出现多次时按出现顺序保留）。
    """
    lines = text.lstrip("\ufeff").splitlines()
    if not lines or lines[0].strip() != "---":
        return {}

    grouped: dict[str, list[str]] = {}
    for line in lines[1:]:
        if line.strip() == "---":
            break
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or ":" not in line:
            continue
        key = line.partition(":")[0].strip()
        grouped.setdefault(key, []).append(line.rstrip())
    return grouped


def _split_sections(body: str) -> list[tuple[str, str]]:
    """按 `## 标题` 将正文拆分为 (标题, 小节文本)。"""
    matches = list(_HEADING_RE.finditer(body))
    sections: list[tuple[str, str]] = []
    for idx, match in enumerate(matches):
        title = match.group(1).strip()
        start = match.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(body)
        sections.append((title, body[start:end].strip()))
    return sections
