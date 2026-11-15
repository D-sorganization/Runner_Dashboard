/** Kanban board API contract (epic #4317, GET /api/kanban/github). */

export type KanbanColumnId =
  | "issue_open"
  | "claimed"
  | "pr_open"
  | "review"
  | "armed"
  | "merged"
  | "waiting_on_dieter"
  | "unknown";

export interface KanbanSourceLink {
  kind: string;
  label: string;
  url: string;
  available: boolean;
}

export interface KanbanStaleReview {
  commit_id: string;
  state: string;
  author: string;
  html_url: string;
}

export interface KanbanCard {
  owner: string;
  repo: string;
  issue_number: number;
  column: KanbanColumnId;
  column_label: string;
  matched_columns: KanbanColumnId[];
  stale_reviews: KanbanStaleReview[];
  reasons: string[];
  sources: KanbanSourceLink[];
}

export interface KanbanBoardItem {
  issue_number: number;
  snapshot: Record<string, unknown>;
  card: KanbanCard;
}

export interface KanbanSourceFreshness {
  status: string;
  synced_at: string;
  detail: string | null;
}

export interface KanbanGithubBoardResponse {
  owner: string;
  repo: string;
  last_sync: string;
  cache_ttl_seconds: number;
  sync_mode: "cold" | "warm";
  metrics: {
    github_api_calls: number;
    cache_hits: number;
    cache_misses: number;
    api_budget_limit: number;
    api_budget_exhausted: boolean;
  };
  freshness: Record<string, KanbanSourceFreshness>;
  items: KanbanBoardItem[];
  /** Present on demo payloads until ingestion (#4319) is deployed. */
  fixture?: boolean;
}

export interface KanbanColumnDef {
  id: KanbanColumnId;
  label: string;
}
