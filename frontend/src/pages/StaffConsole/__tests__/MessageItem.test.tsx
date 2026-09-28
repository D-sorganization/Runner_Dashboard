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
