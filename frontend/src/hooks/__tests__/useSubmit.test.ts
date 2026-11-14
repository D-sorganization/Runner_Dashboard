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
});
