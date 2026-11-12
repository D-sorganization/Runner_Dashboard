import { describe, expect, it } from "vitest";
import {
  branchCompareUrl,
  codebaseReferenceGithubUrl,
  issueUrl,
  pullRequestFilesUrl,
  pullRequestUrl,
} from "../runDetailLinks";

describe("runDetailLinks", () => {
  it("builds org-qualified GitHub URLs for short repo names", () => {
    expect(pullRequestUrl("Runner_Dashboard", 42)).toBe(
      "https://github.com/D-sorganization/Runner_Dashboard/pull/42",
    );
    expect(pullRequestFilesUrl("Runner_Dashboard", 42)).toBe(
      "https://github.com/D-sorganization/Runner_Dashboard/pull/42/files",
    );
    expect(issueUrl("Runner_Dashboard", 3176)).toBe(
      "https://github.com/D-sorganization/Runner_Dashboard/issues/3176",
    );
  });

  it("preserves owner/repo when already qualified", () => {
    expect(pullRequestUrl("org/other", 1)).toBe("https://github.com/org/other/pull/1");
  });

  it("builds a compare URL for a feature branch", () => {
    expect(branchCompareUrl("Runner_Dashboard", "cursor/demo-branch")).toBe(
      "https://github.com/D-sorganization/Runner_Dashboard/compare/main...cursor%2Fdemo-branch",
    );
  });

  it("builds blob URLs for codebase run references", () => {
    const url = codebaseReferenceGithubUrl({
      kind: "codebase",
      node: "hub-a",
      repo: "Runner_Dashboard",
      commit_sha: "a".repeat(40),
      relative_path: "backend/staff/store.py",
      line_start: 4,
      line_end: 8,
      content_hash: "b".repeat(64),
      citation: "[ref:…]",
    });
    expect(url).toBe(
      `https://github.com/D-sorganization/Runner_Dashboard/blob/${"a".repeat(40)}/backend/staff/store.py#L4-L8`,
    );
  });
});
