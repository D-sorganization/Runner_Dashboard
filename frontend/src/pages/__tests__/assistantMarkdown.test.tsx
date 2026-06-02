// @vitest-environment jsdom
/**
 * Unit tests for pages/assistantMarkdown — the minimal Markdown renderer
 * extracted from the legacy App.tsx (decomposition #836, pass 9).
 */
import "@testing-library/jest-dom/vitest";
import React from "react";
import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { inlineMarkdown, renderMarkdown } from "../assistantMarkdown";

afterEach(cleanup);

function renderBlocks(md: string) {
  return render(<div data-testid="md">{renderMarkdown(md)}</div>);
}

describe("renderMarkdown", () => {
  it("returns an empty array for empty input", () => {
    expect(renderMarkdown("")).toEqual([]);
  });

  it("renders a paragraph", () => {
    const { container } = renderBlocks("hello world");
    expect(container.querySelector("p")?.textContent).toBe("hello world");
  });

  it("renders a fenced code block verbatim", () => {
    const { container } = renderBlocks("```\nconst x = 1;\nconst y = 2;\n```");
    const code = container.querySelector("pre code");
    expect(code?.textContent).toBe("const x = 1;\nconst y = 2;");
  });

  it("renders an unordered list", () => {
    const { container } = renderBlocks("- one\n- two");
    const items = container.querySelectorAll("ul li");
    expect(items).toHaveLength(2);
    expect(items[0].textContent).toBe("one");
    expect(items[1].textContent).toBe("two");
  });

  it("renders an ordered list", () => {
    const { container } = renderBlocks("1. first\n2. second");
    const items = container.querySelectorAll("ol li");
    expect(items).toHaveLength(2);
    expect(items[0].textContent).toBe("first");
  });

  it("maps heading levels 1-3 to h4-h6", () => {
    const { container } = renderBlocks("# H1\n## H2\n### H3");
    expect(container.querySelector("h4")?.textContent).toBe("H1");
    expect(container.querySelector("h5")?.textContent).toBe("H2");
    expect(container.querySelector("h6")?.textContent).toBe("H3");
  });

  it("emits a <br> for blank lines", () => {
    const { container } = renderBlocks("a\n\nb");
    expect(container.querySelector("br")).not.toBeNull();
  });
});

describe("inlineMarkdown", () => {
  function renderInline(md: string) {
    return render(<span data-testid="inl">{inlineMarkdown(md)}</span>);
  }

  it("renders bold, italic and inline code", () => {
    const { container } = renderInline("**b** and *i* and `c`");
    expect(container.querySelector("strong")?.textContent).toBe("b");
    expect(container.querySelector("em")?.textContent).toBe("i");
    expect(container.querySelector("code")?.textContent).toBe("c");
  });

  it("renders a link with safe rel/target attributes", () => {
    const { container } = renderInline("see [docs](https://example.com)");
    const a = container.querySelector("a");
    expect(a?.textContent).toBe("docs");
    expect(a?.getAttribute("href")).toBe("https://example.com");
    expect(a?.getAttribute("rel")).toBe("noopener noreferrer");
    expect(a?.getAttribute("target")).toBe("_blank");
  });

  it("passes through plain text untouched", () => {
    const { getByTestId } = renderInline("just text");
    expect(getByTestId("inl").textContent).toBe("just text");
  });
});
