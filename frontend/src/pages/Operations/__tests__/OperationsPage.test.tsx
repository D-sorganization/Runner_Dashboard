// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import React from "react";
import { OperationsPage } from "../OperationsPage";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function mockAllOperationsFetch() {
  return vi.spyOn(globalThis, "fetch").mockImplementation((url) => {
    const u = String(url);
    if (u === "/api/deployment/state") {
      return Promise.resolve(
        jsonResponse({
          expected_version: "4.10.0",
          rollout_state: { status: "steady", machines_attention: 0 },
          machines: [{ name: "ct", display_name: "ControlTower", deployed_version: "4.10.0" }],
        }),
      );
    }
    if (u === "/api/fleet/orchestration") {
      return Promise.resolve(jsonResponse({ machines: [], audit_log: [] }));
    }
    if (u === "/api/orchestrator/queue") {
      return Promise.resolve(
        jsonResponse({
          enabled: true,
          mode: "running",
          active_leases: 2,
          reserved_slots: 0,
          capacity: { idle_runners: 6, online_runners: 8, busy_runners: 2, total_runners: 8 },
          work: { planned: 1, active: 2, blocked: 0 },
          provider_mix: { codex: 2 },
          budget: { spent_usd: 5.0, limit_usd: 25.0 },
        }),
      );
    }
    if (u === "/api/fleet/schedule") {
      return Promise.resolve(
        jsonResponse({
          state: { desired: 8, online: 8, busy: 2, offline: 0 },
          schedule: { schedules: [] },
        }),
      );
    }
    if (u === "/api/scheduled-workflows") {
      return Promise.resolve(
        jsonResponse({
          scheduled_workflow_count: 5,
          repositories: [],
        }),
      );
    }
    if (u === "/api/diagnostics/summary") {
      return Promise.resolve(
        jsonResponse({
          dashboard_memory_mb: 128,
          dashboard_pid: 9999,
          git_commit: "abc999",
        }),
      );
    }
    if (u === "/api/deployment/git-drift") {
      return Promise.resolve(jsonResponse({ is_drifted: false }));
    }
    return Promise.reject(new Error("unexpected fetch " + u));
  });
}

describe("OperationsPage", () => {
  it("renders all 5 operational sections and the status banner", async () => {
    mockAllOperationsFetch();

    render(<OperationsPage />);

    // Header banner
    expect(screen.getByRole("heading", { name: /Fleet Operations/i })).toBeInTheDocument();

    // Sections
    expect(await screen.findByRole("heading", { name: /Deploy & versions/i })).toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: /Admission \(Conductor\)/i })).toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: /Runner hours/i })).toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: /Scheduled workflows/i })).toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: /Diagnostics/i })).toBeInTheDocument();
  });

  it("scrolls to the target section when window.location.hash is present on mount", async () => {
    mockAllOperationsFetch();
    window.location.hash = "#runner-hours";

    const scrollIntoViewMock = vi.fn();
    Element.prototype.scrollIntoView = scrollIntoViewMock;

    render(<OperationsPage />);

    await screen.findByRole("heading", { name: /Runner hours/i });
    expect(scrollIntoViewMock).toHaveBeenCalled();
  });

  it("supports jump button clicks to scroll down to sections", async () => {
    mockAllOperationsFetch();

    const scrollIntoViewMock = vi.fn();
    Element.prototype.scrollIntoView = scrollIntoViewMock;

    render(<OperationsPage />);

    const admissionBtn = screen.getByRole("button", { name: /Admission \(Conductor\)/i });
    fireEvent.click(admissionBtn);

    expect(window.location.hash).toBe("#admission");
    expect(scrollIntoViewMock).toHaveBeenCalled();
  });
});
