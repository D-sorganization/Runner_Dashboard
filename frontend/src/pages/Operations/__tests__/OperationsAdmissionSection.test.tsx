// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import React from "react";
import { OperationsAdmissionSection } from "../OperationsAdmissionSection";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const QUEUE_STATUS = {
  enabled: true,
  mode: "running",
  active_leases: 3,
  reserved_slots: 1,
  capacity: {
    idle_runners: 6,
    online_runners: 8,
    busy_runners: 2,
    total_runners: 8,
  },
  work: {
    planned: 5,
    active: 3,
    blocked: 0,
  },
  provider_mix: {
    codex: 2,
    claude: 1,
  },
  budget: {
    spent_usd: 12.5,
    limit_usd: 50.0,
  },
};

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("OperationsAdmissionSection", () => {
  it("renders section with id=admission and displays queue status", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(jsonResponse(QUEUE_STATUS));

    render(<OperationsAdmissionSection />);

    expect(screen.getByRole("heading", { name: /Admission \(Conductor\)/i })).toBeInTheDocument();
    expect(await screen.findByText("running")).toBeInTheDocument();
    expect(screen.getByText("3 active leases")).toBeInTheDocument();
    expect(screen.getByText("6 idle")).toBeInTheDocument();
    expect(screen.getByText("5 planned")).toBeInTheDocument();
    expect(screen.getByText(/\$12.50 \/ \$50.00/i)).toBeInTheDocument();
  });

  it("handles queue action button clicks (Pause / Resume / Drain)", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation((url, init) => {
      if (String(url) === "/api/orchestrator/queue" && init?.method === "POST") {
        return Promise.resolve(jsonResponse({ ...QUEUE_STATUS, mode: "paused" }));
      }
      return Promise.resolve(jsonResponse(QUEUE_STATUS));
    });

    render(<OperationsAdmissionSection />);

    const pauseBtn = await screen.findByRole("button", { name: /Pause Admission/i });
    fireEvent.click(pauseBtn);

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/orchestrator/queue",
        expect.objectContaining({
          method: "POST",
          body: JSON.stringify({ action: "pause" }),
        }),
      );
    });
  });

  it("displays feature flag disabled empty state when endpoint returns 404", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(jsonResponse({ error: "not found" }, 404));

    render(<OperationsAdmissionSection />);

    expect(
      await screen.findByText(/Conductor admission gate is not enabled/i),
    ).toBeInTheDocument();
  });

  it("shows an error banner with a Retry button when fetch fails", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new Error("Network disconnect"));

    render(<OperationsAdmissionSection />);

    expect(await screen.findByText(/Failed to load admission queue: Network disconnect/i)).toBeInTheDocument();
    const retryBtn = screen.getByRole("button", { name: /Retry/i });
    expect(retryBtn).toBeInTheDocument();
  });
});
