import { Badge } from "../../primitives/Badge";
import type { KanbanCard } from "./types";

function primaryIssueUrl(card: KanbanCard): string | null {
  const issue = card.sources.find((s) => s.kind === "issue" && s.url);
  return issue?.url ?? null;
}

export function KanbanCardView({ card }: { card: KanbanCard }) {
  const issueUrl = primaryIssueUrl(card);
  const title = `${card.repo}#${card.issue_number}`;
  const hasStale = card.stale_reviews.length > 0;
  const isUnknown = card.column === "unknown";

  return (
    <article
      className="kanban-card"
      data-testid={`kanban-card-${card.issue_number}`}
      data-column={card.column}
    >
      <header className="kanban-card__head">
        {issueUrl ? (
          <a className="kanban-card__title" href={issueUrl} rel="noopener noreferrer" target="_blank">
            {title}
          </a>
        ) : (
          <span className="kanban-card__title">{title}</span>
        )}
        <span className="kanban-card__badges">
          {isUnknown ? (
            <Badge tone="warning" size="sm">
              Unknown
            </Badge>
          ) : null}
          {hasStale ? (
            <Badge tone="danger" size="sm">
              Stale review
            </Badge>
          ) : null}
        </span>
      </header>
      {card.reasons.length > 0 && isUnknown ? (
        <p className="kanban-card__reasons">{card.reasons.join(" · ")}</p>
      ) : null}
      <ul className="kanban-card__sources" aria-label="Source links">
        {card.sources
          .filter((s) => s.url)
          .map((source) => (
            <li key={`${source.kind}-${source.url}`}>
              <a
                className={source.available ? "kanban-card__source" : "kanban-card__source kanban-card__source--muted"}
                href={source.url}
                rel="noopener noreferrer"
                target="_blank"
              >
                {source.label || source.kind}
              </a>
            </li>
          ))}
      </ul>
    </article>
  );
}
