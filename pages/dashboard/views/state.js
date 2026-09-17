/**
 * 状态面板视图：会话选择、心情、激活上下文条目管理。
 * 行为与原 app.js 的「Tab 2」保持一致。
 */
import { FireflyApi } from "../modules/api-client.js";
import { confirmDialog, el, on, toast } from "../modules/dom.js";

export const id = "state";
export const label = "状态面板";

const MOODS = ["平静", "开心", "难过", "生气", "疲惫", "惊喜", "想念", "委屈", "心疼"];

let panel = null;
let sessionSelect = null;
let newSessionInput = null;
let entryIdInput = null;
let entryTtlInput = null;
let selectedSession = "";
let panelSession = "";

/**
 * 构建静态 DOM 并绑定事件（不做网络请求）。
 *
 * @param {HTMLElement} container 本视图的 section 容器。
 */
export function mount(container) {
  sessionSelect = el("select", { id: "state-session-select", class: "select-sm" }, [
    el("option", { value: "", text: "选择会话..." }),
  ]);
  newSessionInput = el("input", {
    id: "state-new-session",
    class: "input-sm",
    type: "text",
    placeholder: "新建会话ID...",
  });
  const newBtn = el("button", { id: "btn-state-new", class: "btn btn-sm", type: "button", text: "新建" });
  const refreshBtn = el("button", {
    id: "btn-state-refresh",
    class: "btn btn-sm",
    type: "button",
    text: "刷新",
  });
  panel = el("div", { id: "state-panel", class: "state-panel" }, [
    el("div", { class: "empty", text: "请选择一个会话" }),
  ]);

  container.append(
    el("div", { class: "toolbar" }, [sessionSelect, newSessionInput, newBtn, refreshBtn]),
    panel,
  );

  sessionSelect.addEventListener("change", () => {
    const sessionId = sessionSelect.value;
    if (!sessionId) return;
    selectedSession = sessionId;
    renderPanel(sessionId);
  });
  refreshBtn.addEventListener("click", refresh);
  newBtn.addEventListener("click", () => {
    const sessionId = newSessionInput.value.trim();
    if (!sessionId) {
      toast("请输入会话ID", "error");
      return;
    }
    selectedSession = sessionId;
    sessionSelect.value = sessionId;
    renderPanel(sessionId);
  });

  on(panel, "click", "[data-action]", (target) => {
    const action = target.dataset.action;
    if (action === "activate") activate(panelSession);
    else if (action === "deactivate") deactivate(panelSession, target.dataset.entry);
    else if (action === "reset") reset(panelSession);
  });
}

/**
 * 拉取会话列表并渲染当前选中会话的状态面板。
 *
 * @returns {Promise<void>}
 */
export async function refresh() {
  try {
    const data = await FireflyApi.get("sessions");
    const sessions = data.sessions || [];
    sessionSelect.replaceChildren(el("option", { value: "", text: "选择会话..." }));
    sessions.forEach((session) => {
      sessionSelect.append(
        el("option", {
          value: session.session_id,
          text: session.session_id.slice(0, 40) + " [" + session.mood + "]",
        }),
      );
    });
    if (selectedSession) {
      sessionSelect.value = selectedSession;
      await renderPanel(selectedSession);
    } else if (sessions.length > 0) {
      selectedSession = sessions[0].session_id;
      sessionSelect.value = selectedSession;
      await renderPanel(selectedSession);
    }
  } catch (error) {
    toast("加载状态失败: " + error.message, "error");
  }
}

