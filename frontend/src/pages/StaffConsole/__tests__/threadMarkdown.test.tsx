// @vitest-environment jsdom
/**
 * threadMarkdown.test.tsx — Unit tests for sanitized markdown, code copy,
 * issue/PR previews, and XSS prevention (SC-D4, Issue #1318).
 */
import "@testing-library/jest-dom/vitest";
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { ThreadMarkdown, sanitizeMarkdown, extractIssueOrRunLinks } from "../threadMarkdown";

afterEach(cleanup);

describe("sanitizeMarkdown & ThreadMarkdown", () => {
  it("renders basic markdown headings, lists, bold, and code", () => {
    const md = "# Title\n\n**bold text** and `inline code`\n\n- item 1\n- item 2";
    render(<ThreadMarkdown content={md} />);

    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Title");
    expect(screen.getByText("bold text")).toBeInTheDocument();
    expect(screen.getByText("inline code")).toBeInTheDocument();
    expect(screen.getAllByRole("listitem")).toHaveLength(2);
  });

  it("sanitizes dangerous XSS payloads like script tags and event handlers", () => {
    const xssPayload = `
      Normal text
      <script>alert("pwned")</script>
      <img src="x" onerror="alert('xss')" />
      <a href="javascript:alert('link-xss')">Dangerous link</a>
    `;
    const { container } = render(<ThreadMarkdown content={xssPayload} />);

    expect(container.querySelector("script")).toBeNull();
    const img = container.querySelector("img");
    if (img) {
      expect(img.getAttribute("onerror")).toBeNull();
    }
    const link = container.querySelector("a");
    if (link) {
      expect(link.getAttribute("href")).not.toContain("javascript:");
    }
  });

  it("renders fenced code block with a working Copy button", async () => {
    const codeMd = "```typescript\nconst message = 'hello world';\nconsole.log(message);\n```";
    const writeTextMock = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, {
      clipboard: {
        writeText: writeTextMock,
      },
    });

    render(<ThreadMarkdown content={codeMd} />);

    const pre = screen.getByText(/const message = 'hello world';/);
    expect(pre).toBeInTheDocument();

    const copyBtn = screen.getByRole("button", { name: /copy code/i });
    expect(copyBtn).toBeInTheDocument();

    fireEvent.click(copyBtn);
    expect(writeTextMock).toHaveBeenCalledWith("const message = 'hello world';\nconsole.log(message);");

    // Feedback confirmation
    const { waitFor } = await import("@testing-library/react");
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /copied/i })).toBeInTheDocument();
    });
  });

  it("auto-previews issue, PR, and run references with badges", () => {
    const textWithRefs = "Fixed in #1329 and PR #1410 for run maintenance.runner_restart";
    render(<ThreadMarkdown content={textWithRefs} />);

    const issueBadge = screen.getByText("#1329");
    expect(issueBadge).toBeInTheDocument();
    expect(issueBadge.closest("a")).toHaveAttribute(
      "href",
      "https://github.com/D-sorganization/Runner_Dashboard/issues/1329"
    );

    const prBadge = screen.getByText("#1410");
    expect(prBadge).toBeInTheDocument();
    expect(prBadge.closest("a")).toHaveAttribute(
      "href",
      "https://github.com/D-sorganization/Runner_Dashboard/pull/1410"
    );
  });
});
