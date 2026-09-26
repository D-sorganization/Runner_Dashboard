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

  return (
    <section className="glass-card staff-board" aria-label="Staff board">
      <div className="staff-board__header">
        <h3 className="staff-board__title">Board</h3>
        {board ? (
          <span style={{ marginLeft: "auto", marginRight: 8 }}>
            <RefreshBadge staleness={staleness} onRetry={() => refetch()} />
          </span>
        ) : null}
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
      <PlanQuota />
      {!board && !error ? <p className="staff-muted">Loading board...</p> : null}
      {board && livenessAlerts(board).length > 0 ? (
        <ul className="staff-board__alerts" data-testid="board-liveness-alerts" aria-label="Liveness alerts">
          {livenessAlerts(board).map((row) => (
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
          {groupByMachine(board).map((row) => (
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
    </section>
  );
}

export default Board;
