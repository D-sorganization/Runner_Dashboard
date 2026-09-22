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

export interface ProjectOverview {
  repo: string;
  charter_present: boolean;
  features: ProjectFeature[];
  progress: FeatureProgress;
  status_present: boolean;
  decisions_needed: string[];
  last_steward_run: StewardRun | null;
  error?: string;
}

export interface ProjectsResponse {
  projects: ProjectOverview[];
  count: number;
  cache_ttl_seconds: number;
}

export const STEWARD_RUN_URL = "/api/staff/project-steward/run";
export const STEWARD_RUN_BODY = {
  prompt: "Scheduled steward pass",
  machine: "auto",
} as const;
