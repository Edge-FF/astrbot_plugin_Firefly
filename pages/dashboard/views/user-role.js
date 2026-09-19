/**
 * 用户角色预设视图（全局默认身份的"钉选"面板）。
 *
 * 左栏：二选一（现有角色 / 自定义角色）+ 搜索 + 候选列表。
 * 右栏：候选真实正文预览（与运行时同一来源），以及设为身份 / 编辑 / 删除 / 新建。
 *
 * 边界约定：
 * - 选择是"轻操作"：点击只改预览，确认后才写默认，避免误触即生效。
 * - 空正文候选可见但不可选（服务端也会拒绝）。
 * - 新建/编辑走 role/save，成功后再 user_role/set；两步失败分别提示，不静默。
 * - 删除当前身份文档后会回退内置默认，避免默认指向已删除的 id。
 */
import { FireflyApi } from "../modules/api-client.js";
import { renderMarkdown } from "../modules/markdown.js";
import { debounce, el, formDialog, on, toast } from "../modules/dom.js";

export const id = "user-role";
export const label = "用户角色";

const CUSTOM_DIR = "用户角色";
const MODE_EXISTING = "existing";
const MODE_CUSTOM = "custom";
const ID_RE = /^[A-Za-z0-9_\-\u4e00-\u9fff]{1,64}$/;
const ROUTE_NOTE = "该文档将作为常驻身份注入，并暂时从按需路由中排除。";

let sideEl = null;
let bannerEl = null;
let statusEl = null;
let searchInput = null;
let listEl = null;
let previewEl = null;
let setBtn = null;
let editBtn = null;
let deleteBtn = null;
let newBtn = null;

let data = null;
let kind = MODE_EXISTING;
let selection = null;

/**
 * 构建静态 DOM 并绑定事件（不做网络请求）。
 *
 * @param {HTMLElement} container 本视图的 section 容器。
 */
export function mount(container) {
  bannerEl = el("div", { class: "banner banner-warn hidden" });
  statusEl = el("div", { class: "user-role-status" });

  searchInput = el("input", {
    class: "input-sm",
    type: "search",
    placeholder: "搜索角色名 / id...",
  });
  listEl = el("div", { class: "candidate-list" });
  sideEl = el("div", { class: "panel user-role-side" }, [
    el("div", { class: "user-role-modes" }, [
      radio("现有角色", MODE_EXISTING),
      radio("自定义角色", MODE_CUSTOM),
    ]),
    el("div", { class: "toolbar" }, [searchInput]),
    listEl,
  ]);

  previewEl = el("div", { class: "panel user-role-preview" });
  setBtn = el("button", { class: "btn", type: "button", text: "设为当前身份" });
  editBtn = el("button", { class: "btn btn-sm btn-ghost", type: "button", text: "编辑" });
  deleteBtn = el("button", {
    class: "btn btn-sm btn-danger",
    type: "button",
    text: "删除",
  });
  newBtn = el("button", { class: "btn btn-sm", type: "button", text: "新建自定义角色" });
  const main = el("div", { class: "panel user-role-main" }, [
    el("div", { class: "user-role-actions" }, [
      setBtn,
      editBtn,
      deleteBtn,
      el("span", { class: "spacer" }),
      newBtn,
    ]),
    previewEl,
  ]);

  container.append(
    bannerEl,
    statusEl,
    el("div", { class: "user-role-layout" }, [sideEl, main]),
  );

  searchInput.addEventListener("input", debounce(renderList, 150));
  setBtn.addEventListener("click", applySelection);
  editBtn.addEventListener("click", editFlow);
  deleteBtn.addEventListener("click", deleteFlow);
  newBtn.addEventListener("click", createFlow);
  on(sideEl, "click", "[data-mode]", (target) => {
    kind = target.dataset.mode;
    selection = null;
    renderAll();
  });
  on(listEl, "click", "[data-id]", (target) => selectCandidate(target.dataset.id));
}

/**
 * 拉取身份设置与候选列表并渲染。
 *
 * @returns {Promise<void>}
 */
export async function refresh() {
  try {
    data = await FireflyApi.get("user_role/get");
  } catch (error) {
    showBanner("加载失败：" + error.message);
    toast("加载用户角色失败: " + error.message, "error");
    return;
  }
  syncSelection();
  renderAll();
  await loadSelectionBody();
}

