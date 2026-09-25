import React, { useState } from "react";

/* eslint-disable @typescript-eslint/no-explicit-any */
const h = React.createElement as any;

/**
 * CodeBlockWithCopy — Renders a fenced code block with a language header
 * and an accessible one-click copy button.
 */
export function CodeBlockWithCopy({
  code,
  language,
}: {
  code: string;
  language?: string;
}) {
  const [copied, setCopied] = useState(false);

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(code);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // Fallback if clipboard fails
    }
  };

  return (
    <div
      role="region"
      aria-label="Code block"
      className="staff-code-block"
      style={{
        position: "relative",
        background: "var(--bg-tertiary, #161b22)",
        borderRadius: "6px",
        margin: "8px 0",
        border: "1px solid var(--border-subtle, #30363d)",
      }}
    >
      <div
        className="staff-code-block__header"
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          padding: "4px 8px",
          background: "var(--bg-secondary, #21262d)",
          borderTopLeftRadius: "5px",
          borderTopRightRadius: "5px",
          fontSize: "11px",
          color: "var(--text-muted, #8b949e)",
          fontFamily: "monospace",
        }}
      >
        <span>{language || "text"}</span>
        <button
          type="button"
          onClick={handleCopy}
          aria-label={copied ? "Copied" : "Copy code"}
          style={{
            background: "transparent",
            border: "1px solid var(--border-subtle, #30363d)",
            borderRadius: "4px",
            color: copied ? "var(--accent-green, #3fb950)" : "var(--text-primary, #c9d1d9)",
            fontSize: "11px",
            padding: "2px 6px",
            cursor: "pointer",
          }}
        >
          {copied ? "Copied!" : "Copy"}
        </button>
      </div>
      <pre
        style={{
          margin: 0,
          padding: "10px",
          overflowX: "auto",
          fontSize: "12px",
          lineHeight: "1.4",
          color: "var(--text-primary, #c9d1d9)",
          fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
        }}
      >
        <code>{code}</code>
      </pre>
    </div>
  );
}

/**
 * linkifyText — Parses issue references (#123), PR references (PR #123),
 * and run IDs (run_abc123) into auto-preview interactive chips.
 */
function linkifyText(text: string): (string | React.ReactNode)[] {
  // Regex matching PR #123, #123, run_abc123
  const tokenRegex = /(PR\s+#\d+|#\d+|run_[a-zA-Z0-9_-]+)/g;
  const parts: (string | React.ReactNode)[] = [];
  let lastIndex = 0;
  let match: RegExpExecArray | null;

  while ((match = tokenRegex.exec(text)) !== null) {
    if (match.index > lastIndex) {
      parts.push(text.slice(lastIndex, match.index));
    }
    const token = match[0];
    if (token.startsWith("PR #") || token.startsWith("PR#")) {
      const prNum = token.replace(/PR\s*#/, "");
      parts.push(
        <a
          key={`pr-${match.index}`}
          href={`https://github.com/D-sorganization/Runner_Dashboard/pull/${prNum}`}
          target="_blank"
          rel="noopener noreferrer"
          className="staff-entity-chip staff-entity-chip--pr"
          style={{
            display: "inline-flex",
            alignItems: "center",
            padding: "0 4px",
            borderRadius: "4px",
            background: "rgba(163, 113, 247, 0.15)",
            color: "var(--accent-purple, #a371f7)",
            textDecoration: "none",
            fontWeight: 500,
          }}
        >
          {token}
        </a>
      );
    } else if (token.startsWith("#")) {
      const issueNum = token.slice(1);
      parts.push(
        <a
          key={`issue-${match.index}`}
          href={`https://github.com/D-sorganization/Runner_Dashboard/issues/${issueNum}`}
          target="_blank"
          rel="noopener noreferrer"
          className="staff-entity-chip staff-entity-chip--issue"
          style={{
            display: "inline-flex",
            alignItems: "center",
            padding: "0 4px",
            borderRadius: "4px",
            background: "rgba(46, 160, 67, 0.15)",
            color: "var(--accent-green, #3fb950)",
            textDecoration: "none",
            fontWeight: 500,
          }}
        >
          {token}
        </a>
      );
    } else if (token.startsWith("run_")) {
      parts.push(
        <a
          key={`run-${match.index}`}
          href={`/staff/runs/${token}`}
          className="staff-entity-chip staff-entity-chip--run"
          style={{
            display: "inline-flex",
            alignItems: "center",
            padding: "0 4px",
            borderRadius: "4px",
            background: "rgba(88, 166, 255, 0.15)",
            color: "var(--accent-blue, #58a6ff)",
            textDecoration: "none",
            fontFamily: "monospace",
            fontSize: "11px",
          }}
        >
          {token}
        </a>
      );
    }
    lastIndex = tokenRegex.lastIndex;
  }

  if (lastIndex < text.length) {
    parts.push(text.slice(lastIndex));
  }
  return parts;
}

/**
 * inlineMarkdown — Handles bold, italic, inline code, and sanitized links.
 */
function inlineMarkdown(text: string): React.ReactNode[] {
  // Strip raw HTML script tags to prevent XSS
  const safeText = text.replace(/<\/?script[^>]*>/gi, "");

  const parts: React.ReactNode[] = [];
  const re = /(`[^`]+`|\*\*[^*]+\*\*|\*[^*]+\*|\[([^\]]+)\]\(([^)]+)\))/g;
  let last = 0;
  let m: RegExpExecArray | null;

  while ((m = re.exec(safeText)) !== null) {
    if (m.index > last) {
      parts.push(...linkifyText(safeText.slice(last, m.index)));
    }
    const tok = m[0];
    if (tok.startsWith("`")) {
      parts.push(
        h(
          "code",
          {
            key: `code-${parts.length}`,
            style: {
              background: "rgba(110, 118, 129, 0.2)",
              padding: "0.2em 0.4em",
              borderRadius: "4px",
              fontFamily: "monospace",
              fontSize: "85%",
            },
          },
          tok.slice(1, -1)
        )
      );
    } else if (tok.startsWith("**")) {
      parts.push(h("strong", { key: `strong-${parts.length}` }, tok.slice(2, -2)));
    } else if (tok.startsWith("*")) {
      parts.push(h("em", { key: `em-${parts.length}` }, tok.slice(1, -1)));
    } else {
      const linkHref = m[3].trim();
      const isDangerous = /^javascript:/i.test(linkHref) || /^data:/i.test(linkHref);
      if (!isDangerous) {
        parts.push(
          h(
            "a",
            {
              key: `a-${parts.length}`,
              href: linkHref,
              target: "_blank",
              rel: "noopener noreferrer",
              style: { color: "var(--accent-blue, #58a6ff)", textDecoration: "underline" },
            },
            m[2]
          )
        );
      } else {
        parts.push(m[2]);
      }
    }
    last = re.lastIndex;
  }
  if (last < safeText.length) {
    parts.push(...linkifyText(safeText.slice(last)));
  }
  return parts;
}

