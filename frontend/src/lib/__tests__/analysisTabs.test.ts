/**
 * Unit tests for lib/analysisTabs.ts — the shared Analysis sub-tab key set /
 * routing predicate extracted from the legacy App.tsx (decomposition #836).
 */
import { describe, expect, it } from "vitest";
import { ANALYSIS_TAB_KEYS, isAnalysisTabKey } from "../analysisTabs";

describe("isAnalysisTabKey", () => {
  it("returns true for every canonical analysis key", () => {
    for (const key of ANALYSIS_TAB_KEYS) {
      expect(isAnalysisTabKey(key)).toBe(true);
    }
  });

  it("returns false for non-analysis keys and junk input", () => {
    expect(isAnalysisTabKey("overview")).toBe(false);
    expect(isAnalysisTabKey("queue")).toBe(false);
    expect(isAnalysisTabKey(undefined)).toBe(false);
    expect(isAnalysisTabKey(null)).toBe(false);
    expect(isAnalysisTabKey(42)).toBe(false);
  });
});
