/**
 * Board.tsx — the status monitor panel at the top of the Staff tab (#1198).
 *
 * Polls `GET /api/staff/board` every 10 s and shows running / queued runs
 * per machine plus spend today. Orthogonal to the other panels: a board
 * failure renders an inline notice and never blocks Roster/Runs/Assign.
 */
import { useCallback, useEffect, useState } from "react";
import { Badge } from "../../primitives/Badge";
import { TimeAgo } from "../../primitives/TimeAgo";
import {
  BOARD_POLL_MS,
  errorMessage,
  fetchBoard,
  formatUsd,
  groupByMachine,
  statusTone,
  type BoardResponse,
} from "./staffApi";

export interface BoardProps {
  onOpenRun?: (id: string) => void;
}

export function Board({ onOpenRun }: BoardProps) {
  const [board, setBoard] = useState<BoardResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback((signal?: AbortSignal) => {
    fetchBoard(signal)
      .then((data) => {
        setBoard(data);
        setError(null);
      })
      .catch((e: unknown) => {
        if (signal?.aborted) return;
        setError(errorMessage(e));
      });
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    load(controller.signal);
    const timer = window.setInterval(() => load(controller.signal), BOARD_POLL_MS);
    return () => {
      controller.abort();
      window.clearInterval(timer);
    };
  }, [load]);

  return (
    <section className="glass-card staff-board" aria-label="Staff board">
      <div className="staff-board__header">
        <h3 className="staff-board__title">Board</h3>
        {board ? (
          <span className="staff-board__meta">
            spend today <strong data-testid="board-spend">{formatUsd(board.spend_today_usd)}</strong>
            {" · "}
            <TimeAgo iso={board.generated_at} live />
          </span>
        ) : null}
      </div>
      {error ? <p className="staff-muted">Board unavailable: {error}</p> : null}
      {!board && !error ? <p className="staff-muted">Loading board...</p> : null}
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
