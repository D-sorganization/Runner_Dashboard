/**
 * threadUtils.ts — Pure helper utilities for Staff Console threads, messages,
 * markdown sanitization, formatting, and link previews.
 *
 * Implements SC-D4 (Issue #1318) under Epic SC-D (#1350).
 */
import React from "react";
import DOMPurify from "dompurify";

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

/** Formats a timestamp string into a local short time string (e.g. "10:14 AM") */
export function formatMessageTime(isoString?: string): string {
  if (!isoString) return "";
  try {
    const d = new Date(isoString);
    if (isNaN(d.getTime())) return "";
    return d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  } catch {
    return "";
  }
}

/** Human-readable title for classified failure codes */
export function formatFailureTitle(failureClass?: string | null): string {
  if (!failureClass) return "Action Failed";
  switch (failureClass) {
    case "auth_expired":
      return "Authentication Expired";
    case "rate_limit_exceeded":
      return "Rate Limit Exceeded";
    case "token_budget_exceeded":
      return "Token Budget Exceeded";
    case "timeout":
      return "Turn Execution Timed Out";
    case "provider_unavailable":
      return "Provider Unavailable";
    case "permission_denied":
      return "Permission Denied";
    default:
      return failureClass.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
  }
}

/** Formats separator date string into "Today", "Yesterday", or "MMM D, YYYY" */
export function formatSeparatorDate(isoString?: string): string {
  if (!isoString) return "";
  try {
    const d = new Date(isoString);
    if (isNaN(d.getTime())) return "";
    const today = new Date();
    const yesterday = new Date();
    yesterday.setDate(today.getDate() - 1);

    if (d.toDateString() === today.toDateString()) {
      return "Today";
    }
    if (d.toDateString() === yesterday.toDateString()) {
      return "Yesterday";
    }
    return d.toLocaleDateString([], {
      month: "short",
      day: "numeric",
      year: d.getFullYear() !== today.getFullYear() ? "numeric" : undefined,
    });
  } catch {
    return "";
  }
}

/** Extracts local YYYY-MM-DD date key from an ISO timestamp */
export function getDateKey(isoString?: string): string {
  if (!isoString) return "unknown";
  try {
    const d = new Date(isoString);
    return isNaN(d.getTime()) ? "unknown" : d.toISOString().slice(0, 10);
  } catch {
    return "unknown";
  }
}

/** Previews GitHub issues, PRs, and run identifiers */
export function extractIssueOrRunLinks(text: string): React.ReactNode[] {
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
        React.createElement(
          "a",
          {
            key: `link-${match.index}`,
            href,
            target: "_blank",
            rel: "noopener noreferrer",
            className: "badge-link badge-link--issue",
            style: {
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
            },
          },
          `#${urlNum}`
        )
      );
    } else if (prNum) {
      nodes.push(
        React.createElement("span", { key: `pr-prefix-${match.index}` }, "PR "),
        React.createElement(
          "a",
          {
            key: `pr-${match.index}`,
            href: `${GITHUB_REPO_URL}/pull/${prNum}`,
            target: "_blank",
            rel: "noopener noreferrer",
            className: "badge-link badge-link--pr",
            style: {
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
            },
          },
          `#${prNum}`
        )
      );
    } else if (issueNum) {
      nodes.push(
        React.createElement(
          "a",
          {
            key: `issue-${match.index}`,
            href: `${GITHUB_REPO_URL}/issues/${issueNum}`,
            target: "_blank",
            rel: "noopener noreferrer",
            className: "badge-link badge-link--issue",
            style: {
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
            },
          },
          `#${issueNum}`
        )
      );
    } else if (runId) {
      nodes.push(
        React.createElement(
          "span",
          {
            key: `run-${match.index}`,
            className: "badge-pill badge-pill--run",
            style: {
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
            },
          },
          `⚙ ${runId}`
        )
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
