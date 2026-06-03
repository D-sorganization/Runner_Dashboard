// @vitest-environment jsdom
/**
 * Behaviour tests for pages/RunnerSchedule.tsx — extracted from the legacy
 * App.tsx monolith (decomposition #836, pass 6).
 *
 * Covers:
 * 1. Smoke render.
 * 2. Capacity stat row reflects the state payload.
 * 3. Renders a row per schedule entry.
 * 4. Editing a field updates the draft and is reflected in Save/Apply payloads.
 * 5. Save and Apply-Now invoke onSave with the right applyNow flag.
 * 6. Refresh button invokes onRefresh.
 * 7. Scheduler-missing + error states surface to the operator.
 */
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { RunnerScheduleTab, type RunnerScheduleData } from "../RunnerSchedule";

afterEach(cleanup);
beforeEach(() => {
  vi.spyOn(console, "error").mockImplementation(() => {});
});
afterEach(() => {
  vi.restoreAllMocks();
});

const DATA: RunnerScheduleData = {
  schedule: {
    schedules: [
      {
        name: "weekdays",
        days: ["Mon", "Tue"],
        start: "09:00",
        end: "17:00",
        runners: 4,
      },
      {
        name: "weekend",
        days: ["Sat"],
        start: "00:00",
        end: "23:59",
        runners: 1,
      },
    ],
  },
  state: {
    desired: 4,
    online: 3,
    installed: 5,
    busy: 2,
    idle: 1,
    offline: 2,
    reason: "schedule window",
    available: true,
    timestamp: 123,
  },
  machine: "DeskComputer",
  max_runners: 8,
  config_path: "/etc/runner-schedule.json",
  timers: {
    "runner-scheduler.timer": "active",
    "runner-cleanup.timer": "active",
  },
};

describe("RunnerScheduleTab", () => {
  it("renders without throwing (smoke test)", () => {
    expect(() =>
      render(<RunnerScheduleTab data={{}} loading={false} onSave={() => {}} />),
    ).not.toThrow();
  });

  it("reflects state in the stat row", () => {
    render(<RunnerScheduleTab data={DATA} loading={false} onSave={() => {}} />);
    expect(screen.getByText("Desired")).toBeInTheDocument();
    expect(screen.getByText("schedule window")).toBeInTheDocument();
    expect(screen.getByText("Online")).toBeInTheDocument();
    expect(screen.getByText("Offline")).toBeInTheDocument();
    expect(screen.getByText("scheduler installed")).toHaveAttribute(
      "data-touch-primitive",
      "Badge",
    );
  });

  it("renders a row per schedule entry", () => {
    render(<RunnerScheduleTab data={DATA} loading={false} onSave={() => {}} />);
    expect(screen.getByText("weekdays")).toBeInTheDocument();
    expect(screen.getByText("weekend")).toBeInTheDocument();
    expect(screen.getByText("Mon, Tue")).toBeInTheDocument();
  });

  it("edits a field and carries it into the Save payload", () => {
    const onSave = vi.fn();
    render(<RunnerScheduleTab data={DATA} loading={false} onSave={onSave} />);
    const startInputs = screen.getAllByDisplayValue("09:00");
    fireEvent.input(startInputs[0], { target: { value: "08:00" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(onSave).toHaveBeenCalledTimes(1);
    const [draft, applyNow] = onSave.mock.calls[0];
    expect(applyNow).toBe(false);
    expect(draft.schedules[0].start).toBe("08:00");
  });

  it("coerces the runners field to a number in the draft", () => {
    const onSave = vi.fn();
    render(<RunnerScheduleTab data={DATA} loading={false} onSave={onSave} />);
    const runnerInputs = screen.getAllByDisplayValue("4");
    fireEvent.input(runnerInputs[0], { target: { value: "6" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    const [draft] = onSave.mock.calls[0];
    expect(draft.schedules[0].runners).toBe(6);
    expect(typeof draft.schedules[0].runners).toBe("number");
  });

  it("disables Save and Apply while loading", () => {
    render(<RunnerScheduleTab data={DATA} loading={true} onSave={() => {}} />);
    expect(screen.getByText("Saving...")).toHaveAttribute(
      "data-touch-primitive",
      "Badge",
    );
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
  });

  it("Apply-Now invokes onSave with applyNow=true", () => {
    const onSave = vi.fn();
    render(<RunnerScheduleTab data={DATA} loading={false} onSave={onSave} />);
    fireEvent.click(screen.getByText("Apply Now"));
    expect(onSave).toHaveBeenCalledTimes(1);
    expect(onSave.mock.calls[0][1]).toBe(true);
  });

  it("Refresh invokes onRefresh", () => {
    const onRefresh = vi.fn();
    render(
      <RunnerScheduleTab
        data={DATA}
        loading={false}
        onRefresh={onRefresh}
        onSave={() => {}}
      />,
    );
    fireEvent.click(
      screen.getByRole("button", { name: "Refresh runner capacity" }),
    );
    expect(onRefresh).toHaveBeenCalledTimes(1);
  });

  it("surfaces scheduler-missing and error states", () => {
    render(
      <RunnerScheduleTab
        data={{
          schedule: { schedules: [] },
          state: { available: false, error: "scheduler not installed" },
          machine: "OGLaptop",
        }}
        loading={false}
        onSave={() => {}}
      />,
    );
    expect(screen.getByText(/scheduler missing/)).toBeInTheDocument();
    expect(screen.getByText("scheduler not installed")).toBeInTheDocument();
    expect(document.querySelector(".empty-state")).toBeInTheDocument();
  });

  it("uses shared touch primitives and scoped schedule classes", () => {
    render(<RunnerScheduleTab data={DATA} loading={false} onSave={() => {}} />);
    expect(
      document.querySelector(".runner-schedule__actions"),
    ).toBeInTheDocument();
    expect(
      document.querySelector(".runner-schedule__table"),
    ).toBeInTheDocument();
    expect(
      screen
        .getAllByRole("button")
        .every((button) => button.dataset.touchPrimitive === "TouchButton"),
    ).toBe(true);
    expect(screen.getByText("scheduler installed")).toHaveAttribute(
      "data-touch-primitive",
      "Badge",
    );
  });
});
