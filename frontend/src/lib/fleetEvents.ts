/**
 * fleetEvents — client model for the fleet event log + alarm center (issue #863).
 *
 * Mirrors the backend `/api/events` feed (backend/fleet_events.py). Pure,
 * dependency-free helpers so the EventLog primitive and AlarmPanel stay
 * orthogonal to the polling/React layer:
 *
 *   - `FleetEvent` / `EventSeverity` / `EventKind` — the typed record.
 *   - `mergeEvents` — append-only, de-duplicated merge of freshly-fetched
 *     events into the retained history, newest-first, bounded.
 *   - `loadEvents` / `saveEvents` — durable persistence across reloads via
 *     localStorage (best-effort; degrades to a no-op when unavailable).
 *   - `filterBySeverity` — the severity filter the EventLog UI applies.
 *   - `eventsToAlerts` — folds the *active* offline/disk events into the
 *     AlertsCenter `FleetAlert` model so the header pill surfaces
 *     "runner(s) offline — disk pressure" without a pop-up.
 *
 * Contract: every exported function is pure except the storage helpers, which
 * are best-effort and never throw to the caller.
 */

import type { AlertLevel, FleetAlert } from "./fleetAlerts";
import { alertContentHash } from "./fleetAlerts";

export type EventSeverity = "info" | "warning" | "critical";
export type EventKind =
  | "runner_offline"
  | "runner_online"
  | "low_disk"
  | "saturation"
  | "watchdog";

export interface FleetEvent {
  /** Epoch milliseconds. */
  ts: number;
  severity: EventSeverity;
  kind: EventKind;
  title: string;
  detail: string;
  /** Originating node, when the event is node-scoped. */
  node?: string;
}

/** Shape of the `/api/events` response body. */
export interface EventsResponse {
  events: FleetEvent[];
  count: number;
  capacity: number;
}

/** Max events retained client-side (matches the backend ring-buffer cap). */
export const MAX_RETAINED_EVENTS = 500;

const STORAGE_KEY = "fleetEvents:v1";

/**
 * Stable identity for an event used during de-duplication. Two events from
 * successive polls describing the same occurrence share this key, so refetches
 * never grow the log with duplicates.
 */
export function eventKey(e: FleetEvent): string {
  return `${e.ts}|${e.kind}|${e.node ?? ""}|${e.title}`;
}

/**
 * Merge freshly-fetched events into the retained history.
 *
 * - de-duplicates by `eventKey`;
 * - sorts newest-first (descending `ts`);
 * - caps the result at `MAX_RETAINED_EVENTS` (oldest dropped).
 *
 * Pure: returns a new array, never mutates either input.
 */
export function mergeEvents(
  existing: ReadonlyArray<FleetEvent>,
  incoming: ReadonlyArray<FleetEvent>,
  max: number = MAX_RETAINED_EVENTS,
): FleetEvent[] {
  const byKey = new Map<string, FleetEvent>();
  for (const e of existing) byKey.set(eventKey(e), e);
  for (const e of incoming) byKey.set(eventKey(e), e);
  const merged = Array.from(byKey.values());
  merged.sort((a, b) => b.ts - a.ts);
  return merged.slice(0, Math.max(0, max));
}

/** True iff `e` is one of the recognised, well-formed FleetEvent shapes. */
function isFleetEvent(e: unknown): e is FleetEvent {
  if (!e || typeof e !== "object") return false;
  const r = e as Record<string, unknown>;
  return (
    typeof r.ts === "number" &&
    (r.severity === "info" || r.severity === "warning" || r.severity === "critical") &&
    typeof r.kind === "string" &&
    typeof r.title === "string"
  );
}

/** Load retained events from localStorage. Best-effort; never throws. */
export function loadEvents(): FleetEvent[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw) as unknown;
    if (!Array.isArray(parsed)) return [];
    return parsed.filter(isFleetEvent);
  } catch {
    return [];
  }
}

/** Persist retained events to localStorage. Best-effort; never throws. */
export function saveEvents(events: ReadonlyArray<FleetEvent>): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(events));
  } catch {
    // Quota / private mode — durability is best-effort only.
  }
}

