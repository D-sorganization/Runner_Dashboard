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
  it("keeps the selected provider label and explains its global scope", async () => {
    localStorage.setItem(ACTIVE_PROVIDER_KEY, "claude-cli");
    render(<ActiveProviderControl registry={registry} />);
    const trigger = screen.getByRole("button", { name: "Provider: Claude CLI" });
    expect(trigger).toBeInTheDocument();
    fireEvent.mouseEnter(trigger);
    expect(await screen.findByRole("tooltip")).toHaveTextContent(
      "Global provider preference: Claude CLI. Staff conversations use each role's configured provider.",
    );
  });

  it("explains the unset global preference and role-level provider behavior", async () => {
    render(<ActiveProviderControl registry={registry} />);
    const trigger = screen.getByRole("button", { name: "Provider: not set" });
    expect(trigger).toBeInTheDocument();
    fireEvent.mouseEnter(trigger);
    const tooltip = await screen.findByRole("tooltip");
    expect(tooltip).toHaveTextContent(
      "Global provider preference is not set. Staff conversations use each role's configured provider.",
    );
    expect(trigger).toHaveAttribute("title", tooltip.textContent);
  });

  it("persists a new selection made through the popover", () => {
    render(<ActiveProviderControl registry={registry} />);
    fireEvent.click(screen.getByRole("button", { name: /provider/i }));
    fireEvent.change(screen.getByLabelText(/provider/i), { target: { value: "ollama-local" } });
    expect(localStorage.getItem(ACTIVE_PROVIDER_KEY)).toBe("ollama-local");
  });
});
