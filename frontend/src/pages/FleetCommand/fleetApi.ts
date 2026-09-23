/**
 * fleetApi.ts — typed client + pure helpers for the Fleet Command tab (#1233).
 *
 * Every call goes through the shared `apiRequest`, so the CSRF sentinel header
 * (`X-Requested-With: XMLHttpRequest`) and the structured `ApiClientError`
 * contract apply uniformly (DRY with `pages/Staff/staffApi.ts`, whose roster
 * and dispatch calls the Dispatch panel reuses as-is).
 */
import { useCallback, useEffect, useState } from "react";
import { ApiClientError, apiRequest } from "../../lib/api";
import { tabIdToPath } from "../../shell/routing";
import type {
  Availability,
  BoardSession,
  ClaimBody,
  ClaimReleaseBody,
  ClaimStatus,
  Directive,
  DirectivesResponse,
  InboxResponse,
  MeetingDetail,
  MeetingsResponse,
  MessageBody,
  PrioritiesResponse,
  SessionsResponse,
  StaffRunSummary,
  WriteReceipt,
} from "./types";

export const PRIORITIES_BASE = "/api/priorities";
export const COORDINATION_BASE = "/api/coordination";
export const GITHUB_ORG = "D-sorganization";
export const NOT_AVAILABLE = "Not available on this node.";

function qs(params: Record<string, string | number | undefined | null>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && String(value).trim() !== "") search.set(key, String(value).trim());
  }
  const text = search.toString();
  return text ? `?${text}` : "";
}

// ── Calls ────────────────────────────────────────────────────────────────────

export const fetchPriorities = (signal?: AbortSignal) => apiRequest<PrioritiesResponse>(PRIORITIES_BASE, { signal });

export const fetchMeetings = (signal?: AbortSignal) =>
  apiRequest<MeetingsResponse>(`${PRIORITIES_BASE}/meetings`, { signal });

export const fetchMeeting = (date: string, signal?: AbortSignal) =>
  apiRequest<MeetingDetail>(`${PRIORITIES_BASE}/meetings/${encodeURIComponent(date)}`, { signal });

export const fetchDirectives = (signal?: AbortSignal) =>
  apiRequest<DirectivesResponse>(`${PRIORITIES_BASE}/directives`, { signal });

export const putDirectives = (directives: Directive[]) =>
  apiRequest<DirectivesResponse>(`${PRIORITIES_BASE}/directives`, { method: "PUT", body: { directives } });

export const fetchSessions = (repo?: string, signal?: AbortSignal) =>
  apiRequest<SessionsResponse>(`${COORDINATION_BASE}/sessions${qs({ repo })}`, { signal });

export const fetchInbox = (session: string, repo?: string, signal?: AbortSignal) =>
  apiRequest<InboxResponse>(`${COORDINATION_BASE}/inbox${qs({ session, repo })}`, { signal });

export const sendMessage = (body: MessageBody) => apiRequest<WriteReceipt>(`${COORDINATION_BASE}/messages`, { body });

export const checkClaim = (repo: string, issue: number, signal?: AbortSignal) =>
  apiRequest<ClaimStatus>(`${COORDINATION_BASE}/claims${qs({ repo, issue })}`, { signal });

export const postClaim = (body: ClaimBody) => apiRequest<WriteReceipt>(`${COORDINATION_BASE}/claims`, { body });

export const releaseClaim = (body: ClaimReleaseBody) =>
  apiRequest<WriteReceipt>(`${COORDINATION_BASE}/claims/release`, { body });

// ── Errors and availability ──────────────────────────────────────────────────

/** True when the route is absent on this node (backend PR not deployed). */
export function isNotFound(err: unknown): boolean {
  return err instanceof ApiClientError && err.status === 404;
}

/**
 * Human text for a failed call. Coordination writes answer 409/502 with a
 * structured `detail: {error, held_by?, guidance?}`; `apiRequest` passes that
 * object through untouched, so it is flattened here instead of rendered raw.
 */