// ----------------------------------------------------------------------
// 交互
// ----------------------------------------------------------------------

function radio(text, value) {
  const input = el("input", { type: "radio", name: "user-role-kind" });
  input.checked = kind === value;
  input.dataset.mode = value;
  return el("label", { class: "toggle-row" }, [input, el("span", { text })]);
}

function candidates() {
  if (!data) return [];
  return (data.candidates && data.candidates[kind]) || [];
}

function toSelection(item) {
  return {
    mode: kind,
    role_id: item.id,
    title: item.title || item.id,
    path: item.path,
    body: undefined,
    error: "",
  };
}

function syncSelection() {
  kind = data.default.mode === MODE_CUSTOM ? MODE_CUSTOM : MODE_EXISTING;
  selection = null;
  const pinned = data.resolved.pin_id;
  const match = candidates().find((candidate) => candidate.id === pinned);
  if (match && !match.empty) selection = toSelection(match);
}

async function selectCandidate(roleId) {
  const item = candidates().find((candidate) => candidate.id === roleId);
  if (!item || item.empty) return;
  selection = toSelection(item);
  renderList();
  renderActions();
  renderPreview();
  await loadSelectionBody();
}

/**
 * 加载当前选中候选的真实正文（与运行时同源：role/file 读取磁盘内容）。
 *
 * @returns {Promise<void>}
 */
async function loadSelectionBody() {
  if (!selection) {
    renderPreview();
    return;
  }
  const path = selection.path;
  try {
    const doc = await FireflyApi.get("role/file", { path });
    if (!selection || selection.path !== path) return; // 期间已切换选中
    selection.body = doc.body || "";
  } catch (error) {
    if (!selection || selection.path !== path) return;
    selection.body = "";
    selection.error = error.message;
  }
  renderPreview();
}

// ----------------------------------------------------------------------
// 渲染
// ----------------------------------------------------------------------

function renderAll() {
  if (!data) return;
  if (data.resolved.warning) showBanner(data.resolved.warning);
  else hideBanner();
  sideEl.querySelectorAll("[data-mode]").forEach((input) => {
    input.checked = input.dataset.mode === kind;
  });
  renderStatus();
  renderList();
  renderActions();
  renderPreview();
}

function renderStatus() {
  const resolved = data.resolved;
  const name = resolved.exists ? resolved.title || resolved.pin_id : "（无有效身份）";
  const suffix =
    resolved.origin === "fallback" ? " · 自定义角色失效，已回退默认" : "";
  statusEl.textContent = "当前身份：" + name + suffix;
}

function renderList() {
  if (!data) return;
  const keyword = searchInput.value.trim().toLowerCase();
  const items = candidates().filter((item) => {
    if (!keyword) return true;
    return (item.id + " " + (item.title || "")).toLowerCase().includes(keyword);
  });

  listEl.replaceChildren();
  if (items.length === 0) {
    listEl.append(
      el("div", {
        class: "empty",
        text:
          kind === MODE_CUSTOM ? "还没有自定义角色，点右侧「新建」" : "无匹配角色",
      }),
    );
    return;
  }

  const pinned = data.resolved.pin_id;
  items.forEach((item) => {
    const selected = selection && selection.role_id === item.id;
    const badges = [];
    if (item.id === pinned) {
      badges.push(el("span", { class: "badge badge-ok", text: "当前身份" }));
    }
    if (item.empty) {
      badges.push(el("span", { class: "badge badge-skip", text: "空正文" }));
    }
    listEl.append(
      el(
        "button",
        {
          class:
            "candidate-item" +
            (selected ? " is-selected" : "") +
            (item.empty ? " is-disabled" : ""),
          type: "button",
          dataset: { id: item.id },
          title: item.path,
          "aria-pressed": selected ? "true" : "false",
          disabled: item.empty ? "" : null,
        },
        [
          el("span", { class: "candidate-name", text: item.title || item.id }),
          el("code", { class: "candidate-id", text: item.id }),
          ...badges,
        ],
      ),
    );
  });
}

