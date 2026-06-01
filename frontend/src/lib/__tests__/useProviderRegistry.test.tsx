// @vitest-environment jsdom
/**
 * Tests for useProviderRegistry — the single frontend source of truth for the
 * provider/model registry (issue #811, epic #809).
 *
 * Contract:
 *  - fetches GET /api/providers/registry once (fetch is injectable for tests);
 *  - parses the pinned contract into a typed ProviderRegistry;
 *  - exposes helpers: byId / byDashboardId / modelsFor;
 *  - tolerates missing optional fields (defensive parsing);
 *  - surfaces loading and error states.
 */
import "@testing-library/jest-dom/vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  useProviderRegistry,
  parseRegistry,
  type ProviderRegistryResponse,
} from "../useProviderRegistry";

afterEach(() => {
  vi.restoreAllMocks();
});

const CONTRACT: ProviderRegistryResponse = {
  schema_version: "1.0.0",
  providers: [
    {
      id: "claude-cli",
      dashboard_id: "claude_code_cli",
      label: "Claude CLI",
      execution_mode: "cli",
      dispatch_mode: "workflow",
      auth_mode: "github_app",
      resource: "runner",
      capabilities: ["code"],
      cost_per_task: 0.05,
      max_concurrency: 1,
      models: [],
      models_endpoint: null,
      login_status: "authenticated",
      login_detail: "",
      setup_hint: "",
      experimental: false,
      editable: true,
      remote: false,
    },
    {
      id: "ollama-local",
      dashboard_id: "ollama",
      label: "Ollama",
      auth_mode: "local",
      resource: "local",
      capabilities: ["code"],
      models: ["llama3.2", "mistral"],
      models_endpoint: "http://localhost:11434/api/tags",
      login_status: "authenticated",
      experimental: true,
    } as ProviderRegistryResponse["providers"][number],
  ],
  auth_kinds: ["github_app", "local"],
  task_classes: ["remediation"],
  capabilities: ["code"],
};

function fakeFetch(data: unknown, ok = true) {
  return vi.fn(() =>
    Promise.resolve({
      ok,
      status: ok ? 200 : 500,
      json: () => Promise.resolve(data),
    } as Response),
  );
}

describe("parseRegistry", () => {
  it("parses the pinned contract into typed providers", () => {
    const reg = parseRegistry(CONTRACT);
    expect(reg.providers).toHaveLength(2);
    expect(reg.providers[0].id).toBe("claude-cli");
    expect(reg.providers[0].dashboardId).toBe("claude_code_cli");
    expect(reg.schemaVersion).toBe("1.0.0");
  });

  it("defaults missing optional fields defensively", () => {
    const reg = parseRegistry(CONTRACT);
    const ollama = reg.providers[1];
    expect(ollama.costPerTask).toBe(0);
    expect(ollama.loginDetail).toBe("");
    expect(ollama.setupHint).toBe("");
    expect(ollama.remote).toBe(false);
    expect(ollama.models).toEqual(["llama3.2", "mistral"]);
  });

  it("byId and byDashboardId resolve the underscore<->hyphen id mismatch", () => {
    const reg = parseRegistry(CONTRACT);
    expect(reg.byId("claude-cli")?.label).toBe("Claude CLI");
    expect(reg.byDashboardId("claude_code_cli")?.id).toBe("claude-cli");
    expect(reg.byDashboardId("ollama")?.id).toBe("ollama-local");
  });

  it("modelsFor returns the provider's models list (empty for non-model providers)", () => {
    const reg = parseRegistry(CONTRACT);
    expect(reg.modelsFor("claude-cli")).toEqual([]);
    expect(reg.modelsFor("ollama-local")).toEqual(["llama3.2", "mistral"]);
  });
});

describe("useProviderRegistry", () => {
  it("fetches the registry once on mount", async () => {
    const fetchImpl = fakeFetch(CONTRACT);
    const { result } = renderHook(() => useProviderRegistry({ fetchImpl }));
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(fetchImpl).toHaveBeenCalledTimes(1);
    expect(fetchImpl).toHaveBeenCalledWith("/api/providers/registry");
    expect(result.current.registry?.providers).toHaveLength(2);
    expect(result.current.error).toBeNull();
  });

  it("surfaces an error when the request fails", async () => {
    const fetchImpl = fakeFetch({}, false);
    const { result } = renderHook(() => useProviderRegistry({ fetchImpl }));
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.error).toBeTruthy();
    expect(result.current.registry).toBeNull();
  });
});
