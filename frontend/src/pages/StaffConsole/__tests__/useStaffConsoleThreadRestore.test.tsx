// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ThreadApi } from "../consoleThreads";
import type { ThreadInfo, ThreadMessage } from "../threadTypes";
import type { StaffRoleItem } from "../types";

const api = vi.hoisted(() => ({
  fetchRoster: vi.fn(),
  fetchThread: vi.fn(),
  fetchThreadMessages: vi.fn(),
  postThreadMessage: vi.fn(),
}));

vi.mock("../../Staff/staffApi", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../Staff/staffApi")>()),
  ...api,
}));

import { useStaffConsole } from "../useStaffConsole";

const ROLES: StaffRoleItem[] = [
  { name: "barb", title: "Barb", group: "leadership", valid: true },
  { name: "maintenance", title: "Fleet Maintenance", group: "operations", valid: true },
];

const MAINT_THREAD: ThreadInfo = {
  id: "thr_real_123",
  title: "Conversation with Fleet Maintenance",
  kind: "direct",
  participants: ["user:me", "maintenance"],
  status: "active",
};

const HISTORY: ThreadMessage[] = [
  {
    id: "m1",
    thread_id: "thr_real_123",
    author: "maintenance",
    author_kind: "staff",
    kind: "text",
    body_md: "All hosts healthy.",
    delivery: "complete",
    seq: 1,
  },
];

function threadApi(overrides: Partial<ThreadApi> = {}): ThreadApi {
  return {
    listThreads: vi.fn().mockResolvedValue([MAINT_THREAD]),
    createThread: vi.fn(),
    ...overrides,
  };
}

