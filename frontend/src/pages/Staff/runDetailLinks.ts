import type { components } from "../../lib/api-types";
import { GITHUB_ORG } from "../FleetCommand/fleetApi";

type CodebaseRunReference = components["schemas"]["CodebaseRunReference"];

export function repoFullName(repo: string): string {
  return repo.includes("/") ? repo : `${GITHUB_ORG}/${repo}`;
}

export function pullRequestUrl(repo: string, pr: number): string {
  return `https://github.com/${repoFullName(repo)}/pull/${pr}`;
}

export function pullRequestFilesUrl(repo: string, pr: number): string {
  return `https://github.com/${repoFullName(repo)}/pull/${pr}/files`;
}

export function branchCompareUrl(repo: string, branch: string, base = "main"): string {
  const full = repoFullName(repo);
  return `https://github.com/${full}/compare/${base}...${encodeURIComponent(branch)}`;
}

export function issueUrl(repo: string, number: string | number): string {
  return `https://github.com/${repoFullName(repo)}/issues/${number}`;
}

export function codebaseReferenceGithubUrl(ref: CodebaseRunReference): string {
  const fragment =
    ref.line_start === ref.line_end ? `#L${ref.line_start}` : `#L${ref.line_start}-L${ref.line_end}`;
  return `https://github.com/${repoFullName(ref.repo)}/blob/${ref.commit_sha}/${ref.relative_path}${fragment}`;
}
