"""资料解析工具：front-matter 解析、HTML 注释清除、分节。

解析实现为最小可用版本（无第三方依赖）：仅支持 `key: value` 与 `key: [a, b]` 形式，
以 `#` 开头的行视为注释。够用且保证 core 层可在任意 Python 3.10+ 环境运行。
"""

from __future__ import annotations

import re
from typing import Any

_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_HEADING_RE = re.compile(r"^##\s+(.+)$", re.MULTILINE)


def strip_html_comments(text: str) -> str:
    """去除 HTML 注释（占位文件中用于标明作用的说明会被去掉）。"""
    return _HTML_COMMENT_RE.sub("", text)


def _parse_value(raw: str) -> Any:
    """解析 front-matter 的值：列表 / 布尔 / 整数 / 浮点 / 字符串。"""
    raw = raw.strip()
    if raw.startswith("[") and raw.endswith("]"):
        inner = raw[1:-1].strip()
        if not inner:
            return []
        return [item.strip().strip("\"'") for item in inner.split(",") if item.strip()]
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