beforeEach(() => {
  api.fetchRoster.mockResolvedValue({ roles: ROLES });
  api.fetchThreadMessages.mockResolvedValue({ messages: HISTORY });
  api.postThreadMessage.mockResolvedValue({ message: { id: "m2" } });
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("selected role thread restoration", () => {
  it("marks the initial render as restoring until the role thread list resolves", async () => {
    let resolveThreads!: (threads: ThreadInfo[]) => void;
    const deps = threadApi({
      listThreads: vi.fn(() => new Promise<ThreadInfo[]>((resolve) => { resolveThreads = resolve; })),
    });
    const { result } = renderHook(() =>
      useStaffConsole({
        roles: ROLES,
        initialRole: "barb",
        restoreInitialRoleThread: true,
        threadApi: deps,
        streamEnabled: false,
      }),
    );

    expect(result.current.openingRole).toBe("barb");
    await act(async () => resolveThreads([]));
    await waitFor(() => expect(result.current.openingRole).toBeNull());
    expect(result.current.activeThread).toBeNull();
  });
  it("prefers a selected thread only when it belongs to the requested role", async () => {
    const selectedBarbThread: ThreadInfo = {
      ...MAINT_THREAD,
      id: "thr_selected_barb",
      title: "Selected Barb conversation",
      kind: "auto",
      participants: ["user:me", "barb"],
    };
    const newerBarbThread: ThreadInfo = {
      ...selectedBarbThread,
      id: "thr_newer_barb",
      title: "Newer Barb conversation",
      updated_at: "2099-01-01T00:00:00Z",
    };
    const specialistThread: ThreadInfo = {
      ...selectedBarbThread,
      id: "thr_specialist",
      title: "Specialist conversation",
      kind: "direct",
      participants: ["user:me", "reviewer"],
    };
    const deps = threadApi({
      listThreads: vi.fn().mockResolvedValue([newerBarbThread, selectedBarbThread, specialistThread]),
    });

    const { result } = renderHook(() =>
      useStaffConsole({
        roles: ROLES,
        initialRole: "barb",
        preferredRoleThreadId: selectedBarbThread.id,
        restoreInitialRoleThread: true,
        threadApi: deps,
        streamEnabled: false,
      }),
    );

    await waitFor(() => expect(result.current.activeThread?.id).toBe(selectedBarbThread.id));
    expect(deps.listThreads).toHaveBeenCalledWith("barb");
  });

  it("falls back to a Barb thread when the selected URL thread belongs to another role", async () => {
    const barbThread: ThreadInfo = {
      ...MAINT_THREAD,
      id: "thr_barb_safe",
      kind: "auto",
      participants: ["user:me", "barb"],
    };
    const specialistThread: ThreadInfo = {
      ...barbThread,
      id: "thr_specialist",
      kind: "direct",
      participants: ["user:me", "reviewer"],
    };
    const deps = threadApi({
      listThreads: vi.fn().mockResolvedValue([specialistThread, barbThread]),
    });
    const { result } = renderHook(() =>
      useStaffConsole({
        roles: ROLES,
        initialRole: "barb",
        preferredRoleThreadId: specialistThread.id,
        restoreInitialRoleThread: true,
        threadApi: deps,
        streamEnabled: false,
      }),
    );

    await waitFor(() => expect(result.current.activeThread?.id).toBe(barbThread.id));
    expect(result.current.activeThread?.participants).toContain("barb");
    expect(deps.listThreads).toHaveBeenCalledWith("barb");
    expect(api.fetchThread).not.toHaveBeenCalledWith(specialistThread.id, expect.anything());
  });

  it("does not block the composer while re-syncing to a peer-selected thread id", async () => {
    const threadA: ThreadInfo = {
      ...MAINT_THREAD,
      id: "thr_a",
      kind: "auto",
      participants: ["user:me", "barb"],
    };
    const threadB: ThreadInfo = {
      ...threadA,
      id: "thr_b",
      title: "Conversation with Barb",
    };
    let resolveFirst!: (threads: ThreadInfo[]) => void;
    let resolveSecond!: (threads: ThreadInfo[]) => void;
    const listThreads = vi
      .fn()
      .mockImplementationOnce(
        () =>
          new Promise<ThreadInfo[]>((resolve) => {
            resolveFirst = resolve;
          }),
      )
      .mockImplementationOnce(
        () =>
          new Promise<ThreadInfo[]>((resolve) => {
            resolveSecond = resolve;
          }),
      );
    const deps = threadApi({ listThreads });
    const { result, rerender } = renderHook(
      ({ preferredRoleThreadId }: { preferredRoleThreadId: string }) =>
        useStaffConsole({
          roles: ROLES,
          initialRole: "barb",
          preferredRoleThreadId,
          restoreInitialRoleThread: true,
          threadApi: deps,
          streamEnabled: false,
        }),
      { initialProps: { preferredRoleThreadId: threadA.id } },
    );

    await act(async () => resolveFirst([threadA]));
    await waitFor(() => expect(result.current.activeThread?.id).toBe(threadA.id));

    rerender({ preferredRoleThreadId: threadB.id });
    expect(result.current.openingRole).toBeNull();
    await act(async () => resolveSecond([threadA, threadB]));
    await waitFor(() => expect(result.current.activeThread?.id).toBe(threadB.id));
    expect(result.current.openingRole).toBeNull();
    expect(listThreads).toHaveBeenCalledTimes(2);
  });

  it("keeps a new conversation when its selected URL id catches up", async () => {
    const threadA: ThreadInfo = {
      ...MAINT_THREAD,
      id: "thr_existing_barb",
      kind: "auto",
      participants: ["user:me", "barb"],
    };
    const threadB: ThreadInfo = {
      ...threadA,
      id: "thr_new_barb",
      title: "Conversation with Barb",
    };
    const deps = threadApi({
      listThreads: vi.fn().mockResolvedValue([threadA]),
      createThread: vi.fn().mockResolvedValue(threadB),
    });
    const { result, rerender } = renderHook(
      ({ preferredRoleThreadId }: { preferredRoleThreadId: string }) =>
        useStaffConsole({
          roles: ROLES,
          initialRole: "barb",
          preferredRoleThreadId,
          restoreInitialRoleThread: true,
          threadApi: deps,
          streamEnabled: false,
        }),
      { initialProps: { preferredRoleThreadId: threadA.id } },
    );

    await waitFor(() => expect(result.current.activeThread?.id).toBe(threadA.id));
    let created: ThreadInfo | null = null;
    await act(async () => {
      created = await result.current.newConversation();
    });
    expect(created?.id).toBe(threadB.id);

    rerender({ preferredRoleThreadId: threadB.id });
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    expect(result.current.activeThread?.id).toBe(threadB.id);
    expect(deps.listThreads).toHaveBeenCalledTimes(1);
  });

  it("retries passive restore when a deduped Barb list request fails", async () => {
    const restoredThread: ThreadInfo = {
      ...MAINT_THREAD,
      id: "thr_restore_retry",
      kind: "auto",
      participants: ["user:me", "barb"],
    };
    const listThreads = vi
      .fn()
      .mockRejectedValueOnce(new Error("Failed to fetch"))
      .mockResolvedValueOnce([restoredThread]);
    const deps = threadApi({ listThreads });
    const { result, rerender } = renderHook(
      (props: { restore: boolean }) =>
        useStaffConsole({
          roles: ROLES,
          initialRole: "barb",
          restoreInitialRoleThread: props.restore,
          threadApi: deps,
          streamEnabled: false,
        }),
      { initialProps: { restore: true } },
    );

    await waitFor(() => expect(listThreads).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(result.current.error?.kind).toBe("thread"));
    rerender({ restore: false });
    rerender({ restore: true });
    await waitFor(() => expect(result.current.activeThread?.id).toBe(restoredThread.id));
    expect(listThreads).toHaveBeenCalledTimes(2);
  });

  it("keeps focus provenance attached to passive and explicit thread commits", async () => {
    const restoredThread: ThreadInfo = {
      ...MAINT_THREAD,
      id: "thr_passive_restore",
      kind: "auto",
      participants: ["user:me", "barb"],
    };
    const explicitThread: ThreadInfo = {
      ...restoredThread,
      id: "thr_explicit_switch",
      title: "Explicit Barb conversation",
    };
    const deps = threadApi({
      listThreads: vi.fn().mockResolvedValue([restoredThread]),
    });
    const { result } = renderHook(() =>
      useStaffConsole({
        roles: ROLES,
        initialRole: "barb",
        restoreInitialRoleThread: true,
        threadApi: deps,
        streamEnabled: false,
      }),
    );

    await waitFor(() => expect(result.current.activeThread?.id).toBe(restoredThread.id));
    expect(result.current.activeThreadFocus).toEqual({
      threadId: restoredThread.id,
      origin: "restore",
    });

    await act(async () => result.current.openThread(explicitThread, "barb"));
    expect(result.current.activeThread?.id).toBe(explicitThread.id);
    expect(result.current.activeThreadFocus).toEqual({
      threadId: explicitThread.id,
      origin: "explicit",
    });
  });

});
