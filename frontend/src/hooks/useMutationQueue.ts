/**
 * useMutationQueue — React hook for the IndexedDB mutation queue (issue #380).
 *
 * Exposes:
 *  - queuedCount: number of mutations pending replay
 *  - isOnline: current navigator.onLine value
 *  - queueMutation(): enqueue a failed mutation and show a toast
 *
 * On mount the hook attaches a window 'online' listener that drains the queue
 * automatically and shows toasts for stale entries requiring reconfirmation.
 */

import { useCallback, useEffect, useRef, useState } from "react"
import {
  drain,
  enqueue,
  count,
  generateIdempotencyKey,
  type QueuedMutation,
} from "../lib/mutationQueue"
import { useToast } from "../primitives/Toaster"

export interface QueueMutationOptions {
  url: string
  method: "POST" | "DELETE" | "PATCH"
  body?: unknown
  headers?: Record<string, string>
  /** Caller may provide a stable key; if omitted one is generated. */
  idempotencyKey?: string
}

export function useMutationQueue() {
  const [queuedCount, setQueuedCount] = useState(0)
  const [isOnline, setIsOnline] = useState(() =>
    typeof navigator !== "undefined" ? navigator.onLine : true
  )
  const toast = useToast()
  const drainingRef = useRef(false)

  // Sync the count whenever it might have changed.
  const refreshCount = useCallback(async () => {
    try {
      const n = await count()
      setQueuedCount(n)
    } catch {
      // IndexedDB unavailable (e.g. private browsing) — swallow silently
    }
  }, [])

  // Drain the queue on reconnect.
  const drainQueue = useCallback(async () => {
    if (drainingRef.current) return
    drainingRef.current = true

    try {
      const replayed = await drain({
        onStale: async (entry) => {
          // Entry older than MAX_ENTRY_AGE_MS — confirm via a NON-MODAL toast
          // action instead of window.confirm (issue #819). The toast offers an
          // explicit "Replay" action; if it is dismissed or times out without
          // action, we default to NOT replaying the stale entry (safe default).
          const ageMin = Math.round((Date.now() - entry.queuedAt) / 60_000)
          return new Promise<boolean>((resolve) => {
            let settled = false
            const finish = (value: boolean, id?: number) => {
              if (settled) return
              settled = true
              if (id !== undefined) toast.dismiss(id)
              resolve(value)
            }
            const id = toast.showToast(
              `A queued action for "${entry.url}" is ${ageMin} minutes old.`,
              {
                variant: "warning",
                title: "Replay stale action?",
                durationMs: 15_000,
                actionLabel: "Replay",
                onAction: () => finish(true, id),
              },
            )
            // If the toast auto-dismisses (timeout) without the user choosing,
            // resolve false so a stale mutation is never silently replayed.
            window.setTimeout(() => finish(false, id), 15_500)
          })
        },
        onProgress: (remaining) => setQueuedCount(remaining),
      })

      if (replayed > 0) {
        toast.showToast(
          `${replayed} queued action${replayed === 1 ? "" : "s"} replayed successfully.`,
          { variant: "success", title: "Back online" }
        )
      }
    } catch (err) {
      // eslint-disable-next-line no-console
      console.error("[useMutationQueue] drain failed:", err)
    } finally {
      drainingRef.current = false
      await refreshCount()
    }
  }, [toast, refreshCount])

  // Attach online/offline listeners and seed initial count.
  useEffect(() => {
    refreshCount()

    function handleOnline() {
      setIsOnline(true)
      drainQueue()
    }

    function handleOffline() {
      setIsOnline(false)
    }

    window.addEventListener("online", handleOnline)
    window.addEventListener("offline", handleOffline)

    // Also drain on mount in case we came back online before the hook mounted.
    if (navigator.onLine) {
      drainQueue()
    }

    return () => {
      window.removeEventListener("online", handleOnline)
      window.removeEventListener("offline", handleOffline)
    }
  }, [drainQueue, refreshCount])

  /**
   * Enqueue a failed mutation and show a "Queued — will retry when online" toast.
   *
   * Pre-condition: url must be non-empty.
   */
  const queueMutation = useCallback(
    async (opts: QueueMutationOptions): Promise<void> => {
      if (!opts.url) throw new Error("[useMutationQueue] queueMutation: url is required")

      const mutation: Omit<QueuedMutation, "queuedAt"> = {
        url: opts.url,
        method: opts.method,
        body: opts.body,
        headers: opts.headers,
        idempotencyKey: opts.idempotencyKey ?? generateIdempotencyKey(),
      }

      try {
        await enqueue(mutation)
        await refreshCount()
        toast.showToast("Queued — will retry when online", {
          variant: "warning",
          title: "Offline",
          durationMs: 0, // persistent until dismissed
        })
      } catch (err) {
        // eslint-disable-next-line no-console
        console.error("[useMutationQueue] enqueue failed:", err)
        toast.showToast("Could not queue action — data may be lost.", {
          variant: "error",
        })
      }
    },
    [toast, refreshCount]
  )

  return { queuedCount, isOnline, queueMutation }
}
