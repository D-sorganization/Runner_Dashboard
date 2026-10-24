/**
 * Typed client for the fleet security scanner API (epic #2566, issue #2571).
 *
 * Endpoints live under `/api/v1/security`. Contracts are fleet-peer readable;
 * posture and findings need the `security.read` scope (403 otherwise) and
 * triage needs `security.triage`. A 404 on any of them means the feature is
 * not deployed on this node and is shown as disabled.
 *
 * Findings are model output about public repositories: every free-text field
 * is untrusted. Callers must render them as plain text only.
 */
import { legacyFetch } from "../../lib/api";

export const SEVERITIES = ["critical", "high", "medium", "low", "info"] as const;
export type Severity = (typeof SEVERITIES)[number];

export const FINDING_STATES = [
  "new",
  "confirmed",
  "inconclusive",
  "false_positive",
  "accepted_risk",
  "fixed",
] as const;
export type FindingState = (typeof FINDING_STATES)[number];

/** States the backend refuses without a triage note (`NOTE_REQUIRED`). */
export const NOTE_REQUIRED_STATES: ReadonlySet<string> = new Set(["false_positive", "accepted_risk"]);

export const ALERT_KINDS = ["dependabot", "code_scanning", "secret_scanning"] as const;
export type AlertKind = (typeof ALERT_KINDS)[number];

export interface ContractStatus {
  repo: string;
  present: boolean;
  valid: boolean;
  problems: string[];
  dockerfile?: string | null;
  threat_model?: string | null;
  threat_model_present?: boolean;
  disabled?: boolean;
  error?: string;
}

export interface ContractsOverview {
  contracts: ContractStatus[];
  summary: { repos: number; enrolled: number; valid: number; invalid: number; errors: number };
}

export type AlertKindStatus =
  | { status: "ok"; open: number; by_severity: Record<string, number>; capped: boolean }
  | { status: "unavailable" | "error"; reason?: string };

export interface RepoPosture {
  repo: string;
  contract: ContractStatus;
  operator: { enabled?: boolean; full_scan_days?: number; priority?: number };
  open_findings: Record<string, number>;
  github_alerts: Partial<Record<AlertKind, AlertKindStatus>> | null;
}

/** One row of ``GET /api/v1/security/coverage`` (#2574), most urgent due repo first. */
export interface CoverageRow {
  repo: string;
  contract_present: boolean;
  contract_valid: boolean;
  contract_error?: string | null;
  contract_disabled: boolean;
  enabled: boolean;
  priority: number;
  full_scan_days: number;
  last_scan_at: string;
  last_state: string;
  last_success_at: string;
  due: boolean;
}

export interface CoverageOverview {
  coverage: CoverageRow[];
  summary: { repos: number; with_contract: number; valid: number; enabled: number; scanned: number; due: number };
  schedule: { enabled: boolean; check_minutes: number; retry_hours: number };
}

export interface PostureOverview {
  repos: RepoPosture[];
  operator: {
    scan_hosts: { name: string; isolation: string; eligible_for_real_scans: boolean }[];
    real_scans_possible: boolean;
    errors: string[];
  };
}

export interface Finding {
  id: string;
  repo: string;
  title: string;
  cwe: string;
  severity: string;
  confidence: string;
  path: string;
  symbol: string;
  root_cause: string;
  repro: string;
  patch: string;
  state: string;
  scan_sha?: string;
  image_digest?: string;
  scan_run_id?: string;
  first_seen?: string;
  last_seen?: string;
  occurrences?: number;
}

export interface AuditEntry {
  from_state: string;
  to_state: string;
  actor: string;
  note: string;
  ts: string;
}

export interface FindingDetail {
  finding: Finding;
  audit: AuditEntry[];
}

export interface FindingFilters {
  repo?: string | null;
  state?: string | null;
  severity?: string | null;
}

export type SecurityErrorCode = "disabled" | "forbidden" | "invalid" | "http";

export class SecurityApiError extends Error {
  readonly code: SecurityErrorCode;
  readonly status: number;

  constructor(code: SecurityErrorCode, status: number, message: string) {
    super(message);
    this.name = "SecurityApiError";
    this.code = code;
    this.status = status;
  }
}

const XHR = { "X-Requested-With": "XMLHttpRequest" };
const BASE = "/api/v1/security";

async function detailOf(response: Response): Promise<string | null> {
  try {
    const body = (await response.json()) as { detail?: unknown; error?: { message?: unknown } };
    if (typeof body?.detail === "string") return body.detail;
    if (typeof body?.error?.message === "string") return body.error.message;
  } catch {
    // non-JSON error body
  }
  return null;
}

async function failure(response: Response, scope: string): Promise<SecurityApiError> {
  if (response.status === 404) {
    return new SecurityApiError("disabled", 404, "The security scanner is not enabled on this node");
  }
  if (response.status === 403) {
    return new SecurityApiError("forbidden", 403, `Requires the ${scope} scope`);
  }
  const detail = await detailOf(response);
  return new SecurityApiError("http", response.status, detail ?? `HTTP ${response.status}`);
}

