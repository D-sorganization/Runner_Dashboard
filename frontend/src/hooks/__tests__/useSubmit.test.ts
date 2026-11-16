// @vitest-environment jsdom
import { describe, expect, it, vi } from "vitest";
import { act, renderHook, waitFor } from "@testing-library/react";
import {
  assertNever,
  failureTextFromState,
  useSubmit,
  type SubmitState,
} from "../useSubmit";

describe("useSubmit reducer helpers", () => {
  it("exhaustive never check rejects unknown action shapes", () => {
    expect(() => assertNever({ bogus: true } as never)).toThrow(/Unexpected submit state/);
  });

  it("failureTextFromState only surfaces failed", () => {
    const failed: SubmitState<void> = { status: "failed", idempotencyKey: "k", error: "nope" };
    expect(failureTextFromState(failed)).toBe("nope");
    expect(failureTextFromState({ status: "idle" })).toBeNull();
  });
});

describe("useSubmit", () => {
  it("ignores a second submit while the first is in flight", async () => {
    let resolveFirst!: (v: string) => void;
    const first = new Promise<string>((res) => {
      resolveFirst = res;
    });
    const executor = vi.fn(() => first);

    const { result } = renderHook(() => useSubmit<string>());

    act(() => {
      result.current.submit(executor);
      result.current.submit(executor);
    });

    expect(executor).toHaveBeenCalledTimes(1);
    expect(result.current.isSubmitting).toBe(true);

    await act(async () => {
      resolveFirst("ok");
      await first;
    });

    await waitFor(() => expect(result.current.state.status).toBe("succeeded"));
  });

  it("reuses the idempotency key when retrying after failure", async () => {
    const keys: string[] = [];
    const { result } = renderHook(() => useSubmit<void>());

    act(() => {
      result.current.submit(async (ctx) => {
        keys.push(ctx.idempotencyKey);
        throw new Error("HTTP 500: boom");
      });
    });

    await waitFor(() => expect(result.current.state.status).toBe("failed"));
    const firstKey = keys[0];
    expect(firstKey).toBeTruthy();

    act(() => {
      result.current.submit(async (ctx) => {
        keys.push(ctx.idempotencyKey);
      });
    });

    await waitFor(() => expect(result.current.state.status).toBe("succeeded"));
    expect(keys).toHaveLength(2);
    expect(keys[1]).toBe(firstKey);
  });

  it("mints a fresh key after reset", async () => {
    const keys: string[] = [];
    const { result } = renderHook(() => useSubmit<void>());

    act(() => {
      result.current.submit(async (ctx) => {
        keys.push(ctx.idempotencyKey);
      });
    });
    await waitFor(() => expect(result.current.state.status).toBe("succeeded"));

    act(() => {
      result.current.reset();
      result.current.submit(async (ctx) => {
        keys.push(ctx.idempotencyKey);
      });
    });
    await waitFor(() => expect(keys).toHaveLength(2));
    expect(keys[0]).not.toBe(keys[1]);
  });

  it.each([
    ["the same intent", "request-a", true],
    ["a different intent", "request-b", false],
  ])("a retry with %s reuses the key: %s", async (_name, retryIntent, reused) => {
    const keys: string[] = [];
    const { result } = renderHook(() => useSubmit<void>());
    const failing = async (ctx: { idempotencyKey: string }) => {
      keys.push(ctx.idempotencyKey);
      throw new Error("HTTP 500: boom");
    };

    act(() => result.current.submit(failing, "request-a"));
    await waitFor(() => expect(result.current.state.status).toBe("failed"));
    act(() => result.current.submit(failing, retryIntent));
    await waitFor(() => expect(keys).toHaveLength(2));

    expect(keys[1] === keys[0]).toBe(reused);
  });

  it("a request that failed keeps its key when it is sent again after another request", async () => {
    const keys: string[] = [];
    const { result } = renderHook(() => useSubmit<void>());
    const failing = async (ctx: { idempotencyKey: string }) => {
      keys.push(ctx.idempotencyKey);
      throw new Error("HTTP 502: gateway");
    };
    const send = async (intent: string, count: number) => {
      act(() => result.current.submit(failing, intent));
      await waitFor(() => expect(keys).toHaveLength(count));
      await waitFor(() => expect(result.current.state.status).toBe("failed"));
    };

    await send("request-a", 1);
    await send("request-b", 2);
    await send("request-a", 3);

    expect(keys[1]).not.toBe(keys[0]);
    expect(keys[2]).toBe(keys[0]);
  });

  it("a request that succeeded gets a fresh key when it is sent again", async () => {
    const keys: string[] = [];
    const { result } = renderHook(() => useSubmit<void>());
    let fail = true;
    const executor = async (ctx: { idempotencyKey: string }) => {
      keys.push(ctx.idempotencyKey);
      if (fail) throw new Error("HTTP 502: gateway");
    };

    act(() => result.current.submit(executor, "request-a"));
    await waitFor(() => expect(result.current.state.status).toBe("failed"));
    fail = false;
    act(() => result.current.submit(executor, "request-a"));
    await waitFor(() => expect(result.current.state.status).toBe("succeeded"));
    act(() => result.current.submit(executor, "request-a"));
    await waitFor(() => expect(keys).toHaveLength(3));

    expect(keys[1]).toBe(keys[0]);
    expect(keys[2]).not.toBe(keys[0]);
  });
});
