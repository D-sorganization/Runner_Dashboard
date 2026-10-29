// @vitest-environment jsdom
/**
 * Composer.test.tsx — Unit tests for Staff Console Composer, keyboard navigation,
 * mention autocomplete, slash commands, draft persistence, and reliable send (SC-D4, Issue #1318).
 */
import "@testing-library/jest-dom/vitest";
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { Composer } from "../Composer";
import type { StaffRoleItem } from "../types";

const MOCK_ROLES: StaffRoleItem[] = [
  { name: "barb", title: "Executive Secretary", summary: "Attention gate" },
  { name: "board", title: "Board of Directors", summary: "Governance" },
  { name: "librarian", title: "Librarian", summary: "Documentation" },
  { name: "fleet-maintenance", title: "Fleet Maintenance", summary: "Operations" },
];

afterEach(cleanup);

describe("Composer Component", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it("renders input, send button, and voice input button", () => {
    render(<Composer threadId="thread-1" roles={MOCK_ROLES} onSendMessage={vi.fn()} />);

    expect(screen.getByPlaceholderText(/message barb or type \/dispatch/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /send/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /voice/i })).toBeInTheDocument();
  });

  it("disables send button when input is empty or whitespace only", () => {
    render(<Composer threadId="thread-1" roles={MOCK_ROLES} onSendMessage={vi.fn()} />);
    const sendBtn = screen.getByRole("button", { name: /send/i });
    const textarea = screen.getByPlaceholderText(/message barb/i);

    // Initially empty -> disabled
    expect(sendBtn).toBeDisabled();

    // Whitespace only -> disabled
    fireEvent.change(textarea, { target: { value: "    \n   " } });
    expect(sendBtn).toBeDisabled();

    // Non-empty text -> enabled
    fireEvent.change(textarea, { target: { value: "Hello Barb" } });
    expect(sendBtn).toBeEnabled();
  });

  it("auto-grows textarea rows from 1 up to 10 lines and caps at 10", () => {
    render(<Composer threadId="thread-1" roles={MOCK_ROLES} onSendMessage={vi.fn()} />);
    const textarea = screen.getByPlaceholderText(/message barb/i) as HTMLTextAreaElement;

    // 1 line
    fireEvent.change(textarea, { target: { value: "Line 1" } });
    expect(textarea.rows).toBe(1);

    // 4 lines
    fireEvent.change(textarea, { target: { value: "Line 1\nLine 2\nLine 3\nLine 4" } });
    expect(textarea.rows).toBe(4);

    // 12 lines -> capped at 10
    const twelveLines = Array.from({ length: 12 }, (_, i) => `Line ${i + 1}`).join("\n");
    fireEvent.change(textarea, { target: { value: twelveLines } });
    expect(textarea.rows).toBe(10);
  });

  it("renders a muted hint line underneath the composer", () => {
    render(<Composer threadId="thread-1" roles={MOCK_ROLES} onSendMessage={vi.fn()} />);
    expect(screen.getByText(/enter to send, shift\+enter for a new line, @ to mention/i)).toBeInTheDocument();
  });


  it("submits message on Enter and creates newline on Shift+Enter", () => {
    const onSend = vi.fn().mockResolvedValue({ ok: true });
    render(<Composer threadId="thread-1" roles={MOCK_ROLES} onSendMessage={onSend} />);

    const textarea = screen.getByPlaceholderText(/message barb/i);

    // Shift+Enter does NOT submit
    fireEvent.change(textarea, { target: { value: "Hello world" } });
    fireEvent.keyDown(textarea, { key: "Enter", shiftKey: true });
    expect(onSend).not.toHaveBeenCalled();

    // Plain Enter submits
    fireEvent.keyDown(textarea, { key: "Enter", shiftKey: false });
    expect(onSend).toHaveBeenCalledTimes(1);
    expect(onSend).toHaveBeenCalledWith(
      expect.objectContaining({
        body: "Hello world",
        idempotencyKey: expect.any(String),
      })
    );
  });

  it("shows @mention autocomplete popup and selects role via keyboard", async () => {
    const onSend = vi.fn().mockResolvedValue({ ok: true });
    render(<Composer threadId="thread-1" roles={MOCK_ROLES} onSendMessage={onSend} />);

    const textarea = screen.getByPlaceholderText(/message barb/i);

    // Type @ to trigger mention menu
    fireEvent.change(textarea, { target: { value: "Hello @" } });
    expect(screen.getByRole("listbox", { name: /role mentions/i })).toBeInTheDocument();
    expect(screen.getByText("Executive Secretary")).toBeInTheDocument();

    // Keyboard navigate down to 'board' and hit Enter
    fireEvent.keyDown(textarea, { key: "ArrowDown" });
    fireEvent.keyDown(textarea, { key: "Enter" });

    // The mention is inserted into the textarea
    expect(textarea).toHaveValue("Hello @board ");
    // Autocomplete menu is dismissed
    expect(screen.queryByRole("listbox", { name: /role mentions/i })).toBeNull();
  });

  it("shows slash command autocomplete and selects command via keyboard", async () => {
    render(<Composer threadId="thread-1" roles={MOCK_ROLES} onSendMessage={vi.fn()} />);

    const textarea = screen.getByPlaceholderText(/message barb/i);

    // Type / at start
    fireEvent.change(textarea, { target: { value: "/" } });
    expect(screen.getByRole("listbox", { name: /slash commands/i })).toBeInTheDocument();
    expect(screen.getByText("/dispatch")).toBeInTheDocument();
    expect(screen.getByText("/review")).toBeInTheDocument();
    expect(screen.getAllByText("/status").length).toBeGreaterThan(0);

    // Select with keyboard Enter
    fireEvent.keyDown(textarea, { key: "Enter" });

    // The command is inserted
    expect(textarea).toHaveValue("/dispatch ");
    expect(screen.queryByRole("listbox", { name: /slash commands/i })).toBeNull();
  });

  it("persists draft in localStorage per thread and clears upon successful send", async () => {
    const onSend = vi.fn().mockResolvedValue({ ok: true });
    const { unmount } = render(
      <Composer threadId="thread-abc" roles={MOCK_ROLES} onSendMessage={onSend} />
    );

    const textarea = screen.getByPlaceholderText(/message barb/i);
    fireEvent.change(textarea, { target: { value: "Draft message for abc" } });

    expect(localStorage.getItem("staff-console:draft:thread-abc")).toBe("Draft message for abc");

    // Unmount and remount with same threadId restores draft
    unmount();
    render(<Composer threadId="thread-abc" roles={MOCK_ROLES} onSendMessage={onSend} />);
    const restoredTextarea = screen.getByPlaceholderText(/message barb/i);
    expect(restoredTextarea).toHaveValue("Draft message for abc");

    // Send successfully
    fireEvent.click(screen.getByRole("button", { name: /send/i }));
    await waitFor(() => {
      expect(onSend).toHaveBeenCalled();
      expect(localStorage.getItem("staff-console:draft:thread-abc")).toBeNull();
      expect(restoredTextarea).toHaveValue("");
    });
  });

  it("preserves draft and allows retry with same Idempotency-Key on send failure", async () => {
    const onSend = vi
      .fn()
      .mockRejectedValueOnce(new Error("Network error"))
      .mockResolvedValueOnce({ ok: true });

    const view = render(<Composer threadId="thread-1" roles={MOCK_ROLES} onSendMessage={onSend} />);

    const textarea = screen.getByPlaceholderText(/message barb/i);
    fireEvent.change(textarea, { target: { value: "Important request" } });

    // Attempt send -> fails
    fireEvent.click(screen.getByRole("button", { name: /send/i }));

    await waitFor(() => {
      expect(screen.getByText(/failed to send message/i)).toBeInTheDocument();
      expect(screen.getByRole("button", { name: /retry/i })).toBeInTheDocument();
    });

    // Draft is kept
    expect(textarea).toHaveValue("Important request");
    const firstCallKey = onSend.mock.calls[0][0].idempotencyKey;
    expect(firstCallKey).toBeDefined();

    view.rerender(<Composer threadId="thread-1" roles={MOCK_ROLES} onSendMessage={onSend} disabled />);
    expect(screen.getByRole("button", { name: /retry/i })).toBeDisabled();
    expect(textarea).toHaveValue("Important request");
    view.rerender(<Composer threadId="thread-1" roles={MOCK_ROLES} onSendMessage={onSend} />);

    // Click retry
    fireEvent.click(screen.getByRole("button", { name: /retry/i }));

    await waitFor(() => {
      expect(onSend).toHaveBeenCalledTimes(2);
      const secondCallKey = onSend.mock.calls[1][0].idempotencyKey;
      // Must match same idempotency key for deduplication!
      expect(secondCallKey).toBe(firstCallKey);
    });
  });

  it("sends exactly once when the send button is tapped twice before the first send settles (mobile double-tap)", async () => {
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

  it("uses a new idempotency key when editing a failed draft before retry", async () => {
    const onSend = vi.fn()
      .mockRejectedValueOnce(new Error("Network error"))
      .mockResolvedValueOnce({ ok: true });
    render(<Composer threadId="thread-edited-retry" roles={MOCK_ROLES} onSendMessage={onSend} />);

    const textarea = screen.getByPlaceholderText(/message barb/i);
    fireEvent.change(textarea, { target: { value: "Original request" } });
    fireEvent.click(screen.getByRole("button", { name: /send/i }));

    await waitFor(() => expect(screen.getByRole("button", { name: /retry/i })).toBeInTheDocument());
    const originalKey = onSend.mock.calls[0][0].idempotencyKey;
    fireEvent.change(textarea, { target: { value: "Edited request" } });
    fireEvent.click(screen.getByRole("button", { name: /retry/i }));

    await waitFor(() => expect(onSend).toHaveBeenCalledTimes(2));
    expect(onSend.mock.calls[1][0]).toEqual(expect.objectContaining({
      body: "Edited request",
      idempotencyKey: expect.not.stringMatching(originalKey),
    }));
  });
  it("treats a resolved { ok: false } as a failed send and keeps the draft", async () => {
    const onSend = vi.fn().mockResolvedValue({ ok: false, error: "Message not sent: the Board cost was not confirmed." });
    render(<Composer threadId="thread-ok-false" roles={MOCK_ROLES} onSendMessage={onSend} />);

    const textarea = screen.getByPlaceholderText(/message barb/i);
    fireEvent.change(textarea, { target: { value: "Ask the Board" } });
    fireEvent.click(screen.getByRole("button", { name: /send/i }));

    await waitFor(() => {
      expect(screen.getByText(/the Board cost was not confirmed/i)).toBeInTheDocument();
    });
    expect(textarea).toHaveValue("Ask the Board");
  });

  it("focuses the textarea when clicking anywhere on the composer box surface (#2540)", () => {
    render(<Composer threadId="thread-click" roles={MOCK_ROLES} onSendMessage={vi.fn()} />);
    const textarea = screen.getByPlaceholderText(/message barb/i);
    expect(textarea).not.toHaveFocus();

    fireEvent.click(screen.getByTestId("composer-box"));
    expect(textarea).toHaveFocus();
  });

  it("preserves in-flight draft text when threadId transitions from 'new' to a restored thread (#2540)", () => {
    const { rerender } = render(
      <Composer threadId="new" roles={MOCK_ROLES} onSendMessage={vi.fn()} />,
    );
    const textarea = screen.getByPlaceholderText(/message barb/i);
    fireEvent.change(textarea, { target: { value: "Typing before thread restore completes" } });

    rerender(<Composer threadId="thr_restored_1" roles={MOCK_ROLES} onSendMessage={vi.fn()} />);
    expect(textarea).toHaveValue("Typing before thread restore completes");
    expect(localStorage.getItem("staff-console:draft:thr_restored_1")).toBe(
      "Typing before thread restore completes",
    );
  });

  it("stages files via the + button into the Librarian intake queue and includes attachment metadata on send (#2540)", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({
        id: 42,
        sha256: "sha256-stub-digest",
        sensitivity: "public_reference",
      }),
    } as Response);
    const onSend = vi.fn().mockResolvedValue({ ok: true });

    render(<Composer threadId="thread-attach" roles={MOCK_ROLES} onSendMessage={onSend} />);
    expect(screen.getByTestId("composer-attach-btn")).toBeInTheDocument();

    fireEvent.change(screen.getByTestId("composer-sensitivity-select"), {
      target: { value: "public_reference" },
    });

    const file = new File(["reference content"], "architecture-notes.md", {
      type: "text/markdown",
    });
    const input = screen.getByTestId("composer-file-input");
    fireEvent.change(input, { target: { files: [file] } });

    await waitFor(() => {
      expect(screen.getByTestId("composer-attachment-architecture-notes.md")).toBeInTheDocument();
      expect(screen.getByText("Ready")).toBeInTheDocument();
    });

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/library/intake",
      expect.objectContaining({ method: "POST" }),
    );

    fireEvent.click(screen.getByRole("button", { name: /send/i }));
    await waitFor(() => {
      expect(onSend).toHaveBeenCalledWith(
        expect.objectContaining({
          body: "Attached architecture-notes.md",
          meta: {
            attachments: [
              expect.objectContaining({
                filename: "architecture-notes.md",
                sensitivity: "public_reference",
                sha256: "sha256-stub-digest",
                intake_id: 42,
              }),
            ],
          },
        }),
      );
    });
    fetchMock.mockRestore();
  });

  it("shows a pending upload and blocks both button and Enter sends until it settles", async () => {
    let resolveUpload!: (response: Response) => void;
    const upload = new Promise<Response>((resolve) => { resolveUpload = resolve; });
    const fetchMock = vi.spyOn(globalThis, "fetch").mockReturnValueOnce(upload);
    const onSend = vi.fn().mockResolvedValue({ ok: true });
    render(<Composer threadId="thread-pending-upload" roles={MOCK_ROLES} onSendMessage={onSend} />);

    try {
      fireEvent.change(screen.getByTestId("composer-file-input"), {
        target: { files: [new File(["notes"], "pending.md", { type: "text/markdown" })] },
      });
      await waitFor(() => expect(fetchMock).toHaveBeenCalled());
      const attachment = await screen.findByTestId("composer-attachment-pending.md");
      expect(attachment).toHaveTextContent(/uploading/i);
      fireEvent.change(screen.getByPlaceholderText(/message barb/i), { target: { value: "Review this file" } });

      expect(screen.getByRole("button", { name: /send/i })).toBeDisabled();
      fireEvent.keyDown(screen.getByPlaceholderText(/message barb/i), { key: "Enter" });
      expect(onSend).not.toHaveBeenCalled();
    } finally {
      resolveUpload({ ok: true, status: 200, json: async () => ({ id: 31, sha256: "pending" }) } as Response);
      fetchMock.mockRestore();
    }
  });

  it("does not retry a failed message while an attachment upload is pending", async () => {
    let resolveUpload!: (response: Response) => void;
    const upload = new Promise<Response>((resolve) => { resolveUpload = resolve; });
    const fetchMock = vi.spyOn(globalThis, "fetch").mockReturnValueOnce(upload);
    const onSend = vi.fn().mockRejectedValueOnce(new Error("temporary failure"));
    render(<Composer threadId="thread-pending-retry" roles={MOCK_ROLES} onSendMessage={onSend} />);

    try {
      fireEvent.change(screen.getByPlaceholderText(/message barb/i), { target: { value: "Review later" } });
      fireEvent.click(screen.getByRole("button", { name: /send/i }));
      await screen.findByRole("alert");
      fireEvent.change(screen.getByTestId("composer-file-input"), {
        target: { files: [new File(["notes"], "retry.md", { type: "text/markdown" })] },
      });
      await waitFor(() => expect(fetchMock).toHaveBeenCalled());

      const retry = screen.getByRole("button", { name: /^retry$/i });
      expect(retry).toBeDisabled();
      fireEvent.click(retry);
      fireEvent.keyDown(screen.getByPlaceholderText(/message barb/i), { key: "Enter" });
      expect(onSend).toHaveBeenCalledTimes(1);
    } finally {
      resolveUpload({ ok: true, status: 200, json: async () => ({ id: 32, sha256: "retry" }) } as Response);
      fetchMock.mockRestore();
    }
  });

  it("does not restore an attachment removed while its upload is pending", async () => {
    let resolveUpload!: (response: Response) => void;
    const upload = new Promise<Response>((resolve) => { resolveUpload = resolve; });
    const fetchMock = vi.spyOn(globalThis, "fetch").mockReturnValueOnce(upload);
    render(<Composer threadId="thread-removed-pending" roles={MOCK_ROLES} onSendMessage={vi.fn()} />);

    try {
      fireEvent.change(screen.getByTestId("composer-file-input"), {
        target: { files: [new File(["notes"], "removed.md", { type: "text/markdown" })] },
      });
      await waitFor(() => expect(fetchMock).toHaveBeenCalled());
      const attachment = await screen.findByTestId("composer-attachment-removed.md");
      fireEvent.click(within(attachment).getByRole("button", { name: /remove removed.md/i }));
      expect(screen.queryByTestId("composer-attachment-removed.md")).not.toBeInTheDocument();

      resolveUpload({ ok: true, status: 200, json: async () => ({ id: 33, sha256: "removed" }) } as Response);
      await waitFor(() => expect(screen.queryByTestId("composer-attachment-removed.md")).not.toBeInTheDocument());
    } finally {
      fetchMock.mockRestore();
    }
  });

  it("shows HTTP intake rejection and prevents sending an omitted attachment", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValueOnce({ ok: false, status: 503 } as Response);
    const onSend = vi.fn().mockResolvedValue({ ok: true });
    render(<Composer threadId="thread-http-upload-failure" roles={MOCK_ROLES} onSendMessage={onSend} />);

    fireEvent.change(screen.getByPlaceholderText(/message barb/i), { target: { value: "Please review this file" } });
    fireEvent.change(screen.getByTestId("composer-file-input"), {
      target: { files: [new File(["notes"], "offline.md", { type: "text/markdown" })] },
    });

    const attachment = await screen.findByTestId("composer-attachment-offline.md");
    await waitFor(() => expect(attachment).toHaveClass("staff-composer__chip--rejected"));
    expect(attachment).toHaveTextContent("Upload failed (503)");
    expect(attachment).toHaveTextContent("Confidential work");
    expect(attachment).toHaveTextContent("Needs attention");
    expect(within(attachment).getByRole("status")).toHaveAttribute("aria-live", "polite");
    expect(attachment).toHaveTextContent("Upload failed (503). The service may be temporarily unavailable; remove and reattach the file to retry.");
    expect(attachment).not.toHaveTextContent("confidential_work");
    expect(attachment).not.toHaveTextContent("rejected");
    expect(within(attachment).getByRole("button", { name: /remove offline.md/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /send/i })).toBeDisabled();
    expect(onSend).not.toHaveBeenCalled();
    fetchMock.mockRestore();
  });

  it("keeps a failed intake upload visible and blocks a send that would omit the file", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockRejectedValueOnce(new TypeError("Failed to fetch"));
    const onSend = vi.fn().mockResolvedValue({ ok: true });
    render(<Composer threadId="thread-upload-failure" roles={MOCK_ROLES} onSendMessage={onSend} />);

    fireEvent.change(screen.getByPlaceholderText(/message barb/i), { target: { value: "Review this attachment" } });
    fireEvent.change(screen.getByTestId("composer-file-input"), {
      target: { files: [new File(["notes"], "notes.md", { type: "text/markdown" })] },
    });

    const attachment = await screen.findByTestId("composer-attachment-notes.md");
    await waitFor(() => expect(attachment).toHaveClass("staff-composer__chip--rejected"));
    expect(attachment).toHaveTextContent(/failed/i);
    expect(screen.getByRole("button", { name: /send/i })).toBeDisabled();

    fireEvent.click(screen.getByRole("button", { name: /remove notes.md/i }));
    expect(screen.getByRole("button", { name: /send/i })).toBeEnabled();
    fireEvent.click(screen.getByRole("button", { name: /send/i }));
    await waitFor(() => expect(onSend).toHaveBeenCalledTimes(1));
    expect(onSend.mock.calls[0][0].meta).toBeUndefined();
    fetchMock.mockRestore();
  });
});

