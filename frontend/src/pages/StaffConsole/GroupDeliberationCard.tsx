/**
 * GroupDeliberationCard.tsx — one Board turn (SC-D7, #1342).
 *
 * Shows the coordinator's summary first, then each seat's reply in a
 * collapsed disclosure, with seats that did not answer marked. The proposal
 * action opens the Board Proposal form (#1284), prefilled with the seats'
 * replies. The form submits to `POST /api/proposals` and reports its own
 * success or error, so this card never claims a proposal exists.
 */
import { useState } from "react";
import { Badge } from "../../primitives/Badge";
import { TouchButton } from "../../primitives/TouchButton";
import { ProposalForm } from "../FleetCommand/ProposalForm";
import { consensusMarkdown, proposalPrefill, type GroupTurn, type SeatReply } from "./groupTurn";
import { ThreadMarkdown } from "./threadMarkdown";
import type { ThreadMessage } from "./threadTypes";
import "./groupTurn.css";

export interface GroupDeliberationCardProps {
  message: ThreadMessage;
  turn: GroupTurn;
}

function SeatRow({ seat }: { seat: SeatReply }) {
  return (
    <li className="group-turn__seat" data-testid={`seat-reply-${seat.seat}`}>
      <div className="group-turn__seat-head">
        <strong>{seat.seat}</strong>
        <Badge size="sm" tone={seat.answered ? "success" : "danger"}>
          {seat.answered ? "Answered" : "No response"}
        </Badge>
      </div>
      {seat.text && <p className="group-turn__seat-text">{seat.text}</p>}
      {!seat.answered && (
        <p className="group-turn__seat-error">{seat.errorDetail ? `${seat.status}: ${seat.errorDetail}` : seat.status}</p>
      )}
    </li>
  );
}

export function GroupDeliberationCard({ message, turn }: GroupDeliberationCardProps) {
  const [proposing, setProposing] = useState(false);
  const answered = turn.seats.filter((s) => s.answered).length;

  return (
    <article className="group-turn" aria-label="Board deliberation" data-testid={`group-deliberation-${message.id}`}>
      <header className="group-turn__head">
        {turn.quorum && <span className="group-turn__quorum">{turn.quorum}</span>}
        {turn.costUsd !== null && <span className="group-turn__cost">${turn.costUsd.toFixed(3)}</span>}
      </header>

      <section className="group-turn__consensus" aria-label="Coordinator summary">
        <ThreadMarkdown content={consensusMarkdown(message.body_md)} />
      </section>

      {turn.seats.length > 0 && (
        <details className="group-turn__seats">
          <summary>
            Seat replies ({answered} of {turn.seats.length} answered)
          </summary>
          <ul>
            {turn.seats.map((seat) => (
              <SeatRow key={seat.seat} seat={seat} />
            ))}
          </ul>
        </details>
      )}

      <div className="group-turn__actions">
        <TouchButton variant="default" aria-expanded={proposing} onClick={() => setProposing((open) => !open)}>
          {proposing ? "Hide proposal form" : "Turn into Board Proposal"}
        </TouchButton>
      </div>
      {proposing && <ProposalForm initialPrefill={proposalPrefill(turn)} />}
    </article>
  );
}
