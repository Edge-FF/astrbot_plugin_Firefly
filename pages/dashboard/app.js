/**
 * 流萤认知外壳 — 调试面板主程序（Tab 壳）
 * 只负责：Tab 注册与调度、顶部状态与刷新、启动。
 * 各 Tab 的 DOM 构建与数据加载由 views/*.js 自行负责。
 */
import { byId, el, toast } from "./modules/dom.js";
import { FireflyApi } from "./modules/api-client.js";
import * as injectionsView from "./views/injections.js";
import * as stateView from "./views/state.js";
import * as routeView from "./views/route.js";
import * as materialsView from "./views/materials.js";
import * as userRoleView from "./views/user-role.js";
import * as previewView from "./views/preview.js";
import * as statsView from "./views/stats.js";

const VIEWS = [
  injectionsView,
  stateView,
  routeView,
  materialsView,
  userRoleView,
  previewView,
  statsView,
];

const entries = [];
let currentTab = VIEWS[0].id;

const app = {
  /**
   * 更新顶栏注入计数（由注入日志视图回调）。
   *
   * @param {number} count 注入总条数。
   */
  setInjectionCount(count) {
    byId("injection-counter").textContent = "注入: " + count;
  },
};

/**
 * 由注册表生成 Tab 按钮与 section 容器，并挂载各视图。
 * 标签文案与顺序的唯一来源是 views/*.js 的导出。
 */
function buildTabs() {
  const nav = byId("tab-nav");
  const host = byId("tab-views");
  VIEWS.forEach((view, index) => {
    const button = el("button", {
      class: "tab-btn",
      type: "button",
      dataset: { tab: view.id },
      text: view.label,
    });
    const section = el("section", { id: "tab-" + view.id, class: "tab-content" });
    if (index === 0) {
      button.classList.add("active");
      section.classList.add("active");
    }
    button.addEventListener("click", () => activate(view.id));
    view.mount(section, app);
    nav.append(button);
    host.append(section);
    entries.push({ id: view.id, view, button, section });
  });
}

/**
 * 切换 Tab：只切换可见性并触发目标视图刷新。
 *
 * @param {string} tabId 目标视图 id。
 */
function activate(tabId) {
  const target = entries.find((entry) => entry.id === tabId);
  if (!target) return;
  currentTab = tabId;
  entries.forEach((entry) => {
    entry.button.classList.toggle("active", entry === target);
    entry.section.classList.toggle("active", entry === target);
  });
  target.view.refresh();
}

function refreshTab(tabId) {
  const target = entries.find((entry) => entry.id === tabId);
  return target ? target.view.refresh() : undefined;
}

async function refreshHeaderStats() {
  try {
    const stats = await FireflyApi.get("stats");
    app.setInjectionCount(stats.total_injections || 0);
  } catch (error) {
    /* 顶栏统计失败不打断面板，与原实现一致 */
  }
}

async function init() {
  const statusBadge = byId("status-badge");
  try {
    await FireflyApi.ready();
    statusBadge.textContent = "已连接";
    statusBadge.className = "status-badge status-ok";
  } catch (error) {
    statusBadge.textContent = "未连接";
    statusBadge.className = "status-badge status-err";
  }

  await refreshHeaderStats();
  await refreshTab(currentTab);
  setInterval(() => {
    if (currentTab === "injections") refreshTab("injections");
  }, 5000);
}

buildTabs();

byId("btn-refresh-all").addEventListener("click", () => {
  refreshTab(currentTab);
  toast("已刷新", "success");
});

init();
