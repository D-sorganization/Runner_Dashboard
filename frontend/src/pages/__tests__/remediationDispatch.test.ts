/**
 * Unit tests for pages/remediationDispatch.ts — pure helpers shared by the
 * Remediation PRs/Issues sub-tabs (decomposition #836, pass 8).
 *
 * These lock the legacy coalescing / filtering / styling behaviour 1:1.
 */
import { describe, expect, it, vi, afterEach } from "vitest";
import {
  getComplexityStyle,
  getJudgementStyle,
  getTypeStyle,
  issueKey,
  issueMatchesFilters,
  pillStyle,
  prAgeHours,
  prAgeLabel,
  prMatchesFilters,
  prRowId,
} from "../remediationDispatch";

afterEach(() => vi.useRealTimers());

describe("prRowId", () => {
  it("prefers number, then pr_number, then id", () => {
    expect(prRowId({ number: 7, pr_number: 9, id: 1 })).toBe("7");
    expect(prRowId({ pr_number: 9, id: 1 })).toBe("9");
    expect(prRowId({ id: "abc" })).toBe("abc");
  });
});

describe("prMatchesFilters", () => {
  it("hides drafts when showDrafts is false", () => {
    expect(prMatchesFilters({ draft: true }, "", "", false)).toBe(false);
    expect(prMatchesFilters({ draft: true }, "", "", true)).toBe(true);
  });

  it("matches repo across aliases case-insensitively", () => {
    expect(prMatchesFilters({ repository: "Org/Repo" }, "org/repo", "", true)).toBe(
      true,
    );
    expect(prMatchesFilters({ full_name: "x/y" }, "org/repo", "", true)).toBe(false);
  });

  it("matches author across aliases", () => {
    expect(
      prMatchesFilters({ user: { login: "Octocat" } }, "", "octo", true),
    ).toBe(true);
    expect(prMatchesFilters({ login: "bot" }, "", "octo", true)).toBe(false);
  });
});

describe("prAgeHours / prAgeLabel", () => {
  it("prefers precomputed age_hours", () => {
    expect(prAgeHours({ age_hours: 12 })).toBe(12);
  });

  it("derives from created_at when no age_hours", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-01-02T00:00:00Z"));
    const hrs = prAgeHours({ created_at: "2026-01-01T00:00:00Z" });
    expect(hrs).toBeCloseTo(24, 5);
  });

  it("returns null when neither present", () => {
    expect(prAgeHours({})).toBeNull();
  });

  it("formats hours under 48 and days otherwise", () => {
    expect(prAgeLabel({ age_hours: 5 })).toBe("5h");
    expect(prAgeLabel({ age_hours: 72 })).toBe("3d");
    expect(prAgeLabel({})).toBe("-");
  });
});

describe("issueKey", () => {
  it("uses repo + number when present", () => {
    expect(issueKey({ repo: "o/r", number: 3 })).toBe("o/r:3");
  });

  it("falls back to linear id / url / title for numberless issues", () => {
    expect(issueKey({ linear: { id: "L1" } })).toBe(":L1");
    expect(issueKey({ url: "http://x" })).toBe(":http://x");
    expect(issueKey({})).toBe(":linear");
  });
});

describe("taxonomy style maps", () => {
  it("returns mapped style for known keys and neutral fallback otherwise", () => {
    expect(getTypeStyle("bug").background).toBe("var(--badge-danger-bg)");
    expect(getTypeStyle("unknown").background).toBe("var(--badge-neutral-bg)");
    expect(getTypeStyle(undefined).background).toBe("var(--badge-neutral-bg)");
  });

  it("maps complexity tiers", () => {
    expect(getComplexityStyle("trivial").background).toBe(
      "var(--badge-success-bg)",
    );
    expect(getComplexityStyle("deep").background).toBe("var(--badge-danger-bg)");
    expect(getComplexityStyle(undefined).background).toBe(
      "var(--badge-neutral-bg)",
    );
  });

  it("flags design/contested judgement as danger", () => {
    expect(getJudgementStyle("design").background).toBe("var(--badge-danger-bg)");
    expect(getJudgementStyle("contested").background).toBe(
      "var(--badge-danger-bg)",
    );
    expect(getJudgementStyle("objective").background).toBe(
      "var(--badge-success-bg)",
    );
    expect(getJudgementStyle(undefined).background).toBe(
      "var(--badge-neutral-bg)",
    );
  });
});

describe("pillStyle", () => {
  it("merges defaults with the supplied overrides", () => {
    const s = pillStyle({ color: "red" });
    expect(s.display).toBe("inline-block");
    expect(s.color).toBe("red");
  });
});

describe("issueMatchesFilters", () => {
  const issue = {
    repo: "o/r",
    pickable: true,
    taxonomy: { complexity: "routine", judgement: "objective" },
  };

  it("passes when all filters empty", () => {
    expect(issueMatchesFilters(issue, "", "", "", false)).toBe(true);
  });

  it("filters by exact repo", () => {
    expect(issueMatchesFilters(issue, "o/r", "", "", false)).toBe(true);
    expect(issueMatchesFilters(issue, "other", "", "", false)).toBe(false);
  });

  it("filters by complexity and judgement", () => {
    expect(issueMatchesFilters(issue, "", "routine", "", false)).toBe(true);
    expect(issueMatchesFilters(issue, "", "deep", "", false)).toBe(false);
    expect(issueMatchesFilters(issue, "", "", "preference", false)).toBe(false);
  });

  it("filters by pickable-only", () => {
    expect(issueMatchesFilters({ pickable: false }, "", "", "", true)).toBe(false);
    expect(issueMatchesFilters({ pickable: true }, "", "", "", true)).toBe(true);
  });
});