function renderPreview() {
  if (!previewEl) return;
  previewEl.replaceChildren();
  if (!selection) {
    previewEl.append(el("div", { class: "empty", text: "在左侧选择一个角色" }));
    return;
  }
  const head = el("div", { class: "user-role-preview-head" }, [
    el("strong", { text: selection.title }),
    el("code", { class: "candidate-id", text: selection.role_id }),
  ]);
  if (selection.body === undefined) {
    previewEl.append(head, el("div", { class: "field-hint", text: "加载正文..." }));
    return;
  }
  if (selection.error) {
    previewEl.append(
      head,
      el("div", { class: "field-hint", text: "读取失败：" + selection.error }),
    );
    return;
  }
  previewEl.append(
    head,
    el("div", { class: "field-hint", text: ROUTE_NOTE }),
    selection.body.trim()
      ? renderMarkdown(selection.body)
      : el("div", { class: "empty", text: "（正文为空，不能作为身份）" }),
  );
}

function renderActions() {
  if (!data) return;
  const changed = Boolean(selection && selection.role_id !== data.resolved.pin_id);
  setBtn.disabled = !changed;
  const isCustom = Boolean(selection && selection.mode === MODE_CUSTOM);
  editBtn.disabled = !isCustom;
  deleteBtn.disabled = !isCustom;
}

function showBanner(message) {
  bannerEl.textContent = message;
  bannerEl.classList.remove("hidden");
}

function hideBanner() {
  bannerEl.classList.add("hidden");
}

// ----------------------------------------------------------------------
// 写路径
// ----------------------------------------------------------------------

async function applySelection() {
  if (!selection) return;
  setBtn.disabled = true;
  try {
    await FireflyApi.post("user_role/set", {
      scope: "default",
      mode: selection.mode,
      role_id: selection.role_id,
    });
    toast("已设为当前身份：" + selection.title, "success");
  } catch (error) {
    toast("设置失败: " + error.message, "error");
  }
  await refresh();
}

async function createFlow() {
  const result = await formDialog({
    title: "新建自定义角色",
    intro: "将创建在 role/" + CUSTOM_DIR + "/ 下，并作为你的身份设定。",
    fields: [
      { name: "name", label: "名称", placeholder: "例如：星" },
      {
        name: "id",
        label: "ID",
        hint: "唯一标识，字母/数字/下划线/连字符/中文，1-64；留空则用名称",
      },
      {
        name: "body",
        label: "正文",
        type: "textarea",
        rows: 12,
        hint: "描述这个角色是谁、与流萤的关系与对话定位；不能为空",
      },
    ],
    actions: [
      { value: "cancel", label: "取消", class: "btn btn-ghost", skipValidate: true },
      { value: "create", label: "创建并设为身份", class: "btn", default: true },
    ],
    validate: validateRoleForm,
  });
  if (!result || result.action !== "create") return;

  const name = baseName(result.values.name);
  const roleId = result.values.id.trim() || name;
  try {
    await FireflyApi.post("role/save", {
      path: CUSTOM_DIR + "/" + name + ".md",
      create: true,
      frontmatter: { id: roleId, title: name, kind: "user_role" },
      body: result.values.body.trim(),
    });
  } catch (error) {
    toast("创建失败: " + error.message, "error");
    return;
  }
  await bindDefault(roleId, name);
  kind = MODE_CUSTOM;
  await refresh();
}

async function editFlow() {
  if (!selection || selection.mode !== MODE_CUSTOM) return;
  let doc;
  try {
    doc = await FireflyApi.get("role/file", { path: selection.path });
  } catch (error) {
    toast("读取失败: " + error.message, "error");
    return;
  }
  const name = doc.path.split("/").pop().replace(/\.md$/, "");
  const roleId = String(doc.frontmatter.id || name);
  const result = await formDialog({
    title: "编辑自定义角色",
    intro: "修改正文不会改变身份绑定；ID 与文件名固定。",
    fields: [
      { name: "name", label: "名称", value: name, readonly: true },
      { name: "id", label: "ID", value: roleId, readonly: true },
      { name: "body", label: "正文", type: "textarea", rows: 12, value: doc.body },
    ],
    actions: [
      { value: "cancel", label: "取消", class: "btn btn-ghost", skipValidate: true },
      { value: "save", label: "保存", class: "btn", default: true },
    ],
    validate: (values) => (values.body.trim() ? null : "正文不能为空"),
  });
  if (!result || result.action !== "save") return;

  try {
    await FireflyApi.post("role/save", {
      path: doc.path,
      create: false,
      base_rev: doc.rev,
      frontmatter: { id: roleId, title: name },
      body: result.values.body.trim(),
    });
    toast("已保存 " + name, "success");
  } catch (error) {
    toast("保存失败: " + error.message, "error");
  }
  await refresh();
}

