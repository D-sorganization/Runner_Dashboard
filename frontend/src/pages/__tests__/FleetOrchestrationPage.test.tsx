// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { FleetOrchestrationPage } from "../FleetOrchestrationPage";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

function jsonResponse(body: unknown, init?: ResponseInit): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
    ...init,
  });
}

function orchestrationPayload() {
  return {
    machines: [
      {
        name: "controltower",
        display_name: "ControlTower",
        role: "hub",
        online: true,
        runner_count: 8,
        busy_runners: 1,
        cpu_percent: 12,
        memory_percent: 34,
        last_ping: "2026-06-15T00:00:00Z",
      },
    ],
    online_count: 1,
    total_count: 1,
    audit_log: [
      {
        audit_id: "audit-1",
        recorded_at: "2026-06-15T00:00:00Z",
        orchestration_type: "fleet_deploy",
        machine: "controltower",
        deploy_action: "restart_runner",
        requested_by: "codex",
        decision: "accepted",
      },
    ],
  };
}

function mockFleetOrchestrationFetch() {
  return vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
    const url = String(input);
    if (url === "/api/fleet/orchestration") {
      return Promise.resolve(jsonResponse(orchestrationPayload()));
    }
    if (url === "/api/fleet/orchestration/dispatch") {
      expect(init?.method).toBe("POST");
      expect(JSON.parse(String(init.body))).toMatchObject({
        repo: "Runner_Dashboard",
        workflow: "frontend-tests.yml",
        ref: "main",
        machine_target: "controltower",
      });
      return Promise.resolve(jsonResponse({ audit_id: "dispatch-1" }));
    }
    if (url === "/api/fleet/orchestration/deploy") {
      expect(init?.method).toBe("POST");
      expect(JSON.parse(String(init.body))).toMatchObject({
        machine: "controltower",
        action: "restart_runner",
        confirmed: true,
      });
      return Promise.resolve(
        jsonResponse({ message: "Restart runner dispatched to controltower" }),
      );
    }
    return Promise.reject(new Error("unexpected fetch " + url));
  });
}

describe("FleetOrchestrationPage", () => {
  it("loads fleet orchestration data for the native route", async () => {
    const fetchMock = mockFleetOrchestrationFetch();

    render(<FleetOrchestrationPage />);

    expect(await screen.findByText("fleet_deploy")).toBeInTheDocument();
    expect(screen.getAllByText("ControlTower").length).toBeGreaterThan(0);
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/fleet/orchestration",
      expect.objectContaining({ headers: expect.any(Headers) }),
    );
  });

  it("dispatches workflows through the native route", async () => {
    const fetchMock = mockFleetOrchestrationFetch();

    render(<FleetOrchestrationPage />);
    await screen.findByText("fleet_deploy");

    fireEvent.click(screen.getByRole("button", { name: /Dispatch Workflow/i }));
    fireEvent.change(screen.getByPlaceholderText("e.g. Repository_Management"), {
      target: { value: "Runner_Dashboard" },
    });
    fireEvent.change(screen.getByPlaceholderText("e.g. ci-standard.yml"), {
      target: { value: "frontend-tests.yml" },
    });
    fireEvent.change(screen.getByDisplayValue("main"), {
      target: { value: "main" },
    });
    const selects = screen.getAllByRole("combobox");
    fireEvent.change(selects[selects.length - 1], {
      target: { value: "controltower" },
    });
    fireEvent.click(screen.getByRole("button", { name: /^Dispatch$/i }));

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/fleet/orchestration/dispatch",
        expect.anything(),
      ),
    );
  });

  it("deploys confirmed actions through the native route", async () => {
    const fetchMock = mockFleetOrchestrationFetch();

    render(<FleetOrchestrationPage />);
    await screen.findByText("fleet_deploy");

    const selects = screen.getAllByRole("combobox");
    fireEvent.change(selects[0], { target: { value: "controltower" } });
    fireEvent.click(
      screen.getByLabelText("I confirm this action against the selected machine"),
    );
    fireEvent.click(screen.getByRole("button", { name: /^Deploy$/i }));

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/fleet/orchestration/deploy",
        expect.anything(),
      ),
    );
    expect(
      await screen.findByText("Restart runner dispatched to controltower"),
    ).toBeInTheDocument();
  });
});
