/**
 * FlowMetricsPanel — per-repo flow metrics (PD-4 #3818).
 *
 * Reads `GET /api/v1/fleet/flow-metrics` and surfaces open PR age, merge cycle
 * time, and weekly issue burn-down. PRs over the 72-hour rule are highlighted.
 */
import { Badge } from "../../primitives/Badge";
import { describeError, fetchFlowMetrics, useResource } from "./fleetApi";
import { PanelFrame } from "./PanelFrame";
import type { FlowMetricsRepoRow } from "./types";

function formatHours(hours: number): string {
  if (!Number.isFinite(hours) || hours <= 0) return "—";
  if (hours < 48) return `${Math.round(hours)}h`;
  return `${(hours / 24).toFixed(1)}d`;
}

function burnLabel(net: number): string {
  if (net > 0) return `+${net}`;
  return String(net);
}

function rowWarning(row: FlowMetricsRepoRow, ruleHours: number): boolean {
  return (row.open_pr_age?.over_rule_hours ?? 0) > 0 || (row.open_pr_age?.max_hours ?? 0) > ruleHours;
}

export function FlowMetricsPanel() {
  const res = useResource(fetchFlowMetrics, "flow-metrics");
  const data = res.data;
  const ruleHours = data?.pr_age_rule_hours ?? 72;
  const unavailable = data?.available === false ? data.reason ?? "Flow metrics unavailable" : null;

  return (
    <PanelFrame
      title="Flow metrics"
      testId="flow-metrics"
      loading={res.loading}
      hasData={Boolean(data?.repos?.length)}
      error={res.error ? describeError(res.error) : null}
      unavailable={unavailable}
      onRetry={res.reload}
    >
      {data?.repos?.length ? (
        <div className="fleet-cmd__table-wrap">
          <table className="fleet-cmd__table" data-testid="flow-metrics-table">
            <thead>
              <tr>
                <th scope="col">Repo</th>
                <th scope="col">Open PR age (p50 / max)</th>
                <th scope="col">&gt;72h</th>
                <th scope="col">Cycle time p50</th>
                <th scope="col">Weekly burn</th>
              </tr>
            </thead>
            <tbody>
              {data.repos.map((row) => (
                <tr key={row.repo} data-testid={`flow-metrics-row-${row.repo}`}>
                  <td>
                    {row.repo}
                    {row.error ? (
                      <span className="staff-muted" title={row.error}>
                        {" "}
                        (partial)
                      </span>
                    ) : null}
                  </td>
                  <td>
                    {formatHours(row.open_pr_age.p50_hours)} / {formatHours(row.open_pr_age.max_hours)}
                  </td>
                  <td>
                    {row.open_pr_age.over_rule_hours > 0 ? (
                      <Badge variant="warning" data-testid={`flow-aging-${row.repo}`}>
                        {row.open_pr_age.over_rule_hours}
                      </Badge>
                    ) : (
                      "0"
                    )}
                  </td>
                  <td>
                    {row.pr_cycle_time.sample_size > 0
                      ? formatHours(row.pr_cycle_time.p50_hours)
                      : "—"}
                  </td>
                  <td title={`opened ${row.weekly_issue_burn.opened}, closed ${row.weekly_issue_burn.closed}`}>
                    {burnLabel(row.weekly_issue_burn.net)}
                    {rowWarning(row, ruleHours) ? (
                      <span className="staff-muted"> · review aging PRs</span>
                    ) : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="staff-muted fleet-cmd__footnote">
            Weekly burn is closed minus opened issues in the last {data.repos[0]?.weekly_issue_burn.window_days ?? 7}{" "}
            days. PRs open longer than {ruleHours}h need CI diagnosis or an iterate-or-close decision (PD-4).
          </p>
        </div>
      ) : null}
    </PanelFrame>
  );
}
