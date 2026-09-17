/**
 * 资料管理器视图（两栏 master-detail + 编辑器）。
 *
 * 左栏：按目录分组的可折叠树（搜索 / tier / kind 过滤 / 隐藏文件开关 / 状态徽标）。
 * 右栏：结构化表单与「原始全文」双模式编辑器（保存 / 撤销 / 删除 / 脏标记 / rev 冲突）。
 *
 * 服务端权威：无论提交结构化字段还是 raw，都由 `role/save` 重新解析、规范化后落盘，
 * 保存成功后再回读一次，保证编辑器展示的就是磁盘内容。
 */
import { FireflyApi } from "../modules/api-client.js";
import { renderMarkdown } from "../modules/markdown.js";
import {
  confirmDialog,
  debounce,
  el,
  formDialog,
  formatBytes,
  formatTime,
  on,
  toast,
} from "../modules/dom.js";

export const id = "materials";
export const label = "资料浏览";

const KIND_LABELS = {
  persona: "人格",
  skill: "技能",
  lore: "设定",
  narrative: "叙事",
};

const KIND_OPTIONS = [
  { value: "", label: "（按目录推断）" },
  { value: "persona", label: "人格 persona" },
  { value: "skill", label: "技能 skill" },
  { value: "lore", label: "设定 lore" },
  { value: "narrative", label: "叙事 narrative" },
];

const TYPE_OPTIONS = [
  { value: "", label: "（不设置）" },
  { value: "static", label: "常驻 static" },
  { value: "on_demand", label: "按需 on_demand" },
];

const TIER_OPTIONS = [
  { value: "", label: "（按目录推断）" },
  { value: "1", label: "T1 核心人格" },
  { value: "2", label: "T2 低层常驻" },
  { value: "3", label: "T3 技能 / 设定" },
  { value: "4", label: "T4 世界观 / 叙事" },
];

const STATUS_TEXT = {
  registered: "已收录",
  empty_body: "空正文",
  duplicate_id: "ID冲突",
  read_error: "读失败",
  hidden: "隐藏",
};

const STATUS_BADGE_CLASS = {
  registered: "badge-ok",
  empty_body: "badge-tier",
  duplicate_id: "badge-skip",
  read_error: "badge-skip",
  hidden: "badge-neutral",
};

/** 结构化表单可编辑的标量字段与列表字段。 */
const SCALAR_FIELDS = ["type", "kind", "title", "id", "tier", "priority", "default_ttl"];
const LIST_FIELDS = ["keywords", "tags", "patterns"];
const NUMERIC_FIELDS = ["tier", "priority", "default_ttl"];

let searchInput = null;
let tierFilter = null;
let kindFilter = null;
let hiddenCheckbox = null;
let countLabel = null;
let newFileBtn = null;
let togglePaneBtn = null;
let drawerBtn = null;
let banner = null;
let conflictBanner = null;
let conflictText = null;
let manager = null;
let treePane = null;
let detailPane = null;
let drawerBackdrop = null;
let sectionEl = null;

let treeData = { dirs: [], entries: [], warnings: [], counts: {} };
const collapsedDirs = new Set();
let selectedPath = "";
let currentDoc = null;
let draft = null;
let snapshot = "";
let mode = "form";
let dirty = false;
let saving = false;

/**
 * 构建静态 DOM 并绑定事件（不做网络请求）。
 *
 * @param {HTMLElement} container 本视图的 section 容器。
 */
