import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import React from "react";
import { renderSanitizedMarkdown } from "../markdownRenderer";

describe("renderSanitizedMarkdown (SC-D4)", () => {
  it("renders plain text paragraphs", () => {
    const { container } = render(<div>{renderSanitizedMarkdown("Hello world from staff")}</div>);
    expect(container.textContent).toContain("Hello world from staff");
    expect(container.querySelector("p")).not.toBeNull();
  });

  it("sanitizes script tags and disallows javascript: protocol links to prevent XSS", () => {
    const maliciousText = '<script>alert("xss")</script>[Click me](javascript:alert(1))';
    const { container } = render(<div>{renderSanitizedMarkdown(maliciousText)}</div>);
    // Should NOT contain an actual script element
    expect(container.querySelector("script")).toBeNull();
    // Link with javascript: protocol should be sanitized or stripped
    const link = container.querySelector("a");
    if (link) {
      expect(link.getAttribute("href")).not.toContain("javascript:");
    }
  });

  it("renders bold, italic, and inline code formatting", () => {
    const md = "This is **bold**, this is *italic*, and `code snippet`.";
    const { container } = render(<div>{renderSanitizedMarkdown(md)}</div>);
    expect(container.querySelector("strong")?.textContent).toBe("bold");
    expect(container.querySelector("em")?.textContent).toBe("italic");
    expect(container.querySelector("code")?.textContent).toBe("code snippet");
  });

  it("renders fenced code blocks with copy button", async () => {
    // Mock navigator.clipboard.writeText
    const writeTextMock = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, {
      clipboard: {
        writeText: writeTextMock,
      },
    });

    const md = "```typescript\nconst a = 42;\nconsole.log(a);\n```";
    render(<div>{renderSanitizedMarkdown(md)}</div>);

    const pre = screen.getByRole("region", { name: "Code block" });
    expect(pre).not.toBeNull();
    expect(pre.textContent).toContain("const a = 42;");

    const copyBtn = screen.getByRole("button", { name: /copy/i });
    expect(copyBtn).not.toBeNull();
    fireEvent.click(copyBtn);

    expect(writeTextMock).toHaveBeenCalledWith("const a = 42;\nconsole.log(a);");
  });

  it("auto-previews links to issues, PRs, and staff runs", () => {
    const md = "Related to #1329 and PR #1410 and run_8f3a921d.";
    render(<div>{renderSanitizedMarkdown(md)}</div>);

    const issueLink = screen.getByText("#1329");
    expect(issueLink).not.toBeNull();
    expect(issueLink.getAttribute("href")).toContain("/issues/1329");

    const prLink = screen.getByText("PR #1410");
    expect(prLink).not.toBeNull();
    expect(prLink.getAttribute("href")).toContain("/pull/1410");

    const runLink = screen.getByText("run_8f3a921d");
    expect(runLink).not.toBeNull();
    expect(runLink.getAttribute("href")).toContain("/staff/runs/run_8f3a921d");
  });
});
