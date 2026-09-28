/**
 * threadMarkdown.tsx — Sanitized Markdown renderer with interactive code blocks,
 * copy-to-clipboard, auto-preview badges for issues/PRs/runs, and strict XSS protection.
 *
 * Implements SC-D4 (Issue #1318) under Epic SC-D (#1350).
 */
import React, { useMemo, useState } from "react";
import { marked, type Token } from "marked";
import { extractIssueOrRunLinks, sanitizeMarkdown } from "./threadUtils";
import "./threadMarkdown.css";

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
    <div className="thread-code-block">
      <div className="thread-code-block__header">
        <span>{language || "code"}</span>
        <button
          type="button"
          onClick={handleCopy}
          aria-label={copied ? "Copied" : "Copy code"}
          className={`thread-code-block__copy ${copied ? "thread-code-block__copy--copied" : ""}`}
        >
          {copied ? "Copied!" : "Copy"}
        </button>
      </div>
      <pre className="thread-code-block__pre">
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
          <p key={`p-badge-${idx}`} style={{ margin: "6px 0", lineHeight: 1.6 }}>
            {extractIssueOrRunLinks(token.text)}
          </p>
        );
      }

      // sanitized via DOMPurify.sanitize in sanitizeMarkdown (safe)
      return (
        <div
          key={`md-${idx}`}
          className="thread-md-chunk"
          // safe: cleanHtml is sanitized via DOMPurify.sanitize in lexMarkdownTokens
          dangerouslySetInnerHTML={{ __html: cleanHtml }}
        />
      );
    });
  }, [content]);

  return <div className={`thread-markdown ${className}`}>{renderedElements}</div>;
};