/**
 * renderSanitizedMarkdown — Renders Markdown text with code block copy buttons,
 * entity chips (#issue, PR #pr, run_id), and XSS sanitization.
 */
// eslint-disable-next-line react-refresh/only-export-components
export function renderSanitizedMarkdown(text: string): React.ReactNode[] {
  if (!text) return [];

  // Strip script tags
  const sanitized = text.replace(/<\/?script[^>]*>/gi, "");
  const lines = sanitized.split("\n");
  const out: React.ReactNode[] = [];
  let i = 0;

  while (i < lines.length) {
    const line = lines[i];

    // Fenced code block
    if (line.startsWith("```")) {
      const lang = line.slice(3).trim();
      const codeLines: string[] = [];
      i++;
      while (i < lines.length && !lines[i].startsWith("```")) {
        codeLines.push(lines[i]);
        i++;
      }
      out.push(
        <CodeBlockWithCopy
          key={`code-block-${out.length}`}
          code={codeLines.join("\n")}
          language={lang}
        />
      );
      i++;
      continue;
    }

    // Unordered list
    if (/^[-*] /.test(line)) {
      const listItems: React.ReactNode[] = [];
      while (i < lines.length && /^[-*] /.test(lines[i])) {
        listItems.push(
          h("li", { key: `li-${i}`, style: { margin: "2px 0" } }, inlineMarkdown(lines[i].slice(2)))
        );
        i++;
      }
      out.push(
        h(
          "ul",
          { key: `ul-${out.length}`, style: { margin: "6px 0", paddingLeft: "20px" } },
          listItems
        )
      );
      continue;
    }

    // Ordered list
    if (/^\d+\. /.test(line)) {
      const olItems: React.ReactNode[] = [];
      while (i < lines.length && /^\d+\. /.test(lines[i])) {
        olItems.push(
          h("li", { key: `oli-${i}`, style: { margin: "2px 0" } }, inlineMarkdown(lines[i].replace(/^\d+\. /, "")))
        );
        i++;
      }
      out.push(
        h(
          "ol",
          { key: `ol-${out.length}`, style: { margin: "6px 0", paddingLeft: "20px" } },
          olItems
        )
      );
      continue;
    }

    // Heading
    const hm = line.match(/^(#{1,3}) (.+)/);
    if (hm) {
      const lvl = hm[1].length;
      const tag = "h" + (lvl + 2);
      out.push(
        h(
          tag,
          { key: `h-${out.length}`, style: { margin: "12px 0 6px", fontWeight: 600 } },
          inlineMarkdown(hm[2])
        )
      );
      i++;
      continue;
    }

    // Blank line
    if (line.trim() === "") {
      out.push(h("br", { key: `br-${out.length}` }));
      i++;
      continue;
    }

    // Regular paragraph
    out.push(
      h(
        "p",
        { key: `p-${out.length}`, style: { margin: "6px 0", lineHeight: "1.5" } },
        inlineMarkdown(line)
      )
    );
    i++;
  }

  return out;
}
