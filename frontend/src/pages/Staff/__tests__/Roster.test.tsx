// @vitest-environment jsdom
/**
 * Roster.test.tsx — Staff roster schedule cell rendering (#1744).
 *
 * `role.window` from `GET /api/v1/staff/roster` is null or {start, end};
 * the Schedule cell must format it, not stringify it.
 */
import "@testing-library/jest-dom/vitest";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { RoleCard } from "../Roster";
import type { RoleSpec } from "../staffApi";

function role(overrides: Partial<RoleSpec> = {}): RoleSpec {
  return {
    name: "night-watch",
    title: "Overnight Watchdog",
    active_runs: 0,
    budget: {},
    dispatchable: true,
    holds: [],
    idle_minutes: 20,
    instructions: "",
    persona: null,
    playbook: "",
    providers: [],
    repos: [],
    retired: false,
    retired_reason: "",
    schedule: null,
    source_path: "",
    summary: "",
    surface: "dashboard",
    valid: true,
    window: null,
    ...overrides,
  } as unknown as RoleSpec;
}

describe("RoleCard schedule cell (#1744)", () => {
  it("formats a {start, end} window instead of rendering [object Object]", () => {
    render(
      <RoleCard
        role={role({ schedule: "0 22 * * *", window: { start: "22:00", end: "06:00" } })}
        providers={{}}
      />,
    );

    expect(screen.getByText("0 22 * * * (22:00–06:00)")).toBeInTheDocument();
    expect(screen.queryByText(/\[object Object\]/)).not.toBeInTheDocument();
  });

  it("renders no window suffix when window is null", () => {
    render(<RoleCard role={role({ schedule: "0 22 * * *", window: null })} providers={{}} />);
    expect(screen.getByText("0 22 * * *")).toBeInTheDocument();
  });

  it("falls back to 'manual' when schedule is null", () => {
    render(<RoleCard role={role({ schedule: null, window: null })} providers={{}} />);
    expect(screen.getByText("manual")).toBeInTheDocument();
  });
});
