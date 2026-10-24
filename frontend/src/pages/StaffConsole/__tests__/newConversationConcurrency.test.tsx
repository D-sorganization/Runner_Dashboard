// @vitest-environment jsdom
import { act, renderHook } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import type { StaffRoleItem } from "../types";
import type { ThreadInfo } from "../threadTypes";
import * as staffApi from "../../Staff/staffApi";
import { useStaffConsole } from "../useStaffConsole";

vi.mock("../../Staff/staffApi", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../Staff/staffApi")>()),
  fetchThreadMessages: vi.fn().mockResolvedValue({ messages: [] }),
}));

function role(name: string, group: StaffRoleItem["group"], title = name): StaffRoleItem {
  return { name, title, group, valid: true };
}

const barbRole = role("barb", "leadership", "Barb");

it("shares one pending new-conversation request for repeated activation", async () => {
  const created = deferred<ThreadInfo>();
  const createdThread = thread("new-barb-thread", "barb", "New conversation");
  const createThread = vi.fn(() => created.promise);
  const { result } = renderHook(() =>
    useStaffConsole({
      roles: [barbRole],
      threadApi: { listThreads: vi.fn().mockResolvedValue([]), createThread },
      streamEnabled: false,
    }),
  );
  let first!: Promise<ThreadInfo | null>;
  let second!: Promise<ThreadInfo | null>;
  act(() => {
    first = result.current.newConversation();
    second = result.current.newConversation();
  });
  expect(createThread).toHaveBeenCalledTimes(1);
  await act(async () => {
    created.resolve(createdThread);
    expect(await first).toEqual(createdThread);
    expect(await second).toEqual(createdThread);
  });
  expect(result.current.activeThread?.id).toBe(createdThread.id);
  expect(result.current.openingRole).toBeNull();
  createThread.mockResolvedValueOnce({
    ...createdThread,
    id: "next-deliberate-conversation",
  });
  await act(async () => {
    await result.current.newConversation();
  });
  expect(createThread).toHaveBeenCalledTimes(2);
  expect(result.current.activeThread?.id).toBe("next-deliberate-conversation");
});

it("shares failure and permits a later retry without losing the existing conversation", async () => {
  const old = thread("existing", "barb", "Existing");
  const creation = deferred<ThreadInfo>();
  const createThread = vi.fn(() => creation.promise);
  const { result } = renderHook(() =>
    useStaffConsole({
      roles: [barbRole],
      initialThread: old,
      threadApi: { listThreads: vi.fn().mockResolvedValue([]), createThread },
      streamEnabled: false,
    }),
  );
  let first!: Promise<ThreadInfo | null>;
  let second!: Promise<ThreadInfo | null>;
  act(() => {
    first = result.current.newConversation();
    second = result.current.newConversation();
  });
  expect(result.current.openingRole).toBe("barb");
  await act(async () => {
    creation.reject(new Error("Creation unavailable"));
    expect(await first).toBeNull();
    expect(await second).toBeNull();
  });
  expect(createThread).toHaveBeenCalledTimes(1);
  expect(result.current.activeThread?.id).toBe(old.id);
  expect(result.current.error?.message).toContain("Creation unavailable");
  expect(result.current.openingRole).toBeNull();
  createThread.mockResolvedValueOnce({ ...old, id: "retry-created" });
  await act(async () => {
    await result.current.newConversation();
  });
  expect(result.current.activeThread?.id).toBe("retry-created");
  expect(result.current.error).toBeNull();
});

it("keeps separate console instances independent", async () => {
  const createThread = vi.fn().mockResolvedValue({
    id: "created",
    title: "New",
    kind: "auto",
    participants: ["barb"],
    status: "active",
  });
  const options = {
    roles: [barbRole],
    threadApi: { listThreads: vi.fn().mockResolvedValue([]), createThread },
    streamEnabled: false,
  };
  const first = renderHook(() => useStaffConsole(options));
  const second = renderHook(() => useStaffConsole(options));
  await act(async () => {
    await Promise.all([
      first.result.current.newConversation(),
      second.result.current.newConversation(),
    ]);
  });
  expect(createThread).toHaveBeenCalledTimes(2);
});

function thread(id: string, role: string, title = id): ThreadInfo {
  return {
    id,
    title,
    kind: role === "barb" ? "auto" : "direct",
    participants: [role],
    status: "active",
  };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: Error) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

