import { describe, expect, it } from "vitest";
import { parseRegistry, type ProviderRegistryResponse } from "../useProviderRegistry";
import {
  getDefaultModelForProvider,
  modelOptionsForDashboardProvider,
  providersWithModels,
} from "../providerModels";

const REGISTRY: ProviderRegistryResponse = {
  schema_version: "1.0.0",
  providers: [
    {
      id: "codex-cli",
      dashboard_id: "codex_cli",
      label: "Codex CLI",
      auth_mode: "github_app",
      resource: "runner",
      models: ["gpt-5", "o4-mini"],
      enabled: true,
    },
    {
      id: "jules-api",
      dashboard_id: "jules_api",
      label: "Jules API",
      auth_mode: "api_key",
      resource: "runner",
      models: ["x"],
      enabled: false,
    },
  ],
  auth_kinds: [],
  task_classes: [],
  capabilities: [],
};

describe("providerModels (registry-backed)", () => {
  const registry = parseRegistry(REGISTRY);

  it("derives model options from registry rows", () => {
    const models = modelOptionsForDashboardProvider(registry, "codex_cli");
    expect(models.map((m) => m.id)).toEqual(["gpt-5", "o4-mini"]);
    expect(getDefaultModelForProvider(registry, "codex_cli")).toBe("gpt-5");
  });

  it("providersWithModels ignores disabled providers", () => {
    expect(providersWithModels(registry)).toEqual(new Set(["codex_cli"]));
    expect(modelOptionsForDashboardProvider(registry, "jules_api")).toEqual([]);
  });
});
