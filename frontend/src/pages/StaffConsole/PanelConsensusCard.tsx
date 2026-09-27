/**
 * PanelConsensusCard.tsx — Synthesis & consensus card for expert panels (SC-D/Issue #1635).
 *
 * Renders the moderator's synthesis along with summary fields fetched from the backend:
 * whether consensus was reached, and how many rounds were used.
 */
import { useEffect, useState } from "react";
import { Badge } from "../../primitives/Badge";
import { fetchPanel, type PanelResult } from "./panelApi";
import { ThreadMarkdown } from "./threadMarkdown";
import type { ThreadMessage } from "./threadTypes";
import "./panel.css";

export interface PanelConsensusCardProps {
  message: ThreadMessage;
}

export function PanelConsensusCard({ message }: PanelConsensusCardProps) {
  const [panel, setPanel] = useState<PanelResult | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetchPanel(message.thread_id)
      .then((data) => {
        if (!cancelled) setPanel(data);
      })
      .catch(() => {
        // fail-open: show synthesis message body
      });
    return () => {
      cancelled = true;
    };
  }, [message.thread_id]);

  const consensus = panel?.consensus ?? false;
  const roundsUsed = panel?.rounds_used ?? null;
  const rounds = panel?.rounds ?? null;

  return (
    <article
      className="panel-consensus-card"
      role="article"
      aria-label="Panel consensus"
      data-testid={`panel-consensus-${message.id}`}
    >
      <header className="panel-consensus-card__head">
        <div className="panel-consensus-card__title">
          <strong>Panel Consensus</strong>
          {panel && (
            <Badge size="sm" tone={consensus ? "success" : "warning"}>
              {consensus ? "Consensus reached" : "No consensus reached"}
            </Badge>
          )}
        </div>
        {roundsUsed !== null && rounds !== null && (
          <span className="panel-consensus-card__rounds">
            {roundsUsed} of {rounds} rounds used
          </span>
        )}
      </header>

      <section className="panel-consensus-card__body" aria-label="Moderator synthesis">
        <ThreadMarkdown content={message.body_md} />
      </section>
    </article>
  );
}
