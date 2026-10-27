/**
 * Pure URL builders for staff run detail evidence links (#3176).
 * Uses fields already returned by GET /api/staff/runs/{id} — no extra API calls.
 */
import { GITHUB_ORG, issueUrl } from "../FleetCommand/fleetApi";
import type { RunRecord } from "./staffApi";

export interface EvidenceLink {
  href: string;
  label: string;
}

export interface RunEvidenceLinks {
  artifactLabel: string | null;
  issue: EvidenceLink | null;
  pullRequest: EvidenceLink | null;
  diff: EvidenceLink | null;
  reviewSession: EvidenceLink | null;
  logs: EvidenceLink | null;
}

const ARTIFACT_LABELS: Record<string, string> = {
  pr_open: "PR opened (waiting on CI)",
  pr_merged: "PR merged",
  deployed: "Deployed",
};

export function fullRepoSlug(repo: string, org: string = GITHUB_ORG): string {
  const trimmed = repo.trim();
  return trimmed.includes("/") ? trimmed : `${org}/${trimmed}`;
}

export function pullRequestUrl(repo: string, pr: number, org: string = GITHUB_ORG): string {
  return `https://github.com/${fullRepoSlug(repo, org)}/pull/${pr}`;
}

export function compareDiffUrl(
  repo: string,
  branch: string,
  base: string = "main",
  org: string = GITHUB_ORG,
): string {
  const slug = fullRepoSlug(repo, org);
  return `https://github.com/${slug}/compare/${base}...${branch}`;
}

function parsePositiveInt(ref: string): number | null {
  const n = Number.parseInt(ref.replace(/^#/, "").trim(), 10);
  return Number.isFinite(n) && n > 0 ? n : null;
}

/** Build external and in-app links for the run detail evidence row. */
export function buildRunEvidenceLinks(
  run: Pick<
    RunRecord,
    | "repo"
    | "branch"
    | "target_kind"
    | "target_ref"
    | "pr_number"
    | "artifact"
    | "remote_session_url"
  >,
  logsRef?: string | null,
): RunEvidenceLinks {
  const repo = (run.repo ?? "").trim();
  const artifact = (run.artifact ?? "").trim();
  const artifactLabel = artifact ? ARTIFACT_LABELS[artifact] ?? artifact : null;

  let issue: EvidenceLink | null = null;
  if (repo && run.target_kind === "issue") {
    const num = parsePositiveInt(run.target_ref ?? "");
    if (num) issue = { href: issueUrl(repo, num), label: `Issue #${num}` };
  }

  let pullRequest: EvidenceLink | null = null;
  if (repo && run.pr_number) {
    pullRequest = { href: pullRequestUrl(repo, run.pr_number), label: `PR #${run.pr_number}` };
  } else if (repo && run.target_kind === "pr") {
    const num = parsePositiveInt(run.target_ref ?? "");
    if (num) pullRequest = { href: pullRequestUrl(repo, num), label: `PR #${num}` };
  }

  const branch = (run.branch ?? "").trim();
  const diff =
    repo && branch ? { href: compareDiffUrl(repo, branch), label: `Diff (${branch} vs main)` } : null;

  const session = (run.remote_session_url ?? "").trim();
  const reviewSession = session ? { href: session, label: "Review session" } : null;

  const logsPath = (logsRef ?? "").trim();
  const logs = logsPath ? { href: logsPath, label: "Diagnostic logs" } : null;

  return { artifactLabel, issue, pullRequest, diff, reviewSession, logs };
}

export function hasRunEvidenceLinks(links: RunEvidenceLinks): boolean {
  return Boolean(
    links.artifactLabel ||
      links.issue ||
      links.pullRequest ||
      links.diff ||
      links.reviewSession ||
      links.logs,
  );
}
