/**
 * requestKinds.ts — the work-request kinds the dispatch form offers (SC-G5-2, #1498).
 *
 * Mirrors `staff.work_requests.REQUEST_KINDS`: a kind is listed only once the backend
 * accepts it, so the form never offers a request that is bound to be rejected.
 */

/** A numeric target field a request kind may take. */
export type TargetField = "issue" | "pr" | "run_id";

export interface RequestKindSpec {
  id: string;
  label: string;
  fields: readonly TargetField[];
}

/** The request kinds the backend accepts (`staff.work_requests.REQUEST_KINDS`). */
export const REQUEST_KINDS: readonly RequestKindSpec[] = [
  { id: "staff.dispatch", label: "Staff run (staff.dispatch)", fields: ["issue", "pr"] },
  { id: "ci.remediate", label: "CI Remediation (ci.remediate)", fields: ["run_id"] },
  { id: "issue.act", label: "Issue action (issue.act)", fields: ["issue"] },
  { id: "pr.act", label: "PR action (pr.act)", fields: ["pr"] },
  { id: "code_request.dispatch", label: "Code Request (code_request.dispatch)", fields: [] },
  { id: "assessment.run", label: "Assessment (assessment.run)", fields: [] },
];

export const FIELD_LABELS: Record<TargetField, string> = { issue: "Issue #", pr: "PR #", run_id: "Run ID" };
