// @vitest-environment jsdom
/**
 * Plan quota panel and notional-dollar labels on the Staff tab (#1588).
 */
import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { PlanQuotaView } from "../Staff/PlanQuota";
import { formatEffortUsd, quotaRows, type QuotaReport } from "../Staff/staffApi";

const REPORT: QuotaReport = {
  generated_at: "2026-09-26T18:00:00Z",
  providers: [
    {
      provider: "claude",
      billing: "subscription",
      readable: true,
      quota: {
        account: "claude",
        observed_at: "2026-09-26T17:59:00Z",
        source: "claude-stream",
        plan: null,
        limited_until: null,
        peak_percent: 91,
        windows: [
          { name: "five_hour", used_percent: 12, resets_at: "2026-09-26T20:00:00Z" },
          { name: "seven_day", used_percent: 91, resets_at: "2026-09-30T00:00:00Z" },
        ],
      },
    },
    { provider: "gemini", billing: "subscription", readable: false, quota: null },
    { provider: "ollama", billing: "local", readable: false, quota: null },
  ],
};

describe("Plan quota (#1588)", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("marks run dollars as a notional estimate", () => {
    expect(formatEffortUsd(1.234)).toBe("≈ $1.23");
    vi.spyOn(console, "warn").mockImplementation(() => {});
    expect(formatEffortUsd(null)).toBe("—");
  });

  it("lists subscription plans only, fullest window first, with a tone", () => {
    const rows = quotaRows(REPORT);
    expect(rows.map((r) => r.provider)).toEqual(["claude", "gemini"]);
    expect(rows[0]).toMatchObject({ peak: 91, tone: "danger", summary: "7d 91% · 5h 12%" });
    expect(rows[1]).toMatchObject({ peak: null, tone: "neutral", summary: "not readable" });
  });

  it("renders one row per plan", () => {
    render(<PlanQuotaView report={REPORT} error={null} />);
    expect(screen.getByTestId("plan-quota-claude").textContent).toContain("7d 91%");
    expect(screen.getByTestId("plan-quota-gemini").textContent).toContain("not readable");
    expect(screen.queryByTestId("plan-quota-ollama")).toBeNull();
  });
});
