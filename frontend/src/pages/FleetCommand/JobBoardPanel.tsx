/** Job board panel (#2786): lists claimable work from GET /api/coordination/jobs. */
import { useEffect, useMemo, useState } from "react";
import { probeDashboardSession, type DashboardPrincipal } from "../../hooks/useSession";
import { Badge } from "../../primitives/Badge";
import { TouchButton } from "../../primitives/TouchButton";
import {
  describeError,
  fetchJobs,
  issueUrl,
  jobAgeLabel,
  jobMatchesPriority,
  operatorSession,
  postClaim,
  releaseClaim,
  useResource,
} from "./fleetApi";
import { PanelFrame } from "./PanelFrame";
import type { JobBoardJob } from "./types";

const ALL = "";
const PRIORITY_OPTIONS = ["critical", "high", "medium", "low", "unranked"];

function isOperatorPrincipal(principal: DashboardPrincipal | undefined): boolean {
  if (!principal?.scopes?.includes("coordination.write")) return false;
  if (principal.type === "human") return true;
  return (principal.roles ?? []).some((r) => r === "operator" || r === "admin");
}

export function JobBoardPanel() {
  const [principal, setPrincipal] = useState<DashboardPrincipal | null>(null);
  useEffect(() => {
    void probeDashboardSession().then((r) => setPrincipal(r.loggedIn ? r.principal : null));
  }, []);
  const canWrite = isOperatorPrincipal(principal ?? undefined);
  const [repoFilter, setRepoFilter] = useState(ALL);
  const [priorityFilter, setPriorityFilter] = useState(ALL);
  const [showExcluded, setShowExcluded] = useState(false);
  const [session] = useState(operatorSession);
  const [busyKey, setBusyKey] = useState<string | null>(null);
  const [rowErrors, setRowErrors] = useState<Record<string, string>>({});

  const res = useResource(
    (signal) => fetchJobs({ repo: repoFilter || undefined, include_excluded: showExcluded }, signal),
    `${repoFilter}|${showExcluded}`,
  );
  const jobs = res.data?.jobs ?? [];
  const repos = useMemo(() => Array.from(new Set(jobs.map((j) => j.repo))).sort(), [jobs]);
  const filteredJobs = useMemo(
    () => jobs.filter((j) => jobMatchesPriority(j, priorityFilter)),
    [jobs, priorityFilter],
  );
  const degraded =
    Boolean(res.error) || res.data?.available === false || res.data?.complete === false;
  const degradedText =
    res.error ??
    (res.data?.available === false
      ? res.data.reason || res.unavailable || "Job board unavailable — list may be incomplete."
      : res.data?.complete === false
        ? "Partial job board read — some repositories may be missing."
        : null);

  const mutate = (kind: "claim" | "release", job: JobBoardJob) => {
    const key = `${job.repo}-${job.issue}`;
    setBusyKey(key);
    setRowErrors((prev) => {
      const next = { ...prev };
      delete next[key];
      return next;
    });
    const call =
      kind === "claim"
        ? postClaim({ repo: job.repo, issue: job.issue, session, intent: "implement" })
        : releaseClaim({ repo: job.repo, issue: job.issue, session, reason: "work completed" });
    call
      .then(() => res.reload())
      .catch((e: unknown) => setRowErrors((prev) => ({ ...prev, [key]: describeError(e) })))
      .finally(() => setBusyKey(null));
  };

  return (
    <PanelFrame
      title="Job board"
      testId="fleet-job-board"
      loading={res.loading}
      hasData={res.data !== null}
      error={res.error}
      unavailable={res.data?.available === false ? null : res.unavailable}
      onRetry={res.reload}
      actions={
        <>
          <select className="form-select" aria-label="Filter jobs by repo" value={repoFilter} onChange={(e) => setRepoFilter(e.target.value)}>
            <option value={ALL}>All repos</option>
            {repos.map((r) => (
              <option key={r} value={r}>{r}</option>
            ))}
          </select>
          <select className="form-select" aria-label="Filter jobs by priority" value={priorityFilter} onChange={(e) => setPriorityFilter(e.target.value)}>
            <option value={ALL}>All priorities</option>
            {PRIORITY_OPTIONS.map((p) => (
              <option key={p} value={p}>{p}</option>
            ))}
          </select>
          <label className="fleet-cmd__check">
            <input type="checkbox" checked={showExcluded} onChange={(e) => setShowExcluded(e.target.checked)} /> Show excluded
          </label>
          <TouchButton onClick={res.reload}>Refresh</TouchButton>
        </>
      }
    >
      {degraded && degradedText ? (
        <p className="staff-error" role="alert" data-testid="job-board-degraded">
          {degradedText}
          {res.data?.warnings?.length ? <span className="staff-muted"> — {res.data.warnings.join("; ")}</span> : null}
        </p>
      ) : null}
      {filteredJobs.length === 0 && !res.loading && !degraded ? (
        <p className="staff-muted" data-testid="job-board-empty-all-clear">No claimable jobs match these filters.</p>
      ) : null}
      {filteredJobs.length > 0 ? (
        <div className="fleet-cmd__table-wrap">
          <table className="fleet-cmd__table" data-testid="job-board-table">
            <thead>
              <tr>
                <th scope="col">Repo</th>
                <th scope="col">Issue</th>
                <th scope="col">Title</th>
                <th scope="col">Priority</th>
                <th scope="col">Labels</th>
                <th scope="col">Age</th>
                {canWrite ? <th scope="col"><span className="visually-hidden">Claim actions</span></th> : null}
              </tr>
            </thead>
            <tbody>
              {filteredJobs.map((job) => {
                const key = `${job.repo}-${job.issue}`;
                const rowErr = rowErrors[key];
                return (
                  <tr key={key} data-testid={`job-row-${job.repo}-${job.issue}`}>
                    <td>{job.repo}</td>
                    <td>
                      <a href={job.url || issueUrl(job.repo, job.issue)} target="_blank" rel="noreferrer">#{job.issue}</a>
                    </td>
                    <td>{job.title}</td>
                    <td>
                      <Badge tone={job.priority ? "warning" : "neutral"} size="sm">
                        {job.priority?.trim() || "unranked"}
                      </Badge>
                    </td>
                    <td>{(job.labels ?? []).slice(0, 4).join(", ")}</td>
                    <td>{jobAgeLabel(job.updated_at)}</td>
                    {canWrite ? (
                      <td>
                        <div className="fleet-cmd__row">
                          <TouchButton disabled={busyKey !== null} aria-label={`Claim ${job.repo}#${job.issue}`} onClick={() => mutate("claim", job)}>
                            {busyKey === key ? "…" : "Claim"}
                          </TouchButton>
                          <TouchButton variant="danger" disabled={busyKey !== null} aria-label={`Release ${job.repo}#${job.issue}`} onClick={() => mutate("release", job)}>
                            Release
                          </TouchButton>
                        </div>
                        {rowErr ? (
                          <p className="staff-error" data-testid={`job-claim-error-${job.repo}-${job.issue}`}>{rowErr}</p>
                        ) : null}
                      </td>
                    ) : null}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : null}
      {showExcluded && (res.data?.excluded?.length ?? 0) > 0 ? (
        <ul className="fleet-cmd__list staff-muted" data-testid="job-board-excluded">
          {res.data!.excluded!.map((row) => (
            <li key={`${row.repo}-${row.issue}`} data-testid={`job-excluded-${row.repo}-${row.issue}`}>
              {row.repo}#{row.issue}: {row.reason}
            </li>
          ))}
        </ul>
      ) : null}
    </PanelFrame>
  );
}

export default JobBoardPanel;
