import { describe, expect, it } from "vitest";
import { parseRegistry, type ProviderRegistryResponse } from "../useProviderRegistry";
import {
  buildRemediationProviderEntries,
  coerceRemediationProvider,
  selectableDashboardIds,
} from "../remediationProviders";

const REGISTRY_FIXTURE: ProviderRegistryResponse = {
  schema_version: "1.0.0",
  providers: [
    {
      id: "codex-cli",
      dashboard_id: "codex_cli",
      label: "Codex CLI",
      auth_mode: "github_app",
      resource: "runner",
      enabled: true,
    },
    {
      id: "jules-api",
      dashboard_id: "jules_api",
      label: "Jules API",
      auth_mode: "api_key",
      resource: "runner",
      enabled: false,
    },
    {
      id: "claude-cli",
      dashboard_id: "claude_code_cli",
      label: "Claude CLI",
      auth_mode: "github_app",
      resource: "runner",
      enabled: true,
    },
  ],
  auth_kinds: [],
  task_classes: [],
  capabilities: [],
};

describe("remediationProviders", () => {
  const registry = parseRegistry(REGISTRY_FIXTURE);

  it("selectableDashboardIds omits disabled registry rows", () => {
    expect(selectableDashboardIds(registry)).toEqual(["codex_cli", "claude_code_cli"]);
  });

  it("coerceRemediationProvider rejects retired jules defaults", () => {
    expect(coerceRemediationProvider(registry, "jules_api")).toBe("codex_cli");
    expect(coerceRemediationProvider(registry, "claude_code_cli")).toBe("claude_code_cli");
  });

  it("buildRemediationProviderEntries filters config providers to enabled ids", () => {
    const entries = buildRemediationProviderEntries(registry, {
      jules_api: { label: "Jules API" },
      codex_cli: { label: "Codex" },
      claude_code_cli: { label: "Claude" },
    });
    expect(entries.map(([id]) => id)).toEqual(["codex_cli", "claude_code_cli"]);
  });
});
