import type { KanbanColumnDef, KanbanColumnId } from "./types";

/** Workflow order from epic #4317 (left-to-right on the board). */
export const KANBAN_COLUMNS: readonly KanbanColumnDef[] = [
  { id: "issue_open", label: "Issue open" },
  { id: "claimed", label: "Claimed" },
  { id: "pr_open", label: "PR open" },
  { id: "review", label: "Review" },
  { id: "armed", label: "Armed" },
  { id: "merged", label: "Merged" },
  { id: "waiting_on_dieter", label: "Waiting on Dieter" },
  { id: "unknown", label: "Unknown" },
] as const;

export function columnLabel(id: KanbanColumnId): string {
  return KANBAN_COLUMNS.find((c) => c.id === id)?.label ?? id;
}