// #2654: autosize cap/resize-loop mechanics are unit-tested against the hook
// directly in hooks/__tests__/useComposerAutosize.test.tsx. This covers the
// Composer-specific integration: draft and focus retention across a resize.
// jsdom never computes layout, so scrollHeight is mocked for the duration of
// this test on HTMLTextAreaElement.prototype (restored in `finally`).
it("Composer preserves focus and draft text across an autosize resize (#2654)", () => {
  type ResizeCallback = (entries: Array<{ contentRect: { width: number } }>) => void;
  let capturedCb: ResizeCallback | null = null;
  let mockScrollHeight = 60;
  class FakeResizeObserver {
    constructor(cb: ResizeCallback) {
      capturedCb = cb;
    }
    observe(): void {}
    disconnect(): void {}
  }
  const originalScrollHeightDescriptor = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "scrollHeight");
  const originalResizeObserver = globalThis.ResizeObserver;
  Object.defineProperty(HTMLTextAreaElement.prototype, "scrollHeight", {
    configurable: true,
    get: () => mockScrollHeight,
  });
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  (globalThis as any).ResizeObserver = FakeResizeObserver;

  try {
    localStorage.clear();
    render(<Composer threadId="thread-resize-focus" roles={MOCK_ROLES} onSendMessage={vi.fn()} />);
    const textarea = screen.getByPlaceholderText(/message barb/i) as HTMLTextAreaElement;

    fireEvent.change(textarea, { target: { value: "Mid-resize draft" } });
    textarea.focus();
    expect(textarea).toHaveFocus();

    mockScrollHeight = 95;
    capturedCb!([{ contentRect: { width: 600 } }]);

    expect(textarea).toHaveFocus();
    expect(textarea).toHaveValue("Mid-resize draft");
    expect(localStorage.getItem("staff-console:draft:thread-resize-focus")).toBe("Mid-resize draft");
  } finally {
    globalThis.ResizeObserver = originalResizeObserver;
    if (originalScrollHeightDescriptor) {
      Object.defineProperty(HTMLTextAreaElement.prototype, "scrollHeight", originalScrollHeightDescriptor);
    } else {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      delete (HTMLTextAreaElement.prototype as any).scrollHeight;
    }
  }
});