it.each(["older-first", "newer-first"] as const)(
  "keeps the latest role selection when new-conversation and role resolution finish %s",
  async (completionOrder) => {
    const latestRole = "latest-" + completionOrder;
    const barbCreate = deferred<ThreadInfo>();
    const latestCreate = deferred<ThreadInfo>();
    const createThread = vi.fn(({ role }: { role: string }) =>
      role === "barb" ? barbCreate.promise : latestCreate.promise,
    );
    const { result } = renderHook(() =>
      useStaffConsole({
        roles: [
          barbRole,
          role(latestRole, "engineering"),
        ],
        initialRole: "barb",
        threadApi: { listThreads: vi.fn().mockResolvedValue([]), createThread },
        streamEnabled: false,
      }),
    );

    let older!: Promise<ThreadInfo | null>;
    let latest!: Promise<ThreadInfo | null>;
    act(() => {
      older = result.current.newConversation();
      latest = result.current.openRole(latestRole);
    });
    expect(result.current.openingRole).toBe(latestRole);

    let olderResult: ThreadInfo | null;
    if (completionOrder === "older-first") {
      await act(async () => {
        barbCreate.resolve(thread("barb-new", "barb"));
        olderResult = await older;
      });
      expect(result.current.openingRole).toBe(latestRole);
      expect(result.current.activeThread).toBeNull();
      await act(async () => {
        latestCreate.resolve(thread("latest-selected", latestRole));
        expect(await latest).toEqual(thread("latest-selected", latestRole));
      });
    } else {
      await act(async () => {
        latestCreate.resolve(thread("latest-selected", latestRole));
        expect(await latest).toEqual(thread("latest-selected", latestRole));
      });
      await act(async () => {
        barbCreate.resolve(thread("barb-new", "barb"));
        olderResult = await older;
      });
    }

    expect(olderResult!).toBeNull();
    expect(result.current.selectedRole).toBe(latestRole);
    expect(result.current.activeThread?.id).toBe("latest-selected");
    expect(result.current.openingRole).toBeNull();
  },
);

it("ignores a stale create failure without replacing the latest role's busy state or error", async () => {
  const latestRole = "latest-after-stale-error";
  const barbCreate = deferred<ThreadInfo>();
  const latestCreate = deferred<ThreadInfo>();
  const createThread = vi.fn(({ role }: { role: string }) =>
    role === "barb" ? barbCreate.promise : latestCreate.promise,
  );
  const { result } = renderHook(() =>
    useStaffConsole({
      roles: [
        barbRole,
        role(latestRole, "engineering"),
      ],
      initialRole: "barb",
      threadApi: { listThreads: vi.fn().mockResolvedValue([]), createThread },
      streamEnabled: false,
    }),
  );

  let stale!: Promise<ThreadInfo | null>;
  let latest!: Promise<ThreadInfo | null>;
  act(() => {
    stale = result.current.newConversation();
    latest = result.current.openRole(latestRole);
  });
  let staleResult: ThreadInfo | null;
  await act(async () => {
    barbCreate.reject(new Error("old request failed"));
    staleResult = await stale;
  });
  expect(result.current.openingRole).toBe(latestRole);
  expect(result.current.error).toBeNull();

  await act(async () => {
    latestCreate.resolve(thread("latest-selected", latestRole));
    expect(await latest).toEqual(thread("latest-selected", latestRole));
  });
  expect(staleResult!).toBeNull();
  expect(result.current.error).toBeNull();
  expect(result.current.activeThread?.id).toBe("latest-selected");
  expect(result.current.openingRole).toBeNull();
});

it("does not reopen a thread or clear a newer selection after a deliberate close", async () => {
  const pendingCreate = deferred<ThreadInfo>();
  const old = thread("old-thread", "barb");
  const { result } = renderHook(() =>
    useStaffConsole({
      roles: [barbRole],
      initialRole: "barb",
      initialThread: old,
      threadApi: {
        listThreads: vi.fn().mockResolvedValue([]),
        createThread: vi.fn().mockReturnValue(pendingCreate.promise),
      },
      streamEnabled: false,
    }),
  );

  let creating!: Promise<ThreadInfo | null>;
  act(() => {
    creating = result.current.newConversation();
  });
  expect(result.current.openingRole).toBe("barb");
  act(() => result.current.closeThread());
  expect(result.current.activeThread).toBeNull();
  expect(result.current.openingRole).toBeNull();

  await act(async () => {
    pendingCreate.resolve(thread("stale-created", "barb"));
    expect(await creating).toBeNull();
  });
  expect(result.current.activeThread).toBeNull();
  expect(result.current.openingRole).toBeNull();
});

