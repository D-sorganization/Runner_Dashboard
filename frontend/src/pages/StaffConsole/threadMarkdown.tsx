/**
 * threadMarkdown.tsx — Sanitized Markdown renderer with interactive code blocks,
 * copy-to-clipboard, auto-preview badges for issues/PRs/runs, and strict XSS protection.
 *
 * Implements SC-D4 (Issue #1318) under Epic SC-D (#1350).
 */
import React, { useMemo, useState } from "react";
import DOMPurify from "dompurify";
import { marked, type Token } from "marked";

const GITHUB_REPO_URL = "https://github.com/D-sorganization/Runner_Dashboard";

// DOMPurify configuration allowing safe formatting while neutralizing script/event attacks
const DOMPURIFY_CONFIG = {
  ALLOWED_TAGS: [
    "p",
    "br",
    "b",
    "i",
    "em",
    "strong",
    "strike",
    "code",
    "pre",
    "a",
    "ul",
    "ol",
    "li",
    "blockquote",
    "span",
    "div",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "table",
    "thead",
    "tbody",
    "tr",
    "th",
    "td",
  ],
  ALLOWED_ATTR: ["href", "title", "target", "rel", "class", "style"],
  ALLOW_DATA_ATTR: false,
};

export function sanitizeMarkdown(rawHtml: string): string {
  if (!rawHtml) return "";
  const cleaned = DOMPurify.sanitize(rawHtml, DOMPURIFY_CONFIG);
  return typeof cleaned === "string" ? cleaned : "";
}

/** Previews GitHub issues, PRs, and run identifiers */
export function extractIssueOrRunLinks(text: string): React.ReactNode[] {
  // Regex matches:
  // 1. Full PR/Issue URL: https://github.com/.../(issues|pull)/(\d+)
  // 2. PR reference: (?:PR\s*#)(\d+)
  // 3. Issue reference: (?:#)(\d+)
  // 4. Run/maintenance operation: (maintenance\.[a-zA-Z0-9_.-]+|run-[a-zA-Z0-9_-]+)
  const pattern =
    /(?:https:\/\/github\.com\/[^\s/]+\/[^\s/]+\/(issues|pull)\/(\d+))|(?:PR\s*#(\d+))|(?:#(\d+))|(maintenance\.[a-zA-Z0-9_.-]+|run-[a-zA-Z0-9_-]+)/g;

  const nodes: React.ReactNode[] = [];
  let lastIndex = 0;
  let match: RegExpExecArray | null;

  while ((match = pattern.exec(text)) !== null) {
    if (match.index > lastIndex) {
      nodes.push(text.slice(lastIndex, match.index));
    }

    const [fullMatch, urlType, urlNum, prNum, issueNum, runId] = match;

    if (urlNum) {
      const type = urlType === "pull" ? "pull" : "issues";
      const href = `${GITHUB_REPO_URL}/${type}/${urlNum}`;
      nodes.push(
        <a
          key={`link-${match.index}`}
          href={href}
          target="_blank"
          rel="noopener noreferrer"
          className="badge-link badge-link--issue"
          style={{
            display: "inline-flex",
            alignItems: "center",
            padding: "1px 6px",
            fontSize: "0.85em",
            fontWeight: 600,
            borderRadius: 4,
            background: "var(--bg-tertiary, #21262d)",
            color: "var(--accent-blue, #58a6ff)",
            textDecoration: "none",
            margin: "0 2px",
          }}
        >
          #{urlNum}
        </a>
      );
    } else if (prNum) {
      nodes.push(
        <span key={`pr-prefix-${match.index}`}>PR </span>,
        <a
          key={`pr-${match.index}`}
          href={`${GITHUB_REPO_URL}/pull/${prNum}`}
          target="_blank"
          rel="noopener noreferrer"
          className="badge-link badge-link--pr"
          style={{
            display: "inline-flex",
            alignItems: "center",
            padding: "1px 6px",
            fontSize: "0.85em",
            fontWeight: 600,
            borderRadius: 4,
            background: "rgba(163, 113, 247, 0.15)",
            color: "#bc8cff",
            textDecoration: "none",
            margin: "0 2px",
          }}
        >
          #{prNum}
        </a>
      );
    } else if (issueNum) {
      nodes.push(
        <a
          key={`issue-${match.index}`}
          href={`${GITHUB_REPO_URL}/issues/${issueNum}`}
          target="_blank"
          rel="noopener noreferrer"
          className="badge-link badge-link--issue"
          style={{
            display: "inline-flex",
            alignItems: "center",
            padding: "1px 6px",
            fontSize: "0.85em",
            fontWeight: 600,
            borderRadius: 4,
            background: "rgba(88, 166, 255, 0.15)",
            color: "var(--accent-blue, #58a6ff)",
            textDecoration: "none",
            margin: "0 2px",
          }}
        >
          #{issueNum}
        </a>
      );
    } else if (runId) {
      nodes.push(
        <span
          key={`run-${match.index}`}
          className="badge-pill badge-pill--run"
          style={{
            display: "inline-flex",
            alignItems: "center",
            padding: "1px 6px",
            fontSize: "0.85em",
            fontFamily: "monospace",
            borderRadius: 4,
            background: "rgba(56, 139, 253, 0.12)",
            color: "var(--accent-teal, #39c5bb)",
            border: "1px solid rgba(56, 139, 253, 0.3)",
            margin: "0 2px",
          }}
        >
          ⚙ {runId}
        </span>
      );
    } else {
      nodes.push(fullMatch);
    }

    lastIndex = pattern.lastIndex;
  }

  if (lastIndex < text.length) {
    nodes.push(text.slice(lastIndex));
  }

  return nodes;
}

interface CodeBlockProps {
  code: string;
  language?: string;
}

export const CodeBlock: React.FC<CodeBlockProps> = ({ code, language }) => {
  const [copied, setCopied] = useState(false);

  const handleCopy = async () => {
    try {
      if (navigator?.clipboard?.writeText) {
        await navigator.clipboard.writeText(code);
      }
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // ignore clipboard error
    }
  };

  return (
    <div
      className="thread-code-block"
      style={{
        position: "relative",
        background: "var(--bg-tertiary, #161b22)",
        border: "1px solid var(--border, #30363d)",
        borderRadius: 8,
        margin: "8px 0",
        overflow: "hidden",
      }}
    >
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          padding: "4px 10px",
          fontSize: 11,
          color: "var(--text-muted, #8b949e)",
          borderBottom: "1px solid var(--border, #30363d)",
          background: "rgba(255, 255, 255, 0.02)",
        }}
      >
        <span>{language || "code"}</span>
        <button
          type="button"
          onClick={handleCopy}
          aria-label={copied ? "Copied" : "Copy code"}
          style={{
            background: "transparent",
            border: "1px solid var(--border, #30363d)",
            borderRadius: 4,
            color: copied ? "var(--accent-green, #3fb950)" : "var(--text-secondary, #c9d1d9)",
            cursor: "pointer",
            fontSize: 11,
            padding: "2px 8px",
          }}
        >
          {copied ? "Copied!" : "Copy"}
        </button>
      </div>
      <pre
        style={{
          margin: 0,
          padding: "10px 12px",
          overflowX: "auto",
          fontSize: 12,
          lineHeight: 1.45,
          fontFamily: "var(--font-mono, monospace)",
        }}
      >
        <code>{code}</code>
      </pre>
    </div>
  );
};

