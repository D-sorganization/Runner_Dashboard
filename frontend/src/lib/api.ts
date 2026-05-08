/**
 * Typed API client for the Runner Dashboard backend (issue #376).
 *
 * Wraps fetch() with:
 * - AbortController plumbing (via signal option)
 * - Typed request/response shapes from api-types.ts
 * - Structured typed errors (ApiClientError) surfacing 4xx/5xx
 * - X-Requested-With header on every request (CSRF protection)
 *
 * Usage:
 *   const runs = await api.runs.list({ per_page: 30 });
 *   const queue = await api.queue.get({ signal: controller.signal });
 */

import type {
  ApiError,
  RunsResponse,
  QueueResponse,
  QueueDiagnoseResponse,
  RunnersResponse,
  FleetStatusResponse,
  ProvidersResponse,
  DispatchRequest,
  DispatchResponse,
  CancelWorkflowRequest,
  UserMe,
  StatsResponse,
  UsageResponse,
} from "./api-types";

// Re-export types so consumers can import from a single location.
export type {
  ApiError,
  RunsResponse,
  WorkflowRun,
  QueueResponse,
  QueueDiagnoseResponse,
  RunnersResponse,
  FleetStatusResponse,
  ProvidersResponse,
  ProviderAvailability,
  DispatchRequest,
  DispatchResponse,
  CancelWorkflowRequest,
  UserMe,
  StatsResponse,
  UsageResponse,
} from "./api-types";

// ── Error type ────────────────────────────────────────────────────────────────

export class ApiClientError extends Error {
  constructor(
    public readonly status: number,
    public readonly detail: string,
    public readonly url: string,
  ) {
    super(`API ${status} — ${detail} (${url})`);
    this.name = "ApiClientError";
  }
}

// ── Base fetch helper ─────────────────────────────────────────────────────────

interface FetchOptions {
  signal?: AbortSignal;
  body?: unknown;
  method?: string;
  headers?: Record<string, string>;
}

const DEFAULT_HEADERS: Record<string, string> = {
  "X-Requested-With": "XMLHttpRequest",
};

async function request<T>(
  url: string,
  { signal, body, method = body !== undefined ? "POST" : "GET", headers = {} }: FetchOptions = {},
): Promise<T> {
  const init: RequestInit = {
    method,
    signal,
    headers: {
      ...DEFAULT_HEADERS,
      ...(body !== undefined ? { "Content-Type": "application/json" } : {}),
      ...headers,
    },
    ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
  };

  const resp = await fetch(url, init);

  if (!resp.ok) {
    let detail = `HTTP ${resp.status}`;
    try {
      const errBody = (await resp.json()) as ApiError;
      detail = errBody.detail ?? detail;
    } catch {
      // Non-JSON error body — use status text
    }
    throw new ApiClientError(resp.status, detail, url);
  }

  // 204 No Content — return empty object as T
  if (resp.status === 204) {
    return {} as T;
  }

  return resp.json() as Promise<T>;
}

// ── API surface ───────────────────────────────────────────────────────────────

interface ListRunsParams {
  per_page?: number;
  repo?: string;
  signal?: AbortSignal;
}

interface ListQueueParams {
  signal?: AbortSignal;
}

interface CancelRunParams {
  repo: string;
  run_id: number;
  signal?: AbortSignal;
}

interface RerunParams {
  repo: string;
  run_id: number;
  signal?: AbortSignal;
}

export const api = {
  // ── Runs ──────────────────────────────────────────────────────────────────
  runs: {
    list({ per_page = 30, signal }: ListRunsParams = {}): Promise<RunsResponse> {
      const params = new URLSearchParams({ per_page: String(per_page) });
      return request<RunsResponse>(`/api/runs?${params}`, { signal });
    },
    cancel({ repo, run_id, signal }: CancelRunParams): Promise<void> {
      return request<void>(`/api/runs/${encodeURIComponent(repo)}/cancel/${run_id}`, {
        method: "POST",
        signal,
      });
    },
    rerun({ repo, run_id, signal }: RerunParams): Promise<void> {
      return request<void>(`/api/runs/${encodeURIComponent(repo)}/rerun/${run_id}`, {
        method: "POST",
        signal,
      });
    },
  },

  // ── Queue ─────────────────────────────────────────────────────────────────
  queue: {
    get({ signal }: ListQueueParams = {}): Promise<QueueResponse> {
      return request<QueueResponse>("/api/queue", { signal });
    },
    diagnose({ signal }: { signal?: AbortSignal } = {}): Promise<QueueDiagnoseResponse> {
      return request<QueueDiagnoseResponse>("/api/queue/diagnose", { signal });
    },
    cancelWorkflow(
      body: CancelWorkflowRequest,
      { signal }: { signal?: AbortSignal } = {},
    ): Promise<void> {
      return request<void>("/api/queue/cancel-workflow", { body, signal });
    },
  },

  // ── Runners ───────────────────────────────────────────────────────────────
  runners: {
    list({ signal }: { signal?: AbortSignal } = {}): Promise<RunnersResponse> {
      return request<RunnersResponse>("/api/runners", { signal });
    },
  },

  // ── Fleet ─────────────────────────────────────────────────────────────────
  fleet: {
    status({ signal }: { signal?: AbortSignal } = {}): Promise<FleetStatusResponse> {
      return request<FleetStatusResponse>("/api/fleet/status", { signal });
    },
  },

  // ── Agent Remediation ─────────────────────────────────────────────────────
  agentRemediation: {
    providers({ signal }: { signal?: AbortSignal } = {}): Promise<ProvidersResponse> {
      // Correct endpoint: /api/agents/providers (issue #376 — fixes drift from /api/agent-remediation/providers)
      return request<ProvidersResponse>("/api/agents/providers", { signal });
    },
    dispatch(
      body: DispatchRequest,
      { signal }: { signal?: AbortSignal } = {},
    ): Promise<DispatchResponse> {
      return request<DispatchResponse>("/api/agent-remediation/dispatch", { body, signal });
    },
  },

  // ── Auth ──────────────────────────────────────────────────────────────────
  auth: {
    me({ signal }: { signal?: AbortSignal } = {}): Promise<UserMe> {
      return request<UserMe>("/api/auth/me", { signal });
    },
  },

  // ── Stats ─────────────────────────────────────────────────────────────────
  stats: {
    get({ signal }: { signal?: AbortSignal } = {}): Promise<StatsResponse> {
      return request<StatsResponse>("/api/stats", { signal });
    },
  },

  // ── Usage ─────────────────────────────────────────────────────────────────
  usage: {
    get({ signal }: { signal?: AbortSignal } = {}): Promise<UsageResponse> {
      return request<UsageResponse>("/api/usage", { signal });
    },
  },
} as const;
