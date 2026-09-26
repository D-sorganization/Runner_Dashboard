// @vitest-environment jsdom
/**
 * useGroupCostGuard.test.ts — confirm Board spend before sending (SC-D7, #1342).
 */
import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { SendMessagePayload, ThreadInfo } from "../threadTypes";

const api = vi.hoisted(() => ({ fetchGroupCostEstimate: vi.fn() }));
vi.mock("../../Staff/staffApi", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../Staff/staffApi")>()),
  ...api,
}));

import { useGroupCostGuard } from "../useGroupCostGuard";

const GROUP: ThreadInfo = { id: "g1", title: "Board", kind: "group", participants: [], status: "active", meta: { group: "board" } };
const DIRECT: ThreadInfo = { ...GROUP, id: "d1", kind: "direct", meta: {} };
const PAYLOAD: SendMessagePayload = { body: "Should we adopt X?", idempotencyKey: "k1" };
const OVER = {
  group_id: "board",
  total_cost_usd: 0.84,
  cost_per_seat: { alpha: 0.5, bravo: 0.34 },
  exceeds_threshold: true,
  threshold_usd: 0.5,
};

afterEach(() => vi.clearAllMocks());

function setup(thread: ThreadInfo | null) {
  const send = vi.fn().mockResolvedValue({ ok: true });
  const hook = renderHook(({ t }) => useGroupCostGuard(t, send), { initialProps: { t: thread } });
  return { send, hook };
}

describe("useGroupCostGuard", () => {
  it("sends a direct-thread message straight through, without an estimate", async () => {
    const { send, hook } = setup(DIRECT);
    await act(async () => {
      await hook.result.current.send(PAYLOAD);
    });
    expect(api.fetchGroupCostEstimate).not.toHaveBeenCalled();
    expect(send).toHaveBeenCalledWith(PAYLOAD);
  });

  it("sends a group message under the threshold without asking", async () => {
    api.fetchGroupCostEstimate.mockResolvedValue({ ...OVER, total_cost_usd: 0.1, exceeds_threshold: false });
    const { send, hook } = setup(GROUP);
    await act(async () => {
      await hook.result.current.send(PAYLOAD);
    });
    expect(api.fetchGroupCostEstimate).toHaveBeenCalledWith("board", PAYLOAD.body);
    expect(send).toHaveBeenCalledWith(PAYLOAD);
    expect(hook.result.current.pending).toBeNull();
  });

  it("holds a message over the threshold until the user confirms, then sends with confirm_cost", async () => {
    api.fetchGroupCostEstimate.mockResolvedValue(OVER);
    const { send, hook } = setup(GROUP);
    let result: Promise<unknown> = Promise.resolve();
    act(() => {
      result = hook.result.current.send(PAYLOAD);
    });
    await waitFor(() => expect(hook.result.current.pending).toEqual(OVER));
    expect(send).not.toHaveBeenCalled();

    await act(async () => {
      await hook.result.current.confirm();
    });
    await expect(result).resolves.toEqual({ ok: true });
    expect(send).toHaveBeenCalledWith({ ...PAYLOAD, meta: { confirm_cost: true } });
    expect(hook.result.current.pending).toBeNull();
  });

  it("cancelling resolves the send as not sent, so the composer keeps the draft", async () => {
    api.fetchGroupCostEstimate.mockResolvedValue(OVER);
    const { send, hook } = setup(GROUP);
    let result: Promise<unknown> = Promise.resolve();
    act(() => {
      result = hook.result.current.send(PAYLOAD);
    });
    await waitFor(() => expect(hook.result.current.pending).not.toBeNull());
    act(() => hook.result.current.cancel());
    await expect(result).resolves.toMatchObject({ ok: false, error: expect.stringContaining("not sent") });
    expect(send).not.toHaveBeenCalled();
  });

  it("switching threads cancels a held message", async () => {
    api.fetchGroupCostEstimate.mockResolvedValue(OVER);
    const { send, hook } = setup(GROUP);
    let result: Promise<unknown> = Promise.resolve();
    act(() => {
      result = hook.result.current.send(PAYLOAD);
    });
    await waitFor(() => expect(hook.result.current.pending).not.toBeNull());
    hook.rerender({ t: DIRECT });
    await expect(result).resolves.toMatchObject({ ok: false });
    expect(hook.result.current.pending).toBeNull();
    expect(send).not.toHaveBeenCalled();
  });

  it("when the estimate is unavailable it sends anyway: the backend guard still refuses unconfirmed spend", async () => {
    api.fetchGroupCostEstimate.mockRejectedValue(new Error("offline"));
    const { send, hook } = setup(GROUP);
    await act(async () => {
      await hook.result.current.send(PAYLOAD);
    });
    expect(send).toHaveBeenCalledWith(PAYLOAD);
  });
});
