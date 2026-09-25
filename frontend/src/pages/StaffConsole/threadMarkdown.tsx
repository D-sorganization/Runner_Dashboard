/**
 * threadMarkdown.tsx — Sanitized Markdown renderer with interactive code blocks,
 * copy-to-clipboard, auto-preview badges for issues/PRs/runs, and strict XSS protection.
 *
 * Implements SC-D4 (Issue #1318) under Epic SC-D (#1350).
 */
import React, { useMemo, useState } from "react";
import { marked, type Token } from "marked";
import { extractIssueOrRunLinks, sanitizeMarkdown } from "./threadUtils";

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