export function mount(container) {
  sectionEl = container;
  searchInput = el("input", {
    class: "input-sm role-search",
    type: "search",
    placeholder: "搜索路径 / id / 标题 / 标签...",
  });
  tierFilter = el("select", { class: "select-sm" }, [
    el("option", { value: "", text: "全部层级" }),
  ]);
  kindFilter = el("select", { class: "select-sm" }, [
    el("option", { value: "", text: "全部类型" }),
  ]);
  hiddenCheckbox = el("input", { type: "checkbox" });
  const hiddenToggle = el("label", { class: "toggle-row" }, [hiddenCheckbox, "显示隐藏文件"]);
  newFileBtn = el("button", {
    class: "btn btn-sm",
    type: "button",
    text: "新建文件",
  });
  togglePaneBtn = el("button", {
    class: "btn btn-sm btn-ghost pane-toggle",
    type: "button",
    text: "折叠目录树",
  });
  drawerBtn = el("button", {
    class: "btn btn-sm btn-ghost narrow-only",
    type: "button",
    text: "目录",
    "aria-label": "打开目录树",
  });
  countLabel = el("span", { class: "field-hint" });

  banner = el("div", { class: "banner banner-warn hidden" });
  conflictText = el("span");
  const reloadBtn = el("button", {
    class: "btn btn-sm btn-danger",
    type: "button",
    text: "重新加载",
  });
  conflictBanner = el("div", { class: "banner banner-danger hidden" }, [
    conflictText,
    reloadBtn,
  ]);

  treePane = el("div", { class: "panel role-tree", "aria-label": "资料目录树" });
  drawerBackdrop = el("div", { class: "role-backdrop hidden" });
  detailPane = el("div", { class: "panel role-detail" });
  manager = el("div", { class: "role-manager" }, [treePane, detailPane]);

  container.append(
    el("div", { class: "toolbar" }, [
      searchInput,
      tierFilter,
      kindFilter,
      hiddenToggle,
      newFileBtn,
      togglePaneBtn,
      drawerBtn,
      countLabel,
    ]),
    banner,
    conflictBanner,
    manager,
    drawerBackdrop,
  );

  searchInput.addEventListener("input", debounce(renderTree, 200));
  tierFilter.addEventListener("change", renderTree);
  kindFilter.addEventListener("change", renderTree);
  hiddenCheckbox.addEventListener("change", refresh);
  newFileBtn.addEventListener("click", createFileFlow);
  reloadBtn.addEventListener("click", () => selectPath(selectedPath, { force: true }));
  togglePaneBtn.addEventListener("click", () => {
    manager.classList.toggle("is-collapsed");
    togglePaneBtn.textContent = manager.classList.contains("is-collapsed")
      ? "显示目录树"
      : "折叠目录树";
  });

  const closeDrawer = () => {
    treePane.classList.remove("is-open");
    drawerBackdrop.classList.add("hidden");
  };
  const openDrawer = () => {
    treePane.classList.add("is-open");
    drawerBackdrop.classList.remove("hidden");
  };
  drawerBtn.addEventListener("click", () => {
    if (treePane.classList.contains("is-open")) closeDrawer();
    else openDrawer();
  });
  drawerBackdrop.addEventListener("click", closeDrawer);

  on(treePane, "click", "[data-dir]", (target) => {
    const dir = target.dataset.dir;
    if (collapsedDirs.has(dir)) collapsedDirs.delete(dir);
    else collapsedDirs.add(dir);
    renderTree();
  });
  on(treePane, "click", "[data-path]", (target) => {
    closeDrawer();
    selectPath(target.dataset.path);
  });

  // 切换文件 / 关闭页面前拦截未保存改动
  window.addEventListener("beforeunload", (event) => {
    if (!dirty) return;
    event.preventDefault();
    event.returnValue = "";
  });

  // Ctrl/Cmd+S 保存、Esc 关闭抽屉（仅在本 Tab 可见时响应）
  document.addEventListener("keydown", (event) => {
    if (!sectionEl || !sectionEl.classList.contains("active")) return;
    if (event.key === "Escape" && treePane.classList.contains("is-open")) {
      closeDrawer();
      return;
    }
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "s") {
      event.preventDefault();
      saveCurrent();
    }
  });

  showDetailEmpty("从左侧选择资料文件");
}

/**
 * 拉取目录树并渲染。
 *
 * @returns {Promise<void>}
 */
export async function refresh() {
  const params = {};
  if (hiddenCheckbox.checked) params.include_hidden = "1";
  try {
    const data = await FireflyApi.get("role/tree", params);
    treeData = {
      dirs: data.dirs || [],
      entries: data.entries || [],
      warnings: data.warnings || [],
      counts: data.counts || {},
    };
    fillFilters();
    renderBanner();
    renderTree();
    checkConflict();

    const entry = treeData.entries.find((item) => item.path === selectedPath);
    if (selectedPath && !entry) {
      resetEditor();
      selectedPath = "";
      showDetailEmpty("文件已不存在，可能已被外部删除");
      toast("当前文件已不存在，可能被外部删除", "error");
    }
  } catch (error) {
    showDetailEmpty("加载失败: " + error.message);
    toast("加载资料树失败: " + error.message, "error");
  }
}

// ----------------------------------------------------------------------
// 左栏：树
// ----------------------------------------------------------------------

