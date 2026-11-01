// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { tryRefreshSession as TryRefreshSession } from "../sessionExpired";

describe("tryRefreshSession cooldown", () => {
  let tryRefreshSession: typeof TryRefreshSession;

  beforeEach(async () => {
    vi.useFakeTimers();
    vi.resetModules();
    ({ tryRefreshSession } = await import("../sessionExpired"));
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("dedupes concurrent callers into a single network call", async () => {
    const fetchFn = vi.fn().mockResolvedValue(new Response(null, { status: 200 }));

    const results = await Promise.all([
      tryRefreshSession(fetchFn),
      tryRefreshSession(fetchFn),
      tryRefreshSession(fetchFn),
    ]);

    expect(results).toEqual([true, true, true]);
    expect(fetchFn).toHaveBeenCalledTimes(1);
  });

  it("does not re-attempt a failed refresh again before the cooldown elapses", async () => {
    const fetchFn = vi.fn().mockResolvedValue(new Response(null, { status: 401 }));

    const first = await tryRefreshSession(fetchFn);
    expect(first).toBe(false);
    expect(fetchFn).toHaveBeenCalledTimes(1);

    // Three more independent pollers all hit a 401 immediately after the failed refresh.
    const second = await tryRefreshSession(fetchFn);
    const third = await tryRefreshSession(fetchFn);
    expect(second).toBe(false);
    expect(third).toBe(false);
    expect(fetchFn).toHaveBeenCalledTimes(1);
  });

  it("allows a fresh attempt once the cooldown window has elapsed", async () => {
    const fetchFn = vi.fn().mockResolvedValue(new Response(null, { status: 401 }));

    await tryRefreshSession(fetchFn);
    expect(fetchFn).toHaveBeenCalledTimes(1);

    vi.advanceTimersByTime(5_000);

    await tryRefreshSession(fetchFn);
    expect(fetchFn).toHaveBeenCalledTimes(2);
  });

  it("clears the cooldown after a successful refresh", async () => {
    const fetchFn = vi
      .fn()
      .mockResolvedValueOnce(new Response(null, { status: 401 }))
      .mockResolvedValueOnce(new Response(null, { status: 200 }));

    await tryRefreshSession(fetchFn);
    vi.advanceTimersByTime(5_000);
    const second = await tryRefreshSession(fetchFn);
    expect(second).toBe(true);
    expect(fetchFn).toHaveBeenCalledTimes(2);

    // Immediately after a success, a brand-new 401 should be allowed to retry right away.
    const thirdFetch = vi.fn().mockResolvedValue(new Response(null, { status: 401 }));
    const third = await tryRefreshSession(thirdFetch);
    expect(third).toBe(false);
    expect(thirdFetch).toHaveBeenCalledTimes(1);
  });
});
