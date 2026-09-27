/**
 * panelApi.ts — typed client for Expert Panels (/api/v1/staff/panels/*).
 *
 * Implements issue #1635 (epic #1633), backing backend branch #1634 (PR #1640).
 */
import { apiRequest } from "../../lib/api";
import type { components } from "../../lib/api-types";
import type { ThreadInfo } from "./threadTypes";

export type PanelCreateRequest = components["schemas"]["PanelCreateRequest"];
export type PanelExpert = components["schemas"]["PanelExpert"];

export interface PanelCostEstimate {
  group_id: string;
  total_cost_usd: number;
  cost_per_seat: Record<string, number>;
  exceeds_threshold: boolean;
  threshold_usd: number;
  warning?: string | null;
}

export interface PanelCreateResponse {
  thread: ThreadInfo;
  estimate: PanelCostEstimate;
}

export interface PanelPresetExpert {
  name: string;
  perspective: string;
}

export interface PanelPreset {
  id: string;
  title: string;
  experts: PanelPresetExpert[];
}

export interface PanelPresetsResponse {
  presets: PanelPreset[];
  providers: string[];
}

export interface PanelTurn {
  message_id: string;
  round: number;
  expert: string;
  status: "ok" | "error" | "timeout" | "running";
  stance?: "agree" | "partly" | "disagree" | null;
  position?: string | null;
  body_md?: string;
}

export interface PanelResult {
  thread_id: string;
  title?: string | null;
  status: "running" | "complete" | "failed";
  mode: "debate" | "brainstorm";
  rounds: number;
  rounds_used: number;
  consensus: boolean;
  experts: Array<{ name: string; perspective: string; provider?: string; model?: string | null }>;
  turns: PanelTurn[];
  synthesis?: string | null;
}

export class PanelCostRequired extends Error {
  readonly estimate: PanelCostEstimate | null;

  constructor(message: string, estimate?: PanelCostEstimate | null) {
    super(message);
    this.name = "PanelCostRequired";
    this.estimate = estimate ?? null;
  }
}

export const STAFF_PANELS_BASE = "/api/v1/staff/panels";

export function fetchPanelPresets(signal?: AbortSignal): Promise<PanelPresetsResponse> {
  return apiRequest<PanelPresetsResponse>(`${STAFF_PANELS_BASE}/presets`, { signal });
}

export function fetchPanel(threadId: string, signal?: AbortSignal): Promise<PanelResult> {
  return apiRequest<PanelResult>(`${STAFF_PANELS_BASE}/${encodeURIComponent(threadId)}`, { signal });
}

export async function createPanel(
  body: PanelCreateRequest,
  signal?: AbortSignal,
): Promise<PanelCreateResponse> {
  const resp = await fetch(STAFF_PANELS_BASE, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-Requested-With": "XMLHttpRequest",
    },
    body: JSON.stringify(body),
    signal,
  });

  if (resp.status === 202 || resp.ok) {
    return (await resp.json()) as PanelCreateResponse;
  }

  let parsed: unknown = null;
  try {
    parsed = await resp.json();
  } catch {
    // Non-JSON body
  }

  const raw = parsed as { error?: unknown; detail?: unknown } | null;
  const err = raw?.error ?? raw?.detail;

  if (resp.status === 400 && err && typeof err === "object") {
    const errObj = err as { code?: unknown; message?: unknown; estimate?: PanelCostEstimate };
    if (errObj.code === "group_cost_guard_threshold_exceeded") {
      const msg = typeof errObj.message === "string" ? errObj.message : "Cost threshold exceeded";
      throw new PanelCostRequired(msg, errObj.estimate ?? null);
    }
  }

  let message = "";
  if (typeof err === "string") {
    message = err;
  } else if (err && typeof err === "object") {
    const errObj = err as { message?: unknown };
    if (typeof errObj.message === "string") {
      message = errObj.message;
    } else if (Array.isArray(err)) {
      message = err
        .map((item: unknown) => {
          if (item && typeof item === "object" && "msg" in item && typeof (item as { msg: unknown }).msg === "string") {
            return (item as { msg: string }).msg;
          }
          return JSON.stringify(item);
        })
        .join("; ");
    } else {
      message = JSON.stringify(err);
    }
  }

  if (!message) {
    if (resp.status === 429) {
      message = "2 panels are already running";
    } else {
      message = `HTTP ${resp.status}`;
    }
  }

  throw new Error(message);
}
