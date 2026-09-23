/**
 * ActiveWorkPanel.tsx — who is working on what (#1233).
 *
 * `GET /api/coordination/sessions` returns board sessions (any agent) and
 * this fleet's staff runs in flight; both render as one table, filterable by
 * repository. Rows in the same repository that share an issue or overlapping
 * paths are highlighted as conflicts (advisory, like RM's own check). A
 * partial board read (`complete:false`) is shown as a warning: coordination
 * is degraded, the repository is not necessarily free.
 */
import { useEffect, useMemo, useState } from "react";
import { Badge } from "../../primitives/Badge";
import { TouchButton } from "../../primitives/TouchButton";
import { statusTone } from "../Staff/staffApi";
import {
  expiryLabel,
  fetchSessions,
  findConflicts,
  issueUrl,
  reposOf,
  staffRunHref,
  toWorkRows,
  useResource,
} from "./fleetApi";
import { PanelFrame } from "./PanelFrame";

export const ACTIVE_WORK_POLL_MS = 30_000;
const ALL = "";

export interface ActiveWorkPanelProps {
  /** Jump to the Messages panel addressed to a board session. */
  onMessage?: (session: string, repo: string) => void;
}

export function ActiveWorkPanel({ onMessage }: ActiveWorkPanelProps) {
  const res = useResource((signal) => fetchSessions(undefined, signal), "sessions");
  const [repo, setRepo] = useState(ALL);
  const { reload } = res;

  useEffect(() => {
    const id = window.setInterval(reload, ACTIVE_WORK_POLL_MS);
    return () => window.clearInterval(id);
  }, [reload]);

  const rows = useMemo(
    () => (res.data ? toWorkRows(res.data.sessions ?? [], res.data.staff_runs ?? []) : []),
    [res.data],
  );
  const conflicts = useMemo(() => findConflicts(rows), [rows]);
  const repos = useMemo(() => reposOf(rows), [rows]);
  const shown = repo === ALL ? rows : rows.filter((r) => r.repo.toLowerCase() === repo.toLowerCase());
  const conflictCount = shown.filter((r) => conflicts.has(r.key)).length;
  const warnings = res.data?.warnings ?? [];

  return (
    <PanelFrame
      title="Active work"
      testId="fleet-active-work"
      loading={res.loading}
      hasData={res.data !== null}
      error={res.error}
      unavailable={res.unavailable}
      onRetry={reload}
      actions={
        <>
          <select
            className="form-select"
            aria-label="Filter by repo"
            value={repo}
            onChange={(e) => setRepo(e.target.value)}
          >
            <option value={ALL}>All repos</option>
            {repos.map((r) => (
              <option key={r} value={r}>
                {r}
              </option>
            ))}
          </select>
          <TouchButton onClick={reload}>Refresh</TouchButton>
        </>
      }
    >
      <div className="fleet-cmd__meta">
        <span>{shown.length} in flight</span>
        {conflictCount > 0 ? (
          <Badge tone="danger" size="sm" data-testid="active-work-conflicts">
            {conflictCount} in conflict
          </Badge>
        ) : null}
        {res.data?.complete === false ? (
          <Badge tone="warning" size="sm">
            partial board read
          </Badge>
        ) : null}
      </div>
      {warnings.length > 0 ? (
        <details className="staff-muted" data-testid="active-work-warnings">
          <summary>
            {warnings.length} board {warnings.length === 1 ? "warning" : "warnings"}
          </summary>
          <ul className="fleet-cmd__list">
            {warnings.map((w) => (
              <li key={w}>{w}</li>
            ))}
          </ul>
        </details>
      ) : null}
      {shown.length === 0 ? (
        <p className="staff-muted" data-testid="active-work-empty">
          Nobody has registered work{repo ? ` in ${repo}` : ""}.
        </p>
      ) : (
        <div className="fleet-cmd__table-wrap">
          <table className="fleet-cmd__table" data-testid="active-work-table">
            <thead>
              <tr>
                <th scope="col">Who</th>
                <th scope="col">Repo</th>
                <th scope="col">Issue</th>
                <th scope="col">Branch / goals</th>
                <th scope="col">Where</th>
                <th scope="col">State</th>
                <th scope="col">
                  <span className="visually-hidden">Actions</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {shown.map((r) => {
                const reasons = conflicts.get(r.key);
                const id = r.key.slice(r.key.indexOf(":") + 1);
                return (
                  <tr key={r.key} className={reasons ? "fleet-cmd__conflict" : undefined} data-testid={`work-${r.key}`}>
                    <td>
                      <Badge tone={r.source === "staff" ? "info" : "neutral"} size="sm">
                        {r.source}
                      </Badge>{" "}
                      <strong>{r.who}</strong>
                      {reasons ? (
                        <ul className="fleet-cmd__reasons" data-testid={`conflict-${r.key}`}>
                          {reasons.map((reason) => (
                            <li key={reason}>{reason}</li>
                          ))}
                        </ul>
                      ) : null}
                    </td>
                    <td>{r.repo || "—"}</td>
                    <td>
                      {r.issue !== null && r.repo ? (
                        <a href={issueUrl(r.repo, r.issue)} target="_blank" rel="noreferrer noopener">
                          #{r.issue}
                        </a>
                      ) : (
                        "—"
                      )}
                    </td>
                    <td>
                      {r.branch ? <code>{r.branch}</code> : null}
                      {r.goals.length > 0 ? <div className="staff-muted">{r.goals.join(" · ")}</div> : null}
                      {!r.branch && r.goals.length === 0 ? "—" : null}
                    </td>
                    <td>{r.source === "staff" ? `${r.provider} @ ${r.machine}` : "—"}</td>
                    <td>
                      {r.source === "staff" ? (
                        <Badge tone={statusTone(r.status)} size="sm">
                          {r.status}
                        </Badge>
                      ) : (
                        <span className="staff-muted">{expiryLabel(r.expires, "expires") || "—"}</span>
                      )}
                    </td>
                    <td>
                      {r.source === "staff" ? (
                        <a href={staffRunHref(id)}>Open run</a>
                      ) : onMessage ? (
                        <TouchButton onClick={() => onMessage(id, r.repo)} aria-label={`Message ${id}`}>
                          Message
                        </TouchButton>
                      ) : null}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </PanelFrame>
  );
}

export default ActiveWorkPanel;
