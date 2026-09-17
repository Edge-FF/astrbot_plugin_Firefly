/**
 * 注入预览视图：模拟用户消息，展示 Token 预算与注入 XML。
 * 行为与原 app.js 的「Tab 5」保持一致。
 */
import { FireflyApi } from "../modules/api-client.js";
import { el, highlightXml, toast } from "../modules/dom.js";

export const id = "preview";
export const label = "注入预览";

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
    id: "preview-text",
    class: "textarea",
    rows: "2",
    placeholder: "模拟用户消息...",
  });
  sessionSelect = el("select", { id: "preview-session", class: "select-sm" }, [
    el("option", { value: "", text: "空状态" }),
  ]);
  const previewBtn = el("button", { id: "btn-preview", class: "btn", type: "button", text: "生成预览" });
  result = el("div", { id: "preview-result", class: "preview-result empty", text: "输入消息后生成预览" });

  container.append(
    el("div", { class: "preview-area" }, [textInput, sessionSelect, previewBtn]),
    result,
  );

  previewBtn.addEventListener("click", async () => {
    const text = textInput.value.trim();
    if (!text) {
      toast("请输入消息", "error");
      return;
    }
    const sessionId = sessionSelect.value;
    try {
      const data = await FireflyApi.post("injection/preview", { text, session_id: sessionId || "" });
      renderResult(data);
    } catch (error) {
      toast("预览失败: " + error.message, "error");
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
    sessionSelect.replaceChildren(el("option", { value: "", text: "空状态" }));
    (data.sessions || []).forEach((session) => {
      sessionSelect.append(el("option", { value: session.session_id, text: session.session_id.slice(0, 40) }));
    });
  } catch (error) {
    /* 会话列表拉取失败不影响预览本身 */
  }
}

function renderResult(data) {
  const budget = data.budget_breakdown || {};
  const activeEntries = budget.active_entries || [];
  const truncated = data.truncated_ids || [];
  const totalBudget = budget.total_budget || 1500;
  const fillWidth = (used) => Math.min(100, (used / totalBudget) * 100) + "%";

  const budgetBar = (barLabel, used, reserved, fillClass) =>
    el("div", { class: "budget-bar" }, [
      el("div", { class: "bar-label" }, [
        el("span", { text: barLabel }),
        el("span", { text: used + "/" + reserved + " tokens" }),
      ]),
      el("div", { class: "bar-track" }, [
        el("div", { class: "bar-fill " + fillClass, style: { width: fillWidth(used) } }),
      ]),
    ]);

  const activeBars = activeEntries.map((entry) =>
    el("div", { class: "budget-bar" }, [
      el("div", { class: "bar-label" }, [
        el("span", {}, [
          entry.entry_id + " ",
          el("span", { class: "badge badge-ok", text: "s=" + entry.strength }),
        ]),
        el("span", { text: "~" + entry.token_est + " tokens" }),
      ]),
      el("div", { class: "bar-track" }, [
        el("div", { class: "bar-fill bar-active", style: { width: fillWidth(entry.token_est) } }),
      ]),
    ]),
  );

  result.replaceChildren(
    el("div", { class: "budget-bars" }, [
      el("h4", {
        style: { marginBottom: "8px" },
        text: "Token 预算 (估计" + data.token_estimate + "/" + totalBudget + ")",
      }),
      data.over_budget
        ? el("div", { class: "badge badge-skip", style: { marginBottom: "8px" }, text: "超预算!" })
        : null,
      budgetBar(
        "Tier1 核心人格",
        budget.tier1_reserved || 600,
        budget.tier1_reserved || 600,
        "bar-tier1",
      ),
      ...activeBars,
      truncated.length
        ? el("div", { style: { marginTop: "8px" } }, [
            el("span", { class: "badge badge-skip", text: "被裁剪: " + truncated.join(", ") }),
          ])
        : null,
    ]),
    el("h4", { style: { marginBottom: "8px" }, text: "注入 XML 预览" }),
    el("pre", {
      class: "xml-viewer",
      style: { maxHeight: "400px" },
      html: highlightXml(data.injection_xml || "(空)"),
    }),
    el("div", {
      style: { marginTop: "8px", fontSize: "12px", color: "var(--text-muted)" },
      text: "路由命中: " + ((data.route_needed_ids || []).join(", ") || "无"),
    }),
  );
}
