import { useMemo, useState } from "react";
import { SegmentedControl } from "../../primitives/SegmentedControl";
import { KANBAN_COLUMNS } from "./columns";
import { KanbanCardView } from "./KanbanCardView";
import type { KanbanBoardItem, KanbanColumnId, KanbanGithubBoardResponse } from "./types";

function groupByColumn(items: KanbanBoardItem[]): Map<KanbanColumnId, KanbanBoardItem[]> {
  const map = new Map<KanbanColumnId, KanbanBoardItem[]>();
  for (const col of KANBAN_COLUMNS) {
    map.set(col.id, []);
  }
  for (const row of items) {
    const id = row.card.column;
    const bucket = map.get(id) ?? [];
    bucket.push(row);
    map.set(id, bucket);
  }
  for (const [, bucket] of map) {
    bucket.sort((a, b) => a.issue_number - b.issue_number);
  }
  return map;
}

export function KanbanBoard({ board }: { board: KanbanGithubBoardResponse }) {
  const grouped = useMemo(() => groupByColumn(board.items), [board.items]);
  const [mobileColumn, setMobileColumn] = useState<KanbanColumnId>("issue_open");

  const mobileOptions = KANBAN_COLUMNS.map((col) => ({
    label: `${col.label} (${grouped.get(col.id)?.length ?? 0})`,
    value: col.id,
  }));

  return (
    <div className="kanban-board">
      <div className="kanban-board__mobile" data-testid="kanban-mobile-column">
        <SegmentedControl
          ariaLabel="Kanban column"
          options={mobileOptions}
          value={mobileColumn}
          onChange={(v) => setMobileColumn(v as KanbanColumnId)}
        />
        <div className="kanban-board__mobile-cards">
          {(grouped.get(mobileColumn) ?? []).map((row) => (
            <KanbanCardView key={row.issue_number} card={row.card} />
          ))}
        </div>
      </div>
      <div className="kanban-board__columns" role="list" aria-label="Kanban columns">
        {KANBAN_COLUMNS.map((col) => {
          const cards = grouped.get(col.id) ?? [];
          return (
            <section
              key={col.id}
              className="kanban-column"
              role="listitem"
              aria-label={`${col.label}, ${cards.length} cards`}
              data-testid={`kanban-column-${col.id}`}
            >
              <header className="kanban-column__head">
                <h3 className="kanban-column__title">{col.label}</h3>
                <span className="kanban-column__count">{cards.length}</span>
              </header>
              <div className="kanban-column__cards">
                {cards.map((row) => (
                  <KanbanCardView key={row.issue_number} card={row.card} />
                ))}
              </div>
            </section>
          );
        })}
      </div>
    </div>
  );
}
