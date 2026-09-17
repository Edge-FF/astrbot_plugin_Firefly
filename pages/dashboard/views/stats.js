/**
 * 系统统计视图：统计卡片 + 当前配置表。
 * 行为与原 app.js 的「Tab 6」保持一致。
 */
import { FireflyApi } from "../modules/api-client.js";
import { el, toast } from "../modules/dom.js";

export const id = "stats";
export const label = "系统统计";

let grid = null;
let configBox = null;

/**
 * 构建静态 DOM（不做网络请求）。
 *
 * @param {HTMLElement} container 本视图的 section 容器。
 */
export function mount(container) {
  grid = el("div", { id: "stats-grid", class: "stats-grid" });
  configBox = el("div", { id: "stats-config", class: "stats-config" });
  container.append(grid, configBox);
}

/**
 * 拉取统计数据与配置并渲染。
 *
 * @returns {Promise<void>}
 */
export async function refresh() {
  try {
    const stats = await FireflyApi.get("stats");
    const config = await FireflyApi.get("config");
    renderStats(stats);
    renderConfig(config);
  } catch (error) {
    toast("加载统计失败: " + error.message, "error");
  }
}

function renderStats(stats) {
  const cards = [
    [stats.total_sessions || 0, "活跃会话"],
    [stats.total_injections || 0, "总注入次数"],
    [(stats.llm_route_ratio * 100 || 0).toFixed(0) + "%", "LLM路由占比"],
    [(stats.success_rate * 100 || 0).toFixed(0) + "%", "注入成功率"],
    [stats.over_budget_count || 0, "超预算次数"],
    [stats.avg_token_estimate || 0, "平均 token"],
    [stats.total_materials || 0, "资料条目"],
  ];
  grid.replaceChildren(
    ...cards.map(([value, label]) =>
      el("div", { class: "stat-card" }, [
        el("div", { class: "stat-value", text: value }),
        el("div", { class: "stat-label", text: label }),
      ]),
    ),
  );
}

function renderConfig(config) {
  const rows = [
    ["总开关", config.enabled],
    ["Token 预算", config.max_tokens],
    ["Tier1 保留", config.tier1_reserved],
    ["LLM 路由", config.router_use_llm],
    ["LLM 超时", config.router_llm_timeout + "s"],
    ["关键词兜底", config.router_fallback_to_keyword],
    ["路由缓存", config.router_cache_enabled],
    ["心情衰减", config.decay_hours + "h"],
    ["最大话题", config.max_topics],
    ["Skill TTL", config.default_skill_ttl],
    ["Lore TTL", config.default_lore_ttl],
    ["Narrative TTL", config.default_narrative_ttl],
    ["衰减量", config.strength_decay_per_turn],
    ["最小strength", config.min_strength],
    ["缓存上限", config.content_cache_max_entries],
  ];
  configBox.replaceChildren(
    el("h4", { style: { marginBottom: "8px" }, text: "当前配置" }),
    el("table", { class: "config-table" }, [
      el("thead", {}, [
        el("tr", {}, [el("th", { text: "配置项" }), el("th", { text: "当前值" })]),
      ]),
      el(
        "tbody",
        {},
        rows.map(([name, value]) =>
          el("tr", {}, [
            el("td", { text: name }),
            el("td", { class: "config-value", text: value }),
          ]),
        ),
      ),
    ]),
  );
}
