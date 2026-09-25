/**
 * Projects tab types (issue #1199) — mirrors `GET /api/projects` from
 * `backend/routers/projects.py`. Charter contract: Repository_Management
 * `Project_Template/docs/project/CHARTER.md` (see docs/projects.md).
 */

export type FeatureStatus = "planned" | "in-progress" | "shipped" | "parked";

export interface ProjectFeature {
  id: string;
  feature: string;
  status: FeatureStatus;
  tracking: string;
  notes: string;
}

export interface FeatureProgress {
  planned: number;
  in_progress: number;
  shipped: number;
  parked: number;
  percent_shipped: number;
}

/** Subset of the staff run record the card renders. */
export interface StewardRun {
  id: string;
  status: string;
  created_at: string;
  ended_at?: string | null;
  provider?: string;
  machine?: string;
}

/** Owner priority tier from Repository_Management config/project_priorities.yaml. */
export type PriorityTier = "P0" | "P1" | "P2" | "P3" | "P4" | "unranked";

export interface ProjectPriority {
  tier: PriorityTier;
  focus: string;
  rationale: string;
  decided: string;
}

/** An open issue/PR that no charter feature accounts for. */
export interface UntrackedItem {
  number: number;
  title: string;
  kind: "issue" | "pr";
  url: string;
  updated_at: string;
  labels: string[];
}

export interface ProjectCoverage {
  open_items: number;
  tracked: number;
  percent_tracked: number;
  untracked_count: number;
  untracked: UntrackedItem[];
}

export interface FleetSummary {
  repos: number;
  with_charter: number;
  without_charter: string[];
  features: Omit<FeatureProgress, "percent_shipped">;
  by_tier: Record<PriorityTier, number>;
  decisions_needed: number;
  untracked_items: number;
}

export interface ProjectOverview {
  repo: string;
  charter_present: boolean;
  features: ProjectFeature[];
  progress: FeatureProgress;
  status_present: boolean;
  decisions_needed: string[];
  last_steward_run: StewardRun | null;
  error?: string;
  /** Absent from older backends; `unranked` when the owner has not tiered the repo. */
  priority?: ProjectPriority;
  /** Null when the open-item fetch failed (reason in `coverage_error`). */
  coverage?: ProjectCoverage | null;
  coverage_error?: string;
}

export interface ProjectsResponse {
  projects: ProjectOverview[];
  count: number;
  cache_ttl_seconds: number;
  summary?: FleetSummary;
  priorities_error?: string;
}

export const STEWARD_RUN_URL = "/api/v1/staff/project-steward/run";
export const STEWARD_RUN_BODY = {
  prompt: "Scheduled steward pass",
  machine: "auto",
} as const;
