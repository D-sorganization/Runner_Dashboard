/**
 * AUTO-GENERATED — DO NOT EDIT MANUALLY
 *
 * Generated from FastAPI /openapi.json by openapi-typescript.
 * Regenerate with: ./scripts/gen-api-client.sh
 *
 * This file was bootstrapped manually (backend not running during initial
 * codegen setup). Run the script against a live backend to get the full schema.
 */

// ── Shared ────────────────────────────────────────────────────────────────────

export interface ApiError {
  detail: string;
}

// ── Runs / Workflows ──────────────────────────────────────────────────────────

export interface RunRepository {
  name: string;
  full_name: string;
}

export interface WorkflowRun {
  id: number;
  name: string;
  workflow_name: string;
  status: string;
  conclusion: string | null;
  head_branch: string;
  head_sha: string;
  html_url: string;
  created_at: string;
  updated_at: string;
  run_number: number;
  repository: RunRepository;
  run_attempt?: number;
  actor?: { login: string };
}

export interface RunsResponse {
  runs: WorkflowRun[];
  total_count?: number;
}

// ── Queue ─────────────────────────────────────────────────────────────────────

export interface QueueItem {
  id: number;
  name: string;
  status: string;
  created_at: string;
  html_url: string;
  repository: string;
  workflow_name?: string;
  head_branch?: string;
}

export interface QueueResponse {
  queue: QueueItem[];
  count: number;
}

export interface CancelRunRequest {
  repo: string;
  run_id: number;
}

export interface CancelWorkflowRequest {
  workflow_name: string;
  repo?: string | null;
}

// ── Runners ───────────────────────────────────────────────────────────────────

export interface Runner {
  id: number;
  name: string;
  os: string;
  status: string;
  busy: boolean;
  labels: Array<{ name: string }>;
}

export interface RunnersResponse {
  runners: Runner[];
  total_count: number;
}

// ── Fleet ─────────────────────────────────────────────────────────────────────

export interface FleetStatusResponse {
  nodes: FleetNode[];
  summary: FleetSummary;
}

export interface FleetNode {
  name: string;
  host: string;
  status: string;
  runners: Runner[];
}

export interface FleetSummary {
  total_runners: number;
  online: number;
  offline: number;
  busy: number;
  idle: number;
}

// ── Agent Remediation / Providers ────────────────────────────────────────────

export interface AgentProvider {
  provider_id: string;
  label: string;
  execution_mode: string;
  dispatch_mode: string;
  notes: string;
  experimental: boolean;
  remote: boolean;
  editable: boolean;
}

export interface ProvidersResponse {
  providers: Record<string, AgentProvider>;
}

export interface ProviderAvailability {
  provider_id: string;
  available: boolean;
  status: string;
  detail: string;
}

export interface DispatchRequest {
  provider_id: string;
  run_id: number;
  repo: string;
}

export interface DispatchResponse {
  status: "queued" | "error";
  message: string;
  job_id?: string;
}

// ── Queue Diagnostics ─────────────────────────────────────────────────────────

export interface QueueDiagnoseResponse {
  stale_count: number;
  details: string;
  recommendations: string[];
}

// ── Auth / Session ────────────────────────────────────────────────────────────

export interface UserMe {
  login: string;
  name: string | null;
  avatar_url: string;
  html_url: string;
}

// ── Stats / Usage ─────────────────────────────────────────────────────────────

export interface StatsResponse {
  total_runs_today: number;
  failed_runs_today: number;
  success_rate: number;
  avg_duration_seconds: number;
}

export interface UsageResponse {
  usage: UsageEntry[];
}

export interface UsageEntry {
  repo: string;
  run_count: number;
  total_minutes: number;
}
