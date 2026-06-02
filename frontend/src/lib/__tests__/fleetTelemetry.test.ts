/**
 * Unit tests for lib/fleetTelemetry.ts — the pure telemetry/formatting helpers
 * extracted from the legacy App.tsx FleetTab (decomposition #836, pass 12).
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  boundedPercent,
  compactRunnerActivity,
  cpuColor,
  formatDuration,
  machineTelemetryForRunner,
  runnerCurrentRun,
  shortSha,
  timeAgo,
} from "../fleetTelemetry";

afterEach(() => {
  vi.useRealTimers();
});

describe("timeAgo", () => {
  it("returns empty string for falsy input", () => {
    expect(timeAgo(null)).toBe("");
    expect(timeAgo(undefined)).toBe("");
    expect(timeAgo("")).toBe("");
  });

  it("formats seconds / minutes / hours / days relative to now", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-06-02T00:00:00Z"));
    expect(timeAgo("2026-06-01T23:59:30Z")).toBe("30s ago");
    expect(timeAgo("2026-06-01T23:55:00Z")).toBe("5m ago");
    expect(timeAgo("2026-06-01T21:00:00Z")).toBe("3h ago");
    expect(timeAgo("2026-05-30T00:00:00Z")).toBe("3d ago");
  });
});

describe("formatDuration", () => {
  it("returns dash for falsy or negative durations", () => {
    expect(formatDuration(0)).toBe("-");
    expect(formatDuration(-5)).toBe("-");
  });

  it("formats sub-minute and minute+second durations", () => {
    expect(formatDuration(45)).toBe("45s");
    expect(formatDuration(125)).toBe("2m 5s");
  });
});

describe("boundedPercent", () => {
  it("clamps into [0, 100] and rounds", () => {
    expect(boundedPercent(42.4)).toBe(42);
    expect(boundedPercent(42.6)).toBe(43);
    expect(boundedPercent(-10)).toBe(0);
    expect(boundedPercent(150)).toBe(100);
  });

  it("returns 0 for non-finite input", () => {
    expect(boundedPercent("nope")).toBe(0);
    expect(boundedPercent(NaN)).toBe(0);
    // Infinity is non-finite, so it falls through to the 0 guard.
    expect(boundedPercent(Infinity)).toBe(0);
  });
});

describe("cpuColor", () => {
  it("maps the percent value to the four banded fill colours", () => {
    expect(cpuColor(10)).toBe("rgba(63,185,80,0.3)");
    expect(cpuColor(45)).toBe("rgba(63,185,80,0.6)");
    expect(cpuColor(70)).toBe("rgba(210,153,34,0.6)");
    expect(cpuColor(95)).toBe("rgba(248,81,73,0.7)");
  });
});

describe("shortSha", () => {
  it("abbreviates a sha to 7 chars and falls back to 'unknown'", () => {
    expect(shortSha("0123456789abcdef")).toBe("0123456");
    expect(shortSha(null)).toBe("unknown");
    expect(shortSha("")).toBe("unknown");
  });
});

describe("machineTelemetryForRunner", () => {
  it("resolves CPU/RAM/uptime telemetry for a runner from the node map", () => {
    const nodesByName = {
      deskcomputer: {
        last_seen: "2026-06-01T23:59:00Z",
        system: {
          cpu: { percent_1m_avg: 55 },
          memory: { total_gb: 32, available_gb: 8 },
          uptime_seconds: 90,
        },
      },
    };
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-06-02T00:00:00Z"));
    const t = machineTelemetryForRunner(
      { name: "d-sorg-local-DeskComputer-3" },
      nodesByName,
    );
    expect(t.machine).toBe("DeskComputer");
    expect(t.cpu).toBe(55);
    // (1 - 8/32) * 100 = 75
    expect(t.memory).toBe(75);
    expect(t.uptime).toBe("1m 30s");
    expect(t.seen).toBe("1m ago");
    expect(t.node).toBe(nodesByName.deskcomputer);
  });

  it("falls back gracefully when the node is unknown / has no telemetry", () => {
    const t = machineTelemetryForRunner({ name: "Ghost-runner-1" }, {});
    expect(t.cpu).toBe(0);
    expect(t.memory).toBe(0);
    expect(t.uptime).toBe("no uptime");
    expect(t.seen).toBe("not seen");
  });

  it("uses memory.percent when total_gb is absent", () => {
    const t = machineTelemetryForRunner(
      { name: "d-sorg-local-OGLaptop-1" },
      { oglaptop: { system: { memory: { percent: 40 } } } },
    );
    expect(t.memory).toBe(40);
  });
});

describe("runnerCurrentRun", () => {
  const RUNNER = { name: "DeskComputer-runner-2", id: 77 };

  it("matches an in_progress run by runner name", () => {
    const runs = [
      { status: "completed", runner_name: "DeskComputer-runner-2" },
      { status: "in_progress", runner_name: "DeskComputer-runner-2", id: 1 },
    ];
    expect(runnerCurrentRun(RUNNER, runs)).toEqual(runs[1]);
  });

  it("matches an active run by runner id", () => {
    const runs = [{ status: "queued", runner_id: 77, id: 9 }];
    expect(runnerCurrentRun(RUNNER, runs)).toEqual(runs[0]);
  });

  it("treats a run with no conclusion and non-completed status as active", () => {
    const runs = [{ status: "waiting", runner_name: "DeskComputer-runner-2" }];
    expect(runnerCurrentRun(RUNNER, runs)).toEqual(runs[0]);
  });

  it("returns undefined when no run is active for the runner", () => {
    expect(runnerCurrentRun(RUNNER, [])).toBeUndefined();
    expect(
      runnerCurrentRun(RUNNER, [
        { status: "completed", conclusion: "success", runner_name: "DeskComputer-runner-2" },
      ]),
    ).toBeUndefined();
  });
});

describe("compactRunnerActivity", () => {
  it("returns 'idle' for no current run", () => {
    expect(compactRunnerActivity(null)).toBe("idle");
  });

  it("prefers workflow_name, then name, then status, then 'running'", () => {
    expect(compactRunnerActivity({ workflow_name: "CI", name: "x" })).toBe("CI");
    expect(compactRunnerActivity({ name: "Deploy" })).toBe("Deploy");
    expect(compactRunnerActivity({ status: "queued" })).toBe("queued");
    expect(compactRunnerActivity({})).toBe("running");
  });
});