export interface ThreadMarkdownProps {
  content: string;
  className?: string;
}

export const ThreadMarkdown: React.FC<ThreadMarkdownProps> = ({ content, className = "" }) => {
  const renderedElements = useMemo(() => {
    if (!content) return null;

    // Parse tokens using marked.lexer for top-level code blocks
    let tokens: Token[];
    try {
      tokens = marked.lexer(content, { gfm: true, breaks: true });
    } catch {
      return <div>{content}</div>;
    }

    return tokens.map((token: Token, idx: number) => {
      if (token.type === "code") {
        return <CodeBlock key={`code-${idx}`} code={token.text} language={token.lang} />;
      }

      // Render other markdown tokens via marked.parse and DOMPurify
      const rawHtml = marked.parser([token]);
      const cleanHtml = sanitizeMarkdown(rawHtml);

      // Check if text has issue/PR/run references
      const hasBadges =
        /#\d+/.test(token.raw) ||
        /maintenance\.[a-zA-Z0-9_.-]+/.test(token.raw) ||
        /run-[a-zA-Z0-9_-]+/.test(token.raw);

      if (hasBadges && token.type === "paragraph") {
        // Render rich badges for plain paragraphs with references
        return (
          <p key={`p-badge-${idx}`} style={{ margin: "6px 0", lineHeight: 1.5 }}>
            {extractIssueOrRunLinks(token.text)}
          </p>
        );
      }

      return (
        <div
          key={`md-${idx}`}
          className="thread-md-chunk"
          dangerouslySetInnerHTML={{ __html: cleanHtml }}
          style={{ lineHeight: 1.5 }}
        />
      );
    });
  }, [content]);

  return <div className={`thread-markdown ${className}`}>{renderedElements}</div>;
};
