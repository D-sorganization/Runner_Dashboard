/**
 * consoleThreads.test.ts — resolving a role to a real backend thread (#1446).
 *
 * The console used to invent ids such as `thread-barb-auto` that the backend
 * never created, so every send returned 404. A role now resolves to its most
 * recent live thread, or a newly created one.
 */
import { describe, expect, it, vi } from "vitest";
import { pickRoleThread, resolveRoleThread, threadKindForRole, type ThreadApi } from "../consoleThreads";
import type { ThreadInfo } from "../threadTypes";

function thread(overrides: Partial<ThreadInfo>): ThreadInfo {
  return {
    id: "t-1",
    title: "Conversation",
    kind: "direct",
    participants: ["user:me", "maintenance"],
    status: "active",
    ...overrides,
  };
}

describe("threadKindForRole", () => {
  it("routes Barb through auto threads and every other role through direct threads", () => {
    expect(threadKindForRole("barb")).toBe("auto");
    expect(threadKindForRole("maintenance")).toBe("direct");
  });
});

describe("pickRoleThread", () => {
  it("returns the most recently active live thread for the role", () => {
    const older = thread({ id: "old", last_message_at: "2026-09-25T10:00:00Z" });
    const newer = thread({ id: "new", last_message_at: "2026-09-25T12:00:00Z" });
    expect(pickRoleThread([older, newer], "maintenance")?.id).toBe("new");
  });

  it("ignores archived threads, other roles and threads of the wrong kind", () => {
    const threads = [
      thread({ id: "archived", status: "archived" }),
      thread({ id: "other", participants: ["user:me", "librarian"] }),
      thread({ id: "group", kind: "group" }),
    ];
    expect(pickRoleThread(threads, "maintenance")).toBeNull();
  });

  it("only reuses auto threads for Barb", () => {
    const direct = thread({ id: "barb-direct", participants: ["user:me", "barb"] });
    const auto = thread({ id: "barb-auto", kind: "auto", participants: ["user:me", "barb"] });
    expect(pickRoleThread([direct, auto], "barb")?.id).toBe("barb-auto");
  });
});

describe("resolveRoleThread", () => {
  function api(existing: ThreadInfo[], created?: ThreadInfo): ThreadApi & {
    listThreads: ReturnType<typeof vi.fn>;
    createThread: ReturnType<typeof vi.fn>;
  } {
    return {
      listThreads: vi.fn().mockResolvedValue(existing),
      createThread: vi.fn().mockResolvedValue(created),
    };
  }

  it("reuses an existing thread without creating one", async () => {
    const existing = thread({ id: "live" });
    const deps = api([existing]);
    await expect(resolveRoleThread("maintenance", "Fleet Maintenance", deps)).resolves.toBe(existing);
    expect(deps.listThreads).toHaveBeenCalledWith("maintenance");
    expect(deps.createThread).not.toHaveBeenCalled();
  });

  it("creates a thread of the right kind when none exists", async () => {
    const created = thread({ id: "fresh", kind: "auto", participants: ["user:me", "barb"] });
    const deps = api([], created);
    await expect(resolveRoleThread("barb", "Barb", deps)).resolves.toBe(created);
    expect(deps.createThread).toHaveBeenCalledWith({
      role: "barb",
      kind: "auto",
      title: "Conversation with Barb",
    });
  });

  it("rejects a created thread the role does not participate in (postcondition)", async () => {
    const wrong = thread({ id: "wrong", participants: ["user:me"] });
    await expect(resolveRoleThread("maintenance", "Fleet Maintenance", api([], wrong))).rejects.toThrow(
      /does not include role 'maintenance'/,
    );
  });

  it("rejects an empty role name (precondition)", async () => {
    await expect(resolveRoleThread(" ", "", api([]))).rejects.toThrow(/role name/);
  });

  it("propagates backend failures instead of inventing a thread", async () => {
    const deps: ThreadApi = {
      listThreads: vi.fn().mockRejectedValue(new Error("conversations unavailable")),
      createThread: vi.fn(),
    };
    await expect(resolveRoleThread("maintenance", "Fleet Maintenance", deps)).rejects.toThrow(
      "conversations unavailable",
    );
  });
});
