/**
 * 流萤认知外壳 — 调试面板 API 客户端
 * 与 AstrBot Dashboard 通过 window.AstrBotPluginPage Bridge 通信。
 *
 * AstrBot 会在页面 </body> 前自动注入 bridge SDK，因此这里采用“调用时获取”
 * 的懒加载方式，避免脚本在桥接注入前执行导致 bridge 缺失（bridge not found）。
 * 作为 ES Module 导出，由 app.js 显式 import，保证加载顺序与可用性。
 */
function getBridge() {
  return window.AstrBotPluginPage || null;
}

function requireBridge() {
  var bridge = getBridge();
  if (!bridge) {
    throw new Error("AstrBotPluginPage bridge not available");
  }
  return bridge;
}

function buildEndpoint(path) {
  var cleanPath = String(path || "").replace(/^\/+/, "");
  if (cleanPath.indexOf("page/") === 0) return cleanPath;
  return "page/" + cleanPath;
}

function unwrapResponse(response) {
  if (
    response &&
    response.status === "ok" &&
    Object.prototype.hasOwnProperty.call(response, "data")
  ) {
    return response.data;
  }
  if (response && response.status === "error") {
    throw new Error(response.message || "Unknown error");
  }
  return response || {};
}

export const FireflyApi = {
  async ready() {
    var bridge = requireBridge();
    return bridge.ready();
  },

  async get(path, params) {
    var bridge = requireBridge();
    var filtered = {};
    var p = params || {};
    Object.keys(p).forEach(function (k) {
      if (p[k] != null && p[k] !== "") {
        filtered[k] = p[k];
      }
    });
    // 必须传对象而非字符串：dashboard 的 axios.get 要求 params 是普通对象或 URLSearchParams
    var resp = await bridge.apiGet(buildEndpoint(path), filtered);
    return unwrapResponse(resp);
  },

  async post(path, body) {
    var bridge = requireBridge();
    var resp = await bridge.apiPost(buildEndpoint(path), body || {});
    return unwrapResponse(resp);
  },
};
