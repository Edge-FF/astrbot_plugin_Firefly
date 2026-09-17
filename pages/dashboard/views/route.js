/**
 * 路由测试视图：输入文本 + 可选会话，展示命中/未命中条目。
 * 行为与原 app.js 的「Tab 3」保持一致。
 */
import { FireflyApi } from "../modules/api-client.js";
import { el, toast } from "../modules/dom.js";

export const id = "route";
export const label = "路由测试";

let textInput = null;
let sessionSelect = null;
let result = null;

/**
 * 构建静态 DOM 并绑定事件（不做网络请求）。
 *
 * @param {HTMLElement} container 本视图的 section 容器。
 */
export function mount(container) {
  textInput = el("textarea", {
    id: "route-text",
    class: "textarea",
    rows: "3",
    placeholder: "输入测试文本，如：「我好想你啊，还记得我们在星穹列车上的日子吗？」",
  });
  sessionSelect = el("select", { id: "route-session", class: "select-sm" }, [
    el("option", { value: "", text: "使用空状态" }),
  ]);
  const testBtn = el("button", { id: "btn-route-test", class: "btn", type: "button", text: "测试路由" });
  result = el("div", { id: "route-result", class: "route-result empty", text: "输入文本后点击测试" });

  container.append(
    el("div", { class: "route-tester" }, [
      el("div", { class: "route-input-area" }, [textInput, sessionSelect, testBtn]),
      result,
    ]),
  );

  testBtn.addEventListener("click", async () => {
    const text = textInput.value.trim();
    if (!text) {
      toast("请输入测试文本", "error");
      return;
    }
    const sessionId = sessionSelect.value;
    try {
      const data = await FireflyApi.post("route/test", { text, session_id: sessionId || "" });
      renderResult(data);
    } catch (error) {
      toast("路由测试失败: " + error.message, "error");
    }
  });
}

/**
 * 刷新会话下拉（失败时静默，与原实现一致）。
 *
 * @returns {Promise<void>}
 */
export async function refresh() {
  try {
    const data = await FireflyApi.get("sessions");
    sessionSelect.replaceChildren(el("option", { value: "", text: "使用空状态" }));
    (data.sessions || []).forEach((session) => {
      sessionSelect.append(el("option", { value: session.session_id, text: session.session_id.slice(0, 40) }));
    });
  } catch (error) {
    /* 会话列表拉取失败不影响路由测试本身 */
  }
}

function renderResult(data) {
  const signals = [];
  if (data.signals && data.signals.user_emotion) {
    signals.push("用户情绪=" + data.signals.user_emotion);
  }

  const details = (data.detail || []).map((item) =>
    el("div", { class: "result-item" }, [
      el("h4", {}, [
        el("span", { class: "badge badge-kind", text: item.kind }),
        " " + item.id + " ",
        el("small", { text: "T" + item.tier + " pri=" + item.priority }),
      ]),
      el("div", { class: "hit-detail", text: (item.content_preview || "").slice(0, 200) }),
    ]),
  );
  if (details.length === 0) {
    details.push(el("div", { class: "empty", text: "无命中条目" }));
  }

  result.replaceChildren(
    el("div", { style: { marginBottom: "12px" } }, [
      el("strong", { text: "来源:" }),
      " ",
      el("span", { class: "badge badge-" + (data.source === "llm" ? "llm" : "kw"), text: data.source }),
      " ",
      signals.length ? el("span", {}, [el("strong", { text: "信号:" }), " " + signals.join(" | ")]) : null,
    ]),
    el("div", {}, [
      el("strong", { text: "命中条目:" }),
      " " + ((data.needed_ids || []).join(", ") || "无"),
    ]),
    el("h4", { style: { margin: "12px 0 6px" }, text: "命中详情" }),
    ...details,
    el("h4", { style: { margin: "12px 0 6px" }, text: "未命中条目" }),
    el("div", {
      style: { fontSize: "11px", color: "var(--text-muted)" },
      text: (data.not_matched || []).slice(0, 20).join(", ") || "无",
    }),
  );
}
