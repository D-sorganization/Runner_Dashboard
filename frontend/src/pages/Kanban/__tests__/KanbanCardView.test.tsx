// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { KanbanCardView } from "../KanbanCardView";
import type { KanbanCard } from "../types";

afterEach(() => cleanup());

function baseCard(overrides: Partial<KanbanCard> = {}): KanbanCard {
  return {
    owner: "D-sorganization",
    repo: "Runner_Dashboard",
    issue_number: 99,
    column: "pr_open",
    column_label: "PR open",
    matched_columns: ["pr_open"],
    stale_reviews: [],
    reasons: [],
    sources: [],
    ...overrides,
  };
}

describe("KanbanCardView URL safety", () => {
  it("does not render javascript: or data: URLs as anchor hrefs", () => {
    const card = baseCard({
      sources: [
        {
          kind: "issue",
          label: "Issue",
          url: "javascript:alert(1)",
          available: true,
        },
        {
          kind: "pr",
          label: "PR",
          url: "data:text/html,<script>alert(1)</script>",
          available: true,
        },
      ],
    });

    const { container } = render(<KanbanCardView card={card} />);

    const anchors = container.querySelectorAll("a[href]");
    for (const anchor of anchors) {
      const href = anchor.getAttribute("href") ?? "";
      expect(href).not.toMatch(/^javascript:/i);
      expect(href).not.toMatch(/^data:/i);
    }

    expect(screen.getByText("Issue")).toBeInTheDocument();
    expect(screen.getByText("PR")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Issue" })).toBeNull();
    expect(screen.queryByRole("link", { name: "PR" })).toBeNull();
  });

  it("renders https issue title links when URL is safe", () => {
    const card = baseCard({
      sources: [
        {
          kind: "issue",
          label: "Issue",
          url: "https://github.com/D-sorganization/Runner_Dashboard/issues/99",
          available: true,
        },
      ],
    });

    render(<KanbanCardView card={card} />);

    const titleLink = screen.getByRole("link", { name: "Runner_Dashboard#99" });
    expect(titleLink).toHaveAttribute(
      "href",
      "https://github.com/D-sorganization/Runner_Dashboard/issues/99",
    );
  });

  it("falls back to plain text title when primary issue URL is unsafe", () => {
    const card = baseCard({
      sources: [
        {
          kind: "issue",
          label: "Issue",
          url: "javascript:alert(document.cookie)",
          available: true,
        },
      ],
    });

    render(<KanbanCardView card={card} />);

    expect(screen.getByText("Runner_Dashboard#99")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Runner_Dashboard#99" })).toBeNull();
  });
});
