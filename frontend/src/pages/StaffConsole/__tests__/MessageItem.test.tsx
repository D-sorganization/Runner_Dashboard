// @vitest-environment jsdom
/** System-authored card messages keep their card; plain system notices render markdown (#1718). */
import "@testing-library/jest-dom/vitest";
import React from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { MessageItem } from "../MessageItem";
import type { ThreadMessage } from "../threadTypes";

afterEach(cleanup);

describe("Barb shared continuity status", () => {
  it.each([
    ["saved", "Short summary saved to shared Barb memory."],
    ["replayed", "Short summary already saved to shared Barb memory."],
    ["unavailable", "Shared Barb memory unavailable; summary save is unconfirmed."],
    ["omitted", "No valid continuity summary was supplied; nothing was saved."],
  ])("reports %s using the server outcome", (status, label) => {
    render(<MessageItem message={systemMessage({ author_kind: "staff", author: "barb", kind: "text",
      body_md: "Your answer.", meta: { shared_memory_summary: status } })} />);
    expect(screen.getByRole("status")).toHaveTextContent(label);
    expect(screen.getByTestId("message-m1")).toHaveTextContent("Your answer.");
  });
});

function systemMessage(overrides: Partial<ThreadMessage>): ThreadMessage {
  return {
    id: "m1",
    thread_id: "t1",
    author: "system",
    author_kind: "system",
    body_md: "**Staff Run Started**: `run-9876`",
    created_at: "2026-09-28T00:00:00Z",
    ...overrides,
  } as ThreadMessage;
}

describe("MessageItem system messages", () => {
  it("renders a system-authored run_card message as a run card", () => {
    const { container } = render(
      <MessageItem
        message={systemMessage({
          kind: "run_card",
          meta: { run: { id: "run-9876", status: "running", run_number: 9876 } },
        })}
      />,
    );
    const card = container.querySelector('[data-run-id="run-9876"], .staff-run-card');
    expect(card).not.toBeNull();
    expect(card?.textContent).toMatch(/9876/);
  });

  it("renders a plain system notice's markdown instead of raw asterisks", () => {
    render(<MessageItem message={systemMessage({ kind: "system" })} />);
    const notice = screen.getByTestId("message-m1");
    expect(notice.textContent).not.toContain("**");
    expect(notice.querySelector("strong")?.textContent).toBe("Staff Run Started");
  });
});

/** #1774: a dropped-action warning in `meta.warnings` must surface visibly, not just in meta. */
describe("MessageItem action warnings (#1774)", () => {
  function roleMessage(overrides: Partial<ThreadMessage>): ThreadMessage {
    return {
      id: "m2",
      thread_id: "t1",
      author: "board-secretary",
      author_kind: "staff",
      kind: "text",
      body_md: "I'll convene the Board.",
      delivery: "complete",
      created_at: "2026-09-28T00:00:00Z",
      ...overrides,
    } as ThreadMessage;
  }

  it("renders a compact notice for a dropped-action warning", () => {
    render(
      <MessageItem
        message={roleMessage({
          meta: {
            warnings: [
              "Role 'board-secretary' does not hold permission for action 'board.convene'; action dropped.",
            ],
          },
        })}
      />,
    );
    const notice = screen.getByRole("note", { name: /action warnings/i });
    expect(notice).toHaveTextContent(
      "Action not proposed: Role 'board-secretary' does not hold permission for action 'board.convene'; action dropped.",
    );
  });

  it("renders nothing when the message has no warnings", () => {
    render(<MessageItem message={roleMessage({})} />);
    expect(screen.queryByRole("note", { name: /action warnings/i })).not.toBeInTheDocument();
  });
});

/** #2680: a persona avatar image replaces the letter circle in the thread header when available. */
describe("MessageItem avatar", () => {
  function roleMessage(overrides: Partial<ThreadMessage>): ThreadMessage {
    return {
      id: "m3",
      thread_id: "t1",
      author: "barb",
      author_kind: "staff",
      kind: "text",
      body_md: "On it.",
      delivery: "complete",
      created_at: "2026-09-28T00:00:00Z",
      ...overrides,
    } as ThreadMessage;
  }

  it("renders an <img> when avatarUrl is provided", () => {
    render(<MessageItem message={roleMessage({})} avatarUrl="/api/v1/staff/avatars/barb.webp" />);
    const img = screen.getByTestId("message-m3").querySelector("img.thread-avatar--image");
    expect(img).toHaveAttribute("src", "/api/v1/staff/avatars/barb.webp");
  });

  it("falls back to the letter circle when avatarUrl is absent", () => {
    render(<MessageItem message={roleMessage({})} />);
    const header = screen.getByTestId("message-m3");
    expect(header?.querySelector("img")).toBeNull();
    expect(header?.querySelector(".thread-avatar")).toHaveTextContent("B");
  });

  it("falls back to the letter circle when the avatar image fails to load", () => {
    render(<MessageItem message={roleMessage({})} avatarUrl="/api/v1/staff/avatars/missing.webp" />);
    const header = screen.getByTestId("message-m3");
    const img = header.querySelector("img.thread-avatar--image") as HTMLImageElement;
    expect(img).not.toBeNull();
    fireEvent.error(img);
    expect(header.querySelector("img")).toBeNull();
    expect(header.querySelector(".thread-avatar")).toHaveTextContent("B");
  });
});
