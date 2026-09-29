// @vitest-environment jsdom
/** System-authored card messages keep their card; plain system notices render markdown (#1718). */
import "@testing-library/jest-dom/vitest";
import React from "react";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { MessageItem } from "../MessageItem";
import type { ThreadMessage } from "../threadTypes";

afterEach(cleanup);

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
