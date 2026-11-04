/** Client for C1 shared context (#2862) — conflict badges only (#2865). */

import { apiRequest } from "../../lib/api";
import type { ActivityEnvelope } from "./activityApi";

export type ConflictSummary = {
  conflict_id: string;
  kind: "scope_conflict" | "claim_conflict";
  repo: string;
  issue: number | null;
  sessions: string[];
  agents: string[];
  branches: string[];
  paths: string[];
  goals: string[];
  detected_at: string;
};

export type ActivityContextResponse = {
  conflicts: ConflictSummary[];
};

export function fetchActivityContext(repo?: string, signal?: AbortSignal): Promise<ActivityContextResponse> {
  const query = repo ? `?repo=${encodeURIComponent(repo)}` : "";
  return apiRequest<ActivityContextResponse>("/api/v1/activity/context" + query, { signal });
}

/** Map C1 conflicts to per-event badge labels (C1 output only). */
export function conflictLabelsForEvent(
  event: ActivityEnvelope,
  conflicts: readonly ConflictSummary[],
): string[] {
  const labels = new Set<string>();
  for (const c of conflicts) {
    const sessionHit = c.sessions.includes(event.session_id);
    const scopeHit =
      c.repo === event.repo &&
      c.issue != null &&
      event.issue != null &&
      c.issue === event.issue;
    if (!sessionHit && !scopeHit) continue;
    labels.add(c.kind === "claim_conflict" ? "claim conflict" : "scope conflict");
  }
  return [...labels];
}
