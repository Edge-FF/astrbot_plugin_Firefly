/**
 * 流萤认知外壳 — 调试面板主程序
 * 以 ES Module 加载，并显式 import API 客户端，保证脚本在 bridge 注入后执行。
 * UI 事件在模块加载时同步绑定，不依赖 API 是否就绪，确保标签页始终可切换。
 */
import { FireflyApi } from "./modules/api-client.js";

(function () {
  "use strict";

  var api = FireflyApi;

  // ===== 全局状态 =====
  var state = {
    currentTab: "injections",
    sessions: [],
    selectedSession: "",
    injectionItems: [],
  };

  // 同步绑定 UI 事件（先于异步初始化，保证按钮可用）
  setupUI();

  // ===== 初始化 =====
  async function init() {
    try {
      await api.ready();
      document.getElementById("status-badge").textContent = "已连接";
      document.getElementById("status-badge").className = "status-badge status-ok";
    } catch (e) {
      document.getElementById("status-badge").textContent = "未连接";
      document.getElementById("status-badge").className = "status-badge status-err";
    }

    await refreshStats();
    await loadTabContent(state.currentTab);
    setInterval(function () {
      if (state.currentTab === "injections") { loadInjections(); }
    }, 5000);
  }

  // ===== UI 事件绑定 =====
  function setupUI() {
    // Tabs
    document.querySelectorAll(".tab-btn").forEach(function (btn) {
      btn.addEventListener("click", function () { switchTab(btn.dataset.tab); });
    });

    // Tab 1: 注入日志
    byId("btn-inj-refresh").addEventListener("click", loadInjections);
    byId("filter-session").addEventListener("change", loadInjections);
    byId("filter-source").addEventListener("change", loadInjections);
    byId("filter-search").addEventListener("input", debounce(loadInjections, 300));
    byId("btn-inj-clear").addEventListener("click", function () {
      if (!confirm("清空所有注入日志？")) return;
      var sid = byId("filter-session").value;
      api.post("sessions/reset", { session_id: sid || "" }).then(function () {
        toast("已清空", "success");
        loadInjections();
      }).catch(function (e) { toast("清空失败: " + e.message, "error"); });
    });
    byId("btn-close-detail").addEventListener("click", function () {
      document.getElementById("injection-detail").classList.add("hidden");
    });

    // Tab 2: 状态面板
    byId("state-session-select").addEventListener("change", function () {
      var sid = this.value;
      if (sid) { state.selectedSession = sid; renderStatePanel(sid); }
    });
    byId("btn-state-refresh").addEventListener("click", function () { loadTabContent("state"); });
    byId("btn-state-new").addEventListener("click", function () {
      var sid = byId("state-new-session").value.trim();
      if (!sid) { toast("请输入会话ID", "error"); return; }
      state.selectedSession = sid;
      byId("state-session-select").value = sid;
      renderStatePanel(sid);
    });

    // Tab 3: 路由测试
    byId("btn-route-test").addEventListener("click", function () {
      var text = byId("route-text").value.trim();
      if (!text) { toast("请输入测试文本", "error"); return; }
      var sid = byId("route-session").value;
      api.post("route/test", { text: text, session_id: sid || "" }).then(renderRouteResult)
        .catch(function (e) { toast("路由测试失败: " + e.message, "error"); });
    });

    // Tab 4: 资料浏览
    byId("mat-tier-filter").addEventListener("change", loadMaterials);
    byId("mat-search").addEventListener("input", debounce(loadMaterials, 300));
    byId("btn-mat-reload").addEventListener("click", function () {
      api.post("materials/reload").then(function (d) {
        toast("重载完成: " + d.total + " 条", "success");
        loadMaterials();
      }).catch(function (e) { toast("重载失败: " + e.message, "error"); });
    });
    byId("btn-close-mat").addEventListener("click", function () {
      document.getElementById("material-detail").classList.add("hidden");
    });

    // Tab 5: 注入预览
    byId("btn-preview").addEventListener("click", function () {
      var text = byId("preview-text").value.trim();
      if (!text) { toast("请输入消息", "error"); return; }
      var sid = byId("preview-session").value;
      api.post("injection/preview", { text: text, session_id: sid || "" }).then(renderPreviewResult)
        .catch(function (e) { toast("预览失败: " + e.message, "error"); });
    });

    // 刷新按钮
    byId("btn-refresh-all").addEventListener("click", function () {
      loadTabContent(state.currentTab);
      toast("已刷新", "success");
    });
  }

  // ===== Tab 切换 =====
  function switchTab(name) {
    state.currentTab = name;
    document.querySelectorAll(".tab-btn").forEach(function (b) { b.classList.remove("active"); });
    document.querySelector(".tab-btn[data-tab='" + name + "']").classList.add("active");
    document.querySelectorAll(".tab-content").forEach(function (s) { s.classList.remove("active"); });
    document.getElementById("tab-" + name).classList.add("active");
    loadTabContent(name);
  }

  async function loadTabContent(name) {
    switch (name) {
      case "injections": await loadInjections(); break;
      case "state": await loadStateTab(); break;
      case "route": await loadRouteTab(); break;
      case "materials": await loadMaterials(); break;
      case "preview": await loadPreviewTab(); break;
      case "stats": await loadStats(); break;
    }
  }

  // ===== Tab 1: 注入日志 =====
  async function loadInjections() {
    var f = {};
    var sid = byId("filter-session").value;
    var src = byId("filter-source").value;
    if (sid) f.session_id = sid;
    if (src) f.source = src;
    try {
      var data = await api.get("injections", f);
      state.injectionItems = data.items || [];
      var ids = data.session_ids || [];
      populateSelect("filter-session", "", ids);
      byId("injection-counter").textContent = "注入: " + (data.total || 0);
      renderInjectionList();
    } catch (e) { toast("加载注入日志失败: " + e.message, "error"); }
  }

  function renderInjectionList() {
    var search = byId("filter-search").value.toLowerCase().trim();
    var items = state.injectionItems;
    if (search) { items = items.filter(function (i) { return i.user_msg.toLowerCase().indexOf(search) !== -1; }); }
    var container = byId("injection-list");
    if (items.length === 0) {
      container.innerHTML = '<div class="empty-state">暂无注入记录</div>';
      return;
    }
    container.innerHTML = items.slice(0, 100).map(function (r) {
      var ts = new Date(r.timestamp * 1000).toLocaleString("zh-CN");
      var sigs = [];
      if (r.route_signals_emotion) sigs.push("用户情绪:" + esc(r.route_signals_emotion));
      var tag = r.injected_successfully
        ? '<span class="tag tag-' + (r.route_source === 'llm' ? 'llm' : 'kw') + '">' + r.route_source + '</span>'
        : '<span class="tag tag-skip">跳过: ' + (r.skipped_reason || '?') + '</span>';
      var budget = r.over_budget ? '<span class="tag tag-tier">超预算</span>' : "";
      var activeStr = (r.active_context_after || []).map(function (a) {
        return a.entry_id + "(TTL=" + a.remaining_ttl + ")";
      }).join(", ") || "无";
      return '<div class="card injection-card" data-rid="' + r.record_id + '">'
        + '<div class="card-header" onclick="this.parentElement.classList.toggle(\'expanded\')">'
        + '<span class="card-title">' + ts + '</span>'
        + '<div class="card-meta"><span>会话:' + esc((r.session_id || "").slice(0, 30)) + '</span>' + tag + budget + '<span>' + r.token_estimate + ' tokens</span></div>'
        + '</div><div class="card-body"><div class="card-body-inner">'
        + '<div><strong>用户:</strong> ' + esc(r.user_msg) + '</div>'
        + '<div><strong>路由:</strong> needed=[' + (r.route_needed_ids || []).join(', ') + '] signals={' + sigs.join(', ') + '}</div>'
        + '<div><strong>激活后:</strong> ' + activeStr + '</div>'
        + (r.truncated_ids && r.truncated_ids.length ? '<div><strong>被裁剪:</strong> ' + r.truncated_ids.join(', ') + '</div>' : '')
        + '<div class="card-actions"><button class="btn btn-sm" onclick="FF.viewXML(\'' + r.record_id + '\')">查看XML</button></div>'
        + '</div></div></div>';
    }).join("");
  }

  window.FF = window.FF || {};
  window.FF.viewXML = async function (rid) {
    try {
      var data = await api.get("injections/detail", { record_id: rid });
      byId("injection-detail").classList.remove("hidden");
      byId("injection-xml-viewer").innerHTML = highlightXml(data.injection_xml || "(空)");
    } catch (e) { toast("获取详情失败: " + e.message, "error"); }
  };

  // ===== Tab 2: 状态面板 =====
  async function loadStateTab() {
    try {
      var data = await api.get("sessions");
      state.sessions = data.sessions || [];
      var sel = byId("state-session-select");
      sel.innerHTML = '<option value="">选择会话...</option>';
      state.sessions.forEach(function (s) {
        sel.innerHTML += '<option value="' + esc(s.session_id) + '">' + esc(s.session_id.slice(0, 40)) + ' [' + esc(s.mood) + ']</option>';
      });
      if (state.selectedSession) { sel.value = state.selectedSession; }
      if (state.selectedSession) { await renderStatePanel(state.selectedSession); }
      else if (state.sessions.length > 0) { state.selectedSession = state.sessions[0].session_id; sel.value = state.selectedSession; await renderStatePanel(state.selectedSession); }
    } catch (e) { toast("加载状态失败: " + e.message, "error"); }
  }

  async function renderStatePanel(sid) {
    try {
      var s = await api.get("sessions/detail", { session_id: sid });
      var ac = s.active_context || {};
      var entries = ac.entries || [];
      var entriesHtml = entries.length ? entries.map(function (e) {
        var ttlPct = Math.max(0, Math.min(100, (e.remaining_ttl / 4) * 100));
        var cls = e.strength > 0.7 ? "ttl-high" : (e.strength > 0.4 ? "ttl-mid" : "ttl-low");
        return '<div class="state-entry"><span class="tag tag-kind">' + e.entry_id.slice(0, 30)
          + '</span><span style="font-size:11px;color:var(--text-muted)">TTL:' + e.remaining_ttl + ' s:' + e.strength + '</span>'
          + '<div class="ttl-bar"><div class="ttl-fill ' + cls + '" style="width:' + ttlPct + '%"></div></div>'
          + '<button class="btn btn-sm btn-ghost" onclick="FF.deactivate(\'' + sid + '\',\'' + e.entry_id + '\')">停用</button></div>';
      }).join("") : '<div style="padding:8px;color:var(--text-muted);font-size:12px">无激活条目</div>';

      byId("state-panel").innerHTML =
        '<div class="state-section"><h4>基本状态</h4>'
        + '<div class="state-row"><span class="label">Session ID</span><span class="value">' + esc(s.session_id) + '</span></div>'
        + moodSelect(sid, s.mood)
        + '<div class="state-row"><span class="label">最近话题</span><span class="value">' + (s.recent_topics || []).map(esc).join(' | ') + '</span></div>'
        + '</div>'
        + '<div class="state-section"><h4>激活上下文 (轮次: ' + (ac.turn_count || 0) + ')</h4>'
        + '<div class="state-entries">' + entriesHtml + '</div>'
        + '<div class="card-actions" style="margin-top:8px">'
        + '<input id="activate-entry-id" class="input-sm" placeholder="条目ID" style="width:140px">'
        + '<input id="activate-entry-ttl" class="input-sm" placeholder="TTL" value="3" style="width:60px" type="number" min="1" max="10">'
        + '<button class="btn btn-sm" onclick="FF.activate(\'' + sid + '\')">激活</button>'
        + '<button class="btn btn-sm btn-danger" onclick="FF.reset(\'' + sid + '\')">重置会话</button>'
        + '</div></div>';
    } catch (e) {
      byId("state-panel").innerHTML = '<div class="empty-state">加载失败: ' + e.message + '</div>';
    }
  }

  function moodSelect(sid, current) {
    var moods = ["平静", "开心", "难过", "生气", "疲惫", "惊喜", "想念", "委屈", "心疼"];
    var html = '<div class="state-row"><span class="label">心情</span><select onchange="FF.updateField(\'' + sid + '\',\'mood\',this.value)">';
    moods.forEach(function (m) { html += '<option value="' + m + '"' + (current === m ? ' selected' : '') + '>' + m + '</option>'; });
    html += '</select></div>';
    return html;
  }

  window.FF.updateField = async function (sid, field, value) {
    var body = { session_id: sid };
    body[field] = value;
    try {
      await api.post("sessions/update", body);
      toast(field + " 已更新", "success");
    } catch (e) { toast("更新失败: " + e.message, "error"); }
  };

  window.FF.activate = async function (sid) {
    var eid = byId("activate-entry-id").value.trim();
    var ttl = parseInt(byId("activate-entry-ttl").value) || 3;
    if (!eid) { toast("请输入条目ID", "error"); return; }
    try {
      await api.post("sessions/activate", { session_id: sid, entry_id: eid, ttl: ttl });
      toast("已激活 " + eid, "success");
      renderStatePanel(sid);
    } catch (e) { toast("激活失败: " + e.message, "error"); }
  };

  window.FF.deactivate = async function (sid, eid) {
    try {
      await api.post("sessions/deactivate", { session_id: sid, entry_id: eid });
      toast("已停用 " + eid, "success");
      renderStatePanel(sid);
    } catch (e) { toast("停用失败: " + e.message, "error"); }
  };

  window.FF.reset = async function (sid) {
    if (!confirm("确认重置会话 " + sid + " 的全部状态？")) return;
    try {
      await api.post("sessions/reset", { session_id: sid });
      toast("已重置", "success");
      renderStatePanel(sid);
    } catch (e) { toast("重置失败: " + e.message, "error"); }
  };

  // ===== Tab 3: 路由测试 =====
  async function loadRouteTab() {
    try {
      var data = await api.get("sessions");
      var sel = byId("route-session");
      sel.innerHTML = '<option value="">使用空状态</option>';
      (data.sessions || []).forEach(function (s) {
        sel.innerHTML += '<option value="' + s.session_id + '">' + s.session_id.slice(0, 40) + '</option>';
      });
    } catch (e) { /* ignore */ }
  }

  function renderRouteResult(data) {
    var sigs = [];
    if (data.signals && data.signals.user_emotion) sigs.push("用户情绪=" + data.signals.user_emotion);
    var detailHtml = (data.detail || []).map(function (d) {
      return '<div class="result-item"><h4><span class="tag tag-kind">' + d.kind + '</span> ' + d.id + ' <small>T' + d.tier + ' pri=' + d.priority + '</small></h4><div class="hit-detail">' + esc((d.content_preview || '').slice(0, 200)) + '</div></div>';
    }).join("") || '<div class="empty-state">无命中条目</div>';
    byId("route-result").innerHTML =
      '<div style="margin-bottom:12px"><strong>来源:</strong> <span class="tag tag-' + (data.source === 'llm' ? 'llm' : 'kw') + '">' + data.source + '</span> '
      + (sigs.length ? '<strong>信号:</strong> ' + sigs.join(' | ') : '') + '</div>'
      + '<div><strong>命中条目:</strong> ' + ((data.needed_ids || []).join(', ') || '无') + '</div>'
      + '<h4 style="margin:12px 0 6px">命中详情</h4>' + detailHtml
      + '<h4 style="margin:12px 0 6px">未命中条目</h4><div style="font-size:11px;color:var(--text-muted)">' + ((data.not_matched || []).slice(0, 20).join(', ') || '无') + '</div>';
  }

  // ===== Tab 4: 资料浏览 =====
  async function loadMaterials() {
    var p = {};
    var tier = byId("mat-tier-filter").value;
    var search = byId("mat-search").value.trim();
    if (tier) p.tier = tier;
    if (search) p.search = search;
    try {
      var data = await api.get("materials", p);
      var items = data.items || [];
      var container = byId("materials-list");
      if (items.length === 0) { container.innerHTML = '<div class="empty-state">无匹配资料</div>'; return; }
      container.innerHTML = items.map(function (m) {
        return '<div class="mat-entry" onclick="FF.viewMat(\'' + m.id + '\')">'
          + '<span class="mat-id">' + m.id + '</span>'
          + '<span class="mat-title">' + esc(m.title) + '</span>'
          + '<span class="mat-meta"><span class="tag tag-tier">T' + m.tier + '</span>'
          + '<span class="tag tag-kind">' + m.kind + '</span>'
          + '<span>TTL:' + m.default_ttl + ' pri:' + m.priority + '</span>'
          + (m.is_loaded ? '<span class="tag tag-ok">已加载</span>' : '') + '</span></div>';
      }).join("");
    } catch (e) { toast("加载资料失败: " + e.message, "error"); }
  }

  window.FF.viewMat = async function (eid) {
    try {
      var m = await api.get("materials/detail", { entry_id: eid });
      byId("material-detail").classList.remove("hidden");
      byId("mat-detail-title").innerHTML = '<span class="tag tag-tier">T' + m.tier + '</span><span class="tag tag-kind">' + m.kind + '</span> ' + m.id + ' — ' + esc(m.title);
      byId("mat-detail-content").textContent = m.content || "(空)";
    } catch (e) { toast("获取资料失败: " + e.message, "error"); }
  };

  // ===== Tab 5: 注入预览 =====
  async function loadPreviewTab() {
    try {
      var data = await api.get("sessions");
      var sel = byId("preview-session");
      sel.innerHTML = '<option value="">空状态</option>';
      (data.sessions || []).forEach(function (s) {
        sel.innerHTML += '<option value="' + s.session_id + '">' + s.session_id.slice(0, 40) + '</option>';
      });
    } catch (e) { /* ignore */ }
  }

  function renderPreviewResult(data) {
    var budget = data.budget_breakdown || {};
    var activeE = budget.active_entries || [];
    var truncated = data.truncated_ids || [];
    var totalBudget = budget.total_budget || 1500;

    function bar(label, used, reserved, cls) {
      var pct = Math.min(100, (used / totalBudget) * 100);
      return '<div class="budget-bar"><div class="bar-label"><span>' + label + '</span><span>' + used + '/' + reserved + ' tokens</span></div><div class="bar-track"><div class="bar-fill ' + cls + '" style="width:' + pct + '%"></div></div></div>';
    }

    var activeBars = activeE.map(function (a) {
      return '<div class="budget-bar"><div class="bar-label"><span>' + a.entry_id + ' <span class="tag tag-ok">s=' + a.strength + '</span></span><span>~' + a.token_est + ' tokens</span></div><div class="bar-track"><div class="bar-fill bar-active" style="width:' + Math.min(100, (a.token_est / totalBudget) * 100) + '%"></div></div></div>';
    }).join("");

    var truncatedHtml = truncated.length ? '<div style="margin-top:8px"><span class="tag tag-skip">被裁剪: ' + truncated.join(', ') + '</span></div>' : "";

    byId("preview-result").innerHTML =
      '<div class="budget-bars"><h4 style="margin-bottom:8px">Token 预算 (估计' + data.token_estimate + '/' + totalBudget + ')</h4>'
      + (data.over_budget ? '<div class="tag tag-skip" style="margin-bottom:8px">超预算!</div>' : '')
      + bar("Tier1 核心人格", budget.tier1_reserved || 600, budget.tier1_reserved || 600, "bar-tier1")
      + activeBars + truncatedHtml + '</div>'
      + '<h4 style="margin-bottom:8px">注入 XML 预览</h4>'
      + '<pre class="xml-viewer" style="max-height:400px">' + highlightXml(data.injection_xml || '(空)') + '</pre>'
      + '<div style="margin-top:8px;font-size:12px;color:var(--text-muted)">路由命中: ' + ((data.route_needed_ids || []).join(', ') || '无') + '</div>';
  }

  // ===== Tab 6: 统计 =====
  async function loadStats() {
    try {
      var stats = await api.get("stats");
      var config = await api.get("config");
      renderStats(stats);
      renderConfig(config);
    } catch (e) { toast("加载统计失败: " + e.message, "error"); }
  }

  function renderStats(s) {
    var grid = byId("stats-grid");
    grid.innerHTML =
      '<div class="stat-card"><div class="stat-value">' + (s.total_sessions || 0) + '</div><div class="stat-label">活跃会话</div></div>'
      + '<div class="stat-card"><div class="stat-value">' + (s.total_injections || 0) + '</div><div class="stat-label">总注入次数</div></div>'
      + '<div class="stat-card"><div class="stat-value">' + ((s.llm_route_ratio * 100 || 0).toFixed(0)) + '%</div><div class="stat-label">LLM路由占比</div></div>'
      + '<div class="stat-card"><div class="stat-value">' + ((s.success_rate * 100 || 0).toFixed(0)) + '%</div><div class="stat-label">注入成功率</div></div>'
      + '<div class="stat-card"><div class="stat-value">' + (s.over_budget_count || 0) + '</div><div class="stat-label">超预算次数</div></div>'
      + '<div class="stat-card"><div class="stat-value">' + (s.avg_token_estimate || 0) + '</div><div class="stat-label">平均 token</div></div>'
      + '<div class="stat-card"><div class="stat-value">' + (s.total_materials || 0) + '</div><div class="stat-label">资料条目</div></div>';
  }

  function renderConfig(c) {
    var rows = [
      ["总开关", c.enabled], ["Token 预算", c.max_tokens], ["Tier1 保留", c.tier1_reserved],
      ["LLM 路由", c.router_use_llm], ["LLM 超时", c.router_llm_timeout + "s"],
      ["关键词兜底", c.router_fallback_to_keyword], ["路由缓存", c.router_cache_enabled],
      ["心情衰减", c.decay_hours + "h"], ["最大话题", c.max_topics],
      ["Skill TTL", c.default_skill_ttl], ["Lore TTL", c.default_lore_ttl], ["Narrative TTL", c.default_narrative_ttl],
      ["衰减量", c.strength_decay_per_turn], ["最小strength", c.min_strength], ["缓存上限", c.content_cache_max_entries],
    ];
    byId("stats-config").innerHTML = '<h4 style="margin-bottom:8px">当前配置</h4><table class="config-table"><thead><tr><th>配置项</th><th>当前值</th></tr></thead><tbody>'
      + rows.map(function (r) { return '<tr><td>' + r[0] + '</td><td class="config-value">' + r[1] + '</td></tr>'; }).join("")
      + '</tbody></table>';
  }

  // ===== 工具函数 =====
  function byId(id) { return document.getElementById(id); }

  function esc(str) {
    if (!str) return "";
    var d = document.createElement("div");
    d.textContent = str;
    return d.innerHTML;
  }

  function highlightXml(xml) {
    if (!xml) return "";
    return esc(xml)
      .replace(/&lt;(\/?)(\w+)([^&]*?)&gt;/g, '<span class="xml-tag">&lt;$1$2$3&gt;</span>')
      .replace(/(\w+)="([^"]*)"/g, '<span class="xml-attr">$1</span>="<span class="xml-text">$2</span>"');
  }

  function debounce(fn, ms) {
    var timer;
    return function () {
      var args = arguments, self = this;
      clearTimeout(timer);
      timer = setTimeout(function () { fn.apply(self, args); }, ms);
    };
  }

  function toast(msg, type) {
    var el = document.createElement("div");
    el.className = "toast toast-" + (type || "info");
    el.textContent = msg;
    document.body.appendChild(el);
    setTimeout(function () { el.remove(); }, 3000);
  }

  function populateSelect(elId, emptyLabel, items) {
    var sel = byId(elId);
    var current = sel.value;
    sel.innerHTML = '<option value="">' + (emptyLabel || "全部") + '</option>';
    items.forEach(function (id) { sel.innerHTML += '<option value="' + id + '">' + id.slice(0, 40) + '</option>'; });
    sel.value = current;
  }

  async function refreshStats() {
    try {
      var stats = await api.get("stats");
      byId("injection-counter").textContent = "注入: " + (stats.total_injections || 0);
    } catch (e) { /* ignore */ }
  }

  // ===== 启动 =====
  init();
})();