function fillFilters() {
  const tier = tierFilter.value;
  const kind = kindFilter.value;

  tierFilter.replaceChildren(el("option", { value: "", text: "全部层级" }));
  Object.keys(treeData.counts)
    .sort((a, b) => Number(a) - Number(b))
    .forEach((tierKey) => {
      tierFilter.append(
        el("option", {
          value: tierKey,
          text: "T" + tierKey + "（" + treeData.counts[tierKey] + "）",
        }),
      );
    });

  kindFilter.replaceChildren(el("option", { value: "", text: "全部类型" }));
  const kinds = [...new Set(treeData.entries.map((entry) => entry.kind))]
    .filter(Boolean)
    .sort();
  kinds.forEach((kindName) => {
    kindFilter.append(
      el("option", {
        value: kindName,
        text: KIND_LABELS[kindName] ? KIND_LABELS[kindName] + " " + kindName : kindName,
      }),
    );
  });

  tierFilter.value = tier;
  kindFilter.value = kind;
}

function renderBanner() {
  if (treeData.warnings.length === 0) {
    banner.classList.add("hidden");
    banner.replaceChildren();
    return;
  }
  banner.classList.remove("hidden");
  banner.replaceChildren(
    el("strong", { text: "加载告警（" + treeData.warnings.length + "）：" }),
    el("span", { text: treeData.warnings.join(" ／ ") }),
  );
}

function filterActive() {
  return Boolean(searchInput.value.trim() || tierFilter.value || kindFilter.value);
}

function visibleEntries() {
  const keyword = searchInput.value.trim().toLowerCase();
  const tier = tierFilter.value;
  const kind = kindFilter.value;
  return treeData.entries.filter((entry) => {
    if (tier && String(entry.tier) !== tier) return false;
    if (kind && entry.kind !== kind) return false;
    if (!keyword) return true;
    const haystack = [entry.path, entry.id, entry.title, ...entry.tags]
      .join(" ")
      .toLowerCase();
    return haystack.includes(keyword);
  });
}

function parentOf(dir) {
  return dir.includes("/") ? dir.slice(0, dir.lastIndexOf("/")) : "";
}

function childDirs(dir) {
  return treeData.dirs
    .filter((candidate) => candidate && candidate !== dir && parentOf(candidate) === dir)
    .sort((a, b) => a.localeCompare(b, "zh"));
}

function countFiles(dir, filesByDir) {
  let total = (filesByDir.get(dir) || []).length;
  childDirs(dir).forEach((child) => {
    total += countFiles(child, filesByDir);
  });
  return total;
}

function renderTree() {
  const visible = visibleEntries();
  const filesByDir = new Map();
  visible.forEach((entry) => {
    if (!filesByDir.has(entry.dir)) filesByDir.set(entry.dir, []);
    filesByDir.get(entry.dir).push(entry);
  });

  const dirHasMatches = (dir) =>
    (filesByDir.get(dir) || []).length > 0 || childDirs(dir).some((child) => dirHasMatches(child));

  const total = treeData.entries.length;
  countLabel.textContent =
    visible.length === total
      ? total + " 个文件"
      : "筛选出 " + visible.length + " / " + total + " 个文件";

  const nodes = [];
  const appendDir = (dir, depth) => {
    const children = childDirs(dir).filter((child) => dirHasMatches(child));
    const files = (filesByDir.get(dir) || [])
      .slice()
      .sort((a, b) => a.name.localeCompare(b.name, "zh"));

    if (dir !== "") {
      const isOpen = filterActive() || !collapsedDirs.has(dir);
      nodes.push(
        el(
          "button",
          {
            class: "tree-group-label",
            type: "button",
            dataset: { dir },
            "aria-expanded": isOpen ? "true" : "false",
            style: { paddingLeft: 4 + depth * 14 + "px" },
          },
          [
            el("span", { text: isOpen ? "▾" : "▸" }),
            el("span", { class: "tree-item-label", text: dir.split("/").pop() }),
            el("span", { class: "field-hint", text: countFiles(dir, filesByDir) }),
          ],
        ),
      );
      if (!isOpen) return;
    }

    children.forEach((child) => appendDir(child, dir === "" ? 0 : depth + 1));
    files.forEach((entry) => {
      const statusBadge =
        entry.status === "registered"
          ? null
          : el("span", {
              class: "badge " + STATUS_BADGE_CLASS[entry.status],
              text: STATUS_TEXT[entry.status] || entry.status,
            });
      nodes.push(
        el(
          "button",
          {
            class: "tree-item" + (entry.path === selectedPath ? " is-active" : ""),
            type: "button",
            dataset: { path: entry.path },
            title:
              entry.path +
              " · T" +
              entry.tier +
              " " +
              entry.kind +
              (entry.active_sessions ? " · " + entry.active_sessions + " 个会话在用" : ""),
            "aria-current": entry.path === selectedPath ? "true" : "false",
            style: { paddingLeft: 4 + (dir === "" ? 0 : depth + 1) * 14 + "px" },
          },
          [
            el("span", { class: "badge badge-tier", text: "T" + entry.tier }),
            el("span", { class: "tree-item-label", text: entry.name }),
            statusBadge,
          ],
        ),
      );
    });
  };

  appendDir("", 0);
  if (nodes.length === 0) {
    treePane.replaceChildren(el("div", { class: "empty", text: "无匹配资料" }));
    return;
  }
  treePane.replaceChildren(...nodes);
}

