// @vitest-environment jsdom
/**
 * useStaffConsole.test.tsx — shared Staff Console state for desktop and mobile (#1446).
 */
import "@testing-library/jest-dom/vitest";
import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ThreadApi } from "../consoleThreads";
import type { ThreadInfo, ThreadMessage } from "../threadTypes";
import type { StaffRoleItem } from "../types";

const api = vi.hoisted(() => ({
  fetchRoster: vi.fn(),
  fetchThreadMessages: vi.fn(),
  postThreadMessage: vi.fn(),
  decideActionProposal: vi.fn(),
}));

vi.mock("../../Staff/staffApi", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../Staff/staffApi")>()),
  ...api,
}));

import { ApiClientError } from "../../../lib/api";
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
  api.decideActionProposal.mockResolvedValue({});
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("useStaffConsole", () => {
  it("loads the roster when none is supplied", async () => {
    const { result } = renderHook(() => useStaffConsole({ threadApi: threadApi(), streamEnabled: false }));
    await waitFor(() => expect(result.current.roles).toHaveLength(2));
    expect(result.current.error).toBeNull();
  });

  it("surfaces a roster failure instead of swallowing it", async () => {
    api.fetchRoster.mockRejectedValue(new Error("503 conversations unavailable"));
    const { result } = renderHook(() => useStaffConsole({ threadApi: threadApi(), streamEnabled: false }));
    await waitFor(() => expect(result.current.error?.kind).toBe("roster"));
    expect(result.current.error?.message).toMatch(/503/);
  });

  it("opens a role on its real backend thread and loads the history", async () => {
    const deps = threadApi();
    const { result } = renderHook(() =>
      useStaffConsole({ roles: ROLES, threadApi: deps, streamEnabled: false }),
    );

    await act(async () => {
      await result.current.openRole("maintenance");
    });

    expect(deps.listThreads).toHaveBeenCalledWith("maintenance");
    expect(result.current.activeThread?.id).toBe("thr_real_123");
    expect(result.current.selectedRole).toBe("maintenance");
    await waitFor(() => expect(result.current.messages).toEqual(HISTORY));
    expect(api.fetchThreadMessages).toHaveBeenCalledWith("thr_real_123", expect.anything());
  });

  it("reports a thread that cannot be opened and keeps no fake thread", async () => {
    const deps = threadApi({ listThreads: vi.fn().mockRejectedValue(new Error("thread store offline")) });
    const { result } = renderHook(() =>
      useStaffConsole({ roles: ROLES, threadApi: deps, streamEnabled: false }),
    );

    await act(async () => {
      await result.current.openRole("maintenance");
    });

    expect(result.current.activeThread).toBeNull();
    expect(result.current.error).toEqual({ kind: "thread", message: "thread store offline" });
  });

  it("sends to the resolved thread id with the idempotency key", async () => {
    const { result } = renderHook(() =>
      useStaffConsole({ roles: ROLES, threadApi: threadApi(), streamEnabled: false }),
    );
    await act(async () => {
      await result.current.openRole("maintenance");
    });

    let outcome: { ok: boolean } | undefined;
    await act(async () => {
      outcome = await result.current.sendMessage({ body: "restart CT", idempotencyKey: "k-1" });
    });

    expect(outcome?.ok).toBe(true);
    expect(api.postThreadMessage).toHaveBeenCalledWith(
      "thr_real_123",
      { body: "restart CT", meta: undefined },
      "k-1",
    );
  });

  it("fails a send visibly and reports it", async () => {
    api.postThreadMessage.mockRejectedValue(new Error("500 Internal Server Error"));
    const { result } = renderHook(() =>
      useStaffConsole({ roles: ROLES, threadApi: threadApi(), streamEnabled: false }),
    );
    await act(async () => {
      await result.current.openRole("maintenance");
    });

    let outcome: { ok: boolean; error?: unknown } | undefined;
    await act(async () => {
      outcome = await result.current.sendMessage({ body: "hi", idempotencyKey: "k-2" });
    });

    expect(outcome).toEqual({ ok: false, error: "500 Internal Server Error" });
    expect(result.current.error).toEqual({ kind: "send", message: "500 Internal Server Error" });
  });

  it("reports a structured API refusal by its message, not [object Object] (SC-D7 cost guard)", async () => {
    const detail = { code: "group_cost_guard_threshold_exceeded", message: "Estimated group turn cost $0.84 exceeds threshold $0.50." };
    api.postThreadMessage.mockRejectedValue(
      new ApiClientError(400, detail as unknown as string, "/api/v1/staff/threads/thr_real_123/messages"),
    );
    const { result } = renderHook(() =>
      useStaffConsole({ roles: ROLES, threadApi: threadApi(), streamEnabled: false }),
    );
    await act(async () => {
      await result.current.openRole("maintenance");
    });

    let outcome: { ok: boolean; error?: unknown } | undefined;
    await act(async () => {
      outcome = await result.current.sendMessage({ body: "hi", idempotencyKey: "k-4" });
    });

    expect(outcome).toEqual({ ok: false, error: detail.message });
    expect(result.current.error).toEqual({ kind: "send", message: detail.message });
  });

  it("refuses to send without an open thread", async () => {
    const { result } = renderHook(() =>
      useStaffConsole({ roles: ROLES, threadApi: threadApi(), streamEnabled: false }),
    );
    const outcome = await result.current.sendMessage({ body: "hi", idempotencyKey: "k-3" });
    expect(outcome.ok).toBe(false);
    expect(api.postThreadMessage).not.toHaveBeenCalled();
  });

  it("reports a failed approval decision", async () => {
    api.decideActionProposal.mockRejectedValue(new Error("403 missing staff.approve"));
    const { result } = renderHook(() =>
      useStaffConsole({ roles: ROLES, threadApi: threadApi(), streamEnabled: false }),
    );

    await act(async () => {
      await result.current.approveProposal("prop-1");
    });

    expect(api.decideActionProposal).toHaveBeenCalledWith("prop-1", "approved", expect.any(String));
    expect(result.current.error).toEqual({ kind: "decision", message: "403 missing staff.approve" });
  });

  it("clears the error on dismiss", async () => {
    api.fetchRoster.mockRejectedValue(new Error("boom"));
    const { result } = renderHook(() => useStaffConsole({ threadApi: threadApi(), streamEnabled: false }));
    await waitFor(() => expect(result.current.error).not.toBeNull());
    act(() => result.current.dismissError());
    expect(result.current.error).toBeNull();
  });
});
