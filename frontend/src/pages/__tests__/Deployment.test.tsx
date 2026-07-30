// @vitest-environment jsdom
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { DeploymentTab, type DeploymentData } from "../Deployment";

const DEPLOYMENT_STATE: DeploymentData = {
  expected_version: "4.9.17",
  rollout_state: {
    status: "degraded",
    summary: "One machine needs attention",
    machines_attention: 1,
    machines_online: 2,
    machines_total: 3,
  },
  drift: {
    current: "4.9.16",
    expected: "4.9.17",
    message: "dashboard update available",
  },
  machines: [
    {
      name: "controltower",
      display_name: "ControlTower",
      rollout_state: "drifted",
      rollout_label: "Drifted",
      desired_version: "4.9.17",
      deployed_version: "4.9.16",
      drift_status: {
        severity: "warning",
        update_available: true,
        message: "Behind expected version",
      },
    },
  ],
};

function jsonResponse(payload: unknown): Response {
  return {
    ok: true,
    json: () => Promise.resolve(payload),
  } as Response;
}

describe("DeploymentTab", () => {
  beforeEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("fetches deployment state when rendered outside the legacy App owner", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(jsonResponse(DEPLOYMENT_STATE));

    render(<DeploymentTab />);

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/deployment/state",
        expect.any(Object),
      );
    });
    expect(await screen.findByText("ControlTower")).toBeInTheDocument();
    expect(screen.getAllByText("4.9.17").length).toBeGreaterThan(0);
    expect(screen.getByText("dashboard update available")).toBeInTheDocument();
  });

  it("preserves the legacy prop-driven data path", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch");

    render(
      <DeploymentTab
        data={DEPLOYMENT_STATE}
        loading={false}
        onRefresh={() => {}}
      />,
    );

    expect(screen.getAllByText("ControlTower").length).toBe(1);
    expect(fetchMock).not.toHaveBeenCalledWith(
      "/api/deployment/state",
      expect.any(Object),
    );
  });
});
