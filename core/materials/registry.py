"""资料注册表：分级管理 + 懒加载 + LRU 内容缓存。

特性：
- 4 级 tier 自动推断（按目录路径）
- Tier1/2 启动时立即加载，Tier3/4 首次访问时懒加载
- LRU 内容缓存（默认 50 条），避免高频文件重复读盘
- index_summary() 为 LLM 路由提供轻量摘要
"""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable
from pathlib import Path

from .. import consts
from ..models import LoadReport, MaterialEntry
from .parsers import coerce_int, coerce_str_list, parse_frontmatter, strip_html_comments
from .tier_rules import infer_tier_kind


class MaterialRegistry:
    """资料注册表：管理 MaterialEntry 的索引、加载与缓存。

    - 单线程使用（AstrBot 事件循环）。
    - reload() 失败时回退到上一份索引，避免运行时注入中断。
    """

    def __init__(
        self,
        role_dir: Path,
        cache_size: int = 50,
        default_ttl_lookup: Callable[[str], int] | None = None,
    ) -> None:
        """初始化注册表。

        Args:
            role_dir: role/ 根目录路径。
            cache_size: 内容 LRU 缓存上限（最小 10）。
            default_ttl_lookup: 可选的 kind → 默认 TTL 查询函数（来自插件配置）。
                为 None 时使用 `consts.DEFAULT_TTL_MAP`。每次 `load()` 会重新
                查询一次，因此改配置后调用 `reload()` 即可生效。
        """
        self._role_dir = Path(role_dir)
        self._cache_size = max(cache_size, 10)
        self._default_ttl_lookup = default_ttl_lookup
        self._ttl_defaults: dict[str, int] = dict(consts.DEFAULT_TTL_MAP)

        # 全量索引 (id → MaterialEntry)，始终在内存中（元数据）
        self._index: dict[str, MaterialEntry] = {}
        # 已加载内容的 LRU 缓存 (id → content)
        self._content_cache: OrderedDict[str, str] = OrderedDict()

        self._last_warnings: list[str] = []
        # 索引摘要缓存（资料未 reload 时复用）
        self._index_summary_cache: str = ""

    # ------------------------------------------------------------------
    # 公共接口
    # ------------------------------------------------------------------

    def load(self) -> LoadReport:
        """全量扫描 role/ 目录，Tier1/2 立即加载内容。"""
        warnings: list[str] = []
        if not self._role_dir.is_dir():
            warnings.append(f"资料目录不存在：{self._role_dir}")
            self._last_warnings = warnings
            return LoadReport([], warnings)

        old_index = dict(self._index)
        new_index: dict[str, MaterialEntry] = {}
        self._content_cache.clear()
        self._index_summary_cache = ""

        # 每次 load 重新取一次默认 TTL：配置改动后 reload 即可生效，
        # 同时避免在逐条目解析时重复解析插件配置。
        lookup = self._default_ttl_lookup
        self._ttl_defaults = (
            {kind: lookup(kind) for kind in consts.DEFAULT_TTL_MAP}
            if lookup is not None
            else dict(consts.DEFAULT_TTL_MAP)
        )

        self._scan_directory(new_index, warnings, self._role_dir)

        # 加载 Tier1/2 内容
        for entry in new_index.values():
            if entry.tier <= consts.TIER_RELATIONSHIP:
                raw = _read_file(entry.source_path)
                if raw is not None:
                    _, body = parse_frontmatter(raw)
                    clean = strip_html_comments(body).strip()
                    if not clean:
                        warnings.append(f"{entry.source_path}：正文为空，已跳过")
                        continue
                    entry.content = clean
                    self._content_cache[entry.id] = clean
                else:
                    warnings.append(f"读取失败：{entry.source_path}")

        if old_index and not new_index and warnings:
            # 空结果只在"扫描期间出现过读取告警"时才视为失败并保留快照；
            # 目录可读且确实没有 .md 时（例如删掉最后一个资料）应当接受空索引，
            # 否则面板（以文件系统为准）与运行时索引会长期不一致。
            warnings.append("本次加载为带告警的空结果，保留上一份资料快照")
            self._last_warnings = warnings
            return LoadReport(list(old_index.values()), warnings)

        self._index = new_index
        self._last_warnings = warnings
        return LoadReport(list(new_index.values()), warnings)

    def reload(self) -> LoadReport:
        """热重载资料。"""
        return self.load()

    def fetch(self, ids: list[str]) -> list[MaterialEntry]:
        """按 ID 列表获取条目，触发懒加载。"""
        results: list[MaterialEntry] = []
        for entry_id in ids:
            entry = self._index.get(entry_id)
            if entry is None:
                continue
            if not entry.is_loaded():
                self._lazy_load(entry)
            results.append(entry)
        return results

    def get(self, entry_id: str) -> MaterialEntry | None:
        """获取单条条目。"""
        entry = self._index.get(entry_id)
        if entry is None:
            return None
        if not entry.is_loaded():
            self._lazy_load(entry)
        return entry

    def get_tier(self, tier: int) -> list[MaterialEntry]:
        """获取指定层级的全部条目（已加载内容）。"""
        results: list[MaterialEntry] = []
        for entry in self._index.values():
            if entry.tier == tier:
                if not entry.is_loaded():
                    self._lazy_load(entry)
                results.append(entry)
        return results

    def all_entries(self) -> list[MaterialEntry]:
        """获取全部条目（不触发懒加载，仅返回元数据）。"""
        return list(self._index.values())

    def index_summary(self) -> str:
        """供 LLM 路由使用的轻量摘要（只含 id/title/tags/kind/tier）。"""
        if self._index_summary_cache:
            return self._index_summary_cache
        lines = []
        for entry in sorted(self._index.values(), key=lambda e: (e.tier, e.priority)):
            tags = "、".join(entry.tags[:5]) or "无标签"
            lines.append(
                f"[T{entry.tier}][{entry.kind}] {entry.id} ({entry.title}) 标签:{tags}"
            )
        self._index_summary_cache = "\n".join(lines)
        return self._index_summary_cache

    @property
    def warnings(self) -> tuple[str, ...]:
        """返回最近一次加载的告警列表。"""
        return tuple(self._last_warnings)

    @property
    def is_loaded(self) -> bool:
        """判断是否已成功加载过资料索引。"""
        return bool(self._index)

    @property
    def entry_count(self) -> int:
        """返回当前索引中的条目总数。"""
        return len(self._index)

    # ------------------------------------------------------------------
    # 内部实现
    # ------------------------------------------------------------------

    def _scan_directory(
        self, dest: dict[str, MaterialEntry], warnings: list[str], base_dir: Path
    ) -> None:
        """递归扫描 role/ 目录（单个子目录不可读时告警并继续）。"""
        try:
            children = sorted(base_dir.iterdir())
        except OSError:
            warnings.append(f"目录读取失败：{base_dir.name or base_dir}")
            return

        for path in children:
            if path.name.startswith(consts.IGNORED_PREFIXES):
                continue

            if path.is_dir():
                self._scan_directory(dest, warnings, path)
            elif path.suffix == ".md":
                self._load_entry(dest, warnings, path)

    def _load_entry(
        self,
        dest: dict[str, MaterialEntry],
        warnings: list[str],
        path: Path,
    ) -> None:
        """加载单个 .md 文件为 MaterialEntry 并写入索引。

        Args:
            dest: 条目索引（id → MaterialEntry）。
            warnings: 告警收集列表。
            path: 要加载的文件路径。
        """
        text = _read_file(path)
        if text is None:
            warnings.append(f"读取失败：{path.name}")
            return

        meta, body = parse_frontmatter(text)

        # 计算相对路径和自动推断
        try:
            rel_path = str(path.relative_to(self._role_dir)).replace("\\", "/")
        except ValueError:
            rel_path = path.name

        auto_tier, auto_kind = infer_tier_kind(rel_path)

        # 解析条目（脏值降级 + 告警：单个坏字段不应中断整次加载）
        entry_id = str(meta.get(consts.FM_KEY_ID) or path.stem)
        title = str(meta.get(consts.FM_KEY_TITLE) or path.stem)
        tier = coerce_int(
            meta.get(consts.FM_KEY_TIER) or auto_tier,
            auto_tier,
            warnings,
            f"{path.name} 的 {consts.FM_KEY_TIER}",
        )
        kind = str(meta.get(consts.FM_KEY_KIND) or auto_kind)
        tags = tuple(coerce_str_list(meta.get(consts.FM_KEY_TAGS)))
        keywords = tuple(coerce_str_list(meta.get(consts.FM_KEY_KEYWORDS)))
        patterns = tuple(coerce_str_list(meta.get(consts.FM_KEY_PATTERNS)))
        priority = coerce_int(
            meta.get(consts.FM_KEY_PRIORITY) or consts.DEFAULT_PRIORITY,
            consts.DEFAULT_PRIORITY,
            warnings,
            f"{path.name} 的 {consts.FM_KEY_PRIORITY}",
        )
        default_ttl = coerce_int(
            meta.get(consts.FM_KEY_DEFAULT_TTL) or self._ttl_defaults.get(kind, 0),
            self._ttl_defaults.get(kind, 0),
            warnings,
            f"{path.name} 的 {consts.FM_KEY_DEFAULT_TTL}",
        )

        # ID 冲突检测
        if entry_id in dest:
            warnings.append(
                f"资料 ID 冲突：{entry_id}（{path.name}），"
                f"保留先加载的版本（{dest[entry_id].source_path}）"
            )
            return

        entry = MaterialEntry(
            id=entry_id,
            title=title,
            tier=tier,
            kind=kind,
            source_path=str(path),
            content=None,  # 懒加载
            trigger_keywords=keywords,
            trigger_patterns=patterns,
            tags=tags,
            default_ttl=default_ttl,
            priority=priority,
        )

        dest[entry_id] = entry

    def _lazy_load(self, entry: MaterialEntry) -> None:
        """懒加载条目内容并写入 LRU 缓存。"""
        if entry.id in self._content_cache:
            entry.content = self._content_cache[entry.id]
            # 更新 LRU 访问顺序
            self._content_cache.move_to_end(entry.id)
            return

        content = _read_file(entry.source_path)
        if content is None:
            # 保留空内容作为降级：is_loaded() 随之返回 True，避免每次访问都重试读盘。
            # 但必须留下告警，否则「这个条目内容是空的」将毫无线索。
            entry.content = ""
            self._last_warnings.append(
                f"内容读取失败（已降级为空）：{entry.source_path}"
            )
            return

        _, body = parse_frontmatter(content)
        entry.content = strip_html_comments(body).strip()

        # LRU 淘汰
        if len(self._content_cache) >= self._cache_size:
            self._content_cache.popitem(last=False)
        self._content_cache[entry.id] = entry.content


def _read_file(path: str | Path) -> str | None:
    """读取文件内容，失败返回 None。"""
    try:
        return Path(path).read_text(encoding="utf-8")
    except OSError:
        return None
