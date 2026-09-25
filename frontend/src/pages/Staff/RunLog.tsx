/**
 * RunLog.tsx — run history from `GET /api/staff/runs` (#1198).
 *
 * Role and status filters map 1:1 onto the API query parameters. Clicking a
 * row opens RunDetail (owned by StaffPage so the Board can deep-link too).
 */
import { useEffect, useState } from "react";
import { Badge } from "../../primitives/Badge";
import { EmptyState } from "../../primitives/EmptyState";
import { TimeAgo } from "../../primitives/TimeAgo";
import { TouchButton } from "../../primitives/TouchButton";
import { useStaffRuns } from "../../hooks/useStaffQueries";
import {
  errorMessage,
  formatUsd,
  RUN_STATUSES,
  statusTone,
  targetLabel,
} from "./staffApi";

export interface RunLogProps {
  /** Role names for the filter select (from the roster). */
  roles: string[];
  onOpenRun: (id: string) => void;
  /** Bumped by the parent to force a reload (e.g. after a dispatch). */
  refreshKey?: number;
}

export function RunLog({ roles, onOpenRun, refreshKey = 0 }: RunLogProps) {
  const [role, setRole] = useState("");
  const [status, setStatus] = useState("");

  const {
    data: runsData,
    error: runsErr,
    isLoading,
    refetch,
  } = useStaffRuns({
    role: role || undefined,
    status: status || undefined,
    limit: 100,
  });

  const runs = runsData?.runs ?? (isLoading ? null : []);
  const error = runsErr ? errorMessage(runsErr) : null;

  useEffect(() => {
    if (refreshKey > 0) {
      refetch();
    }
  }, [refreshKey, refetch]);

  return (
    <div className="glass-card staff-panel">
      <div className="staff-panel__header">
        <h3 className="staff-panel__title">Runs</h3>
        <div className="staff-filters">
          <label className="form-label" htmlFor="staff-runs-role">
            Role
          </label>
          <select
            id="staff-runs-role"
            className="form-select"
            value={role}
            onChange={(e) => setRole(e.target.value)}
          >
            <option value="">all</option>
            {roles.map((name) => (
              <option key={name} value={name}>
                {name}
              </option>
            ))}
          </select>
          <label className="form-label" htmlFor="staff-runs-status">
            Status
          </label>
          <select
            id="staff-runs-status"
            className="form-select"
            value={status}
            onChange={(e) => setStatus(e.target.value)}
          >
            <option value="">all</option>
            {RUN_STATUSES.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
          <TouchButton onClick={() => refetch()}>Refresh</TouchButton>
        </div>
      </div>

      {error ? (
        <EmptyState variant="error" title="Failed to load runs" description={error} onRetry={() => refetch()} />
      ) : runs === null ? (
        <p className="staff-muted">Loading runs...</p>
      ) : runs.length === 0 ? (
        <EmptyState title="No runs yet" description="Dispatch a role from the Assign tab to see it here." />
      ) : (
        <div className="staff-table-wrap">
          <table className="staff-table" data-testid="staff-runs">
            <thead>
              <tr>
                <th>Status</th>
                <th>Role</th>
                <th>Provider</th>
                <th>Repo</th>
                <th>Target</th>
                <th>Machine</th>
                <th>Created</th>
                <th>Cost</th>
                <th>Outcome</th>
                <th>Last line</th>
              </tr>
            </thead>
            <tbody>
              {runs.map((run) => (
                <tr
                  key={run.id}
                  className="staff-table__row"
                  data-testid={`run-row-${run.id}`}
                  tabIndex={0}
                  role="button"
                  aria-label={`Open run ${run.id}`}
                  onClick={() => onOpenRun(run.id)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault();
                      onOpenRun(run.id);
                    }
                  }}
                >
                  <td>
                    <Badge tone={statusTone(run.status)} size="sm">
                      {run.status}
                    </Badge>
                  </td>
                  <td>{run.role}</td>
                  <td>{run.provider}</td>
                  <td>{run.repo || "—"}</td>
                  <td>{targetLabel(run)}</td>
                  <td>{run.machine}</td>
                  <td>
                    <TimeAgo iso={run.created_at} />
                  </td>
                  <td>{formatUsd(run.cost_usd)}</td>
                  <td data-testid={`run-outcome-${run.id}`}>{run.outcome || "—"}</td>
                  <td className="staff-table__last-line" title={run.last_line}>
                    {run.error || run.last_line || ""}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

export default RunLog;
