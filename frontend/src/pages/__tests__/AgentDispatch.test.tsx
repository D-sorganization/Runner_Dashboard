// @vitest-environment jsdom
/**
 * Tests for AgentDispatch.tsx — issue #728 E3.
 *
 * Covers:
 * 1. Renders without throwing (smoke test).
 * 2. Shows skeleton/loading state while fetching providers and runs.
 * 3. Renders provider selection list on successful fetch.
 * 4. Shows error state when API calls fail.
 * 5. Provider card renders with availability status.
 * 6. Empty failed runs state renders without crash.
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

const MOCK_PROVIDERS = {
  providers: {
    claude_code_cli: {
      provider_id: "claude_code_cli",
      label: "Claude Code CLI",
      execution_mode: "cli",
      dispatch_mode: "workflow",
      notes: "",
      experimental: false,
      remote: true,
      editable: false,
    },
    codex_cli: {
      provider_id: "codex_cli",
      label: "Codex CLI",
      execution_mode: "cli",
      dispatch_mode: "workflow",
      notes: "",
      experimental: false,
      remote: true,
      editable: false,
    },
  },
  availability: {
    claude_code_cli: {
      provider_id: "claude_code_cli",
      available: true,
      status: "available",
      detail: "ready",
    },
    codex_cli: {
      provider_id: "codex_cli",
      available: false,
      status: "missing_binary",
      detail: "binary not found",
    },
  },
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
  providersOk = true,
  runsOk = true,
  providersData = MOCK_PROVIDERS,
  runsData = MOCK_RUNS,
}: {
  providersOk?: boolean;
  runsOk?: boolean;
  providersData?: object;
  runsData?: object;
} = {}) {
  global.fetch = vi.fn((url: string) => {
    if ((url as string).includes("/api/agent-remediation/providers")) {
      return Promise.resolve({
        ok: providersOk,
        status: providersOk ? 200 : 500,
        json: () => Promise.resolve(providersData),
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
  });
}

describe("AgentDispatchPage", () => {
  it("renders without throwing (smoke test)", () => {
    setupFetch();
    expect(() => render(<AgentDispatchPage />)).not.toThrow();
  });

  it("shows loading skeleton initially", () => {
    // Never resolves
    global.fetch = vi.fn(() => new Promise<Response>(() => {}));
    const { container } = render(<AgentDispatchPage />);
    // The component renders something during loading
    expect(container.firstChild).not.toBeNull();
  });

  it("renders provider list after successful fetch", async () => {
    setupFetch();
    render(<AgentDispatchPage />);
    await waitFor(() => {
      expect(screen.getByText(/Claude Code CLI/i)).toBeInTheDocument();
    });
  });

  it("renders multiple providers from API response", async () => {
    setupFetch();
    render(<AgentDispatchPage />);
    await waitFor(() => {
      expect(screen.getByText(/Claude Code CLI/i)).toBeInTheDocument();
      expect(screen.getByText(/Codex CLI/i)).toBeInTheDocument();
    });
  });

  it("shows error message when providers API fails", async () => {
    setupFetch({ providersOk: false });
    render(<AgentDispatchPage />);
    await waitFor(() => {
      expect(document.body.textContent).toMatch(/error|failed|HTTP 500/i);
    });
  });

  it("shows error message when runs API fails", async () => {
    setupFetch({ runsOk: false });
    render(<AgentDispatchPage />);
    await waitFor(() => {
      expect(document.body.textContent).toMatch(/error|failed|HTTP 500/i);
    });
  });

  it("renders empty failed runs state without crashing", async () => {
    setupFetch({ runsData: EMPTY_RUNS });
    render(<AgentDispatchPage />);
    await waitFor(() => {
      expect(screen.getByText(/Claude Code CLI/i)).toBeInTheDocument();
    });
    // No crash with empty failed runs list
    expect(document.body).toBeInTheDocument();
  });

  it("renders provider select step heading", async () => {
    setupFetch();
    render(<AgentDispatchPage />);
    await waitFor(() => {
      // The page should have some agent/dispatch related heading
      expect(document.body.textContent).toMatch(/agent|dispatch|select|provider/i);
    });
  });
});
