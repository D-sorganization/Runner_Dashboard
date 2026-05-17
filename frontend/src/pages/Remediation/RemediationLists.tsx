import { Badge } from "../../primitives/Badge";
import { InFlightTile } from "./InFlightTile";
import type {
  ActionSheetItem,
  AgentProvider,
  FailedRun,
  InFlightDispatch,
  OpenIssue,
  OpenPR,
} from "./mobileTypes";
import { getProviderLabel } from "./mobileTypes";

interface CommonProps {
  inFlightDispatches: InFlightDispatch[];
  providers: Record<string, AgentProvider>;
  recommendedProviderId: string;
  onSelect: (item: ActionSheetItem) => void;
}

const CARD_STYLE: React.CSSProperties = {
  background: "var(--bg-secondary)",
  border: "1px solid var(--border)",
  borderRadius: 10,
  cursor: "pointer",
  display: "block",
  marginBottom: 10,
  padding: "12px 14px",
  textAlign: "left",
  width: "100%",
};

const EMPTY_STYLE: React.CSSProperties = {
  color: "var(--text-muted)",
  padding: "32px 0",
  textAlign: "center",
};

interface AutomationsListProps extends CommonProps {
  failedRuns: FailedRun[];
}

export function AutomationsList({
  inFlightDispatches,
  failedRuns,
  providers,
  recommendedProviderId,
  onSelect,
}: AutomationsListProps) {
  const inflight = inFlightDispatches.filter((d) => d.status !== "done");
  return (
    <>
      {inflight.map((d) => (
        <InFlightTile key={d.id} dispatch={d} />
      ))}
      {failedRuns.length === 0 ? (
        <div
          aria-label="No failed runs"
          className="remediation-empty"
          style={EMPTY_STYLE}
        >
          No failed runs found.
        </div>
      ) : (
        failedRuns.map((run) => {
          const repoName = run.repository?.name ?? "repo";
          const isInflight = inFlightDispatches.some(
            (d) => d.itemId === run.id,
          );
          if (isInflight) return null;
          return (
            <button
              key={run.id}
              aria-label={`Failed run: ${run.name ?? run.workflow_name} in ${repoName}`}
              className="remediation-card"
              onClick={() =>
                onSelect({
                  id: run.id,
                  title: `${repoName}: ${run.name ?? run.workflow_name}`,
                  htmlUrl: run.html_url,
                  repository: repoName,
                  workflowName: run.workflow_name ?? run.name,
                  branch: run.head_branch ?? "main",
                  runId: run.id,
                })
              }
              style={CARD_STYLE}
              type="button"
            >
              <div
                style={{
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "center",
                  marginBottom: 6,
                }}
              >
                <span
                  style={{
                    color: "var(--text-primary)",
                    fontWeight: 600,
                    fontSize: 13,
                  }}
                >
                  {repoName}: {run.name ?? run.workflow_name}
                </span>
                <Badge tone="danger" size="sm">
                  failure
                </Badge>
              </div>
              <div
                style={{
                  color: "var(--text-secondary)",
                  fontSize: 12,
                  marginBottom: 4,
                }}
              >
                {run.head_branch ?? "main"} · #{run.run_number ?? run.id}
              </div>
              <Badge tone="info" size="sm">
                Recommended: {getProviderLabel(providers, recommendedProviderId)}
              </Badge>
            </button>
          );
        })
      )}
    </>
  );
}

interface PRsListProps extends CommonProps {
  openPRs: OpenPR[];
}

export function PRsList({
  inFlightDispatches,
  openPRs,
  providers,
  recommendedProviderId,
  onSelect,
}: PRsListProps) {
  return (
    <>
      {openPRs.length === 0 ? (
        <div
          aria-label="No open PRs"
          className="remediation-empty"
          style={EMPTY_STYLE}
        >
          No open PRs found.
        </div>
      ) : (
        openPRs.map((pr) => {
          const repoName = pr.base?.repo?.name ?? "repo";
          const isInflight = inFlightDispatches.some((d) => d.itemId === pr.id);
          if (isInflight) return null;
          return (
            <button
              key={pr.id}
              aria-label={`Open PR: ${pr.title} in ${repoName}`}
              className="remediation-card"
              onClick={() =>
                onSelect({
                  id: pr.id,
                  title: `PR #${pr.number}: ${pr.title}`,
                  htmlUrl: pr.html_url,
                  repository: repoName,
                  workflowName: "pr-remediation",
                  branch: pr.head?.ref ?? "main",
                })
              }
              style={CARD_STYLE}
              type="button"
            >
              <div
                style={{
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "center",
                  marginBottom: 6,
                }}
              >
                <span
                  style={{
                    color: "var(--text-primary)",
                    fontWeight: 600,
                    fontSize: 13,
                  }}
                >
                  PR #{pr.number}: {pr.title}
                </span>
                {pr.draft && (
                  <Badge tone="neutral" size="sm">
                    Draft
                  </Badge>
                )}
              </div>
              <div
                style={{
                  color: "var(--text-secondary)",
                  fontSize: 12,
                  marginBottom: 4,
                }}
              >
                {repoName} · {pr.head?.ref ?? "unknown branch"}
              </div>
              <Badge tone="info" size="sm">
                Recommended: {getProviderLabel(providers, recommendedProviderId)}
              </Badge>
            </button>
          );
        })
      )}
    </>
  );
}

interface IssuesListProps extends CommonProps {
  openIssues: OpenIssue[];
}

export function IssuesList({
  inFlightDispatches,
  openIssues,
  providers,
  recommendedProviderId,
  onSelect,
}: IssuesListProps) {
  return (
    <>
      {openIssues.length === 0 ? (
        <div
          aria-label="No open issues"
          className="remediation-empty"
          style={EMPTY_STYLE}
        >
          No open issues found.
        </div>
      ) : (
        openIssues.map((issue) => {
          const repoName = issue.repository_url?.split("/").pop() ?? "repo";
          const isInflight = inFlightDispatches.some(
            (d) => d.itemId === issue.id,
          );
          if (isInflight) return null;
          return (
            <button
              key={issue.id}
              aria-label={`Open issue: ${issue.title}`}
              className="remediation-card"
              onClick={() =>
                onSelect({
                  id: issue.id,
                  title: `Issue #${issue.number}: ${issue.title}`,
                  htmlUrl: issue.html_url,
                  repository: repoName,
                  workflowName: "issue-remediation",
                  branch: "main",
                })
              }
              style={CARD_STYLE}
              type="button"
            >
              <div
                style={{
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "center",
                  marginBottom: 6,
                }}
              >
                <span
                  style={{
                    color: "var(--text-primary)",
                    fontWeight: 600,
                    fontSize: 13,
                  }}
                >
                  #{issue.number}: {issue.title}
                </span>
              </div>
              <div
                style={{
                  color: "var(--text-secondary)",
                  fontSize: 12,
                  marginBottom: 4,
                }}
              >
                {repoName}
                {issue.labels.length > 0 && (
                  <> {issue.labels.map((l) => l.name).join(", ")}</>
                )}
              </div>
              <Badge tone="info" size="sm">
                Recommended: {getProviderLabel(providers, recommendedProviderId)}
              </Badge>
            </button>
          );
        })
      )}
    </>
  );
}