function invalid(what: string): SecurityApiError {
  return new SecurityApiError("invalid", 200, `Invalid ${what} response`);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function assertContracts(value: unknown): ContractsOverview {
  if (!isRecord(value) || !Array.isArray(value.contracts) || !isRecord(value.summary)) {
    throw invalid("security contracts");
  }
  for (const row of value.contracts) {
    if (!isRecord(row) || typeof row.repo !== "string") throw invalid("security contracts");
  }
  return value as unknown as ContractsOverview;
}

function assertPosture(value: unknown): PostureOverview {
  if (!isRecord(value) || !Array.isArray(value.repos) || !isRecord(value.operator)) {
    throw invalid("security posture");
  }
  for (const row of value.repos) {
    if (!isRecord(row) || typeof row.repo !== "string" || !isRecord(row.contract)) {
      throw invalid("security posture");
    }
  }
  return value as unknown as PostureOverview;
}

function assertCoverage(value: unknown): CoverageOverview {
  if (!isRecord(value) || !Array.isArray(value.coverage) || !isRecord(value.summary) || !isRecord(value.schedule)) {
    throw invalid("security coverage");
  }
  for (const row of value.coverage) {
    if (!isRecord(row) || typeof row.repo !== "string" || typeof row.due !== "boolean") {
      throw invalid("security coverage");
    }
  }
  return value as unknown as CoverageOverview;
}

function assertFinding(value: unknown): Finding {
  if (!isRecord(value) || typeof value.id !== "string" || typeof value.title !== "string") {
    throw invalid("security finding");
  }
  return value as unknown as Finding;
}

function assertFindings(value: unknown): Finding[] {
  if (!isRecord(value) || !Array.isArray(value.findings)) throw invalid("security findings");
  return value.findings.map(assertFinding);
}

function assertDetail(value: unknown): FindingDetail {
  if (!isRecord(value) || !Array.isArray(value.audit)) throw invalid("security finding");
  return { finding: assertFinding(value.finding), audit: value.audit as AuditEntry[] };
}

async function getJson(path: string, scope: string): Promise<unknown> {
  const response = await fetch(path, { headers: XHR });
  if (!response.ok) throw await failure(response, scope);
  return response.json();
}

export async function fetchContracts(): Promise<ContractsOverview> {
  return assertContracts(await getJson(`${BASE}/contracts`, "fleet peer"));
}

export async function fetchPosture(): Promise<PostureOverview> {
  return assertPosture(await getJson(`${BASE}/posture?alerts=true`, "security.read"));
}

export async function fetchCoverage(): Promise<CoverageOverview> {
  return assertCoverage(await getJson(`${BASE}/coverage`, "security.read"));
}

export async function fetchFindings(filters: FindingFilters = {}): Promise<Finding[]> {
  const params = new URLSearchParams();
  if (filters.repo) params.set("repo", filters.repo);
  if (filters.state) params.set("state", filters.state);
  if (filters.severity) params.set("severity", filters.severity);
  const qs = params.toString();
  return assertFindings(await getJson(`${BASE}/findings${qs ? "?" + qs : ""}`, "security.read"));
}

export async function fetchFinding(id: string): Promise<FindingDetail> {
  return assertDetail(await getJson(`${BASE}/findings/${encodeURIComponent(id)}`, "security.read"));
}

export async function triageFinding(id: string, state: string, note: string): Promise<Finding> {
  const response = await legacyFetch(`${BASE}/findings/${encodeURIComponent(id)}/triage`, {
    method: "POST",
    credentials: "same-origin",
    headers: { ...XHR, "Content-Type": "application/json" },
    body: JSON.stringify({ state, note }),
  });
  if (!response.ok) throw await failure(response, "security.triage");
  const body: unknown = await response.json();
  if (!isRecord(body)) throw invalid("security triage");
  return assertFinding(body.finding);
}

/** True when the backend would refuse this transition without a note. */
export function noteRequired(state: string): boolean {
  return NOTE_REQUIRED_STATES.has(state);
}

export function severityTone(severity: string): "danger" | "warning" | "info" | "neutral" {
  if (severity === "critical" || severity === "high") return "danger";
  if (severity === "medium") return "warning";
  if (severity === "low") return "info";
  return "neutral";
}

/** "critical 1 · high 2" in canonical severity order; empty counts are skipped. */
export function formatSeverityCounts(counts: Record<string, number> | null | undefined): string {
  if (!counts) return "—";
  const parts = SEVERITIES.filter((s) => (counts[s] ?? 0) > 0).map((s) => `${s} ${counts[s]}`);
  return parts.length ? parts.join(" · ") : "none";
}

export function totalCount(counts: Record<string, number> | null | undefined): number {
  if (!counts) return 0;
  return Object.values(counts).reduce((sum, n) => sum + (typeof n === "number" ? n : 0), 0);
}

export function formatAlert(status: AlertKindStatus | undefined): string {
  if (!status) return "—";
  if (status.status === "ok") {
    return `${status.open}${status.capped ? "+" : ""}`;
  }
  return status.status === "unavailable" ? "unavailable" : "error";
}

export function stateLabel(state: string): string {
  return state.replace(/_/g, " ");
}
