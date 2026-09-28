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

/** One row of the `/api/prs` / `/api/issues` inventories: flat, labels as names. */
export interface InventoryItem {
  repository?: string;
  number?: number;
  title?: string;
  url?: string;
  draft?: boolean;
  labels?: string[];
  head_ref?: string;
  updated_at?: string;
}

/** The inventory rows of a `/api/prs` or `/api/issues` payload (`{items}` or a bare list). */
export function inventoryItems(payload: unknown): InventoryItem[] {
  if (Array.isArray(payload)) return payload as InventoryItem[];
  const items = (payload as { items?: unknown } | null)?.items;
  return Array.isArray(items) ? (items as InventoryItem[]) : [];
}

/** Stable numeric id for an inventory row; list keys and in-flight tracking use numbers. */
export function inventoryItemId(repository: string, number: number): number {
  let hash = 0;
  for (const ch of `${repository}#${number}`) hash = (hash * 31 + ch.charCodeAt(0)) | 0;
  return Math.abs(hash);
}

/** Adapt an `/api/prs` row to the card's PR shape. */
export function prFromInventory(item: InventoryItem): OpenPR {
  const repository = item.repository || "";
  const number = item.number ?? 0;
  return {
    id: inventoryItemId(repository, number),
    number,
    title: item.title || "",
    html_url: item.url || "",
    head: { ref: item.head_ref || "" },
    base: { repo: { name: repository.split("/").pop() || repository, full_name: repository } },
    draft: Boolean(item.draft),
    labels: (item.labels || []).map((name) => ({ name })),
    updated_at: item.updated_at || "",
  };
}

/** Adapt an `/api/issues` row to the card's issue shape. */
export function issueFromInventory(item: InventoryItem): OpenIssue {
  const repository = item.repository || "";
  const number = item.number ?? 0;
  return {
    id: inventoryItemId(repository, number),
    number,
    title: item.title || "",
    html_url: item.url || "",
    repository_url: `https://api.github.com/repos/${repository}`,
    labels: (item.labels || []).map((name) => ({ name })),
    updated_at: item.updated_at || "",
  };
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
  workItemId?: string;
  model?: string | null;
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
