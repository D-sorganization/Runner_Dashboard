// @vitest-environment jsdom
/**
 * Plan quota panel and notional-dollar labels on the Staff tab (#1588).
 */
import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { PlanQuotaView, UsageSnapshotView } from "../Staff/PlanQuota";
import { formatEffortUsd, quotaRows, type QuotaReport, type UsageSnapshotReport } from "../Staff/staffApi";

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

describe("Fleet usage snapshot (#3042)", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  const SNAPSHOT: UsageSnapshotReport = {
    checked_at: "2026-10-26T17:20:00Z",
    received_at: "2026-10-26T17:20:30Z",
    snapshot_age_seconds: 60,
    snapshot_stale: false,
    providers: [
      {
        provider: "codex",
        label: "Codex",
        status: "ok",
        quota_used_pct: 7,
        quota_limit: 100,
        reset_at: null,
        near_limit_flag: false,
        machines: [
          {
            machine: "DeskComputer",
            used_percent: 7,
            resets_at: null,
            observed_at: "2026-10-26T17:19:00Z",
            age_seconds: 90,
            stale: false,
          },
          {
            machine: "ControlTower",
            used_percent: 37,
            resets_at: null,
            observed_at: "2026-10-24T22:25:00Z",
            age_seconds: 9_999_999,
            stale: true,
          },
        ],
      },
    ],
  };

  it("renders an empty state when no snapshot has been ingested yet", () => {
    render(<UsageSnapshotView report={{ snapshot_stale: true, providers: [] }} error={null} />);
    expect(screen.getByTestId("usage-snapshot-empty")).toBeTruthy();
  });

  it("renders the snapshot age line and a stale machine row in the danger tone", () => {
    render(<UsageSnapshotView report={SNAPSHOT} error={null} />);
    expect(screen.getByTestId("usage-snapshot").textContent).toContain("Snapshot 1 min ago");
    const stale = screen.getByTestId("usage-snapshot-codex-ControlTower");
    expect(stale.querySelector(".badge-tone-danger")).toBeTruthy();
    const fresh = screen.getByTestId("usage-snapshot-codex-DeskComputer");
    expect(fresh.querySelector(".badge-tone-danger")).toBeNull();
  });

  it("flags the snapshot itself as stale once it is over 2 hours old", () => {
    const stale: UsageSnapshotReport = { ...SNAPSHOT, snapshot_stale: true, snapshot_age_seconds: 3 * 3600 };
    render(<UsageSnapshotView report={stale} error={null} />);
    expect(screen.getByTestId("usage-snapshot").textContent).toContain("stale");
  });
});
