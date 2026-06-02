/**
 * alertAck — durable acknowledge/snooze layer for fleet alerts (issue #819).
 *
 * Problem this solves: fleet alerts are *derived from live polled state* and
 * recomputed on every refresh, so a dismissal has nothing durable to suppress
 * — the alert simply re-appears on the next poll. This module gives each alert
 * slot a persistent ack/snooze record keyed by its stable `id`, scoped to the
 * alert's `contentHash` so the alert re-surfaces only when its content
 * materially changes.
 *
 * Contract:
 *   - `ack(id, hash)`      → suppress this alert until its content (hash) changes.
 *   - `snooze(id, hash, ms)` → suppress until `ms` elapses OR content changes.
 *   - `isAcked(id, hash, now?)` → true iff a matching, still-valid record exists.
 *   - `clear(id)`          → drop the record (operator un-acknowledges).
 *
 * Re-show rule (the whole point):
 *   An alert is shown again when EITHER
 *     (a) its `contentHash` differs from the acked hash (content changed), OR
 *     (b) it was snoozed and the snooze window has elapsed.
 *   A plain `ack` (no snooze) persists indefinitely until the content changes.
 *
 * Storage: a single localStorage key holding a JSON map id → record. Writes are
 * best-effort; if storage is unavailable (private browsing, quota) the module
 * degrades to an in-memory map so the UI still behaves within the session.
 */

const STORAGE_KEY = "alertAck:v1";

export interface AckRecord {
  /** contentHash of the alert at the moment it was acked/snoozed. */
  hash: string;
  /** epoch ms when the record was written. */
  ackedAt: number;
  /**
   * epoch ms after which the suppression lapses. `null` means a permanent ack
   * (no time-based re-show; only a content change re-surfaces it).
   */
  snoozeUntil: number | null;
}

export type AckMap = Record<string, AckRecord>;

/**
 * In-memory fallback used ONLY when localStorage itself is unavailable/throwing
 * (private browsing, quota). It must never shadow a present-but-empty
 * localStorage — otherwise a real `localStorage.clear()` would be silently
 * undone. `storageBroken` gates the fallback so a key that is simply absent
 * reads as "no acks" rather than a stale in-memory snapshot.
 */
let memoryMap: AckMap = {};
let storageBroken = false;

function readMap(): AckMap {
  if (storageBroken) return memoryMap;
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw === null) return {};
    const parsed = JSON.parse(raw) as unknown;
    if (!parsed || typeof parsed !== "object") return {};
    return parsed as AckMap;
  } catch {
    storageBroken = true;
    return memoryMap;
  }
}

function writeMap(map: AckMap): void {
  memoryMap = map;
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(map));
  } catch {
    // localStorage is unavailable — switch to memory-only for the session.
    storageBroken = true;
  }
}

/**
 * Whether a given alert is currently suppressed.
 *
 * @param id    stable alert slot id
 * @param hash  current contentHash of the alert
 * @param now   injectable clock (epoch ms) for deterministic tests
 */
export function isAcked(id: string, hash: string, now: number = Date.now()): boolean {
  if (!id) throw new Error("[alertAck] isAcked: id is required");
  const rec = readMap()[id];
  if (!rec) return false;
  // Content changed since the ack → re-surface.
  if (rec.hash !== hash) return false;
  // Snoozed and the window elapsed → re-surface.
  if (rec.snoozeUntil !== null && now >= rec.snoozeUntil) return false;
  return true;
}

/**
 * Permanently acknowledge an alert: suppress until its content changes.
 *
 * Pre-condition: id and hash are non-empty.
 */
export function ack(id: string, hash: string, now: number = Date.now()): void {
  if (!id) throw new Error("[alertAck] ack: id is required");
  if (!hash) throw new Error("[alertAck] ack: hash is required");
  const map = readMap();
  map[id] = { hash, ackedAt: now, snoozeUntil: null };
  writeMap(map);
}

/**
 * Snooze an alert for `durationMs`: suppress until the window elapses OR the
 * content changes, whichever comes first.
 *
 * Pre-condition: id and hash are non-empty; durationMs > 0.
 */
export function snooze(
  id: string,
  hash: string,
  durationMs: number,
  now: number = Date.now(),
): void {
  if (!id) throw new Error("[alertAck] snooze: id is required");
  if (!hash) throw new Error("[alertAck] snooze: hash is required");
  if (!(durationMs > 0)) {
    throw new Error("[alertAck] snooze: durationMs must be > 0");
  }
  const map = readMap();
  map[id] = { hash, ackedAt: now, snoozeUntil: now + durationMs };
  writeMap(map);
}

/** Drop any ack/snooze for `id` (operator un-acknowledges). */
export function clear(id: string): void {
  if (!id) throw new Error("[alertAck] clear: id is required");
  const map = readMap();
  if (id in map) {
    delete map[id];
    writeMap(map);
  }
}

/** Common snooze durations exposed for the UI. */
export const SNOOZE_DURATIONS_MS = {
  oneHour: 60 * 60 * 1000,
  fourHours: 4 * 60 * 60 * 1000,
  oneDay: 24 * 60 * 60 * 1000,
} as const;

/**
 * Number of alert slots currently acknowledged/snoozed and still valid for the
 * supplied set of live alerts. Used for the "N acknowledged" affordance.
 */
export function ackedCount(
  alerts: ReadonlyArray<{ id: string; contentHash: string }>,
  now: number = Date.now(),
): number {
  return alerts.filter((a) => isAcked(a.id, a.contentHash, now)).length;
}
