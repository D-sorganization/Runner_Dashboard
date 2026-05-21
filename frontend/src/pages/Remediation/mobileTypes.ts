// Shared types, constants, and small helpers for the Remediation mobile view.
// Extracted from Mobile.tsx to keep that file under the 500-line cap.

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

export interface ProviderAvailability {
  provider_id: string;
  available: boolean;
  status: string;
  detail: string;
}

export interface FailedRun {
  id: number;
  name: string;
  workflow_name: string;
  head_branch: string;
  conclusion: string;
  html_url: string;
  created_at: string;
  run_number?: number;
  repository: { name: string; full_name?: string };
}

export interface OpenPR {
  id: number;
  number: number;
  title: string;
  html_url: string;
  head: { ref: string };
  base: { repo: { name: string; full_name?: string } };
  draft: boolean;
  labels: Array<{ name: string }>;
  updated_at: string;
}

export interface OpenIssue {
  id: number;
  number: number;
  title: string;
  html_url: string;
  repository_url: string;
  labels: Array<{ name: string }>;
  updated_at: string;
}

export type RemediationSubtab = "automations" | "prs" | "issues";

export interface InFlightDispatch {
  id: string;
  itemId: number;
  itemTitle: string;
  provider: string;
  providerLabel: string;
  repository: string;
  startedAt: number;
  lastHeartbeat: number;
  status: "dispatched" | "running" | "done" | "error";
  fingerprint?: string;
}

export interface ActionSheetItem {
  id: number;
  title: string;
  htmlUrl: string;
  repository: string;
  workflowName?: string;
  branch?: string;
  runId?: number;
}

export const SUBTAB_OPTIONS = [
  { label: "Automations", value: "automations" },
  { label: "PRs", value: "prs" },
  { label: "Issues", value: "issues" },
];

export const DEFAULT_PROVIDER_ORDER = [
  "jules_api",
  "codex_cli",
  "claude_code_cli",
  "gemini_cli",
  "ollama",
  "cline",
];

export function pickRecommendedProvider(
  providers: Record<string, AgentProvider>,
  availability: Record<string, ProviderAvailability>,
): string {
  for (const id of DEFAULT_PROVIDER_ORDER) {
    if (providers[id] && availability[id]?.available) return id;
  }
  const first = Object.keys(providers).find(
    (id) => availability[id]?.available,
  );
  return first ?? "claude_code_cli";
}

export function getProviderLabel(
  providers: Record<string, AgentProvider>,
  providerId: string,
): string {
  return providers[providerId]?.label ?? providerId;
}

export function elapsedLabel(startedAt: number): string {
  const secs = Math.floor((Date.now() - startedAt) / 1000);
  if (secs < 60) return `${secs}s`;
  const mins = Math.floor(secs / 60);
  if (mins < 60) return `${mins}m ${secs % 60}s`;
  return `${Math.floor(mins / 60)}h ${mins % 60}m`;
}