function highlightSelection() {
  treePane.querySelectorAll(".tree-item").forEach((node) => {
    const active = node.dataset.path === selectedPath;
    node.classList.toggle("is-active", active);
    node.setAttribute("aria-current", active ? "true" : "false");
  });
}

// ----------------------------------------------------------------------
// 右栏：编辑器
// ----------------------------------------------------------------------

function showDetailEmpty(message) {
  detailPane.replaceChildren(el("div", { class: "empty", text: message }));
}

function showDetailSkeleton() {
  detailPane.replaceChildren(
    el("div", { class: "skeleton skeleton-line" }),
    el("div", { class: "skeleton skeleton-block" }),
  );
}

function resetEditor() {
  currentDoc = null;
  draft = null;
  snapshot = "";
  mode = "form";
  setDirty(false);
}

function setDirty(value) {
  dirty = value;
  const flag = detailPane.querySelector(".dirty-flag");
  if (flag) flag.classList.toggle("hidden", !dirty);
}

function markDirty() {
  if (!draft) return;
  setDirty(JSON.stringify(draft) !== snapshot);
}

/**
 * 切换选中文件；有未保存改动时先询问。
 *
 * @param {string} path 目标文件路径。
 * @param {object} [options] force 为真时允许对同一路径重新加载（脏检查仍会执行）。
 * @returns {Promise<void>}
 */
/**
 * 有未保存改动时询问如何处理。
 *
 * @param {string} action 即将进行的操作描述（用于文案）。
 * @returns {Promise<"cancel"|"discard"|"save"|null>} null 表示用户取消。
 */
async function promptUnsavedChanges(action) {
  const name = selectedPath ? selectedPath.split("/").pop() : "当前文件";
  const choice = await formDialog({
    title: "有未保存的改动",
    intro: "「" + name + "」有未保存的改动，" + action + "会丢弃它们。",
    actions: [
      { value: "cancel", label: "取消", class: "btn btn-ghost" },
      { value: "discard", label: "丢弃改动", class: "btn btn-danger" },
      { value: "save", label: "保存后继续", class: "btn", default: true },
    ],
  });
  if (!choice || choice.action === "cancel") return null;
  return choice.action;
}

async function selectPath(path, options = {}) {
  if (!path) return;
  if (path === selectedPath && !options.force) return;
  if (dirty) {
    const choice = await promptUnsavedChanges("切换文件");
    if (!choice) return;
    if (choice === "save" && !(await saveCurrent())) return;
  }
  selectedPath = path;
  highlightSelection();
  await fetchDocument(path);
}

async function fetchDocument(path) {
  showDetailSkeleton();
  try {
    const doc = await FireflyApi.get("role/file", { path });
    currentDoc = doc;
    mode = "form";
    loadDraft();
    renderEditor();
    checkConflict();
  } catch (error) {
    resetEditor();
    showDetailEmpty("读取失败: " + error.message);
    toast("读取失败: " + error.message, "error");
  }
}

function loadDraft() {
  const frontmatter = {};
  SCALAR_FIELDS.forEach((key) => {
    const value = currentDoc.frontmatter[key];
    frontmatter[key] = value === undefined || value === null ? "" : String(value);
  });
  LIST_FIELDS.forEach((key) => {
    frontmatter[key] = [...(currentDoc.frontmatter[key] || [])];
  });
  draft = { frontmatter, body: currentDoc.body, raw: currentDoc.raw };
  snapshot = JSON.stringify(draft);
  setDirty(false);
}

