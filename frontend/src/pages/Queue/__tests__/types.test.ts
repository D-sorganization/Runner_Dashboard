import { describe, it, expect } from "vitest";

import {
  formatReason,
  normalizeSortValue,
  normalizeStalePayload,
  normalizeStaleRun,
  sortRows,
  sortStateNext,
  STALE_REASONS,
} from "../types";
import type { WorkflowRun } from "../mobileTypes";

describe("sortStateNext", () => {
  it("starts ascending on a fresh column", () => {
    expect(sortStateNext(null, "repo")).toEqual({ key: "repo", dir: "asc" });
  });

  it("toggles direction when the same column is reselected", () => {
    expect(sortStateNext({ key: "repo", dir: "asc" }, "repo")).toEqual({
      key: "repo",
      dir: "desc",
    });
    expect(sortStateNext({ key: "repo", dir: "desc" }, "repo")).toEqual({
      key: "repo",
      dir: "asc",
    });
  });

  it("resets to ascending when switching columns", () => {
    expect(sortStateNext({ key: "repo", dir: "desc" }, "branch")).toEqual({
      key: "branch",
      dir: "asc",
    });
  });
});

describe("normalizeSortValue", () => {
  it("maps nullish to empty string and booleans to 0/1", () => {
    expect(normalizeSortValue(null)).toBe("");
    expect(normalizeSortValue(undefined)).toBe("");
    expect(normalizeSortValue(true)).toBe(1);
    expect(normalizeSortValue(false)).toBe(0);
  });

  it("passes numbers through unchanged", () => {
    expect(normalizeSortValue(42)).toBe(42);
  });

  it("parses ISO-ish date strings to epoch millis", () => {
    const iso = "2026-01-02T03:04:05Z";
    expect(normalizeSortValue(iso)).toBe(Date.parse(iso));
  });

  it("extracts numeric content from mixed strings", () => {
    expect(normalizeSortValue("12m")).toBe(12);
    // A string with no digits strips to "" → Number("") === 0 (preserved quirk).
    expect(normalizeSortValue("plain")).toBe(0);
  });
});

describe("sortRows", () => {
  // `wait` carries numeric seconds so ordering is well-defined; ids 1 and 3
  // tie at 10 to exercise stable ordering.
  const runs: WorkflowRun[] = [
    { id: 1, head_branch: "10" },
    { id: 2, head_branch: "30" },
    { id: 3, head_branch: "10" },
  ];
  const accessors = { wait: (r: WorkflowRun) => r.head_branch };

  it("returns a copy when no sort key is active", () => {
    const out = sortRows(runs, null, accessors);
    expect(out).toEqual(runs);
    expect(out).not.toBe(runs);
  });

  it("sorts ascending and is stable on ties", () => {
    const out = sortRows(runs, { key: "wait", dir: "asc" }, accessors);
    expect(out.map((r) => r.id)).toEqual([1, 3, 2]);
  });

  it("sorts descending", () => {
    const out = sortRows(runs, { key: "wait", dir: "desc" }, accessors);
    expect(out.map((r) => r.id)[0]).toBe(2);
  });

  it("ignores an unknown accessor key", () => {
    const out = sortRows(runs, { key: "missing", dir: "asc" }, accessors);
    expect(out).toEqual(runs);
  });
});

describe("formatReason", () => {
  it("humanises slugs and defaults blanks to 'unknown'", () => {
    expect(formatReason("superseded_pr_head")).toBe("superseded pr head");
    expect(formatReason("")).toBe("unknown");
  });
});

describe("normalizeStaleRun", () => {
  it("resolves repo from nested object and back-fills aliases", () => {
    const run = normalizeStaleRun({
      repository: { name: "repo-x" },
      id: 99,
      workflow_name: "CI",
      head_branch: "feature",
      head_sha: "abc",
      pull_request_number: 7,
    });
    expect(run.repo).toBe("repo-x");
    expect(run.run_id).toBe(99);
    expect(run.workflow).toBe("CI");
    expect(run.branch).toBe("feature");
    expect(run.run_head_sha).toBe("abc");
    expect(run.pr_number).toBe(7);
    expect(run.safe_to_cancel).toBe(false);
  });

  it("falls back to 'unknown' repo and '?' fields", () => {
    const run = normalizeStaleRun({});
    expect(run.repo).toBe("unknown");
    expect(run.workflow).toBe("?");
    expect(run.branch).toBe("?");
    expect(run.run_id).toBeNull();
  });
});

describe("normalizeStalePayload", () => {
  it("derives reason counts from runs when none are supplied", () => {
    const payload = normalizeStalePayload({
      runs: [
        { repo: "a", reason: "age_threshold", safe_to_cancel: true },
        { repo: "b", reason: "age_threshold" },
      ],
    });
    expect(payload.stale_count).toBe(2);
    expect(payload.reason_counts.age_threshold).toBe(2);
    // All known reasons are present and zero-initialised.
    STALE_REASONS.forEach((reason) => {
      expect(payload.reason_counts[reason]).toBeGreaterThanOrEqual(0);
    });
  });

  it("honours explicit reason counts and defaults", () => {
    const payload = normalizeStalePayload({
      stale_count: 5,
      cancelled_count: 1,
      errors: ["boom"],
      reason_counts: { superseded_pr_head: 3 },
      runs: [],
    });
    expect(payload.stale_count).toBe(5);
    expect(payload.cancelled_count).toBe(1);
    expect(payload.errors).toEqual(["boom"]);
    expect(payload.reason_counts.superseded_pr_head).toBe(3);
    expect(payload.min_age_minutes).toBe(60);
  });

  it("tolerates a null payload", () => {
    const payload = normalizeStalePayload(null);
    expect(payload.runs).toEqual([]);
    expect(payload.stale_count).toBe(0);
  });
});
