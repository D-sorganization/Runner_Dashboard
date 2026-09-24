// @vitest-environment jsdom
/**
 * Unit tests for formatUsd and formatSpendSummary in pages/Staff/staffApi (issue #1289).
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { formatSpendSummary, formatUsd } from "../Staff/staffApi";

describe("formatUsd and formatSpendSummary (#1289)", () => {
  beforeEach(() => {
    vi.spyOn(console, "error").mockImplementation(() => {});
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("formatUsd formats numbers and handles non-finite/non-numbers defensively", () => {
    const warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});

    expect(formatUsd(12.345)).toBe("$12.35");
    expect(formatUsd(0)).toBe("$0.00");
    expect(formatUsd(-1.5)).toBe("$-1.50");

    // Non-finite and non-numbers return em dash
    expect(formatUsd(NaN)).toBe("—");
    expect(formatUsd(Infinity)).toBe("—");
    expect(formatUsd(null)).toBe("—");
    expect(formatUsd(undefined)).toBe("—");
    expect(formatUsd("invalid")).toBe("—");
    expect(formatUsd({ total: 10 })).toBe("—");

    // Logs warning once per unique invalid input, never throws
    const callsForNaN = warnSpy.mock.calls.filter((c) => String(c[0]).includes("NaN"));
    expect(callsForNaN.length).toBe(1);
    formatUsd(NaN);
    const callsForNaNSecond = warnSpy.mock.calls.filter((c) => String(c[0]).includes("NaN"));
    expect(callsForNaNSecond.length).toBe(1);
  });

  it("formatSpendSummary parses dictionary, single number, and malformed spend", () => {
    // 1. Dict with total
    const s1 = formatSpendSummary({ claude: 0.28, codex: 0.32, total: 0.60 });
    expect(s1.total).toBe(0.60);
    expect(s1.breakdown).toBe("claude: $0.28 · codex: $0.32");

    // 2. Dict without total (computes sum)
    const s2 = formatSpendSummary({ claude: 0.28, codex: 0.32 });
    expect(s2.total).toBeCloseTo(0.60);
    expect(s2.breakdown).toBe("claude: $0.28 · codex: $0.32");

    // 3. Single number (legacy / fallback)
    const s3 = formatSpendSummary(3.5);
    expect(s3.total).toBe(3.5);
    expect(s3.breakdown).toBe("");

    // 4. Missing or non-object spend
    expect(formatSpendSummary(null)).toEqual({ total: null, breakdown: "" });
    expect(formatSpendSummary(undefined)).toEqual({ total: null, breakdown: "" });
    expect(formatSpendSummary(NaN)).toEqual({ total: null, breakdown: "" });
    expect(formatSpendSummary("invalid")).toEqual({ total: null, breakdown: "" });
  });
});
