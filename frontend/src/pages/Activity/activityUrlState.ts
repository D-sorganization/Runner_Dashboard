/** URL persistence for Activity filters (#2865). */

export type ActivityFilters = {
  repo: string;
  issue: string;
  agent: string;
  node: string;
};

export const EMPTY_ACTIVITY_FILTERS: ActivityFilters = {
  repo: "",
  issue: "",
  agent: "",
  node: "",
};

export const ALL_WORK_CHANNEL = "__all_work__";

export function readActivityFiltersFromUrl(search = window.location.search): ActivityFilters {
  const params = new URLSearchParams(search);
  return {
    repo: params.get("repo")?.trim() ?? "",
    issue: params.get("issue")?.trim() ?? "",
    agent: params.get("agent")?.trim() ?? "",
    node: params.get("node")?.trim() ?? "",
  };
}

export function readSessionFromUrl(search = window.location.search): string | null {
  const raw = new URLSearchParams(search).get("session");
  return raw?.trim() ? raw : null;
}

export function syncActivityUrl(filters: ActivityFilters, sessionKey: string | null): void {
  if (typeof window === "undefined") return;
  const params = new URLSearchParams();
  if (sessionKey && sessionKey !== ALL_WORK_CHANNEL) params.set("session", sessionKey);
  for (const [key, value] of Object.entries(filters)) {
    const trimmed = value.trim();
    if (trimmed) params.set(key, trimmed);
  }
  const qs = params.toString();
  window.history.replaceState(window.history.state, "", `${window.location.pathname}${qs ? `?${qs}` : ""}`);
}

export function eventMatchesFilters(
  event: { repo?: string | null; issue?: number | null; node?: string | null; principal?: string | null; session_id?: string | null; payload?: Record<string, unknown> },
  filters: ActivityFilters,
): boolean {
  if (filters.repo && event.repo !== filters.repo) return false;
  if (filters.node && event.node !== filters.node) return false;
  if (filters.issue) {
    const want = Number.parseInt(filters.issue, 10);
    if (!Number.isFinite(want) || event.issue !== want) return false;
  }
  if (filters.agent) {
    const agent =
      (typeof event.payload?.agent === "string" && event.payload.agent.trim()) ||
      event.principal ||
      event.session_id ||
      "";
    if (!agent.toLowerCase().includes(filters.agent.toLowerCase())) return false;
  }
  return true;
}
