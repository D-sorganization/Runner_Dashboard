/**
 * Remediation UI provider helpers — registry-backed (issue #2715).
 *
 * All pickers read ``GET /api/providers/registry`` via ``ProviderRegistry`` and
 * honor ``enabled`` rows only. No duplicate order lists or hardcoded Jules defaults.
 */
import type { ProviderRegistry } from "./useProviderRegistry";

export const FALLBACK_REMEDIATION_PROVIDER = "codex_cli";

/** Dashboard ids for enabled registry rows, in registry table order. */
export function selectableDashboardIds(registry: ProviderRegistry): string[] {
  return registry.selectableProviders.map((p) => p.dashboardId);
}

export function coerceRemediationProvider(
  registry: ProviderRegistry | null,
  preferred: string | null | undefined,
): string {
  const fallback = FALLBACK_REMEDIATION_PROVIDER;
  if (!registry) {
    if (preferred && preferred !== "jules_api" && preferred !== "jules_cli") {
      return preferred;
    }
    return fallback;
  }
  const allowed = selectableDashboardIds(registry);
  if (preferred && allowed.includes(preferred)) return preferred;
  return allowed[0] ?? fallback;
}

export function policyDefaultProvider(
  policy: { default_provider?: string | null },
  registry: ProviderRegistry | null,
): string {
  return coerceRemediationProvider(registry, policy.default_provider);
}

export type RemediationProviderMeta = { label: string; notes: string };

/** Provider `<option>` rows: enabled registry ids merged with config labels. */
export function buildRemediationProviderEntries(
  registry: ProviderRegistry | null,
  configProviders: Record<string, { label?: string; notes?: string }>,
): Array<[string, RemediationProviderMeta]> {
  const order = registry ? selectableDashboardIds(registry) : [];
  const configIds = Object.keys(configProviders).filter((id) =>
    registry ? order.includes(id) : id !== "jules_api" && id !== "jules_cli",
  );
  const ids = configIds.length
    ? [...order.filter((id) => configIds.includes(id)), ...configIds.filter((id) => !order.includes(id))]
    : order;
  return ids.map((id) => {
    const fromConfig = configProviders[id];
    const row = registry?.byDashboardId(id);
    return [
      id,
      {
        label: row?.label || fromConfig?.label || id,
        notes: fromConfig?.notes || "",
      },
    ];
  });
}

export function providerSortIndex(registry: ProviderRegistry | null, dashboardId: string): number {
  if (!registry) return Number.MAX_SAFE_INTEGER;
  const idx = selectableDashboardIds(registry).indexOf(dashboardId);
  return idx === -1 ? Number.MAX_SAFE_INTEGER : idx;
}

export function pickRecommendedRemediationProvider(
  registry: ProviderRegistry | null,
  providers: Record<string, unknown>,
  availability: Record<string, { available?: boolean }>,
): string {
  const order = registry ? selectableDashboardIds(registry) : [];
  for (const id of order) {
    if (providers[id] && availability[id]?.available) return id;
  }
  const first = Object.keys(providers).find(
    (id) =>
      availability[id]?.available &&
      (!registry || registry.byDashboardId(id)?.enabled !== false),
  );
  return first ?? FALLBACK_REMEDIATION_PROVIDER;
}