function renderEditor() {
  if (!currentDoc || !draft) return;
  const entry = treeData.entries.find((item) => item.path === currentDoc.path);
  const status = entry ? entry.status : "registered";

  const modeSwitch = el("div", { class: "segmented", role: "group", "aria-label": "编辑模式" }, [
    el("button", {
      class: mode === "form" ? "is-active" : "",
      type: "button",
      text: "表单",
      "aria-pressed": mode === "form" ? "true" : "false",
      dataset: { mode: "form" },
    }),
    el("button", {
      class: mode === "preview" ? "is-active" : "",
      type: "button",
      text: "预览",
      "aria-pressed": mode === "preview" ? "true" : "false",
      dataset: { mode: "preview" },
    }),
    el("button", {
      class: mode === "raw" ? "is-active" : "",
      type: "button",
      text: "原始全文",
      "aria-pressed": mode === "raw" ? "true" : "false",
      dataset: { mode: "raw" },
    }),
  ]);

  const saveBtn = el("button", { class: "btn btn-sm", type: "button", text: "保存" });
  const undoBtn = el("button", { class: "btn btn-sm btn-ghost", type: "button", text: "撤销" });
  const renameBtn = el("button", {
    class: "btn btn-sm btn-ghost",
    type: "button",
    text: "重命名",
  });
  const deleteBtn = el("button", {
    class: "btn btn-sm btn-danger",
    type: "button",
    text: "删除",
  });
  saveBtn.addEventListener("click", saveCurrent);
  renameBtn.addEventListener("click", renameFlow);
  undoBtn.addEventListener("click", () => {
    loadDraft();
    renderEditor();
    toast("已撤销到上次加载的内容", "success");
  });
  deleteBtn.addEventListener("click", deleteFlow);
  on(modeSwitch, "click", "[data-mode]", (target) => switchMode(target.dataset.mode));

  detailPane.replaceChildren(
    el("div", { class: "detail-header" }, [
      el("div", { class: "breadcrumb" }, breadcrumbNodes(currentDoc.path)),
      el("div", { class: "doc-badges" }, [
        el("span", { class: "badge badge-tier", text: "T" + (entry ? entry.tier : "?") }),
        el("span", { class: "badge badge-kind", text: (entry ? entry.kind : "") || "?" }),
        el("span", {
          class: "badge " + STATUS_BADGE_CLASS[status],
          text: STATUS_TEXT[status] || status,
        }),
      ]),
    ]),
    el("div", {
      class: "field-hint",
      text:
        "rev " +
        currentDoc.rev +
        " · " +
        formatBytes(currentDoc.size) +
        " · " +
        formatTime(currentDoc.mtime) +
        (entry && entry.active_sessions ? " · " + entry.active_sessions + " 个会话在用" : ""),
    }),
    el("div", { class: "doc-toolbar" }, [
      saveBtn,
      undoBtn,
      renameBtn,
      deleteBtn,
      el("span", { class: "dirty-flag hidden", text: "● 未保存" }),
      modeSwitch,
      el("span", { class: "field-hint", text: "Ctrl+S 保存" }),
    ]),
    mode === "form" ? renderForm() : mode === "preview" ? renderPreview() : renderRawEditor(),
  );
  setDirty(dirty);
}

function breadcrumbNodes(path) {
  const nodes = [el("span", { text: "role" })];
  path.split("/").forEach((segment, index, all) => {
    nodes.push(el("span", { class: "breadcrumb-sep", text: "›" }));
    nodes.push(
      el("span", {
        class: index === all.length - 1 ? "breadcrumb-current" : "",
        text: segment,
      }),
    );
  });
  return nodes;
}

