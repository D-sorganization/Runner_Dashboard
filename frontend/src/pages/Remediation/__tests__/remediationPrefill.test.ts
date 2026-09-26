/**
 * remediationPrefill.test.ts — Unit tests for building prefilled remediation work requests (SC-G5-3, #1499).
 *
 * Verifies:
 *  - the "Fix this failed run" context button builds a prefilled WorkRequest with kind "ci.remediate"
 *  - captures repo, run_id, workflow name, branch, and log excerpt in the prompt
 *  - preserves provider and model choice
 *  - builds canonical prefill query string / URL for the Staff Console Advanced form
 *  - feature checklist: covers every field AgentDispatch sent
 */
import { describe, expect, it } from "vitest";
import {
  buildPrefilledRemediationRequest,
  buildPrefilledRemediationUrl,
  type FailedRunTarget,
} from "../remediationPrefill";

describe("buildPrefilledRemediationRequest (#1499)", () => {
  const sampleRun: FailedRunTarget = {
    id: 987654,
    repository: { name: "Runner_Dashboard" },
    workflow_name: "CI Workflow",
    head_branch: "feat/my-branch",
    html_url: "https://github.com/D-sorganization/Runner_Dashboard/actions/runs/987654",
    failure_reason: "Tests failed on step 4",
    log_excerpt: "AssertionError: expected true to be false at test_runner.py:42",
  };

  it("builds a WorkRequest with kind 'ci.remediate' and target repo and run_id", () => {
    const req = buildPrefilledRemediationRequest(sampleRun);
    expect(req.kind).toBe("ci.remediate");
    expect(req.target).toEqual({
      repo: "Runner_Dashboard",
      ref: "feat/my-branch",
      run_id: 987654,
    });
    expect(req.machine).toBe("local");
  });

  it("includes run id, workflow name, branch, and log excerpt in the prompt", () => {
    const req = buildPrefilledRemediationRequest(sampleRun);
    expect(req.prompt).toContain("#987654");
    expect(req.prompt).toContain("CI Workflow");
    expect(req.prompt).toContain("feat/my-branch");
    expect(req.prompt).toContain("AssertionError: expected true to be false at test_runner.py:42");
  });

  it("handles string repository name and fallback workflow name and branch", () => {
    const minimalRun: FailedRunTarget = {
      id: "12345",
      repository: "UpstreamDrift",
      name: "Biomechanics Pipeline",
    };
    const req = buildPrefilledRemediationRequest(minimalRun);
    expect(req.target?.repo).toBe("UpstreamDrift");
    expect(req.target?.run_id).toBe(12345);
    expect(req.prompt).toContain("Biomechanics Pipeline");
    expect(req.prompt).toContain("main");
  });

  it("preserves provider and model options when supplied", () => {
    const req = buildPrefilledRemediationRequest(sampleRun, {
      provider: "claude_code_cli",
      model: "claude-3-5-sonnet",
    });
    expect(req.provider).toBe("claude_code_cli");
    expect(req.model).toBe("claude-3-5-sonnet");
  });

  it("covers every field AgentDispatch sent (no feature loss)", () => {
    const agentDispatchFields = {
      repository: "Runner_Dashboard",
      workflow_name: "CI Workflow",
      branch: "feat/my-branch",
      failure_reason: "Dispatching claude_code_cli for failed run #987654",
      log_excerpt: "Run 987654 concluded with failure. Dispatched via mobile agent dispatch flow.",
      run_id: 987654,
      provider: "claude_code_cli",
      model: "claude-3-5-sonnet",
      dispatch_origin: "manual",
    };

    const req = buildPrefilledRemediationRequest(
      {
        id: agentDispatchFields.run_id,
        repository: { name: agentDispatchFields.repository },
        workflow_name: agentDispatchFields.workflow_name,
        head_branch: agentDispatchFields.branch,
        failure_reason: agentDispatchFields.failure_reason,
        log_excerpt: agentDispatchFields.log_excerpt,
      },
      {
        provider: agentDispatchFields.provider,
        model: agentDispatchFields.model,
      },
    );

    // 1. repository
    expect(req.target?.repo).toBe(agentDispatchFields.repository);
    // 2. run_id
    expect(req.target?.run_id).toBe(agentDispatchFields.run_id);
    // 3. workflow_name
    expect(req.prompt).toContain(agentDispatchFields.workflow_name);
    // 4. branch
    expect(req.prompt).toContain(agentDispatchFields.branch);
    // 5. failure_reason / log_excerpt
    expect(req.prompt).toContain(agentDispatchFields.log_excerpt);
    // 6. provider
    expect(req.provider).toBe(agentDispatchFields.provider);
    // 7. model
    expect(req.model).toBe(agentDispatchFields.model);
    // 8. kind / origin
    expect(req.kind).toBe("ci.remediate");
  });

  it("builds the prefilled navigation URL for Staff Console / Advanced form", () => {
    const url = buildPrefilledRemediationUrl(sampleRun, {
      provider: "jules_api",
      model: "default",
    });
    expect(url).toContain("/?section=assign");
    expect(url).toContain("kind=ci.remediate");
    expect(url).toContain("repo=Runner_Dashboard");
    expect(url).toContain("run_id=987654");
    expect(url).toContain("provider=jules_api");
    expect(url).toContain("model=default");
    expect(decodeURIComponent(url.replace(/\+/g, " "))).toContain("CI Workflow");
  });
});