/** Filter events by severity. `null`/"all" returns everything. Pure. */
export function filterBySeverity(
  events: ReadonlyArray<FleetEvent>,
  severity: EventSeverity | "all" | null,
): FleetEvent[] {
  if (!severity || severity === "all") return events.slice();
  return events.filter((e) => e.severity === severity);
}

/**
 * The current set of "active" condition events derived from the most-recent
 * per-node state. Walks newest→oldest and keeps the latest state per node so a
 * later `runner_online` cancels an earlier `runner_offline`, and the latest
 * `low_disk` reflects current pressure. Pure.
 */
export interface ActiveConditions {
  /** Nodes currently offline, keyed insertion-ordered newest-first. */
  offlineNodes: string[];
  /** Nodes currently offline specifically due to disk pressure. */
  diskOfflineNodes: string[];
  /** Nodes flagged with low disk while still online. */
  lowDiskNodes: string[];
}

export function activeConditions(
  events: ReadonlyArray<FleetEvent>,
): ActiveConditions {
  // events may be in any order; sort newest-first so the first state we see per
  // node is the current one.
  const sorted = events.slice().sort((a, b) => b.ts - a.ts);
  const seenNode = new Set<string>();
  const offlineNodes: string[] = [];
  const diskOfflineNodes: string[] = [];
  const lowDiskNodes: string[] = [];
  const seenLowDisk = new Set<string>();

  for (const e of sorted) {
    const node = e.node;
    if ((e.kind === "runner_offline" || e.kind === "runner_online") && node) {
      if (seenNode.has(node)) continue;
      seenNode.add(node);
      if (e.kind === "runner_offline") {
        offlineNodes.push(node);
        // Disk pressure is encoded in the title/detail by the backend.
        if (/disk/i.test(e.title) || /disk/i.test(e.detail)) {
          diskOfflineNodes.push(node);
        }
      }
    } else if (e.kind === "low_disk" && node && !seenLowDisk.has(node)) {
      seenLowDisk.add(node);
      // Only count as currently-low if the node isn't already offline (the
      // offline event already carries the disk reason).
      if (!seenNode.has(node)) lowDiskNodes.push(node);
    }
  }
  return { offlineNodes, diskOfflineNodes, lowDiskNodes };
}

/**
 * Fold active offline/disk conditions into AlertsCenter `FleetAlert`s so the
 * header pill surfaces them as concise, dismissible rows (issue #863). Returns
 * at most two synthetic alerts: a disk-pressure alert (when any node is offline
 * due to disk OR under low-disk pressure) and a generic runners-offline alert
 * (for non-disk offline nodes). Pure.
 */
export function eventsToAlerts(
  events: ReadonlyArray<FleetEvent>,
): FleetAlert[] {
  const { offlineNodes, diskOfflineNodes, lowDiskNodes } =
    activeConditions(events);
  const alerts: FleetAlert[] = [];

  const push = (a: { id: FleetAlert["id"]; level: AlertLevel; title: string; detail: string }) => {
    alerts.push({ ...a, contentHash: alertContentHash(a) });
  };

  // Disk pressure (highest priority — this is the headline operator concern).
  if (diskOfflineNodes.length > 0 || lowDiskNodes.length > 0) {
    const offlineTxt =
      diskOfflineNodes.length > 0
        ? `${diskOfflineNodes.length} runner(s) offline — disk pressure`
        : "Disk pressure detected";
    const detailParts: string[] = [];
    if (diskOfflineNodes.length > 0) {
      detailParts.push(`Offline: ${diskOfflineNodes.join(", ")}`);
    }
    if (lowDiskNodes.length > 0) {
      detailParts.push(`Low disk: ${lowDiskNodes.join(", ")}`);
    }
    push({
      id: "disk-pressure",
      level: diskOfflineNodes.length > 0 ? "critical" : "warning",
      title: offlineTxt,
      detail: detailParts.join(" · ") || "See the event log for details.",
    });
  }

  // Non-disk offline runners.
  const nonDiskOffline = offlineNodes.filter(
    (n) => !diskOfflineNodes.includes(n),
  );
  if (nonDiskOffline.length > 0) {
    push({
      id: "runners-offline",
      level: "critical",
      title: `${nonDiskOffline.length} runner(s) offline`,
      detail: nonDiskOffline.join(", "),
    });
  }

  return alerts;
}
