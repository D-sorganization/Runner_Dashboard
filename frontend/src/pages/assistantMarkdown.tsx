/**
 * assistantMarkdown.tsx — the minimal Markdown renderer used to format chat
 * assistant replies, extracted (behaviour-wise 1:1) from the legacy `App.tsx`
 * monolith as part of the decomposition epic (#836, pass 9).
 *
 * Lives in its own module (rather than alongside the `AssistantSidebar`
 * component) so the sidebar file only exports components — keeping Vite fast
 * refresh happy (react-refresh/only-export-components) and letting these pure
 * renderers be unit-tested directly. Supports bold, italic, inline code, fenced
 * code blocks, links, ordered/unordered lists and h1–h3 headings, exactly as
 * the original did.
 */
import React from "react";

/* eslint-disable @typescript-eslint/no-explicit-any */
const h = React.createElement as any;

/** Minimal Markdown renderer — bold, italic, inline code, code blocks, links, lists */
export function renderMarkdown(text: string) {
  if (!text) return [];
  const out = [];
  const lines = text.split("\n");
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    // Fenced code block
    if (line.startsWith("```")) {
      const codeLines = [];
      i++;
      while (i < lines.length && !lines[i].startsWith("```")) {
        codeLines.push(lines[i]);
        i++;
      }
      out.push(h("pre", { key: out.length, className: "assistant-markdown__code-block" },
        h("code", null, codeLines.join("\n"))
      ));
      i++;
      continue;
    }
    // Unordered list item
    if (/^[-*] /.test(line)) {
      const listItems = [];
      while (i < lines.length && /^[-*] /.test(lines[i])) {
        listItems.push(h("li", { key: i }, inlineMarkdown(lines[i].slice(2))));
        i++;
      }
      out.push(h("ul", { key: out.length, className: "assistant-markdown__list" }, listItems));
      continue;
    }
    // Ordered list item
    if (/^\d+\. /.test(line)) {
      const olItems = [];
      while (i < lines.length && /^\d+\. /.test(lines[i])) {
        olItems.push(h("li", { key: i }, inlineMarkdown(lines[i].replace(/^\d+\. /, ""))));
        i++;
      }
      out.push(h("ol", { key: out.length, className: "assistant-markdown__list" }, olItems));
      continue;
    }
    // Heading
    const hm = line.match(/^(#{1,3}) (.+)/);
    if (hm) {
      const lvl = hm[1].length;
      const tag = "h" + (lvl + 3);
      out.push(h(tag, { key: out.length, className: `assistant-markdown__heading assistant-markdown__heading--${lvl}` }, inlineMarkdown(hm[2])));
      i++;
      continue;
    }
    // Blank line
    if (line.trim() === "") {
      out.push(h("br", { key: out.length }));
      i++;
      continue;
    }
    // Paragraph
    out.push(h("p", { key: out.length, className: "assistant-markdown__paragraph" }, inlineMarkdown(line)));
    i++;
  }
  return out;
}

export function inlineMarkdown(text: string) {
  // Split on inline code, bold, italic, links
  const parts = [];
  const re = /(`[^`]+`|\*\*[^*]+\*\*|\*[^*]+\*|\[([^\]]+)\]\(([^)]+)\))/g;
  let last = 0, m;
  while ((m = re.exec(text)) !== null) {
    if (m.index > last) parts.push(text.slice(last, m.index));
    const tok = m[0];
    if (tok.startsWith("`")) {
      parts.push(h("code", { key: parts.length, className: "assistant-markdown__inline-code" }, tok.slice(1, -1)));
    } else if (tok.startsWith("**")) {
      parts.push(h("strong", { key: parts.length }, tok.slice(2, -2)));
    } else if (tok.startsWith("*")) {
      parts.push(h("em", { key: parts.length }, tok.slice(1, -1)));
    } else {
      parts.push(h("a", { key: parts.length, href: m[3], target: "_blank", rel: "noopener noreferrer", className: "assistant-markdown__link" }, m[2]));
    }
    last = re.lastIndex;
  }
  if (last < text.length) parts.push(text.slice(last));
  return parts;
}