function renderForm() {
  const fields = [
    {
      label: "type",
      hint: "运行时行为实际由 tier 与 keywords 决定，此字段仅作标注",
      control: () => selectControl("type", TYPE_OPTIONS),
    },
    { label: "kind", control: () => selectControl("kind", KIND_OPTIONS) },
    {
      label: "tier",
      hint: "由目录推断，可显式覆盖",
      control: () => selectControl("tier", TIER_OPTIONS),
    },
    { label: "id", hint: "留空则按文件名生成", control: () => textControl("id") },
    { label: "title", control: () => textControl("title") },
    { label: "priority", control: () => textControl("priority", "50", "number") },
    { label: "default_ttl", control: () => textControl("default_ttl", "", "number") },
  ];

  const grid = el(
    "div",
    { class: "meta-grid" },
    fields.map((field) =>
      el("div", { class: "field" }, [
        el("span", { class: "field-label", text: field.label }),
        field.control(),
        field.hint ? el("span", { class: "field-hint", text: field.hint }) : null,
      ]),
    ),
  );

  const lists = el(
    "div",
    { class: "meta-grid" },
    LIST_FIELDS.map((key) =>
      el("div", { class: "field" }, [
        el("span", { class: "field-label", text: key }),
        tagInput(key),
      ]),
    ),
  );

  const bodyArea = el("textarea", {
    class: "editor",
    rows: "14",
    spellcheck: "false",
    "aria-label": "正文",
  });
  bodyArea.value = draft.body;
  bodyArea.addEventListener("input", () => {
    draft.body = bodyArea.value;
    markDirty();
  });

  return el("div", {}, [
    grid,
    el("div", { class: "doc-body-header" }, [el("strong", { text: "正文" })]),
    bodyArea,
    el("div", { class: "doc-body-header" }, [el("strong", { text: "标签类字段" })]),
    lists,
  ]);
}

function selectControl(key, options) {
  const control = el(
    "select",
    { class: "select" },
    options.map((option) => el("option", { value: option.value, text: option.label })),
  );
  control.value = draft.frontmatter[key];
  control.addEventListener("change", () => {
    draft.frontmatter[key] = control.value;
    markDirty();
  });
  return control;
}

function textControl(key, placeholder, type) {
  const control = el("input", {
    class: "input",
    type: type || "text",
    placeholder: placeholder || "",
  });
  control.value = draft.frontmatter[key];
  control.addEventListener("input", () => {
    draft.frontmatter[key] = control.value;
    markDirty();
  });
  return control;
}

function tagInput(key) {
  const list = draft.frontmatter[key];
  const valueInput = el("input", { type: "text", "aria-label": key + " 输入" });
  const chips = el("span", { class: "tag-chips" });
  const host = el("div", { class: "tag-input" }, [chips, valueInput]);

  // 只重建 chips：输入框始终留在 DOM 中，回车/逗号后不丢焦点
  const renderChips = () => {
    chips.replaceChildren(
      ...list.map((value, index) =>
        el("span", { class: "tag-chip" }, [
          el("span", { text: value }),
          el("button", {
            type: "button",
            text: "×",
            "aria-label": "移除 " + value,
            dataset: { remove: String(index) },
          }),
        ]),
      ),
    );
  };
  const commit = (raw) => {
    const parts = String(raw)
      .split(/[,，]/)
      .map((item) => item.trim())
      .filter(Boolean);
    if (!parts.length) return;
    parts.forEach((part) => {
      if (!list.includes(part)) list.push(part);
    });
    valueInput.value = "";
    renderChips();
    markDirty();
  };

  valueInput.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === ",") {
      event.preventDefault();
      commit(valueInput.value);
    } else if (event.key === "Backspace" && !valueInput.value && list.length) {
      list.pop();
      renderChips();
      markDirty();
    }
  });
  valueInput.addEventListener("blur", () => commit(valueInput.value));
  on(host, "click", "[data-remove]", (target) => {
    list.splice(Number(target.dataset.remove), 1);
    renderChips();
    markDirty();
  });

  renderChips();
  return host;
}

function renderPreview() {
  return el("div", {}, [
    el("div", { class: "doc-body-header" }, [
      el("strong", { text: "预览" }),
      el("span", {
        class: "field-hint",
        text: "预览基于表单中的正文（含未保存改动）；全文模式的改动请保存后查看",
      }),
    ]),
    renderMarkdown(draft.body || "(空)"),
  ]);
}

function renderRawEditor() {
  const area = el("textarea", {
    class: "editor",
    rows: "24",
    spellcheck: "false",
    "aria-label": "原始全文",
  });
  area.value = draft.raw;
  area.addEventListener("input", () => {
    draft.raw = area.value;
    markDirty();
  });
  return el("div", {}, [
    el("div", { class: "doc-body-header" }, [
      el("strong", { text: "原始全文" }),
      el("span", {
        class: "field-hint",
        text: "直接编辑完整文件；未识别的键会原样保留，保存时由服务端重新解析规范化",
      }),
    ]),
    area,
  ]);
}