async function renderPanel(sessionId) {
  panelSession = sessionId;
  try {
    const data = await FireflyApi.get("sessions/detail", { session_id: sessionId });
    const activeContext = data.active_context || {};
    const entries = activeContext.entries || [];

    const moodSelect = el(
      "select",
      {},
      MOODS.map((mood) => el("option", { value: mood, text: mood })),
    );
    moodSelect.value = data.mood;
    moodSelect.addEventListener("change", () => updateField(sessionId, "mood", moodSelect.value));

    entryIdInput = el("input", {
      id: "activate-entry-id",
      class: "input-sm",
      type: "text",
      placeholder: "条目ID",
      style: { width: "140px" },
    });
    entryTtlInput = el("input", {
      id: "activate-entry-ttl",
      class: "input-sm",
      type: "number",
      min: "1",
      max: "10",
      value: "3",
      placeholder: "TTL",
      style: { width: "60px" },
    });
    const activateBtn = el("button", {
      class: "btn btn-sm",
      type: "button",
      dataset: { action: "activate" },
      text: "激活",
    });
    const resetBtn = el("button", {
      class: "btn btn-sm btn-danger",
      type: "button",
      dataset: { action: "reset" },
      text: "重置会话",
    });

    panel.replaceChildren(
      el("div", { class: "state-section" }, [
        el("h4", { text: "基本状态" }),
        el("div", { class: "state-row" }, [
          el("span", { class: "label", text: "Session ID" }),
          el("span", { class: "value", text: data.session_id }),
        ]),
        el("div", { class: "state-row" }, [el("span", { class: "label", text: "心情" }), moodSelect]),
        el("div", { class: "state-row" }, [
          el("span", { class: "label", text: "最近话题" }),
          el("span", { class: "value", text: (data.recent_topics || []).join(" | ") }),
        ]),
      ]),
      el("div", { class: "state-section" }, [
        el("h4", { text: "激活上下文 (轮次: " + (activeContext.turn_count || 0) + ")" }),
        el("div", { class: "state-entries" }, entries.length ? entries.map(renderEntry) : [renderEmptyEntries()]),
        el("div", { class: "panel-actions" }, [entryIdInput, entryTtlInput, activateBtn, resetBtn]),
      ]),
    );
  } catch (error) {
    panel.replaceChildren(el("div", { class: "empty", text: "加载失败: " + error.message }));
  }
}

function renderEmptyEntries() {
  return el("div", {
    style: { padding: "8px", color: "var(--text-muted)", fontSize: "12px" },
    text: "无激活条目",
  });
}

function renderEntry(entry) {
  const ttlPercent = Math.max(0, Math.min(100, (entry.remaining_ttl / 4) * 100));
  const strengthClass = entry.strength > 0.7 ? "ttl-high" : entry.strength > 0.4 ? "ttl-mid" : "ttl-low";
  return el("div", { class: "state-entry" }, [
    el("span", { class: "badge badge-kind", text: entry.entry_id.slice(0, 30) }),
    el("span", {
      style: { fontSize: "11px", color: "var(--text-muted)" },
      text: "TTL:" + entry.remaining_ttl + " s:" + entry.strength,
    }),
    el("div", { class: "ttl-bar" }, [
      el("div", { class: "ttl-fill " + strengthClass, style: { width: ttlPercent + "%" } }),
    ]),
    el("button", {
      class: "btn btn-sm btn-ghost",
      type: "button",
      dataset: { action: "deactivate", entry: entry.entry_id },
      text: "停用",
    }),
  ]);
}

async function updateField(sessionId, field, value) {
  const body = { session_id: sessionId };
  body[field] = value;
  try {
    await FireflyApi.post("sessions/update", body);
    toast(field + " 已更新", "success");
  } catch (error) {
    toast("更新失败: " + error.message, "error");
  }
}

async function activate(sessionId) {
  const entryId = entryIdInput.value.trim();
  const ttl = parseInt(entryTtlInput.value, 10) || 3;
  if (!entryId) {
    toast("请输入条目ID", "error");
    return;
  }
  try {
    await FireflyApi.post("sessions/activate", { session_id: sessionId, entry_id: entryId, ttl });
    toast("已激活 " + entryId, "success");
    renderPanel(sessionId);
  } catch (error) {
    toast("激活失败: " + error.message, "error");
  }
}

async function deactivate(sessionId, entryId) {
  try {
    await FireflyApi.post("sessions/deactivate", { session_id: sessionId, entry_id: entryId });
    toast("已停用 " + entryId, "success");
    renderPanel(sessionId);
  } catch (error) {
    toast("停用失败: " + error.message, "error");
  }
}

async function reset(sessionId) {
  const confirmed = await confirmDialog("确认重置会话 " + sessionId + " 的全部状态？", {
    title: "重置会话",
    danger: true,
  });
  if (!confirmed) return;
  try {
    await FireflyApi.post("sessions/reset", { session_id: sessionId });
    toast("已重置", "success");
    renderPanel(sessionId);
  } catch (error) {
    toast("重置失败: " + error.message, "error");
  }
}
