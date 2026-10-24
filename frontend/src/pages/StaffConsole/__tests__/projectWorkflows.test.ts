import { describe, expect, it } from "vitest";
import { PROJECT_WORKFLOWS, repositoryScopedProjectPrompt } from "../projectWorkflows";

describe("repositoryScopedProjectPrompt", () => {
  it("names the repository and asks for issues, PRs, and a test-first next step", () => {
    const prompt = repositoryScopedProjectPrompt("UpstreamDrift");
    expect(prompt).toContain("UpstreamDrift repository");
    expect(prompt).toContain("review open issues and pull requests");
    expect(prompt).toContain("test-first next step");
    expect(prompt).toContain("wait for my approval before dispatching");
  });

  it("keeps Barb sidebar and staff console starters aligned", () => {
    const upstream = PROJECT_WORKFLOWS.find((row) => row.project === "UpstreamDrift");
    const affine = PROJECT_WORKFLOWS.find((row) => row.project === "AffineDrift");
    expect(upstream?.prompt).toBe(repositoryScopedProjectPrompt("UpstreamDrift"));
    expect(affine?.prompt).toBe(repositoryScopedProjectPrompt("AffineDrift"));
  });
});
