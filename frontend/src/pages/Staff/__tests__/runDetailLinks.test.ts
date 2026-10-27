import { describe, expect, it } from "vitest";
import { buildRunEvidenceLinks, compareDiffUrl, hasRunEvidenceLinks, pullRequestUrl } from "../runDetailLinks";

describe("runDetailLinks (#3176)", () => {
  it("builds issue, PR, diff, review session, artifact, and logs links from run fields", () => {
    const links = buildRunEvidenceLinks(
      {
        repo: "Runner_Dashboard",
        branch: "cursor/demo-branch",
        target_kind: "issue",
        target_ref: "3176",
        pr_number: 9001,
        artifact: "pr_open",
        remote_session_url: "https://example.com/agents/abc",
      },
      "/api/v1/diagnostics/logs?run_id=run-1",
    );

    expect(links.artifactLabel).toBe("PR opened (waiting on CI)");
    expect(links.issue?.href).toBe("https://github.com/D-sorganization/Runner_Dashboard/issues/3176");
    expect(links.pullRequest?.href).toBe("https://github.com/D-sorganization/Runner_Dashboard/pull/9001");
    expect(links.diff?.href).toBe(
      "https://github.com/D-sorganization/Runner_Dashboard/compare/main...cursor/demo-branch",
    );
    expect(links.reviewSession?.href).toBe("https://example.com/agents/abc");
    expect(links.logs?.href).toBe("/api/v1/diagnostics/logs?run_id=run-1");
    expect(hasRunEvidenceLinks(links)).toBe(true);
  });

  it("returns empty links when the run has no evidence fields", () => {
    const links = buildRunEvidenceLinks({
      repo: "",
      branch: "",
      target_kind: "prompt",
      target_ref: "",
      pr_number: null,
      artifact: "",
      remote_session_url: "",
    });
    expect(hasRunEvidenceLinks(links)).toBe(false);
  });

  it("uses owner/repo slug when repo already includes the org", () => {
    expect(pullRequestUrl("OtherOrg/OtherRepo", 12)).toBe("https://github.com/OtherOrg/OtherRepo/pull/12");
    expect(compareDiffUrl("OtherOrg/OtherRepo", "feat/x")).toBe(
      "https://github.com/OtherOrg/OtherRepo/compare/main...feat/x",
    );
  });
});