async function deleteFlow() {
  if (!selection || selection.mode !== MODE_CUSTOM) return;
  const name = selection.path.split("/").pop();
  let entry = null;
  try {
    const tree = await FireflyApi.get("role/tree", {});
    entry = (tree.entries || []).find((item) => item.path === selection.path) || null;
  } catch (error) {
    toast("读取文件信息失败: " + error.message, "error");
    return;
  }
  if (!entry) {
    toast("文件已不存在，请刷新", "error");
    return;
  }

  const activeSessions = entry.active_sessions || 0;
  const sessionIds = entry.active_session_ids || [];
  const activeNote = activeSessions
    ? "⚠ 该文档正被 " +
      activeSessions +
      " 个使用方引用（含身份 pin / 激活上下文）：" +
      sessionIds.slice(0, 5).join("、") +
      (sessionIds.length > 5 ? " 等" : "") +
      "。"
    : "";
  let intro = "硬删除不可恢复，不会进入回收站。" + activeNote;
  const wasPinned = selection.role_id === data.resolved.pin_id;
  if (wasPinned) {
    intro += "删除后当前身份将回退到默认（内置开拓者）。";
  }

  const fields = [
    {
      name: "confirm_name",
      label: "文件名确认",
      placeholder: name,
      hint: "必须与文件名逐字符相同",
    },
  ];
  if (activeSessions) {
    fields.push({
      name: "confirm_active",
      type: "checkbox",
      label: "确认",
      text: "我确认让上述引用方立即失效",
    });
  }

  const result = await formDialog({
    title: "删除 " + name,
    intro,
    fields,
    actions: [
      { value: "cancel", label: "取消", class: "btn btn-ghost", skipValidate: true },
      { value: "delete", label: "确认删除", class: "btn btn-danger", default: true },
    ],
    validate: (values) => {
      if (values.confirm_name !== name) return "输入的文件名与目标不一致";
      if (activeSessions && !values.confirm_active) return "请勾选确认";
      return null;
    },
  });
  if (!result || result.action !== "delete") return;

  try {
    await FireflyApi.post("role/delete", {
      path: selection.path,
      base_rev: entry.rev,
      confirm_name: name,
      confirm_active: Boolean(result.values.confirm_active),
    });
  } catch (error) {
    toast("删除失败: " + error.message, "error");
    await refresh();
    return;
  }
  if (wasPinned) {
    await bindDefault("", "", { silent: true });
  }
  toast("已删除 " + name, "success");
  selection = null;
  await refresh();
}

/**
 * 把全局默认身份设为给定自定义角色（或回退内置默认）。
 *
 * @param {string} roleId 角色 id；空串表示回退内置默认（existing 哨兵）。
 * @param {string} title 角色名（用于提示）。
 * @param {object} [options] silent 为真时不弹成功提示。
 * @returns {Promise<void>}
 */
async function bindDefault(roleId, title, options = {}) {
  try {
    await FireflyApi.post("user_role/set", {
      scope: "default",
      mode: roleId ? MODE_CUSTOM : MODE_EXISTING,
      role_id: roleId,
    });
    if (!options.silent) toast("已设为当前身份：" + title, "success");
  } catch (error) {
    toast("设为身份失败: " + error.message, "error");
  }
}

/**
 * 归一化用户输入的名称：去掉可选的 .md 后缀。
 *
 * @param {string} raw 原始输入。
 * @returns {string} 规范化名称。
 */
function baseName(raw) {
  const name = String(raw || "").trim();
  return name.toLowerCase().endsWith(".md") ? name.slice(0, -3) : name;
}

function validateRoleForm(values) {
  const name = baseName(values.name);
  const roleId = (values.id || "").trim() || name;
  if (!name) return "请填写名称";
  if (/[\\/]/.test(name)) return "名称不能包含路径分隔符";
  if (name.startsWith(".") || name.startsWith("_")) return "隐藏前缀（_ / .）不允许写入";
  if (!ID_RE.test(roleId)) return "ID 仅允许字母/数字/下划线/连字符/中文，长度 1-64";
  if (!(values.body || "").trim()) return "正文不能为空";
  return null;
}
