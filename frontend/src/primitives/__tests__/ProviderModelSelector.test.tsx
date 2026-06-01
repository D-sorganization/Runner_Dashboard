// @vitest-environment jsdom
/**
 * Tests for ProviderModelSelector — the reusable cascading provider->model
 * picker (issue #811, epic #809; login UX from #812).
 *
 * Contract:
 *  - renders one option per provider from the injected registry;
 *  - the model picker is hidden/disabled until a provider with models[] is chosen;
 *  - choosing Ollama populates its live models;
 *  - choosing a provider with empty models[] hides the model picker;
 *  - onChange emits {providerId, dashboardId, model};
 *  - DbC invariant: a model can never be emitted without a provider;
 *  - each provider renders its login_status with a fix affordance (#812).
 */
import "@testing-library/jest-dom/vitest";
import React from "react";
import { cleanup, render, screen, fireEvent, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ProviderModelSelector } from "../ProviderModelSelector";
import { parseRegistry, type ProviderRegistryResponse } from "../../lib/useProviderRegistry";

afterEach(cleanup);

const CONTRACT: ProviderRegistryResponse = {
  schema_version: "1.0.0",
  providers: [
    {
      id: "claude-cli",
      dashboard_id: "claude_code_cli",
      label: "Claude CLI",
      auth_mode: "github_app",
      resource: "runner",
      capabilities: ["code"],
      models: [],
      models_endpoint: null,
      login_status: "authenticated",
      setup_hint: "",
      experimental: false,
    } as ProviderRegistryResponse["providers"][number],
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
    {
      id: "codex-cli",
      dashboard_id: "codex_cli",
      label: "Codex CLI",
      auth_mode: "api_key",
      resource: "runner",
      capabilities: ["code"],
      models: [],
      login_status: "unauthenticated",
      setup_hint: "Add your OpenAI API key in Credentials.",
      experimental: false,
    } as ProviderRegistryResponse["providers"][number],
  ],
  auth_kinds: [],
  task_classes: [],
  capabilities: [],
};

const registry = parseRegistry(CONTRACT);

describe("ProviderModelSelector — cascading", () => {
  it("renders one option per provider", () => {
    render(<ProviderModelSelector registry={registry} onChange={vi.fn()} />);
    const providerSelect = screen.getByLabelText(/provider/i);
    expect(within(providerSelect).getByRole("option", { name: /Claude CLI/i })).toBeInTheDocument();
    expect(within(providerSelect).getByRole("option", { name: /Ollama/i })).toBeInTheDocument();
    expect(within(providerSelect).getByRole("option", { name: /Codex CLI/i })).toBeInTheDocument();
  });

  it("hides the model picker until a provider is chosen", () => {
    render(<ProviderModelSelector registry={registry} onChange={vi.fn()} />);
    expect(screen.queryByLabelText(/model/i)).not.toBeInTheDocument();
  });

  it("populates Ollama's live models after selecting Ollama", () => {
    render(<ProviderModelSelector registry={registry} onChange={vi.fn()} />);
    fireEvent.change(screen.getByLabelText(/provider/i), { target: { value: "ollama-local" } });
    const modelSelect = screen.getByLabelText(/model/i);
    expect(within(modelSelect).getByRole("option", { name: /llama3.2/i })).toBeInTheDocument();
    expect(within(modelSelect).getByRole("option", { name: /mistral/i })).toBeInTheDocument();
  });

  it("hides the model picker for a provider with empty models[]", () => {
    render(<ProviderModelSelector registry={registry} onChange={vi.fn()} />);
    fireEvent.change(screen.getByLabelText(/provider/i), { target: { value: "claude-cli" } });
    expect(screen.queryByLabelText(/model/i)).not.toBeInTheDocument();
  });

  it("emits {providerId, dashboardId, model} on provider change (model defaults to null for model-less providers)", () => {
    const onChange = vi.fn();
    render(<ProviderModelSelector registry={registry} onChange={onChange} />);
    fireEvent.change(screen.getByLabelText(/provider/i), { target: { value: "claude-cli" } });
    expect(onChange).toHaveBeenCalledWith({
      providerId: "claude-cli",
      dashboardId: "claude_code_cli",
      model: null,
    });
  });

  it("emits the chosen model for a provider with models", () => {
    const onChange = vi.fn();
    render(<ProviderModelSelector registry={registry} onChange={onChange} />);
    fireEvent.change(screen.getByLabelText(/provider/i), { target: { value: "ollama-local" } });
    // First model is auto-selected as default.
    expect(onChange).toHaveBeenLastCalledWith({
      providerId: "ollama-local",
      dashboardId: "ollama",
      model: "llama3.2",
    });
    fireEvent.change(screen.getByLabelText(/model/i), { target: { value: "mistral" } });
    expect(onChange).toHaveBeenLastCalledWith({
      providerId: "ollama-local",
      dashboardId: "ollama",
      model: "mistral",
    });
  });

  it("never emits a model without a provider (DbC invariant)", () => {
    const onChange = vi.fn();
    render(<ProviderModelSelector registry={registry} onChange={onChange} />);
    // No provider chosen yet → no model picker, no emission.
    expect(onChange).not.toHaveBeenCalled();
    expect(screen.queryByLabelText(/model/i)).not.toBeInTheDocument();
  });
});

describe("ProviderModelSelector — login status (#812)", () => {
  it("renders the login status of the selected provider", () => {
    render(<ProviderModelSelector registry={registry} onChange={vi.fn()} />);
    fireEvent.change(screen.getByLabelText(/provider/i), { target: { value: "codex-cli" } });
    expect(screen.getByText(/unauthenticated/i)).toBeInTheDocument();
  });

  it("offers a fix affordance (link to Credentials) for an unauthenticated provider", () => {
    render(<ProviderModelSelector registry={registry} onChange={vi.fn()} />);
    fireEvent.change(screen.getByLabelText(/provider/i), { target: { value: "codex-cli" } });
    const fix = screen.getByRole("button", { name: /fix login|set up|credentials/i });
    expect(fix).toBeInTheDocument();
  });

  it("calls onRequestLogin with the provider when the fix affordance is clicked", () => {
    const onRequestLogin = vi.fn();
    render(
      <ProviderModelSelector registry={registry} onChange={vi.fn()} onRequestLogin={onRequestLogin} />,
    );
    fireEvent.change(screen.getByLabelText(/provider/i), { target: { value: "codex-cli" } });
    fireEvent.click(screen.getByRole("button", { name: /fix login|set up|credentials/i }));
    expect(onRequestLogin).toHaveBeenCalledWith("codex-cli");
  });

  it("honors a controlled value prop", () => {
    render(
      <ProviderModelSelector
        registry={registry}
        value={{ providerId: "ollama-local", dashboardId: "ollama", model: "mistral" }}
        onChange={vi.fn()}
      />,
    );
    expect(screen.getByLabelText(/provider/i)).toHaveValue("ollama-local");
    expect(screen.getByLabelText(/model/i)).toHaveValue("mistral");
  });
});
