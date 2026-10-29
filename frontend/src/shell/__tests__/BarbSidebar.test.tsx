// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const mockUseStaffConsole = vi.hoisted(() => vi.fn());
vi.mock("../../pages/StaffConsole/useStaffConsole", () => ({
  useStaffConsole: mockUseStaffConsole,
}));

import { BarbSidebar } from "../BarbSidebar";

const sendMessage = vi.fn();
const role = {
  name: "barb",
  title: "Barb",
  summary: "Fleet concierge",
  group: "leadership" as const,
  valid: true,
};

function staffConsoleState(overrides: Record<string, unknown> = {}) {
  return {
    roles: [role],
    selectedRole: "barb",
    activeThread: null,
    openingRole: null,
    messages: [],
    isReconnecting: false,
    error: null,
    currentRole: role,
    sendMessage,
    costGuard: { pending: null, confirm: vi.fn(), cancel: vi.fn() },
    approveProposal: vi.fn(),
    denyProposal: vi.fn(),
    cancelRun: vi.fn(),
    answerRun: vi.fn(),
    dismissError: vi.fn(),
    newConversation: vi.fn(),
    openRole: vi.fn(),
    openThread: vi.fn(),
    closeThread: vi.fn(),
    ...overrides,
  };
}

beforeEach(() => {
  sendMessage.mockResolvedValue({ ok: true });
  mockUseStaffConsole.mockReturnValue(staffConsoleState());
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("BarbSidebar", () => {
  it("continues the selected Barb conversation instead of restoring another recent thread", () => {
    window.history.replaceState({}, "", "/?thread=th-selected");
    render(<BarbSidebar open onClose={vi.fn()} onNavigate={vi.fn()} />);

    expect(mockUseStaffConsole).toHaveBeenCalledWith(
      expect.objectContaining({ initialRole: "barb", preferredRoleThreadId: "th-selected", restoreInitialRoleThread: true }),
    );
  });

  it("auto-focuses the composer textarea immediately when opened on desktop", () => {
    render(<BarbSidebar open onClose={vi.fn()} onNavigate={vi.fn()} />);
    const composer = screen.getByRole("textbox", { name: /staff conversation input/i });
    expect(composer).toHaveFocus();
  });


  it("does not stage attachments against the new-thread placeholder while restoring Barb", async () => {
    const thread = {
      id: "th-restored-barb",
      title: "Existing Barb conversation",
      kind: "auto",
      participants: ["barb"],
      status: "active",
    };
    mockUseStaffConsole.mockReturnValue(staffConsoleState({ openingRole: "barb" }));
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ id: 81, sha256: "fixture" }),
    } as Response);
    const view = render(<BarbSidebar open onClose={vi.fn()} onNavigate={vi.fn()} />);
    const fileInput = screen.getByTestId("composer-file-input");

    expect(screen.getByRole("status")).toHaveTextContent(/conversation.*attachments.*available/i);
    expect(fileInput).toBeDisabled();
    expect(screen.getByTestId("composer-attach-btn")).toBeDisabled();
    fireEvent.change(fileInput, { target: { files: [new File(["notes"], "audit.md", { type: "text/markdown" })] } });
    expect(fetchMock).not.toHaveBeenCalled();

    mockUseStaffConsole.mockReturnValue(staffConsoleState({ activeThread: thread, openingRole: null }));
    view.rerender(<BarbSidebar open onClose={vi.fn()} onNavigate={vi.fn()} />);
    expect(screen.getByTestId("composer-file-input")).toBeEnabled();
    fireEvent.change(screen.getByTestId("composer-file-input"), {
      target: { files: [new File(["notes"], "audit.md", { type: "text/markdown" })] },
    });
    await waitFor(() => expect(fetchMock).toHaveBeenCalledOnce());
    const request = fetchMock.mock.calls[0][1];
    expect(JSON.parse(String(request?.body))).toMatchObject({ thread_id: thread.id });
    fetchMock.mockRestore();
  });

  it("does not stage attachments against the previous thread during a non-Barb handoff", async () => {
    const previousThread = {
      id: "th-barb-before-handoff",
      title: "Barb conversation",
      kind: "auto",
      participants: ["barb"],
      status: "active",
    };
    const maintenanceThread = {
      id: "th-maintenance-after-handoff",
      title: "Maintenance conversation",
      kind: "direct",
      participants: ["maintenance"],
      status: "active",
    };
    mockUseStaffConsole.mockReturnValue(staffConsoleState({ activeThread: previousThread, openingRole: "maintenance" }));
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ id: 82, sha256: "fixture" }),
    } as Response);
    const view = render(<BarbSidebar open onClose={vi.fn()} onNavigate={vi.fn()} />);
    const fileInput = screen.getByTestId("composer-file-input");

    expect(screen.getByRole("status")).toHaveTextContent(/opening a conversation.*attachments.*available/i);
    expect(fileInput).toBeDisabled();
    expect(screen.getByTestId("composer-attach-btn")).toBeDisabled();
    fireEvent.change(fileInput, { target: { files: [new File(["notes"], "handoff.md", { type: "text/markdown" })] } });
    expect(fetchMock).not.toHaveBeenCalled();

    mockUseStaffConsole.mockReturnValue(staffConsoleState({ activeThread: maintenanceThread, openingRole: null }));
    view.rerender(<BarbSidebar open onClose={vi.fn()} onNavigate={vi.fn()} />);
    fireEvent.change(screen.getByTestId("composer-file-input"), {
      target: { files: [new File(["notes"], "handoff.md", { type: "text/markdown" })] },
    });
    await waitFor(() => expect(fetchMock).toHaveBeenCalledOnce());
    expect(JSON.parse(String(fetchMock.mock.calls[0][1]?.body))).toMatchObject({ thread_id: maintenanceThread.id });
    fetchMock.mockRestore();
  });

  it("holds drafts without sending into the old thread during a role handoff", async () => {
    const previousThread = {
      id: "th-before-send-handoff",
      title: "Previous Barb conversation",
      kind: "auto",
      participants: ["barb"],
      status: "active",
    };
    const nextThread = {
      id: "th-after-send-handoff",
      title: "Maintenance conversation",
      kind: "direct",
      participants: ["maintenance"],
      status: "active",
    };
    const handoffSend = vi.fn().mockResolvedValue({ ok: true });
    mockUseStaffConsole.mockReturnValue(staffConsoleState({
      activeThread: previousThread,
      openingRole: "maintenance",
      sendMessage: handoffSend,
    }));
    const view = render(<BarbSidebar open onClose={vi.fn()} onNavigate={vi.fn()} />);
    const textarea = screen.getByRole("textbox", { name: /staff conversation input/i });
    fireEvent.change(textarea, { target: { value: "Send this only to the new conversation" } });
    fireEvent.keyDown(textarea, { key: "Enter" });

    expect(handoffSend).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: /send/i })).toBeDisabled();

    mockUseStaffConsole.mockReturnValue(staffConsoleState({
      activeThread: nextThread,
      openingRole: null,
      sendMessage: handoffSend,
    }));
    view.rerender(<BarbSidebar open onClose={vi.fn()} onNavigate={vi.fn()} />);
    expect(textarea).toHaveValue("Send this only to the new conversation");
    expect(screen.getByRole("button", { name: /send/i })).toBeEnabled();
    fireEvent.click(screen.getByRole("button", { name: /send/i }));
    await waitFor(() => expect(handoffSend).toHaveBeenCalledOnce());
    expect(handoffSend.mock.calls[0][0]).toMatchObject({ body: "Send this only to the new conversation" });
  });

  it("keeps the new-chat attachment action available after an empty Barb restore", () => {
    mockUseStaffConsole.mockReturnValue(staffConsoleState({ openingRole: null }));
    render(<BarbSidebar open onClose={vi.fn()} onNavigate={vi.fn()} />);

    expect(screen.queryByRole("status")).not.toBeInTheDocument();
    expect(screen.getByTestId("composer-file-input")).toBeEnabled();
    expect(screen.getByTestId("composer-attach-btn")).toBeEnabled();
  });

  it("restores Barb history without sending a message when the sidebar opens", () => {
    window.history.replaceState({}, "", "/");
    render(<BarbSidebar open onClose={vi.fn()} onNavigate={vi.fn()} />);

    expect(mockUseStaffConsole).toHaveBeenCalledWith(
      expect.objectContaining({ initialRole: "barb", restoreInitialRoleThread: true }),
    );
    expect(sendMessage).not.toHaveBeenCalled();
  });

  it("restores focus to its launcher if focus moved while the lazy drawer mounted", async () => {
    const originalFocus = document.createElement("button");
    const launcher = document.createElement("button");
    document.body.append(originalFocus, launcher);
    originalFocus.focus();
    const restoreFocusTargetRef = { current: launcher };
    const onClose = vi.fn();
    const view = render(
      <BarbSidebar open onClose={onClose} onNavigate={vi.fn()} restoreFocusTargetRef={restoreFocusTargetRef} />,
    );
    await waitFor(() => expect(screen.getByRole("textbox", { name: /staff conversation input/i })).toHaveFocus());

    fireEvent.keyDown(window, { key: "Escape" });
    expect(onClose).toHaveBeenCalledOnce();
    view.rerender(
      <BarbSidebar open={false} onClose={onClose} onNavigate={vi.fn()} restoreFocusTargetRef={restoreFocusTargetRef} />,
    );

    await waitFor(() => expect(launcher).toHaveFocus());
    originalFocus.remove();
    launcher.remove();
  });

  it("navigates directly to fleet pages from the persistent panel", () => {
    const onNavigate = vi.fn();
    render(<BarbSidebar open onClose={vi.fn()} onNavigate={onNavigate} />);

    fireEvent.click(screen.getByRole("button", { name: /open projects/i }));
    expect(onNavigate).toHaveBeenCalledWith("projects");
  });

  it("fills and sends an UpstreamDrift starter through Barb chat", async () => {
    render(<BarbSidebar open onClose={vi.fn()} onNavigate={vi.fn()} />);

    const starter = await screen.findByRole("button", { name: /triage upstreamdrift/i });
    fireEvent.click(starter);
    const input = screen.getByRole("textbox", { name: /staff conversation input/i });
    expect((input as HTMLTextAreaElement).value).toContain("UpstreamDrift");
    expect((input as HTMLTextAreaElement).value).toContain("review open issues and pull requests in the UpstreamDrift repository");
    expect((input as HTMLTextAreaElement).value).toContain("registered workflows");
    expect(sendMessage).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: /send/i }));
    await waitFor(() =>
      expect(sendMessage).toHaveBeenCalledWith(
        expect.objectContaining({ body: expect.stringContaining("UpstreamDrift") }),
      ),
    );
  });
});


