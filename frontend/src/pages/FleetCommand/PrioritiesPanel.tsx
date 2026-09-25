/**
 * PrioritiesPanel.tsx — board-meeting priorities (#1233).
 *
 * `GET /api/priorities` gives the newest meeting's consensus; the history
 * selector (`GET /api/priorities/meetings`) swaps in any dated meeting via
 * `GET /api/priorities/meetings/{date}`. Active priorities link their
 * tracking reference to GitHub; deferred items and disagreement flags follow.
 * `available:false` here means "no board meeting yet" (or no RM checkout),
 * which is an empty state with the backend's reason, not an error.
 */
import { useState } from "react";
import { Badge } from "../../primitives/Badge";
import { EmptyState } from "../../primitives/EmptyState";
import { TouchButton } from "../../primitives/TouchButton";
import {
  fetchMeeting,
  fetchMeetings,
  fetchPriorities,
  fetchProposals,
  trackingUrl,
  useResource,
} from "./fleetApi";
import { PanelFrame } from "./PanelFrame";
import { renderOutcomeBadge } from "./ProposalLists";
import type { Consensus, ProposalItem } from "./types";

const LATEST = "";

function ConsensusView({
  consensus,
  meetingProposals,
}: {
  consensus: Consensus;
  meetingProposals?: ProposalItem[];
}) {
  return (
    <>
      <h4 className="fleet-cmd__subtitle">Active priorities</h4>
      {consensus.active.length === 0 ? (
        <p className="staff-muted">The consensus lists no active priorities.</p>
      ) : (
        <div className="fleet-cmd__table-wrap">
          <table className="fleet-cmd__table" data-testid="priorities-active">
            <thead>
              <tr>
                <th scope="col">#</th>
                <th scope="col">Item</th>
                <th scope="col">Project</th>
                <th scope="col">Assigned to</th>
                <th scope="col">Tracking</th>
              </tr>
            </thead>
            <tbody>
              {consensus.active.map((p) => {
                const url = p.tracking ? trackingUrl(p.tracking, p.project) : null;
                return (
                  <tr key={`${p.rank}-${p.item}`}>
                    <td className="fleet-cmd__rank">{p.rank}</td>
                    <td>
                      <strong>{p.item}</strong>
                      {p.scope ? <div className="staff-muted">{p.scope}</div> : null}
                    </td>
                    <td>{p.project || "—"}</td>
                    <td>{p.assigned_to || "—"}</td>
                    <td>
                      {url ? (
                        <a href={url} target="_blank" rel="noreferrer noopener">
                          {p.tracking}
                        </a>
                      ) : (
                        p.tracking || "—"
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      {consensus.deferred.length > 0 ? (
        <details className="fleet-cmd__details">
          <summary>Deferred backlog ({consensus.deferred.length})</summary>
          <ul className="fleet-cmd__list" data-testid="priorities-deferred">
            {consensus.deferred.map((d) => (
              <li key={`${d.item}-${d.project}`}>
                <strong>{d.item}</strong>
                {d.project ? ` · ${d.project}` : ""}
                {d.reason ? <span className="staff-muted"> — {d.reason}</span> : null}
                {d.reassess ? <span className="staff-muted"> (reassess {d.reassess})</span> : null}
              </li>
            ))}
          </ul>
        </details>
      ) : null}
      {consensus.disagreements.length > 0 ? (
        <div className="fleet-cmd__flags" data-testid="priorities-disagreements">
          <Badge tone="warning" size="sm">
            {consensus.disagreements.length} disagreement flag{consensus.disagreements.length === 1 ? "" : "s"}
          </Badge>
          <ul className="fleet-cmd__list">
            {consensus.disagreements.map((flag) => (
              <li key={flag}>{flag}</li>
            ))}
          </ul>
        </div>
      ) : null}
      {meetingProposals && meetingProposals.length > 0 ? (
        <div style={{ marginTop: 16 }}>
          <h4 className="fleet-cmd__subtitle">
            Proposals decided at this meeting ({meetingProposals.length})
          </h4>
          <div className="fleet-cmd__table-wrap">
            <table className="fleet-cmd__table" data-testid="priorities-decided-proposals">
              <thead>
                <tr>
                  <th scope="col">#</th>
                  <th scope="col">Proposal</th>
                  <th scope="col">Decision</th>
                </tr>
              </thead>
              <tbody>
                {meetingProposals.map((p) => (
                  <tr key={p.number}>
                    <td className="fleet-cmd__rank">
                      <a
                        href={p.html_url || "#"}
                        target="_blank"
                        rel="noreferrer noopener"
                        style={{ fontWeight: 600 }}
                      >
                        #{p.number}
                      </a>
                    </td>
                    <td>
                      <strong>{p.title}</strong>
                      <div className="staff-muted" style={{ fontSize: 12 }}>
                        {p.target_repos.join(", ")}
                      </div>
                    </td>
                    <td>{renderOutcomeBadge(p.decision)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      ) : null}
    </>
  );
}

export function PrioritiesPanel() {
  const [date, setDate] = useState(LATEST);
  const latest = useResource(date === LATEST ? fetchPriorities : null, `latest:${date}`);
  const meetings = useResource(fetchMeetings, "meetings");
  const meeting = useResource(date === LATEST ? null : (signal) => fetchMeeting(date, signal), `meeting:${date}`);
  const decidedRes = useResource(
    (signal) => fetchProposals({ state: "decided" }, signal),
    "priorities:proposals:decided",
  );

  const current = date === LATEST ? latest : meeting;
  // A 404 (route absent) is "not available"; `available:false` is the no-meeting empty state.
  const routeMissing = current.data === null ? current.unavailable : null;
  const consensus = date === LATEST ? (latest.data?.board ?? null) : (meeting.data?.consensus ?? null);
  const boardDate = date === LATEST ? latest.data?.board?.date : date;
  const history = meetings.data?.meetings ?? [];
  const meetingProposals = (decidedRes.data?.proposals ?? []).filter(
    (p) => boardDate && p.meeting_date === boardDate,
  );

  return (
    <PanelFrame
      title="Priorities"
      testId="fleet-priorities"
      loading={current.loading}
      hasData={current.data !== null}
      error={current.error}
      unavailable={routeMissing}
      onRetry={() => {
        current.reload();
        decidedRes.reload();
      }}
      actions={
        <>
          {history.length > 0 ? (
            <select
              className="form-select"
              aria-label="Board meeting"
              value={date}
              onChange={(e) => setDate(e.target.value)}
            >
              <option value={LATEST}>Latest meeting</option>
              {history.map((m) => (
                <option key={m.date} value={m.date}>
                  {m.date}
                  {m.has_consensus ? "" : " (no consensus)"}
                </option>
              ))}
            </select>
          ) : null}
          <TouchButton
            onClick={() => {
              current.reload();
              decidedRes.reload();
            }}
          >
            Refresh
          </TouchButton>
        </>
      }
    >
      {boardDate ? (
        <p className="fleet-cmd__meta" data-testid="priorities-date">
          Board meeting <strong>{boardDate}</strong>
        </p>
      ) : null}
      {consensus ? (
        <ConsensusView consensus={consensus} meetingProposals={meetingProposals} />
      ) : (
        <EmptyState
          title={date === LATEST ? "No board meeting yet" : "No consensus for this meeting"}
          description={
            current.data?.reason || "Nothing has been decided at a board meeting yet; operator directives still apply."
          }
          data-testid="priorities-empty"
        />
      )}
    </PanelFrame>
  );
}

export default PrioritiesPanel;
