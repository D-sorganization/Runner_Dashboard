/**
 * panelApi.test.ts — createPanel reads the cost-guard refusal from the `error`
 * envelope and from a legacy `detail` body (#1635). Kept apart from
 * NewPanelForm.test.tsx, which mocks the panelApi module.
 */
import { describe, expect, it, vi } from "vitest";
import type { PanelCostEstimate } from "../panelApi";
import { createPanel } from "../panelApi";

const mockEstimate: PanelCostEstimate = {
  group_id: "panel",
  total_cost_usd: 1.5,
  threshold_usd: 1.0,
  cost_per_seat: {
    Architect: 0.45,
    Skeptic: 0.45,
    Operator: 0.45,
    Moderator: 0.15,
  },
  exceeds_threshold: true,
  warning: "Estimated cost $1.50 exceeds $1.00 threshold.",
};

describe("createPanel error parsing (from error envelope and legacy detail body)", () => {
  it("reads cost-guard refusal from error envelope", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          error: {
            code: "group_cost_guard_threshold_exceeded",
            message: "Envelope cost warning",
            estimate: mockEstimate,
            retryable: true,
          },
        }),
        { status: 400, headers: { "Content-Type": "application/json" } },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    try {
      await expect(
        createPanel({
          topic: "Test topic",
          mode: "debate",
          rounds: 3,
          moderator_provider: "claude",
          confirm_cost: false,
          experts: [
            { name: "Exp1", perspective: "P1", provider: "claude" },
            { name: "Exp2", perspective: "P2", provider: "claude" },
            { name: "Exp3", perspective: "P3", provider: "claude" },
          ],
        }),
      ).rejects.toMatchObject({
        name: "PanelCostRequired",
        message: "Envelope cost warning",
        estimate: mockEstimate,
      });
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it("reads cost-guard refusal from legacy detail envelope", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          detail: {
            code: "group_cost_guard_threshold_exceeded",
            message: "Legacy detail cost warning",
            estimate: mockEstimate,
            retryable: true,
          },
        }),
        { status: 400, headers: { "Content-Type": "application/json" } },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    try {
      await expect(
        createPanel({
          topic: "Test topic",
          mode: "debate",
          rounds: 3,
          moderator_provider: "claude",
          confirm_cost: false,
          experts: [
            { name: "Exp1", perspective: "P1", provider: "claude" },
            { name: "Exp2", perspective: "P2", provider: "claude" },
            { name: "Exp3", perspective: "P3", provider: "claude" },
          ],
        }),
      ).rejects.toMatchObject({
        name: "PanelCostRequired",
        message: "Legacy detail cost warning",
        estimate: mockEstimate,
      });
    } finally {
      vi.unstubAllGlobals();
    }
  });
});
