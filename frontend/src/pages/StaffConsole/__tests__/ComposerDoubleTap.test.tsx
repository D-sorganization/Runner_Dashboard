// @vitest-environment jsdom
/**
 * ComposerDoubleTap.test.tsx — regression for #3497: a rapid double-tap must
 * send exactly once. Split out of Composer.test.tsx, which is already at the
 * repo's 500-line file cap.
 */
import "@testing-library/jest-dom/vitest";
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { Composer } from "../Composer";
import type { StaffRoleItem } from "../types";

const MOCK_ROLES: StaffRoleItem[] = [
  { name: "barb", title: "Executive Secretary", summary: "Attention gate" },
];

afterEach(cleanup);

describe("Composer double-tap", () => {
  it("sends exactly once when the send button is tapped twice before the first send settles", async () => {
    let resolveSend: (value: { ok: boolean }) => void = () => {};
    const onSend = vi.fn(() => new Promise<{ ok: boolean }>((resolve) => { resolveSend = resolve; }));
    render(<Composer threadId="thread-double-tap" roles={MOCK_ROLES} onSendMessage={onSend} />);

    const textarea = screen.getByPlaceholderText(/message barb/i);
    fireEvent.change(textarea, { target: { value: "Don't send me twice" } });
    const sendButton = screen.getByRole("button", { name: /send/i });

    // Both native clicks land in the same task, before React re-renders to
    // disable the button -- the real shape of a mobile double-tap.
    act(() => {
      sendButton.click();
      sendButton.click();
    });

    expect(onSend).toHaveBeenCalledTimes(1);

    resolveSend({ ok: true });
    await waitFor(() => expect(textarea).toHaveValue(""));
  });
});
