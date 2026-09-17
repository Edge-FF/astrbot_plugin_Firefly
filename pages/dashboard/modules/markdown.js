/**
 * 极简 Markdown 渲染器（无第三方依赖）。
 *
 * 安全性：全部节点经 DOM API 构建，从不使用 innerHTML；链接只接受 http/https
 * 且带 rel="noreferrer noopener"。支持标题、无序/有序列表、引用、围栏代码块、
 * 水平线、粗体/斜体/行内代码与链接，其余语法按纯文本处理。
 */
import { el } from "./dom.js";

const INLINE_RE =
  /(`[^`]+`)|(\*\*[^*]+\*\*)|(\*[^*]+\*)|(\[[^\]]+\]\((https?:\/\/[^)\s]+)\))/g;
const FENCE_RE = /^```/;
const HEADING_RE = /^(#{1,6})\s+(.*)$/;
const QUOTE_RE = /^>\s?(.*)$/;
const UNORDERED_RE = /^\s*[-*+]\s+(.*)$/;
const ORDERED_RE = /^\s*\d+\.\s+(.*)$/;
const RULE_RE = /^\s*(?:-{3,}|\*{3,})\s*$/;

/**
 * 渲染行内语法为节点数组（字符串会被 el() 转成文本节点）。
 *
 * @param {string} text 单行文本。
 * @returns {Array<Node|string>} 节点与文本片段。
 */
function renderInline(text) {
  const source = String(text);
  const nodes = [];
  let last = 0;
  for (const match of source.matchAll(INLINE_RE)) {
    if (match.index > last) nodes.push(source.slice(last, match.index));
    const token = match[0];
    if (token.startsWith("`")) {
      nodes.push(el("code", { class: "markdown-inline", text: token.slice(1, -1) }));
    } else if (token.startsWith("**")) {
      nodes.push(el("strong", { text: token.slice(2, -2) }));
    } else if (token.startsWith("*")) {
      nodes.push(el("em", { text: token.slice(1, -1) }));
    } else {
      const link = /\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/.exec(token);
      nodes.push(
        el("a", {
          href: link[2],
          target: "_blank",
          rel: "noreferrer noopener",
          text: link[1],
        }),
      );
    }
    last = match.index + token.length;
  }
  if (last < source.length) nodes.push(source.slice(last));
  return nodes;
}

/**
 * 渲染 Markdown 文本为可插入的容器节点。
 *
 * @param {string} source Markdown 源文本。
 * @returns {HTMLElement} class 为 markdown 的容器。
 */
export function renderMarkdown(source) {
  const root = el("div", { class: "markdown" });
  const lines = String(source || "").split(/\r?\n/);
  let list = null;
  const flushList = () => {
    if (list) {
      root.append(list.node);
      list = null;
    }
  };

  for (let index = 0; index < lines.length; index += 1) {
    const line = lines[index];

    if (FENCE_RE.test(line)) {
      flushList();
      const code = [];
      index += 1;
      while (index < lines.length && !FENCE_RE.test(lines[index])) {
        code.push(lines[index]);
        index += 1;
      }
      root.append(el("pre", { class: "markdown-code" }, [el("code", { text: code.join("\n") })]));
      continue;
    }

    const heading = HEADING_RE.exec(line);
    if (heading) {
      flushList();
      root.append(el("h" + heading[1].length, {}, renderInline(heading[2])));
      continue;
    }

    if (RULE_RE.test(line)) {
      flushList();
      root.append(el("hr"));
      continue;
    }

    const quote = QUOTE_RE.exec(line);
    if (quote) {
      flushList();
      root.append(el("blockquote", {}, renderInline(quote[1])));
      continue;
    }

    const unordered = UNORDERED_RE.exec(line);
    const ordered = unordered ? null : ORDERED_RE.exec(line);
    if (unordered || ordered) {
      const tag = unordered ? "ul" : "ol";
      if (!list || list.tag !== tag) {
        flushList();
        list = { tag, node: el(tag) };
      }
      list.node.append(el("li", {}, renderInline((unordered || ordered)[1])));
      continue;
    }

    if (!line.trim()) {
      flushList();
      continue;
    }

    flushList();
    root.append(el("p", {}, renderInline(line)));
  }

  flushList();
  return root;
}
