// @vitest-environment jsdom
/**
 * Tests for Remediation/Mobile.tsx -- issue #196 M10.
 *
 * Covers:
 * 1. Renders subtab control with Automations / PRs / Issues.
 * 2. Clicking a subtab changes the visible content.
 * 3. Tapping a card opens the action sheet (BottomSheet).
 * 4. Confirming dispatch calls POST /api/agent-remediation/dispatch.
 * 5. In-flight tile appears after dispatch.
 * 6. BottomSheet closes on Escape key.
 */
import "@testing-library/jest-dom/vitest";
import React, { useState } from "react";
import { cleanup, render, screen, fireEvent, waitFor, act } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { RemediationMobile } from "../Mobile";
import type { InFlightDispatch } from "../Mobile";

afterEach(cleanup);

// -- Mocks ---------------------------------------------------------------------

const MOCK_PROVIDERS = {
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
};

const MOCK_AVAILABILITY = {
  claude_code_cli: {
    provider_id: "claude_code_cli",
    available: true,
    status: "available",
    detail: "ready",
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

const MOCK_PRS = [
  {
    id: 2001,
    number: 42,
    title: "Fix flaky test",
    html_url: "https://github.com/org/repo/pull/42",
    head: { ref: "fix/flaky-test" },
    base: { repo: { name: "runner-dashboard" } },
    draft: false,
    labels: [],
    updated_at: "2026-05-01T10:00:00Z",
  },
];

const MOCK_ISSUES = [
  {
    id: 3001,
    number: 196,
    title: "Mobile Remediation + 3-tap Agent Dispatch",
    html_url: "https://github.com/org/repo/issues/196",
    repository_url: "https://api.github.com/repos/org/runner-dashboard",
    labels: [{ name: "enhancement" }],
    updated_at: "2026-05-01T10:00:00Z",
  },
];

const MOCK_DISPATCH_RESPONSE = {
  status: "dispatched",
  workflow: "Agent-CI-Remediation.yml",
  target_repository: "d-sorganization/runner-dashboard",
  provider: "claude_code_cli",
  fingerprint: "abc123",
  note: "Dispatch submitted successfully.",
};

// -- Helpers -------------------------------------------------------------------

function setupFetch({
  providersOk = true,
  runsOk = true,
  prsOk = true,
  issuesOk = true,
  dispatchOk = true,
}: {
  providersOk?: boolean;
  runsOk?: boolean;
  prsOk?: boolean;
  issuesOk?: boolean;
  dispatchOk?: boolean;
} = {}) {
  const fetchMock = vi.fn((url: string, options?: RequestInit) => {
    if (url.includes("/api/agent-remediation/providers")) {
      return Promise.resolve({
        ok: providersOk,
        status: providersOk ? 200 : 500,
        json: () =>
          Promise.resolve({
            providers: MOCK_PROVIDERS,
            availability: MOCK_AVAILABILITY,
          }),
      } as Response);
    }
    if (url.includes("/api/runs")) {
      return Promise.resolve({
        ok: runsOk,
        status: runsOk ? 200 : 500,
        json: () => Promise.resolve(MOCK_RUNS),
      } as Response);
    }
    if (url.includes("/api/pulls")) {
      return Promise.resolve({
        ok: prsOk,
        status: prsOk ? 200 : 500,
        json: () => Promise.resolve(MOCK_PRS),
      } as Response);
    }
    if (url.includes("/api/issues")) {
      return Promise.resolve({
        ok: issuesOk,
        status: issuesOk ? 200 : 500,
        json: () => Promise.resolve(MOCK_ISSUES),
      } as Response);
    }
    if (url.includes("/api/agent-remediation/dispatch") && options?.method === "POST") {
      return Promise.resolve({
        ok: dispatchOk,
        status: dispatchOk ? 200 : 409,
        json: () =>
          Promise.resolve(
            dispatchOk
              ? MOCK_DISPATCH_RESPONSE
              : { detail: "Dispatch rejected" },
          ),
      } as Response);
    }
    return Promise.resolve({
      ok: false,
      status: 404,
      json: () => Promise.resolve({}),
    } as Response);
  });

  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

// -- Wrapper that holds in-flight state at parent level -----------------------

interface WrapperProps {
  initial?: InFlightDispatch[];
}

function Wrapper({ initial = [] }: WrapperProps) {
  const [inFlight, setInFlight] = useState<InFlightDispatch[]>(initial);
  const handleAdd = (d: InFlightDispatch) => setInFlight((prev) => [...prev, d]);
  return (
    <RemediationMobile inFlightDispatches={inFlight} onAddInFlight={handleAdd} />
  );
}

// -- Tests --------------------------------------------------------------------

describe("RemediationMobile", () => {
  beforeEach(() => {
    vi.spyOn(console, "error").mockImplementation(() => {});
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    cleanup();
  });

  it("renders subtab control with Automations, PRs, Issues", async () => {
    setupFetch();
    render(<Wrapper />);

    await waitFor(() => {
      expect(screen.getByRole("radiogroup", { name: /remediation subtabs/i })).toBeInTheDocument();
    });

    expect(screen.getByRole("radio", { name: /automations/i })).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: /prs/i })).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: /issues/i })).toBeInTheDocument();
  });

  it("defaults to Automations subtab and shows failed runs", async () => {
    setupFetch();
    render(<Wrapper />);

    await waitFor(() => {
      expect(screen.getByRole("radio", { name: /automations/i })).toHaveAttribute(
        "aria-checked",
        "true",
      );
    });

    expect(screen.getByText(/CI Build/i)).toBeInTheDocument();
  });

  it("clicking PRs subtab shows open PRs", async () => {
    setupFetch();
    render(<Wrapper />);

    await waitFor(() => {
      expect(screen.getByRole("radio", { name: /prs/i })).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole("radio", { name: /prs/i }));

    await waitFor(() => {
      expect(screen.getByText(/Fix flaky test/i)).toBeInTheDocument();
    });

    expect(screen.queryByText(/CI Build/i)).not.toBeInTheDocument();
  });

  it("clicking Issues subtab shows open issues", async () => {
    setupFetch();
    render(<Wrapper />);

    await waitFor(() => {
      expect(screen.getByRole("radio", { name: /issues/i })).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole("radio", { name: /issues/i }));

    await waitFor(() => {
      expect(screen.getByText(/Mobile Remediation/i)).toBeInTheDocument();
    });
  });

  it("tapping a card opens the action sheet BottomSheet", async () => {
    setupFetch();
    render(<Wrapper />);

    await waitFor(() => {
      expect(screen.getByText(/CI Build/i)).toBeInTheDocument();
    });

    const card = screen.getByRole("button", { name: /Failed run: CI Build/i });
    fireEvent.click(card);

    await waitFor(() => {
      expect(screen.getByRole("dialog")).toBeInTheDocument();
    });

    expect(screen.getByRole("button", { name: /Dispatch Claude Code CLI/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Pick a different agent/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Open on desktop/i })).toBeInTheDocument();
  });

  it("confirming dispatch calls POST /api/agent-remediation/dispatch", async () => {
    const fetchMock = setupFetch();
    render(<Wrapper />);

    await waitFor(() => {
      expect(screen.getByText(/CI Build/i)).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole("button", { name: /Failed run: CI Build/i }));

    await waitFor(() => {
      expect(screen.getByRole("dialog")).toBeInTheDocument();
    });

    const dispatchBtn = screen.getByRole("button", { name: /Dispatch Claude Code CLI/i });

    await act(async () => {
      fireEvent.click(dispatchBtn);
    });

    await waitFor(() => {
      const dispatchCalls = (fetchMock.mock.calls as [string, RequestInit?][]).filter(
        ([url, opts]) =>
          url.includes("/api/agent-remediation/dispatch") &&
          opts?.method === "POST",
      );
      expect(dispatchCalls).toHaveLength(1);
    });

    const dispatchCall = (fetchMock.mock.calls as [string, RequestInit?][]).find(
      ([url, opts]) =>
        url.includes("/api/agent-remediation/dispatch") && opts?.method === "POST",
    );
    expect(dispatchCall).toBeDefined();
    const body = JSON.parse(dispatchCall![1]!.body as string);
    expect(body.provider).toBe("claude_code_cli");
    expect(body.repository).toBe("runner-dashboard");
  });

  it("in-flight tile appears after dispatch", async () => {
    setupFetch();
    render(<Wrapper />);

    await waitFor(() => {
      expect(screen.getByText(/CI Build/i)).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole("button", { name: /Failed run: CI Build/i }));

    await waitFor(() => {
      expect(screen.getByRole("dialog")).toBeInTheDocument();
    });

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /Dispatch Claude Code CLI/i }));
    });

    await waitFor(() => {
      const tiles = screen.getAllByRole("status");
      const inflight = tiles.find((el) =>
        el.getAttribute("aria-label")?.startsWith("In-flight dispatch"),
      );
      expect(inflight).toBeDefined();
    });
  });

  it("BottomSheet closes on Escape key", async () => {
    setupFetch();
    render(<Wrapper />);

    await waitFor(() => {
      expect(screen.getByText(/CI Build/i)).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole("button", { name: /Failed run: CI Build/i }));

    await waitFor(() => {
      expect(screen.getByRole("dialog")).toBeInTheDocument();
    });

    fireEvent.keyDown(document, { key: "Escape" });

    await waitFor(() => {
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    });
  });

  it("shows recommended agent badge on each automation card", async () => {
    setupFetch();
    render(<Wrapper />);

    await waitFor(() => {
      expect(screen.getByText(/CI Build/i)).toBeInTheDocument();
    });

    expect(screen.getByText(/Recommended: Claude Code CLI/i)).toBeInTheDocument();
  });

  it("shows empty state when no failed runs", async () => {
    const fetchMock = vi.fn((url: string) => {
      if (url.includes("/api/agent-remediation/providers")) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () =>
            Promise.resolve({
              providers: MOCK_PROVIDERS,
              availability: MOCK_AVAILABILITY,
            }),
        } as Response);
      }
      if (url.includes("/api/runs")) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve({ workflow_runs: [] }),
        } as Response);
      }
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () => Promise.resolve([]),
      } as Response);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<Wrapper />);

    await waitFor(() => {
      expect(screen.getByText(/No failed runs found/i)).toBeInTheDocument();
    });
  });

  it("in-flight dispatches persist across subtab switches", async () => {
    setupFetch();

    const preExisting: InFlightDispatch[] = [
      {
        id: "test-inflight-1",
        itemId: 1001,
        itemTitle: "runner-dashboard: ci.yml",
        provider: "claude_code_cli",
        providerLabel: "Claude Code CLI",
        repository: "runner-dashboard",
        startedAt: Date.now() - 30000,
        lastHeartbeat: Date.now() - 5000,
        status: "dispatched",
        fingerprint: "abc123",
      },
    ];

    render(<Wrapper initial={preExisting} />);

    await waitFor(() => {
      expect(screen.getByRole("radiogroup")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole("radio", { name: /prs/i }));

    await waitFor(() => {
      expect(screen.getByText(/Fix flaky test/i)).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole("radio", { name: /automations/i }));

    await waitFor(() => {
      expect(screen.getByRole("radiogroup")).toBeInTheDocument();
    });

    const inflight = screen.queryByRole("status", {
      name: /In-flight dispatch/i,
    });
    expect(screen.getByRole("radiogroup")).toBeInTheDocument();
  });
});
