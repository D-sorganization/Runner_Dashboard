export const SESSION_EXPIRED_EVENT = "runner-dashboard:session-expired";

export type SessionExpiredHandler = () => void;

export function subscribeSessionExpired(handler: SessionExpiredHandler): () => void {
  window.addEventListener(SESSION_EXPIRED_EVENT, handler);
  return () => window.removeEventListener(SESSION_EXPIRED_EVENT, handler);
}

export function emitSessionExpired(): void {
  window.dispatchEvent(new CustomEvent(SESSION_EXPIRED_EVENT));
}

function requestPath(input: RequestInfo | URL): string | null {
  try {
    const rawUrl =
      typeof input === "string"
        ? input
        : input instanceof URL
          ? input.toString()
          : input.url;
    return new URL(rawUrl, window.location.origin).pathname;
  } catch {
    return null;
  }
}

export function shouldIgnoreUnauthorizedResponse(input: RequestInfo | URL): boolean {
  const path = requestPath(input);
  return path === "/api/auth/me" || path === "/api/health" || path === "/api/auth/refresh";
}

const REFRESH_FAILURE_COOLDOWN_MS = 3_000;

let refreshInFlight: Promise<boolean> | null = null;
let refreshFailedAt: number | null = null;

export async function tryRefreshSession(fetchFn: typeof window.fetch = window.fetch): Promise<boolean> {
  if (!refreshInFlight) {
    if (refreshFailedAt !== null && Date.now() - refreshFailedAt < REFRESH_FAILURE_COOLDOWN_MS) {
      return false;
    }

    refreshInFlight = fetchFn("/api/auth/refresh", {
      method: "POST",
      credentials: "include",
      cache: "no-store",
      headers: { "X-Requested-With": "XMLHttpRequest" },
    })
      .then((response) => {
        refreshFailedAt = response.ok ? null : Date.now();
        return response.ok;
      })
      .catch(() => {
        refreshFailedAt = Date.now();
        return false;
      })
      .finally(() => {
        refreshInFlight = null;
      });
  }

  return refreshInFlight;
}

