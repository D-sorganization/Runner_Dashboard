/**
 * Board.tsx — the status monitor panel at the top of the Staff tab (#1198).
 *
 * Polls `GET /api/staff/board` every 10 s and shows running / queued runs
 * per machine plus spend today, and a warning list of scheduled roles that
 * are late or dead (#1209). Orthogonal to the other panels: a board failure
 * renders an inline notice and never blocks Roster/Runs/Assign.
 */
import { Badge } from "../../primitives/Badge";
import { RefreshBadge } from "../../primitives/RefreshBadge";
import { TimeAgo } from "../../primitives/TimeAgo";
import { Tooltip } from "../../primitives/Tooltip";
import { useStalenessWarning } from "../../hooks/useStalenessWarning";
import { useStaffBoard } from "../../hooks/useStaffQueries";
import { PlanQuota } from "./PlanQuota";
import {
  errorMessage,
  formatEffortUsd,
  formatSpendSummary,
  groupByMachine,
  livenessAlerts,
  statusTone,
} from "./staffApi";
import "./Board.css";

export interface BoardProps {
  onOpenRun?: (id: string) => void;
}

export function Board({ onOpenRun }: BoardProps) {
  const {
    data: board = null,
    error: boardErr,
    dataUpdatedAt,
    errorUpdatedAt,
    failureCount,
    isFetching,
    refetch,
  } = useStaffBoard();
  const error = boardErr ? errorMessage(boardErr) : null;

  const staleness = useStalenessWarning(
    {
      dataUpdatedAt,
      errorUpdatedAt,
      failureCount,
      isFetching,
    },
    15_000,
  );

  const spendSummary = board ? formatSpendSummary(board.spend_today_usd) : null;
  const alerts = board ? livenessAlerts(board) : [];
  const machines = board ? groupByMachine(board) : [];
  const runningCount = machines.reduce((n, m) => n + m.running.length, 0);
  const queuedCount = machines.reduce((n, m) => n + m.queued.length, 0);

  return (
    <section className="staff-board" aria-label="Staff board">
      <div className="staff-board__header">
        <div className="staff-board__title-wrap">
          <span
            className={`staff-board__live-dot ${error || staleness.state === "error" ? "staff-board__live-dot--danger" : staleness.state === "stale" ? "staff-board__live-dot--warning" : "staff-board__live-dot--live"}`}
            data-testid="board-live-dot"
            aria-hidden="true"
          />
          <h3 className="staff-board__title">Board</h3>
          {board ? (
            <span className="staff-board__summary" data-testid="board-summary">
              <Badge tone={runningCount > 0 ? "info" : "neutral"} size="sm">
                {runningCount} running
              </Badge>
              <Badge tone={queuedCount > 0 ? "warning" : "neutral"} size="sm">
                {queuedCount} queued
              </Badge>
              {alerts.length > 0 ? (
                <Badge tone={alerts.some((a) => a.status === "dead") ? "danger" : "warning"} size="sm">
                  {alerts.length} late
                </Badge>
              ) : null}
            </span>
          ) : null}
          {board ? (
            <RefreshBadge staleness={staleness} onRetry={() => refetch()} />
          ) : null}
        </div>
        {board && spendSummary ? (
          <span className="staff-board__meta">
            spend today{" "}
            {spendSummary.breakdown ? (
              <Tooltip content={spendSummary.breakdown} placement="bottom">
                <strong
                  data-testid="board-spend"
                  title={spendSummary.breakdown}
                  tabIndex={0}
                  style={{ cursor: "help" }}
                >
                  {formatEffortUsd(spendSummary.total)}
                </strong>
              </Tooltip>
            ) : (
              <strong data-testid="board-spend">
                {formatEffortUsd(spendSummary.total)}
              </strong>
            )}
            {" · "}
            <TimeAgo iso={board.generated_at} live />
          </span>
        ) : null}
      </div>
      {error ? <p className="staff-muted">Board unavailable: {error}</p> : null}
      {!board && !error ? <p className="staff-muted">Loading board...</p> : null}
      {/* Compact by default so the console stays above the fold (#1722);
          quotas, late roles and per-machine runs sit behind one disclosure. */}
      <details className="staff-board__details" data-testid="board-details">
        <summary className="staff-board__details-summary">Quotas, schedules and machines</summary>
        <PlanQuota />
        {board && alerts.length > 0 ? (
          <ul className="staff-board__alerts" data-testid="board-liveness-alerts" aria-label="Liveness alerts">
            {alerts.map((row) => (
              <li key={`${row.machine ?? board.machine}:${row.role}`}>
                <Badge tone={row.status === "dead" ? "danger" : "warning"} size="sm">
                  {row.status}
                </Badge>{" "}
                {row.role} on {row.machine ?? board.machine} · last success{" "}
                {row.last_success ? <TimeAgo iso={row.last_success} /> : "never"}
              </li>
            ))}
          </ul>
        ) : null}
        {board ? (
          <div className="staff-board__machines">
            {machines.map((row) => (
              <div key={row.machine} className="staff-board__machine" data-testid={`board-machine-${row.machine}`}>
                <div className="staff-board__machine-name">
                  {row.machine}
                  <Badge tone={row.running.length > 0 ? "info" : "neutral"} size="sm">
                    {row.running.length} running
                  </Badge>
                  <Badge tone={row.queued.length > 0 ? "warning" : "neutral"} size="sm">
                    {row.queued.length} queued
                  </Badge>
                </div>
                <ul className="staff-board__runs">
                  {[...row.running, ...row.queued].map((run) => (
                    <li key={run.id}>
                      <button type="button" className="staff-link" onClick={() => onOpenRun?.(run.id)}>
                        <Badge tone={statusTone(run.status)} size="sm">
                          {run.status}
                        </Badge>{" "}
                        {run.role} · {run.provider}
                        {run.repo ? ` · ${run.repo}` : ""}
                        {run.target_ref ? ` ${run.target_ref}` : ""}
                      </button>
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </div>
        ) : null}
      </details>
    </section>
  );
}

export default Board;
