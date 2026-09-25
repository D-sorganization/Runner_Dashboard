// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { FleetEventsSection } from "../FleetEventsSection";

afterEach(() => {
  cleanup();
});

const mockEvents = [
  {
    id: 1,
    event_type: "runner_online",
    level: "info",
    source: "ControlTower",
    message: "Runner d-sorg-local-ControlTower-1 connected",
    timestamp: "2026-09-25T10:00:00Z",
  },
  {
    id: 2,
    event_type: "runner_offline",
    level: "warning",
    source: "DeskComputer",
    message: "Runner d-sorg-local-DeskComputer-1 disconnected",
    timestamp: "2026-09-25T09:45:00Z",
  },
];

describe("FleetEventsSection", () => {
  it("renders recent fleet events with filters", () => {
    render(
      <FleetEventsSection
        events={mockEvents}
        loading={false}
        error={null}
        onRetry={vi.fn()}
      />
    );

    expect(screen.getByRole("region", { name: /Fleet Event Log/i })).toBeInTheDocument();
    expect(screen.getByText("Runner d-sorg-local-ControlTower-1 connected")).toBeInTheDocument();
    expect(screen.getByText("Runner d-sorg-local-DeskComputer-1 disconnected")).toBeInTheDocument();
  });

  it("filters events by level", () => {
    render(
      <FleetEventsSection
        events={mockEvents}
        loading={false}
        error={null}
        onRetry={vi.fn()}
      />
    );

    fireEvent.click(screen.getByRole("button", { name: /^Warning/i }));

    expect(screen.getByText("Runner d-sorg-local-DeskComputer-1 disconnected")).toBeInTheDocument();
    expect(screen.queryByText("Runner d-sorg-local-ControlTower-1 connected")).not.toBeInTheDocument();
  });

  it("fails visibly with independent error state and retry CTA", () => {
    const onRetry = vi.fn();
    render(
      <FleetEventsSection
        events={[]}
        loading={false}
        error="Failed to load events: HTTP 500"
        onRetry={onRetry}
      />
    );

    expect(screen.getByRole("alert")).toHaveTextContent(/Failed to load events: HTTP 500/);
    fireEvent.click(screen.getByRole("button", { name: /Retry/i }));
    expect(onRetry).toHaveBeenCalled();
  });
});
