// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { dispatchDashboardNavigation } from "../dashboardNavigation";
import type { ThreadMessage } from "../threadTypes";

const resultMessage = (id: string): ThreadMessage =>
  ({
    id,
    thread_id: "thread-1",
    author: "system",
    author_kind: "system",
    kind: "action_result",
    body_md: "Navigating",
    meta: { action: "dashboard.navigate", success: true, result: { navigate_to: "projects" } },
  }) as ThreadMessage;

afterEach(() => {
  vi.restoreAllMocks();
});

describe("dashboard navigation event", () => {
  it("does not replay an approved route result after a module reload", async () => {
    const listener = vi.fn();
    window.addEventListener("runner-dashboard:navigate", listener);
    const message = resultMessage("reload-guard");
    dispatchDashboardNavigation(message);

    vi.resetModules();
    const reloaded = await import("../dashboardNavigation");
    reloaded.dispatchDashboardNavigation(message);

    expect(listener).toHaveBeenCalledTimes(1);
    window.removeEventListener("runner-dashboard:navigate", listener);
  });

  it("still navigates when session storage is unavailable", () => {
    const listener = vi.fn();
    const get = vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("storage blocked");
    });
    const set = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("storage blocked");
    });
    window.addEventListener("runner-dashboard:navigate", listener);

    dispatchDashboardNavigation(resultMessage("storage-blocked"));

    expect(get).toHaveBeenCalled();
    expect(set).not.toHaveBeenCalled();
    expect(listener).toHaveBeenCalledWith(
      expect.objectContaining({ detail: { tabId: "projects" } }),
    );
    window.removeEventListener("runner-dashboard:navigate", listener);
  });
});
