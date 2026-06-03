// @vitest-environment jsdom
/**
 * Tests for the EventLog primitive (issue #863).
 *
 * TDD: authored alongside the primitive. Covers severity filtering, the
 * append-only history rendering, the accessible log/live regions, and the
 * windowed (virtualized) rendering for large feeds.
 */
import "@testing-library/jest-dom/vitest";
import { render, screen, fireEvent, cleanup, within } from "@testing-library/react";
import { afterEach, describe, it, expect } from "vitest";
import { EventLog } from "../EventLog";
import type { FleetEvent } from "../../lib/fleetEvents";

afterEach(cleanup);

function ev(p: Partial<FleetEvent> & Pick<FleetEvent, "ts">): FleetEvent {
  return { severity: "info", kind: "runner_online", title: "t", detail: "", ...p };
}

describe("EventLog", () => {
  it("renders an empty state when there are no events", () => {
    render(<EventLog events={[]} />);
    expect(screen.getByText(/no events to show/i)).toBeInTheDocument();
  });

  it("renders events with severity chips and a log role", () => {
    render(
      <EventLog
        events={[
          ev({ ts: 2, severity: "critical", kind: "runner_offline", title: "N1 offline — disk pressure", node: "N1" }),
          ev({ ts: 1, severity: "info", title: "N1 back online", node: "N1" }),
        ]}
      />,
    );
    expect(screen.getByRole("log", { name: /fleet event history/i })).toBeInTheDocument();
    expect(screen.getByText("N1 offline — disk pressure")).toBeInTheDocument();
    expect(screen.getByText("critical")).toHaveClass("event-log__chip--critical");
    expect(screen.getByText("N1 offline — disk pressure").closest("li")).toHaveClass(
      "event-log__row--critical",
    );
    // The node tag appears.
    expect(screen.getAllByText("N1").length).toBeGreaterThan(0);
  });

  it("filters by severity", () => {
    render(
      <EventLog
        events={[
          ev({ ts: 2, severity: "critical", title: "crit event" }),
          ev({ ts: 1, severity: "info", title: "info event" }),
        ]}
      />,
    );
    expect(screen.getByText("crit event")).toBeInTheDocument();
    expect(screen.getByText("info event")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Critical" }));
    expect(screen.getByRole("button", { name: "Critical" })).toHaveClass(
      "event-log__filter--active",
    );
    expect(screen.getByText("crit event")).toBeInTheDocument();
    expect(screen.queryByText("info event")).toBeNull();
  });

  it("announces the newest critical event via a polite live region", () => {
    render(
      <EventLog
        events={[ev({ ts: 5, severity: "critical", title: "DISK", detail: "low" })]}
      />,
    );
    const live = screen.getByTestId("event-log-live");
    expect(live).toHaveAttribute("aria-live", "polite");
    expect(live).toHaveClass("visually-hidden");
    expect(within(live).getByText(/critical: disk/i)).toBeInTheDocument();
  });

  it("renders a large feed without crashing (windowed)", () => {
    const many = Array.from({ length: 300 }, (_, i) =>
      ev({ ts: 1000 - i, title: `event ${i}` }),
    );
    render(<EventLog events={many} height={200} />);
    // The count reflects the full feed even though only a slice is in the DOM.
    expect(screen.getByText(/300 events/i)).toBeInTheDocument();
  });
});
