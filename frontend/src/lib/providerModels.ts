/**
 * Model options for remediation pickers — derived from the provider registry (#2715).
 */
import type { ProviderRegistry } from "./useProviderRegistry";

export interface ModelOption {
  id: string;
  label: string;
  default?: boolean;
}

export function modelOptionsForDashboardProvider(
  registry: ProviderRegistry,
  dashboardId: string,
): ModelOption[] {
  const row = registry.byDashboardId(dashboardId);
  if (!row?.models.length) return [];
  return row.models.map((id, index) => ({
    id,
    label: id,
    default: index === 0,
  }));
}

export function providersWithModels(registry: ProviderRegistry): Set<string> {
  return new Set(
    registry.selectableProviders.filter((p) => p.models.length > 0).map((p) => p.dashboardId),
  );
}

export function getDefaultModelForProvider(registry: ProviderRegistry, dashboardId: string): string {
  const models = modelOptionsForDashboardProvider(registry, dashboardId);
  if (!models.length) return "";
  const def = models.find((m) => m.default);
  return def ? def.id : models[0].id;
}
