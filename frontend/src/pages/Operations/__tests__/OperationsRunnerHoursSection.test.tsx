// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import React from "react";
import { OperationsRunnerHoursSection } from "../OperationsRunnerHoursSection";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const SCHEDULE_DATA = {
  machine: "controltower",
  max_runners: 16,
  config_path: "/etc/runner-schedule.json",
  timers: {
    scheduler: "active (running)",
    cleanup: "active (waiting)",
  },
  state: {
    desired: 8,
    online: 8,
    busy: 2,
    idle: 6,
    offline: 0,
    timestamp: "2026-06-15T12:00:00Z",
  },
  schedule: {
    schedules: [
      {
        name: "workday-core",
        days: ["mon", "tue", "wed", "thu", "fri"],
        start: "08:00",
        end: "18:00",
        runners: 8,
      },
    ],
  },
};

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("OperationsRunnerHoursSection", () => {
  it("renders section with id=runner-hours and displays runner hours stats and windows", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(jsonResponse(SCHEDULE_DATA));

    render(<OperationsRunnerHoursSection />);

    expect(screen.getByRole("heading", { name: /Runner hours/i })).toBeInTheDocument();
    expect(await screen.findByText("workday-core")).toBeInTheDocument();
    expect(screen.getByText("08:00 - 18:00")).toBeInTheDocument();
    expect(screen.getByText("8 desired")).toBeInTheDocument();
    expect(screen.getByText("8 online")).toBeInTheDocument();
    expect(screen.getByText("2 busy")).toBeInTheDocument();
    expect(screen.getByText("/etc/runner-schedule.json")).toBeInTheDocument();
  });

  it("handles Save & Apply Now button click", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation((url, init) => {
      if (String(url) === "/api/fleet/schedule" && init?.method === "POST") {
        return Promise.resolve(jsonResponse({ ...SCHEDULE_DATA, saved: true }));
      }
      return Promise.resolve(jsonResponse(SCHEDULE_DATA));
    });

    render(<OperationsRunnerHoursSection />);

    const applyBtn = await screen.findByRole("button", { name: /Apply Now/i });
    fireEvent.click(applyBtn);

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/fleet/schedule",
        expect.objectContaining({
          method: "POST",
          body: expect.stringContaining('"apply_now":true'),
        }),
      );
    });
  });

  it("shows an error banner with a Retry button when fetch fails", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new Error("Schedule unavailable"));

    render(<OperationsRunnerHoursSection />);

    expect(await screen.findByText(/Failed to load runner hours: Schedule unavailable/i)).toBeInTheDocument();
    const retryBtn = screen.getByRole("button", { name: /Retry/i });
    expect(retryBtn).toBeInTheDocument();
  });
});