it("shows named project starters in an active Barb thread and after New chat without sending or navigating", async () => {
  const thread = { id: "barb-active", title: "Active Barb", kind: "auto", participants: ["barb"], status: "active" };
  const onThreadSelected = vi.fn();
  const newConversation = vi.fn().mockResolvedValue({ ...thread, id: "barb-new" });
  mockUseStaffConsole.mockReturnValue(staffConsoleState({ activeThread: thread, newConversation }));
  const view = render(<BarbSidebar open onClose={vi.fn()} onNavigate={vi.fn()} onThreadSelected={onThreadSelected} />);
  for (const project of ["UpstreamDrift", "AffineDrift"]) {
    fireEvent.click(await screen.findByRole("button", { name: `Start ${project}` }));
    expect((screen.getByRole("textbox", { name: /staff conversation input/i }) as HTMLTextAreaElement).value).toContain(`Triage ${project}`);
    expect(sendMessage).not.toHaveBeenCalled();
    expect(onThreadSelected).not.toHaveBeenCalled();
    expect(newConversation).not.toHaveBeenCalled();
  }
  fireEvent.click(screen.getByRole("button", { name: "New chat" }));
  await waitFor(() => expect(newConversation).toHaveBeenCalledOnce());
  mockUseStaffConsole.mockReturnValue(staffConsoleState({ activeThread: { ...thread, id: "barb-new" }, newConversation }));
  view.rerender(<BarbSidebar open onClose={vi.fn()} onNavigate={vi.fn()} onThreadSelected={onThreadSelected} />);
  expect(await screen.findByRole("button", { name: "Start UpstreamDrift" })).toBeInTheDocument();
  expect(await screen.findByRole("button", { name: "Start AffineDrift" })).toBeInTheDocument();
});
