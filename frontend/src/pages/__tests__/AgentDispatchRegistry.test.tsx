// @vitest-environment jsdom
/**
 * Tests for the AgentDispatch refactor onto the unified provider registry
 * (issue #811). These assert the DRY win — the old hardcoded
 * DEFAULT_PROVIDER_ORDER is gone and providers come from
 * GET /api/providers/registry — and that the selected MODEL flows through to
 * the dispatch payload.
 */
import "@testing-library/jest-dom/vitest";
import React from "react";
import { cleanup, render, screen, fireEvent, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AgentDispatchPage } from "../AgentDispatch";

afterEach(cleanup);
beforeEach(() => {
  vi.spyOn(console, "error").mockImplementation(() => {});
});
afterEach(() => vi.restoreAllMocks());

const REGISTRY = {
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
      experimental: false,
      remote: true,
      editable: false,
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
    },
  ],
  auth_kinds: [],
  task_classes: [],
  capabilities: [],
};

const RUNS = {
  workflow_runs: [
    {
      id: 1001,
      name: "CI Build",
      workflow_name: "ci.yml",
      head_branch: "main",
      conclusion: "failure",
      html_url: "https://x/1001",
      created_at: "2026-05-01T10:00:00Z",
      run_number: 42,
      repository: { name: "runner-dashboard" },
    },
  ],
};

let dispatchBody: Record<string, unknown> | null = null;

function setupFetch() {
  dispatchBody = null;
  global.fetch = vi.fn((url: string, init?: RequestInit) => {
    if (url.includes("/api/providers/registry")) {
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(REGISTRY) } as Response);
    }
    if (url.includes("/api/runs")) {
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(RUNS) } as Response);
    }
    if (url.includes("/api/agent-remediation/dispatch")) {
      dispatchBody = init?.body ? JSON.parse(init.body as string) : null;
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({ note: "ok" }) } as Response);
    }
    return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({}) } as Response);
  }) as unknown as typeof fetch;
}

describe("AgentDispatch — unified registry", () => {
  it("populates providers from GET /api/providers/registry (not a hardcoded list)", async () => {
    setupFetch();
    render(<AgentDispatchPage />);
    await waitFor(() => {
      expect(screen.getByText(/Claude CLI/i)).toBeInTheDocument();
      expect(screen.getByText(/Ollama/i)).toBeInTheDocument();
    });
    const calledUrls = (global.fetch as ReturnType<typeof vi.fn>).mock.calls.map((c) => c[0]);
    expect(calledUrls.some((u: string) => u.includes("/api/providers/registry"))).toBe(true);
  });

  it("includes the selected model in the dispatch payload", async () => {
    setupFetch();
    render(<AgentDispatchPage />);
    await waitFor(() => expect(screen.getByText(/Ollama/i)).toBeInTheDocument());

    // Choose Ollama (which has models) via the provider selector.
    const providerSelect = screen.getByLabelText(/provider/i);
    fireEvent.change(providerSelect, { target: { value: "ollama-local" } });
    fireEvent.change(screen.getByLabelText(/model/i), { target: { value: "mistral" } });

    // Select a run, advance to review, then dispatch.
    fireEvent.click(screen.getByText(/CI Build/i));
    fireEvent.click(screen.getByRole("button", { name: /review dispatch/i }));
    const confirm = await screen.findByRole("button", { name: /confirm dispatch/i });
    fireEvent.click(confirm);

    await waitFor(() => expect(dispatchBody).not.toBeNull());
    expect(dispatchBody?.provider).toBe("ollama");
    expect(dispatchBody?.model).toBe("mistral");
  });
});