it("does not let a pending new conversation replace an explicitly opened thread", async () => {
  const pendingCreate = deferred<ThreadInfo>();
  const opened = thread("manually-opened", "grok");
  const { result } = renderHook(() =>
    useStaffConsole({
      roles: [barbRole],
      initialRole: "barb",
      threadApi: {
        listThreads: vi.fn().mockResolvedValue([]),
        createThread: vi.fn().mockReturnValue(pendingCreate.promise),
      },
      streamEnabled: false,
    }),
  );

  let creating!: Promise<ThreadInfo | null>;
  act(() => {
    creating = result.current.newConversation();
  });
  act(() => result.current.openThread(opened, "grok"));
  await act(async () => {
    pendingCreate.resolve(thread("stale-created", "barb"));
    expect(await creating).toBeNull();
  });
  expect(result.current.activeThread?.id).toBe(opened.id);
  expect(result.current.selectedRole).toBe("grok");
  expect(result.current.openingRole).toBeNull();
});

it("does not let mount-time role restoration replace a newer explicit thread selection", async () => {
  const restore = deferred<ThreadInfo[]>();
  const opened = thread("manually-opened-during-restore", "grok");
  const threadApi = {
    listThreads: vi.fn().mockReturnValue(restore.promise),
    createThread: vi.fn(),
  };
  const { result } = renderHook(() =>
    useStaffConsole({
      roles: [barbRole],
      initialRole: "barb",
      restoreInitialRoleThread: true,
      threadApi,
      streamEnabled: false,
    }),
  );
  expect(result.current.openingRole).toBe("barb");
  act(() => result.current.openThread(opened, "grok"));
  await act(async () => {
    restore.resolve([thread("restored-barb", "barb")]);
  });
  expect(result.current.activeThread?.id).toBe(opened.id);
  expect(result.current.selectedRole).toBe("grok");
  expect(result.current.openingRole).toBeNull();
});

it("ignores route-load failures after a newer explicit thread selection", async () => {
  const routeLoad = deferred<{ thread: ThreadInfo; messages: [] }>();
  const fetchThread = vi
    .spyOn(staffApi, "fetchThread")
    .mockReturnValue(routeLoad.promise);
  const { result } = renderHook(() =>
    useStaffConsole({
      roles: [barbRole],
      initialThreadId: "route-thread",
      streamEnabled: false,
    }),
  );
  const opened = thread("explicit-selection", "grok");
  act(() => result.current.openThread(opened, "grok"));

  await act(async () => {
    routeLoad.reject(new Error("stale route failed"));
    await Promise.resolve();
  });
  expect(result.current.activeThread?.id).toBe(opened.id);
  expect(result.current.error).toBeNull();
  fetchThread.mockRestore();
});

it("returns no thread when a pending creation resolves after the console unmounts", async () => {
  const creation = deferred<ThreadInfo>();
  const { result, unmount } = renderHook(() =>
    useStaffConsole({
      roles: [barbRole],
      threadApi: {
        listThreads: vi.fn().mockResolvedValue([]),
        createThread: vi.fn().mockReturnValue(creation.promise),
      },
      streamEnabled: false,
    }),
  );
  let pending!: Promise<ThreadInfo | null>;
  act(() => {
    pending = result.current.newConversation();
  });
  unmount();
  creation.resolve(thread("created-after-unmount", "barb"));
  await expect(pending).resolves.toBeNull();
});

it("treats an incoming route thread as newer than a pending create", async () => {
  const route = deferred<{ thread: ThreadInfo; messages: [] }>();
  const creation = deferred<ThreadInfo>();
  const fetchThread = vi
    .spyOn(staffApi, "fetchThread")
    .mockReturnValue(route.promise);
  const { result, rerender } = renderHook(({ id }: { id?: string } = {}) =>
    useStaffConsole({
      roles: [barbRole],
      initialThreadId: id,
      threadApi: {
        listThreads: vi.fn(),
        createThread: vi.fn().mockReturnValue(creation.promise),
      },
      streamEnabled: false,
    }),
  );
  let pending!: Promise<ThreadInfo | null>;
  act(() => {
    pending = result.current.newConversation();
  });
  rerender({ id: "incoming-route" });
  await act(async () => {
    route.resolve({ thread: thread("route", "grok"), messages: [] });
  });
  await act(async () => {
    creation.resolve(thread("stale-create", "barb"));
    expect(await pending).toBeNull();
  });
  expect(result.current.activeThread?.id).toBe("route");
  expect(result.current.openingRole).toBeNull();
  fetchThread.mockRestore();
});
