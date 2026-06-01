// @vitest-environment jsdom
/**
 * Tests for the persistent/global ActiveProviderControl surfaced in the shell
 * (issue #811). The user can see and change the active provider+model at all
 * times; the choice is persisted to localStorage and restored on mount.
 *
 * Contract:
 *  - reads/writes the active provider+model via the useActiveProvider hook,
 *    keyed on "dashboard.activeProvider" / "dashboard.activeModel";
 *  - shows the current active provider label as a compact trigger;
 *  - changing the selection persists to localStorage.
 */
import "@testing-library/jest-dom/vitest";
import { renderHook, act, render, screen, fireEvent, cleanup } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  useActiveProvider,
  ACTIVE_PROVIDER_KEY,
  ACTIVE_MODEL_KEY,
} from "../useActiveProvider";
import { ActiveProviderControl } from "../ActiveProviderControl";
import { parseRegistry, type ProviderRegistryResponse } from "../../lib/useProviderRegistry";

const CONTRACT: ProviderRegistryResponse = {
  schema_version: "1.0.0",
  providers: [
    {
      id: "claude-cli",
      dashboard_id: "claude_code_cli",
      label: "Claude CLI",
      auth_mode: "github_app",
      resource: "runner",
      capabilities: [],
      models: [],
      login_status: "authenticated",
    } as ProviderRegistryResponse["providers"][number],
    {
      id: "ollama-local",
      dashboard_id: "ollama",
      label: "Ollama",
      auth_mode: "local",
      resource: "local",
      capabilities: [],
      models: ["llama3.2", "mistral"],
      login_status: "authenticated",
    } as ProviderRegistryResponse["providers"][number],
  ],
  auth_kinds: [],
  task_classes: [],
  capabilities: [],
};
const registry = parseRegistry(CONTRACT);

beforeEach(() => {
  localStorage.clear();
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("useActiveProvider", () => {
  it("defaults to null when nothing is stored", () => {
    const { result } = renderHook(() => useActiveProvider());
    expect(result.current.active.providerId).toBeNull();
  });

  it("persists provider + model to localStorage", () => {
    const { result } = renderHook(() => useActiveProvider());
    act(() => {
      result.current.setActive({ providerId: "ollama-local", dashboardId: "ollama", model: "mistral" });
    });
    expect(localStorage.getItem(ACTIVE_PROVIDER_KEY)).toBe("ollama-local");
    expect(localStorage.getItem(ACTIVE_MODEL_KEY)).toBe("mistral");
  });

  it("restores a previously stored selection on mount", () => {
    localStorage.setItem(ACTIVE_PROVIDER_KEY, "ollama-local");
    localStorage.setItem(ACTIVE_MODEL_KEY, "llama3.2");
    const { result } = renderHook(() => useActiveProvider());
    expect(result.current.active.providerId).toBe("ollama-local");
    expect(result.current.active.model).toBe("llama3.2");
  });
});

describe("ActiveProviderControl", () => {
  it("shows the current active provider label", () => {
    localStorage.setItem(ACTIVE_PROVIDER_KEY, "claude-cli");
    render(<ActiveProviderControl registry={registry} />);
    expect(screen.getByText(/Claude CLI/i)).toBeInTheDocument();
  });

  it("shows a sensible default label when nothing is active", () => {
    render(<ActiveProviderControl registry={registry} />);
    // Falls back to the first authenticated provider's label or a placeholder.
    expect(screen.getByRole("button", { name: /provider/i })).toBeInTheDocument();
  });

  it("persists a new selection made through the popover", () => {
    render(<ActiveProviderControl registry={registry} />);
    fireEvent.click(screen.getByRole("button", { name: /provider/i }));
    fireEvent.change(screen.getByLabelText(/provider/i), { target: { value: "ollama-local" } });
    expect(localStorage.getItem(ACTIVE_PROVIDER_KEY)).toBe("ollama-local");
  });
});
