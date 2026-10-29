/**
 * activityApi.ts — typed client for the fleet activity log (`/api/v1/activity/*`,
 * epic #2856). Mirrors `backend/routers/activity.py`'s contract.
 *
 * No SSE endpoint exists yet for raw events (that is #2867 T1b, the dedicated
 * transcript-streaming slice) — callers poll `fetchSessionEvents` on an
 * interval and merge by `event_id`, the same "live" convention already used
 * by `ActiveWorkPanel`/`AgentBusPage`.
 */
import { apiRequest } from "../../lib/api";
import type { components } from "../../lib/api-types";

export type SessionSummary = components["schemas"]["SessionSummary"];
export type SessionsPage = components["schemas"]["SessionsPage"];
export type ActivityEnvelope = components["schemas"]["ActivityEnvelope"];
export type ActivityPage = components["schemas"]["ActivityPage"];

const SESSIONS_LIMIT = 500;
const EVENTS_LIMIT = 200;

/** Status display order for grouped session lists (live fleet work first). */
export const STATUS_ORDER: readonly SessionSummary["status"][] = [
  "live",
  "idle",
  "stale",
  "ended",
  "uncaptured",
];

/** The three gap record kinds (#2856): never imply an unpersisted chunk was accepted. */
const GAP_KINDS = new Set(["OverflowGap", "RetentionGap", "CaptureGap"]);

export function isGapEvent(event: ActivityEnvelope): boolean {
  return GAP_KINDS.has(event.kind);
}

/** Stable identity for a session: `(source, session_id)` per #2860. */
export function sessionKeyOf(session: Pick<SessionSummary, "source" | "session_id">): string {
  return `${session.source}:${session.session_id}`;
}

export function fetchSessions(signal?: AbortSignal): Promise<SessionsPage> {
  return apiRequest<SessionsPage>(`/api/v1/activity/sessions?limit=${SESSIONS_LIMIT}`, { signal });
}

export function fetchSessionEvents(sessionId: string, signal?: AbortSignal): Promise<ActivityPage> {
  return apiRequest<ActivityPage>(
    `/api/v1/activity?session_id=${encodeURIComponent(sessionId)}&limit=${EVENTS_LIMIT}`,
    { signal },
  );
}

/** Group sessions by node, then by status in {@link STATUS_ORDER}, for the left panel. */
export function groupSessions(items: SessionSummary[]): Map<string, Map<string, SessionSummary[]>> {
  const byNode = new Map<string, Map<string, SessionSummary[]>>();
  for (const item of items) {
    let byStatus = byNode.get(item.node);
    if (!byStatus) {
      byStatus = new Map();
      byNode.set(item.node, byStatus);
    }
    const bucket = byStatus.get(item.status);
    if (bucket) bucket.push(item);
    else byStatus.set(item.status, [item]);
  }
  const orderedNodes = new Map<string, Map<string, SessionSummary[]>>();
  for (const node of [...byNode.keys()].sort()) {
    const byStatus = byNode.get(node);
    if (!byStatus) continue;
    const ordered = new Map<string, SessionSummary[]>();
    for (const status of STATUS_ORDER) {
      const bucket = byStatus.get(status);
      if (bucket) ordered.set(status, bucket);
    }
    orderedNodes.set(node, ordered);
  }
  return orderedNodes;
}

/** Sort events for scrollback display: chronological, ties broken by `seq`. */
export function sortEvents(events: Iterable<ActivityEnvelope>): ActivityEnvelope[] {
  return [...events].sort((a, b) => a.ts.localeCompare(b.ts) || a.seq - b.seq);
}
