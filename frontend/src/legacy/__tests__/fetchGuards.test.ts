import { afterEach, describe, expect, it, vi } from "vitest";
import {
  installLegacyFetchGuards,
  shouldBypassServiceWorkerCache,
} from "../fetchGuards";

describe("fetchGuards", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("bypasses service-worker cache only for credential API paths", () => {
    expect(shouldBypassServiceWorkerCache("/api/credentials")).toBe(true);
    expect(shouldBypassServiceWorkerCache("/api/credentials/set-key")).toBe(true);
    expect(shouldBypassServiceWorkerCache("/api/credential-status")).toBe(false);
    expect(shouldBypassServiceWorkerCache("/api/runners")).toBe(false);
  });

  it("adds no-store cache options for credential API requests", async () => {
    const fetchImpl = vi.fn().mockResolvedValue(new Response(null, { status: 204 }));
    const targetWindow = { ...window, fetch: fetchImpl as unknown as typeof fetch };

    installLegacyFetchGuards({
      emitSessionExpired: vi.fn(),
      shouldIgnoreUnauthorizedResponse: () => false,
      tryRefreshSession: vi.fn(),
      targetWindow,
    });

    await targetWindow.fetch("/api/credentials/set-key", { method: "POST" });

    expect(fetchImpl).toHaveBeenCalledWith("/api/credentials/set-key", {
      method: "POST",
      cache: "no-store",
    });
  });

  it("silently refreshes and retries once before surfacing session expiry", async () => {
    const fetchImpl = vi
      .fn()
      .mockResolvedValueOnce(new Response(null, { status: 401 }))
      .mockResolvedValueOnce(new Response(null, { status: 204 }));
    const emitSessionExpired = vi.fn();
    const tryRefreshSession = vi.fn().mockResolvedValue(true);
    const targetWindow = { ...window, fetch: fetchImpl as unknown as typeof fetch };

    installLegacyFetchGuards({
      emitSessionExpired,
      shouldIgnoreUnauthorizedResponse: () => false,
      tryRefreshSession,
      targetWindow,
    });

    const response = await targetWindow.fetch("/api/runners");

    expect(response.status).toBe(204);
    expect(tryRefreshSession).toHaveBeenCalledTimes(1);
    expect(fetchImpl).toHaveBeenCalledTimes(2);
    expect(emitSessionExpired).not.toHaveBeenCalled();
  });

  it("announces session expiry when refresh cannot recover the request", async () => {
    const fetchImpl = vi.fn().mockResolvedValue(new Response(null, { status: 401 }));
    const emitSessionExpired = vi.fn();
    const showToast = vi.fn();
    const targetWindow = { ...window, fetch: fetchImpl as unknown as typeof fetch };

    installLegacyFetchGuards({
      emitSessionExpired,
      shouldIgnoreUnauthorizedResponse: () => false,
      tryRefreshSession: vi.fn().mockResolvedValue(false),
      getToaster: () => ({ showToast }),
      targetWindow,
    });

    await targetWindow.fetch("/api/runners");

    expect(showToast).toHaveBeenCalledWith(
      "Your session has expired. Please log in again to continue.",
      { variant: "error", title: "Session expired" },
    );
    expect(emitSessionExpired).toHaveBeenCalledTimes(1);
  });
});
