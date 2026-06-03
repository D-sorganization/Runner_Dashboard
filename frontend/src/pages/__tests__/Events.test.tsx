// @vitest-environment jsdom
/**
 * Behaviour tests for pages/Events.tsx — the dedicated event-log and
 * alarm-center tab.
 */
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { EventsTab, OverviewEventSection } from "../Events";

vi.mock("../../hooks/useFleetEvents", () => ({
  useFleetEvents: () => ({
    events: [],
    alerts: [],
    error: "stream offline",
  }),
}));

afterEach(cleanup);

describe("EventsTab", () => {
  it("renders the event log shell with scoped degraded-state classes", () => {
    const { container } = render(<EventsTab />);

    expect(container.querySelector(".events-tab")).toBeInTheDocument();
    expect(screen.getByText("Event log")).toHaveClass("events-tab__title");
    expect(screen.getByText(/Live updates degraded:/)).toHaveClass("events-tab__degraded");
  });
});

describe("OverviewEventSection", () => {
  it("renders the compact overview section through its scoped class", () => {
    const { container } = render(<OverviewEventSection />);

    expect(container.querySelector(".overview-event-section")).toBeInTheDocument();
    expect(container.querySelector('[data-touch-primitive="EventLog"]')).toBeInTheDocument();
  });
});
