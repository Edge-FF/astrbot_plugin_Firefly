/**
 * 流萤认知外壳 — 调试面板 DOM 工具
 * 仅依赖浏览器标准 API，不持有业务状态，供 app.js 与 views/* 复用。
 *
 * 约定：视图一律用 el() 构建节点、用 on() 做事件委托，
 * 禁止拼接 HTML 字符串与内联 onclick 属性。
 */

/**
 * 按 id 取骨架中的固定节点。
 *
 * @param {string} id 元素 id。
 * @returns {HTMLElement|null} 命中的元素。
 */
export function byId(id) {
  return document.getElementById(id);
}

/**
 * 创建元素。
 *
 * 除 text/html 外的子节点一律经 textContent 写入，天然免疫注入；
 * html 分支只接受已经过 esc() 处理的可信片段（如 highlightXml 输出）。
 *
 * @param {string} tag 标签名。
 * @param {object} [props] 属性表。class/text/html/dataset/style 走专用分支，
 *   其余作为普通属性写入；值为 null/undefined/false 时跳过。
 * @param {Array<Node|string|null|undefined>} [children] 子节点，字符串会转成文本节点。
 * @returns {HTMLElement} 新建元素。
 */
export function el(tag, props = {}, children = []) {
  const node = document.createElement(tag);
  Object.entries(props).forEach(([key, value]) => {
    if (value == null || value === false) return;
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = String(value);
    else if (key === "html") node.innerHTML = value;
    else if (key === "dataset") Object.assign(node.dataset, value);
    else if (key === "style") Object.assign(node.style, value);
    else node.setAttribute(key, value === true ? "" : String(value));
  });
  const list = Array.isArray(children) ? children : [children];
  list.forEach((child) => {
    if (child == null) return;
    node.append(typeof child === "string" ? document.createTextNode(child) : child);
  });
  return node;
}

/**
 * 事件委托：在 root 上监听，事件目标命中 selector 时回调。
 *
 * @param {Element} root 委托根节点。
 * @param {string} type 事件类型。
 * @param {string} selector 目标选择器。
 * @param {(target: Element, event: Event) => void} handler 命中后的回调。
 */
export function on(root, type, selector, handler) {
  root.addEventListener(type, (event) => {
    const target = event.target instanceof Element ? event.target.closest(selector) : null;
    if (!target || !root.contains(target)) return;
    handler(target, event);
  });
}

/**
 * 转义文本为可安全插入 HTML 的字符串。
 *
 * @param {string} value 原始文本。
 * @returns {string} 转义结果；空值返回空串（与原实现保持一致）。
 */
export function esc(value) {
  if (!value) return "";
  const holder = document.createElement("div");
  holder.textContent = value;
  return holder.innerHTML;
}

/**
 * 顶部提示条，多条时纵向堆叠。
 *
 * @param {string} message 提示文本。
 * @param {string} [type] success / error / info。
 */
export function toast(message, type) {
  let stack = document.querySelector(".toast-stack");
  if (!stack) {
    stack = el("div", { class: "toast-stack" });
    document.body.append(stack);
  }
  const node = el("div", { class: "toast toast-" + (type || "info"), text: message });
  stack.append(node);
  setTimeout(() => node.remove(), 3000);
}

/**
 * 通用表单弹窗：支持自定义字段与多分支动作。
 *
 * @param {object} options 配置。
 * @param {string} [options.title] 标题。
 * @param {string} [options.intro] 说明文本。
 * @param {Array<object>} [options.fields] 字段定义 {name, label, type, options, value, placeholder, hint}。
 * @param {Array<object>} [options.actions] 动作 {value, label, class, default, skipValidate}。
 *   skipValidate 为真时点击该动作不触发 validate（用于「取消」等非提交动作）。
 * @param {Function} [options.validate] 提交前校验 (values) => 错误文本或 null。
 * @returns {Promise<{action: any, values: Record<string, string>}|null>} 取消（Esc / 点遮罩）返回 null。
 */
