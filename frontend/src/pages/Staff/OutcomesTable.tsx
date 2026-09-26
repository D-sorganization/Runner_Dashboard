/**
 * OutcomesTable.tsx — agent outcome scorecard (WP-1.2, #1517).
 *
 * Merge rate, verified rate, CI first-pass rate, fix-within-48h rate and cost per
 * merged PR for the last 14 days, grouped by role, provider or repo. A rate with no
 * data behind it shows "—", never 0%.
 */
import { useState } from "react";
import { useStaffOutcomes } from "../../hooks/useStaffQueries";
import { EmptyState } from "../../primitives/EmptyState";
import { TouchButton } from "../../primitives/TouchButton";
import { errorMessage, formatEffortUsd, formatRate, type OutcomeRow, type OutcomesGroupBy } from "./staffApi";

const GROUPS: readonly { id: OutcomesGroupBy; label: string }[] = [
  { id: "role", label: "By role" },
  { id: "provider", label: "By provider" },
  { id: "repo", label: "By repo" },
];

function Cells({ row }: { row: OutcomeRow }) {
  return (
    <>
      <td>{row.runs}</td>
      <td>{formatRate(row.verified_rate)}</td>
      <td>
        {row.prs}
        {row.prs_unknown ? ` (${row.prs_unknown} unread)` : ""}
      </td>
      <td>{row.merged}</td>
      <td>{formatRate(row.merge_rate)}</td>
      <td>{formatRate(row.ci_first_pass_rate)}</td>
      <td>{formatRate(row.fix_within_48h_rate)}</td>
      <td>{row.cost_per_merged_pr == null ? "—" : formatEffortUsd(row.cost_per_merged_pr)}</td>
      <td>{formatEffortUsd(row.cost_usd)}</td>
    </>
  );
}

export function OutcomesTable() {
  const [groupBy, setGroupBy] = useState<OutcomesGroupBy>("role");
  const { data, error, isLoading, refetch } = useStaffOutcomes(groupBy);

  return (
    <div className="glass-card staff-panel" data-testid="staff-outcomes">
      <div className="staff-panel__header">
        <h3 className="staff-panel__title">Outcomes (last 14 days)</h3>
        <div role="group" aria-label="Group scorecard by">
          {GROUPS.map((g) => (
            <TouchButton
              key={g.id}
              variant={groupBy === g.id ? "primary" : "default"}
              pressed={groupBy === g.id}
              onClick={() => setGroupBy(g.id)}
            >
              {g.label}
            </TouchButton>
          ))}
          <TouchButton onClick={() => void refetch()}>Refresh</TouchButton>
        </div>
      </div>
      {error ? (
        <EmptyState
          variant="error"
          title="Outcome scorecard unavailable"
          description={errorMessage(error)}
          onRetry={() => void refetch()}
        />
      ) : isLoading || !data ? (
        <p className="staff-muted">Loading the scorecard…</p>
      ) : data.rows.length === 0 ? (
        <EmptyState title="No staff runs" description="No staff run started on this node in the last 14 days." />
      ) : (
        <div className="staff-table-wrap">
          {data.prs_truncated ? (
            <p className="staff-muted" data-testid="outcomes-truncated">
              Only the newest PRs were read from GitHub; older ones count as unread.
            </p>
          ) : null}
          <table className="staff-table" data-testid="outcomes-table">
            <thead>
              <tr>
                <th>{GROUPS.find((g) => g.id === groupBy)?.label.replace("By ", "")}</th>
                <th>Runs</th>
                <th>Verified</th>
                <th>PRs</th>
                <th>Merged</th>
                <th>Merge rate</th>
                <th>CI first pass</th>
                <th>Fix ≤48h</th>
                <th>Cost / merged PR</th>
                <th>Cost</th>
              </tr>
            </thead>
            <tbody>
              {data.rows.map((row) => (
                <tr key={row.key}>
                  <th scope="row">{row.key || "(none)"}</th>
                  <Cells row={row} />
                </tr>
              ))}
            </tbody>
            <tfoot>
              <tr>
                <th scope="row">Totals</th>
                <Cells row={data.totals} />
              </tr>
            </tfoot>
          </table>
        </div>
      )}
    </div>
  );
}
