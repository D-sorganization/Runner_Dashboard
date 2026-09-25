import React from "react";
import { Badge } from "../../primitives/Badge";
import { EmptyState } from "../../primitives/EmptyState";
import { PanelFrame } from "./PanelFrame";
import type { ProposalItem } from "./types";

interface OpenProposalsProps {
  proposals: ProposalItem[];
  loading: boolean;
  hasData: boolean;
  error: string | null;
  onRetry: () => void;
}

interface DecidedProposalsProps {
  proposals: ProposalItem[];
  loading: boolean;
  hasData: boolean;
  error: string | null;
  onRetry: () => void;
}

export function OutcomeBadge({
  decision,
}: {
  decision: string | null | undefined;
}): React.ReactElement {
  const d = (decision || "decided").toLowerCase();
  if (d === "accepted") return <Badge tone="success">accepted</Badge>;
  if (d === "declined") return <Badge tone="danger">declined</Badge>;
  if (d === "deferred") return <Badge tone="neutral">deferred</Badge>;
  return <Badge tone="neutral">{d}</Badge>;
}

export function OpenProposalsSection({
  proposals,
  loading,
  hasData,
  error,
  onRetry,
}: OpenProposalsProps): React.ReactElement {
  return (
    <PanelFrame
      title={`Open Proposals (${proposals.length})`}
      testId="open-proposals-frame"
      loading={loading}
      hasData={hasData}
      error={error}
      onRetry={onRetry}
    >
      {proposals.length === 0 ? (
        <EmptyState
          title="No open proposals"
          description="There are currently no open suggestions awaiting Board review."
        />
      ) : (
        <div className="fleet-cmd__table-wrap">
          <table className="fleet-cmd__table" data-testid="proposals-open-table">
            <thead>
              <tr>
                <th scope="col">#</th>
                <th scope="col">Title</th>
                <th scope="col">Repos</th>
                <th scope="col">Source</th>
                <th scope="col">Urgency</th>
              </tr>
            </thead>
            <tbody>
              {proposals.map((p) => (
                <tr key={p.number}>
                  <td>
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
                    {p.lean ? (
                      <div className="staff-muted" style={{ fontSize: 12 }}>
                        Lean: {p.lean}
                      </div>
                    ) : null}
                  </td>
                  <td>
                    {(p.target_repos || []).map((r) => (
                      <Badge key={r} tone="neutral" size="sm">
                        {r}
                      </Badge>
                    ))}
                  </td>
                  <td>
                    <span className="staff-muted">{p.source}</span>
                  </td>
                  <td>
                    <Badge
                      tone={
                        p.urgency === "critical" || p.urgency === "high"
                          ? "warning"
                          : "neutral"
                      }
                      size="sm"
                    >
                      {p.urgency}
                    </Badge>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </PanelFrame>
  );
}

export function DecidedProposalsSection({
  proposals,
  loading,
  hasData,
  error,
  onRetry,
}: DecidedProposalsProps): React.ReactElement {
  return (
    <PanelFrame
      title={`Decided Proposals (${proposals.length})`}
      testId="decided-proposals-frame"
      loading={loading}
      hasData={hasData}
      error={error}
      onRetry={onRetry}
    >
      {proposals.length === 0 ? (
        <EmptyState
          title="No decided proposals yet"
          description="Proposals decided by the Board will appear here with outcome badges and meeting links."
        />
      ) : (
        <div className="fleet-cmd__table-wrap">
          <table
            className="fleet-cmd__table"
            data-testid="proposals-decided-table"
          >
            <thead>
              <tr>
                <th scope="col">#</th>
                <th scope="col">Title</th>
                <th scope="col">Outcome</th>
                <th scope="col">Meeting Decision</th>
              </tr>
            </thead>
            <tbody>
              {proposals.map((p) => (
                <tr key={p.number}>
                  <td>
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
                      {(p.target_repos || []).join(", ")}
                    </div>
                  </td>
                  <td>
                    <OutcomeBadge decision={p.decision} />
                  </td>
                  <td>
                    {p.meeting_date ? (
                      p.consensus_url ? (
                        <a
                          href={p.consensus_url}
                          target="_blank"
                          rel="noreferrer noopener"
                          style={{ fontWeight: 600 }}
                        >
                          Meeting {p.meeting_date}
                        </a>
                      ) : (
                        <span>Meeting {p.meeting_date}</span>
                      )
                    ) : (
                      <span className="staff-muted">—</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </PanelFrame>
  );
}
