/**
 * 注入日志视图：注入记录列表 + XML 详情。
 * 行为与原 app.js 的「Tab 1」保持一致。
 */
import { FireflyApi } from "../modules/api-client.js";
import {
  confirmDialog,
  debounce,
  el,
  formatTime,
  highlightXml,
  on,
  toast,
} from "../modules/dom.js";

export const id = "injections";
export const label = "注入日志";

let app = null;
let list = null;
let detail = null;
let xmlViewer = null;
let filterSession = null;
let filterSource = null;
let filterSearch = null;
let items = [];

/**
 * 构建静态 DOM 并绑定事件（不做网络请求）。
 *
 * @param {HTMLElement} container 本视图的 section 容器。
 * @param {{ setInjectionCount: (count: number) => void }} appApi 壳层提供的接口。
 */
export function mount(container, appApi) {
  app = appApi;

  filterSession = el("select", { id: "filter-session", class: "select-sm" }, [
    el("option", { value: "", text: "全部会话" }),
  ]);
  filterSource = el("select", { id: "filter-source", class: "select-sm" }, [
    el("option", { value: "", text: "全部来源" }),
    el("option", { value: "llm", text: "LLM路由" }),
    el("option", { value: "keyword", text: "关键词路由" }),
  ]);
  filterSearch = el("input", {
    id: "filter-search",
    class: "input-sm",
    type: "text",
    placeholder: "搜索消息内容...",
  });
  const refreshBtn = el("button", {
    id: "btn-inj-refresh",
    class: "btn btn-sm",
    type: "button",
    text: "刷新",
  });
  const clearBtn = el("button", {
    id: "btn-inj-clear",
    class: "btn btn-sm btn-danger",
    type: "button",
    text: "清空日志",
  });

  list = el("div", { id: "injection-list", class: "injection-list" });
  xmlViewer = el("pre", { id: "injection-xml-viewer", class: "xml-viewer" });
  const closeDetailBtn = el("button", {
    id: "btn-close-detail",
    class: "btn btn-sm",
    type: "button",
    text: "关闭",
  });
  detail = el("div", { id: "injection-detail", class: "injection-detail hidden" }, [
    el("div", { class: "detail-header" }, [el("h3", { text: "注入详情" }), closeDetailBtn]),
    xmlViewer,
  ]);

  container.append(
    el("div", { class: "toolbar" }, [filterSession, filterSource, filterSearch, refreshBtn, clearBtn]),
    list,
    detail,
  );

  filterSession.addEventListener("change", refresh);
  filterSource.addEventListener("change", refresh);
  filterSearch.addEventListener("input", debounce(refresh, 300));
  refreshBtn.addEventListener("click", refresh);
  clearBtn.addEventListener("click", async () => {
    const sessionId = filterSession.value;
    const scope = sessionId ? "会话 " + sessionId + " 的" : "全部";
    const confirmed = await confirmDialog("清空" + scope + "注入日志？", {
      title: "清空注入日志",
      danger: true,
    });
    if (!confirmed) return;
    try {
      const data = await FireflyApi.post("injections/clear", { session_id: sessionId || "" });
      toast("已清空 " + (data.removed || 0) + " 条", "success");
      refresh();
    } catch (error) {
      toast("清空失败: " + error.message, "error");
    }
  });
  closeDetailBtn.addEventListener("click", () => detail.classList.add("hidden"));

  on(list, "click", "[data-action]", (target) => {
    if (target.dataset.action === "toggle") {
      target.closest(".injection-card").classList.toggle("expanded");
    } else if (target.dataset.action === "xml") {
      viewXml(target.dataset.rid);
    }
  });
}

/**
 * 按当前筛选条件拉取注入记录并渲染。
 *
 * @returns {Promise<void>}
 */
export async function refresh() {
  const params = {};
  const sessionId = filterSession.value;
  const source = filterSource.value;
  if (sessionId) params.session_id = sessionId;
  if (source) params.source = source;
  try {
    const data = await FireflyApi.get("injections", params);
    items = data.items || [];
    fillSessionFilter(data.session_ids || []);
    app.setInjectionCount(data.total || 0);
    render();
  } catch (error) {
    toast("加载注入日志失败: " + error.message, "error");
  }
}

/**
 * 重建会话筛选下拉，保留当前选中值。
 *
 * @param {string[]} sessionIds 会话 id 列表。
 */
function fillSessionFilter(sessionIds) {
  const current = filterSession.value;
  filterSession.replaceChildren(el("option", { value: "", text: "全部会话" }));
  sessionIds.forEach((sessionId) => {
    filterSession.append(el("option", { value: sessionId, text: sessionId.slice(0, 40) }));
  });
  filterSession.value = current;
}

function render() {
  const keyword = filterSearch.value.toLowerCase().trim();
  const visible = keyword
    ? items.filter((item) => item.user_msg.toLowerCase().indexOf(keyword) !== -1)
    : items;
  if (visible.length === 0) {
    list.replaceChildren(el("div", { class: "empty", text: "暂无注入记录" }));
    return;
  }
  list.replaceChildren(...visible.slice(0, 100).map(renderCard));
}

function renderCard(record) {
  const signals = [];
  if (record.route_signals_emotion) {
    signals.push("用户情绪:" + record.route_signals_emotion);
  }
  const sourceBadge = record.injected_successfully
    ? el("span", {
        class: "badge badge-" + (record.route_source === "llm" ? "llm" : "kw"),
        text: record.route_source,
      })
    : el("span", { class: "badge badge-skip", text: "跳过: " + (record.skipped_reason || "?") });
  const budgetBadge = record.over_budget
    ? el("span", { class: "badge badge-tier", text: "超预算" })
    : null;
  const activeText =
    (record.active_context_after || [])
      .map((entry) => entry.entry_id + "(TTL=" + entry.remaining_ttl + ")")
      .join(", ") || "无";

  const header = el("div", { class: "panel-header", dataset: { action: "toggle" } }, [
    el("span", { class: "panel-title", text: formatTime(record.timestamp) }),
    el("div", { class: "panel-meta" }, [
      el("span", { text: "会话:" + (record.session_id || "").slice(0, 30) }),
      sourceBadge,
      budgetBadge,
      el("span", { text: record.token_estimate + " tokens" }),
    ]),
  ]);

  const inner = el("div", { class: "panel-body-inner" }, [
    el("div", {}, [el("strong", { text: "用户:" }), " " + record.user_msg]),
    el("div", {}, [
      el("strong", { text: "路由:" }),
      " needed=[" +
        (record.route_needed_ids || []).join(", ") +
        "] signals={" +
        signals.join(", ") +
        "}",
    ]),
    el("div", {}, [el("strong", { text: "激活后:" }), " " + activeText]),
    record.truncated_ids && record.truncated_ids.length
      ? el("div", {}, [el("strong", { text: "被裁剪:" }), " " + record.truncated_ids.join(", ")])
      : null,
    el("div", { class: "panel-actions" }, [
      el("button", {
        class: "btn btn-sm",
        type: "button",
        dataset: { action: "xml", rid: record.record_id },
        text: "查看XML",
      }),
    ]),
  ]);

  return el("div", { class: "panel injection-card" }, [header, el("div", { class: "panel-body" }, [inner])]);
}

async function viewXml(recordId) {
  try {
    const data = await FireflyApi.get("injections/detail", { record_id: recordId });
    detail.classList.remove("hidden");
    xmlViewer.innerHTML = highlightXml(data.injection_xml || "(空)");
  } catch (error) {
    toast("获取详情失败: " + error.message, "error");
  }
}
