/** Delivery metrics — weekly trends and PR drill-down (#2122). */
import React from "react";
import { EmptyState } from "../../primitives/EmptyState";
import { TouchButton } from "../../primitives/TouchButton";
import { formatDuration, uniqueRepos } from "./deliveryMetricsApi";
import { useDeliveryMetrics } from "./useDeliveryMetrics";

export function DeliveryMetricsPanel(): React.ReactElement {
  const {
    state,
    repoFilter,
    setRepoFilter,
    refetch,
    pullDetail,
    pullLoading,
    pullError,
    selectPull,
    clearPull,
  } = useDeliveryMetrics();

  if (state.kind === "loading") {
    return (
      <p role="status" className="staff-muted" data-testid="delivery-metrics-loading">
        Loading delivery metrics…
      </p>
    );
  }
  if (state.kind === "disabled") {
    return (
      <EmptyState
        title="Delivery metrics are disabled"
        description="Enable DASHBOARD_ENABLE_DELIVERY_METRICS on this node to collect GitHub delivery facts."
        data-testid="delivery-metrics-disabled"
      />
    );
  }
  if (state.kind === "error") {
    return (
      <EmptyState
        variant="error"
        title="Delivery metrics unavailable"
        description={state.message}
        onRetry={refetch}
        data-testid="delivery-metrics-error"
      />
    );
  }

  const { data } = state;
  const repos = uniqueRepos(data.pr_drill_down);
  const rows = repoFilter ? data.pr_drill_down.filter((r) => r.repo_full === repoFilter) : data.pr_drill_down;

  return (
    <div data-testid="delivery-metrics-panel" style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      <p className="staff-muted" data-testid="delivery-metrics-posture">
        Weekly delivery trends — not leaderboards. Repo filter is live; role and model filters ship with the weekly
        summary (#2123).
      </p>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 8, alignItems: "center" }}>
        <label>
          Repository
          <select
            aria-label="Filter by repository"
            value={repoFilter ?? ""}
            onChange={(e) => setRepoFilter(e.target.value || null)}
            style={{ marginLeft: 8 }}
          >
            <option value="">All tracked repos</option>
            {repos.map((repo) => (
              <option key={repo} value={repo}>{repo}</option>
            ))}
          </select>
        </label>
        <TouchButton onClick={refetch}>Refresh</TouchButton>
      </div>
      <div className="stat-row">
        <div className="stat">
          <div className="stat-label">PRs</div>
          <div className="stat-value">{data.summary.prs}</div>
        </div>
        <div className="stat">
          <div className="stat-label">Merged</div>
          <div className="stat-value">{data.summary.merged}</div>
        </div>
        <div className="stat">
          <div className="stat-label">Median CI rounds</div>
          <div className="stat-value">
            {data.summary.median_ci_rounds == null ? "—" : data.summary.median_ci_rounds}
          </div>
        </div>
      </div>
      <div className="card">
        <h3 style={{ marginTop: 0 }}>Weekly trends</h3>
        {data.weekly_trends.length === 0 ? (
          <EmptyState title="No weekly buckets yet" description="No pull requests recorded for this window." />
        ) : (
          <table className="staff-table" data-testid="delivery-weekly-trends">
            <thead>
              <tr>
                <th>Week</th>
                <th>PRs</th>
                <th>Merged</th>
                <th>Median CI rounds</th>
              </tr>
            </thead>
            <tbody>
              {data.weekly_trends.map((row) => (
                <tr key={row.week_start}>
                  <th scope="row">{row.week_start}</th>
                  <td>{row.prs}</td>
                  <td>{row.merged}</td>
                  <td>{row.median_ci_rounds == null ? "—" : row.median_ci_rounds}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
      <div className="card">
        <h3 style={{ marginTop: 0 }}>Pull requests</h3>
        {rows.length === 0 ? (
          <EmptyState title="No pull requests" description="Clear the repo filter or wait for sync." />
        ) : (
          <table className="staff-table" data-testid="delivery-pr-list">
            <thead>
              <tr>
                <th>PR</th>
                <th>CI rounds</th>
                <th>Lead</th>
                <th>CI wait</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={`${row.repo_full}#${row.number}`}>
                  <th scope="row">
                    <a href={row.html_url} target="_blank" rel="noreferrer">
                      {row.repo_full.split("/").pop()}#{row.number}
                    </a>
                  </th>
                  <td>{row.ci_rounds_to_green ?? "—"}</td>
                  <td>{formatDuration(row.lead_time_seconds)}</td>
                  <td>{formatDuration(row.ci_wait_seconds)}</td>
                  <td>
                    <TouchButton onClick={() => void selectPull(row.repo_full, row.number)}>Checks</TouchButton>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
      {(pullDetail || pullLoading || pullError) && (
        <div className="card" data-testid="delivery-pull-detail">
          <div style={{ display: "flex", justifyContent: "space-between" }}>
            <h3 style={{ margin: 0 }}>
              {pullDetail ? pullDetail.pull.title : "Pull detail"}
            </h3>
            <TouchButton onClick={clearPull}>Close</TouchButton>
          </div>
          {pullLoading ? <p role="status">Loading check runs…</p> : null}
          {pullError ? <EmptyState variant="error" title="Pull detail failed" description={pullError} /> : null}
          {pullDetail ? (
            <ul>
              {pullDetail.check_runs.map((run) => (
                <li key={run.name}>
                  <a
                    href={run.details_url}
                    target="_blank"
                    rel="noreferrer"
                    title={`Open check run ${run.name} on GitHub`}
                    aria-label={`Open check run ${run.name} on GitHub`}
                  >
                    {run.name}
                  </a>
                  {" — "}
                  {run.conclusion ?? run.status}
                </li>
              ))}
            </ul>
          ) : null}
        </div>
      )}
    </div>
  );
}
