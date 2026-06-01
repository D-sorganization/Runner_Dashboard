/**
 * useProviderRegistry — the SINGLE frontend source of truth for the unified
 * provider/model registry (issue #811, epic #809).
 *
 * Background: the dashboard previously carried THREE duplicate, drifting
 * provider lists (AgentDispatch's DEFAULT_PROVIDER_ORDER, Conductor's
 * provider_mix labels, and Credentials' PROVIDER_MAP) with mismatched ids
 * (claude_code_cli vs claude-cli, ollama vs ollama-local). This hook fetches
 * the backend's pinned `GET /api/providers/registry` once and exposes ONE
 * typed model mirroring that contract, plus id-mapping helpers that resolve the
 * underscore<->hyphen mismatch — so every consumer reads the same data (DRY).
 *
 * LoD: callers receive a flat typed `ProviderRegistry` with simple lookup
 * helpers; they never reach into the raw wire shape. `fetchImpl` is injectable
 * so tests drive the hook without touching the network.
 */
import { useEffect, useMemo, useRef, useState } from "react";

/** Pinned contract: GET /api/providers/registry. */
export const PROVIDER_REGISTRY_ENDPOINT = "/api/providers/registry";

/** A provider's login state, surfaced for the per-provider login UX (#812). */
export type LoginStatus = "authenticated" | "unauthenticated" | "error";

/** Raw provider record as it arrives on the wire (optional fields tolerated). */
export interface RawProvider {
  id: string;
  dashboard_id: string;
  label: string;
  execution_mode?: string;
  dispatch_mode?: string;
  auth_mode: string;
  resource: string;
  capabilities?: string[];
  cost_per_task?: number;
  max_concurrency?: number;
  models?: string[];
  models_endpoint?: string | null;
  login_status?: LoginStatus;
  login_detail?: string;
  setup_hint?: string;
  experimental?: boolean;
  editable?: boolean;
  remote?: boolean;
}

/** Raw top-level registry response. */
export interface ProviderRegistryResponse {
  schema_version: string;
  providers: RawProvider[];
  auth_kinds: string[];
  task_classes: string[];
  capabilities: string[];
}

/** Normalized, camelCased provider — the single frontend type consumers use. */
export interface Provider {
  /** Canonical registry id (hyphenated), e.g. "claude-cli". */
  id: string;
  /** Dashboard-side id (underscored), e.g. "claude_code_cli". */
  dashboardId: string;
  label: string;
  executionMode: string;
  dispatchMode: string;
  authMode: string;
  resource: string;
  capabilities: string[];
  costPerTask: number;
  maxConcurrency: number;
  models: string[];
  modelsEndpoint: string | null;
  loginStatus: LoginStatus;
  loginDetail: string;
  setupHint: string;
  experimental: boolean;
  editable: boolean;
  remote: boolean;
}

/** Normalized registry with lookup helpers. */
export interface ProviderRegistry {
  schemaVersion: string;
  providers: Provider[];
  authKinds: string[];
  taskClasses: string[];
  capabilities: string[];
  /** Resolve by canonical (hyphenated) id. */
  byId(id: string): Provider | undefined;
  /** Resolve by dashboard (underscored) id. */
  byDashboardId(dashboardId: string): Provider | undefined;
  /** Models for a provider (by canonical id); [] if none/unknown. */
  modelsFor(id: string): string[];
}

function normalizeProvider(raw: RawProvider): Provider {
  return {
    id: raw.id,
    dashboardId: raw.dashboard_id,
    label: raw.label || raw.id,
    executionMode: raw.execution_mode ?? "",
    dispatchMode: raw.dispatch_mode ?? "",
    authMode: raw.auth_mode ?? "",
    resource: raw.resource ?? "",
    capabilities: raw.capabilities ?? [],
    costPerTask: raw.cost_per_task ?? 0,
    maxConcurrency: raw.max_concurrency ?? 1,
    models: raw.models ?? [],
    modelsEndpoint: raw.models_endpoint ?? null,
    loginStatus: raw.login_status ?? "unauthenticated",
    loginDetail: raw.login_detail ?? "",
    setupHint: raw.setup_hint ?? "",
    experimental: raw.experimental ?? false,
    editable: raw.editable ?? false,
    remote: raw.remote ?? false,
  };
}

/**
 * Parse the raw wire response into the normalized registry.
 *
 * Pre: `raw` follows the pinned contract (providers[] present).
 * Post: every provider is normalized; lookups are O(1) via prebuilt maps.
 */
export function parseRegistry(raw: ProviderRegistryResponse): ProviderRegistry {
  const providers = (raw.providers ?? []).map(normalizeProvider);
  const byIdMap = new Map(providers.map((p) => [p.id, p]));
  const byDashboardIdMap = new Map(providers.map((p) => [p.dashboardId, p]));
  return {
    schemaVersion: raw.schema_version ?? "",
    providers,
    authKinds: raw.auth_kinds ?? [],
    taskClasses: raw.task_classes ?? [],
    capabilities: raw.capabilities ?? [],
    byId: (id) => byIdMap.get(id),
    byDashboardId: (dashboardId) => byDashboardIdMap.get(dashboardId),
    modelsFor: (id) => byIdMap.get(id)?.models ?? [],
  };
}

export interface UseProviderRegistryOptions {
  /** Injectable fetch (tests). Defaults to global fetch. */
  fetchImpl?: typeof fetch;
}

export interface UseProviderRegistryResult {
  registry: ProviderRegistry | null;
  loading: boolean;
  error: string | null;
}

/**
 * Fetch the provider registry once on mount.
 *
 * LoD: returns a flat result object; consumers never see the raw Response.
 */
export function useProviderRegistry(
  options: UseProviderRegistryOptions = {},
): UseProviderRegistryResult {
  const fetchImpl = options.fetchImpl ?? fetch;
  const [registry, setRegistry] = useState<ProviderRegistry | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  // Pin the fetch impl for the lifetime of the hook so the effect runs once.
  const fetchRef = useRef(fetchImpl);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    fetchRef
      .current(PROVIDER_REGISTRY_ENDPOINT)
      .then(async (resp) => {
        if (!resp.ok) throw new Error(`Registry HTTP ${resp.status}`);
        const data = (await resp.json()) as ProviderRegistryResponse;
        if (cancelled) return;
        setRegistry(parseRegistry(data));
      })
      .catch((e: unknown) => {
        if (cancelled) return;
        setRegistry(null);
        setError(e instanceof Error ? e.message : String(e));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return useMemo(
    () => ({ registry, loading, error }),
    [registry, loading, error],
  );
}
