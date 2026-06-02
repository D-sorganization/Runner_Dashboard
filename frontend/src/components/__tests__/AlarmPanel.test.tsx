// @vitest-environment jsdom
/**
 * Tests for the AlarmPanel component (issue #863).
 */
import "@testing-library/jest-dom/vitest";
import { render, screen, fireEvent, cleanup } from "@testing-library/react";
import { afterEach, describe, it, expect, vi } from "vitest";
import { AlarmPanel } from "../AlarmPanel";
import { alarmLevel } from "../../lib/fleetAlerts";
import type { FleetAlert } from "../../lib/fleetAlerts";

afterEach(cleanup);

function alert(p: Partial<FleetAlert> & Pick<FleetAlert, "id" | "level">): FleetAlert {
  return { title: "t", detail: "d", contentHash: "h", ...p };
}

describe("alarmLevel", () => {
  it("is ok with no alerts", () => {
    expect(alarmLevel([])).toBe("ok");
  });
  it("is warning when only warnings present", () => {
    expect(alarmLevel([alert({ id: "disk-pressure", level: "warning" })])).toBe("warning");
  });
  it("is critical when any critical present", () => {
    expect(
      alarmLevel([
        alert({ id: "disk-pressure", level: "warning" }),
        alert({ id: "runners-offline", level: "critical" }),
      ]),
    ).toBe("critical");
  });
});

describe("AlarmPanel", () => {
  it("shows the all-clear state with no alerts", () => {
    render(<AlarmPanel alerts={[]} />);
    expect(screen.getByText(/all systems nominal/i)).toBeInTheDocument();
  });

  it("lists active alarms with their titles", () => {
    render(
      <AlarmPanel
        alerts={[
          alert({ id: "disk-pressure", level: "critical", title: "1 runner offline — disk pressure" }),
        ]}
      />,
    );
    expect(screen.getByText(/disk pressure/i)).toBeInTheDocument();
    // Critical severity raises the panel state.
    expect(screen.getByText("Critical")).toBeInTheDocument();
  });

  it("invokes onNavigate when an alarm row is activated", () => {
    const onNavigate = vi.fn();
    render(
      <AlarmPanel
        alerts={[alert({ id: "runners-offline", level: "critical", title: "2 offline" })]}
        onNavigate={onNavigate}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "2 offline" }));
    expect(onNavigate).toHaveBeenCalledWith("runners-offline");
  });
});
