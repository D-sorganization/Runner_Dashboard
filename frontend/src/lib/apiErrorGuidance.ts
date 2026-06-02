/**
 * apiErrorGuidance — map common API failures to operator actions (issue #837).
 *
 * Background: pages surfaced raw status codes ("HTTP 401", "ConnectError") that
 * tell an operator nothing about what to *do*. This module translates the
 * failures the dashboard actually hits into a short title + a concrete next
 * step, so every empty/error surface speaks the same operator language (DRY).
 *
 * LoD: callers pass a flat, partially-known `ApiFailure` (a thrown Error, a
 * Response, or a bare status) and receive a flat `OperatorGuidance` — they
 * never branch on status codes themselves.
 */

/** Operator-facing guidance for a failed API call. */
export interface OperatorGuidance {
  /** Short headline, e.g. "Maxwell-Daemon not running". */
  title: string;
  /** One-line concrete next step the operator can take. */
  action: string;
  /** Stable category for styling/telemetry. */
  kind: "connection" | "auth" | "forbidden" | "not-found" | "server" | "unknown";
}

/** Loosely-typed failure shape accepted by {@link guidanceForFailure}. */
export interface ApiFailure {
  /** HTTP status code, if known. */
  status?: number;
  /** Error name (e.g. "ConnectError", "TypeError") or thrown Error. */
  error?: unknown;
  /** Optional already-extracted message text. */
  message?: string;
}

function messageOf(error: unknown): string {
  if (!error) return "";
  if (typeof error === "string") return error;
  if (error instanceof Error) return `${error.name}: ${error.message}`;
  return String(error);
}

/**
 * Detect a network/connection failure. `fetch()` rejects with a `TypeError`
 * ("Failed to fetch") when the daemon is unreachable; the backend also relays
 * httpx `ConnectError` strings in error bodies.
 */
function looksLikeConnectionError(text: string): boolean {
  const t = text.toLowerCase();
  return (
    t.includes("connecterror") ||
    t.includes("failed to fetch") ||
    t.includes("networkerror") ||
    t.includes("connection refused") ||
    t.includes("econnrefused") ||
    t.includes("network request failed")
  );
}

/**
 * Translate an API failure into operator guidance.
 *
 * Contract:
 *  - a 401 → refresh the token in Credentials;
 *  - a 403 → insufficient permissions (re-auth / acting-as);
 *  - a 404 → endpoint/resource missing (likely deploy drift);
 *  - a connection error (fetch TypeError / httpx ConnectError) → start the
 *    relevant daemon from Local Tools;
 *  - a 5xx → backend error, retry / check Diagnostics;
 *  - anything else → a generic retry message.
 * Postcondition: always returns a fully-populated `OperatorGuidance` (never
 * surfaces a bare status code to the operator).
 */
export function guidanceForFailure(failure: ApiFailure): OperatorGuidance {
  const text = failure.message ?? messageOf(failure.error);
  const status = failure.status;

  // Connection failures take priority — a thrown fetch TypeError has no status.
  if ((status === undefined || status === 0) && looksLikeConnectionError(text)) {
    return {
      title: "Service unreachable",
      action:
        "Maxwell-Daemon isn't responding — start it from the Local Tools tab, then retry.",
      kind: "connection",
    };
  }

  switch (status) {
    case 401:
      return {
        title: "Session expired",
        action: "Your token expired — refresh it in the Credentials tab, then retry.",
        kind: "auth",
      };
    case 403:
      return {
        title: "Not permitted",
        action:
          "This action needs more permissions — sign in (or switch acting-as identity) in Credentials.",
        kind: "forbidden",
      };
    case 404:
      return {
        title: "Not found",
        action:
          "That endpoint is missing — the backend may be mid-deploy or out of date. Check the Diagnostics tab.",
        kind: "not-found",
      };
    default:
      break;
  }

  if (typeof status === "number" && status >= 500) {
    return {
      title: "Backend error",
      action:
        "The dashboard backend returned an error — retry shortly, then check the Diagnostics tab if it persists.",
      kind: "server",
    };
  }

  if (looksLikeConnectionError(text)) {
    return {
      title: "Service unreachable",
      action:
        "Maxwell-Daemon isn't responding — start it from the Local Tools tab, then retry.",
      kind: "connection",
    };
  }

  return {
    title: "Couldn't load data",
    action: "Something went wrong fetching this — retry, or check the Diagnostics tab.",
    kind: "unknown",
  };
}

/**
 * Convenience: derive guidance directly from a non-OK `Response`.
 * Pre: `response.ok` is false (otherwise there is nothing to explain).
 */
export function guidanceForResponse(response: {
  ok: boolean;
  status: number;
}): OperatorGuidance {
  return guidanceForFailure({ status: response.status });
}
