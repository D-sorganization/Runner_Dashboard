// @vitest-environment jsdom
/**
 * Conversation transitions for the Staff Console Composer.
 */
import "@testing-library/jest-dom/vitest";
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { Composer } from "../Composer";
import type { StaffRoleItem } from "../types";

const MOCK_ROLES: StaffRoleItem[] = [
  { name: "barb", title: "Executive Secretary", summary: "Attention gate" },
  { name: "board", title: "Board of Directors", summary: "Governance" },
  { name: "librarian", title: "Librarian", summary: "Documentation" },
  { name: "fleet-maintenance", title: "Fleet Maintenance", summary: "Operations" },
];

afterEach(cleanup);

describe("Composer conversation transitions", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it("preserves a handoff draft in StrictMode when completion changes the thread", () => {
    const { rerender } = render(
      <React.StrictMode>
        <Composer threadId="old-thread" conversationOpening roles={MOCK_ROLES} onSendMessage={vi.fn()} />
      </React.StrictMode>,
    );
    const textarea = screen.getByPlaceholderText(/message barb/i);
    fireEvent.change(textarea, { target: { value: "Keep this for the restored conversation" } });

    rerender(
      <React.StrictMode>
        <Composer threadId="restored-thread" roles={MOCK_ROLES} onSendMessage={vi.fn()} />
      </React.StrictMode>,
    );

    expect(textarea).toHaveValue("Keep this for the restored conversation");
    expect(localStorage.getItem("staff-console:draft:restored-thread")).toBe(
      "Keep this for the restored conversation",
    );
  });
  it("keeps both thread drafts when the destination already has saved text", () => {
    localStorage.setItem("staff-console:draft:old-thread", "Original saved draft");
    localStorage.setItem("staff-console:draft:restored-thread", "Destination draft");
    const { rerender } = render(
      <Composer threadId="old-thread" conversationOpening roles={MOCK_ROLES} onSendMessage={vi.fn()} />,
    );
    const textarea = screen.getByPlaceholderText(/message barb/i);
    fireEvent.change(textarea, { target: { value: "Updated outgoing draft" } });

    rerender(
      <Composer threadId="restored-thread" roles={MOCK_ROLES} onSendMessage={vi.fn()} />,
    );

    expect(textarea).toHaveValue("Destination draft");
    expect(localStorage.getItem("staff-console:draft:old-thread")).toBe("Updated outgoing draft");
    expect(localStorage.getItem("staff-console:draft:restored-thread")).toBe("Destination draft");
  });
  it("clears an attached file when the composer switches conversations", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ id: 42, sha256: "sha256-stub-digest" }),
    } as Response);
    const onSend = vi.fn().mockResolvedValue({ ok: true });
    const view = render(<Composer threadId="thread-original" roles={MOCK_ROLES} onSendMessage={onSend} />);

    fireEvent.change(screen.getByTestId("composer-file-input"), {
      target: { files: [new File(["private notes"], "original.md", { type: "text/markdown" })] },
    });
    await waitFor(() => expect(screen.getByTestId("composer-attachment-original.md")).toHaveTextContent("Ready"));

    view.rerender(<Composer threadId="thread-next" roles={MOCK_ROLES} onSendMessage={onSend} />);
    expect(screen.queryByTestId("composer-attachment-original.md")).not.toBeInTheDocument();
    fireEvent.change(screen.getByPlaceholderText(/message barb/i), { target: { value: "Review the next conversation" } });
    fireEvent.click(screen.getByRole("button", { name: /send/i }));
    await waitFor(() => expect(onSend).toHaveBeenCalledOnce());
    expect(onSend.mock.calls[0][0].meta).toBeUndefined();
    fetchMock.mockRestore();
  });
  it("keeps the first new-thread send payload when its thread id appears", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ id: 44, sha256: "new-thread-digest" }),
    } as Response);
    let resolveSend!: (result: { ok: true }) => void;
    const onSend = vi.fn(() => new Promise<{ ok: true }>((resolve) => { resolveSend = resolve; }));
    const view = render(<Composer threadId="new" roles={MOCK_ROLES} onSendMessage={onSend} />);

    fireEvent.change(screen.getByTestId("composer-file-input"), {
      target: { files: [new File(["notes"], "first-message.md", { type: "text/markdown" })] },
    });
    await waitFor(() => expect(screen.getByTestId("composer-attachment-first-message.md")).toHaveTextContent("Ready"));
    fireEvent.change(screen.getByPlaceholderText(/message barb/i), { target: { value: "Review this file" } });
    fireEvent.click(screen.getByRole("button", { name: /send/i }));
    expect(onSend).toHaveBeenCalledOnce();
    expect(onSend.mock.calls[0][0].meta).toMatchObject({
      attachments: [expect.objectContaining({ filename: "first-message.md", intake_id: 44 })],
    });

    view.rerender(<Composer threadId="thread-created-by-first-send" roles={MOCK_ROLES} onSendMessage={onSend} />);
    expect(screen.queryByTestId("composer-attachment-first-message.md")).not.toBeInTheDocument();
    resolveSend({ ok: true });
    await waitFor(() => expect(onSend).toHaveBeenCalledOnce());
    fetchMock.mockRestore();
  });
  it("does not restore an upload chip when its upload finishes after a thread switch", async () => {
    let resolveUpload!: (response: Response) => void;
    const upload = new Promise<Response>((resolve) => { resolveUpload = resolve; });
    const fetchMock = vi.spyOn(globalThis, "fetch").mockReturnValueOnce(upload);
    const onSend = vi.fn().mockResolvedValue({ ok: true });
    const view = render(<Composer threadId="thread-original-pending" roles={MOCK_ROLES} onSendMessage={onSend} />);

    try {
      fireEvent.change(screen.getByTestId("composer-file-input"), {
        target: { files: [new File(["private notes"], "pending-original.md", { type: "text/markdown" })] },
      });
      await waitFor(() => expect(fetchMock).toHaveBeenCalledOnce());
      expect(await screen.findByTestId("composer-attachment-pending-original.md")).toBeInTheDocument();

      view.rerender(<Composer threadId="thread-next-pending" roles={MOCK_ROLES} onSendMessage={onSend} />);
      expect(screen.queryByTestId("composer-attachment-pending-original.md")).not.toBeInTheDocument();
      resolveUpload({ ok: true, status: 200, json: async () => ({ id: 43, sha256: "late-digest" }) } as Response);
      await waitFor(() => expect(fetchMock).toHaveBeenCalledOnce());
      expect(screen.queryByTestId("composer-attachment-pending-original.md")).not.toBeInTheDocument();
      fireEvent.change(screen.getByPlaceholderText(/message barb/i), { target: { value: "Review the next conversation" } });
      fireEvent.click(screen.getByRole("button", { name: /send/i }));
      await waitFor(() => expect(onSend).toHaveBeenCalledOnce());
      expect(onSend.mock.calls[0][0].meta).toBeUndefined();
    } finally {
      fetchMock.mockRestore();
    }
  });
});