async function switchMode(next) {
  if (next === mode) return;
  // 只有涉及「原始全文」的切换才可能丢改动：预览与表单共用同一份草稿
  if (dirty && (mode === "raw" || next === "raw")) {
    const confirmed = await confirmDialog(
      "切换编辑模式会丢弃当前未保存的改动（表单与全文各自持有编辑态）。",
      { title: "切换编辑模式", danger: true },
    );
    if (!confirmed) return;
    loadDraft();
  }
  mode = next;
  renderEditor();
}

function structuredPayload() {
  const frontmatter = {};
  ["type", "kind", "id", "title"].forEach((key) => {
    const value = (draft.frontmatter[key] || "").trim();
    if (value) frontmatter[key] = value;
  });
  NUMERIC_FIELDS.forEach((key) => {
    const raw = (draft.frontmatter[key] || "").trim();
    if (!raw) return;
    const value = Number(raw);
    if (!Number.isNaN(value)) frontmatter[key] = value;
  });
  LIST_FIELDS.forEach((key) => {
    const values = (draft.frontmatter[key] || []).filter((item) => item !== "");
    if (values.length) frontmatter[key] = values;
  });
  return { frontmatter, body: draft.body };
}

/**
 * 保存当前文件；成功返回 true。
 *
 * @returns {Promise<boolean>} 是否保存成功。
 */
async function saveCurrent() {
  if (!currentDoc || !draft || saving) return false;
  saving = true;
  const payload = { path: currentDoc.path, create: false };
  if (mode === "raw") {
    payload.raw = draft.raw;
  } else {
    Object.assign(payload, structuredPayload());
  }
  // 覆盖时带乐观锁：以磁盘现值为准
  payload.base_rev = currentDoc.rev;
  try {
    await FireflyApi.post("role/save", payload);
    toast("已保存 " + currentDoc.path, "success");
    await fetchDocument(currentDoc.path);
    await refresh();
    return true;
  } catch (error) {
    toast("保存失败: " + error.message, "error");
    // 失败后刷新树：若属 rev 冲突，刷新会带出冲突横幅
    await refresh();
    return false;
  } finally {
    saving = false;
  }
}

function checkConflict() {
  const entry = treeData.entries.find((item) => item.path === selectedPath);
  const stale = Boolean(entry && currentDoc && entry.rev !== currentDoc.rev);
  if (!stale) {
    conflictBanner.classList.add("hidden");
    return;
  }
  conflictText.textContent =
    "文件已被外部修改（磁盘 rev " +
    entry.rev +
    "，编辑器 rev " +
    currentDoc.rev +
    "）。请重新加载后再编辑。";
  conflictBanner.classList.remove("hidden");
}

// ----------------------------------------------------------------------
// 新建 / 删除
// ----------------------------------------------------------------------

async function createFileFlow() {
  const dirOptions = [{ value: "", label: "（role 根目录）" }].concat(
    treeData.dirs
      .filter((dir) => dir !== "")
      .sort((a, b) => a.localeCompare(b, "zh"))
      .map((dir) => ({ value: dir, label: dir })),
  );
  const result = await formDialog({
    title: "新建资料文件",
    intro: "文件会创建在 role/ 目录内，仅支持 .md。",
    fields: [
      { name: "dir", label: "目录", type: "select", options: dirOptions, value: "", hint: "不存在的新目录请先用文件管理器创建" },
      { name: "name", label: "文件名", placeholder: "例如：讲笑话.md", hint: "留空的 .md 会自动补上" },
    ],
    actions: [
      { value: "cancel", label: "取消", class: "btn btn-ghost" },
      { value: "create", label: "创建并编辑", class: "btn", default: true },
    ],
    validate: (values) => {
      const name = (values.name || "").trim();
      if (!name) return "请填写文件名";
      if (/[\\/]/.test(name)) return "文件名不能包含路径分隔符";
      if (name.startsWith(".") || name.startsWith("_")) return "隐藏前缀（_ / .）不允许写入";
      return null;
    },
  });
  if (!result || result.action !== "create") return;

  let name = result.values.name.trim();
  if (!name.endsWith(".md")) name += ".md";
  const path = result.values.dir ? result.values.dir + "/" + name : name;

  try {
    await FireflyApi.post("role/save", {
      path,
      create: true,
      frontmatter: { id: name.slice(0, -3), kind: "skill", title: name.slice(0, -3) },
      body: "## 说明\n（待填写）",
    });
    toast("已创建 " + path, "success");
    await refresh();
    await selectPath(path, { force: true });
  } catch (error) {
    toast("创建失败: " + error.message, "error");
  }
}

