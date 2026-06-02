// @vitest-environment jsdom
/**
 * Behaviour tests for AlertsCenter (issue #819).
 *
 * The operator's #1 complaint was that acknowledged alerts re-popped on every
 * poll. These tests pin the durable-ack contract end-to-end through the UI:
 *
 *   1. The pill summarises the count and worst severity.
 *   2. Opening the drawer lists active alerts.
 *   3. Acknowledging a row removes it from the visible list AND keeps it
 *      acknowledged when the same alert is re-supplied (simulating a re-poll).
 *   4. The acked alert is re-surfaced only when its contentHash changes.
 *   5. Snooze suppresses the row; it returns after the window elapses.
 */

import React from "react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { AlertsCenter } from "../AlertsCenter";
import { computeFleetAlerts, type FleetState } from "../../lib/fleetAlerts";

const offlineState: FleetState = {
  machineCount: 3,
  machineOnline: 2,
  machineNodes: [
    { name: "ControlTower", online: true },
    { name: "DeskComputer", online: true },
    { name: "OGLaptop", online: false },
  ],
  watchdog: { status: "healthy" },
  stats: { success_rate: 95, runs_success: 95 },
  completedRuns: 100,
  runnerAudit: { violations: [] },
};

beforeEach(() => {
  localStorage.clear();
});

afterEach(() => {
  cleanup();
  localStorage.clear();
});

describe("AlertsCenter — pill summary", () => {
  it("shows the count and a red dot when a critical alert is active", () => {
    const { alerts } = computeFleetAlerts(offlineState);
    render(<AlertsCenter alerts={alerts} />);
    const pill = screen.getByRole("button", { name: /open alerts/i });
    expect(pill.className).toContain("alerts-pill--critical");
    expect(pill.textContent).toContain("1 alert");
  });

  it("reads 'All clear' when there are no alerts", () => {
    render(<AlertsCenter alerts={[]} />);
    const pill = screen.getByRole("button", { name: /open alerts/i });
    expect(pill.className).toContain("alerts-pill--ok");
    expect(pill.textContent).toContain("All clear");
  });
});

describe("AlertsCenter — drawer + durable ack", () => {
  it("acknowledging a row hides it and keeps it hidden across a re-poll", () => {
    const { alerts } = computeFleetAlerts(offlineState);
    const { rerender } = render(<AlertsCenter alerts={alerts} />);

    fireEvent.click(screen.getByRole("button", { name: /open alerts/i }));
    expect(screen.getByText("1 machine(s) offline")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: /acknowledge/i }));
    expect(screen.queryByText("1 machine(s) offline")).toBeNull();
    // The "N acknowledged" affordance appears.
    expect(screen.getByText(/1 acknowledged/i)).toBeTruthy();

    // Simulate a fresh poll producing an identical alert object.
    const repoll = computeFleetAlerts(offlineState).alerts;
    rerender(<AlertsCenter alerts={repoll} />);
    expect(screen.queryByText("1 machine(s) offline")).toBeNull();
    // Pill drops to "All clear" because the only alert is acknowledged.
    const pill = screen.getByRole("button", { name: /open alerts/i });
    expect(pill.className).toContain("alerts-pill--ok");
  });

  it("re-surfaces an acked alert when its content changes", () => {
    const first = computeFleetAlerts(offlineState).alerts;
    const { rerender } = render(<AlertsCenter alerts={first} />);
    fireEvent.click(screen.getByRole("button", { name: /open alerts/i }));
    fireEvent.click(screen.getByRole("button", { name: /acknowledge/i }));
    expect(screen.queryByText("1 machine(s) offline")).toBeNull();

    // A second machine drops — same rule id, different content/hash.
    const worse = computeFleetAlerts({
      ...offlineState,
      machineOnline: 1,
      machineNodes: [
        { name: "ControlTower", online: true },
        { name: "DeskComputer", online: false },
        { name: "OGLaptop", online: false },
      ],
    }).alerts;
    rerender(<AlertsCenter alerts={worse} />);
    expect(screen.getByText("2 machine(s) offline")).toBeTruthy();
  });

  it("un-acknowledge brings a row back into the active list", () => {
    const { alerts } = computeFleetAlerts(offlineState);
    render(<AlertsCenter alerts={alerts} />);
    fireEvent.click(screen.getByRole("button", { name: /open alerts/i }));
    fireEvent.click(screen.getByRole("button", { name: /^acknowledge$/i }));
    fireEvent.click(screen.getByRole("button", { name: /1 acknowledged/i }));
    fireEvent.click(screen.getByRole("button", { name: /un-acknowledge/i }));
    expect(screen.getByText("1 machine(s) offline")).toBeTruthy();
  });
});

describe("AlertsCenter — snooze", () => {
  it("snooze hides the row, and it returns after the window elapses", () => {
    let clock = 1_000;
    const now = () => clock;
    // Each poll yields a fresh array (as the real polling layer does).
    const { rerender } = render(
      <AlertsCenter alerts={computeFleetAlerts(offlineState).alerts} now={now} />,
    );

    fireEvent.click(screen.getByRole("button", { name: /open alerts/i }));
    fireEvent.click(screen.getByRole("button", { name: /snooze 1h/i }));
    expect(screen.queryByText("1 machine(s) offline")).toBeNull();

    // Advance past one hour and re-render with a fresh poll result.
    clock = 1_000 + 61 * 60 * 1000;
    rerender(
      <AlertsCenter alerts={computeFleetAlerts(offlineState).alerts} now={now} />,
    );
    expect(screen.getByText("1 machine(s) offline")).toBeTruthy();
  });
});
