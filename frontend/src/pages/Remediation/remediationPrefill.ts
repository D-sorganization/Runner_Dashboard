/**
 * remediationPrefill.ts — Builds prefilled WorkRequest payloads and Staff Console URLs
 * for CI remediation context buttons (SC-G5-3, #1499).
 *
 * Implements "Fix this failed run" context prefill: captures run ID, repo, workflow name,
 * branch, log excerpt/failure reason, and chosen provider/model without feature loss
 * compared to legacy AgentDispatch.
 */
import type { WorkRequest } from "../Staff/staffApi";

export interface FailedRunTarget {
  id: number | string;
  repository?: { name: string; full_name?: string } | string;
  name?: string;
  workflow_name?: string;
  head_branch?: string;
  html_url?: string;
  conclusion?: string;
  failure_reason?: string;
  log_excerpt?: string;
}

export interface RemediationPrefillOptions {
  provider?: string | null;
  model?: string | null;
  machine?: string;
  logExcerpt?: string;
}

/**
 * Builds a canonical `WorkRequest` for kind `ci.remediate` from a failed run target.
 */
export function buildPrefilledRemediationRequest(
  run: FailedRunTarget,
  options?: RemediationPrefillOptions,
): WorkRequest {
  const repoName =
    typeof run.repository === "string"
      ? run.repository
      : run.repository?.name || "";
  const runId = typeof run.id === "number" ? run.id : Number(run.id) || null;
  const workflowName = run.workflow_name || run.name || "CI Workflow";
  const branch = run.head_branch || "main";
  const excerpt =
    options?.logExcerpt ||
    run.log_excerpt ||
    run.failure_reason ||
    "Run concluded with failure.";

  const prompt = `Fix failed run #${run.id} (${workflowName} on ${branch}): ${excerpt}`.trim();

  return {
    kind: "ci.remediate",
    role: null,
    provider: options?.provider || null,
    model: options?.model || null,
    machine: options?.machine || "local",
    prompt,
    dry_run: false,
    target: {
      repo: repoName,
      ref: branch || "",
      run_id: runId,
    },
  };
}

/**
 * Builds the canonical deep link URL to open the Staff Console Advanced form prefilled.
 */
export function buildPrefilledRemediationUrl(
  run: FailedRunTarget,
  options?: RemediationPrefillOptions,
): string {
  const req = buildPrefilledRemediationRequest(run, options);
  const p = new URLSearchParams();
  p.set("section", "assign");
  p.set("kind", req.kind);
  if (req.target?.repo) p.set("repo", req.target.repo);
  if (req.target?.run_id) p.set("run_id", String(req.target.run_id));
  if (req.provider) p.set("provider", req.provider);
  if (req.model) p.set("model", req.model);
  if (req.machine && req.machine !== "local") p.set("machine", req.machine);
  if (req.prompt) p.set("prompt", req.prompt);

  return `/?${p.toString()}`;
}
