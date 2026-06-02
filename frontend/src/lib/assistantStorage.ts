/**
 * assistantStorage.ts — localStorage helpers and key registry for the in-app
 * chat assistant sidebar, extracted (behaviour-wise 1:1) from the legacy
 * `App.tsx` monolith as part of the decomposition epic (#836, pass 9).
 *
 * These were free functions inside `App.tsx` (`lsGet`/`lsSet`/
 * `lsLoadTranscript`/`clearAssistantTranscriptHistory`) plus the `ASST_LS`
 * key registry and the 24h transcript TTL. They are shared between the
 * extracted `AssistantSidebar` page and the legacy App shell (which still
 * reads/writes the open/position/openByDefault keys), so they live in `lib/`
 * rather than alongside the page. Every read swallows JSON/quota errors and
 * falls back, exactly as the original did.
 */

/** A single chat message in the assistant transcript. */
export interface AssistantMessage {
  role: "user" | "assistant";
  content: string;
  id: number;
}

/** localStorage key registry for the assistant sidebar. */
export const ASST_LS = {
  open: "assistant:open",
  position: "assistant:position",
  width: "assistant:width",
  transcript: "assistant:transcript",
  transcriptTimestamp: "assistant:transcript:ts",
  openByDefault: "assistant:openByDefault",
  includeContext: "assistant:includeContext",
  saveHistory: "assistant:saveHistory",
} as const;

/** Persisted transcripts older than this are discarded on load. */
export const ASST_HISTORY_TTL_MS = 24 * 60 * 60 * 1000; // 24 hours

/**
 * Read+parse a JSON value from localStorage, returning `fallback` when the key
 * is absent or unparseable (and on any storage exception).
 */
export function lsGet<T>(key: string, fallback: T): T {
  try {
    const v = localStorage.getItem(key);
    return v === null ? fallback : (JSON.parse(v) as T);
  } catch {
    return fallback;
  }
}

/** Write a JSON-serialised value to localStorage, swallowing quota errors. */
export function lsSet(key: string, val: unknown): void {
  try {
    localStorage.setItem(key, JSON.stringify(val));
  } catch {
    /* ignore quota / serialisation errors */
  }
}

/**
 * Load the saved transcript, discarding (and clearing) it when older than the
 * TTL. Returns an empty array on any error or when expired.
 */
export function lsLoadTranscript(): AssistantMessage[] {
  try {
    const ts = parseInt(
      localStorage.getItem(ASST_LS.transcriptTimestamp) || "0",
      10,
    );
    if (!ts || Date.now() - ts > ASST_HISTORY_TTL_MS) {
      localStorage.removeItem(ASST_LS.transcript);
      localStorage.removeItem(ASST_LS.transcriptTimestamp);
      return [];
    }
    return lsGet<AssistantMessage[]>(ASST_LS.transcript, []);
  } catch {
    return [];
  }
}

/** Remove the persisted transcript and its timestamp. */
export function clearAssistantTranscriptHistory(): void {
  try {
    localStorage.removeItem(ASST_LS.transcript);
    localStorage.removeItem(ASST_LS.transcriptTimestamp);
  } catch {
    /* ignore storage errors */
  }
}
