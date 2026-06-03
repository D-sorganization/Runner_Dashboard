// @vitest-environment jsdom
/**
 * Tests for AgentDispatch.tsx — refactored onto the unified provider registry
 * (issue #728 E3; updated for #811).
 *
 * Covers:
 * 1. Renders without throwing (smoke test).
 * 2. Shows skeleton/loading state while fetching the registry and runs.
 * 3. Renders providers from GET /api/providers/registry.
 * 4. Shows error state when the runs API fails.
 * 5. Empty failed runs state renders without crash.
 */
import "@testing-library/jest-dom/vitest";
import React from "react";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AgentDispatchPage } from "../AgentDispatch";

afterEach(cleanup);

beforeEach(() => {
  vi.spyOn(console, "error").mockImplementation(() => {});
});
afterEach(() => {
  vi.restoreAllMocks();
});

const MOCK_REGISTRY = {
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
    },
    {
      id: "codex-cli",
      dashboard_id: "codex_cli",
      label: "Codex CLI",
      auth_mode: "api_key",
      resource: "runner",
      capabilities: ["code"],
      models: [],
      login_status: "unauthenticated",
      experimental: false,
    },
  ],
  auth_kinds: [],
  task_classes: [],
  capabilities: [],
};

const MOCK_RUNS = {
  workflow_runs: [
    {
      id: 1001,
      name: "CI Build",
      workflow_name: "ci.yml",
      head_branch: "main",
      conclusion: "failure",
      html_url: "https://github.com/org/repo/actions/runs/1001",
      created_at: "2026-05-01T10:00:00Z",
      run_number: 42,
      repository: { name: "runner-dashboard" },
    },
  ],
};

const EMPTY_RUNS = { workflow_runs: [] };

function setupFetch({
  registryOk = true,
  runsOk = true,
  registryData = MOCK_REGISTRY,
  runsData = MOCK_RUNS,
}: {
  registryOk?: boolean;
  runsOk?: boolean;
  registryData?: object;
  runsData?: object;
} = {}) {
  global.fetch = vi.fn((url: string) => {
    if ((url as string).includes("/api/providers/registry")) {
      return Promise.resolve({
        ok: registryOk,
        status: registryOk ? 200 : 500,
        json: () => Promise.resolve(registryData),
      } as Response);
    }
    if ((url as string).includes("/api/runs")) {
      return Promise.resolve({
        ok: runsOk,
        status: runsOk ? 200 : 500,
        json: () => Promise.resolve(runsData),
      } as Response);
    }
    return Promise.resolve({
      ok: true,
      status: 200,
      json: () => Promise.resolve({}),
    } as Response);
  }) as unknown as typeof fetch;
}

describe("AgentDispatchPage", () => {
  it("renders without throwing (smoke test)", () => {
    setupFetch();
    expect(() => render(<AgentDispatchPage />)).not.toThrow();
  });

  it("shows loading skeleton initially", () => {
    global.fetch = vi.fn(() => new Promise<Response>(() => {})) as unknown as typeof fetch;
    const { container } = render(<AgentDispatchPage />);
    expect(
      container.querySelector("[aria-busy='true']") ||
        container.querySelector(".skeleton") ||
        container.querySelector("[class*='skeleton']"),
    ).not.toBeNull();
    expect(container.firstChild).not.toBeNull();
  });

  it("renders providers from the registry after successful fetch", async () => {
    setupFetch();
    render(<AgentDispatchPage />);
    await waitFor(() => {
      expect(screen.getByText(/Claude CLI/i)).toBeInTheDocument();
      expect(screen.getByText(/Codex CLI/i)).toBeInTheDocument();
    });
  });

  it("shows error message when the runs API fails", async () => {
    setupFetch({ runsOk: false });
    render(<AgentDispatchPage />);
    await waitFor(() => {
      expect(document.body.textContent).toMatch(/error|failed|HTTP 500/i);
    });
    expect(document.querySelector(".empty-state")).toHaveAttribute("data-variant", "error");
    expect(screen.getByRole("button", { name: /retry/i })).toHaveAttribute(
      "data-touch-primitive",
      "TouchButton",
    );
  });

  it("renders empty failed runs state without crashing", async () => {
    setupFetch({ runsData: EMPTY_RUNS });
    render(<AgentDispatchPage />);
    await waitFor(() => {
      expect(screen.getByText(/Claude CLI/i)).toBeInTheDocument();
    });
    expect(screen.getByText(/No failed runs found/i)).toBeInTheDocument();
    expect(document.querySelector(".empty-state")).toBeInTheDocument();
    expect(document.body).toBeInTheDocument();
  });

  it("renders provider select step heading", async () => {
    setupFetch();
    render(<AgentDispatchPage />);
    await waitFor(() => {
      expect(document.body.textContent).toMatch(/agent|dispatch|select|provider/i);
    });
  });
});
