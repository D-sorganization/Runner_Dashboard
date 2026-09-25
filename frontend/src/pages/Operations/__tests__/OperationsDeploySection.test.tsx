// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import React from "react";
import { OperationsDeploySection } from "../OperationsDeploySection";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const DEPLOYMENT_DATA = {
  expected_version: "4.10.0",
  rollout_state: {
    status: "degraded",
    summary: "One machine needs attention",
    machines_attention: 1,
    machines_online: 2,
    machines_total: 3,
  },
  drift: {
    current: "4.9.16",
    expected: "4.10.0",
    message: "dashboard update available",
  },
  machines: [
    {
      name: "controltower",
      display_name: "ControlTower",
      rollout_state: "drifted",
      rollout_label: "Drifted",
      desired_version: "4.10.0",
      deployed_version: "4.9.16",
      drift_status: {
        severity: "warning",
        update_available: true,
        message: "Behind expected version",
      },
    },
  ],
};

const ORCHESTRATION_DATA = {
  machines: [
    {
      name: "controltower",
      display_name: "ControlTower",
      role: "hub",
      online: true,
      runner_count: 8,
      busy_runners: 2,
      cpu_percent: 15,
      memory_percent: 42,
      last_ping: "2026-06-15T12:00:00Z",
    },
  ],
  online_count: 1,
  total_count: 1,
  audit_log: [
    {
      audit_id: "audit-101",
      recorded_at: "2026-06-15T12:00:00Z",
      orchestration_type: "fleet_deploy",
      machine: "controltower",
      deploy_action: "restart_runner",
      requested_by: "codex",
      decision: "accepted",
    },
  ],
};

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function mockDeployFetch() {
  return vi.spyOn(globalThis, "fetch").mockImplementation((url, init) => {
    const urlStr = String(url);
    if (urlStr === "/api/deployment/state") {
      return Promise.resolve(jsonResponse(DEPLOYMENT_DATA));
    }
    if (urlStr === "/api/fleet/orchestration") {
      return Promise.resolve(jsonResponse(ORCHESTRATION_DATA));
    }
    if (urlStr === "/api/fleet/orchestration/deploy" && init?.method === "POST") {
      return Promise.resolve(
        jsonResponse({ message: "Deploy signal dispatched" }),
      );
    }
    if (urlStr === "/api/deployment/update-signal" && init?.method === "POST") {
      return Promise.resolve(
        jsonResponse({ message: "Update signal sent" }),
      );
    }
    return Promise.reject(new Error("unexpected url " + urlStr));
  });
}

describe("OperationsDeploySection", () => {
  it("renders section with id=deploy and displays rollout & orchestration info", async () => {
    mockDeployFetch();

    render(<OperationsDeploySection />);

    expect(screen.getByRole("heading", { name: /Deploy & versions/i })).toBeInTheDocument();
    expect((await screen.findAllByText("ControlTower")).length).toBeGreaterThan(0);
    expect(screen.getAllByText("4.10.0").length).toBeGreaterThan(0);
    expect(screen.getByText("Behind expected version")).toBeInTheDocument();
    expect(screen.getByText("fleet_deploy")).toBeInTheDocument();
    expect(screen.getByText(/restart_runner/i)).toBeInTheDocument();
  });

  it("handles dispatching a deploy action to a machine", async () => {
    const fetchMock = mockDeployFetch();

    render(<OperationsDeploySection />);

    await screen.findAllByText("ControlTower");

    const actionInput = screen.getByPlaceholderText(/e\.g\. restart_runner/i);
    fireEvent.change(actionInput, { target: { value: "restart_runner" } });

    const confirmCheck = screen.getByRole("checkbox", { name: /Confirm action/i });
    fireEvent.click(confirmCheck);

    const submitBtn = screen.getByRole("button", { name: /Execute Deploy/i });
    fireEvent.click(submitBtn);

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/fleet/orchestration/deploy",
        expect.objectContaining({ method: "POST" }),
      );
    });
    expect(await screen.findByText(/Deploy signal dispatched/i)).toBeInTheDocument();
  });

  it("shows an error banner with a Retry button when fetch fails", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new Error("Deployment unreachable"));

    render(<OperationsDeploySection />);

    expect(await screen.findByText(/Failed to load deployment: Deployment unreachable/i)).toBeInTheDocument();
    const retryBtn = screen.getByRole("button", { name: /Retry/i });
    expect(retryBtn).toBeInTheDocument();
  });
});