export function describeError(err: unknown): string {
  if (err instanceof ApiClientError) {
    if (err.status === 404) return NOT_AVAILABLE;
    const detail: unknown = err.detail;
    if (detail && typeof detail === "object" && !Array.isArray(detail)) {
      const d = detail as Record<string, unknown>;
      const parts = [String(d.error ?? `HTTP ${err.status}`)];
      if (d.held_by) parts.push(`held by ${String(d.held_by)}`);
      if (d.guidance) parts.push(String(d.guidance));
      return parts.join(" — ");
    }
    if (Array.isArray(detail)) {
      return detail
        .map((e) => (e && typeof e === "object" ? String((e as { msg?: unknown }).msg ?? "") : String(e)))
        .join("; ");
    }
    return `${err.status}: ${String(detail)}`;
  }
  return err instanceof Error ? err.message : String(err);
}

export interface Resource<T> {
  data: T | null;
  loading: boolean;
  error: string | null;
  /** Set when the route 404s or answers `available: false`; holds the reason. */
  unavailable: string | null;
  reload: () => void;
}

/**
 * Load one read endpoint with abort-on-unmount and the tab's degradation
 * contract: a 404 or `available:false` is "not available on this node",
 * never an error. `key` re-runs the load when a parameter changes.
 */
export function useResource<T extends object>(
  load: ((signal: AbortSignal) => Promise<T>) | null,
  key: string,
): Resource<T> {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(load !== null);
  const [error, setError] = useState<string | null>(null);
  const [unavailable, setUnavailable] = useState<string | null>(null);
  const [tick, setTick] = useState(0);

  useEffect(() => {
    if (!load) {
      setLoading(false);
      return undefined;
    }
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    load(controller.signal)
      .then((payload) => {
        const avail = payload as Availability;
        setUnavailable(avail.available === false ? avail.reason || NOT_AVAILABLE : null);
        setData(payload);
        setLoading(false);
      })
      .catch((e: unknown) => {
        if (controller.signal.aborted) return;
        if (isNotFound(e)) setUnavailable(NOT_AVAILABLE);
        else setError(describeError(e));
        setData(null);
        setLoading(false);
      });
    return () => controller.abort();
    // `load` is recreated by callers each render; `key` carries its inputs.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, tick]);

  const reload = useCallback(() => setTick((n) => n + 1), []);
  return { data, loading, error, unavailable, reload };
}

// ── Pure helpers ─────────────────────────────────────────────────────────────

/** GitHub URL for a board tracking reference (`#12`, `Tools#12`, `org/repo#12`, or a URL). */
export function trackingUrl(tracking: string, project?: string): string | null {
  const ref = tracking.trim();
  if (/^https:\/\/github\.com\//.test(ref)) return ref;
  const m = ref.match(/^(?:([\w.-]+)\/)?([\w.-]+)?#(\d+)$/);
  if (!m) return null;
  const repo = m[2] || (project ?? "").trim();
  if (!repo || !/^[\w.-]+$/.test(repo)) return null;
  return `https://github.com/${m[1] || GITHUB_ORG}/${repo}/issues/${m[3]}`;
}

export function issueUrl(repo: string, issue: number): string {
  return `https://github.com/${GITHUB_ORG}/${repo}/issues/${issue}`;
}

/** "in 1h 20m", "in 5m" or "expired" for an ISO expiry; "" when absent or invalid. */
export function expiresIn(iso: string | null | undefined, now: number = Date.now()): string {
  if (!iso) return "";
  const at = Date.parse(iso);
  if (!Number.isFinite(at)) return "";
  const minutes = Math.round((at - now) / 60_000);
  if (minutes <= 0) return "expired";
  const h = Math.floor(minutes / 60);
  const d = Math.floor(h / 24);
  if (d > 0) return `in ${d}d ${h % 24}h`;
  return h > 0 ? `in ${h}h ${minutes % 60}m` : `in ${minutes}m`;
}

/** "expires in 2h 5m" / "expired" / "" — `verb` names the lapse ("expires", "lapses"). */
export function expiryLabel(iso: string | null | undefined, verb: string, now?: number): string {
  const text = expiresIn(iso, now);
  if (!text) return "";
  return text === "expired" ? "expired" : `${verb} ${text}`;
}

/** Staff tab deep link that opens one run (StaffPage honours `?run=`). */
export function staffRunHref(runId: string): string {
  return `${tabIdToPath("staff")}?run=${encodeURIComponent(runId)}`;
}

/** One row of the Active work table: a board session or a staff run. */
export interface WorkRow {
  key: string;
  source: "board" | "staff";
  who: string;
  repo: string;
  issue: number | null;
  branch: string;
  goals: string[];
  expires: string;
  provider: string;
  machine: string;
  status: string;
  paths: string[];
}

function targetIssue(target: string | undefined): number | null {
  const m = (target ?? "").match(/^#(\d+)$/);
  return m ? Number(m[1]) : null;
}

export function toWorkRows(sessions: BoardSession[], runs: StaffRunSummary[]): WorkRow[] {
  const board: WorkRow[] = sessions.map((s) => ({
    key: `board:${s.session}`,
    source: "board",
    who: s.agent || s.session,
    repo: s.repo,
    issue: s.issue ?? null,
    branch: s.branch ?? "",
    goals: Object.entries(s.goals ?? {}).map(([k, v]) => `${k}: ${v}`),
    expires: s.expires ?? "",
    provider: "",
    machine: "",
    status: "active",
    paths: s.paths ?? [],
  }));
  const staff: WorkRow[] = runs.map((r) => ({
    key: `staff:${r.id}`,
    source: "staff",
    who: r.role,
    repo: r.repo,
    issue: targetIssue(r.target),
    branch: "",
    goals: r.target && targetIssue(r.target) === null ? [r.target] : [],
    expires: "",
    provider: r.provider,
    machine: r.machine,
    status: r.status,
    paths: [],
  }));
  return [...board, ...staff];
}

const fold = (s: string) => s.toLowerCase();

function pathsOverlap(a: string, b: string): boolean {
  const x = fold(a);
  const y = fold(b);
  return x === y || x.startsWith(`${y}/`) || y.startsWith(`${x}/`);
}

/**
 * Advisory conflicts between rows in the same repository: the same issue, or
 * overlapping paths (same rule as RM `agent_messages.conflicts`). Returns
 * row key → human reasons; rows without a conflict are absent.
 */
export function findConflicts(rows: WorkRow[]): Map<string, string[]> {
  const out = new Map<string, string[]>();
  const add = (key: string, reason: string) => out.set(key, [...(out.get(key) ?? []), reason]);
  for (let i = 0; i < rows.length; i += 1) {
    for (let j = i + 1; j < rows.length; j += 1) {
      const a = rows[i];
      const b = rows[j];
      if (!a.repo || fold(a.repo) !== fold(b.repo)) continue;
      if (a.issue !== null && a.issue === b.issue) {
        add(a.key, `same issue #${a.issue} as ${b.who}`);
        add(b.key, `same issue #${b.issue} as ${a.who}`);
      }
      const pairs = a.paths.flatMap((p) => b.paths.filter((q) => pathsOverlap(p, q)).map((q) => [p, q] as const));
      if (pairs.length > 0) {
        add(a.key, `paths overlap ${b.who}: ${pairs.map(([p, q]) => `${p} ↔ ${q}`).join(", ")}`);
        add(b.key, `paths overlap ${a.who}: ${pairs.map(([p, q]) => `${q} ↔ ${p}`).join(", ")}`);
      }
    }
  }
  return out;
}

/** Sorted, de-duplicated repositories named by the rows (for the repo filter). */
export function reposOf(rows: WorkRow[]): string[] {
  return Array.from(new Set(rows.map((r) => r.repo).filter(Boolean))).sort((a, b) => a.localeCompare(b));
}

export function parseIssue(value: string): number | null {
  const n = Number(value.trim().replace(/^#/, ""));
  return Number.isInteger(n) && n >= 1 ? n : null;
}