async function renameFlow() {
  if (!currentDoc || !selectedPath) return;
  if (dirty) {
    const choice = await promptUnsavedChanges("重命名");
    if (!choice) return;
    if (choice === "save" && !(await saveCurrent())) return;
  }

  const currentName = currentDoc.path.split("/").pop();
  const currentDir = currentDoc.path.includes("/")
    ? currentDoc.path.slice(0, currentDoc.path.lastIndexOf("/"))
    : "";
  const hasExplicitId = Boolean(currentDoc.frontmatter.id);
  const dirOptions = [{ value: "", label: "（role 根目录）" }].concat(
    treeData.dirs
      .filter((dir) => dir !== "")
      .sort((a, b) => a.localeCompare(b, "zh"))
      .map((dir) => ({ value: dir, label: dir })),
  );

  const result = await formDialog({
    title: "重命名 / 移动",
    intro: hasExplicitId
      ? "文件内容与 rev 不变，仅改路径；移动到别的目录会改变 tier/kind 的目录推断结果。"
      : "该文件没有显式 id，改名会同时改变由文件名派生的 id。",
    fields: [
      { name: "dir", label: "目录", type: "select", options: dirOptions, value: currentDir },
      { name: "name", label: "文件名", value: currentName, hint: "留空的 .md 会自动补上" },
    ],
    actions: [
      { value: "cancel", label: "取消", class: "btn btn-ghost" },
      { value: "rename", label: "重命名", class: "btn", default: true },
    ],
    validate: (values) => {
      const name = (values.name || "").trim();
      if (!name) return "请填写文件名";
      if (/[\\/]/.test(name)) return "文件名不能包含路径分隔符";
      if (name.startsWith(".") || name.startsWith("_")) return "隐藏前缀（_ / .）不允许写入";
      return null;
    },
  });
  if (!result || result.action !== "rename") return;

  let name = result.values.name.trim();
  if (!name.endsWith(".md")) name += ".md";
  const target = result.values.dir ? result.values.dir + "/" + name : name;

  try {
    await FireflyApi.post("role/rename", {
      path: currentDoc.path,
      base_rev: currentDoc.rev,
      target,
    });
    toast("已移动到 " + target, "success");
    selectedPath = target;
    await refresh();
    await fetchDocument(target);
  } catch (error) {
    toast("重命名失败: " + error.message, "error");
    await refresh();
  }
}

async function deleteFlow() {
  if (!currentDoc || !selectedPath) return;
  const entry = treeData.entries.find((item) => item.path === selectedPath);
  const activeSessions = entry ? entry.active_sessions || 0 : 0;
  const sessionIds = entry ? entry.active_session_ids || [] : [];
  const name = currentDoc.path.split("/").pop();
  const activeNote = activeSessions
    ? "⚠ 该条目正被 " +
      activeSessions +
      " 个会话激活使用：" +
      sessionIds.slice(0, 5).join("、") +
      (sessionIds.length > 5 ? " 等" : "") +
      "。删除后这些会话中的该条目会立即失效。"
    : "";
  const intro = "硬删除不可恢复，不会进入回收站。" + activeNote;

  const fields = [
    {
      name: "confirm_name",
      label: "文件名确认",
      value: "",
      placeholder: name,
      hint: "必须与文件名逐字符相同",
    },
  ];
  if (activeSessions) {
    fields.push({
      name: "confirm_active",
      type: "checkbox",
      label: "活跃确认",
      text: "我确认让上述 " + activeSessions + " 个会话中的该条目立即失效",
    });
  }

  const result = await formDialog({
    title: "删除 " + name,
    intro,
    fields,
    actions: [
      { value: "cancel", label: "取消", class: "btn btn-ghost" },
      { value: "delete", label: "确认删除", class: "btn btn-danger", default: true },
    ],
    validate: (values) => {
      if (values.confirm_name !== name) return "输入的文件名与目标不一致";
      if (activeSessions && !values.confirm_active) return "请勾选活跃会话确认";
      return null;
    },
  });
  if (!result || result.action !== "delete") return;

  try {
    await FireflyApi.post("role/delete", {
      path: currentDoc.path,
      base_rev: currentDoc.rev,
      confirm_name: name,
      confirm_active: Boolean(result.values.confirm_active),
    });
    toast("已删除 " + name, "success");
    resetEditor();
    selectedPath = "";
    showDetailEmpty("从左侧选择资料文件");
    await refresh();
  } catch (error) {
    toast("删除失败: " + error.message, "error");
    await refresh();
  }
}
