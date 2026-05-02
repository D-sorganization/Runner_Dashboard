/**
 * mutationQueue — IndexedDB-backed offline mutation queue (issue #380).
 *
 * When a POST/DELETE/PATCH request fails because the browser is offline
 * (navigator.onLine === false), callers should enqueue the mutation here
 * instead of silently dropping it.  On `window.online` the queue is
 * drained automatically and each mutation replayed exactly once, protected
 * by a per-entry `Idempotency-Key` header so server-side duplicate
 * execution is impossible.
 *
 * Entries older than MAX_ENTRY_AGE_MS (10 min) are surfaced to the user
 * for reconfirmation before replay.
 *
 * Design contracts (DbC):
 *   - enqueue(): accepts a QueuedMutation; asserts all required fields present
 *   - drain():   replays in FIFO order; asserts idempotency-key is set before
 *                each fetch
 *   - Each entry carries a monotonic `queuedAt` timestamp for age checks.
 */

import { openDB, IDBPDatabase } from "idb"

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const DB_NAME = "runner-dashboard-mutation-queue"
const DB_VERSION = 1
const STORE_NAME = "mutations"

/** Entries older than this are flagged as stale and require reconfirmation. */
export const MAX_ENTRY_AGE_MS = 10 * 60 * 1000 // 10 minutes

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface QueuedMutation {
  /** Stable unique key for idempotent replay — set by the caller or generated. */
  idempotencyKey: string
  /** Absolute URL or path, e.g. "/api/runs/123/cancel". */
  url: string
  /** HTTP method (POST, DELETE, PATCH). */
  method: "POST" | "DELETE" | "PATCH"
  /** Optional JSON-serialisable request body. */
  body?: unknown
  /** Additional request headers (Content-Type etc.). */
  headers?: Record<string, string>
  /** Unix timestamp (ms) when the entry was queued. */
  queuedAt: number
}

export interface MutationQueueEntry extends QueuedMutation {
  /** Auto-incremented IDB key. */
  id?: number
}

// ---------------------------------------------------------------------------
// DB initialisation
// ---------------------------------------------------------------------------

let _db: IDBPDatabase | null = null

async function getDb(): Promise<IDBPDatabase> {
  if (_db) return _db
  _db = await openDB(DB_NAME, DB_VERSION, {
    upgrade(db) {
      if (!db.objectStoreNames.contains(STORE_NAME)) {
        db.createObjectStore(STORE_NAME, { keyPath: "id", autoIncrement: true })
      }
    },
  })
  return _db
}

// ---------------------------------------------------------------------------
// Queue operations
// ---------------------------------------------------------------------------

/**
 * Enqueue a failed mutation for later replay.
 *
 * Pre-condition: mutation.url must be non-empty; method must be POST/DELETE/PATCH.
 * Post-condition: mutation is persisted to IndexedDB with a timestamp.
 */
export async function enqueue(mutation: Omit<QueuedMutation, "queuedAt">): Promise<void> {
  // DbC: assert preconditions
  if (!mutation.url) throw new Error("[mutationQueue] enqueue: url is required")
  if (!mutation.idempotencyKey)
    throw new Error("[mutationQueue] enqueue: idempotencyKey is required")

  const entry: QueuedMutation = {
    ...mutation,
    queuedAt: Date.now(),
  }

  const db = await getDb()
  await db.add(STORE_NAME, entry)
}

/**
 * Return all queued mutations in insertion order.
 */
export async function getAll(): Promise<MutationQueueEntry[]> {
  const db = await getDb()
  return db.getAll(STORE_NAME)
}

/**
 * Return the number of mutations currently queued.
 */
export async function count(): Promise<number> {
  const db = await getDb()
  return db.count(STORE_NAME)
}

/**
 * Remove a single entry by its IDB auto-increment key.
 */
export async function remove(id: number): Promise<void> {
  const db = await getDb()
  await db.delete(STORE_NAME, id)
}

/**
 * Drain the queue — replay each mutation in FIFO order.
 *
 * @param onStale  Called for entries older than MAX_ENTRY_AGE_MS; if the
 *                 returned promise resolves `false` the entry is skipped and
 *                 left in the queue.
 * @param onQueued Called after the final count changes so UI can update.
 * @returns Number of mutations successfully replayed.
 */
export async function drain(options?: {
  onStale?: (entry: MutationQueueEntry) => Promise<boolean>
  onProgress?: (remaining: number) => void
}): Promise<number> {
  const entries = await getAll()
  let replayed = 0
  const now = Date.now()

  for (const entry of entries) {
    // DbC: idempotency-key must be set
    if (!entry.idempotencyKey) {
      console.warn("[mutationQueue] drain: entry missing idempotencyKey, skipping", entry)
      continue
    }

    const age = now - entry.queuedAt
    if (age > MAX_ENTRY_AGE_MS && options?.onStale) {
      const proceed = await options.onStale(entry)
      if (!proceed) {
        options?.onProgress?.(await count())
        continue
      }
    }

    const headers: Record<string, string> = {
      "Content-Type": "application/json",
      "Idempotency-Key": entry.idempotencyKey,
      "X-Requested-With": "XMLHttpRequest",
      ...entry.headers,
    }

    try {
      const resp = await fetch(entry.url, {
        method: entry.method,
        headers,
        body: entry.body !== undefined ? JSON.stringify(entry.body) : undefined,
        credentials: "same-origin",
      })

      if (resp.ok || resp.status < 500) {
        // 2xx = success; 4xx = server-side rejection (don't retry)
        if (entry.id !== undefined) await remove(entry.id)
        replayed++
      } else {
        // 5xx = transient server error — leave in queue
        console.warn(`[mutationQueue] replay got ${resp.status} for ${entry.url}, keeping in queue`)
      }
    } catch (err) {
      // Network still down — abort drain for this cycle
      console.warn("[mutationQueue] drain aborted (network unavailable):", err)
      break
    }

    options?.onProgress?.(await count())
  }

  return replayed
}

/**
 * Remove all entries from the queue (e.g. on sign-out).
 */
export async function clearAll(): Promise<void> {
  const db = await getDb()
  await db.clear(STORE_NAME)
}

// ---------------------------------------------------------------------------
// Crypto helper — generate a UUID-like idempotency key
// ---------------------------------------------------------------------------

/**
 * Generate a random idempotency key using the Web Crypto API.
 * Falls back to Math.random() in environments that don't support crypto.uuid().
 */
export function generateIdempotencyKey(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID()
  }
  // Fallback
  return `${Date.now()}-${Math.random().toString(36).slice(2)}`
}