export function formDialog({ title, intro, fields = [], actions = [], validate } = {}) {
  return new Promise((resolve) => {
    const inputs = new Map();
    const errorBox = el("div", { class: "field-error hidden" });
    const body = [];
    if (intro) body.push(el("p", { class: "dialog-body", text: intro }));
    fields.forEach((field) => {
      let control;
      if (field.type === "select") {
        control = el(
          "select",
          { class: "select", name: field.name },
          (field.options || []).map((option) =>
            el("option", { value: option.value, text: option.label }),
          ),
        );
        control.value = field.value == null ? "" : field.value;
      } else if (field.type === "checkbox") {
        control = el("input", { type: "checkbox", name: field.name });
        control.checked = Boolean(field.value);
        inputs.set(field.name, control);
        body.push(
          el("label", { class: "toggle-row" }, [control, field.text || field.label]),
        );
        return;
      } else if (field.type === "textarea") {
        control = el("textarea", {
          class: "editor",
          name: field.name,
          rows: field.rows || 12,
          spellcheck: "false",
          readonly: field.readonly ? "" : null,
        });
        control.value = field.value == null ? "" : field.value;
      } else {
        control = el("input", {
          class: "input",
          type: "text",
          name: field.name,
          placeholder: field.placeholder || "",
          readonly: field.readonly ? "" : null,
        });
        control.value = field.value == null ? "" : field.value;
      }
      inputs.set(field.name, control);
      body.push(
        el("label", { class: "field" }, [
          el("span", { class: "field-label", text: field.label }),
          control,
          field.hint ? el("span", { class: "field-hint", text: field.hint }) : null,
        ]),
      );
    });
    if (fields.length) body.push(errorBox);

    const buttons = actions.map((action) => {
      const button = el("button", {
        class: action.class || "btn",
        type: "button",
        text: action.label,
      });
      button.addEventListener("click", () => submit(action.value));
      return button;
    });

    const backdrop = el("div", { class: "dialog-backdrop" }, [
      el("div", { class: "dialog", role: "dialog", "aria-modal": "true" }, [
        el("h3", { class: "dialog-title", text: title || "确认操作" }),
        ...body,
        el("div", { class: "dialog-actions" }, buttons),
      ]),
    ]);

    const close = (result) => {
      document.removeEventListener("keydown", onKeydown);
      backdrop.remove();
      resolve(result);
    };
    const readValues = () => {
      const values = {};
      inputs.forEach((control, name) => {
        values[name] = control.type === "checkbox" ? control.checked : control.value;
      });
      return values;
    };
    const submit = (value) => {
      const values = readValues();
      const action = actions.find((item) => item.value === value);
      // 取消等非提交动作不应触发校验，否则空表单下无法关闭弹窗
      if (validate && action && !action.skipValidate) {
        const message = validate(values);
        if (message) {
          errorBox.textContent = message;
          errorBox.classList.remove("hidden");
          return;
        }
      }
      close({ action: value, values });
    };
    const onKeydown = (event) => {
      if (event.key === "Escape") {
        close(null);
      } else if (event.key === "Enter" && event.target instanceof HTMLInputElement) {
        const primaryIndex = actions.findIndex((action) => action.default);
        submit(actions[primaryIndex < 0 ? 0 : primaryIndex]?.value);
      }
    };

    backdrop.addEventListener("click", (event) => {
      if (event.target === backdrop) close(null);
    });
    document.addEventListener("keydown", onKeydown);
    document.body.append(backdrop);

    const primaryIndex = actions.findIndex((action) => action.default);
    const focusTarget = inputs.values().next().value || buttons[primaryIndex < 0 ? 0 : primaryIndex];
    if (focusTarget) focusTarget.focus();
  });
}

/**
 * 自定义确认弹窗，替代原生 confirm。
 *
 * @param {string} message 确认内容。
 * @param {object} [options] title 标题；danger 为真时确认键使用危险样式。
 * @returns {Promise<boolean>} 确认返回 true，取消 / Esc / 点击遮罩返回 false。
 */
export function confirmDialog(message, options = {}) {
  const { title = "确认操作", danger = false } = options;
  return formDialog({
    title,
    intro: message,
    actions: [
      { value: false, label: "取消", class: "btn btn-ghost" },
      { value: true, label: "确认", class: danger ? "btn btn-danger" : "btn", default: true },
    ],
  }).then((result) => Boolean(result && result.action));
}

/**
 * 秒级时间戳格式化为本地时间文本。
 *
 * @param {number} timestamp 秒级 Unix 时间戳。
 * @returns {string} 本地化时间。
 */
export function formatTime(timestamp) {
  return new Date(timestamp * 1000).toLocaleString("zh-CN");
}

/**
 * 防抖包装。
 *
 * @param {Function} fn 目标函数。
 * @param {number} ms 静默间隔（毫秒）。
 * @returns {Function} 防抖后的函数。
 */
export function debounce(fn, ms) {
  let timer;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), ms);
  };
}

/**
 * 字节数格式化为易读文本。
 *
 * @param {number} bytes 字节数。
 * @returns {string} 形如 "1.4 KB" 的文本。
 */
export function formatBytes(bytes) {
  if (!bytes || bytes < 0) return "0 B";
  if (bytes < 1024) return bytes + " B";
  const units = ["KB", "MB", "GB"];
  let value = bytes / 1024;
  let index = 0;
  while (value >= 1024 && index < units.length - 1) {
    value /= 1024;
    index += 1;
  }
  return value.toFixed(1) + " " + units[index];
}

/**
 * XML 语法着色。先整体转义再做标签/属性着色，避免注入。
 *
 * @param {string} xml 原始 XML 文本。
 * @returns {string} 可直接写入 innerHTML 的着色结果。
 */
export function highlightXml(xml) {
  if (!xml) return "";
  return esc(xml)
    .replace(/&lt;(\/?)(\w+)([^&]*?)&gt;/g, '<span class="xml-tag">&lt;$1$2$3&gt;</span>')
    .replace(/(\w+)="([^"]*)"/g, '<span class="xml-attr">$1</span>="<span class="xml-text">$2</span>"');
}
